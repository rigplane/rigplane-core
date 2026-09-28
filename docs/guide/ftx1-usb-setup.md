---
description: Connect a Yaesu FTX-1 to RigPlane over USB — the CAT port and rate, radio menu settings for USB audio, and the CLI, Web UI and Python entry points.
---

# FTX-1 USB Setup

RigPlane drives the Yaesu FTX-1 over its USB jack with the `yaesu-cat`
backend, which speaks Yaesu's text CAT protocol.

## What You Need

- A USB cable with a type-C plug for the radio. The FTX-1 has a built-in USB
  to dual-UART bridge, so no interface box is needed.
- On a PC, the Virtual COM port driver: Yaesu's CAT manual says to install it
  from the Yaesu website before connecting.
- RigPlane itself: see [Installation](installation.md).

## Serial Ports and CAT Rate

Over USB the FTX-1 shows two serial ports:

| Port | Name in the CAT manual | Use |
|------|------------------------|-----|
| Enhanced COM Port | CAT-1 | CAT commands (frequency, mode and the rest). Use this port with RigPlane. |
| Standard COM Port | CAT-2 | TX control (PTT, CW keying, digital modes) or CAT |

CAT-1 runs at 38400 bps from the factory (**OPERATION SETTING → GENERAL →
CAT-1 RATE**, 4800 to 115200 bps). The `yaesu-cat` backend also defaults to
38400; if you change CAT-1 RATE, pass the same rate with `--serial-baud`.

To find the port, run:

```bash
rigplane discover --serial-only
```

It lists the port where the FTX-1 answered.

## Radio Settings for USB Audio

When no audio device is named, RigPlane first looks for the audio device on
the same USB connection as the serial port, then falls back to picking one by
name. To choose devices yourself, pass `--rx-device` and `--tx-device` with
names from `rigplane --list-audio-devices`.

To transmit audio from the computer (Web UI voice or digital modes), set
**MOD SOURCE** to **USB** in the **RADIO SETTING** menu for each mode group
you transmit in (MODE SSB, MODE AM, MODE FM, MODE DATA and so on). The
choices are MIC, USB, Bluetooth and AUTO.

## CLI

Name the backend: without `--backend yaesu-cat`, `--serial-port` selects the
Icom serial backend.

```bash
# Status
rigplane --backend yaesu-cat --serial-port /dev/cu.usbserial-XXXX status

# Set frequency and mode
rigplane --backend yaesu-cat --serial-port /dev/cu.usbserial-XXXX freq 14.074m
rigplane --backend yaesu-cat --serial-port /dev/cu.usbserial-XXXX mode USB
```

## Web UI

```bash
rigplane --backend yaesu-cat --serial-port /dev/cu.usbserial-XXXX web
# Then open http://localhost:8080
```

RigPlane has no hardware spectrum scope for the FTX-1. The Web UI draws its
panadapter from the received audio instead.

## Python

```python
import asyncio
from rigplane import create_radio, YaesuCatBackendConfig

async def main():
    config = YaesuCatBackendConfig(device="/dev/cu.usbserial-XXXX")
    async with create_radio(config) as radio:
        freq = await radio.get_freq()
        mode, _ = await radio.get_mode()
        print(f"{freq/1e6:.3f} MHz  {mode}")

asyncio.run(main())
```

`YaesuCatBackendConfig` also takes `baudrate` (default 38400), `rx_device`,
`tx_device` and `audio_sample_rate`.

## Two Receivers

The FTX-1 profile has two receivers that share one VFO (VFO scheme
`ab_shared`). Some controls exist for MAIN only; see the FTX-1 items in the
[beta known limitations](../release-notes/2026-beta-known-limitations.md).

## See Also

- [Supported Radios](radios.md)
- [CLI Reference](cli.md)
- [Web UI](web-ui.md)
- [Troubleshooting](troubleshooting.md)
