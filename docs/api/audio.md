# Audio Streaming

Audio RX/TX via the Icom audio UDP port (default 50003).

## Naming Map

Low-level Opus methods are now explicitly suffixed with `_opus`.
High-level PCM APIs are available for both RX and TX.

| Scope | Preferred method names |
|------|-------------------------|
| Low-level Opus (current) | `start_audio_rx_opus`, `stop_audio_rx_opus`, `start_audio_tx_opus`, `push_audio_tx_opus`, `stop_audio_tx_opus`, `start_audio_opus`, `stop_audio_opus` |
| High-level PCM | `start_audio_rx_pcm`, `stop_audio_rx_pcm`, `start_audio_tx_pcm`, `push_audio_tx_pcm`, `stop_audio_tx_pcm` |

Deprecated aliases still work during the deprecation window (two minor releases):
`start_audio_rx`, `stop_audio_rx`, `start_audio_tx`, `push_audio_tx`, `stop_audio_tx`, `start_audio`, `stop_audio`.

## AudioStream

::: rigplane.audio.lan_stream.AudioStream

## Runtime Audio Stats (`get_audio_stats`)

Use `get_audio_stats()` on `AudioStream` or on the **Radio** (from `create_radio`) to retrieve a JSON-friendly
snapshot of live stream quality metrics.

```python
stats = radio.get_audio_stats()
print(stats["packet_loss_percent"], stats["reorder_depth_ema_ms"])
```

### Metrics, Units, Bounds

| Field | Unit | Bounds | Notes |
|------|------|--------|-------|
| `active` | boolean | `true/false` | Whether stream state is not `idle` |
| `state` | string | `idle` / `receiving` / `transmitting` | Current stream state |
| `rx_packets_received` | packets | `>= 0` | Parsed RX audio packets |
| `rx_packets_delivered` | packets | `>= 0` | RX packets delivered to callback |
| `tx_packets_sent` | packets | `>= 0` | TX packets sent |
| `packets_lost` | packets | `>= 0` | Inferred missing RX packets |
| `packet_loss_percent` | percent | `0.0..100.0` | `packets_lost / (delivered + lost)` |
| `reorder_depth_ema_ms` | milliseconds | `>= 0.0` | EMA of reorder depth (not RFC 3550 jitter) |
| `jitter_max_ms` | milliseconds | `>= 0.0` | Peak observed reorder-depth deviation |
| `underrun_count` | events | `>= 0` | Jitter-buffer underrun events |
| `overrun_count` | events | `>= 0` | Jitter-buffer overrun events |
| `estimated_latency_ms` | milliseconds | `>= 0.0` | Estimated buffering delay |
| `jitter_buffer_depth_packets` | packets | `>= 0` | Configured jitter depth (`0` when disabled) |
| `jitter_buffer_pending_packets` | packets | `>= 0` | Currently buffered packets |
| `duplicates_dropped` | packets | `>= 0` | Duplicate RX packets dropped |
| `stale_packets_dropped` | packets | `>= 0` | Stale/old RX packets dropped |
| `out_of_order_packets` | packets | `>= 0` | RX packets observed out of sequence |

## AudioPacket

::: rigplane.audio.lan_stream.AudioPacket

## AudioState

::: rigplane.audio.lan_stream.AudioState

## JitterBuffer

::: rigplane.audio.lan_stream.JitterBuffer

## Packet Functions

::: rigplane.audio.lan_stream.parse_audio_packet

::: rigplane.audio.lan_stream.build_audio_packet

## Internal Transcoder Layer

`rigplane` now includes an internal PCM<->Opus transcoder foundation used for
future high-level PCM APIs.

- Module: `rigplane._audio_transcoder` (internal, no stability guarantee yet)
- Backend: optional `opuslib` (`pip install rigplane[audio]`)
- Typed failures:
  - `AudioCodecBackendError` for missing backend
  - `AudioFormatError` for invalid PCM/Opus frame formats
  - `AudioTranscodeError` for codec encode/decode failures

## AudioBus (pub/sub multi-consumer)

::: rigplane.audio.bus.AudioBus
::: rigplane.audio.bus.AudioSubscription

The AudioBus provides pub/sub distribution for radio RX audio. Multiple consumers (WebSocket broadcaster, audio bridge, recorders) share a single radio RX stream.

### Basic Usage

```python
from rigplane import create_radio, LanBackendConfig

config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    # Subscribe to audio bus
    async with radio.audio_bus.subscribe(name="my-app") as sub:
        async for packet in sub:
            if packet is not None:
                process(packet.data)  # opus bytes
```

### Multiple Consumers

```python
bus = radio.audio_bus

# Web UI gets audio
web = bus.subscribe(name="web-audio")
await web.start()

# Bridge gets audio simultaneously
bridge = bus.subscribe(name="audio-bridge")
await bridge.start()

# Both receive the same packets independently
# First subscriber triggers radio.start_audio_rx_opus()
# Last .stop() schedules radio.stop_audio_rx_opus()
```

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `subscriber_count` | `int` | Number of active subscribers |
| `rx_active` | `bool` | Whether radio RX is currently streaming |

## Module Constants

### `MAX_AUDIO_PAYLOAD`

```
rigplane.audio.MAX_AUDIO_PAYLOAD: int = 1364
```

Maximum audio payload in bytes per TX UDP packet.

The IC-7610 silently drops TX audio UDP packets whose payload exceeds **1364 bytes**. This
limit is undocumented but observed empirically and matches the wfview source:

```cpp
// wfview: audio.data.mid(len, 1364)
```

`push_tx()` automatically chunks oversized payloads:

```
push_tx(pcm_frame)  # 1920-byte 20ms PCM frame @ 48kHz/16-bit
  → chunk 0: bytes [0 : 1364]    → 1364-byte UDP payload  ✓
  → chunk 1: bytes [1364 : 1920] →  556-byte UDP payload  ✓
```

The two chunk sizes — 1364 and 556 bytes — correspond to the fixed audio payload sizes
documented in wfview for the IC-7610. Low-level callers do not need to pre-chunk payloads.
The high-level `push_audio_tx_pcm()` API still requires one complete PCM frame at the
configured sample rate, channel count, and frame duration.

## Usage

### RX Audio (callback-based)

```python
from rigplane import create_radio, LanBackendConfig

config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    received = []

    def on_audio(pkt):
        if pkt is not None:  # None = gap (missing packet)
            received.append(pkt.data)

    await radio.start_audio_rx_opus(on_audio)
    await asyncio.sleep(10)
    await radio.stop_audio_rx_opus()
```

### RX Audio (high-level PCM)

```python
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    def on_pcm(frame: bytes | None) -> None:
        if frame is None:
            return  # gap placeholder from jitter buffer
        # frame is 16-bit little-endian PCM for configured format
        process_pcm(frame)

    await radio.start_audio_rx_pcm(
        on_pcm,
        sample_rate=48000,
        channels=1,
        frame_ms=20,
        jitter_depth=5,
    )
    await asyncio.sleep(10)
    await radio.stop_audio_rx_pcm()
```

### TX Audio (push-based)

```python
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    await radio.start_audio_tx_opus()
    await radio.push_audio_tx_opus(audio_payload)
    await radio.stop_audio_tx_opus()
```

The low-level method names are historical. For direct Icom LAN sessions,
rigplane currently negotiates TX as `PCM_1CH_16BIT`, so the TX payload sent to
the radio is raw PCM16LE. Opus TX payloads are only valid for endpoints that
negotiate an Opus TX codec, such as wfview-compatible server paths.

### TX Audio (high-level PCM)

```python
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    await radio.start_audio_tx_pcm(sample_rate=48000, channels=1, frame_ms=20)
    await radio.push_audio_tx_pcm(pcm_frame)  # one 20ms PCM frame (1920 bytes)
    await radio.stop_audio_tx_pcm()
```

### Full-Duplex

```python
config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
async with create_radio(config) as radio:
    await radio.start_audio_opus(rx_callback=on_audio, tx_enabled=True)
    # ... push TX frames, receive RX via callback ...
    await radio.stop_audio_opus()
```

### Codec Selection

```python
from rigplane import create_radio, LanBackendConfig, AudioCodec

config = LanBackendConfig(
    host="192.168.1.100",
    username="u",
    password="p",
    audio_codec=AudioCodec.PCM_1CH_16BIT,  # default
    audio_sample_rate=48000,
)
async with create_radio(config) as radio:
    ...
```

### Capability Introspection

Use the capability API to inspect negotiated client-side audio options and defaults. The same API is available on the **Radio** returned by `create_radio` and on **IcomRadio** (legacy):

```python
from rigplane import create_radio, get_audio_capabilities, LanBackendConfig

config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
# Static defaults (no connection required):
caps = get_audio_capabilities()
print(caps.supported_codecs)
print(caps.supported_sample_rates_hz)
print(caps.supported_channels)
print(caps.default_codec, caps.default_sample_rate_hz, caps.default_channels)
```

For legacy LAN-only code, `IcomRadio.audio_capabilities()` returns the same structure.

Deterministic default selection rules:

1. Codec: first supported codec in rigplane preference order.
2. Sample rate: highest supported sample rate.
3. Channels: the channel count implied by default codec (fallback: minimum supported channels).

!!! note "Opus codecs"
    `OPUS_1CH` (0x40) and `OPUS_2CH` (0x41) are only supported when
    the radio reports `connection_type == "WFVIEW"`. Standard connections
    use LPCM16 (0x04).

## Migration

Use the explicit `_opus` methods now:

| Deprecated alias | Replacement |
|------------------|-------------|
| `start_audio_rx` | `start_audio_rx_opus` |
| `stop_audio_rx` | `stop_audio_rx_opus` |
| `start_audio_tx` | `start_audio_tx_opus` |
| `push_audio_tx` | `push_audio_tx_opus` |
| `stop_audio_tx` | `stop_audio_tx_opus` |
| `start_audio` | `start_audio_opus` |
| `stop_audio` | `stop_audio_opus` |

For RX PCM, migrate callback-side decoding to the built-in API:

- Before: `start_audio_rx_opus()` + manual Opus decode in callback.
- Now: `start_audio_rx_pcm()` and receive `bytes | None` directly.

For TX PCM, migrate manual Opus encoding to the built-in API:

- Before: manually prepare the low-level negotiated-codec payload and call
  `push_audio_tx_opus()`.
- Now: `start_audio_tx_pcm()` and `push_audio_tx_pcm()` with fixed-size PCM frames.
