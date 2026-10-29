# 3.0 known limitations

This file is the list of operator-visible limitations RigPlane 3.0 ships with.

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

## FTX-1 specific

- **Manual-notch position on the FTX-1 is shown as a position, not in Hz.**
  The radio's own display does the same; the CAT code range is bounded to
  001..320.
- **Attenuator and preamp cannot be set for the SUB receiver.** The FTX-1 CAT
  `RA`/`PA` commands have no SUB form; the controls are shown disabled with
  the hint "Not available on this receiver" while SUB is active.

## IC-7300 specific

- **IC-7300: 4 m (70 MHz) can be tuned and received, transmit on 4 m is
  not offered.**
- **IC-7300 has no APF.** The radio's manual lists no command for it, so a
  profile advertising CW APF for the IC-7300 would expose a nonfunctional
  control.

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
  change; it discovers the real value on its next read.
- **For up to about a second after an unkey, the seat may still read the
  radio as transmitting and drop a mode/VFO/split write the same way.**
- **Frequency and RIT/XIT are exempt from both limitations above.** These writes are accepted
  while the radio is keyed, so the two families are classified TX-SAFE and are always
  applied, in any RF state, including the up-to-a-second post-unkey
  window. This also resolves the case previously recorded here of a
  WSJT-X "Fake It" split-mode dial restore issued right after unkey being
  swallowed: that restore is a frequency write and is no longer subject to
  any drop or refusal.

## rigctld raw CI-V (`w`) timeouts

- **A raw `w` frame the radio never answers is now reported as
  `RPRT -5` (timeout) instead of an empty success.** Nothing is known about
  whether such a frame was applied, so reporting success was a lie. Clients
  that treated the old empty success as "sent OK" will now see an error on
  frames that time out. `w` is a raw CI-V escape hatch for diagnostics and
  advanced tooling, not part of the frequency/mode/PTT command flow a
  logging or digital-mode client uses for normal operation.

## Dual-receiver topology

**Dual-receiver operation is covered by automated profile fixtures and
fail-closed tests.** This includes the IC-7610's dual-watch, dual-scope and
simultaneous MAIN/SUB audio-routing paths. Expect rough edges there and
report them. On the FTX-1, the SUB receiver supports single-receive
operation; see the FTX-1 items above for the controls that exist for MAIN
only.

## Command acknowledgement

- **A write the radio refuses can still answer `ok` to the caller.** The
  refusal shows only in the server log.

## Web UI

- **The LCD skin's VFO control panel shows the A↔B and A=B keys without
  checking whether the radio's VFO scheme supports them.**
