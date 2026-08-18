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

## Receive-control feedback and precision

- **PBT values can briefly blank or show a transient endpoint during
  acquisition gaps.** The radio state is unaffected; the last confirmed values
  return without intervention. (MOR-1692)
- **No PBT Reset action.** Restoring both passband controls to neutral requires
  setting each slider manually. (MOR-1690)
- **Manual Notch Width renders as a slider although the radio accepts only
  three values** (WIDE/MID/NAR). Positions between detents are quantized; the
  affordance suggests more precision than exists. (MOR-1685)
- **Combined RF/SQL control does not track the gesture locally.** Values update
  only after canonical radio readback (~1 s), which makes precise placement
  awkward; the SDR-screen skin's rendering of the same control tracks
  correctly. (MOR-1693)
- **PBT Inner/Outer lack gesture-local draft and pending feedback.** Numbers
  follow delayed readback rather than the thumb. (MOR-1691)
- **Filter Shape and grouped Notch choices give no pending/accepted
  feedback.** The commands themselves dispatch and confirm correctly.
  (MOR-1689)
- **Notch mode choices (OFF/AUTO/MANUAL) show no pending state** while a
  change is in flight. (MOR-1672)
- **AF/RF/SQL slider steps do not always restore the exact original raw
  value** after a reversible up/down step pair; drift is within one raw step.
  (MOR-1676)
- **The CW APF toggle is not connected to the IC-7300's observable
  audio-peak-filter field**; its rendered state may not reflect the radio.
  (MOR-1647)
- **A power-state control is offered on radios whose power cannot be switched
  over CAT**; it presents a readiness the radio does not have, and unknown
  power state is not always rendered neutrally. (MOR-1673)

## FTX-1 specific

- **NR level does not honor the radio's native 0–10 domain**; the UI exposes
  a finer projected scale whose consecutive steps can map to the same radio
  value. (MOR-1678)
- **Filter-width (SH) codes and mode routing do not follow CAT 2508-C Table 5
  in every mode**; the wrong width-table family can be offered for some modes.
  (MOR-1679)
- **Manual-notch position does not use the documented CAT 001..320 code
  range**; endpoint positions are unreachable and the excluded code 000 can be
  emitted. (MOR-1680)
- **IF Shift does not use the official 20 Hz lattice**; requested values are
  quantized by the radio. (MOR-1681)
- **CW Pitch is exposed as 300–900 Hz in 5 Hz steps although the radio
  supports 300–1050 Hz in exact 10 Hz steps**; pitch above 900 Hz is
  unreachable from the UI, and off-lattice requests are floored by the
  software before reaching the radio. (MOR-1682)
- **The band picker omits 6 m, 2 m, and 70 cm** although the radio supports
  them; use the frequency entry or the radio's own controls for those bands.
  (MOR-1674)

## rigctld write handling during transmit

- **While the radio is known to be transmitting, rigctld silently drops
  state-changing writes in the frequency, mode, VFO, split, and RIT/XIT
  families and answers `RPRT 0` (success) instead of an error.** This
  deliberately mirrors hamlib's own core, which returns success without
  writing while PTT is on: a non-zero return makes WSJT-X treat the
  situation as a hard rig-control failure — it de-keys, stops the
  auto-sequencer, and on a repeated error shows a modal dialog. Under
  unknown or stale RF state the seat still refuses truthfully with an
  error; the drop applies only to known transmit. A third-party client
  that writes while the radio is transmitting is told the write succeeded
  even though the radio did not change; it discovers the real value on its
  next read. (MOR-1881)
- **For up to about a second after an unkey, the seat may still read the
  radio as transmitting and drop a write the same way.** A WSJT-X "Fake It"
  split-mode dial restore issued right after unkey can be swallowed this
  way, leaving the rig on the transmit-shifted dial frequency. (MOR-1892)

## rigctld raw CI-V (`w`) timeouts

- **A raw `w` frame the radio never answers is now reported as
  `RPRT -5` (timeout) instead of an empty success.** Nothing is known about
  whether such a frame was applied, so reporting success was a lie. Clients
  that treated the old empty success as "sent OK" will now see an error on
  frames that time out. `w` is a raw CI-V escape hatch for diagnostics and
  advanced tooling, not part of the frequency/mode/PTT command flow a
  logging or digital-mode client uses for normal operation. (MOR-1882)

## Dual-receiver topology (permanent limitation)

**Dual-receiver hardware certification is permanently out of scope for this
product line.** The dual-RX bench hardware (IC-7610, X6200) was destroyed and
no replacement is planned (owner ruling on MOR-1568, 2026-08-17). This beta —
and future releases until stated otherwise — does not include representative
dual-receiver hardware certification. IC-7300 and FTX-1 acceptance evidence
covers only the controls and receiver behaviors those radios actually expose.
Positive dual-watch, dual-scope, simultaneous MAIN/SUB audio/routing, and
related dual-receiver command/readback paths (including the dual-receiver
cockpit skin) are covered by automated profile fixtures and fail-closed tests,
not by real dual-receiver hardware. Do not treat those paths as
hardware-certified.
