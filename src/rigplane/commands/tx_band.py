"""TX band edge commands (0x1E).

Queries the radio for valid TX frequency ranges:
- 0x1E 0x00: number of TX bands
- 0x1E 0x01 [band_bcd]: start/end frequencies for a specific TX band

Both builders migrated onto the bound command map in MOR-2008 (batch 4,
`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N,
"Group B"): each now requires ``cmd_map`` -- no hardcoded fallback ever
existed to delete here, since neither took a ``cmd_map`` parameter at all
before this batch. Neither builder has a production call site
(`runtime/radio.py`, `runtime/_dual_rx_runtime.py`): only the response
parsers below are consumed, by `runtime/_civ_rx.py`, decoding unsolicited
0x1E frames rather than one this module itself requested.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..types import bcd_decode
from ._frame import (
    CONTROLLER_ADDR,
    _build_from_map,
    expose_command_key,
    require_cmd_map,
)

if TYPE_CHECKING:
    from ..command_map import CommandMap


@expose_command_key(lambda cmd_map: "get_tx_band_count")
@require_cmd_map
def get_tx_band_count(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to query number of TX bands (0x1E 0x00)."""
    return _build_from_map(
        cmd_map, "get_tx_band_count", to_addr=to_addr, from_addr=from_addr
    )


@expose_command_key(lambda cmd_map: "get_tx_band_edge")
@require_cmd_map
def get_tx_band_edge(
    band: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
    *,
    cmd_map: CommandMap,
) -> bytes:
    """Build CI-V frame to query TX band N edge frequencies (0x1E 0x01).

    Args:
        band: Band number (0-99, encoded as BCD byte).
        to_addr: Radio CI-V address.
        from_addr: Controller CI-V address.
        cmd_map: The radio's bound command map.

    Returns:
        Complete CI-V frame bytes.
    """
    band_bcd = ((band // 10) << 4) | (band % 10)
    return _build_from_map(
        cmd_map,
        "get_tx_band_edge",
        to_addr=to_addr,
        from_addr=from_addr,
        data=bytes([band_bcd]),
    )


def parse_tx_band_count_response(data: bytes) -> int:
    """Parse 0x1E 0x00 response data into band count (BCD decoded).

    Args:
        data: Response payload (single BCD byte, or empty).

    Returns:
        Number of TX bands.
    """
    if not data:
        return 0
    return ((data[0] >> 4) & 0x0F) * 10 + (data[0] & 0x0F)


def parse_tx_band_edge_response(data: bytes) -> tuple[int, int]:
    """Parse 0x1E 0x01 response data into (start_hz, end_hz).

    Data format: 5-byte BCD start frequency + 5-byte BCD end frequency.

    Args:
        data: 10 bytes of BCD-encoded frequency pair.

    Returns:
        Tuple of (start_hz, end_hz).

    Raises:
        ValueError: If data is shorter than 10 bytes.
    """
    if len(data) < 10:
        raise ValueError(f"TX band edge response too short: {len(data)} bytes")
    start_hz = bcd_decode(data[0:5])
    end_hz = bcd_decode(data[5:10])
    return start_hz, end_hz
