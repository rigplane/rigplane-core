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
* IC-7300: Advanced Manual (11a) p.19-11 "Memory content" (guide bytes
  q-@7 = 1-27, one block copy).
* IC-7300MK2: CI-V Reference Guide p.17 "Memory channel content" (guide
  bytes 1-33, one block copy).
* IC-7610: the legacy 26-byte record this package has always built
  (byte-identical; the manual's own 27-byte form is the deferred
  discrepancy, see the PR body and MOR-2055).

Every radio here is an in-process mock/simulator; all results are
simulated.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.commands._frame import build_civ_frame
from rigplane.commands.memory import (
    build_memory_clear,
    build_memory_contents_set,
)
from rigplane.profiles import MemoryWriteUnverifiedError, get_radio_profile
from rigplane.profiles.rig_loader import load_rig
from rigplane.radio import IcomRadio
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
    "000000"  # tone squelch off
    "000000"  # DTCS off
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
    "000000"  # tone squelch off
    "000000"  # DTCS off
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
    # !1 data|tone | !2-!4 tone | !5-!7 tsql | !8-@7 name (10). The
    # IC-7300 record has no duplex, DTCS, DV or call-sign fields at all.
    "IC-7300": (
        "0007"  # q,w: channel 7
        "00"  # e: split OFF | select OFF
        "0040071400"  # r-i: 14.074 MHz, LE BCD
        "01"  # o: mode USB (raw code)
        "01"  # !0: filter 1
        "01"  # !1: data OFF | TONE
        "001365"  # !2-!4: 136.5 Hz
        "000000"  # !5-!7: tone squelch off
        "46543820202020202020"  # !8-@7: "FT8", 10, space padded
    ),
    # IC-7300MK2 CI-V Reference Guide p.17, after ``1A 00``:
    # 1,2 channel | 3 split|select | 4-8 frequency | 9 mode | 10 filter |
    # 11 data|tone | 12-14 tone | 15-17 tsql | 18-33 name (16 characters).
    "IC-7300MK2": (
        "0007"  # 1,2: channel 7
        "00"  # 3: split OFF | select OFF
        "0040071400"  # 4-8: 14.074 MHz, LE BCD
        "01"  # 9: mode USB
        "01"  # 10: filter 1
        "01"  # 11: data OFF | TONE
        "001365"  # 12-14: 136.5 Hz
        "000000"  # 15-17: tone squelch off
        "46543820202020202020202020202020"  # 18-33: "FT8", 16, space padded
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
    """One repeater channel per the case table: FM (USB for the HF-only
    radios), TONE 136.5 Hz, DUP+; the IC-7610 override keeps its legacy
    Hz-based tone field."""
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
        "duplex": 2,
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
    """115 (705) / 114 (9700) / 27 (7300) / 33 (MK2) / 28 (7610 legacy)
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
    assert entry.tsql_centihz == 0
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


def test_dtcs_tone_mode_builds_where_the_record_declares_it() -> None:
    """The IC-705's tone nibble is 0-3 (guide p.17: OFF/TONE/TSQL/DTCS)."""
    frame = _build("IC-705", _mem("IC-705", tonemode=3))
    # byte 14 of the record: duplex 2 | DTCS 3
    record = frame[10:-1]  # after FE FE A4 E0 1A 00 + the 4-byte address
    assert record[9] == 0x23


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
        ("IC-9700", {"duplex": 3, "group": 1}, "duplex"),
        ("IC-705", {"duplex": 3}, "duplex"),  # a group/channel radio has 0-2
        ("IC-9700", {"mode": 0x22, "group": 3, "duplex": 2}, "duplex"),
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
    frame = _build("IC-9700", _mem("IC-9700", mode=0x22, group=3, duplex=3, tonemode=0))
    record = frame[9:-1]  # after FE FE A2 E0 1A 00 + the 3-byte address
    assert record[6] == 0x22  # mode DD
    assert record[9] == 0x30  # duplex 3 (RPS) | tone OFF


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300"])
async def test_a_refused_write_sends_nothing_on_the_wire(model: str) -> None:
    """The builder refusal surfaces at the radio and nothing is sent."""
    transport = MockTransport()
    radio = _radio(model, transport)
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
@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
async def test_an_out_of_range_clear_address_refuses(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(ValueError, match="channel"):
            await radio.memory_clear(100, group=_CASES[model][1])
    finally:
        radio._connected = False
    assert transport.sent_packets == []


# ---------------------------------------------------------------------------
# Round trip: write through the production radio path, read the slot back
# ---------------------------------------------------------------------------


class _MemoryStoreTransport(MockTransport):
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


def _radio(model: str, transport: MockTransport) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_stream_ready = True
    radio._last_civ_data_received = time.monotonic()
    return radio


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_write_then_read_returns_what_was_written(model: str) -> None:
    """Simulated round trip: the decoded entry equals the written channel
    (frequency, mode, duplex, tone mode/frequency, name) -- the write
    record and the merged read model agree through the production radio
    methods. The reply's own bytes are pinned too: the address, the
    select byte, the 47-byte channel block TWICE (the reply carries the
    split TX copy), then the name."""
    from rigplane.core.types import RepeaterShiftDirection, ToneSquelchType

    radio_addr, group, channel = _CASES[model]
    mem = _mem(model)
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _radio(model, transport)
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
    assert entry.tsql_centihz == 0
    assert entry.name == mem.name
    # The reply data after ``1A 00``, by hand from the manual tables: the
    # stored record, which carries its channel block twice (the record's
    # own DTCS/offset/DV fields are their declared OFF values, all
    # zeros).
    block = bytes.fromhex({"IC-705": _IC705_BLOCK, "IC-9700": _IC9700_BLOCK}[model])
    address = {
        "IC-705": bytes.fromhex("00020007"),
        "IC-9700": bytes.fromhex("020007"),
    }[model]
    assert transport.last_reply_data == (
        address + b"\x00" + block + block + mem.name.encode("ascii").ljust(16, b" ")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_boolean_addresses_refuse_without_sending(model: str) -> None:
    """``True`` is an ``int`` to a range check and would silently encode
    group/band 1; the shared encoder refuses it before any byte is built
    (the SDK path -- the dispatch arm checks the same rule eagerly)."""
    transport = MockTransport()
    radio = _radio(model, transport)
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
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_a_grouped_radio_refuses_an_unnamed_group(model: str) -> None:
    """MOR-3644: a grouped memory never accepts an unnamed group -- a
    channel-only clear or write would silently hit group/band 0 and could
    erase or overwrite a slot in a group the caller never named (the UI's
    channel-only store/clear flow is exactly that hazard; it is refused
    until the UI carries one explicit slot address)."""
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(ValueError, match="a memory group/band is required"):
            await radio.memory_clear(5)
        with pytest.raises(ValueError, match="a memory group/band is required"):
            await radio.set_memory_contents(_mem(model, group=None))
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2"])
async def test_the_written_wire_bytes_are_the_manual_record(model: str) -> None:
    """The IC-7300/IC-7300MK2 read reply shape is not in the provided
    manual extracts, so the read listing stays off for them; the round
    trip pins the write side: the write crosses the production radio
    path, the wire carries exactly the manual record, and the clear then
    empties the slot in its declared form."""
    radio_addr, group, channel = _CASES[model]
    mem = _mem(model)
    transport = _MemoryStoreTransport(_memory(model), radio_addr)
    radio = _radio(model, transport)
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
async def test_a_cleared_slot_reads_back_empty_ic705() -> None:
    """Simulated: after the 1A 00 + address + FF clear, the slot answers
    empty on the next read."""
    radio_addr, group, channel = _CASES["IC-705"]
    transport = _MemoryStoreTransport(_memory("IC-705"), radio_addr)
    radio = _radio("IC-705", transport)
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
@pytest.mark.parametrize("model", ["X6100", "X6200"])
async def test_radios_without_a_layout_still_refuse(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
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

    radio = YaesuCatRadio("/dev/null", profile=get_radio_profile("FTX-1"))
    with pytest.raises(NotImplementedError):
        await radio.set_memory_contents(_mem("IC-705"))
    with pytest.raises(NotImplementedError):
        await radio.memory_clear(5)
