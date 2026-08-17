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

## Receive-control feedback and precision (both radios)

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
  over CAT**; the affordance is inert there, and unknown power state is not
  always rendered neutrally. (MOR-1673)

## FTX-1 specific

- **NR level uses a scaled 0–100 projection instead of the radio's native
  0–10 domain**; consecutive UI steps can map to the same radio value.
  (MOR-1678)
- **Filter-width (SH) codes and mode routing do not follow CAT 2508-C Table 5
  in every mode**; the wrong width-table family can be offered for some modes.
  (MOR-1679)
- **Manual-notch position does not use the documented CAT 001..320 code
  range**; endpoint positions are unreachable. (MOR-1680)
- **IF Shift does not use the official 20 Hz lattice**; requested values are
  quantized by the radio. (MOR-1681)
- **CW Pitch does not expose the exact 300..1050 Hz / 10 Hz lattice**;
  intermediate values are quantized by the radio. (MOR-1682)
- **The band picker omits 6 m, 2 m, and 70 cm** although the radio supports
  them; use the frequency entry or the radio's own controls for those bands.
  (MOR-1674)

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
