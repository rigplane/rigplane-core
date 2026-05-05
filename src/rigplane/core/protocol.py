"""Packet parsing and serialization for the Icom LAN UDP protocol."""

import struct

from .types import HEADER_SIZE, PacketHeader, PacketType

__all__ = [
    "parse_header",
    "serialize_header",
    "identify_packet_type",
]

# Header format: length(u32 LE), type(u16 LE), seq(u16 LE), sentid(u32 LE), rcvdid(u32 LE)
_HEADER_FMT = "<IHHII"
_HEADER_STRUCT = struct.Struct(_HEADER_FMT)


def parse_header(data: bytes) -> PacketHeader:
    """Parse the fixed 16-byte header from raw packet data.

    Args:
        data: Raw packet bytes (at least 16 bytes).

    Returns:
        Parsed PacketHeader.

    Raises:
        ValueError: If data is too short to contain a header.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError(
            f"Packet too short: need at least {HEADER_SIZE} bytes, got {len(data)}"
        )

    length, ptype, seq, sender_id, receiver_id = _HEADER_STRUCT.unpack_from(data)
    return PacketHeader(
        length=length,
        type=ptype,
        seq=seq,
        sender_id=sender_id,
        receiver_id=receiver_id,
    )


def serialize_header(header: PacketHeader) -> bytes:
    """Serialize a PacketHeader to 16 bytes (little-endian).

    Args:
        header: The packet header to serialize.

    Returns:
        16 bytes of wire-format header.
    """
    return _HEADER_STRUCT.pack(
        header.length,
        header.type,
        header.seq,
        header.sender_id,
        header.receiver_id,
    )


def identify_packet_type(data: bytes) -> PacketType | None:
    """Identify the packet type from raw data.

    Args:
        data: Raw packet bytes (at least 16 bytes).

    Returns:
        The PacketType if recognized, None otherwise.
    """
    if len(data) < HEADER_SIZE:
        return None

    ptype = struct.unpack_from("<H", data, 4)[0]
    try:
        return PacketType(ptype)
    except ValueError:
        return None
