# 2026 beta — known limitations

This file is the single authoritative list of operator-visible limitations the
2026 beta ships with. Every line traces to an explicit owner decision on the
MOR-1410 hardware-conformance gate (scope freeze of 2026-08-16: bucket B =
release-note scope) or to a standalone owner ruling cited inline.

**Process rule (owner decision, 2026-08-17):** a ticket may move to bucket B
(release-note scope) only together with its line in this file. A bucket-B
reclassification without a corresponding line here is incomplete.

None of the items below produces false transmit state or routes RF incorrectly;
that class of defect is release-blocking by definition and is not on this list.

## Browser microphone capture

- **Browser voice TX requires a secure browser context.** `getUserMedia()` is
  available to the Web UI only over browser-trusted HTTPS or a local loopback
  origin such as `localhost`; a private-LAN HTTP URL is not a loopback origin.
  Use Core TLS setup with `--tls-cert` and `--tls-key` together. Use a certificate
  valid and trusted for the server hostname. The `--tls` option alone generates
  self-signed TLS and does not establish browser certificate trust. Open the
  resulting `https://` URL. Alternatively, access a loopback endpoint through
  an SSH tunnel. This limitation concerns microphone capture and voice TX; it
  does not make all control or RX operations unavailable over HTTP.

## Receive-control feedback and precision

- **PBT values across acquisition gaps: the last confirmed value is kept in
  software, but the owner-present IC-7300 observation is still pending.**
  (MOR-1692)
- **Combined RF/SQL control does not track the gesture locally.** Values update
  only after canonical radio readback (~1 s), which makes precise placement
  awkward; the SDR-screen skin's rendering of the same control tracks
  correctly. (MOR-1693)
- **Filter Shape and grouped Notch choices show pending feedback in software,
  but the owner-present IC-7300 rerun is still pending.** (MOR-1689)
- **IC-7300 has no APF.** Advanced Manual (11a) omits `16 32`, and a
  read-only remote-testbed GET NAK'd twice with documented `16 22` DATA as
  the adjacent control. Any build or profile advertising CW APF for IC-7300
  exposes a nonfunctional control. (MOR-2144)

## FTX-1 specific

- **Manual-notch position on the FTX-1 is shown as a position, not in Hz.**
  The radio's own display does the same; the CAT code range is bounded to
  001..320 since #3480/#3526. (MOR-1680, owner ruling 2026-09-17)
- **IF Shift, CW Pitch and NR level on the FTX-1 follow the CAT lattices in
  software (20 Hz, 10 Hz to 1050 Hz, 0–10) but have not been re-run on the
  radio since the change.** (MOR-1681, MOR-1682, MOR-1678 — hardware rerun
  pending)
- **Attenuator and preamp cannot be set for the SUB receiver.** The FTX-1 CAT
  `RA`/`PA` commands have no SUB form; the controls are shown disabled with
  the hint "Not available on this receiver" while SUB is active. (MOR-2511,
  owner ruling 2026-09-17)

## rigctld write handling during transmit

- **While the radio is known to be transmitting, rigctld silently drops
  state-changing writes in the mode, VFO, and split families and answers
  `RPRT 0` (success) instead of an error.** This deliberately mirrors
  hamlib's own core, which returns success without writing while PTT is
  on: a non-zero return makes WSJT-X treat the situation as a hard
  rig-control failure — it de-keys, stops the auto-sequencer, and on a
  repeated error shows a modal dialog. Under unknown or stale RF state the
  seat still refuses truthfully with an error; the drop applies only to
  known transmit. A third-party client that writes while the radio is
  transmitting is told the write succeeded even though the radio did not
  change; it discovers the real value on its next read. (MOR-1881)
- **For up to about a second after an unkey, the seat may still read the
  radio as transmitting and drop a mode/VFO/split write the same way.**
  (MOR-1881; owner ruling 2026-09-17 on MOR-1892: not to be narrowed further
  for the beta)
- **Frequency and RIT/XIT are exempt from both limitations above.** Bench
  measurement showed both radios accept and apply these writes while
  keyed, so the two families are classified TX-SAFE and are always
  applied, in any RF state, including the up-to-a-second post-unkey
  window. This also resolves the case previously recorded here of a
  WSJT-X "Fake It" split-mode dial restore issued right after unkey being
  swallowed: that restore is a frequency write and is no longer subject to
  any drop or refusal. (MOR-1940)

## rigctld raw CI-V (`w`) timeouts

- **A raw `w` frame the radio never answers is now reported as
  `RPRT -5` (timeout) instead of an empty success.** Nothing is known about
  whether such a frame was applied, so reporting success was a lie. Clients
  that treated the old empty success as "sent OK" will now see an error on
  frames that time out. `w` is a raw CI-V escape hatch for diagnostics and
  advanced tooling, not part of the frequency/mode/PTT command flow a
  logging or digital-mode client uses for normal operation. (MOR-1882)

## Dual-receiver topology

**Dual-receiver hardware certification is not part of this beta.** The bench
holds an IC-7610 (returned 2026-09-14) and an FTX-1; the IC-7610's dual-watch,
dual-scope and simultaneous MAIN/SUB audio-routing paths were not re-run for
this beta and remain covered by automated profile fixtures and fail-closed
tests only. What was accepted on hardware: single-receive SUB operation on the
FTX-1 — frequency, mode, width, S-meter, AF/RF/squelch, repeater shift, NB/NR,
notch, IF shift, NARROW and AGC read from the SUB receiver, and AGC and NB
writes measured landing on SUB with MAIN unchanged (MOR-2511, 2026-09-18). Do
not treat any other dual-receiver path as hardware-certified.
