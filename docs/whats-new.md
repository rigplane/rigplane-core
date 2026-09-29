---
description: What changes for an operator in RigPlane 3.0 (beta) — the radio model is required, readings show only what the radio reported, clearer transmit feedback, faster LAN recovery, and more radio controls.
---

# What's New in 3.0

RigPlane 3.0 is in beta. This page lists the changes an operator notices.
Code written for 2.x is covered by the [migration guide](migrate.md), every
change is in the [changelog](CHANGELOG.md), and the known gaps of this beta
are in [2026 beta known limitations](release-notes/2026-beta-known-limitations.md).

## Name Your Radio

Every command that talks to a radio must say which radio it is: `--model
IC-7300` on the command line, `model="IC-7300"` in Python. On the LAN backend a
`--radio-addr` (or `radio_addr=`) that matches a known radio also works; the
serial backend needs the model. RigPlane no longer guesses; 2.11 fell back to
the IC-7610 profile. The FTX-1 on `--backend yaesu-cat` and an external rigctld
on `--backend rigctld` run without it. See
[The radio model is required](migrate.md#the-radio-model-is-required).

## Readings Show Only What the Radio Reported

- A reading the radio has not reported yet stays empty. The Web UI does not
  fill it with "?", dashes, "unknown" or a made-up default, and the empty slot
  keeps its size, so nothing moves when the first reading arrives.
- NARROW, Filter Shape (SHARP/SOFT), the notch mode choices and the Manual
  Notch Width choices mark your change as pending until the radio confirms
  it. What is lit is the radio's confirmed setting.

## Transmit

- A transmit state that is not confirmed shows "TX" with a hollow lamp; a
  confirmed one is filled.
- When a control is refused because the radio is transmitting, or because its
  transmit state is not confirmed, the control you touched says so.

## LAN Radios

- A dropped LAN session comes back as soon as the radio reports the session
  free, instead of after a watchdog wait (checked on the IC-7610).
- A refused IC-7610 login says what the radio's error code means: another
  session from this computer, or a client on another computer.

## More of Each Radio's Controls

- Repeater controls (tone, tone squelch and the CTCSS tone) appear in a side
  panel while a receiver is tuned to a band the radio's profile marks as a
  repeater band: 2 m and 70 cm on the FTX-1 and IC-705; 2 m, 70 cm and 23 cm
  on the IC-9700.
- NARROW on the FTX-1.
- Manual Notch Width is a WIDE / MID / NAR choice on the IC-7300, IC-7610,
  IC-705 and IC-9700.
- The FTX-1's SUB receiver: which SUB controls were checked on the radio is
  listed under "Dual-receiver topology" in the
  [known limitations](release-notes/2026-beta-known-limitations.md).

## Web UI Skins

The Web UI's radio logic is shared, and a skin only decides how things look.
With a source checkout you can build your own skin without changing the radio
logic.

## Command Line

`web`, `serve` and `station` take `--listen` for the address the server
listens on. `--host` after these commands still works but prints a
deprecation warning; if both are given, `--listen` wins.

## For Library Users

On the `lan`, `serial` and `yaesu-cat` backends, each radio's commands and
value ranges come from its profile file under `rigs/`
([Rig Profiles](guide/rig-profiles.md)). The
[migration guide](migrate.md) lists the API changes from 2.x.
