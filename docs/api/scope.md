---
robots: noindex, follow
---

# Scope / Waterfall

Spectrum and waterfall data from the radio's scope display.

## Overview

Icom radios with spectrum scope (IC-7610, IC-7300, IC-705, etc.) stream real-time spectrum data over the CI-V protocol as unsolicited `0x27 0x00` packets. The `rigplane` library reassembles these multi-sequence bursts and delivers complete frames via callback.

## Quick Start

```python
from rigplane import create_radio, LanBackendConfig
from rigplane.scope import ScopeFrame

async def main():
    config = LanBackendConfig(host="192.168.1.100", username="u", password="p")
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

Low-level CI-V command builders for scope control. All accept optional `to_addr` and `from_addr` parameters.

| Function | CI-V | Description |
|----------|------|-------------|
| `scope_on()` | `0x27 0x10 0x01` | Enable scope display |
| `scope_off()` | `0x27 0x10 0x00` | Disable scope display |
| `scope_data_output(on)` | `0x27 0x11` | Enable/disable wave data output |
| `scope_data_output_on()` | `0x27 0x11 0x01` | Shortcut: enable data output |
| `scope_data_output_off()` | `0x27 0x11 0x00` | Shortcut: disable data output |
| `scope_main_sub(receiver)` | `0x27 0x12` | Select scope receiver (0=MAIN, 1=SUB) |
| `scope_single_dual(dual)` | `0x27 0x13` | Single/dual scope mode |
| `scope_set_mode(mode)` | `0x27 0x14` | Set scope mode (0–3) |
| `scope_set_span(span)` | `0x27 0x15` | Set scope span (0–7) |
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
