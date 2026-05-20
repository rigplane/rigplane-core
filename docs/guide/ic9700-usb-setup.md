---
description: Control the dual-receiver Icom IC-9700 via USB serial CI-V or LAN from macOS with RigPlane — both transport options covered end to end.
---

# IC-9700 USB Serial Backend Setup (macOS)

This guide shows how to control the **IC-9700 dual-receiver transceiver** via USB serial CI-V + USB audio devices or LAN network connection.

## Why Use the Serial Backend?

- **No network required** — direct USB connection (alternative to LAN)
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Field operation** — works with battery-powered USB
- **Dual-receiver control** — both MAIN and SUB receivers simultaneously

## IC-9700 Special Features

The IC-9700 is the only supported radio with **dual independent receivers**:

| Feature | IC-7610 | IC-705 | IC-7300 | IC-9700 |
|---------|---------|--------|----------|----------|
| **Receivers** | 2 | 1 | 1 | **2** ✨ |
| **LAN Backend** | ✅ | ✅ | ❌ | ✅ |
| **Serial Backend** | ✅ | ✅ | ✅ | ✅ |
| **CI-V Address** | 0x98 | 0xA4 | 0x94 | **0xA2** |

## Hardware Requirements

- IC-9700 satellite/VHF/UHF transceiver
- USB cable (Micro-USB or native USB depending on model variant)
- Ethernet (RJ-45) for LAN backend OR Micro-USB for serial backend
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

### Serial Backend (USB)

!!! danger "Critical Setup Step"
    On the IC-9700, navigate to **Menu → Set → Connectors → CI-V → CI-V USB Port** and set it to **`Link to [CI-V]`**, **NOT** `[REMOTE]`.

    - `Link to [CI-V]` — serial CI-V commands work (required for rigplane serial backend)
    - `[REMOTE]` — RS-BA1 mode, serial CI-V is blocked

### LAN Backend (Ethernet)

The IC-9700 supports LAN operation:

1. Connect Ethernet to a network with your computer
2. Obtain IC-9700's IP address from the radio's menu or by scanning your network
3. Create LAN radio with IP address (see LAN backend section below)

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Port** | `Link to [CI-V]` | ✅ Required for serial |
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0xA2` (IC-9700 default) | Library auto-detects from profile |
| **USB Audio TX** | Enabled | Allows TX via USB audio |
| **USB Audio RX** | Enabled | Exports RX audio to computer |
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
pip install rigplane[serial]
```

### 2. Connect USB and Verify Devices

Plug in the USB cable:

```bash
ls /dev/cu.usbserial-* | head -5
# Output example: /dev/cu.usbserial-A602RVBV
```

Verify audio devices:

```bash
python -c "from rigplane.usb_audio_resolve import list_usb_audio_devices; import json; print(json.dumps(list_usb_audio_devices(), indent=2))"
```

You should see IC-9700 input (RX) and output (TX) audio devices.

### 3. Connect via Python (Serial)

```python
import asyncio
from rigplane import IcomRadio

async def main():
    # Create serial radio for IC-9700
    radio = IcomRadio(
        backend="serial",
        model="IC-9700",
        serial_port="/dev/cu.usbserial-A602RVBV"
    )

    async with radio:
        # MAIN receiver operations (default)
        freq_main = await radio.get_frequency(receiver=0)
        print(f"MAIN Frequency: {freq_main / 1e6:.6f} MHz")

        # SUB receiver operations
        freq_sub = await radio.get_frequency(receiver=1)
        print(f"SUB Frequency: {freq_sub / 1e6:.6f} MHz")

        # Set frequencies independently
        await radio.set_frequency(144_100_000, receiver=0)  # MAIN
        await radio.set_frequency(144_200_000, receiver=1)  # SUB

        # Monitor both receivers
        for _ in range(5):
            main_s = await radio.get_s_meter(receiver=0)
            sub_s = await radio.get_s_meter(receiver=1)
            print(f"MAIN S-meter: {main_s}  SUB S-meter: {sub_s}")
            await asyncio.sleep(1)

asyncio.run(main())
```

### 4. CLI Usage (Serial)

```bash
# Check connection
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV status

# MAIN receiver (default)
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV freq 144100000

# SUB receiver
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV \
    --receiver 1 freq 144200000

# Monitor both
rigplane --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV meters
```

### 5. Web UI (Serial)

```bash
rigplane web --backend serial --model IC-9700 --serial-port /dev/cu.usbserial-A602RVBV
# Open http://localhost:8000
# Use MAIN/SUB selector in web UI
```

## macOS Setup: LAN Backend

The IC-9700 supports direct LAN connection (Ethernet):

### 1. Network Configuration

```bash
# Discover IC-9700 on your network
rigplane discover --timeout 5

# Output:
# Found: IC-9700 at 192.168.1.100
```

### 2. Connect via Python (LAN)

```python
import asyncio
from rigplane import IcomRadio

async def main():
    # Create LAN radio for IC-9700
    radio = IcomRadio(
        backend="lan",
        host="192.168.1.100",
        model="IC-9700",
        username="radio",
        password="password"  # From radio network settings
    )

    async with radio:
        # LAN operations are identical to serial
        main_freq = await radio.get_frequency(receiver=0)
        sub_freq = await radio.get_frequency(receiver=1)
        print(f"MAIN: {main_freq / 1e6:.6f} MHz  SUB: {sub_freq / 1e6:.6f} MHz")

asyncio.run(main())
```

### 3. CLI Usage (LAN)

```bash
rigplane --backend lan --host 192.168.1.100 status
rigplane --backend lan --host 192.168.1.100 freq 144100000
```

## Dual-Receiver Operations

### Independent Frequency Control

```python
async with radio:
    # Set MAIN and SUB to different frequencies simultaneously
    await radio.set_frequency(144_100_000, receiver=0)  # MAIN: 144.1 MHz
    await radio.set_frequency(144_200_000, receiver=1)  # SUB:  144.2 MHz

    # Read both
    main = await radio.get_frequency(receiver=0)
    sub = await radio.get_frequency(receiver=1)
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

### Dual Audio RX

```python
async def on_main_audio(data: bytes, sample_rate: int):
    print(f"MAIN RX: {len(data)} bytes")

async def on_sub_audio(data: bytes, sample_rate: int):
    print(f"SUB RX: {len(data)} bytes")

async with radio:
    # Both receivers streaming simultaneously
    radio.start_audio_rx(callback=on_main_audio, receiver=0)
    radio.start_audio_rx(callback=on_sub_audio, receiver=1)

    await asyncio.sleep(10)

    radio.stop_audio_rx(receiver=0)
    radio.stop_audio_rx(receiver=1)
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
CommandError: does not support receiver=1
```

**Cause**: Using `receiver=1` on a single-receiver radio (IC-705, IC-7300).

**Solution**: Only IC-9700 (and IC-7610) support dual receivers. Check model in use.

### "Device not found"

```
ConnectionError: failed to open serial port /dev/cu.usbserial-XXXXX
```

**Solution**: Verify USB cable and check `/dev/cu.usbserial-*` listing.

### Low Baud Rate Warning (Serial)

**Solution**: Set **CI-V USB Baud Rate** to `115200` in radio menu.

### LAN Connection Timeout

```
ConnectionError: failed to connect to 192.168.1.100
```

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
