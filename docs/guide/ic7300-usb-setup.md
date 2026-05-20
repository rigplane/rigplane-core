---
description: Set up the Icom IC-7300 over USB serial CI-V and USB audio from macOS with RigPlane — low-latency direct control without LAN, hamlib, or RS-BA1.
---

# IC-7300 USB Serial Backend Setup (macOS)

This guide shows how to control the IC-7300 via **USB serial CI-V + USB audio devices** instead of the default LAN backend.

## Why Use the Serial Backend?

- **No network required** — direct USB connection
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Field operation** — works with battery-powered USB (e.g., Powerex)
- **Alternative to LAN** — for users without Ethernet/WiFi on IC-7300

## Hardware Requirements

- IC-7300 transceiver (HF/50 MHz)
- Micro-USB cable (IC-7300 uses Micro-USB)
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

!!! danger "Critical Setup Step"
    On the IC-7300, navigate to **Menu → Set → Connectors → CI-V → CI-V USB Port** and set it to **`Link to [CI-V]`**, **NOT** `[REMOTE]`.

    - `Link to [CI-V]` — serial CI-V commands work (required for rigplane serial backend)
    - `[REMOTE]` — RS-BA1 mode, serial CI-V is blocked

    This is the same critical setting as IC-7610 and IC-705.

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Port** | `Link to [CI-V]` | ✅ Required — enables serial CI-V |
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0x94` (IC-7300 default) | Library auto-detects from profile |
| **USB Audio TX** | Enabled | Allows browser/WSJT-X TX via USB audio |
| **USB Audio RX** | Enabled | Exports RX audio to computer |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Lower baud rates (19200, 9600) work for basic control (freq, mode, PTT) but scope/waterfall is disabled by a guardrail due to high packet rate
    - CI-V baud rate IS significant on IC-7300 — it must match between radio and library

!!! info "IC-7300 Single Receiver"
    The IC-7300 has a single receiver (unlike IC-7610's dual receiver or IC-9700's dual). The library automatically enforces this via the IC-7300 profile — operations on `receiver=1` will fail with `CommandError`.

## macOS Setup

### 1. Install rigplane

```bash
pip install rigplane[serial]
```

### 2. Connect USB and Verify Devices

Plug in the USB cable and verify the serial port:

```bash
ls /dev/cu.usbserial-* | head -5
# Output example: /dev/cu.usbserial-A602RVAV
```

Check for audio devices exported by the radio:

```bash
python -c "from rigplane.usb_audio_resolve import list_usb_audio_devices; import json; print(json.dumps(list_usb_audio_devices(), indent=2))"
```

You should see input and output devices for the IC-7300:
- **Input (RX)**: e.g., `"IC-7300 (In 1)"`
- **Output (TX)**: e.g., `"IC-7300 (Out 1)"`

### 3. Connect via Python

```python
import asyncio
from rigplane import IcomRadio

async def main():
    # Create serial radio for IC-7300
    radio = IcomRadio(backend="serial", model="IC-7300", serial_port="/dev/cu.usbserial-A602RVAV")

    async with radio:
        # Read frequency
        freq = await radio.get_frequency()
        print(f"Frequency: {freq / 1e6:.6f} MHz")

        # Set frequency
        await radio.set_frequency(7_074_000)

        # Read mode
        mode, _ = await radio.get_mode()
        print(f"Mode: {mode}")

        # Enable scope
        await radio.enable_scope()

        # Scope data is now available via radio.scope_data

asyncio.run(main())
```

### 4. CLI Usage

```bash
# Check connection
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV status

# Set frequency
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV freq 7074000

# Monitor metrics
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV meters
```

### 5. Web UI

```bash
rigplane web --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV
# Open http://localhost:8000
```

## Supported Features

| Feature | Status | Notes |
|---------|--------|-------|
| **Frequency** | ✅ Full | Get/set HF/50 MHz |
| **Mode** | ✅ Full | USB, LSB, CW, FM, AM, RTTY, PSK, etc. |
| **Power** | ✅ Full | RF power 0-100W |
| **Scope/Waterfall** | ✅ Full | Real-time scope at 115200 baud |
| **Audio RX/TX** | ✅ Full | PCM and Opus codecs |
| **Meters** | ✅ Full | S-meter, SWR, ALC, Power, Vd, Id |
| **Filters** | ✅ Full | Filter width selection |
| **DSP** | ✅ Full | NB, NR, APF, Twin Peak, PBT |
| **Dual Watch** | ⚠️ Single RX | IC-7300 has single receiver only |

## Audio Subsystem

IC-7300 exports USB audio devices that rigplane can use:

```python
# Receive audio from radio
async def on_audio(pcm_data: bytes, sample_rate: int):
    print(f"RX audio: {len(pcm_data)} bytes @ {sample_rate} Hz")

async with radio:
    radio.start_audio_rx(callback=on_audio)
    await asyncio.sleep(5)
    radio.stop_audio_rx()

# Transmit audio to radio
async with radio:
    radio.start_audio_tx(sample_rate=16000, channels=1)
    # Push 160ms of PCM data (2560 bytes @ 16kHz)
    radio.push_audio(pcm_data)
    radio.stop_audio_tx()
```

### WSJT-X Integration

Use macOS BlackHole (or Loopback) to bridge rigplane audio to WSJT-X:

1. **Install BlackHole 2ch**: https://github.com/ExistentialAudio/BlackHole
2. **Create aggregate device**:
   - Audio Midi Setup → + → "IC-7300 Bridge"
   - Add "IC-7300" input + "BlackHole 2ch" output
3. **Start audio bridge**:
   ```bash
   rigplane audio bridge --serial-port ic-7300-usb-in --loopback "BlackHole 2ch"
   ```
4. **WSJT-X settings**:
   - Input Device: "IC-7300 Bridge"
   - Output Device: "BlackHole 2ch" or USB audio device

## Troubleshooting

### "Device not found" Error

```
ConnectionError: failed to open serial port /dev/cu.usbserial-XXXXX
```

**Solution**: Verify USB cable connection and check `/dev/cu.usbserial-*` listing.

### Low Baud Rate Warning

```
WARNING: Scope disabled due to low baud rate (9600 < 115200 minimum)
```

**Solution**: Set **CI-V USB Baud Rate** to `115200` in radio menu → Set → Connectors.

### Audio Devices Not Found

```
WARNING: audio subsystem not detected; TX/RX disabled
```

**Solution**: Verify **USB Audio TX/RX** are enabled in radio settings. Check with:
```bash
python -c "from rigplane.usb_audio_resolve import list_usb_audio_devices; import json; print(json.dumps(list_usb_audio_devices(), indent=2))"
```

### CI-V Commands Timing Out

```
CommandError: timeout waiting for CI-V response
```

**Solution**: Verify **CI-V USB Port** is set to `Link to [CI-V]`, not `[REMOTE]`.

## Performance Tuning

### Serial CI-V Pacing

If commands are arriving too fast, adjust the minimum interval between CI-V commands:

```python
radio = IcomRadio(
    backend="serial",
    model="IC-7300",
    serial_port="/dev/cu.usbserial-A602RVAV",
)
# Set 100ms minimum between CI-V commands (default is 50ms)
import os
os.environ["ICOM_SERIAL_CIV_MIN_INTERVAL_MS"] = "100"
```

### Scope Baud Rate Tradeoff

- **115200 baud**: Full scope resolution, ~50 spectra/sec
- **57600 baud**: Reduced scope rate, more headroom for CI-V
- **19200 baud**: Minimal scope rate, most headroom

## Hardware Notes

- **Micro-USB connector**: Strain relief recommended for field use
- **USB power**: IC-7300 can be powered via USB; use quality cable
- **Cable length**: Keep under 3m to avoid signal integrity issues
- **macOS driver**: No additional driver needed; built-in CDC ACM support

## See Also

- [IC-7610 USB Setup](ic7610-usb-setup.md) — Dual-receiver configuration
- [IC-705 USB Setup](ic705-usb-setup.md) — Portable transceiver
- [Audio Recipes](audio-recipes.md) — RX/TX examples
- [WSJT-X Setup](wsjtx-setup.md) — Digital mode integration
