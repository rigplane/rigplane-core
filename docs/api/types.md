# Types & Enums

Core data types used throughout the library.

## StateCache

::: icom_lan.core._state_cache.StateCache

## Enums

### `PacketType`

```python
from icom_lan import PacketType
```

UDP packet type codes from the Icom LAN protocol header (offset 0x04, 2 bytes LE).

| Value | Name | Description |
|-------|------|-------------|
| `0x00` | `DATA` | Data packet (CI-V, audio, etc.) |
| `0x01` | `CONTROL` | Control / retransmit request |
| `0x03` | `ARE_YOU_THERE` | Discovery request |
| `0x04` | `I_AM_HERE` | Discovery response |
| `0x05` | `DISCONNECT` | Disconnect notification |
| `0x06` | `ARE_YOU_READY` | Ready handshake |
| `0x07` | `PING` | Keep-alive ping/pong |

### `Mode`

```python
from icom_lan import Mode
```

Icom CI-V operating modes. Values match the CI-V mode byte.

| Value | Name | Description |
|-------|------|-------------|
| `0x00` | `LSB` | Lower Sideband |
| `0x01` | `USB` | Upper Sideband |
| `0x02` | `AM` | Amplitude Modulation |
| `0x03` | `CW` | Continuous Wave |
| `0x04` | `RTTY` | Radio Teletype |
| `0x05` | `FM` | Frequency Modulation |
| `0x06` | `WFM` | Wide FM |
| `0x07` | `CW_R` | CW Reverse |
| `0x08` | `RTTY_R` | RTTY Reverse |
| `0x17` | `DV` | D-Star Digital Voice |

---

## Dataclasses

### `PacketHeader`

```python
from icom_lan import PacketHeader
```

Fixed 16-byte header present in every Icom LAN UDP packet.

| Field | Type | Description |
|-------|------|-------------|
| `length` | `int` | Total packet length (bytes) |
| `type` | `int` | Packet type code |
| `seq` | `int` | Sequence number |
| `sender_id` | `int` | Sender's connection ID |
| `receiver_id` | `int` | Receiver's connection ID |

### `CivFrame`

```python
from icom_lan import CivFrame
```

Parsed CI-V frame.

| Field | Type | Description |
|-------|------|-------------|
| `to_addr` | `int` | Destination CI-V address |
| `from_addr` | `int` | Source CI-V address |
| `command` | `int` | CI-V command byte |
| `sub` | `int \| None` | Sub-command byte (if applicable) |
| `data` | `bytes` | Payload data |

---

### `AudioCapabilities`

```python
from icom_lan import AudioCapabilities
```

Stable audio capability structure returned by `get_audio_capabilities()` (and by
`IcomRadio.audio_capabilities()` for legacy use).

| Field | Type | Description |
|-------|------|-------------|
| `supported_codecs` | `tuple[AudioCodec, ...]` | Supported codecs in stable order |
| `supported_sample_rates_hz` | `tuple[int, ...]` | Supported sample rates |
| `supported_channels` | `tuple[int, ...]` | Supported channel counts |
| `default_codec` | `AudioCodec` | Deterministic default codec |
| `default_sample_rate_hz` | `int` | Deterministic default sample rate |
| `default_channels` | `int` | Deterministic default channel count |

---

## Helper Functions

### `bcd_encode()`

```python
def bcd_encode(freq_hz: int) -> bytes
```

Encode a frequency (Hz) to Icom's 5-byte BCD format (little-endian BCD).

```python
>>> from icom_lan import bcd_encode
>>> bcd_encode(14074000).hex()
'0040071400'
```

### `bcd_decode()`

```python
def bcd_decode(data: bytes) -> int
```

Decode 5-byte BCD to frequency in Hz.

```python
>>> from icom_lan import bcd_decode
>>> bcd_decode(bytes.fromhex('0040071400'))
14074000
```

### `get_audio_capabilities()`

```python
from icom_lan import get_audio_capabilities
```

Return the static icom-lan audio capability matrix and deterministic defaults.

---

## Constants

### `HEADER_SIZE`

```python
from icom_lan import HEADER_SIZE  # 16 (0x10)
```

Size of the fixed packet header in bytes.
