"""Read-only Icom memory entries (MOR-3627): ``1A 00`` decode, address, profile.

The golden reply is a real IC-705 record (MOR-3650) with its frequency and
name replaced; the IC-9700 layout comes from the CI-V reference only (tier B-).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from rigplane.commands import build_memory_entry_read, parse_memory_entry
from rigplane.commands.memory import _encode_memory_address
from rigplane.core.types import (
    MemoryEntry,
    MemoryProfile,
    RepeaterShiftDirection,
    ToneSquelchType,
)
from rigplane.profiles import get_radio_profile

# IC-705 group 0, channel 4: 146.940 MHz FM, DUP+ 600 kHz, TONE 136.5 Hz,
# TSQL 136.5 Hz, DTCS 023, UR "CQCQCQ", name "TEST RPT" (hex of the reply
# bytes after ``1A 00``).
# fmt: off
_IC705_GOLDEN = bytes.fromhex(
    "0000" "0004" "00"  # group, channel, select
    "0000944601" "0501" "00" "21" "00" "001365" "001365" "000023" "00" "006000"
    "435143514351202020202020202020202020202020202020"  # UR, R1, R2
    "0000944601" "0501" "00" "21" "00" "001365" "001365" "000023" "00" "006000"
    "435143514351202020202020202020202020202020202020"
    "54455354205250542020202020202020"  # name
)
# fmt: on

# The read-side GHz ceiling comes from profile data (MOR-3983): the shipped
# IC-705 rig file declares no frequency_ranges and a write-layout frequency
# max_hz of 999_999_999, so the ceiling resolves to 0. Load the real profile
# so this synthetic address matches the real radio exactly.
_IC705 = get_radio_profile("IC-705").memory
_IC9700 = MemoryProfile(
    "band_channel", 1, 3, 1, 99, dd_groups=(3,), offset_10mhz_groups=(3,)
)


def _block(
    *,
    freq: str,
    mode: int = 0x05,
    data: int = 0x00,
    duplex_tone: int = 0x00,
    tone: str = "000885",
    offset: str = "000000",
) -> bytes:
    """One 47-byte channel block from the guide's field list (independent of
    the decoder). ``freq`` is ten digits, 1 GHz down to 1 Hz; ``offset`` is
    six digits, 10 MHz down to 100 Hz."""
    freq_bytes = bytes(int(freq[i : i + 2], 16) for i in (8, 6, 4, 2, 0))
    off = bytes(int(offset[i : i + 2], 16) for i in (4, 2, 0))
    return (
        freq_bytes
        + bytes([mode, 0x01, data, duplex_tone, 0x00])
        + bytes.fromhex(tone)
        + bytes.fromhex(tone)
        + bytes.fromhex("000023")
        + b"\x00"
        + off
        + b" " * 24
    )


def _ic9700_reply(block: bytes, *, band: int, channel: int, name: bytes) -> bytes:
    address = bytes([band]) + bytes.fromhex(f"{channel:04d}")
    return address + b"\x00" + block + block + name.ljust(16, b" ")


def test_decodes_the_real_icom_record_shape() -> None:
    assert parse_memory_entry(_IC705_GOLDEN, _IC705) == MemoryEntry(
        group=0,
        channel=4,
        frequency_hz=146_940_000,
        mode="FM",
        data_mode=False,
        duplex=RepeaterShiftDirection.PLUS,
        offset_hz=600_000,
        tone_mode=ToneSquelchType.TONE,
        tone_centihz=13_650,
        tsql_centihz=13_650,
        dtcs_code=23,
        name="TEST RPT",
    )


def test_leading_spaces_in_a_name_are_kept() -> None:
    reply = _IC705_GOLDEN[:-16] + b"  HOME".ljust(16, b" ")
    assert parse_memory_entry(reply, _IC705).name == "  HOME"


def test_empty_slot_answers_ff_after_the_address() -> None:
    entry = parse_memory_entry(bytes.fromhex("00000099ff"), _IC705)
    assert entry == MemoryEntry(group=0, channel=99, empty=True)


@pytest.mark.parametrize(
    ("duplex_tone", "duplex", "tone_mode"),
    [
        (0x00, RepeaterShiftDirection.SIMPLEX, ToneSquelchType.OFF),
        (0x11, RepeaterShiftDirection.MINUS, ToneSquelchType.TONE),
        (0x22, RepeaterShiftDirection.PLUS, ToneSquelchType.TSQL),
        (0x03, RepeaterShiftDirection.SIMPLEX, ToneSquelchType.DTCS),
    ],
)
def test_ic9700_band_layout_duplex_and_tone_nibbles(
    duplex_tone: int, duplex: RepeaterShiftDirection | None, tone_mode: ToneSquelchType
) -> None:
    block = _block(
        freq="0439000000", duplex_tone=duplex_tone, tone="000885", offset="050000"
    )
    entry = parse_memory_entry(
        _ic9700_reply(block, band=2, channel=5, name=b"UHF TEST"), _IC9700
    )
    assert (entry.group, entry.channel) == (2, 5)
    assert entry.frequency_hz == 439_000_000
    assert entry.duplex == duplex
    assert entry.tone_mode == tone_mode
    assert entry.offset_hz == 5_000_000
    assert entry.tone_centihz == 8_850
    assert entry.name == "UHF TEST"


def test_ic9700_rps_duplex_exists_only_in_dd_mode() -> None:
    def reply(mode: int) -> bytes:
        block = _block(freq="1296000000", mode=mode, duplex_tone=0x30)
        return _ic9700_reply(block, band=3, channel=1, name=b"")

    rps = parse_memory_entry(reply(0x22), _IC9700)
    assert (rps.mode, rps.duplex) == ("DD", None)
    with pytest.raises(ValueError):
        parse_memory_entry(reply(0x05), _IC9700)
    for duplex_tone in (0x10, 0x20):  # DUP-/DUP+ do not exist in DD mode
        block = _block(freq="1296000000", mode=0x22, duplex_tone=duplex_tone)
        with pytest.raises(ValueError):
            parse_memory_entry(
                _ic9700_reply(block, band=3, channel=1, name=b""), _IC9700
            )
    dd = _block(freq="1296000000", mode=0x22)
    assert (
        parse_memory_entry(_ic9700_reply(dd, band=3, channel=1, name=b""), _IC9700).mode
        == "DD"
    )


@pytest.mark.parametrize("start", [5, 52])
def test_ic705_rejects_dd_mode_in_either_block(start: int) -> None:
    reply = bytearray(_IC705_GOLDEN)
    reply[start + 5] = 0x22
    reply[start + 8] = 0x00  # simplex must not conceal an unsupported DD mode
    with pytest.raises(ValueError, match="mode"):
        parse_memory_entry(bytes(reply), _IC705)


@pytest.mark.parametrize("band", [1, 2])
def test_ic9700_rejects_dd_outside_profile_groups(band: int) -> None:
    reply = _ic9700_reply(
        _block(freq="0146000000", mode=0x22), band=band, channel=1, name=b""
    )
    with pytest.raises(ValueError, match="mode"):
        parse_memory_entry(reply, _IC9700)


def test_ic9700_frequency_ghz_digit_is_0_or_1() -> None:
    reply = _ic9700_reply(_block(freq="2296000000"), band=3, channel=1, name=b"")
    with pytest.raises(ValueError):
        parse_memory_entry(reply, _IC9700)


def test_ic9700_offset_10_mhz_digit_only_on_the_1200_mhz_band() -> None:
    def reply(band: int, freq: str) -> bytes:
        block = _block(freq=freq, offset="106000")
        return _ic9700_reply(block, band=band, channel=1, name=b"")

    assert parse_memory_entry(reply(3, "1296000000"), _IC9700).offset_hz == 10_600_000
    for band, freq in ((1, "0146940000"), (2, "0439000000")):
        with pytest.raises(ValueError):
            parse_memory_entry(reply(band, freq), _IC9700)
    for band in (1, 2, 3):
        with pytest.raises(ValueError, match="offset"):
            parse_memory_entry(
                reply(band, "0146940000"), replace(_IC9700, offset_10mhz_groups=())
            )


def test_ic9700_select_byte_has_no_split_flag() -> None:
    block = _block(freq="0439000000")
    good = _ic9700_reply(block, band=2, channel=5, name=b"")
    assert parse_memory_entry(good, _IC9700).channel == 5
    with pytest.raises(ValueError):
        parse_memory_entry(good[:3] + b"\x10" + good[4:], _IC9700)
    ic705 = _IC705_GOLDEN[:4] + b"\x10" + _IC705_GOLDEN[5:]
    assert parse_memory_entry(ic705, _IC705).name == "TEST RPT"  # split flag is valid


def test_mode_names_and_unknown_mode_codes() -> None:
    dv = parse_memory_entry(
        _ic9700_reply(
            _block(freq="0014600000", mode=0x17, data=0x01), band=1, channel=1, name=b""
        ),
        _IC9700,
    )
    assert (dv.mode, dv.data_mode) == ("DV", True)
    odd = parse_memory_entry(
        _ic9700_reply(
            _block(freq="0014600000", mode=0x42), band=1, channel=1, name=b""
        ),
        _IC9700,
    )
    assert odd.mode == "0x42"


@pytest.mark.parametrize(
    "reply",
    [
        _IC705_GOLDEN[:10],  # too short
        _IC705_GOLDEN[:-1],  # truncated name
        _IC705_GOLDEN + b"\x00",  # trailing byte
        bytes.fromhex("000000ff0004"),  # FF with extra bytes is not "empty"
        _IC705_GOLDEN[:5] + b"\x0a" + _IC705_GOLDEN[6:],  # non-BCD frequency nibble
        _IC705_GOLDEN[:13] + b"\x04" + _IC705_GOLDEN[14:],  # unknown tone-mode nibble
        _IC705_GOLDEN[:-3] + b"\x07" + _IC705_GOLDEN[-2:],  # control byte in the name
        _IC705_GOLDEN[:4] + b"\x04" + _IC705_GOLDEN[5:],  # unknown select byte
        _IC705_GOLDEN[:12] + b"\x02" + _IC705_GOLDEN[13:],  # data mode is 0 or 1
        _IC705_GOLDEN[:14] + b"\x01" + _IC705_GOLDEN[15:],  # digital squelch is X0
        _IC705_GOLDEN[:13]
        + b"\x31"
        + _IC705_GOLDEN[14:],  # duplex 3 is IC-9700 DD only
        _IC705_GOLDEN[:22] + b"\x08" + _IC705_GOLDEN[23:],  # DTCS digit is not octal
        _IC705_GOLDEN[:15] + b"\x01" + _IC705_GOLDEN[16:],  # tone's fixed byte is 00
        _IC705_GOLDEN[:9] + b"\x11" + _IC705_GOLDEN[10:],  # fixed GHz digit
        _IC705_GOLDEN[:27] + b"\x10" + _IC705_GOLDEN[28:],  # fixed 10 MHz offset digit
        _IC705_GOLDEN[:56] + b"\x11" + _IC705_GOLDEN[57:],  # same, TX block
        _IC705_GOLDEN[:9] + b"\x09" + _IC705_GOLDEN[10:],  # 100 MHz digit above 4
        _IC705_GOLDEN[:16] + b"\x33" + _IC705_GOLDEN[17:],  # tone 100 Hz digit above 2
        _IC705_GOLDEN[:63] + b"\x33" + _IC705_GOLDEN[64:],  # same, TX block
        _IC705_GOLDEN[:52] + b"\xfa" + _IC705_GOLDEN[53:],  # corrupt TX block
        bytes.fromhex("0100") + _IC705_GOLDEN[2:],  # group outside the profile
        _IC705_GOLDEN[:2] + bytes.fromhex("0100") + _IC705_GOLDEN[4:],  # channel too
    ],
)
def test_malformed_replies_raise(reply: bytes) -> None:
    with pytest.raises(ValueError):
        parse_memory_entry(reply, _IC705)


@pytest.mark.parametrize("reply", ["040001ff", "010000ff", "010100ff"])
def test_empty_replies_outside_the_ic9700_profile_raise(reply: str) -> None:
    with pytest.raises(ValueError):
        parse_memory_entry(bytes.fromhex(reply), _IC9700)


def test_address_bytes_follow_the_profile_and_are_bounds_checked() -> None:
    assert _encode_memory_address(_IC705, 0, 4) == bytes.fromhex("00000004")
    assert _encode_memory_address(_IC705, 12, 99) == bytes.fromhex("00120099")
    assert _encode_memory_address(_IC9700, 3, 99) == bytes.fromhex("030099")
    for group, channel in ((100, 0), (0, 100), (-1, 0)):
        with pytest.raises(ValueError):
            _encode_memory_address(_IC705, group, channel)
    for group, channel in ((0, 1), (4, 1), (1, 0), (1, 100)):
        with pytest.raises(ValueError):
            _encode_memory_address(_IC9700, group, channel)


def test_read_frames_are_1a00_with_the_address() -> None:
    ic705 = get_radio_profile("IC-705")
    frame = build_memory_entry_read(
        ic705.memory, 0, 4, to_addr=0xA4, cmd_map=ic705.command_map
    )
    assert frame == bytes.fromhex("fefea4e01a0000000004fd")
    ic9700 = get_radio_profile("IC-9700")
    frame = build_memory_entry_read(
        ic9700.memory, 2, 5, to_addr=0xA2, cmd_map=ic9700.command_map
    )
    assert frame == bytes.fromhex("fefea2e01a00020005fd")
