---
description: Configure WSJT-X, JTDX, and OpsLog against RigPlane's TCI endpoint for FT8 and FT4 (control plus audio over one socket).
---

# TCI Setup Guide (Control and Audio)

This guide covers configuring data-mode clients against RigPlane's
client-facing TCI endpoint (frequency, mode, split, PTT and RX/TX
audio). It is separate from the `rigctld`-compatible endpoint covered
in the [WSJT-X / JTDX / JS8Call guide](wsjtx-setup.md). Audio over TCI
needs no virtual audio cable: RX audio arrives as mono frames on the
same socket after `audio_start:0`, and TX audio goes out after
`trx:0,true,tci`.

Client behaviour in this page is reported, not captured: no
client-recording session has been done yet, so the steps below follow
the TCI spec and second-hand client notes. If a client waits for a line
this server does not send, the server logs the unknown command name
once per connection at INFO — that log line is the fastest way to
report the gap.

## 1. Start the server

```bash
rigplane --model IC-7610 --host <RADIO_IP> web --tci
```

This starts the Web UI on `:8080`, `rigctld` on `:4532` (enabled by
default) and TCI on `:50001`. Relocate the TCI port with
`--tci-port N`: AetherSDR already listens on `50001` by default, so
running both on one machine needs one of them moved. TCI binds
loopback unless `--ingress-host` names another address; a non-loopback
address logs a WARNING on start. TCI is off unless `--tci` is passed,
and `serve` has no TCI support.

Check the banner: it lists the `tci:` address next to `rigctld:` when
the listener started.

## 2. Configure the client

Point the client at the TCI host and port (`127.0.0.1:50001` by
default) and select the TCI rig type where the client offers one:

- **WSJT-X 3.0.0 and newer (official), WSJT-X Improved 3.1.0, JTDX
  2.2.159**: select TCI as the rig, enter the host and port, enable the
  connection. Split operation ("Fake It" / "Rig") programs channel 1
  (`vfo:0,1`) the same way it does over `rigctld`.
- **OpsLog (TCI backend)**: enter the host and port (OpsLog defaults to
  `40001`; use `--tci-port` on this side or OpsLog's own port field so
  the two match). OpsLog asks for real split rather than "Fake It".

On connect the client waits for the init burst up to `ready;` and seeds
its state from it; every write the client sends is answered, and a
refused PTT arrives as `trx:0,false;`.

## 3. Known limits

- `tune` is answered `tune:0,false;` and does nothing; `tune_drive`
  follows `drive`.
- `trx:0,true,tci` keys with TX audio only when the radio has a
  `PCM_1CH_16BIT` TX route; any other explicit audio source
  (`trx:0,true,<source>`) is refused. A bare `trx:0,true` keys the
  radio's current input.
- Without the managed TX composition the server announces
  `receive_only:true` and refuses every key.
- A TX over driven by a TCI client leaves the TX input where it is;
  the server does not restore it afterwards.
- RX is served as real mono in every mode; a client's request for two
  channels is answered with the value in force (`1`).
- Real-client TX behaviour is not captured yet (no C0-TX session has
  run against this socket): keying a transmitter from a client is
  bench-only until that evidence lands, and this phase stays HOLD for
  merge meanwhile.
