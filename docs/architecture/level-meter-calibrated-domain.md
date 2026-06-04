# Vendor-neutral calibrated domain for level/meter values

**Status:** Accepted (Phase 0 of MOR-453). The canonical DOMAIN (option d, below)
is ratified; the per-field unit specifics and the rigctld back-compat policy are
deferred to Phase 1 (see Open questions). Supersedes the interim "device-scale on
neutral paths" convention used during MOR-437/451/452. Relates to
field-path-promotion-criterion.md (its value-side counterpart) and open-core-policy.md.

## Context

The radio state pipeline emits backend-neutral FieldPaths, but the VALUES on those
paths are still on each device's native scale. `field-path-promotion-criterion.md`
governs which fields earn a neutral path (SEMANTIC coherence) and deliberately left
value normalization to MOR-453. This ADR is the value-side counterpart: it fixes the
DOMAIN that neutral values live in.

The current state is inconsistent across backends and consumers:

- **Icom levels are raw 0-255.** `runtime/_civ_rx.py` (`_decode_level`, ~L1713)
  decodes the 2-byte BCD level frame to its native integer; for the level/meter
  commands (0x14/0x15/0x16) that integer is the device's 0-255 scale, emitted
  verbatim to neutral paths.
- **rigctld_client up-scales to 0-255.** `backends/rigctld_client/radio.py`
  (`_level_255_to_float` / `_float_to_level_255`, ~L650-668) converts hamlib's
  native 0.0-1.0 to/from a 0-255 integer. So 0-255 is the de-facto INTERNAL control
  scale even for a backend whose wire protocol is normalized float.
- **FTX-1 (Yaesu CAT) is mixed.** `backends/yaesu_cat/radio.py` declares
  `native_power_unit = "watts"` (~L158); `backends/yaesu_cat/observations.py` emits
  `global.operator_controls.power_level` as WATTS (the CAT `PC` setpoint, ~L691-697),
  while af/rf/squelch stay raw. So a single neutral path `power_level` means a raw
  0-255 device level for Icom but a physical watt count for Yaesu — the same path,
  different physical quantities per vendor. `cw_pitch` is similarly Hz, not raw.
- **`tone_freq` / `tsql_freq` are centiHz but carry no unit.** `radio_state.py`
  (~L95-96) documents the value as centiHz (8850 = 88.50 Hz) and the Yaesu observer
  emits `round(Hz * 100)` to match the Icom MOR-451 convention, yet the FieldSpec
  entries (`core/state_pipeline_contracts.py` ~L1018-1027) declare no `unit`.
- **FieldSpec has `unit` but no range/scale/curve.** `core/state_pipeline_contracts.py`
  `FieldSpec` (~L433) carries `unit: str | None` (~L441) and nothing else — no range,
  no scale, no calibration reference.
- **Per-rig non-linear calibration tables already exist** in `rigs/*.toml`
  (`[[meters.<name>.calibration]]` raw→actual point lists) but are consumed only
  client-side / for display, and coverage is uneven: IC-7610 is full (s_meter, power,
  swr, alc); IC-7300/IC-705/IC-9700 are swr-only; FTX-1 has s_meter + swr (no power,
  no alc); X6100/X6200/TX500 have none. The curves are genuinely per-rig and
  non-linear — e.g. IC-7610 s_meter steps 6/12/12 dB across raw 0/26/52/78 while
  FTX-1 s_meter steps a uniform 3 dB across raw 0/13/26/39.
- **Consumers assume raw 0-255.** rigctld (`rigctld/handler.py`, ~L1660-1890)
  converts STRENGTH / RFPOWER / LEVEL inline with hand-rolled formulas — `/255.0` for
  RFPOWER and the float levels, a distinct `(raw / 241.0) * 114.0 - 54.0` for STRENGTH.
  The v2 frontend hard-codes `max={255}` sliders across panels (TxPanel, VoxPanel,
  DspPanel, EssentialsPanel, CwPanel, RfFrontEnd, …) and carries ad-hoc display
  formulas in `components-v2/layout/mobile-layout-logic.ts` (~L25-49:
  `raw / 255 * 100` watts, `raw / 120 * 9` S-units, an S9=-73 dBm map).

The result: neutral values are NOT comparable across vendors today, and every
consumer re-derives scaling privately.

## Decision

Four candidate domains were considered:

- **(a) normalized float controls + real-unit meters** — controls become
  dimensionless 0.0-1.0; meters become engineering units. Cross-vendor comparable;
  aligns controls with hamlib's native scale. Cost: every backend converts at the
  boundary, every consumer drops its private scaling.
- **(b) integer 0-100% controls** — human-friendly, but quantizes to 101 steps and
  loses hamlib's float pass-through and precision.
- **(c) keep raw + attach calibration metadata, consumers normalize** — minimal
  backend change, but VALUES STAY INCOMPARABLE across vendors, which is exactly the
  failure this issue exists to fix; it just relocates the per-consumer scaling.
- **(d) HYBRID** — (a) reframed as a deliberate control/meter split: controls and
  meters are different kinds of value and get different domains.

**ADOPTED: option (d).** Controls map to a dimensionless 0.0-1.0 float (unit
`"normalized"`); meters map to engineering units with per-rig curves (s_meter dB,
power W, swr ratio, alc/comp dB-or-normalized).

Rationale: maximum cross-vendor comparability where it is achievable; the control
domain aligns with hamlib's native 0.0-1.0 so the rigctld layer SIMPLIFIES rather
than grows another conversion; and per-rig non-linearity is confined to meters, where
the calibration tables already live. (c) is rejected because values stay incomparable
(fails the issue goal). (b) is rejected because it loses hamlib pass-through and
precision. Explicitly: the DOMAIN, the unit vocabulary, and the interpolation
ALGORITHM are GLOBAL (defined in the contract / shared via `runtime/meter_cal.py`);
the correction CURVES are PER-RIG (`rigs/*.toml`) because they are physically
device-specific.

## Phased migration

- **Phase 0** — this ADR plus the inventory above.
- **Phase 1 (contract)** — extend the unit vocabulary (`"normalized"` for controls;
  `"db"` / `"w"` / `"ratio"` for meters; also declare `"centihz"` on `tone_freq` /
  `tsql_freq` for consistency); decide whether calibration lives inside `FieldSpec` or
  is referenced from the rig profile by path; add a single shared conversion helper
  alongside `runtime/meter_cal.py`; define the no-table fallback policy.
- **Phase 2 (per-backend conversion)** — convert at the observation boundary, one
  field-class at a time, each with regressions: Icom `runtime/_civ_rx.py`, Yaesu
  `backends/yaesu_cat/observations.py`, and `backends/rigctld_client`. The existing
  calibrated `get_swr()` (`runtime/sync.py` → `runtime/meter_cal.py interpolate_swr`)
  is the proof-of-concept for the meter side.
- **Phase 3 (consumers)** — drop the frontend `max={255}` sliders and the ad-hoc
  `mobile-layout-logic.ts` formulas; simplify the rigctld GET/SET conversions.
- **Phase 4 (cleanup)** — remove device-scale assumptions: the 0-255 rigctld_client
  internal convention and the legacy `RadioState` raw mirrors.

## Consequences

- One authority for what a value MEANS; consumers stop carrying private scaling.
- Cross-vendor comparison becomes well-defined.
- The rigctld control path simplifies (controls already align with hamlib 0.0-1.0).
- New cost: every rig needs a per-rig meter table. X6100/X6200/TX500 (no tables) and
  FTX-1 power/alc (missing) are the visible gaps — a documented fallback prevents
  breakage where a curve is absent.
- The migration is cross-cutting but stays decomposed per field-class (mirroring the
  field-path-promotion approach), and it is NOT release-gating.

## Open questions (deferred to Phase 1 — product/architecture decisions, not derivable from code)

1. Control domain confirmed 0.0-1.0 [ratified] vs revisit.
2. `power_level` unit — fraction of `[power].max_watts` vs explicit `unit="w"` (the
   biggest current incomparability).
3. `s_meter` unit — dB-rel-S9 vs absolute dBm vs S-units (three consumers disagree
   today).
4. alc/comp unit — dB vs normalized.
5. Calibration location — embedded in `FieldSpec` vs referenced from the rig profile
   by path.
6. No-table fallback policy — identity-on-device-scale vs linear default vs mark
   missing.
7. rigctld backward-compat — may the wire output change, or must rigctld convert back
   to preserve exact current behavior for WSJT-X / hamlib clients.
8. Interim MOR-451/452 fields (`apf_type_level`, raw att / `tuner_status` /
   `break_in` / `rit_freq`) — re-scale in the same passes or revisit individually.
