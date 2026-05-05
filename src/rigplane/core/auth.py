"""Authentication logic for the Icom LAN protocol.

Implements credential encoding and login/conninfo packet construction
based on the wfview reference implementation.
"""

import struct
from dataclasses import dataclass

__all__ = [
    "PASSCODE_SEQUENCE",
    "AuthResponse",
    "StatusResponse",
    "encode_credentials",
    "build_login_packet",
    "build_conninfo_packet",
    "parse_auth_response",
    "parse_status_response",
]

# XOR-style substitution table from wfview (icomudpbase.h passcode()).
# Index = (ascii_value + position) mod range; value = encoded byte.
PASSCODE_SEQUENCE: bytes = bytes(
    [
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0x47,
        0x5D,
        0x4C,
        0x42,
        0x66,
        0x20,
        0x23,
        0x46,
        0x4E,
        0x57,
        0x45,
        0x3D,
        0x67,
        0x76,
        0x60,
        0x41,
        0x62,
        0x39,
        0x59,
        0x2D,
        0x68,
        0x7E,
        0x7C,
        0x65,
        0x7D,
        0x49,
        0x29,
        0x72,
        0x73,
        0x78,
        0x21,
        0x6E,
        0x5A,
        0x5E,
        0x4A,
        0x3E,
        0x71,
        0x2C,
        0x2A,
        0x54,
        0x3C,
        0x3A,
        0x63,
        0x4F,
        0x43,
        0x75,
        0x27,
        0x79,
        0x5B,
        0x35,
        0x70,
        0x48,
        0x6B,
        0x56,
        0x6F,
        0x34,
        0x32,
        0x6C,
        0x30,
        0x61,
        0x6D,
        0x7B,
        0x2F,
        0x4B,
        0x64,
        0x38,
        0x2B,
        0x2E,
        0x50,
        0x40,
        0x3F,
        0x55,
        0x33,
        0x37,
        0x25,
        0x77,
        0x24,
        0x26,
        0x74,
        0x6A,
        0x28,
        0x53,
        0x4D,
        0x69,
        0x22,
        0x5C,
        0x44,
        0x31,
        0x36,
        0x58,
        0x3B,
        0x7A,
        0x51,
        0x5F,
        0x52,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
        0,
    ]
)


@dataclass(frozen=True, slots=True)
class AuthResponse:
    """Parsed login response from the radio.

    Attributes:
        success: Whether authentication succeeded.
        token: Session token assigned by the radio.
        tok_request: Token request ID echoed back.
        connection_type: Connection type string (e.g. "FTTH", "WFVIEW").
        error: Raw error code.
    """

    success: bool
    token: int
    tok_request: int
    connection_type: str
    error: int


@dataclass(frozen=True, slots=True)
class StatusResponse:
    """Parsed status packet from the radio.

    Attributes:
        civ_port: CI-V data port.
        audio_port: Audio stream port.
        error: Error code (0 = ok, 0xFFFFFFFF = fatal).
        disconnected: Whether radio signaled disconnect.
    """

    civ_port: int
    audio_port: int
    error: int
    disconnected: bool


def encode_credentials(text: str) -> bytes:
    """Encode a username or password using the Icom substitution table.

    Each character is mapped through a position-dependent lookup:
    index = (ascii_value + position). If > 126, wraps as 32 + (index % 127).
    Maximum 16 characters.

    Args:
        text: Plain-text credential string.

    Returns:
        Encoded bytes (up to 16).
    """
    result = bytearray()
    for i, ch in enumerate(text[:16]):
        p = ord(ch) + i
        if p > 126:
            p = 32 + p % 127
        result.append(PASSCODE_SEQUENCE[p])
    return bytes(result)


def build_login_packet(
    username: str,
    password: str,
    *,
    sender_id: int,
    receiver_id: int,
    tok_request: int = 0,
    auth_seq: int = 0,
    computer_name: str = "icom-lan",
) -> bytes:
    """Build a 0x80-byte login packet.

    Args:
        username: Plain-text username.
        password: Plain-text password.
        sender_id: Our connection ID.
        receiver_id: Radio's connection ID.
        tok_request: Random token request identifier.
        auth_seq: Inner authentication sequence number.
        computer_name: Client computer name (max 16 chars).

    Returns:
        0x80 bytes ready to send.
    """
    pkt = bytearray(0x80)
    struct.pack_into("<I", pkt, 0x00, 0x80)  # len
    struct.pack_into("<H", pkt, 0x04, 0x00)  # type (data)
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into(">I", pkt, 0x10, 0x80 - 0x10)  # payloadsize (big-endian)
    pkt[0x14] = 0x01  # requestreply
    pkt[0x15] = 0x00  # requesttype (login)
    struct.pack_into(">H", pkt, 0x16, auth_seq)  # innerseq (big-endian)
    struct.pack_into("<H", pkt, 0x1A, tok_request)

    enc_user = encode_credentials(username)
    enc_pass = encode_credentials(password)
    comp = computer_name.encode("ascii")[:16]

    pkt[0x40 : 0x40 + len(enc_user)] = enc_user
    pkt[0x50 : 0x50 + len(enc_pass)] = enc_pass
    pkt[0x60 : 0x60 + len(comp)] = comp

    return bytes(pkt)


def build_conninfo_packet(
    *,
    sender_id: int,
    receiver_id: int,
    username: str,
    token: int,
    tok_request: int,
    radio_name: str,
    mac_address: bytes,
    auth_seq: int = 0,
    rx_codec: int = 0x04,
    tx_codec: int = 0x04,
    rx_sample_rate: int = 48000,
    tx_sample_rate: int = 48000,
    civ_local_port: int = 0,
    audio_local_port: int = 0,
    tx_buffer: int = 150,
    guid: bytes | None = None,
) -> bytes:
    """Build a 0x90-byte conninfo / stream request packet.

    Args:
        sender_id: Our connection ID.
        receiver_id: Radio's connection ID.
        username: Plain-text username (will be encoded).
        token: Session token from login.
        tok_request: Token request ID.
        radio_name: Radio device name.
        mac_address: 6-byte MAC address of the radio.
        auth_seq: Inner auth sequence number.
        rx_codec: RX audio codec.
        tx_codec: TX audio codec.
        rx_sample_rate: RX sample rate.
        tx_sample_rate: TX sample rate.
        civ_local_port: Local CI-V port.
        audio_local_port: Local audio port.
        tx_buffer: TX buffer latency in ms.
        guid: Optional 16-byte GUID (used instead of mac_address if set).

    Returns:
        0x90 bytes ready to send.
    """
    pkt = bytearray(0x90)
    struct.pack_into("<I", pkt, 0x00, 0x90)
    struct.pack_into("<H", pkt, 0x04, 0x00)  # type
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into(">I", pkt, 0x10, 0x90 - 0x10)  # payloadsize (big-endian)
    pkt[0x14] = 0x01  # requestreply
    pkt[0x15] = 0x03  # requesttype (conninfo)
    struct.pack_into(">H", pkt, 0x16, auth_seq)
    struct.pack_into("<H", pkt, 0x1A, tok_request)
    struct.pack_into("<I", pkt, 0x1C, token)

    if guid is not None:
        pkt[0x20 : 0x20 + len(guid[:16])] = guid[:16]
    else:
        struct.pack_into("<H", pkt, 0x27, 0x8010)  # commoncap
        pkt[0x2A : 0x2A + 6] = mac_address[:6]

    name_bytes = radio_name.encode("ascii")[:32]
    pkt[0x40 : 0x40 + len(name_bytes)] = name_bytes

    enc_user = encode_credentials(username)
    pkt[0x60 : 0x60 + len(enc_user)] = enc_user

    pkt[0x70] = 0x01  # rxenable
    pkt[0x71] = 0x01  # txenable
    pkt[0x72] = rx_codec
    pkt[0x73] = tx_codec
    struct.pack_into(">I", pkt, 0x74, rx_sample_rate)
    struct.pack_into(">I", pkt, 0x78, tx_sample_rate)
    struct.pack_into(">I", pkt, 0x7C, civ_local_port)
    struct.pack_into(">I", pkt, 0x80, audio_local_port)
    struct.pack_into(">I", pkt, 0x84, tx_buffer)
    pkt[0x88] = 0x01  # convert

    return bytes(pkt)


def parse_auth_response(data: bytes) -> AuthResponse:
    """Parse a login response packet (0x60 bytes).

    Args:
        data: Raw packet data (at least 0x60 bytes).

    Returns:
        Parsed AuthResponse.

    Raises:
        ValueError: If data is too short.
    """
    if len(data) < 0x60:
        raise ValueError(f"Login response too short: {len(data)} < 0x60")

    error = struct.unpack_from("<I", data, 0x30)[0]
    token = struct.unpack_from("<I", data, 0x1C)[0]
    tok_request = struct.unpack_from("<H", data, 0x1A)[0]

    # Connection type string at 0x40, 16 bytes null-terminated
    conn_raw = data[0x40:0x50]
    connection_type = conn_raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")

    success = error != 0xFEFFFFFF and error != 0xFFFFFFFF

    return AuthResponse(
        success=success,
        token=token,
        tok_request=tok_request,
        connection_type=connection_type,
        error=error,
    )


def parse_status_response(data: bytes) -> StatusResponse:
    """Parse a status packet (0x50 bytes).

    Args:
        data: Raw packet data (at least 0x50 bytes).

    Returns:
        Parsed StatusResponse.

    Raises:
        ValueError: If data is too short.
    """
    if len(data) < 0x50:
        raise ValueError(f"Status packet too short: {len(data)} < 0x50")

    error = struct.unpack_from("<I", data, 0x30)[0]
    disc = data[0x40]
    civ_port = struct.unpack_from(">H", data, 0x42)[0]
    audio_port = struct.unpack_from(">H", data, 0x46)[0]

    return StatusResponse(
        civ_port=civ_port,
        audio_port=audio_port,
        error=error,
        disconnected=disc == 0x01,
    )
