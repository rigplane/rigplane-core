---
description: Set up the Icom IC-7300 over USB serial CI-V and USB audio from macOS with RigPlane's native Icom provider.
---

# IC-7300 USB Serial Backend Setup (macOS)

This guide shows how to control the IC-7300 via **USB serial CI-V + USB audio devices** instead of the default LAN backend.

## Why Use the Serial Backend?

- **No network required** — direct USB connection
- **Lower latency** — no UDP/network overhead
- **Simpler setup** — no IP config, username, or password
- **Alternative to LAN** — for users without Ethernet/WiFi on IC-7300

## Hardware Requirements

- IC-7300 transceiver (HF/50 MHz)
- USB cable with a Type-B plug (the IC-7300 `[USB]` port is Type B)
- macOS computer (tested on Ventura+ arm64/Intel)

## Radio Configuration

!!! danger "Critical Setup Step"
    On the IC-7300, navigate to **Menu → Set → Connectors → CI-V → CI-V USB Port** and set it to **`Unlink from [REMOTE]`** (the radio's default), **NOT** `Link to [REMOTE]`.

    - `Unlink from [REMOTE]` — the `[USB]` and `[REMOTE]` CI-V ports work independently; the CI-V USB Baud Rate setting applies only in this mode
    - `Link to [REMOTE]` — the `[USB]` and `[REMOTE]` CI-V ports are connected internally

    The IC-7610 has the same setting.

### Recommended Radio Settings

| Setting | Value | Why |
|---------|-------|-----|
| **CI-V USB Port** | `Unlink from [REMOTE]` | ✅ Required — the USB baud rate setting applies only in this mode |
| **CI-V USB Baud Rate** | `115200` | Recommended for scope/waterfall |
| **CI-V Address** | `0x94` (IC-7300 default) | Library auto-detects from profile |

!!! note "Baud Rate"
    - `115200` baud is recommended for scope/waterfall capability
    - Lower baud rates (19200, 9600) work for basic control (freq, mode, PTT) but scope/waterfall is disabled by a guardrail due to high packet rate
    - CI-V baud rate IS significant on IC-7300 — it must match between radio and library

!!! info "IC-7300 Single Receiver"
    The IC-7300 has a single receiver (unlike IC-7610's dual receiver or IC-9700's dual). The library automatically enforces this via the IC-7300 profile — operations on `receiver=1` will fail with `CommandError`.

## macOS Setup

### 1. Install rigplane

```bash
pip install rigplane
```

### 2. Connect USB and Verify Devices

Plug in the USB cable and verify the serial port:

```bash
ls /dev/cu.usbserial-* | head -5
# Output example: /dev/cu.usbserial-A602RVAV
```

Check for audio devices exported by the radio:

```bash
rigplane --list-audio-devices
```

An Icom radio's USB audio device is named `USB Audio CODEC`.

### 3. Connect via Python

```python
import asyncio
from rigplane import SerialBackendConfig, create_radio

async def main():
    # Serial radio for IC-7300
    config = SerialBackendConfig(device="/dev/cu.usbserial-A602RVAV", model="IC-7300")

    async with create_radio(config) as radio:
        # Read frequency
        freq = await radio.get_freq()
        print(f"Frequency: {freq / 1e6:.6f} MHz")

        # Set frequency
        await radio.set_freq(7_074_000)

        # Read mode
        mode, _ = await radio.get_mode()
        print(f"Mode: {mode}")

        # Enable scope; frames arrive through radio.on_scope_data(callback)
        await radio.enable_scope()

asyncio.run(main())
```

### 4. CLI Usage

```bash
# Check connection
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV status

# Set frequency
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV freq 7074000

# Monitor metrics
rigplane --backend serial --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV meter
```

### 5. Web UI

```bash
rigplane --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV web
# Open http://localhost:8080
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
| **DSP** | ✅ Full | NB, NR, Twin Peak, PBT |
| **Dual Watch** | ⚠️ Single RX | IC-7300 has single receiver only |

## Audio Subsystem

IC-7300 exports a USB audio device that rigplane can use. For RX/TX code, see
[Audio Recipes](audio-recipes.md).

### WSJT-X Integration

On macOS, bridge rigplane audio to WSJT-X through the RigPlane Virtual Cable
(the RigPlane Virtual Audio Driver is installed by RigPlane Pro):

1. Start the audio bridge against the cable ends:
    ```bash
    rigplane --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV audio bridge --device "RigPlane Virtual Cable Output" --tx-device "RigPlane Virtual Cable Input"
    ```
2. **WSJT-X settings**:
    - Input Device: "RigPlane Virtual Cable Input"
    - Output Device: "RigPlane Virtual Cable Output"

## Troubleshooting

### Serial Port Not Found

**Solution**: Verify the USB cable connection and check the `/dev/cu.usbserial-*` listing.

### Low Baud Rate Warning

```
WARNING Scope disabled at low baud rate (9600 < 115200). Set allow_low_baud_scope=True to override or set ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1.
```

**Solution**: Set **CI-V USB Baud Rate** to `115200` in radio menu → Set → Connectors.

### Audio Devices Not Found

**Solution**: Check that the radio's `USB Audio CODEC` device is listed:
```bash
rigplane --list-audio-devices
```

### CI-V Commands Timing Out

**Solution**: Verify **CI-V USB Port** is set to `Unlink from [REMOTE]`, not `Link to [REMOTE]`.

## Performance Tuning

### Serial CI-V Pacing

If commands are arriving too fast, raise the minimum interval between CI-V
commands. The serial backend reads it when the radio object is created, so set
it before starting:

```bash
# 100 ms minimum between CI-V commands (Icom default: 25 ms)
export ICOM_SERIAL_CIV_MIN_INTERVAL_MS=100
rigplane --model IC-7300 --serial-port /dev/cu.usbserial-A602RVAV web
```

## Hardware Notes

- **Power**: the IC-7300 takes 13.8 V DC through its `[DC 13.8 V]` socket, not USB power
- **Cable length**: Keep under 3m to avoid signal integrity issues

## See Also

- [IC-7610 USB Setup](ic7610-usb-setup.md) — Dual-receiver configuration
- [IC-705 USB Setup](ic705-usb-setup.md) — Portable transceiver
- [Audio Recipes](audio-recipes.md) — RX/TX examples
- [WSJT-X Setup](wsjtx-setup.md) — Digital mode integration

## Get the Packaged Desktop App

!!! tip "Prefer a packaged desktop app?"
    This guide covers the open-source `rigplane` Python library. If you want
    a polished desktop application with a GUI for IC-7300 control on macOS
    and Linux, check out RigPlane Pro.

    [Download RigPlane Pro for Mac and Linux →](https://rigplane.com/downloads/?utm_source=docs.rigplane.dev&utm_medium=cta&utm_campaign=ic7300-usb-setup)
