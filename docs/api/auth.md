---
robots: noindex, follow
---

# Authentication

Low-level authentication functions for the Icom LAN protocol. These are used internally by the LAN backend (e.g. when using [`create_radio`](public-api-surface.md) with `LanBackendConfig`, or [`IcomRadio`](radio.md) directly).

## Credential Encoding

### `encode_credentials()`

```python
def encode_credentials(text: str) -> bytes
```

Encode a username or password using Icom's position-dependent substitution table. Maximum 16 characters.

!!! warning "Not Encryption"
    This is a simple obfuscation scheme, not cryptographic encryption. The substitution table is publicly known. See [Security](../SECURITY.md).

## Packet Builders

### `build_login_packet()`

```python
def build_login_packet(
    username: str,
    password: str,
    *,
    sender_id: int,
    receiver_id: int,
    tok_request: int = 0,
    auth_seq: int = 0,
    computer_name: str = "rigplane",
) -> bytes
```

Build a 0x80-byte login packet with encoded credentials.

### `build_conninfo_packet()`

```python
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
) -> bytes
```

Build a 0x90-byte connection info / stream request packet. Includes audio codec preferences and the radio's GUID (must be echoed from the radio's conninfo).

## Response Parsers

### `parse_auth_response()`

```python
def parse_auth_response(data: bytes) -> AuthResponse
```

Parse a 0x60-byte login response.

#### `AuthResponse`

| Field | Type | Description |
|-------|------|-------------|
| `success` | `bool` | Whether authentication succeeded |
| `token` | `int` | Session token (4 bytes) |
| `tok_request` | `int` | Token request ID |
| `connection_type` | `str` | Connection type string (e.g., "FTTH") |
| `error` | `int` | Raw error code (0 = success) |

### `parse_status_response()`

```python
def parse_status_response(data: bytes) -> StatusResponse
```

Parse a 0x50-byte status packet.

#### `StatusResponse`

| Field | Type | Description |
|-------|------|-------------|
| `civ_port` | `int` | CI-V data port number |
| `audio_port` | `int` | Audio stream port number |
| `error` | `int` | Error code (0 = OK) |
| `disconnected` | `bool` | Whether radio signaled disconnect |

## Substitution Table

The `PASSCODE_SEQUENCE` table (128 bytes) is derived from wfview's reverse engineering of Icom's credential obfuscation. Each character is mapped based on its position:

```python
index = (ascii_value + position) % 127
if index < 32:
    index += 32  # Stay in printable range
encoded_byte = PASSCODE_SEQUENCE[index]
```

This table is identical across all Icom radios that support LAN control.
