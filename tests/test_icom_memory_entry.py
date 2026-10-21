"""Read-only Icom memory entries (MOR-3627): ``1A 00`` decode, address, profile.

The golden reply is a real IC-705 record (MOR-3650) with its frequency and
name replaced; the IC-9700 layout comes from the CI-V reference only (tier B-).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import build_memory_entry_read, parse_memory_entry
from rigplane.commands.memory import _encode_memory_address
from rigplane.core.types import MemoryEntry, RepeaterShiftDirection, ToneSquelchType
from rigplane.profiles import MemoryProfile, get_radio_profile
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from test_profile_identity_metadata import _minimal_toml, _write_toml

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

_IC705 = MemoryProfile("group_channel", 0, 99, 0, 99)
_IC9700 = MemoryProfile("band_channel", 1, 3, 1, 99)


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
        (0x30, None, ToneSquelchType.OFF),
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


def test_shipped_profiles_declare_memory_addressing_and_capability() -> None:
    ic705 = get_radio_profile("IC-705")
    assert ic705.memory == MemoryProfile("group_channel", 0, 99, 0, 99)
    ic9700 = get_radio_profile("IC-9700")
    assert ic9700.memory == MemoryProfile("band_channel", 1, 3, 1, 99)
    for profile in (ic705, ic9700):
        assert "memory_read" in profile.capabilities
    # Radios without a [memory] section stay without the listing.
    for model in ("IC-7300", "IC-7610"):
        profile = get_radio_profile(model)
        assert profile.memory is None
        assert "memory_read" not in profile.capabilities


def _load(tmp_path: Path, memory: str):
    return load_rig(_write_toml(tmp_path, _minimal_toml(memory)))


_GOOD = """
[memory]
addressing = "band_channel"
first_group = 1
last_group = 3
first_channel = 1
last_channel = 99
recall = true
"""


def test_loader_reads_the_memory_section(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD)
    assert config.to_profile().memory == MemoryProfile(
        "band_channel", 1, 3, 1, 99, recall=True
    )
    assert _load(tmp_path, "").memory is None


@pytest.mark.parametrize(
    "edit",
    [
        ('addressing = "band_channel"', 'addressing = "tape"'),
        ("first_group = 1", 'first_group = "1"'),
        ("first_group = 1", "first_group = 5"),  # first > last
        ("last_channel = 99", "last_channel = 0"),  # first > last
        ("last_channel = 99", "last_channel = 10000"),
        ("recall = true", "recall = 1"),
        ("recall = true", "recall = true\nstray = 1"),
        ('addressing = "band_channel"', "addressing = []"),
        ('addressing = "band_channel"', "addressing = {}"),
        ("last_group = 3", "last_group = 100"),  # a band code is one BCD byte
    ],
)
def test_loader_rejects_a_bad_memory_section(
    tmp_path: Path, edit: tuple[str, str]
) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\]"):
        _load(tmp_path, _GOOD.replace(*edit))


def test_largest_accepted_band_code_can_be_encoded(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD.replace("last_group = 3", "last_group = 99"))
    memory = config.to_profile().memory
    assert _encode_memory_address(memory, 99, 99) == bytes.fromhex("990099")
