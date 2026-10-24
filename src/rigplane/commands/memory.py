"""Memory mode/write/clear/contents and band stacking register commands.

All nine builders in this module migrated onto the bound command map in
MOR-2008 (batch 4, `docs/plans/2026-08-29-profile-driven-command-bytes.md`
§4 Steps 5..N, "Group B"): each now requires ``cmd_map`` -- no hardcoded
fallback ever existed to delete here, unlike batches 1-3's modules, since
none of these nine ever took a ``cmd_map`` parameter at all before this
batch (`tests/command_map_parity_uncovered.txt`'s own
``hardcode_only_builders`` census, pre-migration: 16, this module's share
of it: 9).

Canonical command-map key naming (owner ruling, MOR-2008 batch 4): a
genuine get/set *value* pair keeps its ``get_``/``set_`` prefix
(``get_memory_mode``/``set_memory_mode``, ``get_memory_contents``/
``set_memory_contents``, ``get_bsr``/``set_bsr``); a single-wire-form
*action* command uses the bare name, the same convention already
established for ``power_on``/``power_off``/``ptt_on``/``ptt_off``/
``scan_start`` -- so ``build_memory_write``/``build_memory_to_vfo``/
``build_memory_clear`` resolve ``"memory_write"``/``"memory_to_vfo"``/
``"memory_clear"``, not a ``get_``/``set_`` spelling. The Python function
names keep their anomalous ``build_X_get``/``build_X_set``/``build_X``
shape (only ``get_bsr``/``set_bsr`` already followed the package-wide
``get_X``/``set_X`` convention) -- renaming a public function is out of
this migration's scope; only the TOML key spellings were reconciled.

``get_memory_mode`` is left deliberately undeclared (D1 state 3) on every
CI-V profile: the four Icom manuals phrase 0x08 as "Select the Memory
mode" (imperative), not "Send/read" like the package's other bidirectional
rows, and no live-bench probe has settled whether it answers a bare read
-- see each profile's own "PROVENANCE OPEN" note. Calling it now raises
`core.exceptions.CommandError` (undeclared-command refusal) on every
shipped Icom profile, the honest outcome until that probe happens.

``build_memory_to_vfo``/``build_memory_clear`` still append the 2-byte BCD
channel payload all four Icom manuals show 0x0A/0x0B accepting no data
column at all -- MOR-2055, not touched by this migration, which only
re-plumbs the command-map lookup and leaves the wire bytes exactly as they
were.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..core.types import (
    MemoryEntry,
    MemoryProfile,
    Mode,
    RepeaterShiftDirection,
    ToneSquelchType,
)
from ..types import bcd_decode, bcd_encode
from ._codec import _bcd_decode_value, bcd_encode_value
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_CTL_MEM,
    _CMD_MEMORY_MODE,
    _SUB_BAND_STACK,
    _SUB_MEMORY_CONTENTS,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap
    from ..types import BandStackRegister, CivFrame, MemoryChannel


@expose_command_key(lambda cmd_map: "get_memory_mode")
@require_cmd_map
def build_memory_mode_get(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to get current memory mode (0x08)."""
    return _build_from_map(
        cmd_map, "get_memory_mode", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "set_memory_mode")
@require_cmd_map
def build_memory_mode_set(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
    memory: MemoryProfile | None = None,
) -> bytes:
    """Build CI-V frame to set memory mode (0x08).

    With a profile's ``[memory]`` section the channel is bounded by it;
    otherwise by the IC-7610 range 1-101.
    """
    first, last = (
        (1, 101) if memory is None else (memory.first_channel, memory.last_channel)
    )
    if not first <= channel <= last:
        raise ValueError(f"Channel must be {first}-{last}, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "set_memory_mode", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "set_memory_group")
@require_cmd_map
def build_memory_group_set(
    memory: MemoryProfile,
    group: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to select a memory group (``08 A0 gggg``, IC-705)."""
    if not memory.first_group <= group <= memory.last_group:
        raise ValueError(
            f"memory group must be {memory.first_group}-{memory.last_group}, got {group}"
        )
    return _build_from_map(
        cmd_map,
        "set_memory_group",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bcd_encode_value(group, byte_count=2),
    )


def parse_memory_mode_response(frame: CivFrame) -> int:
    """Parse memory mode response (0x08)."""
    if frame.command != _CMD_MEMORY_MODE:
        raise ValueError(f"Not a memory mode response: 0x{frame.command:02x}")
    if len(frame.data) < 2:
        raise ValueError(f"Expected 2 bytes for memory mode, got {len(frame.data)}")
    return _bcd_decode_value(frame.data[:2])


@expose_command_key(lambda cmd_map: "memory_write")
@require_cmd_map
def build_memory_write(
    to_addr: int, from_addr: int = CONTROLLER_ADDR, *, cmd_map: CommandMap
) -> bytes:
    """Build CI-V frame to write VFO to memory (0x09)."""
    return _build_from_map(
        cmd_map, "memory_write", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "memory_to_vfo")
@require_cmd_map
def build_memory_to_vfo(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to load memory to VFO (0x0A).

    Note:
        The 2-byte BCD channel payload appended here is contradicted by
        all four Icom manuals, which document 0x0A as a bare command with
        no data field -- see MOR-2055 for the payload question. This
        migration re-plumbs the command-map lookup only and does not
        change these wire bytes.
    """
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "memory_to_vfo", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "memory_clear")
@require_cmd_map
def build_memory_clear(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to clear memory channel (0x0B).

    Note:
        The 2-byte BCD channel payload appended here is contradicted by
        all four Icom manuals, which document 0x0B as a bare command with
        no data field -- see MOR-2055 for the payload question. This
        migration re-plumbs the command-map lookup only and does not
        change these wire bytes.
    """
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "memory_clear", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "get_memory_contents")
@require_cmd_map
def build_memory_contents_get(
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to get memory contents (0x1A 0x00)."""
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    channel_bcd = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map,
        "get_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=channel_bcd,
    )


@expose_command_key(lambda cmd_map: "set_memory_contents")
@require_cmd_map
def build_memory_contents_set(
    mem: MemoryChannel,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to set memory contents (0x1A 0x00)."""
    from ..types import MemoryChannel as MC

    if not isinstance(mem, MC):
        raise TypeError(f"Expected MemoryChannel, got {type(mem)}")
    if not 1 <= mem.channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {mem.channel}")

    payload = bytearray(26)
    payload[0] = mem.scan
    payload[1:6] = bcd_encode(mem.frequency_hz)
    payload[6] = bcd_encode_value(mem.mode, byte_count=1)[0]
    payload[7] = bcd_encode_value(mem.filter, byte_count=1)[0]
    payload[8] = (mem.datamode << 4) | (mem.tonemode & 0x0F)
    if mem.tone_freq_hz:
        payload[9:12] = bcd_encode_value(mem.tone_freq_hz, byte_count=3)
    if mem.tsql_freq_hz:
        payload[12:15] = bcd_encode_value(mem.tsql_freq_hz, byte_count=3)
    name_bytes = mem.name.encode("ascii", errors="replace")[:10]
    payload[15 : 15 + len(name_bytes)] = name_bytes

    channel_bcd = bcd_encode_value(mem.channel, byte_count=2)
    return _build_from_map(
        cmd_map,
        "set_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=channel_bcd + bytes(payload),
    )


def parse_memory_contents_response(frame: CivFrame) -> MemoryChannel:
    """Parse memory contents response (0x1A 0x00)."""
    from ..types import MemoryChannel

    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_MEMORY_CONTENTS:
        raise ValueError(
            f"Not a memory contents response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 28:
        raise ValueError(f"Memory contents too short: {len(frame.data)} bytes")

    data = frame.data
    return MemoryChannel(
        channel=_bcd_decode_value(data[0:2]),
        scan=data[2],
        frequency_hz=bcd_decode(data[3:8]),
        mode=_bcd_decode_value(data[8:9]),
        filter=_bcd_decode_value(data[9:10]),
        datamode=(data[10] >> 4) & 0x0F,
        tonemode=data[10] & 0x0F,
        tone_freq_hz=_bcd_decode_value(data[11:14])
        if data[11:14] != b"\x00\x00\x00"
        else None,
        tsql_freq_hz=_bcd_decode_value(data[14:17])
        if data[14:17] != b"\x00\x00\x00"
        else None,
        name=data[17:27].rstrip(b"\x00").decode("ascii", errors="replace"),
    )


# --- Band Stacking Register ---


@expose_command_key(lambda cmd_map: "get_bsr")
@require_cmd_map
def get_bsr(
    band: int,
    register: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to get band stacking register (0x1A 0x01)."""
    if not 0 <= band <= 24:
        raise ValueError(f"Band must be 0-24, got {band}")
    if not 1 <= register <= 3:
        raise ValueError(f"Register must be 1-3, got {register}")
    data = bytes([band, register])
    return _build_from_map(
        cmd_map, "get_bsr", to_addr=to_addr, from_addr=from_addr, data=data
    )


@expose_command_key(lambda cmd_map: "set_bsr")
@require_cmd_map
def set_bsr(
    bsr: BandStackRegister,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to set band stacking register (0x1A 0x01)."""
    from ..types import BandStackRegister as BSR

    if not isinstance(bsr, BSR):
        raise TypeError(f"Expected BandStackRegister, got {type(bsr)}")
    if not 0 <= bsr.band <= 24:
        raise ValueError(f"Band must be 0-24, got {bsr.band}")
    if not 1 <= bsr.register <= 3:
        raise ValueError(f"Register must be 1-3, got {bsr.register}")

    payload = bytes([bsr.band, bsr.register])
    payload += bcd_encode(bsr.frequency_hz)
    payload += bcd_encode_value(bsr.mode, byte_count=1)
    payload += bcd_encode_value(bsr.filter, byte_count=1)
    return _build_from_map(
        cmd_map, "set_bsr", to_addr=to_addr, from_addr=from_addr, data=payload
    )


def parse_band_stack_response(frame: CivFrame) -> BandStackRegister:
    """Parse band stacking register response (0x1A 0x01)."""
    from ..types import BandStackRegister

    if frame.command != _CMD_CTL_MEM or frame.sub != _SUB_BAND_STACK:
        raise ValueError(
            f"Not a band stack response: 0x{frame.command:02x} sub=0x{frame.sub!r}"
        )
    if len(frame.data) < 9:
        raise ValueError(f"Band stack data too short: {len(frame.data)} bytes")

    data = frame.data
    return BandStackRegister(
        band=data[0],
        register=data[1],
        frequency_hz=bcd_decode(data[2:7]),
        mode=_bcd_decode_value(data[7:8]),
        filter=_bcd_decode_value(data[8:9]),
    )


# Backward-compat aliases
build_band_stack_get = get_bsr
build_band_stack_set = set_bsr


# --- Read-only memory listing (MOR-3627) ----------------------------------
#
# CI-V ``1A 00`` (IC-705 guide p.18, IC-9700 p.14; real IC-705: MOR-3650). A
# reply is the address (group 2 + channel 2 bytes, or band 1 + channel 2), a
# select byte, the 47-byte channel block twice (the second is the split TX
# copy), then a 16-byte name. A block is: frequency 5, mode 1, filter 1, data
# mode 1, duplex|tone 1, digital squelch 1, TX tone 3, TSQL tone 3, DTCS 3, DV
# code 1, duplex offset 3, UR/R1/R2 24. An empty slot answers address + ``FF``.

_MEMORY_BLOCK_LEN = 47
_MODE_DD = 0x22  # IC-9700 digital data mode (1.2 GHz)
_MEMORY_NAME_LEN = 16
_ADDRESS_LEN = {"group_channel": 4, "band_channel": 3}
_DUPLEX_BY_NIBBLE = {
    0: RepeaterShiftDirection.SIMPLEX,
    1: RepeaterShiftDirection.MINUS,
    2: RepeaterShiftDirection.PLUS,
}
_TONE_MODE_BY_NIBBLE = {
    0: ToneSquelchType.OFF,
    1: ToneSquelchType.TONE,
    2: ToneSquelchType.TSQL,
    3: ToneSquelchType.DTCS,
}


def _bcd_int(raw: bytes) -> int:
    """Strict packed-BCD to int; a non-decimal nibble is a corrupt reply."""
    digits = raw.hex()
    if not digits.isdigit():
        raise ValueError(f"not packed BCD: {digits}")
    return int(digits)


def _encode_memory_address(memory: MemoryProfile, group: int, channel: int) -> bytes:
    """The ``1A 00`` address bytes for one slot, bounds-checked by the profile."""
    if not memory.first_group <= group <= memory.last_group:
        raise ValueError(
            f"memory group must be {memory.first_group}-{memory.last_group}, got {group}"
        )
    if not memory.first_channel <= channel <= memory.last_channel:
        raise ValueError(
            f"memory channel must be {memory.first_channel}-{memory.last_channel}, "
            f"got {channel}"
        )
    if memory.addressing not in _ADDRESS_LEN:
        raise ValueError(f"{memory.addressing} is not a CI-V memory layout")
    group_bytes = 2 if memory.addressing == "group_channel" else 1
    return bcd_encode_value(group, byte_count=group_bytes) + bcd_encode_value(
        channel, byte_count=2
    )


@expose_command_key(lambda cmd_map: "get_memory_contents")
@require_cmd_map
def build_memory_entry_read(
    memory: MemoryProfile,
    group: int,
    channel: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build the read-only CI-V frame for one stored memory slot (``1A 00``)."""
    return _build_from_map(
        cmd_map,
        "get_memory_contents",
        to_addr=to_addr,
        from_addr=from_addr,
        data=_encode_memory_address(memory, group, channel),
    )


def _parse_block(block: bytes, memory: MemoryProfile, group: int) -> dict[str, Any]:
    """Validate one 47-byte channel block and return its decoded fields.

    Fields the read model does not expose (filter, DTCS polarity, DV code,
    call signs) are not validated: a listing must not drop a real channel over
    a value it never shows.
    """
    mode_code = block[5]  # the guide's mode codes are raw bytes (DV = 0x17)
    if block[7] not in (0, 1):
        raise ValueError(f"data mode byte must be 00 or 01, got {block[7]:02X}")
    if block[9] not in (0x00, 0x10, 0x20):  # digital squelch: off, DSQL, CSQL
        raise ValueError(f"digital squelch byte must be X0, got {block[9]:02X}")
    duplex_nibble, tone_nibble = block[8] >> 4, block[8] & 0x0F
    tone_mode = _TONE_MODE_BY_NIBBLE.get(tone_nibble)
    if tone_mode is None:
        raise ValueError(f"unknown tone mode nibble {tone_nibble}")
    dd = mode_code == _MODE_DD and group in memory.dd_groups
    if mode_code == _MODE_DD and not dd:
        raise ValueError(f"unknown mode code {mode_code:02X} for memory group {group}")
    # RPS (3) exists only in DD; DD has no DUP-/DUP+.
    if duplex_nibble > (2 if memory.addressing == "group_channel" else 3) or (
        (duplex_nibble == 3) != dd and duplex_nibble != 0
    ):
        raise ValueError(
            f"duplex nibble {duplex_nibble} does not fit mode {mode_code:02X}"
        )
    if memory.addressing == "group_channel" and block[4] >> 4:
        raise ValueError("IC-705 frequency GHz digit is fixed 0")
    if block[22] >> 4 and group not in memory.offset_10mhz_groups:
        raise ValueError(f"offset 10 MHz digit is not valid for memory group {group}")
    # 100 MHz digit 0-4 (both radios); GHz digit 0-1 on the IC-9700 (fixed 0 above)
    if block[4] & 0x0F > 4 or block[4] >> 4 > 1:
        raise ValueError("frequency 100 MHz or GHz digit out of range")
    # tone and TSQL frequencies: a fixed 00 byte, then a 100 Hz digit of 0-2
    if block[10] or block[13] or block[11] >> 4 > 2 or block[14] >> 4 > 2:
        raise ValueError("tone frequency digits out of range")
    dtcs_digits = f"{block[17]:02x}{block[18]:02x}"
    if not dtcs_digits.isdigit() or dtcs_digits[0] != "0" or max(dtcs_digits) > "7":
        raise ValueError(f"DTCS code is not three octal digits: {dtcs_digits}")
    try:
        mode = Mode(mode_code).name.replace("_R", "-R")
    except ValueError:
        mode = "DD" if dd else f"0x{mode_code:02X}"
    return {
        "frequency_hz": _bcd_int(block[4::-1]),
        "mode": mode,
        "data_mode": block[7] == 1,
        "duplex": _DUPLEX_BY_NIBBLE.get(duplex_nibble),
        # [10 MHz|1 MHz][100 kHz|10 kHz][1 kHz|100 Hz], most significant first
        "offset_hz": _bcd_int(block[22:19:-1]) * 100,
        "tone_mode": tone_mode,
        "tone_centihz": _bcd_int(block[10:13]) * 10,
        "tsql_centihz": _bcd_int(block[13:16]) * 10,
        "dtcs_code": int(dtcs_digits),
    }


def parse_memory_entry(data: bytes, memory: MemoryProfile) -> MemoryEntry:
    """Decode a ``1A 00`` reply body (the bytes after ``1A 00``) into a slot."""
    if memory.addressing not in _ADDRESS_LEN:
        raise ValueError(f"{memory.addressing} is not a CI-V memory layout")
    address_len = _ADDRESS_LEN[memory.addressing]
    group_len = address_len - 2
    if len(data) < address_len + 1:
        raise ValueError(f"memory reply too short: {len(data)} bytes")
    group = _bcd_int(data[:group_len])
    channel = _bcd_int(data[group_len:address_len])
    if not (
        memory.first_group <= group <= memory.last_group
        and memory.first_channel <= channel <= memory.last_channel
    ):
        raise ValueError(
            f"memory reply address {group}/{channel} is outside the profile"
        )
    if len(data) == address_len + 1 and data[address_len] == 0xFF:
        return MemoryEntry(group=group, channel=channel, empty=True)
    select = data[address_len]  # split (high nibble 0/1) | select memory (0-3)
    max_split = 1 if memory.addressing == "group_channel" else 0  # IC-9700: fixed 0
    if select >> 4 > max_split or select & 0x0F > 3:
        raise ValueError(f"unknown select/split byte {select:02X}")
    start = address_len + 1
    expected = start + 2 * _MEMORY_BLOCK_LEN + _MEMORY_NAME_LEN
    if len(data) != expected:
        raise ValueError(f"memory reply is {len(data)} bytes, expected {expected}")
    fields = _parse_block(data[start : start + _MEMORY_BLOCK_LEN], memory, group)
    tx_block = data[start + _MEMORY_BLOCK_LEN : expected - _MEMORY_NAME_LEN]
    _parse_block(tx_block, memory, group)
    name_raw = data[expected - _MEMORY_NAME_LEN : expected]
    if not all(0x20 <= b <= 0x7E or b == 0 for b in name_raw):
        raise ValueError("memory name is not printable ASCII")
    name = name_raw.decode("ascii").replace("\x00", " ").rstrip()
    return MemoryEntry(group=group, channel=channel, name=name, **fields)
