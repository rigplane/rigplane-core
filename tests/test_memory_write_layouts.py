"""Verified ``1A 00`` memory write layouts as profile data (MOR-3644).

Every radio that has memory commands now declares its verified write
record as a field table in its profile (``[memory.write_layout]``), and one
table-driven encoder builds the frame from that declaration. The fixture
bytes below are built BY HAND from each manual's own position table -- the
only permitted source -- and compared with the encoder output, so a wrong
offset or width in the TOML cannot pass:

* IC-705: CI-V Reference Guide (A7560-8EX, Jul. 2020) p.17 "Memory
  content". The guide prints the channel block's labels (6~52) twice,
  the name (53~68) after them, and its NOTE says the second block is the
  TX data, to be filled with the same values when Split is OFF -- so the
  record is select byte + the 47-byte block x ``copies`` + the 16-byte
  name, the same record a read reply carries (``parse_memory_entry``).
* IC-9700: CI-V Reference Guide (rev. 4) p.14 "Memory content" -- the
  same two-block shape (labels 5~51 twice, name 52~67, same NOTE).
* IC-7300: Advanced Manual (11a) p.19-11 "Memory content" -- the
  channel-data span r-!7 (14 bytes: frequency, mode, filter, data|tone,
  tone, tsql) is printed twice before the name !8-@7 (10 characters),
  with the same TX/split NOTE.
* IC-7300MK2: CI-V Reference Guide p.17 "Memory channel content" -- the
  span 4~17 printed twice, name 18-33 (16 characters), same NOTE.
* IC-7610: the legacy 26-byte record this package has always built
  (byte-identical; the manual's own 27-byte form is the deferred
  discrepancy, see the PR body and MOR-2055).

Every radio here is an in-process mock/simulator; all results are
simulated.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

import rigplane.runtime._civ_rx as civ_rx_module
import rigplane.runtime.radio as runtime_radio_module
from rigplane.commands import CONTROLLER_ADDR
from rigplane.commands._frame import build_civ_frame
from rigplane.commands.memory import (
    build_memory_clear,
    build_memory_contents_set,
)
from rigplane.core.exceptions import CommandError, CommandRejectedError
from rigplane.core.types import CivFrame, MemoryWriteField, MemoryWriteLayout
from rigplane.profiles import MemoryWriteUnverifiedError, get_radio_profile
from rigplane.profiles.rig_loader import load_rig
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane.types import MemoryChannel
from _helpers import CIV_HEADER_SIZE, wrap_civ_in_udp
from test_radio import MockTransport

RIGS = Path(__file__).resolve().parents[1] / "rigs"

# Per radio: (model, CI-V address, group, channel). The IC-705 group and
# the IC-9700 band code are part of the write address; the channel-only
# radios use group 0.
_CASES = {
    "IC-705": (0xA4, 2, 7),
    "IC-9700": (0xA2, 2, 7),
    "IC-7300": (0x94, 0, 7),
    "IC-7300MK2": (0xB6, 0, 7),
    "IC-7610": (0x98, 0, 1),
}

# The blank UR/R1/R2 call-sign fields: the read record (MOR-3650) pads
# unused call-sign fields with spaces.
_SP24 = "20" * 24

# The 14-byte channel-data span of the HF records (frequency 5 | mode 1
# | filter 1 | data|tone 1 | tone 3 | tsql 3): the IC-7300 manual prints
# it as r-!7, the IC-7300MK2 guide as 4~17 -- both print the span TWICE
# before the name, with the same TX/split NOTE as the IC-705/IC-9700.
_IC7300_BLOCK = (
    "0040071400"  # 14.074 MHz, LE BCD
    "01"  # mode USB (raw code)
    "01"  # filter 1
    "01"  # data OFF | TONE
    "001365"  # 136.5 Hz
    "000885"  # tsql unset: the declared default 88.5 Hz (the radio's own default, as seen in a real record; zeros are refused)
)
# The 47-byte channel blocks of the two-block records, written field by
# field in manual order: frequency 5 | mode 1 | filter 1 | data mode 1 |
# duplex|tone 1 | digital squelch 1 | tone 3 | tsql 3 | DTCS 3 | DV code
# 1 | duplex offset 3 | UR/R1/R2 24.
_IC705_BLOCK = (
    "0000944601"  # 146.940 MHz, LE BCD
    "05"  # mode FM (raw code, same as the read block)
    "01"  # filter 1
    "00"  # data mode OFF
    "21"  # duplex + | TONE
    "00"  # digital squelch off (X0, fixed low nibble)
    "001365"  # 136.5 Hz (the 1B 00 encoding, guide p.21)
    "000885"  # tsql unset: the declared default 88.5 Hz (the radio's own default, as seen in a real record; zeros are refused)
    "000023"  # DTCS polarity NN, code 023 (the radio's own default, as seen in a real record; zeros are refused)
    "00"  # DV code squelch off
    "000000" + _SP24  # duplex offset 0 (not exposed in this slice)  # UR/R1/R2 blank
)
_IC9700_BLOCK = (
    "0000004604"  # 446.000 MHz, LE BCD
    "05"  # mode FM
    "01"  # filter 1
    "00"  # data mode OFF
    "21"  # duplex + | TONE
    "00"  # digital squelch off
    "001365"  # 136.5 Hz
    "000885"  # tsql unset: the declared default 88.5 Hz (as above)
    "000023"  # DTCS polarity NN, code 023 (as above)
    "00"  # DV code squelch off
    "000000" + _SP24  # duplex offset 0  # UR/R1/R2 blank
)

# The hand-built manual records for the `_mem` inputs below. Offsets are
# the manual's own position labels; the hex is written field by field in
# manual order so a mis-declared offset in the TOML shifts a byte here.
_MANUAL_RECORDS = {
    # IC-705 CI-V Reference Guide p.17, after ``1A 00``: 1,2 group | 3,4
    # channel | 5 select | 6-52 channel block | the block AGAIN (the
    # guide prints 6~52 twice; its NOTE: the second block is the TX data,
    # filled with the same values when Split is OFF) | 53-68 name (16
    # characters, printed after the second block).
    "IC-705": (
        "00020007"  # group 2, channel 7 (2-byte BCD each)
        "00"  # 5: split OFF | select OFF
        + _IC705_BLOCK  # 6-52: the RX block
        + _IC705_BLOCK  # the TX/split copy repeats the RX values
        + "54455354205250542020202020202020"  # 53-68: "TEST RPT", 16
    ),
    # IC-9700 CI-V Reference Guide p.14, after ``1A 00``: 1 band | 2,3
    # channel | 4 select | 5-51 channel block | the block AGAIN (the
    # guide prints 5~51 twice, same NOTE as the IC-705) | 52-67 name
    # (16 characters).
    "IC-9700": (
        "020007"  # band 2 (430 MHz), channel 7
        "00"  # 4: select OFF (high nibble fixed 0)
        + _IC9700_BLOCK  # 5-51: the RX block
        + _IC9700_BLOCK  # the TX/split copy repeats the RX values
        + "55484620544553542020202020202020"  # 52-67: "UHF TEST", 16
    ),
    # IC-7300 Advanced Manual (11a) p.19-11, after ``1A 00``:
    # q,w channel | e split|select | r-i frequency | o mode | !0 filter |
    # !1 data|tone | !2-!4 tone | !5-!7 tsql -- the span printed AGAIN as
    # "r-!7" (the NOTE: the second copy is the TX data, filled with the
    # same values when Split is OFF) -- then !8-@7 name (10). The
    # IC-7300 record has no duplex, DTCS, DV or call-sign fields at all.
    "IC-7300": (
        "0007"  # q,w: channel 7
        "00"  # e: split OFF | select OFF
        + _IC7300_BLOCK  # r-!7: the RX span
        + _IC7300_BLOCK  # the TX/split copy repeats the RX values
        + "46543820202020202020"  # !8-@7: "FT8", 10, space padded
    ),
    # IC-7300MK2 CI-V Reference Guide p.17, after ``1A 00``:
    # 1,2 channel | 3 split|select | 4-8 frequency | 9 mode | 10 filter |
    # 11 data|tone | 12-14 tone | 15-17 tsql -- the span printed AGAIN as
    # "4 ~ 17" (same NOTE as the IC-7300) -- then 18-33 name (16
    # characters).
    "IC-7300MK2": (
        "0007"  # 1,2: channel 7
        "00"  # 3: split OFF | select OFF
        + _IC7300_BLOCK  # 4-17: the RX span
        + _IC7300_BLOCK  # the TX/split copy repeats the RX values
        + "46543820202020202020202020202020"  # 18-33: "FT8", 16, space padded
    ),
    # The legacy 26-byte record, byte-identical to the pre-MOR-3644
    # builder (the complete-frame golden in tests/test_memory_write_gate.py
    # pins the same shape through the whole radio path). The IC-7610 CI-V
    # Reference Guide (Rev. 4) p.11 documents a DIFFERENT 27-byte record
    # (channel, select, frequency, 2-byte mode, data|tone, tone, tsql,
    # 10-char name) and a ``1A 00 + channel + FF`` clear; this profile
    # deliberately keeps the historic bytes (MOR-2055; the owner decides
    # separately whether to switch).
    "IC-7610": (
        "0001"  # channel 1, 2-byte BCD
        "00"  # scan
        "0040071400"  # 14.074 MHz, LE BCD
        "01"  # mode (BCD of the int, legacy form)
        "01"  # filter
        "01"  # datamode<<4 | tonemode (TONE)
        "000885"  # tone frequency, legacy Hz-BCD form (885 -> 00 08 85)
        "000000"  # tsql frequency
        "46543800000000000000"  # "FT8", 10, NUL padded
        "00"  # record pad byte
    ),
}


def _memory(model: str):
    profile = get_radio_profile(model)
    assert profile.memory is not None
    return profile.memory


def _cmd_map(model: str):
    name = {"IC-7300MK2": "ic7300mk2"}.get(model, model.lower().replace("-", ""))
    return load_rig(RIGS / f"{name}.toml").to_command_map()


def _mem(model: str, **overrides) -> MemoryChannel:
    fields: dict = {
        "channel": _CASES[model][2],
        "group": _CASES[model][1],
        "frequency_hz": {
            "IC-705": 146_940_000,
            "IC-9700": 446_000_000,
        }.get(model, 14_074_000),
        "mode": 0x05 if model in ("IC-705", "IC-9700") else 0x01,
        "filter": 1,
        "scan": 0,
        "datamode": 0,
        "tonemode": 1,
        "duplex": 2 if model in ("IC-705", "IC-9700") else 0,
        "tone_centihz": 13_650,
        "name": {
            "IC-705": "TEST RPT",
            "IC-9700": "UHF TEST",
        }.get(model, "FT8"),
    }
    if model == "IC-7610":  # the legacy record reads the Hz field
        fields["tone_centihz"] = None
        fields["tone_freq_hz"] = 885
    fields.update(overrides)
    return MemoryChannel(**fields)


def _build(model: str, mem: MemoryChannel) -> bytes:
    profile = get_radio_profile(model)
    return build_memory_contents_set(
        mem,
        _memory(model),
        to_addr=_CASES[model][0],
        cmd_map=_cmd_map(model),
        modes=profile.modes,
        ctcss_tones_centihz=profile.ctcss_tones_centihz,
        data_mode_count=profile.data_mode_count,
    )


def _manual_frame(model: str) -> bytes:
    radio_addr = _CASES[model][0]
    return (
        bytes.fromhex(f"fefe{radio_addr:02x}e01a00")
        + bytes.fromhex(_MANUAL_RECORDS[model])
        + b"\xfd"
    )


# ---------------------------------------------------------------------------
# Position fixtures: the encoder output equals the manual record
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model",
    ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2", "IC-7610"],
)
def test_the_encoder_builds_the_manual_record(model: str) -> None:
    """The complete frame equals the hand-built bytes of the manual table."""
    assert _build(model, _mem(model)) == _manual_frame(model)


@pytest.mark.parametrize(
    "model",
    ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2", "IC-7610"],
)
def test_the_frame_length_is_the_manual_total(model: str) -> None:
    """115 (705) / 114 (9700) / 41 (7300) / 47 (MK2) / 28 (7610 legacy)
    data bytes after ``1A 00`` -- the position table's own byte count,
    both block copies included."""
    frame = _build(model, _mem(model))
    expected = len(_MANUAL_RECORDS[model]) // 2
    assert len(frame) == 6 + expected + 1  # header+1A00, data, FD


def _record(model: str, mem: MemoryChannel) -> bytes:
    """The encoder's write record (the bytes after the ``1A 00``
    command and the memory address)."""
    memory = _memory(model)
    address_len = {"group_channel": 4, "band_channel": 3, "channel": 2}[
        memory.addressing
    ]
    return _build(model, mem)[6 + address_len : -1]


@pytest.mark.parametrize(
    "model", ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2", "IC-7610"]
)
def test_the_block_repeats_the_profile_declared_number_of_times(model: str) -> None:
    """The copy count is profile data (``write_layout.copies``), read here
    from the loaded profile -- never a number pinned in the test -- so a
    data-only profile change flips it: the wire record carries the
    channel block exactly ``copies`` times between the select byte and
    the name, and is ``wire_record_length`` bytes long."""
    layout = _memory(model).write_layout
    assert layout is not None
    record = _record(model, _mem(model))
    assert len(record) == layout.wire_record_length()
    span = layout.data_block_span()
    assert span is not None
    block = record[span[0] : span[1]]
    for copy in range(layout.copies):
        start = span[0] + copy * len(block)
        assert record[start : start + len(block)] == block
    name_field = next(f for f in layout.fields if f.encoding in ("name", "name_nul"))
    name_start = name_field.offset + (layout.copies - 1) * len(block)
    pad = b"\x00" if name_field.encoding == "name_nul" else b" "
    expected_name = _mem(model).name.encode("ascii").ljust(name_field.length, pad)
    assert record[name_start : name_start + name_field.length] == expected_name


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_the_write_body_decodes_with_the_read_parser(model: str) -> None:
    """Write and read tied together (MOR-3644): the bytes the encoder
    builds for the ``1A 00`` write body are accepted by
    ``parse_memory_entry`` for the same profile and decode to what was
    written -- address, select byte, BOTH block copies and the name. A
    one-copy record fails the parser's length check, which is what makes
    the two-block declaration load-bearing."""
    from rigplane.commands.memory import parse_memory_entry
    from rigplane.core.types import RepeaterShiftDirection, ToneSquelchType

    mem = _mem(model)
    entry = parse_memory_entry(_build(model, mem)[6:-1], _memory(model))
    assert (entry.group, entry.channel) == (_CASES[model][1], _CASES[model][2])
    assert entry.frequency_hz == mem.frequency_hz
    assert entry.mode == "FM"
    assert entry.data_mode is False
    assert entry.duplex is RepeaterShiftDirection.PLUS
    assert entry.tone_mode is ToneSquelchType.TONE
    assert entry.tone_centihz == 13_650
    assert entry.tsql_centihz == 8_850  # unset tsql wrote the declared default
    assert entry.name == mem.name


# ---------------------------------------------------------------------------
# Negative pins: out-of-range values refuse before any byte is built
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2", "IC-7610"]
)
def test_an_over_long_name_refuses(model: str) -> None:
    limit = 10 if model in ("IC-7300", "IC-7610") else 16
    with pytest.raises(ValueError, match="name"):
        _build(model, _mem(model, name="X" * (limit + 1)))


@pytest.mark.parametrize(
    ("model", "overrides", "match"),
    [
        ("IC-705", {"channel": 100}, "channel"),
        ("IC-705", {"group": 100}, "group"),
        ("IC-9700", {"channel": 0}, "channel"),
        ("IC-9700", {"group": 4}, "group"),
        ("IC-7300", {"channel": 0}, "channel"),
        ("IC-7300MK2", {"channel": 100}, "channel"),
        ("IC-7610", {"channel": 102}, "channel"),
    ],
)
def test_an_out_of_range_address_refuses(
    model: str, overrides: dict, match: str
) -> None:
    with pytest.raises(ValueError, match=match):
        _build(model, _mem(model, **overrides))


@pytest.mark.parametrize(
    ("model", "overrides"),
    [
        ("IC-705", {"frequency_hz": 1_000_000_000}),  # GHz digit fixed 0
        ("IC-9700", {"frequency_hz": 1_500_000_000}),  # 100 MHz digit > 4
        ("IC-7300", {"frequency_hz": 75_000_000}),  # above the 74.8 MHz top
        ("IC-7300MK2", {"frequency_hz": 75_000_000}),
    ],
)
def test_an_out_of_range_frequency_refuses(model: str, overrides: dict) -> None:
    with pytest.raises(ValueError, match="frequency"):
        _build(model, _mem(model, **overrides))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2"])
def test_a_tone_outside_the_ctcss_domain_refuses(model: str) -> None:
    with pytest.raises(ValueError, match="[Tt]one"):
        _build(model, _mem(model, tone_centihz=12_345))


@pytest.mark.parametrize(
    ("model", "overrides"),
    [
        ("IC-7300", {"tonemode": 3}),  # no DTCS field on this radio
        ("IC-7300MK2", {"tonemode": 3}),
    ],
)
def test_a_tone_mode_the_record_cannot_store_refuses(
    model: str, overrides: dict
) -> None:
    """The IC-7300/MK2 record's tone nibble is 0=OFF, 1=TONE, 2=TSQL
    (Advanced Manual 11a p.19-11 / MK2 guide p.17); DTCS (3) is declared
    out of range as layout data (``tone_max = 2``)."""
    with pytest.raises(ValueError, match="tone mode"):
        _build(model, _mem(model, **overrides))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_dtcs_tone_mode_builds_with_a_caller_code(model: str) -> None:
    """MOR-3648: the DTCS field is caller data now (encoding ``dtcs``), so
    DTCS mode builds -- with an explicit code, the same rule an active
    TONE/TSQL mode follows; an inactive mode's field carries the default."""
    record = _record(model, _mem(model, tonemode=3, dtcs_code=125))
    assert record[17:20].hex() == "000125"  # polarity byte 00, code 125


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_dtcs_tone_mode_requires_an_explicit_code(model: str) -> None:
    with pytest.raises(ValueError, match="dtcs_code"):
        _build(model, _mem(model, tonemode=3))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_dtcs_tone_mode_still_refuses_a_fixed_dtcs_field(model: str) -> None:
    """The refusal guard stays for a layout whose DTCS bytes are fixed
    constants (no shipped profile declares one; an SDK-spliced layout could)."""
    memory = _memory(model)
    layout = memory.write_layout
    assert layout is not None
    fixed = replace(
        layout,
        fields=tuple(
            replace(f, encoding="fixed", value="000023") if f.field == "dtcs" else f
            for f in layout.fields
        ),
    )
    profile = get_radio_profile(model)
    with pytest.raises(ValueError, match="DTCS"):
        build_memory_contents_set(
            _mem(model, tonemode=3),
            replace(memory, write_layout=fixed),
            to_addr=_CASES[model][0],
            cmd_map=_cmd_map(model),
            modes=profile.modes,
            ctcss_tones_centihz=profile.ctcss_tones_centihz,
            data_mode_count=profile.data_mode_count,
        )


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_the_encoder_writes_a_caller_offset_and_dtcs(model: str) -> None:
    """MOR-3648: the duplex offset and the DTCS field are caller data. The
    offset is 3-byte little-endian BCD in 100 Hz units (the read side's own
    decode shape, MOR-3650); the DTCS field is the polarity byte plus the
    code as BCD under a fixed 0 digit. Both repeat in the TX/split copy."""
    record = _record(
        model, _mem(model, tonemode=3, dtcs_code=125, duplex_offset_hz=5_000_000)
    )
    assert record[17:20].hex() == "000125"  # polarity NN, code 125
    assert record[21:24].hex() == "000005"  # 5 MHz, LE 100 Hz units
    assert record[64:67].hex() == "000125"  # the TX/split copy repeats
    assert record[68:71].hex() == "000005"


def test_the_offset_10mhz_digit_only_where_the_profile_declares_it() -> None:
    """The 10 MHz offset digit exists only in the profile's
    ``offset_10mhz_groups`` (the read parser's own rule, shared)."""
    record = _record(
        "IC-9700",
        _mem(
            "IC-9700", group=3, frequency_hz=1_260_000_000, duplex_offset_hz=10_600_000
        ),
    )
    assert record[21:24].hex() == "006010"
    for model, group in (("IC-705", 2), ("IC-9700", 2)):
        with pytest.raises(ValueError, match="offset"):
            _build(model, _mem(model, group=group, duplex_offset_hz=10_600_000))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"dtcs_code": 88}, "DTCS code is not three octal digits"),
        ({"dtcs_code": 0}, "all-zero"),
        ({"dtcs_code": 1000}, "DTCS"),
        ({"dtcs_code": -1}, "DTCS"),
        ({"dtcs_code": "023"}, "DTCS"),
        ({"dtcs_polarity": 256}, "polarity"),
        ({"dtcs_polarity": 253}, "polarity"),
        ({"dtcs_polarity": "NN"}, "polarity"),
        ({"duplex_offset_hz": -600}, "offset"),
        ({"duplex_offset_hz": 150}, "offset"),
        ({"duplex_offset_hz": 100_000_000}, "offset"),
        ({"duplex_offset_hz": 600.0}, "offset"),
    ],
)
def test_an_unknown_offset_or_dtcs_value_refuses_with_a_reason(
    model: str, overrides: dict, match: str
) -> None:
    """An unknown value is rejected with a reason, never a crash and never
    a silently truncated byte."""
    with pytest.raises(ValueError, match=match):
        _build(model, _mem(model, **overrides))


def test_a_zero_dtcs_code_is_refused_like_the_radio_does() -> None:
    """Hardware evidence (MOR-3650 capture): a real IC-705 NAKs a record
    whose DTCS field is all-zero and ACKs the same record with 023. The
    shared encoder refuses the zero before a byte is built instead of
    reintroducing that known-rejected record."""
    capture = json.loads(
        (
            Path(__file__).parent / "fixtures" / "ic705_memory_calibration.json"
        ).read_text(encoding="utf-8")
    )
    frames = capture["encoder_frames"]
    assert frames["zero_dtcs_rejected"]["rx"].endswith("fafd")  # NAK
    assert frames["dtcs_023_accepted"]["rx"].endswith("fbfd")  # ACK
    for model in ("IC-705", "IC-9700"):
        with pytest.raises(ValueError, match="all-zero"):
            _build(model, _mem(model, dtcs_code=0))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_a_terminator_polarity_byte_refuses(model: str) -> None:
    """0xFD is the CI-V frame terminator: as a DTCS polarity byte inside the
    record it would end the frame early, so the encoder refuses it."""
    with pytest.raises(ValueError, match="terminator"):
        _build(model, _mem(model, dtcs_polarity=0xFD))


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"dtcs_code": 23}, "dtcs_code"),
        ({"dtcs_polarity": 1}, "dtcs_polarity"),
        ({"duplex_offset_hz": 600_000}, "duplex_offset_hz"),
    ],
)
def test_radios_without_the_fields_refuse_the_values(
    overrides: dict, match: str
) -> None:
    """The IC-7300 record has no DTCS or duplex-offset field at all: a
    caller value would be silently dropped, so it refuses (the shared
    ``_check_field_storage`` guard)."""
    with pytest.raises(ValueError, match=match):
        _build("IC-7300", _mem("IC-7300", **overrides))


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_a_dv_memory_builds_on_both_dv_radios(model: str) -> None:
    mem = _mem(model, mode=0x17, tonemode=0, duplex=0)
    assert _build(model, mem)


@pytest.mark.parametrize(("model", "mode"), [("IC-705", 0x06), ("IC-7610", 0x12)])
def test_mode_declared_by_the_active_profile_still_builds(
    model: str, mode: int
) -> None:
    assert _build(model, _mem(model, mode=mode))


@pytest.mark.parametrize("datamode", [2, 3], ids=["DATA2", "DATA3"])
def test_the_ic7610_record_stores_its_three_data_modes(datamode: int) -> None:
    """The IC-7610's record carries DATA1-3 in the data nibble (guide
    p.11: 0=OFF, 1=DATA 1, 2=DATA 2, 3=DATA 3 -- the profile's
    ``[data_mode].count = 3``); the legacy behaviour, preserved."""
    frame = _build("IC-7610", _mem("IC-7610", datamode=datamode, tonemode=0))
    record = frame[8:-1]  # after FE FE 98 E0 1A 00 + the 2-byte channel
    assert record[8] == datamode << 4


@pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2"])
def test_a_data_mode_the_radio_does_not_have_refuses(model: str) -> None:
    """The other manuals' records carry data mode 0/1 only (their
    profiles declare ``[data_mode].count = 1``)."""
    with pytest.raises(ValueError, match="data mode"):
        _build(model, _mem(model, datamode=2))


@pytest.mark.parametrize(
    ("model", "overrides", "match"),
    [
        # DD mode (0x22) is a mode only in the IC-9700's DD groups (band 3).
        ("IC-9700", {"mode": 0x22, "group": 1}, "DD mode"),
        ("IC-705", {"mode": 0x22}, "DD mode"),  # no DD groups on this radio
        # RPS (duplex 3) exists only in DD mode; DUP-/DUP+ never in DD.
        ("IC-9700", {"duplex": 3, "group": 1, "frequency_hz": 146_940_000}, "duplex"),
        ("IC-705", {"duplex": 3}, "duplex"),  # a group/channel radio has 0-2
        (
            "IC-9700",
            {"mode": 0x22, "group": 3, "duplex": 2, "frequency_hz": 1_260_000_000},
            "duplex",
        ),
    ],
)
def test_dd_and_rps_combinations_refuse_outside_their_modes(
    model: str, overrides: dict, match: str
) -> None:
    """The reader's own DD/duplex rules (``_parse_block``), shared with the
    encoder: a record the write builds must always be one the read side
    accepts back."""
    with pytest.raises(ValueError, match=match):
        _build(model, _mem(model, **overrides))


def test_dd_mode_with_rps_builds_in_a_dd_group() -> None:
    """IC-9700 band 3 (1.2 GHz, ``dd_groups``): DD mode with RPS -- the one
    combination the guide documents (p.14: RPS in DD, DUP+/- elsewhere)."""
    frame = _build(
        "IC-9700",
        _mem(
            "IC-9700",
            mode=0x22,
            group=3,
            duplex=3,
            tonemode=0,
            frequency_hz=1_260_000_000,
        ),
    )
    record = frame[9:-1]  # after FE FE A2 E0 1A 00 + the 3-byte address
    assert record[6] == 0x22  # mode DD
    assert record[9] == 0x30  # duplex 3 (RPS) | tone OFF


@pytest.mark.parametrize(
    ("group", "frequency"),
    [
        (1, 144_000_000),
        (1, 148_000_000),
        (2, 430_000_000),
        (2, 450_000_000),
        (3, 1_240_000_000),
        (3, 1_300_000_000),
    ],
)
def test_memory_band_frequency_edges_build(group: int, frequency: int) -> None:
    assert _build("IC-9700", _mem("IC-9700", group=group, frequency_hz=frequency))


def test_no_frequency_band_is_invented_for_an_undeclared_profile() -> None:
    profile = get_radio_profile("IC-9700")
    memory = replace(profile.memory, frequency_ranges=())
    assert build_memory_contents_set(
        _mem("IC-9700", group=1),
        memory,
        to_addr=0xA2,
        cmd_map=_cmd_map("IC-9700"),
        modes=profile.modes,
        ctcss_tones_centihz=profile.ctcss_tones_centihz,
        data_mode_count=profile.data_mode_count,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("filter", 2),
        ("scan", 1),
        ("datamode", 1),
        ("tonemode", 1),
        ("duplex", 1),
        ("tone_freq_hz", 88),
        ("tsql_freq_hz", 88),
        ("tone_centihz", 8850),
        ("tsql_centihz", 8850),
    ],
)
def test_a_layout_cannot_silently_drop_optional_memory_fields(
    field: str, value: int
) -> None:
    profile = get_radio_profile("IC-705")
    layout = MemoryWriteLayout(
        clear="1a00_ff",
        record_length=10,
        fields=(
            MemoryWriteField("frequency", 0, 5, "frequency"),
            MemoryWriteField("mode", 5, 1, "mode"),
            MemoryWriteField("name", 6, 4, "name"),
        ),
    )
    mem = _mem("IC-705", duplex=0, tonemode=0, tone_centihz=None, name="TEST")
    mem = replace(mem, **{field: value})
    with pytest.raises(ValueError, match=field):
        build_memory_contents_set(
            mem,
            replace(profile.memory, write_layout=layout),
            to_addr=0xA4,
            cmd_map=_cmd_map("IC-705"),
            modes=profile.modes,
            ctcss_tones_centihz=profile.ctcss_tones_centihz,
        )


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    ("model", "overrides", "match"),
    [
        ("IC-9700", {"group": 1}, "frequency"),
        ("IC-9700", {"group": 2, "frequency_hz": 146_940_000}, "frequency"),
        ("IC-9700", {"group": 3}, "frequency"),
        ("IC-9700", {"group": 1, "frequency_hz": 143_999_999}, "frequency"),
        ("IC-9700", {"group": 1, "frequency_hz": 148_000_001}, "frequency"),
        ("IC-7300", {"duplex": 1}, "duplex"),
        ("IC-7300MK2", {"duplex": 2}, "duplex"),
        ("IC-7610", {"duplex": 1}, "duplex"),
        ("IC-705", {"scan": 1}, "scan"),
        ("IC-9700", {"scan": 1}, "scan"),
    ],
)
async def test_unstorable_memory_values_refuse_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    overrides: dict,
    match: str,
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match=match):
            await radio.set_memory_contents(_mem(model, **overrides))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize(
    "model", ["IC-705", "IC-9700", "IC-7300", "IC-7300MK2", "IC-7610"]
)
@pytest.mark.parametrize("name", [None, [], {}, 123, b"NAME"])
async def test_non_string_names_refuse_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    name: object,
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="name must be a string"):
            await radio.set_memory_contents(_mem(model, name=name))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300"])
async def test_a_refused_write_sends_nothing_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """The builder refusal surfaces at the radio and nothing is sent."""
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="name"):
            await radio.set_memory_contents(_mem(model, name="X" * 40))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


# ---------------------------------------------------------------------------
# Clear: 1A 00 + address + FF, and the legacy 0x0B form for the IC-7610
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "frame"),
    [
        ("IC-705", "fefea4e01a0000020007fffd"),
        ("IC-9700", "fefea2e01a00020007fffd"),
        ("IC-7300", "fefe94e01a000007fffd"),
        ("IC-7300MK2", "fefeb6e01a000007fffd"),
    ],
)
def test_the_clear_frame_is_the_address_plus_ff(model: str, frame: str) -> None:
    """IC-705 guide p.17 and IC-9700 p.14 "To clear the memory channel
    contents on 1A 00", IC-7300 p.19-11 ("e: FF, r: None") and IC-7300MK2
    p.17 ("send the command 1A 00 XX XX FF"): the clear keeps the radio's
    own address form and appends FF."""
    radio_addr, group, channel = _CASES[model]
    built = build_memory_clear(
        _memory(model), group, channel, to_addr=radio_addr, cmd_map=_cmd_map(model)
    )
    assert built == bytes.fromhex(frame)


def test_the_ic7610_keeps_its_legacy_clear_frame() -> None:
    """The 0x0B + channel form, unchanged on purpose: the IC-7610 CI-V
    Reference Guide p.11 documents the clear as ``1A 00 + channel + FF``
    -- the discrepancy is deferred to MOR-2055 and named in the PR body."""
    built = build_memory_clear(
        _memory("IC-7610"), 0, 10, to_addr=0x98, cmd_map=_cmd_map("IC-7610")
    )
    assert built == bytes.fromhex("fefe98e00b0010fd")


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
async def test_an_out_of_range_clear_address_refuses(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="channel"):
            await radio.memory_clear(100, group=_CASES[model][1])
    finally:
        radio._connected = False
    assert transport.sent_packets == []


# ---------------------------------------------------------------------------
# Round trip: write through the production radio path, read the slot back
# ---------------------------------------------------------------------------


class _MemoryAckTransport(MockTransport):
    """The simulated peer ACKs memory mutations, leaving reads to the store."""

    def __init__(self, reply_command: int | None = 0xFB) -> None:
        super().__init__()
        self.reply_command = reply_command

    async def send_tracked(self, data: bytes) -> None:
        await super().send_tracked(data)
        civ = data[CIV_HEADER_SIZE:]
        mutation = civ[4] == 0x0B or (
            civ[4:6] == b"\x1a\x00" and (len(civ) > 16 or civ[-2] == 0xFF)
        )
        if mutation and self.reply_command is not None:
            self.queue_response(
                wrap_civ_in_udp(
                    build_civ_frame(CONTROLLER_ADDR, civ[2], self.reply_command)
                )
            )


class _MemoryStoreTransport(_MemoryAckTransport):
    """A table-driven CI-V memory store built from the SAME layout
    declaration the production encoder uses.

    A ``1A 00`` SET is stored by address (its length is checked against
    the profile's own ``wire_record_length``, block copies included); a
    GET is answered with the stored record AS IS -- the write body is the
    same record a read reply carries (select byte, the channel block
    ``copies`` times, the name), so no synthesis happens here; a
    ``1A 00 + address + FF`` clear empties the slot. The double cannot
    invent a layout of its own.
    """

    def __init__(self, memory, radio_addr: int) -> None:
        super().__init__()
        self._radio_addr = radio_addr
        self._address_len = {
            "group_channel": 4,
            "band_channel": 3,
            "channel": 2,
        }[memory.addressing]
        layout = memory.write_layout
        assert layout is not None
        self._record_len = layout.wire_record_length()
        self._slots: dict[bytes, bytes] = {}
        self.cleared: list[bytes] = []
        self.last_reply_data: bytes | None = None

    async def send_tracked(self, data: bytes) -> None:
        civ = data[CIV_HEADER_SIZE:]
        if civ[4:6] != b"\x1a\x00":
            await super().send_tracked(data)
            return
        payload = civ[6:-1]
        address, body = payload[: self._address_len], payload[self._address_len :]
        if not body:  # GET
            await super().send_tracked(data)
            stored = self._slots.get(address)
            reply_data = address + (stored if stored is not None else b"\xff")
            reply = build_civ_frame(
                CONTROLLER_ADDR, self._radio_addr, 0x1A, sub=0x00, data=reply_data
            )
            self.last_reply_data = reply_data
            self.queue_response(wrap_civ_in_udp(reply))
            return
        if body == b"\xff":
            self.cleared.append(address)
            self._slots.pop(address, None)
        else:
            assert len(body) == self._record_len
            self._slots[address] = body
        await super().send_tracked(data)


def _scope_clock(monkeypatch: pytest.MonkeyPatch, clock: VirtualClock) -> None:
    """Point the exercised runtime modules' own ``time``/``asyncio`` at ``clock``.

    The send-gate stamps and the ``_civ_min_interval`` sleeps live in
    ``runtime/_civ_rx.py``; the construction stamp and the readiness window
    live in ``runtime/radio.py``. Only those two modules' names are replaced
    (``VirtualClock.scope_to``); the event loop itself keeps working because
    the ``virtual_clock`` fixture installs the same clock as ``loop.time``.
    """
    clock.scope_to(monkeypatch, civ_rx_module)
    clock.scope_to(monkeypatch, runtime_radio_module)


def _sim_radio(model: str, transport: MockTransport, clock: VirtualClock) -> IcomRadio:
    """Build the simulator double with its stamps on the virtual clock.

    The caller scopes ``clock`` to the runtime modules first
    (``_scope_clock``), so the stamps below read virtual time and the
    radio's own finite idle window (``_civ_ready_idle_timeout``, default
    5 s) covers the whole test: the instant in-process transports advance
    the clock by milliseconds at most. No timeout is raised, and none is
    infinite.
    """
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_stream_ready = True
    radio._last_civ_data_received = clock()
    radio._civ_last_waiter_gc_monotonic = clock()
    return radio


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300"])
@pytest.mark.parametrize(
    "overrides",
    [
        {"mode": 0x12},
        {"tonemode": 1, "tone_centihz": None},
        {"tonemode": 2, "tsql_centihz": None},
        {"tonemode": 3},
        {"tone_freq_hz": 123},
    ],
)
async def test_sdk_memory_write_refuses_before_sending(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    overrides: dict,
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = _MemoryAckTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError):
            await radio.set_memory_contents(_mem(model, **overrides))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
@pytest.mark.parametrize("default_tone", [-1, 12345])
@pytest.mark.parametrize("operation", ["write", "clear"])
async def test_a_default_tone_outside_the_ctcss_domain_is_not_admitted(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    default_tone: int,
    operation: str,
) -> None:
    """The SDK gate applies the loader's domain rule: a spliced-in default
    tone the profile's CTCSS table lacks refuses like no layout at all."""
    _scope_clock(monkeypatch, virtual_clock)
    transport = _MemoryAckTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    profile = radio._profile
    layout = replace(profile.memory.write_layout, default_tone_centihz=default_tone)
    radio._profile = replace(
        profile, memory=replace(profile.memory, write_layout=layout)
    )
    try:
        with pytest.raises(MemoryWriteUnverifiedError):
            if operation == "write":
                await radio.set_memory_contents(_mem(model))
            else:
                await radio.memory_clear(_CASES[model][2], group=_CASES[model][1])
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
@pytest.mark.parametrize("operation", ["write", "clear"])
async def test_memory_mutations_report_nak(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    operation: str,
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = _MemoryAckTransport(reply_command=0xFA)
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(CommandRejectedError):
            if operation == "write":
                await radio.set_memory_contents(_mem(model))
            else:
                await radio.memory_clear(_CASES[model][2], group=_CASES[model][1])
    finally:
        radio._connected = False
    assert len(transport.sent_packets) == 1


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
@pytest.mark.parametrize("operation", ["write", "clear"])
@pytest.mark.parametrize("reply", [None, CivFrame(0xE0, 0xA4, 0x1A, 0, b"", 0)])
async def test_memory_mutations_need_an_ack(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    model: str,
    operation: str,
    reply: CivFrame | None,
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    radio = _sim_radio(model, MockTransport(), virtual_clock)
    radio._send_civ_raw = AsyncMock(return_value=reply)
    try:
        with pytest.raises(CommandError):
            if operation == "write":
                await radio.set_memory_contents(_mem(model))
            else:
                await radio.memory_clear(_CASES[model][2], group=_CASES[model][1])
    finally:
        radio._connected = False
    radio._send_civ_raw.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_write_then_read_returns_what_was_written(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """Simulated round trip: the decoded entry equals the written channel
    (frequency, mode, duplex, tone mode/frequency, name) -- the write
    record and the merged read model agree through the production radio
    methods. The reply's own bytes are pinned too: the address, the
    select byte, the 47-byte channel block TWICE (the reply carries the
    split TX copy), then the name."""
    from rigplane.core.types import RepeaterShiftDirection, ToneSquelchType

    _scope_clock(monkeypatch, virtual_clock)
    radio_addr, group, channel = _CASES[model]
    mem = _mem(model)
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        await radio.set_memory_contents(mem)
        entries = await radio.read_memory_entries(group, [channel])
    finally:
        radio._connected = False
    assert len(entries) == 1
    entry = entries[0]
    assert (entry.group, entry.channel) == (group, channel)
    assert entry.frequency_hz == mem.frequency_hz
    assert entry.mode == "FM"
    assert entry.data_mode is False
    assert entry.duplex is RepeaterShiftDirection.PLUS
    assert entry.tone_mode is ToneSquelchType.TONE
    assert entry.tone_centihz == 13_650
    assert entry.tsql_centihz == 8_850  # unset tsql wrote the declared default
    assert entry.name == mem.name
    # The reply data after ``1A 00``, by hand from the manual tables: the
    # stored record, which carries its channel block twice (the record's
    # own DTCS/offset/DV fields are their declared fixed values).
    block = bytes.fromhex({"IC-705": _IC705_BLOCK, "IC-9700": _IC9700_BLOCK}[model])
    address = {
        "IC-705": bytes.fromhex("00020007"),
        "IC-9700": bytes.fromhex("020007"),
    }[model]
    assert transport.last_reply_data == (
        address + b"\x00" + block + block + mem.name.encode("ascii").ljust(16, b" ")
    )


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_write_with_offset_and_dtcs_reads_back_equals(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """Simulated repeater write (MOR-3648): a caller duplex offset and DTCS
    code cross the production write path and read back exactly."""
    from rigplane.core.types import RepeaterShiftDirection, ToneSquelchType

    _scope_clock(monkeypatch, virtual_clock)
    radio_addr, group, channel = _CASES[model]
    mem = _mem(model, tonemode=3, dtcs_code=125, duplex_offset_hz=5_000_000)
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        await radio.set_memory_contents(mem)
        entry = (await radio.read_memory_entries(group, [channel]))[0]
    finally:
        radio._connected = False
    assert entry.duplex is RepeaterShiftDirection.PLUS
    assert entry.offset_hz == 5_000_000
    assert entry.tone_mode is ToneSquelchType.DTCS
    assert entry.dtcs_code == 125
    assert entry.dtcs_polarity == 0
    assert entry.filter == mem.filter
    assert entry.split is False


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_read_modify_write_round_trip_preserves_the_channel(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """Read-modify-write is exact (MOR-3648): the read model carries the
    filter, the DTCS code and polarity, the offset and the split block, so
    rewriting a saved channel with one changed field loses nothing else."""
    from rigplane.core.types import Mode, RepeaterShiftDirection, ToneSquelchType

    tone_nibble = {
        ToneSquelchType.OFF: 0,
        ToneSquelchType.TONE: 1,
        ToneSquelchType.TSQL: 2,
        ToneSquelchType.DTCS: 3,
    }
    duplex_nibble = {
        RepeaterShiftDirection.SIMPLEX: 0,
        RepeaterShiftDirection.MINUS: 1,
        RepeaterShiftDirection.PLUS: 2,
    }
    _scope_clock(monkeypatch, virtual_clock)
    radio_addr, group, channel = _CASES[model]
    original = _mem(
        model, tonemode=3, dtcs_code=125, duplex_offset_hz=5_000_000, filter=2
    )
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        await radio.set_memory_contents(original)
        saved = (await radio.read_memory_entries(group, [channel]))[0]
        # A split channel is not representable by the write API (the record
        # fills the TX copy with the RX values); the read model shows it.
        assert saved.split is False
        assert saved.filter is not None and saved.dtcs_polarity is not None
        assert saved.tone_mode is not None and saved.duplex is not None
        new_frequency = {"IC-705": 145_000_000, "IC-9700": 439_000_000}[model]
        rewritten = MemoryChannel(
            channel=saved.channel,
            group=saved.group,
            frequency_hz=new_frequency,  # the one intended change
            mode=Mode[saved.mode.replace("-", "_")].value,
            filter=saved.filter,
            scan=0,
            datamode=int(saved.data_mode),
            tonemode=tone_nibble[saved.tone_mode],
            duplex=duplex_nibble[saved.duplex],
            tone_centihz=saved.tone_centihz,
            tsql_centihz=saved.tsql_centihz,
            dtcs_code=saved.dtcs_code,
            dtcs_polarity=saved.dtcs_polarity,
            duplex_offset_hz=saved.offset_hz,
            name=saved.name,
        )
        await radio.set_memory_contents(rewritten)
        reread = (await radio.read_memory_entries(group, [channel]))[0]
    finally:
        radio._connected = False
    # Everything but the frequency survived; the TX/split copy tracks the
    # RX values, frequency included (the record's own NOTE).
    assert saved.tx_block is not None
    assert reread.tx_block == replace(saved.tx_block, frequency_hz=new_frequency)
    assert replace(
        reread, frequency_hz=saved.frequency_hz, tx_block=saved.tx_block
    ) == (saved)


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_a_refused_offset_or_dtcs_sends_nothing_on_the_wire(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="DTCS"):
            await radio.set_memory_contents(_mem(model, dtcs_code=88))
        with pytest.raises(ValueError, match="all-zero"):
            await radio.set_memory_contents(_mem(model, dtcs_code=0))
        with pytest.raises(ValueError, match="terminator"):
            await radio.set_memory_contents(_mem(model, dtcs_polarity=0xFD))
        with pytest.raises(ValueError, match="offset"):
            await radio.set_memory_contents(_mem(model, duplex_offset_hz=-1))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_boolean_addresses_refuse_without_sending(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """``True`` is an ``int`` to a range check and would silently encode
    group/band 1; the shared encoder refuses it before any byte is built
    (the SDK path -- the dispatch arm checks the same rule eagerly)."""
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="group must be an integer"):
            await radio.memory_clear(5, group=True)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="group must be an integer"):
            await radio.set_memory_contents(
                _mem(model, group=True)  # type: ignore[arg-type]
            )
        with pytest.raises(ValueError, match="channel must be an integer"):
            await radio.memory_clear(True, group=2)  # type: ignore[arg-type]
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_a_grouped_radio_refuses_an_unnamed_group(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """MOR-3644: a grouped memory never accepts an unnamed group -- a
    channel-only clear or write would silently hit group/band 0 and could
    erase or overwrite a slot in a group the caller never named (the UI's
    channel-only store/clear flow is exactly that hazard; it is refused
    until the UI carries one explicit slot address)."""
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(ValueError, match="a memory group/band is required"):
            await radio.memory_clear(5)
        with pytest.raises(ValueError, match="a memory group/band is required"):
            await radio.set_memory_contents(_mem(model, group=None))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
async def test_the_written_wire_bytes_are_the_manual_record(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    """The IC-7300/IC-7300MK2 read reply shape is not in the provided
    manual extracts, so the read listing stays off for them; the round
    trip pins the write side: the write crosses the production radio
    path, the wire carries exactly the manual record, and the clear then
    empties the slot in its declared form."""
    _scope_clock(monkeypatch, virtual_clock)
    radio_addr, group, channel = _CASES[model]
    mem = _mem(model)
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        await radio.set_memory_contents(mem)
        await radio.memory_clear(channel, group=group)
    finally:
        radio._connected = False
    frames = [packet[CIV_HEADER_SIZE:] for packet in transport.sent_packets]
    # The clear frame by hand: 1A 00 + the channel as 2-byte packed BCD
    # (decimal digits, high pair first) + FF.
    digits = f"{channel:04d}"
    clear = (
        bytes.fromhex(f"fefe{radio_addr:02x}e01a00")
        + bytes(
            [
                (int(digits[0]) << 4) | int(digits[1]),
                (int(digits[2]) << 4) | int(digits[3]),
            ]
        )
        + b"\xff\xfd"
    )
    assert frames == [_manual_frame(model), clear]
    assert transport.cleared == [frames[1][6:-2]]


@pytest.mark.asyncio
@pytest.mark.timeout(30)
async def test_a_cleared_slot_reads_back_empty_ic705(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    """Simulated: after the 1A 00 + address + FF clear, the slot answers
    empty on the next read."""
    _scope_clock(monkeypatch, virtual_clock)
    radio_addr, group, channel = _CASES["IC-705"]
    transport = _MemoryStoreTransport(_memory("IC-705"), radio_addr)
    radio = _sim_radio("IC-705", transport, virtual_clock)
    try:
        await radio.set_memory_contents(_mem("IC-705"))
        entry = (await radio.read_memory_entries(group, [channel]))[0]
        assert not entry.empty
        await radio.memory_clear(channel, group=group)
        entry = (await radio.read_memory_entries(group, [channel]))[0]
    finally:
        radio._connected = False
    assert entry.empty


# ---------------------------------------------------------------------------
# Radios left refused: no layout, no write
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.timeout(30)
@pytest.mark.parametrize("model", ["X6100", "X6200"])
async def test_radios_without_a_layout_still_refuse(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, model: str
) -> None:
    _scope_clock(monkeypatch, virtual_clock)
    transport = MockTransport()
    radio = _sim_radio(model, transport, virtual_clock)
    try:
        with pytest.raises(MemoryWriteUnverifiedError) as excinfo:
            await radio.set_memory_contents(_mem("IC-705"))
        with pytest.raises(MemoryWriteUnverifiedError):
            await radio.memory_clear(5)
    finally:
        radio._connected = False
    assert excinfo.value.reason_code == "memory_write_unverified"
    assert transport.sent_packets == []


@pytest.mark.asyncio
async def test_ftx1_has_no_write_path_at_all() -> None:
    """FTX-1: the Yaesu backend implements neither command
    (``backends/yaesu_cat/radio.py`` raises NotImplementedError), and its
    profile declares no write layout, so the shared admission gate refuses
    before anything is enqueued -- no write path is added here."""
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    with pytest.raises(NotImplementedError):
        await radio.set_memory_contents(_mem("IC-705"))
    with pytest.raises(NotImplementedError):
        await radio.memory_clear(5)
