---
description: Connect a Yaesu FT-991A to RigPlane over USB — the CAT port and rate, radio menu settings, and the CLI, Web UI and Python entry points.
---

# FT-991A USB Setup

RigPlane drives the Yaesu FT-991A over its USB jack with the `yaesu-cat`
backend, which speaks Yaesu's text CAT protocol.

## What You Need

- A USB cable for the radio. The FT-991A has a built-in USB to dual-UART
  bridge, so no interface box is needed.
- On a PC, the Virtual COM port driver: the CAT manual says to install it
  from the Yaesu website before connecting.
- RigPlane itself: see [Installation](installation.md).

## Serial Ports and CAT Rate

Over USB the FT-991A shows two serial ports (seen on one radio with the
Silicon Labs CP210x bridge, VID:PID `10C4:EA70`):

| Port | Use |
|------|-----|
| Enhanced COM Port | CAT (frequency, mode and the rest). RigPlane uses this port. |
| Standard COM Port | PTT, CW keying and digital-mode transmit control (RTS/DTR) for other programs. RigPlane does not use it in this version. |

COM port numbers are host-specific; they are never the same twice. To find
the port, run:

```bash
rigplane discover --serial-only
```

It lists the port where the FT-991A answered (`ID0670;`).

Set the radio menus (menu numbers from the CAT manual, EX table):

- **031 CAT RATE** — 4800 / 9600 / 19200 / 38400 bps. The `yaesu-cat`
  backend defaults to 38400; if you change CAT RATE, pass the same rate
  with `--serial-baud`.
- **032 CAT TOT** — CAT timeout: 10 / 100 / 1000 / 3000 ms. The default
  is fine.
- **033 CAT RTS** — hardware flow control: disabled or enabled.
  RigPlane has not yet been verified with CAT RTS enabled or disabled.

## Radio Settings for USB Audio

When no audio device is named, RigPlane first looks for the audio device on
the same USB connection as the serial port, then falls back to picking one by
name. To choose devices yourself, pass `--rx-device` and `--tx-device` with
names from `rigplane --list-audio-devices`.

## CLI

`--model FT-991A` selects the Yaesu CAT backend with the FT-991A profile.
`--backend yaesu-cat` alone keeps the FTX-1 default profile; pair it with
`--model FT-991A` to drive this radio.

```bash
# Status
rigplane --model FT-991A --serial-port /dev/cu.usbserial-XXXX status

# Set frequency and mode
rigplane --model FT-991A --serial-port /dev/cu.usbserial-XXXX freq 14.074m
rigplane --model FT-991A --serial-port /dev/cu.usbserial-XXXX mode USB
```

## Web UI

```bash
rigplane --model FT-991A --serial-port /dev/cu.usbserial-XXXX web
# Then open http://localhost:8080
```

RigPlane has no hardware scope for the FT-991A (the CAT manual documents no
spectrum data). The Web UI draws its panadapter from the received audio
instead.

## Python

```python
import asyncio
from rigplane import create_radio, YaesuCatBackendConfig

async def main():
    config = YaesuCatBackendConfig(
        device="/dev/cu.usbserial-XXXX", model="FT-991A"
    )
    async with create_radio(config) as radio:
        freq = await radio.get_freq()
        mode, _ = await radio.get_mode()
        print(f"{freq/1e6:.3f} MHz  {mode}")

asyncio.run(main())
```

`YaesuCatBackendConfig` also takes `baudrate` (default 38400), `rx_device`,
`tx_device` and `audio_sample_rate`.

## Single Receiver

The FT-991A has one receiver with VFO A and VFO B (VFO scheme `ab`);
split uses the two VFOs.

## What This Version Does

Radio control (frequency, mode, filters, VFO A/B, split, meters), audio,
and digital modes through the radio's USB audio device work. Not yet
available: transmit from the web UI, antenna tuner, noise blanker/noise
reduction switches, clarifier, memory channels, squelch, contour.

The profile was built from the manufacturer's CAT manual and is being
verified on real radios. If something misbehaves, see
[Troubleshooting](troubleshooting.md).

## See Also

- [Supported Radios](radios.md)
- [CLI Reference](cli.md)
- [Web UI](web-ui.md)
- [Troubleshooting](troubleshooting.md)
