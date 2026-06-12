---
robots: noindex, follow
---

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

## Capture Health And Bridge Metrics

PortAudio-backed capture and the PCM TX bridge expose a second set of metrics
that answer a different question from `get_audio_stats()`: whether the local
OS audio callback and the bridge TX path are keeping up.

### Capture callback health (`RxStreamHealth`)

`RxStreamHealth` snapshots input-side callback delivery for RX-only and
full-duplex capture streams.

| Field | Unit | Meaning | Typical next step |
|------|------|---------|-------------------|
| `frames_delivered` | frames | PCM frames successfully handed to the bridge/callback | Confirms the callback is running at all |
| `input_overflow_events` | events | PortAudio reported captured input was dropped because the process/backend could not keep up | Check host CPU pressure, device/driver stability, and callback cadence before blaming RigPlane TX |
| `input_underflow_events` | events | PortAudio reported the input callback arrived without enough fresh captured samples | Check capture device/driver health and OS scheduling; this is still capture-side, not radio TX failure |
| `callback_errors` | events | Callback-level errors while processing capture frames | Inspect logs for the paired exception or status context |
| `callback_status_flags` | map | Per-flag totals such as `input_overflow` / `input_underflow` | Use the exact flag mix to separate capture starvation from other failures |

### Bridge TX path health (`BridgeMetrics`)

These counters are surfaced on `AudioBridge.metrics`, `AudioBridge.stats`, and
runtime bridge-status consumers such as the Web server's `audio_bridge_stats`.

| Field | Unit | Meaning | Not the same as |
|------|------|---------|-----------------|
| `capture_input_overflows` | events | Cumulative `RxStreamHealth.input_overflow_events` observed by the active bridge capture stream | `tx_overruns` queue drops |
| `capture_input_underflows` | events | Cumulative `RxStreamHealth.input_underflow_events` observed by the active bridge capture stream | TX playback underruns or radio write failures |
| `capture_callback_status_flags` | map | Bridge-side rollup of capture callback flags, for example `{"input_overflow": 3}` | Silence gating decisions |
| `tx_silence_suppressed` | frames | Frames intentionally skipped because captured PCM stayed below the bridge silence/noise-gate threshold | Capture overrun or lost OS buffers |
| `tx_overruns` | events | Bridge TX queue drops: RigPlane evicted stale queued frames to preserve bounded latency before `push_audio_tx_pcm()` | PortAudio callback overflow |

### TX playback write health (`TxStreamHealth`)

These counters live on the writable PortAudio TX stream itself and describe
playback-side queue pressure after audio has already left the bridge queue.

| Field | Unit | Meaning | Not the same as |
|------|------|---------|-----------------|
| `overrun_events` | events | TX playback queue overflow events on the writable stream | `BridgeMetrics.tx_overruns` bridge queue drops |
| `overrun_audio_ms` | milliseconds | Total playback-queue audio duration dropped during TX stream overflow handling | Bridge capture callback overflow |
| `frames_dropped` | frames | TX playback frames dropped by the writable stream while preserving bounded latency | Silence gating or radio write failure |

### Interpretation Rules

- `capture_input_overflows > 0` means the OS capture callback already lost input
  before RigPlane could bridge it. Start with local capture/device pressure.
- `tx_overruns > 0` with zero capture overflow means capture kept running, but
  the bridge TX queue backed up and RigPlane dropped stale frames on purpose.
- `TxStreamHealth.overrun_events > 0`, `overrun_audio_ms > 0`, or
  `frames_dropped > 0` mean the writable TX playback queue overflowed later in
  the path; that is distinct from bridge queue pressure and uses different
  counters.
- `tx_silence_suppressed > 0` means quiet frames were filtered by policy. This
  is expected during RX silence and is not evidence of callback starvation.
- Downstream TX push/write failures live elsewhere: `TxStreamHealth.write_failures`,
  `last_error`, or radio/backend error logs indicate that RigPlane tried to
  send audio onward and that stage failed after capture succeeded.

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
# Last awaited close triggers radio.stop_audio_rx_opus()
await web.aclose()
await bridge.aclose()
```

Subscriptions support `async with`, `await sub.aclose()`, and the older
`sub.stop()` convenience path. Prefer `async with` or `await aclose()` when
coordinating restart/teardown: the awaited close path removes the subscriber
before the caller proceeds, so bridge or WebSocket restarts do not race a stale
subscriber.

### Properties

| Property | Type | Description |
|----------|------|-------------|
| `subscriber_count` | `int` | Number of active subscribers |
| `rx_active` | `bool` | Whether radio RX is currently streaming |

## Queue And Frame Semantics

Audio queues are bounded to preserve real-time behavior. When the bridge TX
queue overflows, RigPlane drops the oldest queued audio and keeps the newest
live frame. Diagnostics count that bridge-side event as `tx_overruns`.

When the writable TX playback queue overflows later in the path, `TxStreamHealth`
tracks it separately via `overrun_events`, `overrun_audio_ms`, and
`frames_dropped`. Those playback counters are not reported as `tx_overruns`.

Do not confuse bridge queue drops with PortAudio capture callback overflow:
`tx_overruns` means RigPlane chose to evict stale already-captured audio,
whereas `capture_input_overflows` / `input_overflow_events` mean the OS/backend
capture callback reported lost input before the bridge queue decision.

PortAudio capture uses engine-native callback periods (`blocksize=0`) and then
losslessly re-chunks the continuous callback stream into fixed `frame_ms`
frames before handing it to PCM TX validators. At 48 kHz, 16-bit mono,
`frame_ms=20` means each emitted TX frame is 1920 bytes. Consumers should still
treat WebSocket/DataChannel `frame_ms` as an advisory label and derive actual
duration from payload size and metadata.

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
