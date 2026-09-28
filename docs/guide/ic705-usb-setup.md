---
description: Configure the Icom IC-705 for USB serial CI-V and USB audio control from macOS with RigPlane — field-friendly setup with no network required.
---

# IC-705 USB Serial Backend Setup (macOS)

This guide shows how to control the IC-705 via **USB serial CI-V + USB audio devices** instead of the default LAN backend.

## Why Use the Serial Backend?

- **No network required** — direct USB connection
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Field operation** — works without WiFi/Ethernet
- **Portable operation** — ideal for IC-705's portable/QRP use case

## Hardware Requirements

- IC-705 portable transceiver (HF/VHF/UHF)
- micro-USB cable (the IC-705 has a `[microUSB]` port)
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

!!! note "Two USB serial ports"
    Over USB the IC-705 presents two serial ports. CI-V uses the first one,
    which Icom's USB driver names "IC-705 Serial Port A (CI-V)".

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0xA4` (IC-705 default) | Library auto-detects from profile |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Lower baud rates (19200, 9600) work for basic control (freq, mode, PTT) but scope/waterfall is disabled by a guardrail due to high packet rate
    - CI-V baud rate IS significant on IC-705 — it must match between radio and library

!!! info "IC-705 Single Receiver"
    The IC-705 has a single receiver, unlike the IC-7610's dual receiver. The library automatically enforces this via the IC-705 profile — operations on `receiver=1` will fail with `CommandError`.

## macOS Setup

### 1. Install rigplane

```bash
# Core install — includes serial CI-V (pyserial), USB audio RX/TX,
# audio-device listing (sounddevice + numpy + opuslib).
pip install rigplane
```

!!! note
    Since v0.19 the audio-bridge stack (`sounddevice`, `numpy`, `opuslib`)
    is part of the core install. The legacy `[bridge]` and `[audio]` extras
    still resolve but are now no-op aliases.

### 2. Connect the Radio

1. Power on the IC-705
2. Connect the micro-USB cable from the Mac to the IC-705 `[microUSB]` port
3. Wait ~5 seconds for macOS to enumerate the device

### 3. Find the Serial Device

```bash
# List serial devices
ls -l /dev/cu.usbserial-*

# Example output:
# /dev/cu.usbserial-IC705123
```

The IC-705 typically appears as `/dev/cu.usbserial-*` where the suffix may include "IC705" or the radio's serial number.

You can also use `rigplane discover --serial-only` to list USB serial candidates and identify likely supported radios:

```bash
rigplane discover --serial-only
```

```
IC-705:
  • Serial: /dev/cu.usbserial-IC705123 (115200 baud)
```

### 4. Find USB Audio Devices

```bash
# List available audio devices
rigplane --list-audio-devices
```

Audio-device listing requires `sounddevice`, which ships with the core
install since v0.19 (`pip install rigplane`).

An Icom radio's USB audio device is named `USB Audio CODEC`. The serial
backend picks the audio device automatically when `--rx-device` and
`--tx-device` are not given.

## Usage Examples

### CLI: Basic Control

```bash
# Read status over USB serial
rigplane --backend serial --serial-port /dev/cu.usbserial-IC705123 \
  --model IC-705 --serial-baud 115200 status

# Set the frequency and mode
rigplane --backend serial --serial-port /dev/cu.usbserial-IC705123 \
  --model IC-705 --serial-baud 115200 freq 14074000
rigplane --backend serial --serial-port /dev/cu.usbserial-IC705123 \
  --model IC-705 --serial-baud 115200 mode USB
```

### Python: Async API

```python
import asyncio
from rigplane.backends.factory import create_radio
from rigplane.backends.config import SerialBackendConfig

async def main():
    # Create IC-705 serial backend
    config = SerialBackendConfig(
        device="/dev/cu.usbserial-IC705123",
        model="IC-705",
        baudrate=115200,
        rx_device="USB Audio CODEC",  # Optional
        tx_device="USB Audio CODEC",  # Optional
    )

    radio = create_radio(config)

    async with radio:
        # Get frequency
        freq = await radio.get_freq()
        print(f"Frequency: {freq} Hz")

        # Set frequency (14.074 MHz USB for FT8)
        await radio.set_freq(14_074_000)
        await radio.set_mode("USB")

        # Get mode
        mode, filt = await radio.get_mode()
        print(f"Mode: {mode}, Filter: {filt}")

        # Note: IC-705 has single receiver only
        # receiver=1 operations will fail

asyncio.run(main())
```

### Web UI

```bash
# Start web server with IC-705 serial backend
rigplane --backend serial --serial-port /dev/cu.usbserial-IC705123 \
  --model IC-705 --serial-baud 115200 \
  web

# Open browser to http://localhost:8080
```

The web UI provides:
- Frequency/mode control
- VFO select/swap
- Scope/waterfall display
- Audio bridge (browser → radio TX, radio RX → browser)
- PTT control
- Memory/filter/settings panels

### rigctld (Hamlib Compatibility)

```bash
# Start rigctld server with IC-705 serial backend
rigplane --backend serial --serial-port /dev/cu.usbserial-IC705123 \
  --model IC-705 --serial-baud 115200 \
  serve --port 4532

# Test with rigctl client
rigctl -m 2 -r localhost:4532 f  # Get frequency
rigctl -m 2 -r localhost:4532 F 14074000  # Set frequency
```

## Troubleshooting

### Serial Device Not Found

```bash
# Check if USB device enumerated
system_profiler SPUSBDataType | grep -A 10 "IC-705"

# Check for any serial devices
ls -l /dev/cu.*
```

**Solutions:**
- Verify the micro-USB cable is data-capable (not charge-only)
- Try a different USB port on your Mac
- Power cycle the IC-705 and wait for enumeration
- Check macOS System Settings → Privacy & Security → USB permissions

### CI-V Commands Failing

**Symptoms:** Connection succeeds but commands timeout or return NAK

**Solutions:**
1. Check CI-V baud rate matches radio setting (default: 115200)
2. Verify CI-V address is `0xA4` (IC-705 default)
3. Check USB cable integrity

### USB Audio Not Working

**Symptoms:** CI-V control works but no audio in browser/WSJT-X

**Solutions:**
- Make sure you're on rigplane v0.19+ (audio deps ship with the core install; for older versions run `pip install 'rigplane[bridge]'`)
- Check audio device names with `rigplane --list-audio-devices`
- macOS may require microphone/audio permissions for the terminal app

### Scope/Waterfall Disabled

**Symptoms:** `enable_scope()` raises error about baud rate

**Solutions:**
- IC-705 requires `115200` baud for scope capability
- Lower baud rates (19200, 9600) will trigger the guardrail
- Set `--serial-baud 115200` or `baudrate=115200` in config
- Override the guardrail with `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1` or `allow_low_baud_scope=True` (not recommended)

### Single Receiver Errors

**Symptoms:** `CommandError: … does not support receiver=1 for profile IC-705 (receivers=1)`

**This is expected behavior** — IC-705 has a single receiver (receiver=0 only). The dual-receiver APIs inherited from `CoreRadio` will fail for `receiver=1` operations. Use `receiver=0` (or omit the parameter, as 0 is the default).

## Capability Matrix: IC-705 vs IC-7610

| Feature | IC-705 | IC-7610 | Notes |
|---------|--------|---------|-------|
| **Receiver count** | 1 | 2 | IC-705 single receiver only |
| **Command 29 (sub RX)** | ❌ No | ✅ Yes | IC-705 profile: `command_29` not in capabilities |
| **CI-V Address** | `0xA4` | `0x98` | Auto-detected from profile |
| **Baud rates** | 115200 recommended | 115200 recommended | Lower rates work but disable scope |
| **Audio codec** | PCM 1ch 16bit | PCM 1ch/2ch 16bit | |
| **Scope** | Single stream | Dual stream | IC-705 single receiver = single scope |
| **USB connector** | micro-USB | USB-B | Different cables |
| **Portable** | ✅ Yes (QRP, battery) | ❌ No (base station) | IC-705 field-optimized |

## Backend Comparison: LAN vs Serial

| | LAN (UDP) | Serial (USB) |
|-|-----------|--------------|
| **Connection** | Ethernet/WiFi | USB cable |
| **Setup** | IP config, username, password | Plug and play |
| **Latency** | ~10-50ms | ~5-20ms |
| **Field operation** | Requires network | Direct connection |
| **Audio** | Opus over LAN | USB audio devices |
| **Scope** | UDP stream | USB serial stream |
| **Production status** | ✅ Stable (M1-M4) | ✅ Stable (M3, IC-7610 parity) |

## See Also

- [IC-7610 USB Setup](ic7610-usb-setup.md) — Similar setup for base station model
- [Radio Protocol](../radio-protocol.md) — Backend abstraction details
- [Troubleshooting](troubleshooting.md) — Common issues and solutions
- [Backend Capabilities](radios.md) — Full capability matrix

## Hardware Procurement Status

!!! warning "Development Hardware"
    **Hardware validation of the IC-705 serial backend is pending.**

## Get the Packaged Desktop App

!!! tip "Prefer a packaged desktop app?"
    This guide covers the open-source `rigplane` Python library. If you want
    a polished desktop application with a GUI for IC-705 control on macOS
    and Linux, check out RigPlane Pro.

    [Download RigPlane Pro for Mac and Linux →](https://rigplane.com/downloads/?utm_source=docs.rigplane.dev&utm_medium=cta&utm_campaign=ic705-usb-setup)
