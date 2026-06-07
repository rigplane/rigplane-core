# IC-7610 control readback audit (front-panel → web)

> **Branch:** `codex/mor-334-radio-state-pipeline` · **HEAD:** `e0594bce` ·
> **Date:** 2026-06-05 · **Epic:** MOR-488
>
> These are **code-derived predictions — confirm on hardware.** Verdicts and gap
> classes are taken from two completed code audits; line numbers below were
> re-confirmed against this HEAD. Use the hand-verification checklist at the end
> to walk the v2 UI and confirm or refute each predicted verdict physically.

Scope: every IC-7610 v2 Web UI control, classified by whether a **front-panel**
change propagates back to the **web UI** (readback). This is distinct from
web→radio control (sending a value); a control can send fine yet never reflect a
knob turned on the rig.

---

## Mechanism (how a front-panel change reaches the web UI)

On the IC-7610 **LAN** path an `AcquisitionScheduler` is attached in
`src/rigplane/web/server.py`. With the scheduler attached, the legacy broad
`_STATE_QUERIES` round-robin is **dead** — it no longer drives field refreshes.

A field is **POLLED** if and only if **both**:

1. its **exact** `FieldPath` appears in `polling_only` in `rigs/ic7610.toml`
   (lines 136–150 at this HEAD), **AND**
2. `IcomCivAcquisitionExecutor.query_for_path`
   (`src/rigplane/core/acquisition_scheduler.py`, def at line 283) returns a CI-V
   read tuple `(cmd, sub, receiver)` for it.

CI-V **transceive is never auto-enabled**, so the radio broadcasts nothing
unsolicited. Therefore **every front-panel → web readback depends on polling.**

`StateStore.apply` is **unconditional**: if a frame for a field arrives and a
`_civ_rx.py` emitter (a `_observations_from_frame` parser in
`src/rigplane/runtime/_civ_rx.py`) exists for it, the UI updates. So:

> A control reflects a front-panel change **iff**: an **emitter exists** AND the
> **field is polled**.

The `command_response_observable` and `unsolicited_push` TOML lists only steer
*which scheduler method/route* a field uses — they do **not** decide whether
`apply` happens. Do not mistake presence in those lists for readback.

### Structural keystone

`query_for_path` has **no branch at all** for the `operator_toggles` family, and
no branch for many commands. Confirmed at this HEAD, the method only handles:

| Branch (line) | Family / scope | Coverage |
|---|---|---|
| 290 | `receiver.*.freq_mode` | `freq_hz` (0x25), `mode` (0x26) only |
| 296 | `receiver.*.meters` | `s_meter` only |
| 300 | `receiver.*.operator_controls` | level subs + att/preamp |
| 306 | `global.meters` | power/swr/alc |
| 309 | `global.slow_state` | `active` only |
| 313 | `global.tx_state` | `ptt`, `rit_on`, `rit_tx` only |
| 321 | `global.operator_controls` | `rit_freq` + global level subs |

Missing entirely: the `operator_toggles` family; 0x16 toggles; 0x1B tone;
0x1C tuner; 0x0F split; 0x07/0xC2 dual_watch; 0x1A/0x03 filter_width;
0x1A/0x06 data_mode; 0x10 tuning_step; 0x14/0x09 cw_pitch. These mappings must
be **added** before the corresponding fields can be polled.

---

## Gap classes

| Class | Condition | Fix |
|---|---|---|
| **A1** | emitter exists AND a `query_for_path` mapping already exists | add the FieldPath to `polling_only` (**TOML-only**) |
| **A2** | emitter exists BUT `query_for_path` lacks a mapping | add a `query_for_path` branch **+** add to `polling_only` |
| **B** | no emitter | add a `_civ_rx._observations_from_frame` parser (+ a `FieldSpec` in `state_pipeline_contracts.py` where noted), then treat as A |
| **C** | polled MAIN only | add the `receiver.sub.*` path (every receiver-scoped A-fix must add **main + sub**) |
| **D** | intentional / not-applicable | document, do **not** "fix" by polling |

---

## WORKS today (front-panel → web)

| Control | UI panel | FieldPath | Projection key | CI-V read | Polled |
|---|---|---|---|---|---|
| Frequency main | VFO | `receiver.0.active.freq_mode.freq_hz` | `main.freqHz` | 0x25 | main + sub |
| Frequency sub | VFO | `receiver.1.active.freq_mode.freq_hz` | `sub.freqHz` | 0x25 | main + sub |
| Mode main | VFO | `receiver.0.active.freq_mode.mode` | `main.mode` | 0x26 | main + sub |
| Mode sub | VFO | `receiver.1.active.freq_mode.mode` | `sub.mode` | 0x26 | main + sub |
| Filter FIL1/2/3 main | Filter | `receiver.0.active.freq_mode.filter_num` | `main.filter` | rides 0x26 | indirect |
| Filter FIL1/2/3 sub | Filter | `receiver.1.active.freq_mode.filter_num` | `sub.filter` | rides 0x26 | indirect |
| Active RX select | VFO | `global.slow_state.active` | `active` | 0x07/0xD2 | yes |
| RIT on/off | RIT/XIT | `global.tx_state.rit_on` | `ritOn` | 0x21/01 | yes |
| XIT on/off | RIT/XIT | `global.tx_state.rit_tx` | `ritTx` | 0x21/02 | yes |
| RIT/XIT offset | RIT/XIT | `global.operator_controls.rit_freq` | `ritFreq` | 0x21/00 | yes |
| RF Gain (MAIN) | RF Front End | `receiver.main.operator_controls.rf_gain` | `main.rfGain` | 0x14/02 | MAIN only |
| AF Level (MAIN) | RF Front End | `receiver.main.operator_controls.af_level` | `main.afLevel` | 0x14/01 | MAIN only |
| Squelch (MAIN) | RF Front End | `receiver.main.operator_controls.squelch` | `main.squelch` | 0x14/03 | MAIN only |
| ATT (MAIN) | RF Front End | `receiver.main.operator_controls.att` | `main.att` | 0x11 | MAIN only |
| Preamp (MAIN) | RF Front End | `receiver.main.operator_controls.preamp` | `main.preamp` | 0x16/02 | MAIN only |

Notes:
- **Filter FIL1/2/3** is **indirect** — it rides the mode 0x26 poll, so its
  readback cadence equals the mode TTL (~2 s), not an independent poll.
- **RF Gain / AF Level / Squelch / ATT / Preamp** work (MOR-487) but **MAIN
  only**, at ~3 s cadence (visible lag). Their SUB counterparts are **broken
  (class C)**.

---

## Class A1 — emitter + query mapping exist → `polling_only` add only

All receiver-scoped rows below are confirmed mapped in
`_RECEIVER_LEVEL_QUERY_SUBS` (line 218); global rows in
`_GLOBAL_LEVEL_QUERY_SUBS` (line 233). Add main **and** sub for receiver rows.

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| NR Level | DSP | `receiver.{main,sub}.operator_controls.nr_level` | `main\|sub.nrLevel` | 0x14/06 | yes | no | BROKEN-A1 | add to `polling_only` |
| NB Level | DSP | `receiver.{main,sub}.operator_controls.nb_level` | `nbLevel` | 0x14/12 | yes | no | BROKEN-A1 | add to `polling_only` |
| PBT inner | Filter | `receiver.{main,sub}.operator_controls.pbt_inner` | `pbtInner` | 0x14/07 | yes | no | BROKEN-A1 | add to `polling_only` |
| PBT outer | Filter | `receiver.{main,sub}.operator_controls.pbt_outer` | `pbtOuter` | 0x14/08 | yes | no | BROKEN-A1 | add to `polling_only` |
| APF type/level | DSP | `receiver.{main,sub}.operator_controls.apf_type_level` | `apfTypeLevel` | 0x14/05 | yes | no | BROKEN-A1 | add to `polling_only` |
| RF Power | TX | `global.operator_controls.power_level` | `powerLevel` | 0x14/0A | yes | no | BROKEN-A1 | add to `polling_only` |
| Mic Gain | TX | `global.operator_controls.mic_gain` | `micGain` | 0x14/0B | yes | no | BROKEN-A1 | add to `polling_only` |
| Comp Level | TX | `global.operator_controls.compressor_level` | `compressorLevel` | 0x14/0E | yes | no | BROKEN-A1 | add to `polling_only` |
| Monitor Level | TX | `global.operator_controls.monitor_gain` | `monitorGain` | 0x14/15 | yes | no | BROKEN-A1 | add to `polling_only` |
| VOX Gain | VOX | `global.operator_controls.vox_gain` | `voxGain` | 0x14/16 | yes | no | BROKEN-A1 | add to `polling_only` |
| Anti-VOX Gain | VOX | `global.operator_controls.anti_vox_gain` | `antiVoxGain` | 0x14/17 | yes | no | BROKEN-A1 | add to `polling_only` |

---

## Class A2 — emitter exists, `query_for_path` branch MISSING → add branch + `polling_only`

### cmd16 receiver toggles (need a new `operator_toggles` family branch in `query_for_path`)

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| DIGI-SEL | RF Front End | `receiver.{main,sub}.operator_toggles.digisel` | `main\|sub.digisel` | 0x16/4E (cmd29 ✓) | yes | no | BROKEN-A2 | add `operator_toggles` branch + `polling_only` |
| IP+ | RF Front End | `receiver.{main,sub}.operator_toggles.ipplus` | `ipplus` | 0x16/65 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| NB on/off | DSP | `receiver.{main,sub}.operator_toggles.nb` | `nb` | 0x16/22 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| NR on/off | DSP | `receiver.{main,sub}.operator_toggles.nr` | `nr` | 0x16/40 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Auto-Notch | DSP | `receiver.{main,sub}.operator_toggles.auto_notch` | `autoNotch` | 0x16/41 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Manual-Notch on/off | DSP | `receiver.{main,sub}.operator_toggles.manual_notch` | `manualNotch` | 0x16/48 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Twin-Peak | DSP | `receiver.{main,sub}.operator_toggles.twin_peak_filter` | `twinPeakFilter` | 0x16/4F (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Repeater tone | Tone/FM | `receiver.{main,sub}.operator_toggles.repeater_tone` | `repeaterTone` | 0x16/42 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Repeater TSQL | Tone/FM | `receiver.{main,sub}.operator_toggles.repeater_tsql` | `repeaterTsql` | 0x16/43 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |

### cmd16 receiver `operator_controls` values

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| AGC mode | AGC | `receiver.{main,sub}.operator_controls.agc` | `agc` | 0x16/12 (**cmd29 route MISSING** — add `[0x16,0x12]` for SUB) | yes | no | BROKEN-A2 | add branch + `polling_only` + cmd29 SUB route |
| Audio Peak Filter on | CW | `receiver.{main,sub}.operator_controls.audio_peak_filter` | `audioPeakFilter` | 0x16/32 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |

### cmd16 global `tx_state` toggles (need a global `tx_state` cmd16 branch)

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| Compressor on/off | TX | `global.tx_state.compressor_on` | `compressorOn` | 0x16/44 (global, no cmd29) | yes | no | BROKEN-A2 | add `tx_state` cmd16 branch + `polling_only` |
| Monitor on/off | TX | `global.tx_state.monitor_on` | `monitorOn` | 0x16/45 (global, no cmd29) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| VOX on/off | VOX | `global.tx_state.vox_on` | `voxOn` | 0x16/46 (global, no cmd29) | yes | no | BROKEN-A2 | add branch + `polling_only` |

### cmd1A

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| AGC time const | AGC | `receiver.{main,sub}.operator_controls.agc_time_constant` | `agcTimeConstant` | 0x1A/04 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| VOX delay | VOX | `global.operator_controls.vox_delay` | `voxDelay` | 0x1A/05 | yes | no | BROKEN-A2 (low priority) | add branch + `polling_only` |

### cmd14 global

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| CW Pitch | CW | `global.operator_controls.cw_pitch` | `cwPitch` | 0x14/09 | yes | no | BROKEN-A2 | add 0x09 to `_GLOBAL_LEVEL_QUERY_SUBS` + `polling_only` |

### freq_mode / VFO / tone / tuning / tuner

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| Filter width | Filter | `receiver.{main,sub}.active.freq_mode.filter_width` | `filterWidth` | 0x1A/03 (cmd29 ✓) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Data mode | VFO | `receiver.{main,sub}.active.freq_mode.data_mode` | `dataMode` | 0x1A/06 (plain) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Tone freq | Tone/FM | `receiver.{main,sub}.operator_controls.tone_freq` | `toneFreq` | 0x1B/00 (cmd29 ✓) | yes | no | BROKEN-A2 | add 0x1B branch + `polling_only` |
| TSQL freq | Tone/FM | `receiver.{main,sub}.operator_controls.tsql_freq` | `tsqlFreq` | 0x1B/01 (cmd29 ✓) | yes | no | BROKEN-A2 | add 0x1B branch + `polling_only` |
| Split | VFO/Split | `global.tx_state.split` | `split` | 0x0F (plain) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Dual watch | VFO/Split | `global.tx_state.dual_watch` | `dualWatch` | 0x07/0xC2 (plain) | yes | no | BROKEN-A2 | extend 0x07 branch + `polling_only` |
| Tuning step | VFO | `global.slow_state.tuning_step` | `tuningStep` | 0x10 (plain) | yes | no | BROKEN-A2 | add branch + `polling_only` |
| Tuner on/off + start | Antenna/Tuner | `global.operator_controls.tuner_status` | `tunerStatus` | 0x1C/01 (plain) | yes | no | BROKEN-A2 | add 0x1C branch + `polling_only` |

---

## Class B — no emitter → add `_civ_rx` parser (+ `FieldSpec` where noted), then treat as A

| Control | UI panel | FieldPath | Projection key | CI-V read | Emitter? | Polled now? | Verdict | Fix |
|---|---|---|---|---|---|---|---|---|
| Key Speed (WPM) | CW | `global.operator_controls.key_speed` | `keySpeed` | 0x14/0C | no | no | BROKEN-B | add parser (non-linear WPM decode); FieldSpec **exists**; query mapping **NOT** present — also add query mapping + `polling_only` |
| Break-in delay | CW | `global.operator_controls.break_in_delay` | `breakInDelay` | 0x14/0F | no | no | BROKEN-B | add emitter only; query mapping **already present** (0x0F in `_GLOBAL_LEVEL_QUERY_SUBS`); FieldSpec exists; then `polling_only` |
| Drive gain | TX | `global.operator_controls.drive_gain` | `driveGain` | 0x14/14 | no | no | BROKEN-B | add emitter; query mapping present (0x14); then `polling_only` |
| Notch position | DSP | `global.operator_controls.notch_filter` | `notchFilter` | 0x14/0D | no | no | BROKEN-B | add emitter (the position half of "NOTCH from radio doesn't work"); query mapping present (0x0D); then `polling_only` |
| Filter shape | Filter | `receiver.{id}.operator_controls.filter_shape` | `filterShape` | 0x16/56 | no | no | BROKEN-B | add parser **+ FieldSpec**, then A |
| Dial lock | VFO | `global.tx_state.dial_lock` | `dialLock` | 0x16/50 | no | no | BROKEN-B | add parser **+ FieldSpec**, then A |
| Break-in mode (OFF/SEMI/FULL) | CW | `global.operator_controls.break_in` | `breakIn` | 0x16/47 | no | no | BROKEN-B | add parser, then A |
| Manual-notch width | DSP | `receiver.{id}.operator_controls.manual_notch_width` | `manualNotchWidth` | 0x16/57 | no | no | BROKEN-B | add parser **+ FieldSpec** (registry spec missing), then A |
| Main/Sub tracking | VFO/Split | `global.tx_state.main_sub_tracking` | `mainSubTracking` | 0x16/5E | no | no | BROKEN-B | add parser **+ FieldSpec**; verify the SET path exists (may be missing in UI), then A |
| SSB TX bandwidth | TX | — | — | 0x16/58 | no | no | BROKEN-B (low priority) | add parser only if UI uses it |
| RX-I/O antenna | Antenna/Tuner | — | — | 0x16/53 | no | no | BROKEN-B (low priority) | add parser only if UI uses it |

---

## Class C — polled MAIN only → extend to SUB

| Controls | UI panel | Fix |
|---|---|---|
| rf_gain, af_level, squelch, att, preamp | RF Front End | add the `receiver.sub.operator_controls.*` paths to `polling_only` |

**Rule:** every receiver-scoped A-fix above must add **both main and sub** from
the start, to avoid re-introducing this gap for the newly enabled controls.

---

## Class D — intentional / not-applicable (document, do NOT "fix" by polling)

| Item | FieldPath / detail | Why D |
|---|---|---|
| TX antenna | `global.operator_controls.tx_antenna` (0x12) | emitter exists, but **0x12 is explicitly "NOT safe to poll"**; only CI-V transceive would deliver front-panel changes. No poll fix. |
| RX antenna | `global.slow_state.rx_antenna_1/2` (0x12) | same — 0x12 not safe to poll. No poll fix. |
| A=B / swap | momentary VFO actions | no readback field; result reflected via freq/mode polls. |
| contour | `main.contour` | dead/unexposed UI field — frontend cleanup, not a readback fix. |
| keyer type | `keyerType` (hard-coded 0) | hard-coded — frontend cleanup. |
| keyer memory | — | unexposed UI field — frontend cleanup. |
| repeater offset | — | unexposed UI field — frontend cleanup. |

---

## Cadence note

The MOR-487 controls (RF/AF/SQL/ATT/preamp, MAIN) poll at **3.0 s** (see
`rigs/ic7610.toml` field policies, e.g. line 230; default is 2.0 s). In practice
this **felt laggy** during validation. Cadence is a tuning knob: tighter cadence
improves readback responsiveness but adds CI-V bus load, which competes with the
~500 ms control keep-alive and the ~100 ms audio keep-alive. Tune deliberately;
do not weaken the keep-alives.

---

## Summary by class

| Class | Count |
|---|---|
| WORKS today | 15 rows (11 distinct controls + 4 receiver-scoped pairs) |
| A1 | 11 |
| A2 | 24 (9 toggles + 2 controls + 3 tx toggles + 2 cmd1A + 1 cmd14 + 7 freq/VFO/tone/tuner, several main+sub) |
| B | 11 |
| C | 5 |
| D | 7 |

(A2/C counts are per *control*; receiver-scoped controls each imply a main + sub
FieldPath pair in the fix.)

---

## Hand-verification checklist

Walk the v2 UI panel by panel. For each control: change it **on the radio front
panel** and watch whether the **web UI** updates. The predicted verdict (WORKS /
BROKEN-classX) is the code-derived expectation — confirm or refute it.

### RF Front End
- [ ] RF Gain (MAIN) — turn knob on front panel, expect web to update (predicted: WORKS, ~3 s lag)
- [ ] RF Gain (SUB) — (predicted: BROKEN-C)
- [ ] AF Level (MAIN) — (predicted: WORKS, ~3 s lag)
- [ ] AF Level (SUB) — (predicted: BROKEN-C)
- [ ] Squelch (MAIN) — (predicted: WORKS, ~3 s lag)
- [ ] Squelch (SUB) — (predicted: BROKEN-C)
- [ ] ATT (MAIN) — (predicted: WORKS, ~3 s lag)
- [ ] ATT (SUB) — (predicted: BROKEN-C)
- [ ] Preamp (MAIN) — (predicted: WORKS, ~3 s lag)
- [ ] Preamp (SUB) — (predicted: BROKEN-C)
- [ ] DIGI-SEL — (predicted: BROKEN-A2)
- [ ] IP+ — (predicted: BROKEN-A2)

### DSP
- [ ] NR on/off — (predicted: BROKEN-A2)
- [ ] NR Level — (predicted: BROKEN-A1)
- [ ] NB on/off — (predicted: BROKEN-A2)
- [ ] NB Level — (predicted: BROKEN-A1)
- [ ] Auto-Notch — (predicted: BROKEN-A2)
- [ ] Manual-Notch on/off — (predicted: BROKEN-A2)
- [ ] Manual-Notch width — (predicted: BROKEN-B)
- [ ] Notch position — (predicted: BROKEN-B)
- [ ] Twin-Peak — (predicted: BROKEN-A2)
- [ ] APF type/level — (predicted: BROKEN-A1)

### AGC
- [ ] AGC mode — (predicted: BROKEN-A2; SUB also needs cmd29 route)
- [ ] AGC time const — (predicted: BROKEN-A2)

### TX
- [ ] RF Power — (predicted: BROKEN-A1)
- [ ] Mic Gain — (predicted: BROKEN-A1)
- [ ] Comp Level — (predicted: BROKEN-A1)
- [ ] Compressor on/off — (predicted: BROKEN-A2)
- [ ] Monitor Level — (predicted: BROKEN-A1)
- [ ] Monitor on/off — (predicted: BROKEN-A2)
- [ ] Drive gain — (predicted: BROKEN-B)
- [ ] SSB TX bandwidth — (predicted: BROKEN-B, low priority)

### CW
- [ ] Audio Peak Filter on — (predicted: BROKEN-A2)
- [ ] CW Pitch — (predicted: BROKEN-A2)
- [ ] Key Speed (WPM) — (predicted: BROKEN-B)
- [ ] Break-in delay — (predicted: BROKEN-B)
- [ ] Break-in mode (OFF/SEMI/FULL) — (predicted: BROKEN-B)

### VOX
- [ ] VOX on/off — (predicted: BROKEN-A2)
- [ ] VOX Gain — (predicted: BROKEN-A1)
- [ ] Anti-VOX Gain — (predicted: BROKEN-A1)
- [ ] VOX delay — (predicted: BROKEN-A2, low priority)

### Tone / FM
- [ ] Repeater tone on/off — (predicted: BROKEN-A2)
- [ ] Repeater TSQL on/off — (predicted: BROKEN-A2)
- [ ] Tone freq — (predicted: BROKEN-A2)
- [ ] TSQL freq — (predicted: BROKEN-A2)

### VFO / Split
- [ ] Frequency main — (predicted: WORKS)
- [ ] Frequency sub — (predicted: WORKS)
- [ ] Mode main — (predicted: WORKS)
- [ ] Mode sub — (predicted: WORKS)
- [ ] Active RX select — (predicted: WORKS)
- [ ] Data mode — (predicted: BROKEN-A2)
- [ ] Tuning step — (predicted: BROKEN-A2)
- [ ] Split — (predicted: BROKEN-A2)
- [ ] Dual watch — (predicted: BROKEN-A2)
- [ ] Main/Sub tracking — (predicted: BROKEN-B)
- [ ] Dial lock — (predicted: BROKEN-B)
- [ ] A=B / swap — (predicted: N/A class D — reflected via freq/mode polls)

### RIT / XIT
- [ ] RIT on/off — (predicted: WORKS)
- [ ] XIT on/off — (predicted: WORKS)
- [ ] RIT/XIT offset — (predicted: WORKS)

### Antenna / Tuner
- [ ] Tuner on/off + start — (predicted: BROKEN-A2)
- [ ] TX antenna — (predicted: N/A class D — 0x12 not safe to poll)
- [ ] RX antenna — (predicted: N/A class D — 0x12 not safe to poll)
- [ ] RX-I/O antenna — (predicted: BROKEN-B, low priority)

### Filter
- [ ] Filter FIL1/2/3 main — (predicted: WORKS, indirect via mode poll ~2 s)
- [ ] Filter FIL1/2/3 sub — (predicted: WORKS, indirect via mode poll ~2 s)
- [ ] Filter width — (predicted: BROKEN-A2)
- [ ] Filter shape — (predicted: BROKEN-B)
- [ ] PBT inner — (predicted: BROKEN-A1)
- [ ] PBT outer — (predicted: BROKEN-A1)
