"""Shared test helpers for building fake radio UDP/CI-V responses.

Consolidates helpers that were duplicated across multiple test files.
"""

from __future__ import annotations

import struct
from typing import Any

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_ACK,
    _CMD_FREQ_GET,
    _CMD_MODE_GET,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.types import Mode, PacketType, bcd_encode

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


def observe_selected_receiver(radio: Any, receiver: int = 0) -> None:
    """Deliver the radio's 0x07 0xD2 answer (which receiver is selected).

    On a dual-receiver profile a receiver-scoped command without a cmd29
    route is admitted only for the selected receiver, and the selection is
    unknown until the radio reports it (MOR-3591). The connect flow's state
    queries ask for it; tests that bypass connect deliver the answer here,
    through the real CI-V RX path.
    """
    radio._update_state_cache_from_frame(
        parse_civ_frame(
            build_civ_frame(
                CONTROLLER_ADDR,
                radio._radio_addr,
                0x07,
                data=bytes([0xD2, receiver]),
            )
        )
    )
