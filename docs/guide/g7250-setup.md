---
description: Connect a Xiegu G7250 to RigPlane over USB or, with firmware V1.0.1 or later, over the network in the radio's wfview mode.
---

# G7250 USB and Network Setup

RigPlane drives the Xiegu G7250 over CI-V. Over USB the serial port works
and RX polling is clean. TX, PTT and TX audio have not
been verified on this radio.

## USB

### What You Need

- A USB cable to the radio. The radio shows a serial port (CI-V) and a USB
  audio device.
- RigPlane itself: see [Installation](installation.md).

### Connect

CI-V runs at 115200 baud; the radio did not answer at 19200. Find the port:

```bash
rigplane discover --serial-only
```

Then read the radio:

```bash
rigplane --backend serial --model G7250 --serial-port /dev/cu.usbserial-XXXX \
  --serial-baud 115200 status
```

Replace the port with the one `discover` lists. The radio answers any CI-V
address over USB. RX audio comes from the radio's USB audio device.

A USB-connected G7250 gets the audio-derived panorama only: the radio does not
answer the CI-V scope commands on the serial port. With `--model G7250`, always
pass `--serial-port` for USB; without `--serial-port` or `--host` RigPlane
searches the network.

## Network (wfview mode)

### What You Need

- Radio firmware **V1.0.1 or later**.
- The radio and your computer on the same network.

### On the Radio

The radio's network menu has two modes, and only one can be active at a time:

- **wfview**: Icom LAN control. This is the mode RigPlane uses.
- **HDSDR**: a proprietary IQ stream over UDP. In this mode the radio refuses
  Icom LAN control.

1. Set the network mode to **wfview**. Check it again after every radio
   restart: the radio can come back in HDSDR mode, or switch to it on its
   own. The radio does not report its mode over CI-V, so if the connection
   fails, check the mode first.
2. Use the network login set in the same menu. RigPlane signs in with the
   radio's own user name and password.

### Connect

```bash
rigplane --model G7250 --backend lan --host RADIO_IP --user RADIO_USER \
  --pass-file ~/.radio-pass status
```

`--pass-file` reads the password from the first line of a file; the
`ICOM_PASS` environment variable also works. The radio uses UDP ports 50001
(control), 50002 (CI-V) and 50003 (audio); allow them through any firewall
between you and the radio.

- Pass `--model G7250` yourself. With several radios on the network, give the
  radio's address with `--host`.
- Over the network only CI-V address `0xA0` answers; the G7250 profile uses
  it.
- Frequency, mode and S-meter polling works over the network.
- Do not use USB and network control of the same radio at the same time.
- Panorama (scope and waterfall) over the network comes from the radio's own
  scope. Span presets: 48, 96, 192 and 384 kHz.
- TX and TX audio over the network are not verified.

## Not Exposed

The 1D 17, 1D 18 and 1D 20 controls and the firmware readout are not exposed
by RigPlane.

## Firmware

Xiegu G7250 support was verified on firmware V1.0.1, the first firmware
with network control. Some functions of this firmware are incomplete or
behave differently over USB and over the network; RigPlane can only
control what the radio itself implements. For the radio's own functions
and settings, refer to the manufacturer's documentation and firmware
release notes. RigPlane's support will be extended as the firmware
matures.

See [Supported Radios](radios.md#xiegu-g7250) for what has been verified.
