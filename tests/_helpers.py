"""Shared test helpers for building fake radio UDP/CI-V responses.

Consolidates helpers that were duplicated across multiple test files.
"""

from __future__ import annotations

import struct

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_ACK,
    _CMD_FREQ_GET,
    _CMD_MODE_GET,
    build_civ_frame,
)
from rigplane.types import CivFrame, Mode, PacketType, bcd_encode

CIV_HEADER_SIZE = 0x15


def wrap_civ_in_udp(
    civ_data: bytes,
    *,
    sender_id: int = 0xDEADBEEF,
    receiver_id: int = 0x00010001,
    seq: int = 1,
) -> bytes:
    """Wrap a CI-V frame in a minimal UDP data packet."""
    total_len = CIV_HEADER_SIZE + len(civ_data)
    pkt = bytearray(total_len)
    struct.pack_into("<I", pkt, 0, total_len)
    struct.pack_into("<H", pkt, 4, PacketType.DATA)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    pkt[0x10] = 0x00
    struct.pack_into("<H", pkt, 0x11, len(civ_data))
    struct.pack_into("<H", pkt, 0x13, 0)
    pkt[CIV_HEADER_SIZE:] = civ_data
    return bytes(pkt)


def freq_response(freq_hz: int) -> bytes:
    """Build a CI-V frequency response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET, data=bcd_encode(freq_hz)
    )
    return wrap_civ_in_udp(civ)


def mode_response(mode: Mode, filt: int = 1) -> bytes:
    """Build a CI-V mode response wrapped in UDP."""
    civ = build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, _CMD_MODE_GET, data=bytes([mode, filt])
    )
    return wrap_civ_in_udp(civ)


def ack_response() -> bytes:
    """Build a CI-V ACK wrapped in UDP."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_ACK)
    return wrap_civ_in_udp(civ)


def report_receiver_selection(radio, receiver: int) -> None:
    """The radio reports its MAIN/SUB selection (``07 D2 00``/``01``).

    Fed through the RX path like a real answer, so ``RadioState.active``
    is a reading the routed MAIN/SUB operations may trust (MOR-3623),
    not the dataclass default.
    """
    radio._civ_runtime._update_state_cache_from_frame(
        CivFrame(
            to_addr=CONTROLLER_ADDR,
            from_addr=radio.profile.civ_addr,
            command=0x07,
            sub=None,
            data=bytes([0xD2, receiver]),
        )
    )
