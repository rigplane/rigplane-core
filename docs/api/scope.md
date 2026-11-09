---
robots: noindex, follow
---

# Scope / Waterfall

Spectrum and waterfall data from the radio's scope display.

## WebSocket subscriptions

`/api/v1/scope` multiplexes radio, audio FFT and IQ FFT subscriptions on one
physical WebSocket. Each subscription is keyed by `sourceId` and `receiver`;
a subscribe with the same key replaces only that subscription, while distinct
keys coexist. The former `/api/v1/audio-scope` endpoint is removed. Opening a socket does not
start a source: send a subscription first. Source IDs are `radio`, `audio_fft`
and `iq_fft` when the corresponding producer is available.

Audio FFT requires observed frequency, filter width and mode. The web adapter
maps `USB`, `LSB`, `AM`, `FM`, `WFM` and the data/narrow aliases declared in
`src/rigplane/web/scope_sources.py: _AUDIO_MODE_ALIASES`. Explicit `CW-U` and
`CW-L` additionally require observed CW pitch. The ambiguous `CW`, `CW-R`,
`RTTY`, `RTTY-R` and `DV` modes keep main's symmetric VFO-centered placement:
the mode filter rule bandwidth, or the full spectrum when the profile declares
no rule. See
`src/rigplane/web/scope_sources.py: WebScopePcmTap.facts`.

```json
{"type":"subscribe","sourceId":"radio","receiver":0,"view":{"follow":"vfo","spanHz":100000},"width":1024,"maxFps":10}
```

An absolute view uses `{"startHz":14100000,"endHz":14200000}` instead of
`follow` and `spanHz`. Audio and IQ sources use `receiver: null`. The reply is
`type: "granted"` with `sourceId`, `receiver`, `view`, `coverage`, `width` and
`fps`. `coverage` can be null. Widths above the source's native bin count are
granted at native resolution instead of being upsampled; the granted message
reports the actual width. For radio sources, `RadioScopeSource.feed_frame` in
`src/rigplane/runtime/radio_scope_source.py` observes native width from the
assembled pixel payload separately for each receiver; null and out-of-range
frames retain that receiver's prior width. Audio FFT native width is the emitted
passband crop's bin count, not the full FFT size; see
`src/rigplane/audio/scope_source.py: AudioFftSource._emit`.
If native width is unknown for the requested receiver, the initial grant
provisionally echoes the requested width.
When the observed width changes, the server sends an updated `granted` before
the corresponding binary frame; existing subscriptions need no new request.
Send another subscription with the same key to change its view, width or rate;
`maxFps: 0` pauses that subscription. To release one stream, send
`{"type":"unsubscribe","sourceId":"radio","receiver":0}`. Bare
`{"type":"unsubscribe"}` and socket close release all subscriptions. A rejected
update preserves the previous subscriptions; errors include `sourceId` and
`receiver` when a valid target can be parsed. The hub starts the producer when
the first positive-rate subscription arrives and stops it when none remain,
including when all retained subscriptions have `maxFps: 0`. An initially paused
subscription does not start the producer. See
`src/rigplane/scope/hub.py: ScopeHub.subscribe` and `ScopeHub._unsubscribe`.

Frames use the typed v2 schema in
`src/rigplane/scope/models.py: ScopeWireFrame`, encoded by
`src/rigplane/web/protocol.py: encode_scope_frame_v2`. Pixels span the granted
view; coverage describes the supplied subrange. Consumers must mark uncovered
columns rather than stretch the covered data. Null pixels and `outOfRange`
are valid frame states. The hub uses max-hold resampling and latest-wins rate
limiting, so sequence gaps can be intentional.

### Binary wire v2

All multibyte fields are little-endian. The fixed header is 56 bytes, followed
by the source ID and optional pixels. The encoder and decoder are
`src/rigplane/web/protocol.py: encode_scope_frame_v2` and
`src/rigplane/web/protocol.py: decode_scope_frame_v2`; portable examples and
the standalone decoder are in `tests/fixtures/scope-wire/`.

| Offset | Bytes | Field |
| --- | --- | --- |
| 0 | 1 | Message type `0x02` |
| 1 | 1 | Source: radio `0`, audio FFT `1`, IQ FFT `2` |
| 2 | 1 | Flags: bit 0 out of range, bit 1 pixels present |
| 3 | 1 | Source ID length `L`, 1–255 |
| 4 | 2 | Receiver uint16; `65535` means null |
| 6 | 2 | Width uint16, 1–65535 |
| 8 | 4 | Epoch uint32 |
| 12 | 4 | Sequence uint32, wrapping modulo 2³² |
| 16 | 8 | Sent time int64 milliseconds; `-1` means null |
| 24 | 8 | View start float64 Hz |
| 32 | 8 | View end float64 Hz |
| 40 | 8 | Coverage start float64 Hz |
| 48 | 8 | Coverage end float64 Hz |
| 56 | `L` | Printable ASCII source ID without spaces |
| `56+L` | width or 0 | Pixel bytes when flag bit 1 is set |

Equal coverage edges represent no covered range. The total length is
`56 + L + (width if pixels are present else 0)`.

The headless simulator benchmark is
`uv run python qa/bench/dogfood/scope_fps.py --radio simulator --transport sim --both`.
It requests concurrent 1024-pixel/10-fps and 2800-pixel/20-fps streams; each
grant is capped at the source native width. A normal
in-range run passes only when usable pixel delivery reaches at least 80% of
each granted frame rate; native sequence gaps caused by rate limiting are
allowed. Null and out-of-range states are counted separately and do not count
towards usable pixel delivery. See `qa/bench/dogfood/scope_fps.py: collect`.

## Overview

Icom radios with spectrum scope (IC-7610, IC-7300, IC-705, etc.) stream real-time spectrum data over the CI-V protocol as unsolicited `0x27 0x00` packets. The `rigplane` library reassembles these multi-sequence bursts and delivers complete frames via callback.

## Quick Start

```python
from rigplane import create_radio, LanBackendConfig
from rigplane.scope import ScopeFrame

async def main():
    config = LanBackendConfig(host="192.168.1.100", username="u", password="p", model="IC-7610")
    async with create_radio(config) as radio:
        frames = []

        def on_frame(frame: ScopeFrame):
            frames.append(frame)
            print(f"{frame.start_freq_hz/1e6:.3f}–{frame.end_freq_hz/1e6:.3f} MHz "
                  f"({len(frame.pixels)} px)")

        radio.on_scope_data(on_frame)
        await radio.enable_scope()

        # ... frames will arrive via callback ...

        await radio.disable_scope()
        radio.on_scope_data(None)  # unregister
```

## Classes

### `ScopeFrame`

```python
from rigplane.scope import ScopeFrame
```

A complete spectrum scope frame, assembled from a burst of CI-V sequences.

| Attribute | Type | Description |
|-----------|------|-------------|
| `receiver` | `int` | 0=MAIN, 1=SUB |
| `mode` | `int` | 0=center, 1=fixed, 2=scroll-C, 3=scroll-F |
| `start_freq_hz` | `int` | Lower edge frequency in Hz |
| `end_freq_hz` | `int` | Upper edge frequency in Hz |
| `pixels` | `bytes` | Amplitude values, each 0x00–0xA0 (0–160) |
| `out_of_range` | `bool` | True if scope data is out of range |

**IC-7610 parameters:**
- Up to 689 pixels per frame
- 15 sequences per burst (SpectrumSeqMax=15)
- Amplitude range 0–160 (SpectrumAmpMax=200, but data range is 0x00–0xA0)
- Dual receiver support (main + sub independently)

### `ScopeAssembler`

```python
from rigplane.scope import ScopeAssembler
```

Low-level assembler that reconstructs `ScopeFrame` objects from raw CI-V `0x27 0x00` payloads. Maintains independent state for main and sub receivers.

```python
asm = ScopeAssembler()
frame = asm.feed(raw_payload, receiver=0)  # payload after receiver byte
if frame is not None:
    process(frame)
```

Most users should use `radio.on_scope_data()` (on the `Radio` returned by `create_radio`) instead of `ScopeAssembler` directly.

## Scope Command Builders

Low-level CI-V command builders for scope control. All take a required `to_addr` and an optional `from_addr`.

| Function | CI-V | Description |
|----------|------|-------------|
| `scope_on()` | `0x27 0x10 0x01` | Enable scope display |
| `scope_off()` | `0x27 0x10 0x00` | Disable scope display |
| `scope_data_output(on)` | `0x27 0x11` | Enable/disable wave data output |
| `scope_main_sub(receiver)` | `0x27 0x12` | Select scope receiver (0=MAIN, 1=SUB) |
| `scope_single_dual(dual)` | `0x27 0x13` | Single/dual scope mode |
| `scope_set_mode(mode)` | `0x27 0x14` | Set scope mode (0–3) |
| `scope_set_span(span, presets=...)` | `0x27 0x15` | Set scope span: an index into `presets`, the radio's declared span table (`RadioProfile.scope_span_presets_hz`) |
| `scope_set_edge(edge)` | `0x27 0x16` | Set scope edge (1–4) |
| `scope_set_hold(on)` | `0x27 0x17` | Scope hold on/off |
| `scope_set_ref(ref)` | `0x27 0x19` | Set reference level in dB (-30.0 to +10.0) |
| `scope_set_speed(speed)` | `0x27 0x1A` | Set speed (0=fast, 1=mid, 2=slow) |
| `scope_set_vbw(narrow)` | `0x27 0x1D` | Video bandwidth (narrow/wide) |
| `scope_set_rbw(rbw)` | `0x27 0x1F` | Resolution bandwidth (0=wide, 1=mid, 2=narrow) |

## Protocol Details

The radio sends spectrum data as a burst of CI-V frames:

```
FE FE <to> <from> 27 00 <receiver> <seq_bcd> <seq_max_bcd> <data...> FD
```

- **Sequence 1**: Metadata — mode, start/end frequency (5-byte BCD each), out-of-range flag
- **Sequences 2..N-1**: Pixel amplitude data (50 pixels per sequence)
- **Sequence N**: Final pixel chunk, frame complete

In center mode, the radio sends center frequency and half-span; the assembler expands these to actual edge frequencies.

Scope data arrives **unsolicited** mixed with normal CI-V traffic. The library processes it as a side-effect in the CI-V receive loop without blocking command responses.

## Capture Helpers

### `capture_scope_frame()`

```python
async def capture_scope_frame(self, timeout: float = 5.0) -> ScopeFrame
```

Enable scope and capture one complete frame. Does NOT disable scope after.

### `capture_scope_frames()`

```python
async def capture_scope_frames(self, count: int = 50, timeout: float = 10.0) -> list[ScopeFrame]
```

Enable scope and capture `count` complete frames. Returns list oldest-first.

**Raises:** `TimeoutError` if fewer than `count` frames arrive within timeout.

## Rendering (optional, requires Pillow)

```bash
pip install rigplane[scope]
```

### `render_spectrum()`

```python
from rigplane.scope_render import render_spectrum

img = render_spectrum(frame, width=800, height=200, theme="classic")
img.save("spectrum.png")
```

Renders a single ScopeFrame as a spectrum plot (amplitude vs frequency) with grid lines and frequency labels.

### `render_waterfall()`

```python
from rigplane.scope_render import render_waterfall

img = render_waterfall(frames, width=800, height=400, theme="classic")
```

Renders multiple frames as a waterfall display. Newest frame at top, color encodes amplitude.

### `render_scope_image()`

```python
from rigplane.scope_render import render_scope_image

img = render_scope_image(frames, output="scope.png", theme="classic")
```

Combined image: spectrum on top, waterfall below.

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `frames` | `list[ScopeFrame]` | *required* | Frames (oldest first) |
| `width` | `int` | `800` | Image width |
| `spectrum_height` | `int` | `200` | Spectrum section height |
| `waterfall_height` | `int` | `400` | Waterfall section height |
| `theme` | `str` | `"classic"` | `"classic"` or `"grayscale"` |
| `output` | `str \| Path \| None` | `None` | Save path (PNG) |

### Color Themes

**`classic`** — WSJT-X inspired: dark blue (noise) → blue → cyan → yellow → red (max)

**`grayscale`** — black (noise) → white (max)

Custom themes can be added to `scope_render.THEMES` dict with anchor points for linear interpolation.

## SDK lifecycle

`src/rigplane/runtime/_scope_runtime.py: ScopeRuntimeMixin.enable_scope` and the
web radio source share `src/rigplane/runtime/scope_session.py: ScopeSession`.
Scope writes now await a CI-V acknowledgement for every completion policy:
`fast` and `strict` do not wait for frames; `verify` additionally waits for
scope activity. This changes the former fire-and-forget timing of `fast`.
`disable_scope` releases SDK demand; the last SDK or web release restores the
captured settings. The wire lifecycle is covered by
`tests/runtime/test_scope_session_wire.py: test_last_release_restores_wire_snapshot_once`.

Implicit FIXED range selection in
`src/rigplane/commands/scope.py: scope_set_fixed_edge` requires profile `ranges`;
callers can instead provide an explicit `range_index`. Negotiated FIXED views
use only the profiles that declare ranges and their frequency step. The shipped
IC-7610 data cites its CI-V reference in `rigs/ic7610.toml`; profiles without
that data negotiate CENTER views.

Hardware scope providers used by the web server must expose
`src/rigplane/runtime/scope_session.py: ScopeSessionProvider.scope_session`;
this replaces the web-owned writer adapter. Providers must feed frame arrival,
readiness, connection changes and TX observations into that session even when
web subscriptions are paused; Core does this from its RX and lifecycle paths.
