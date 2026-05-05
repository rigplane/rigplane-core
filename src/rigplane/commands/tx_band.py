"""TX band edge commands (0x1E).

Queries the radio for valid TX frequency ranges:
- 0x1E 0x00: number of TX bands
- 0x1E 0x01 [band_bcd]: start/end frequencies for a specific TX band
"""

from __future__ import annotations

from ..types import bcd_decode
from ._frame import (
    CONTROLLER_ADDR,
    _CMD_TX_BAND_EDGE,
    build_civ_frame,
)


def get_tx_band_count(
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build CI-V frame to query number of TX bands (0x1E 0x00)."""
    return build_civ_frame(to_addr, from_addr, _CMD_TX_BAND_EDGE, sub=0x00)


def get_tx_band_edge(
    band: int,
    to_addr: int,
    from_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Build CI-V frame to query TX band N edge frequencies (0x1E 0x01).

    Args:
        band: Band number (0-99, encoded as BCD byte).
        to_addr: Radio CI-V address.
        from_addr: Controller CI-V address.

    Returns:
        Complete CI-V frame bytes.
    """
    band_bcd = ((band // 10) << 4) | (band % 10)
    return build_civ_frame(
        to_addr, from_addr, _CMD_TX_BAND_EDGE, sub=0x01, data=bytes([band_bcd])
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
