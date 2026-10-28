"""IC-705 memory write golden frames from the calibration capture.

These golden bytes were captured from a real radio over CI-V; re-capture
them only if a model's memory layout code or profile changes, or on
firmware news.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane.commands.memory import (
    build_memory_clear,
    build_memory_contents_set,
    parse_memory_entry,
)
from rigplane.core.types import RepeaterShiftDirection, ToneSquelchType
from rigplane.profiles import get_radio_profile
from rigplane.types import MemoryChannel
from test_memory_write_layouts import _cmd_map, _memory

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ic705_memory_calibration.json").read_text()
)

_RADIO_ADDR = int(FIXTURE["radio_address"], 16)


def _build(mem: MemoryChannel) -> bytes:
    profile = get_radio_profile("IC-705")
    return build_memory_contents_set(
        mem,
        profile.memory,
        to_addr=_RADIO_ADDR,
        cmd_map=_cmd_map("IC-705"),
        modes=profile.modes,
        ctcss_tones_centihz=profile.ctcss_tones_centihz,
        data_mode_count=profile.data_mode_count,
    )


def test_encoder_frame_equals_the_accepted_capture() -> None:
    """The encoder's frame for the record the capture's
    ``dtcs_023_accepted`` entry describes is that entry's frame, byte for
    byte. The inputs, read off the captured bytes: 146.610 MHz, FM (raw
    mode code 05), filter 1, data 1, TONE, DUP-, tone 123.0 Hz, tsql
    88.5 Hz, name "K4GSO", group 1, channel 99."""
    mem = MemoryChannel(
        channel=99,
        group=1,
        frequency_hz=146_610_000,
        mode=0x05,
        filter=1,
        scan=0,
        datamode=1,
        tonemode=1,
        duplex=1,
        tone_centihz=12_300,
        tsql_centihz=8_850,
        name="K4GSO",
    )
    assert _build(mem).hex() == FIXTURE["encoder_frames"]["dtcs_023_accepted"]["tx"]


def test_parse_decodes_the_real_record_and_the_replayed_readback() -> None:
    """``parse_memory_entry`` decodes the capture's own record reply and
    the readback of the replayed write -- the same content at two
    addresses."""
    memory = _memory("IC-705")
    real = parse_memory_entry(
        bytes.fromhex(FIXTURE["real_record_read"]["rx"])[6:-1], memory
    )
    replayed = parse_memory_entry(
        bytes.fromhex(FIXTURE["readback_after_write"]["rx"])[6:-1], memory
    )
    for entry, group, channel in ((real, 0, 1), (replayed, 1, 99)):
        assert not entry.empty
        assert (entry.group, entry.channel) == (group, channel)
        assert entry.frequency_hz == 146_970_000
        assert entry.mode == "FM"
        assert entry.data_mode is True
        assert entry.duplex is RepeaterShiftDirection.MINUS
        assert entry.tone_mode is ToneSquelchType.TONE
        assert entry.tone_centihz == 12_300
        assert entry.tsql_centihz == 8_850
        assert entry.dtcs_code == 23
        assert entry.offset_hz == 600_000
        assert entry.name == "Ka4WjA"


def test_unset_tones_write_the_declared_defaults_never_zeros() -> None:
    """With the caller leaving tone/tsql unset, the tone, tsql and DTCS
    fields carry 88.5 Hz and DTCS 023 in BOTH block copies -- never the
    zeros the capture's rejected frames carry."""
    layout = _memory("IC-705").write_layout
    assert layout is not None
    span = layout.data_block_span()
    assert span is not None
    block_len = span[1] - span[0]
    offsets = {field.field: field.offset for field in layout.fields}
    # The accepted capture's own field bytes, sliced out of its frame
    # (header + command + 4-byte address, then the record).
    accepted = bytes.fromhex(FIXTURE["encoder_frames"]["dtcs_023_accepted"]["tx"])
    accepted_record = accepted[10:-1]
    mem = MemoryChannel(
        channel=99,
        group=1,
        frequency_hz=146_610_000,
        mode=0x05,
        filter=1,
        scan=0,
        datamode=1,
        tonemode=0,
        duplex=1,
        name="K4GSO",
    )
    record = _build(mem)[10:-1]
    for copy in range(layout.copies):
        base = copy * block_len
        for name in ("tone", "tsql", "dtcs"):
            field_offset = offsets[name] + base
            got = record[field_offset : field_offset + 3]
            expected = (
                b"\x00\x08\x85"
                if name in ("tone", "tsql")
                else accepted_record[field_offset : field_offset + 3]
            )
            assert got == expected
            assert got != b"\x00\x00\x00"


def test_clear_frame_equals_the_captured_clear() -> None:
    """``1A 00`` + the address + ``FF`` is the capture's clear frame."""
    built = build_memory_clear(
        _memory("IC-705"), 1, 99, to_addr=_RADIO_ADDR, cmd_map=_cmd_map("IC-705")
    )
    assert built.hex() == FIXTURE["clear"]["tx"]
