---
description: Connect a Xiegu G7250 to RigPlane over USB or, with firmware V1.0.1 or later, over the network in the radio's wfview mode.
---

# G7250 USB and Network Setup

RigPlane drives the Xiegu G7250 over CI-V. Facts here come from the
Xiegu G7250 CI-V Reference Rev 1.2 and from tests on a real radio. TX, PTT
and TX audio have not been verified on this radio.

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

There is no panorama over USB: the radio does not answer the CI-V scope
commands on the serial port.

## Network (wfview mode)

<!-- PENDING: L1+L2+L3 — this whole section waits for the network work to merge -->

### What You Need

- Radio firmware **V1.0.1 or later**.
- The radio and your computer on the same network.

### On the Radio

1. In the radio's network menu, set the network mode to **wfview**. The other
   mode, **HDSDR**, is a raw IQ stream and is not used for control. The
   radio offers one mode at a time. The radio does not report the mode over
   CI-V, so if the connection fails, check it first. Whether a mode change
   needs a power cycle is not verified.
2. Set a user name and password in the same menu.

### Connect

```bash
rigplane --model G7250 --backend lan --host RADIO_IP --user RADIO_USER \
  --pass-file ~/.radio-pass status
```

`--pass-file` reads the password from the first line of a file; the
`ICOM_PASS` environment variable also works. The radio uses UDP ports 50001
(control), 50002 (CI-V) and 50003 (audio); allow them through any firewall
between you and the radio.

- Network discovery finds the radio but does not name the model. Pass
  `--model G7250` yourself. <!-- PENDING: L3 -->
- Over the network only CI-V address `0xA0` answers; the G7250 profile uses
  it. <!-- PENDING: L3 -->
- Panorama over the network: the radio sends scope data in wfview mode
  (spans 48, 96, 192 and 384 kHz). RigPlane does not show it yet.
  <!-- PENDING: scope-PR (alt: "Panorama works over the network.") -->
- TX and TX audio over the network are not verified.
  <!-- PENDING: TX-check (alt: verified result) -->

## Limits

- AGC: the radio has Off, Fast, Medium, Slow and Auto, but only Off, Fast and
  Slow can be set by CI-V command in V1.0.1. RigPlane offers those three.
- Controls the radio only stores without acting on are not offered.

See [Supported Radios](radios.md#xiegu-g7250) for the full list.
