---
description: Point WSJT-X, JTDX, and other TCI clients at RigPlane's TCI endpoint for frequency, mode, split, and PTT (control phase; audio over TCI is a later phase).
---

# TCI Setup Guide (Control Phase)

TCI (Transceiver Control Interface, the Expert Electronics protocol many
digimode programs speak) drives the radio over the local network instead
of a virtual serial port: frequency, mode, split, and PTT travel as
short text lines over one connection per client. RigPlane's TCI endpoint
speaks the control half of that protocol. Sound is the other half and is
not there yet: until the audio phase ships, WSJT-X audio still flows
through the audio bridge while CAT and PTT go over TCI.

No live session with a real end-user client has been recorded yet: the
C0 capture sessions in `docs/validation/tci-capture-c0.md` are still to
run, so the per-client steps below are not verified against any released
application. If a client waits for a line this server does not send, the
server logs the unknown command name once per connection at INFO — that
log line is the fastest way to report the gap.

## 1. Start the server

```bash
rigplane --model IC-7610 --host <RADIO_IP> web --tci
```

This starts the Web UI on `:8080`, `rigctld` on `:4532` (enabled by
default) and TCI on `:50001`. Relocate the TCI port with
`--tci-port N`: AetherSDR already listens on `50001` by default, so
running both on one machine needs one of them moved. An already-used
port aborts startup. TCI binds loopback (`127.0.0.1`) unless
`--ingress-host` names another address — one bind-host setting shared
with the `rigctld` listener; a non-loopback address logs a WARNING on
start, and TCI has no authentication, so it should stay on loopback.
TCI is off unless `--tci` is passed (with `--no-tci` to turn it back
off), and `serve` has no TCI support. `station` accepts the same flags
as `web`.

Check the banner: it lists the `tci:` address next to `rigctld:` when
the listener started.

## 2. Configure the client

Point the client at the TCI host and port (`127.0.0.1:50001` by
default) and select the TCI rig type where the client offers one.
Verified against a live client application: none yet — WSJT-X
(official and Improved), JTDX, OpsLog, and MSHV are C0 capture targets
but no recording session has been run for any of them, so every
per-client step is still "not yet verified".

What was exercised instead, in tests: the `eesdr-tci` 0.1.0 Python
client library reads the init burst, sets a frequency and reads it
back, programs DIGU, and keys and unkeys through the managed TX path,
all through the real `web` wiring against a simulator-backed radio (no
real TX). The repo also holds two example client-to-server command
streams (`tests/fixtures/tci/wsjtx_improved_qso.tci`,
`tests/fixtures/tci/jtdx_qso.tci`) in the capture-extraction format;
no test replays them against the server.

On connect the server sends one deterministic init burst ending with
`ready;` and the client seeds its state from it. Split uses channel 1
(`vfo:0,1`, VFO B) as the split TX frequency while split is on. Every
write the client sends is answered: an accepted set echoes to every
session, while a no-op or a refused write answers the actual state to
the requester only — a refused PTT arrives as `trx:0,false;`.

## 3. Known limits of this phase

- `tune` is answered `tune:0,false;` and does nothing; `tune_drive`
  follows `drive` on reads and ignores sets.
- `audio_start` (and any other Tier C or unknown command) is ignored
  without a reply; binary frames are ignored until the audio phase
  ships. If the client expects RX/TX audio frames on this socket, that
  is the audio phase: not offered yet.
- Keying with an explicit audio source (`trx:0,true,<source>`) is
  refused; a bare `trx:0,true` keys the radio's current input.
- Without the managed TX composition the server announces
  `receive_only:true` and refuses every key.
- A TX over driven by a TCI client leaves the TX input where it is;
  the server does not restore it afterwards.

## 4. Troubleshooting

- **Port in use.** A busy TCI port aborts startup before anything
  binds; the usual squatters are AetherSDR (`50001` by default) and an
  orphaned rigplane instance. Run `lsof -i :<port>` to find it, or
  relocate with `--tci-port`.
- **Client on another machine cannot reach TCI.** TCI stays on
  loopback unless `--ingress-host` names another address. A
  non-loopback address logs a WARNING on start.
- **Client waits forever for audio or a missing line.** Look for
  `ignoring Tier C/unknown command <name>` at INFO, once per
  connection: the name tells what the client asked for. `audio_start`
  there means the client wants the audio phase.
- **Logs.** `web` and `station` log to `rigplane.log` in the `logs`
  folder of the per-user cache directory
  (`~/Library/Caches/rigplane/logs/` on macOS,
  `~/.cache/rigplane/logs/` on Linux). `ICOM_LOG_FILE` overrides the
  file, `RIGPLANE_LOG_DIR` replaces the folder.
