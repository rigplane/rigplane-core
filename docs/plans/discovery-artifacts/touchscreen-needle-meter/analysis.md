# Touchscreen SDR needle-meter reconstruction

## Boundary and result

This is a `compound` reference: it combines curved analog scales, two live traces, two waterfalls, dense chrome, numeric readouts, indicators, and touchscreen controls. The first pass therefore selects only `main-multiscale-needle-meter`. The right meter is recorded as `repeatedFrom` and is not generated independently. Full-face composition is outside this pass.

The reference looks like an IC-7610-style dual-receiver display, but the model name is not visible. This pass uses `rigs/ic7610.toml` as an explicit, image-derived assumption because it is the repository's dual-receiver Icom profile and it backs the visible meter family. That assumption is not hardware or model identification evidence.

## Source and reproducible measurements

- Source: `/var/folders/gt/c_czgx6x5bxc1sb3ntph9mgr0000gn/T/codex-clipboard-9ae12bbf-60c3-439d-9bc0-41d56e3f2220.png`
- SHA-256: `a2ef93da070e56f051d9674045b3de49420cbddc5fc4a0b932149f23f89d36c8`
- Dimensions: 1734 by 1000 pixels.
- Panel crop: the full image. Region bounds in `face-map.json` are shares of this box.
- Selected crop: `(235, 65, 640, 240)`, or `(0.1355, 0.0650, 0.3691, 0.2400)` of the panel.

Commands run from the repository root:

```text
/Users/moroz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 .claude/skills/design-a-face/measure-reference.py selftest
/Users/moroz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 .claude/skills/design-a-face/measure-reference.py bands <source>
/Users/moroz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 .claude/skills/design-a-face/measure-reference.py columns <source>
/Users/moroz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 .claude/skills/design-a-face/measure-reference.py bands <source> --crop 220,45,875,315 --min-run 3
/Users/moroz/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3 .claude/skills/design-a-face/measure-reference.py columns <source> --crop 220,45,875,315 --min-run 3
```

The self-test recovered all planted bands, the low-contrast case, smoothing, colour dispatch, variation dispatch, and degenerate-chroma warning. Whole-panel ink profiling found a broad upper band occupying 73.2% of panel height and a scope/lower-controls band from 73.5% to 96.7%. Whole-panel columns collapsed into a 99.1% run, so they do not establish the receiver divider. In the 655 by 270 meter crop, horizontal profiling found runs at 0.0-30.7%, 31.9-42.6%, and 43.0-100.0%; vertical profiling found runs at 0.0-40.8%, 41.4-42.1%, and 42.4-100.0%. Those runs support a dense layered instrument, not individual landmark positions.

The detector cannot isolate the meter's curved landmarks. Their map bounds are therefore direct image readings with a generous tolerance of plus or minus 2% of panel width/height. The implementation comparison uses the skill defaults: 1.5% for selected-region bounds, 2% for primary landmarks within the crop, and 3% for major proportional relationships. Text anti-aliasing is excluded.

## Contract read

`extract-contract.py --radio ic7610` read `frontend/src/presentation/layouts/contract.ts`, `frontend/src/semantic/radio-view-model.ts`, `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`, `rigs/ic7610.toml`, the panel command/adapter files, `radio-intents.ts`, and `SemanticRadioSurfaces.svelte`.

- `SEMANTIC_SURFACE_NAMES` contains 14 names, including `meters`; `meters` is display-only at its mount.
- `RadioViewModel` has 25 fields, 15 optional groups, and an optional `meters?: MetersViewModel` group.
- `meters` is absent when no App TX-authority snapshot or server state exists, and also absent when no meter sample has ever existed. A missing group is not an all-unsupported group.
- Every `MeterField` has `reading: known(value) | unknown`, `availability.structural`, `availability.operational`, and `relevant`. It does not carry a stale/not-observed reason.
- `TxTargetViewModel` alone carries `not-observed | stale | unsupported | contradiction`. `FrequencyPermit` separately carries `outside-configured-ranges | ranges-unconfigured | tx-target-unknown`. `DisabledReasonCode` separately carries `capability-unavailable | field-not-observed | tx-target-unknown | out-of-band | mutually-exclusive-control`. None of these vocabularies is projected onto meter fields.
- `rigs/ic7610.toml` declares `meters` and profile commands for S, Po, SWR, ALC, COMP, Vd, and Id. Its S-meter calibration is table-driven; the selected implementation therefore accepts a profile-normalized fraction and preformatted value instead of importing the store-owning calibration helper.

### Selected-region state matrix

| Input state | Treatment |
| --- | --- |
| `structural=false` | Preserve only the layout spacer; hide scale, labels, and pointer. |
| `structural=true`, `operational=false` | Keep scale geometry dimmed, remove the pointer, and show `?`; do not fabricate zero. |
| known and `relevant=false` | Keep the known pointer and scale, uniformly ghosted to show a currently inactive domain. |
| known and relevant | Render the normalized pointer, fixed scale artwork, and preformatted value for assistive text. |

## Reference reasoning

### Established outside the picture

- The selected instrument is passive: the repository's `meters` semantic surface has no callback props and dispatches no intent. Established by `extract-contract.py --radio ic7610`.
- SVG is the appropriate renderer for the curved scales, radial ticks, labels, and pointer. Established by the local `design-a-face` visual-reconstruction contract.
- The IC-7610 profile exposes the seven meter fields visible in the reference family. Established by `rigs/ic7610.toml` plus `MetersViewModel`.

### Inferred from the picture

- High confidence: the left and right meters are one repeated graphic family with independent readings.
- High confidence: the large frequency readouts and analog meters are continuously scanned; scope controls and softkeys are secondary.
- Medium confidence: `MET Po` is the currently selected meter scale and the white pointer is a power reading. The fixture uses this reading, but no production binding is added.
- Medium confidence: the centered `DUAL-W` chip communicates the relationship between receiver clusters rather than belonging to either one.
- Low confidence: the second line under the clock is another time-zone or offset readout. It remains unidentified.

The image captures one working moment. It does not establish unknown, unsupported, stale, disconnected, fault, or transmit transitions. All such treatments in the implementation are new state work constrained by the repository contract, not copied from the reference.

## Backed, unbacked, and unshown

### Backed in the selected region

| Element | Field | State coverage | Drawing owner |
| --- | --- | --- | --- |
| Power pointer and active `Po` label | `meters.power` | known, known-inactive, unknown-operational, unsupported | New passive SVG sibling; profile normalization and formatting remain upstream. |
| S scale artwork | `meters.signal` | Structural artwork only in this selected Po fixture; a future S selection would use the same four-state grammar. | New passive SVG sibling. |
| SWR scale artwork | `meters.swr` | Structural artwork only in this selected Po fixture. | New passive SVG sibling. |
| ALC scale artwork | `meters.alc` | Structural artwork only in this selected Po fixture. | New passive SVG sibling. |
| COMP scale artwork | `meters.compression` | Structural artwork only in this selected Po fixture. | New passive SVG sibling. |
| Id/Vd labels | `meters.drainCurrent`, `meters.drainVoltage` | Structural artwork only in this selected Po fixture. | New passive SVG sibling. |

The selected component is display-only. It dispatches no `RADIO_INTENT_NAMES` member and has no command-feedback path.

### Unbacked or not available through this selected seam

- The clock cluster has no `RadioViewModel` field and remains unidentified.
- Spectrum and waterfall sample pixels are not carried by `ScopeDisplayViewModel`; they require the existing App-owned scope resource seam and are outside this selected meter pass.
- The exact meaning of several single-letter scope-strip legends is not established by the image. Their geometry remains mapped without production function claims.

### Unshown by the selected crop

All VFO, RX/TX, TX auxiliary, RX audio, filter, DSP, RF-front-end, band, antenna, RIT/XIT/scan, CW-keyer, scope-display, and scope-control fields are outside the selected meter crop. The complete mechanical accounting is in `buildability-checklist.txt`; none is silently treated as drawn.

## Placement and ownership

The selected meter occupies a fixed-aspect SVG box because its scale and pointer landmarks must stay aligned. This is an inferred placement rule from the image, not a production layout decision. The design-language tier cannot draw the geometry: current languages reach `LinearSMeter` and `BarGauge` descriptors, while the curved scale is code-owned. The implementation is therefore a sibling renderer, not a stylesheet change and not a modification to the existing linear meter.

No skin, layout, workspace design-language ID, semantic surface, profile, production registration, or backend is changed in this pass. The browser fixture alone supplies the normalized sample and display text.
