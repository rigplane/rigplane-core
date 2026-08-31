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

from typing import TYPE_CHECKING

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
) -> bytes:
    """Build CI-V frame to set memory mode (0x08)."""
    if not 1 <= channel <= 101:
        raise ValueError(f"Channel must be 1-101, got {channel}")
    data = bcd_encode_value(channel, byte_count=2)
    return _build_from_map(
        cmd_map, "set_memory_mode", to_addr=to_addr, from_addr=from_addr, data=data
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
