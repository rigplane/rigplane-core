---
description: Configure the Icom IC-705 for USB serial CI-V and USB audio control from macOS with RigPlane — field-friendly setup with no network required.
---

# IC-705 USB Serial Backend Setup (macOS)

This guide shows how to control the IC-705 via **USB serial CI-V + USB audio devices** instead of the default LAN backend.

## Why Use the Serial Backend?

- **No network required** — direct USB connection
- **Simpler setup** — no IP config, username, or password
- **Field operation** — works without WiFi/Ethernet
- **Portable operation** — ideal for IC-705's portable/QRP use case

## Hardware Requirements

- IC-705 portable transceiver (HF/VHF/UHF)
- USB cable for the IC-705's **[microUSB]** port
- macOS computer

## Radio Configuration

The IC-705 CI-V Reference Guide (p.2) says to set the radio's CI-V address,
data communication speed and transceive function in Set mode, and refers to
the IC-705 instruction manual for those settings.

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V data communication speed** | `115200` | RigPlane's default for the IC-705; needed for scope/waterfall |
| **CI-V Address** | `0xA4` (IC-705 default) | RigPlane reads it from the IC-705 profile (`--model IC-705`) |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Below `115200`, scope/waterfall is disabled by a guardrail due to high packet rate

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
2. Connect a USB cable from the Mac to the IC-705's **[microUSB] port** (right side panel)

### 3. Find the Serial Device

The IC-705 presents two USB serial ports. Its CI-V Reference Guide (p.2)
names them "IC-705 Serial Port A (CI-V)" and "IC-705 Serial Port B" on a PC
with Icom's USB driver: CI-V is on port A.

```bash
# List serial devices
ls -l /dev/cu.*
```

You can also use `rigplane discover --serial-only` to list USB serial candidates and identify likely supported radios:

```bash
rigplane discover --serial-only
```

### 4. Find USB Audio Devices

```bash
# List available audio devices
rigplane --list-audio-devices
```

Audio-device listing requires `sounddevice`, which ships with the core
install since v0.19 (`pip install rigplane`).

!!! note "Audio Device Names"
    Without `--rx-device` and `--tx-device`, RigPlane picks a USB audio device
    itself. To choose one, pass a device name from this list to `--rx-device`
    and `--tx-device`.

## Usage Examples

### CLI: Basic Control

Replace `/dev/cu.XXXX` with the IC-705's CI-V port from step 3.

```bash
# Frequency, mode and meters over USB serial
rigplane --serial-port /dev/cu.XXXX --model IC-705 status
```

### Python: Async API

```python
import asyncio
from rigplane.backends.factory import create_radio
from rigplane.backends.config import SerialBackendConfig

async def main():
    # Create IC-705 serial backend
    config = SerialBackendConfig(
        device="/dev/cu.XXXX",
        model="IC-705",
        baudrate=115200,
        rx_device=None,  # Optional: a name from `rigplane --list-audio-devices`
        tx_device=None,  # Optional
    )

    radio = create_radio(config)

    async with radio:
        # Get frequency
        freq = await radio.get_frequency()
        print(f"Frequency: {freq} Hz")

        # Set frequency (14.074 MHz USB for FT8)
        await radio.set_frequency(14_074_000)
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
rigplane --serial-port /dev/cu.XXXX --model IC-705 \
  --rx-device "<RX device name>" \
  --tx-device "<TX device name>" \
  web

# Open browser to http://localhost:8080
```

### rigctld (Hamlib Compatibility)

```bash
# Start rigctld server with IC-705 serial backend
rigplane --serial-port /dev/cu.XXXX --model IC-705 \
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
- Verify the USB cable is data-capable (not charge-only)
- Try a different USB port on your Mac
- Power cycle the IC-705 and wait for enumeration

### CI-V Commands Failing

**Symptoms:** Connection succeeds but commands timeout or return NAK

**Solutions:**
1. Check the radio's CI-V data communication speed against the baud rate RigPlane uses (`115200` unless `--serial-baud` sets another)
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
- Override guardrail with `allow_low_baud_scope=True` in `SerialBackendConfig` or `ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1` (not recommended)

### Single Receiver Errors

**Symptoms:** `CommandError: does not support receiver=1`

**This is expected behavior** — IC-705 has a single receiver (receiver=0 only). The dual-receiver APIs inherited from `CoreRadio` will fail for `receiver=1` operations. Use `receiver=0` (or omit the parameter, as 0 is the default).

## Capability Matrix: IC-705 vs IC-7610

| Feature | IC-705 | IC-7610 | Notes |
|---------|--------|---------|-------|
| **Receiver count** | 1 | 2 | IC-705 single receiver only |
| **Command 29 (sub RX)** | ❌ No | ✅ Yes | IC-705 profile: `command_29` not in capabilities |
| **CI-V Address** | `0xA4` | `0x98` | From the `--model` profile |
| **Baud rates** | 115200 recommended | 115200 recommended | Lower rates disable scope |
| **Scope** | Single stream | Dual stream | IC-705 single receiver = single scope |
| **Portable** | ✅ Yes (QRP, battery) | ❌ No (base station) | IC-705 field-optimized |

## Backend Comparison: LAN vs Serial

| | LAN (UDP) | Serial (USB) |
|-|-----------|--------------|
| **Connection** | WiFi | USB cable |
| **Setup** | IP address, username, password, `--model IC-705` | Serial port, `--model IC-705` |
| **Field operation** | Requires network | Direct connection |
| **Audio** | Over the LAN link | USB audio devices |
| **Scope** | UDP stream | USB serial stream |

## See Also

- [IC-7610 USB Setup](ic7610-usb-setup.md) — Similar setup for base station model
- [Radio Protocol](../radio-protocol.md) — Backend abstraction details
- [Troubleshooting](troubleshooting.md) — Common issues and solutions
- [Backend Capabilities](radios.md) — Full capability matrix

## Validation Status

!!! warning "Not yet validated on 3.0"
    The IC-705 is community-validated on RigPlane 2.x over WiFi. It has not
    yet been validated on 3.0.

## Get the Packaged Desktop App

!!! tip "Prefer a packaged desktop app?"
    This guide covers the open-source `rigplane` Python library. If you want
    a polished desktop application with a GUI for IC-705 control on macOS
    and Linux, check out RigPlane Pro.

    [Download RigPlane Pro for Mac and Linux →](https://rigplane.com/downloads/?utm_source=docs.rigplane.dev&utm_medium=cta&utm_campaign=ic705-usb-setup)
