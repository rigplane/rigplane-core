---
description: Control the dual-receiver Icom IC-9700 via USB serial CI-V or LAN from macOS with RigPlane — both transport options covered end to end.
---

# IC-9700 USB Serial Backend Setup (macOS)

This guide shows how to control the **IC-9700 dual-receiver transceiver** via USB serial CI-V + USB audio devices or LAN network connection.

## Why Use the Serial Backend?

- **No network required** — direct USB connection (alternative to LAN)
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Dual-receiver control** — both MAIN and SUB receivers simultaneously

## IC-9700 Special Features

The IC-9700 has **dual independent receivers**:

| Feature | IC-7610 | IC-705 | IC-7300 | IC-9700 |
|---------|---------|--------|----------|----------|
| **Receivers** | 2 | 1 | 1 | **2** ✨ |
| **LAN Backend** | ✅ | ✅ | ❌ | ✅ |
| **Serial Backend** | ✅ | ✅ | ✅ | ✅ |
| **CI-V Address** | 0x98 | 0xA4 | 0x94 | **0xA2** |

## Hardware Requirements

- IC-9700 satellite/VHF/UHF transceiver
- USB cable with a Type-B plug (the IC-9700 `[USB]` port is Type B) for the serial backend
- Ethernet (RJ-45) for the LAN backend
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

### Serial Backend (USB)

!!! danger "Critical Setup Step"
    On the IC-9700, navigate to **Menu → Set → Connectors → CI-V → CI-V USB Port** and set it to **`Unlink from [REMOTE]`** (the radio's default), **NOT** `Link to [REMOTE]`.

    - `Unlink from [REMOTE]` — the `[USB]` and `[REMOTE]` CI-V ports work independently; the CI-V USB Baud Rate setting applies only in this mode
    - `Link to [REMOTE]` — the `[USB]` and `[REMOTE]` CI-V ports are connected internally

### LAN Backend (Ethernet)

The IC-9700 supports LAN operation:

1. Connect Ethernet to a network with your computer
2. Obtain IC-9700's IP address from the radio's menu or by scanning your network
3. Create LAN radio with IP address (see LAN backend section below)

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Port** | `Unlink from [REMOTE]` | ✅ Required for serial |
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0xA2` (IC-9700 default) | Library auto-detects from profile |
| **Dual Watch** | Enabled (optional) | Monitor both MAIN and SUB |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Lower baud rates (19200, 9600) work for basic control but scope is disabled
    - CI-V baud rate must match between radio and library configuration

!!! warning "Dual-Receiver Considerations"
    When using both receivers:
    - MAIN and SUB can be on different frequencies
    - Each receiver has independent controls (AF/RF/mode/etc.)
    - Audio from both receivers can be simultaneously RX
    - Library enforces receiver-aware operations via receiver parameter

## macOS Setup: Serial Backend

### 1. Install rigplane

```bash
pip install rigplane
```

### 2. Connect USB and Verify Devices

Plug in the USB cable:

```bash
ls /dev/cu.usbserial-* | head -5
# Output example: /dev/cu.usbserial-A602RVBV
```

Verify audio devices:

```bash
rigplane --list-audio-devices
```

An Icom radio's USB audio device is named `USB Audio CODEC`.

### 3. Connect via Python (Serial)

```python
import asyncio
from rigplane import SerialBackendConfig, create_radio

async def main():
    # Serial radio for IC-9700
    config = SerialBackendConfig(device="/dev/cu.usbserial-A602RVBV", model="IC-9700")

    async with create_radio(config) as radio:
        # MAIN receiver operations (default)
        freq_main = await radio.get_freq(receiver=0)
        print(f"MAIN Frequency: {freq_main / 1e6:.6f} MHz")

        # SUB receiver operations
        freq_sub = await radio.get_freq(receiver=1)
        print(f"SUB Frequency: {freq_sub / 1e6:.6f} MHz")

        # Set frequencies independently
        await radio.set_freq(144_100_000, receiver=0)  # MAIN
        await radio.set_freq(144_200_000, receiver=1)  # SUB

asyncio.run(main())
```

### 4. CLI Usage (Serial)

```bash
# Check connection
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV status

# Set the frequency
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV freq 144100000

# Read the meters
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV meter
```

### 5. Web UI (Serial)

```bash
rigplane --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV web
# Open http://localhost:8080
# Use MAIN/SUB selector in web UI
```

## macOS Setup: LAN Backend

The IC-9700 supports direct LAN connection (Ethernet):

### 1. Network Configuration

```bash
# Discover IC-9700 on your network
rigplane discover --timeout 5
```

### 2. Connect via Python (LAN)

```python
import asyncio
from rigplane import LanBackendConfig, create_radio

async def main():
    # LAN radio for IC-9700
    config = LanBackendConfig(
        host="192.168.1.100",
        model="IC-9700",
        username="radio",
        password="password",  # From radio network settings
    )

    async with create_radio(config) as radio:
        # LAN operations are identical to serial
        main_freq = await radio.get_freq(receiver=0)
        sub_freq = await radio.get_freq(receiver=1)
        print(f"MAIN: {main_freq / 1e6:.6f} MHz  SUB: {sub_freq / 1e6:.6f} MHz")

asyncio.run(main())
```

### 3. CLI Usage (LAN)

```bash
rigplane --model IC-9700 --backend lan --host 192.168.1.100 status
rigplane --model IC-9700 --backend lan --host 192.168.1.100 freq 144100000
```

## Dual-Receiver Operations

### Independent Frequency Control

```python
async with radio:
    # Set MAIN and SUB to different frequencies simultaneously
    await radio.set_freq(144_100_000, receiver=0)  # MAIN: 144.1 MHz
    await radio.set_freq(144_200_000, receiver=1)  # SUB:  144.2 MHz

    # Read both
    main = await radio.get_freq(receiver=0)
    sub = await radio.get_freq(receiver=1)
    print(f"MAIN: {main}  SUB: {sub}")
```

### Independent Mode Control

```python
async with radio:
    await radio.set_mode("USB", receiver=0)   # MAIN: USB
    await radio.set_mode("CW", receiver=1)    # SUB:  CW

    main_mode, _ = await radio.get_mode(receiver=0)
    sub_mode, _ = await radio.get_mode(receiver=1)
    print(f"MAIN: {main_mode}  SUB: {sub_mode}")
```

## Supported Features

| Feature | MAIN RX | SUB RX | Status |
|---------|---------|--------|--------|
| **Frequency** | ✅ | ✅ | Independent control |
| **Mode** | ✅ | ✅ | Independent control |
| **Power** | ✅ | N/A | MAIN only (radio limitation) |
| **Scope/Waterfall** | ✅ | ✅ (SUB) | Both receivers, switchable |
| **Audio RX** | ✅ | ✅ | Simultaneous dual audio |
| **Meters** | ✅ | ✅ | Per-receiver S-meter |
| **Filters** | ✅ | ✅ | Independent per receiver |
| **DSP** | ✅ | ✅ | Per-receiver settings |
| **Dual Watch** | ✅ (setting) | ✅ (setting) | Library compatible |

## Troubleshooting

### Receiver=1 Operations Fail

```
CommandError: get_freq does not support receiver=1 for profile IC-7300 (receivers=1)
```

**Cause**: Using `receiver=1` on a single-receiver radio (IC-705, IC-7300).

**Solution**: Only profiles with two receivers accept `receiver=1` (IC-9700, IC-7610, FTX-1). Check model in use.

### Serial port not found

**Solution**: Verify USB cable and check `/dev/cu.usbserial-*` listing.

### Low Baud Rate Warning (Serial)

**Solution**: Set **CI-V USB Baud Rate** to `115200` in radio menu.

### LAN Connection Timeout

**Solution**:
1. Verify IP address with `rigplane discover`
2. Check network connectivity: `ping 192.168.1.100`
3. Verify radio network settings (username/password)

## Performance Notes

- **Dual-receiver switching**: ~200ms per receiver (web UI smoothing applied)
- **Dual audio RX**: ~10-20ms latency per receiver
- **Scope data rate**: ~50 Hz at 115200 baud

## See Also

- [IC-7610 USB Setup](ic7610-usb-setup.md) — Dual-receiver desktop radio
- [IC-7300 USB Setup](ic7300-usb-setup.md) — Single-receiver desktop radio
- [Audio Recipes](audio-recipes.md) — RX/TX dual-receiver examples
- [Web UI Guide](web-ui.md) — Dual-receiver web interface

## Get the Packaged Desktop App

!!! tip "Prefer a packaged desktop app?"
    This guide covers the open-source `rigplane` Python library. If you want
    a polished desktop application with a GUI for IC-9700 control on macOS
    and Linux, check out RigPlane Pro.

    [Download RigPlane Pro for Mac and Linux →](https://rigplane.com/downloads/?utm_source=docs.rigplane.dev&utm_medium=cta&utm_campaign=ic9700-usb-setup)
