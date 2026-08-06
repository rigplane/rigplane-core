# desktop-v2 on the v3 path — operator-visible parity evidence

**Ticket:** MOR-1372 (v3-rework **S12**, the closing slice of MOR-1263) ·
**Verified at:** `main` `7612a4f8` ("feat(MOR-1370): scopeControls zone ownership on desktop-v2") ·
**Kind:** evidence artefact. It records what an operator gains, loses and keeps when `desktop-v2`
renders the semantic surface family instead of the legacy panel family. It defines no behaviour and
changes no production file.

**Feeds:** MOR-1090 (visual/pixel gate) and MOR-1099 (cutover review).
**Sibling of:** `docs/validation/workspace-v1-verification.md` (same directory, same "verified, not
asserted" contract). Its two ruling siblings are `docs/plans/2026-08-06-settings-modal-boundary.md`
(S10) and `docs/plans/2026-08-06-panel-order-workspace-boundary.md` (S11); where they *ruled*, this
document *counts*.

---

## 0. What this is, and the one rule it follows

The zone-ownership wave (S6-pre … S6b-2) moved ten legacy panel families off `desktop-v2` and put
fourteen semantic surfaces in their place, one zone each. Every slice landed with its own tests and
its own independent verification. This document is the single place where the *operator-visible*
consequences of all of them are stated together, so that the cutover decision is made against a
list rather than against a feeling.

**The rule: every row carries an evidence pointer, and a row whose evidence contradicted the
planning seed says so out loud.** Six rows did. They are collected in §3 and are the most important
part of this document, because each of them was, until now, a fact the program believed and could
have shipped on.

**Verdict vocabulary** (fixed, five values):

| verdict | means |
|---|---|
| `parity` | the operator sees the same fact, by the same or better means |
| `improved` | the semantic surface is strictly better on this axis |
| `recorded lack` | something the legacy family showed is not shown; ticketed or owner-batched |
| `changed derivation` | the fact is still shown but computed or presented differently |
| `by design` | a deliberate boundary, stated so it does not get re-litigated as a defect |

---

## 1. Harness blindness — stated once, and only once

**Four zeroes are not four confirmations.**

S6a, S7, S8, S9 and S6b-2 each reported "zero movement" from the browser capture harness. Every one
of those zeroes is structurally empty, for the same reason, and this section is the only place that
reason is recorded. **Do not repeat it per row, and do not read any harness number in §7 as
`desktop-v2` parity evidence.**

The harness (`frontend/fixtures/`) mounts exactly two components — `frontend/fixtures/main.ts:112`:

```ts
mount(fixture.layout === 'reference' ? ReferenceLayout : DualReceiverCockpit, { … })
```

`RadioLayout` is never mounted. `ReferenceLayout.svelte:32` mounts `<SemanticRadioSurfaces
strips="single" />` bare, and says why in its own header (`:7-14`): pulling in `RadioLayout` "would
pull in that whole chrome tree… for zero additional coverage". That judgement was correct when the
semantic surfaces had no chrome-side counterpart. It stopped being correct the moment the wave's
whole subject became the chrome-side suppression arm.

Consequences, all verified at `7612a4f8`:

1. **No legacy twin is ever rendered by the harness**, so no suppression can be observed.
2. **`desktopV2Layout` is never resolved.** MOR-1355 (`fe01a1f1`) did land and does supply a
   `SurfacePlan` — but opt-in, for exactly one fixture (`catalog.ts:814-818`,
   `topology-2-main-sub--planned`), and it resolves `dualReceiverCockpitLayout`, never
   `desktopV2Layout` (`main.ts:104-109`; `capture.mjs:542-548` says so: "the `--reference` family …
   has no plan-ful twin in this slice"). **The seed's wording "supplies no `SurfacePlan`" is now out
   of date; the structural conclusion it supported is not.**
3. **The harness's own build-identity ledger proves the blindness.** `manifest.json`'s
   `buildIdentity.productionSourceDigests` fingerprints fifteen files. It includes
   `presentation/layouts/dual-receiver-cockpit.ts`. It does **not** include
   `presentation/layouts/desktop-declarations.ts` or `components-v2/layout/RadioLayout.svelte` — the
   two files the entire wave edited. A harness that does not hash the changed file cannot report on
   it.

**What is therefore NOT proven by any harness run in this program:** that `desktop-v2` renders the
zone-owned surfaces in the intended order, that the legacy twins are gone from its DOM, or that the
composite looks acceptable. Those claims rest on jsdom component tests
(`semantic-desktop-migration.component.test.ts` is the load-bearing one) plus MOR-1090's pixel gate.
Widening the harness to mount `RadioLayout` under `desktopV2Layout` is the obvious follow-up and is
**not** in this slice.

One further evidence-shape caveat, from the S6b-2 verification, that belongs here rather than in a
row: the scope-toolbar suppression is proven as a **two-link chain**, not end to end. The
manifest → prop wire is asserted at `RadioLayout` against a stub, and the prop → DOM behaviour is
asserted on `SpectrumToolbar` in isolation. Nothing mounts the real toolbar under the real manifest.

---

## 2. The parity matrix

Twenty-three rows plus three sub-rows (P1r, P13c, P13r — the last two added by `verify-mor-1372`
when the MOR-1358 row was split; see §3.5). "Evidence" is a test name, a file:line, or a verify
report; "disposition" is what
happens next. Rows P10, P11 and P12 are safety adjudications — see §6.

| # | family / row | verdict | evidence | disposition |
|---|---|---|---|---|
| **P1** | **Meters peak-hold (MOR-1282).** The seed recorded this as a lack. It is **not** one at `7612a4f8`: `MetersSurface` enables peak-hold per meter (`MetersSurface.svelte:57-64`, `showPeak` true for Po/SWR/ALC/Id, mirroring the dock's own `PeakKey` set) and delegates the ballistics to `BarGauge`, which holds the state (`BarGauge.svelte:63,76`), decays over the shared `PEAK_DECAY_MS = 1500` (`meter-utils.ts:361`) and resets on `ondblclick` (`BarGauge.svelte:120`) | `parity` | `MetersSurface.test.ts:467` *"BarGauge peak channel (MOR-1282)"*; `:493` *"MetersSurface and MetersDockPanel are on the same peak-hold channel"*; `BarGauge.peakhold.svelte.test.ts:57` | **closed.** See §3.1 for the correction, and the residual below |
| **P1r** | **Peak-marker relevance gate (residual of P1, new here).** The dock hides the marker on an irrelevant tile (`MetersDockPanel.svelte:422`, `&& tile.relevant`); `BarGauge` has no relevance gate, so an RX-dimmed Po tile shows a peak marker on the semantic surface and none in the dock | `recorded lack` (cosmetic) | `MetersDockPanel.svelte:422` vs `MetersSurface.svelte:145-148,168`; no test either side | owner batch — cosmetic, sub-tile |
| **P2** | **SWR/ALC fault border (MOR-1345).** The dock draws a fault border for SWR > 2.0 and ALC ≥ redline during TX; nothing in the semantic path reproduces it. Note the seed's framing is wrong: `fault` is not a tile-component prop, it is a field on `MetersDockPanel`'s own local `Tile` type — `MeterPanel` and `DockMeterPanel` never had it | `recorded lack` | legacy: `MetersDockPanel.svelte:75,272,286,400,495` + `meter-utils.ts:327,332`, pinned by `MetersDockPanel.isolated.test.ts:433`. Semantic: absent — **no test pins presence or deliberate absence** | MOR-1345 |
| **P3** | **Whole-group-absent meters (S5-N1).** A radio reporting no meter fields renders **nothing** on the semantic surface (`MetersSurface.svelte:112`, `{#if meters}` around the whole section). The seed said the legacy dock showed `?` placeholders. It did not — it rendered a titled empty shell (`MetersDockPanel.svelte:385-392` are unconditional; tiles are push-gated at `:243-323`). The `?` is the **semantic** surface's per-field behaviour (`MetersSurface.svelte:132,150`) | `changed derivation` | `MetersSurface.test.ts:127` *"renders NOTHING at all when the view model carries no meters group"*; `semantic-meters-wiring.component.test.ts:244` | **accepted, direction corrected** — §3.3 |
| **P4** | **SUB tuning on dual-receiver radios (MOR-1335).** `isActive` is a single radio-wide fact (`radio-view-model-adapter.ts:1200-1203,1255`), so at most one VFO tile per **radio** is tunable. `VfoSurface.svelte:226-228` gates the per-digit control on it, and the handler re-guards at `:231`. With `state.active === 'MAIN'` both SUB tiles are read-only. The legacy `VfoPanel` mounted one tunable widget per **receiver** | `recorded lack` | `VfoSurface.svelte:208-214` (the gap is self-documented in-file); `VfoSurface.test.ts:794,843`; `radio-view-model-adapter.test.ts:175` | MOR-1335. The gate is deliberate — it closed a wrong-VFO dispatch hazard (MOR-1322 B1) — so the fix must add a receiver axis, not remove the gate |
| **P5** | **Translation delta.** Corrected: the retired legacy family carries **4** real `t()` call sites across **2** of its 11 components, and the semantic family carries **18**, all in `VfoSurface`. Three locale keys lose their only rendering surface on `desktop-v2`. See §5 for the full count and the grep artefact that produced "42" | `recorded lack` (small) + `improved` (VFO) | §5, with the counting method | **MOR-1373** — retargeted; it is a 3-key gap plus a 13-surface hardcoding policy question, not a 42-string regression |
| **P6** | **Audio-link-lost readout (new in this document).** `RxAudioPanel.svelte:53-54` renders an output indicator that reads `core.overlay.audioLinkLost` when live monitoring is selected and the browser audio transport is down, and `formatMonitorStatus(monitorMode)` otherwise. `RxAudioSurface` has no equivalent element (`data-testid` sweep: monitor, af, focus, split, mod-input — no transport-health readout), and a repo-wide grep finds **no other consumer** of that key. On `desktop-v2` the operator no longer sees that the audio link dropped | `recorded lack` | `RxAudioPanel.svelte:18-20,53-54`; `RxAudioSurface.svelte:113-205`; `grep -rn audioLinkLost src/` → 1 consumer + 3 locale files | **needs a ticket.** Arguably out of the facts-only doctrine (transport health is a client fact, not a radio fact) — which explains the omission but does not restore the readout |
| **P7** | **Deck relocation.** Ten legacy panel families are gone from the sidebars and fourteen semantic surfaces now render inside `.receiver-deck`. See §4 for the corrected geometry and the surviving sidebar contents | `changed derivation` | §4 | **accepted with consequence** under the MOR-1263 owner decision; recorded for MOR-1099 |
| **P8** | **SPLIT is now disabled on unknown split status.** The settings modal's `SPLIT` `<HardwareButton>` was never disabled (`RadioLayout.svelte:425-434`; `active` collapses unknown into the grey/false branch). `VfoSurface`'s replacement is `disabled={viewModel.split.status === 'unknown'}` (`:388`) with a handler re-guard (`:254-257`) and `aria-checked="mixed"` (`:386`). On a radio that never reports split status the operator could press it before and cannot now | `by design` (fail-closed) | `VfoSurface.test.ts:200` *"unknown split renders an explicit 'unknown' tri-state"* (the `disabled` assertion at `:212` was added because mutation M14 deleted it and nothing failed); `:319` *"MUTATION KILL: clicking a disabled (unknown) split toggle never emits the intent"* | none — the wave's fail-closed doctrine, recorded as its own row per S10 §6 so it is never absorbed into "improvement" |
| **P9** | **Scan type / resume render as raw integers (MOR-1308 N4).** `RitXitScanSurface.svelte:173,176` print `String(value)` via `textOf` (`:56-57`), so `0x22` reads `34` and a resume mode reads `1`. `ScanPanel.svelte:17-23,37-40` carried label tables (`MEM`, `SEL`, `5s`, `10s`, `15s`) rendered at `:88,:123` | `recorded lack` | file:line above; **no test pins the rendering** — `RitXitScanSurface.test.ts:376-393` pins only structural presence | **owner batch item.** Deliberate and self-documented (`RitXitScanSurface.svelte:39-43`: the label tables are UI-only in v2) |
| **P10** | **`deriveBand` reads `state.active` with no freshness gate (MOR-1307 N4).** `radio-view-model-adapter.ts:705-707` reads `state?.active === 'SUB'` raw; the sibling `activeReceiver` derivation at `:1200-1203` requires `seen()` (observed **and** fresh **and** available, `:81-85`). With `active` unobserved or stale, `onSub` silently defaults to **MAIN** and the adapter publishes `currentBandTx: 'allowed'` while simultaneously emitting `{ field: 'activeReceiver', code: 'field-not-observed' }` (`:1277-1278`) | `recorded lack` — **safety-adjacent** | file:line above; **no test exercises the ungated path** — `band-adapter.test.ts:348` inherits a `fresh` fieldStatus | **MOR-1356** (sole source: this document and `verify-mor-1307`) |
| **P11** | **`RF_MUST_BE_IDLE` is an allow-list over 3 of 7 reasons (MOR-1309 N1).** `AntennaSurface.svelte:75-77` lists `tx-busy`, `radio-transmitting`, `rf-state-unknown`; the `KeyBlockedReason` union (`rx-tx-surface.ts:37-39`) has seven members. The filter at `:114-115` therefore ignores `tx-target-unknown`, `tx-permit-denied`, `tx-permit-unknown` and `tx-fault`. Adding an eighth member produces a silently non-blocking reason with no type error | `recorded lack` — **safety-adjacent** | file:line above; `AntennaSurface.test.ts:303` *"does not block on a permit or target reason alone"* pins the narrowing as intended | **MOR-1361** (sole source). The narrowing is reasoned (`:70-74`); the residual is **structural** — no exhaustiveness check binds new union members to a decision |
| **P12** | **Break-in posture readout is not permit-gated (MOR-1310 6(c)).** `CwKeyerSurface.svelte:181` renders the posture `<output>` under the structural gate only, while the three break-in choices at `:173` carry `disabled={!usable(cw.breakIn) \|\| !permitAllowed}`. Result: "break-in ARMED — the key transmits" beside three disabled buttons. Companion: unread level thumbs park at `min` (`:196`), so an unread keyer speed sits at 6 WPM | `by design` | `CwKeyerSurface.test.ts:345` *"renders armed-and-not-permitted differently from off-and-not-permitted"*; `:435` *"renders an unread %s as unknown and parks the thumb at min"* | **none — correct and deliberate.** The radio can still key from its own front panel (`:177-180`); the readout reports the radio, the controls report the permit |
| **P13** | **Unknown announced as not-pressed (MOR-1358) — TOGGLES ONLY, and they are done.** The defect is a two-state toggle emitting `aria-pressed="false"` over an unread reading, which claims "this control is OFF" about a fact the radio never reported. Every such toggle in the family now emits `undefined`: `TxAuxSurface:113`, `DspSurface:124`, `RitXitScanSurface:138,144,167`, `RfFrontEndSurface:183`, `CwKeyerSurface:208,247`, `ScopeControlsSurface:176`, `AntennaSurface:181` (`valueOf(ant.rxAnt)` — the same shape under a different name, missed by every `pressedOf` grep) | `parity` — closed | shared helper + unit pin at `semantic/pressed-of.ts` / `__tests__/pressed-of.test.ts`; DOM omission pins at `DspSurface.test.ts`, `RitXitScanSurface.test.ts` ×3, `CwKeyerSurface.test.ts` ×2, `AntennaSurface.test.ts:221`; mutation-killed 8× (verify-MOR-1358 §4) | **MOR-1358 built, NOT YET LANDED** at `0a1ecf60` (branch `codex/mor-1358-shared-pressedof`). Land it, then this row is closed. `ScopeControlsSurface` + `AntennaSurface` remain on local copies → **MOR-1383**, widened to two surfaces |
| **P13c** | **Choice groups are a DIFFERENT question, and `false` there is correct — see §3.5.** Sixteen mutually-exclusive options emit a definite boolean: six `aria-pressed` (`FilterSurface.svelte:114,129,160`, `BandSurface.svelte:187`, `DspSurface.svelte:165,177`) and **ten** `aria-checked` on `role="radio"` (`RfFrontEndSurface.svelte:130,151` PREAMP/ATT, `CwKeyerSurface.svelte:171,229`, `ScopeControlsSurface.svelte:120,133`, `RxAudioSurface.svelte:124,157,174`, `AntennaSurface.svelte:169`). "This option is not the current selection" is true even when the selection is unknown; the unknown is carried by `disabled` + the adjacent `<output>` rendering `?`/`—`. **Two groups stay live under an unread reading** — `AntennaSurface:169` (`disabled` = TX-block only) and `BandSurface:187` (`disabled={!receiverKnown}`) | `by design`, with a residual | `FilterSurface.test.ts:188,415` and `BandSurface.test.ts:334` assert `'false'` **and** `disabled` — they pin intended behaviour, not a bug | **not MOR-1358.** If the residual is worth a ticket it is "an unread choice group should disable", scoped to the two live groups. Do **not** omit `aria-checked` (required on `role="radio"`) and do **not** use `'mixed'` (unsupported there) |
| **P13r** | **Filter-selection highlight is unpinned (MOR-1304 N1 / mutant MF6).** `isSelected(modeFilter.currentFilter, index + 1)` (`FilterSurface.svelte:129`) is off-by-one-able: no test asserts *which* filter reads pressed, so `index` for `index + 1` survives. This is a **coverage** gap, not an aria-semantics one — the seed's row 11 cited it as MOR-1358 evidence and it was lost when the two were merged | `recorded lack` (test coverage) | `verify-mor-1304` N1/MF6; `FilterSurface.test.ts` has click tests at `:222,278` but no positive selection pin | **needs a one-line pin**, or fold into MOR-1358's landing |
| **P14** | **The workspace still cannot hide a zoned optional surface (MOR-1337), and this wave enlarges the residual by ten.** `zoneOwning()` returns `null` both when no manifest zone declares a surface and when a zone declared it and the workspace subtracted it (the plan handed in is post-subtraction, `resolution.ts:165`); `zoned()` renders `null` **bare** rather than withholding (`SemanticRadioSurfaces.svelte:652-660`). Subtraction costs the zone wrapper, never the readout — and never resurrects the legacy twin, which gates on the manifest (`RadioLayout.svelte:140-142`) | `recorded lack` | self-documented at `SemanticRadioSurfaces.svelte:129-136`; tests pin the wrapper *appearing* (`semantic-desktop-migration.component.test.ts:1273-1283`) and **none** asserts a hide | **MOR-1337.** The fix needs `SurfacePlan` to carry declaration and visibility separately |
| **P15** | **`sdr-test` double-presentation, final position.** `sdr-test` declares only `main: ['vfo','rxTx']` (`declarations.ts:21`), so all ten `declared`-channel legacy panels keep rendering **and** the twelve undeclared semantic surfaces render bare. **Exception the seed missed:** `TxPanel` does *not* survive there — `hideTxPanel` derives from `declared.has('vfo')`, which is true on `sdr-test`, so the TX panel is suppressed on the separate R9 channel | `recorded lack` — out of scope | `semantic-desktop-migration.component.test.ts:1049-1062` asserts all ten twins render on `sdr-test`; `RadioLayout.svelte:90,112` for the TX exception | **needs a ticket.** Per S10 §9(2), widen to cover the settings-modal family too: rows 1-6 of the S10 table are the same class; its rows 8-10 were never suppressed on any manifest and must **not** be counted in |
| **P16** | **Shells share components but not the suppression channel — three instances.** (a) `LcdLayout.svelte:55` mounts `<StatusBar />` with no `declared` prop, so `StatusBar.svelte:271`'s scope-indicator suppression can never fire under the three LCD skins; (b) `MobileRadioLayout.svelte:493,636` mount `<SpectrumPanel />` without `hideScopeControls` and `:689` mounts `<BandSelector />` without `hamBands` — the file imports no `declaredSurfaces`/`getLayout` at all; (c) both sidebars use one un-namespaced storage key each (`LeftSidebar.svelte:52` `rigplane:panel-order`, `RightSidebar.svelte:38` `rigplane:right-panel-order`), shared across every skin that mounts them | `recorded lack` — **structural** | file:line above; `RadioLayout.svelte:311,363,460` for the contrast; S11 doc for (c) | **MOR-1380.** One pattern, three instances — a component that reads a suppression channel must be given it by every shell that mounts it |
| **P17** | **`known-defaults` now permanently mis-describes `desktop-v2` (S11).** `drag-reorder.svelte.ts:48-49` records **every** default as "presented" on every load, regardless of the `declared` render gate, and `:67-78` treats a known-but-absent id as "deliberately removed by the user" | `changed derivation` | `drag-reorder.svelte.ts:44,48-49,67-78`; S11 doc §1.2 | **accepted divergence.** Precision note: the ten ids are recorded as **known**, not as **removed** — they are still in the stored order. The hazard fires only if a zone declaration is later withdrawn, and is recoverable via "Reset panel order" |
| **P18** | **`scopeControls.fixedEdge` — a registered leaf with no fact-layer home.** The wire produces it (`state_schema.py:167`, `runtime_helpers.py:204`, `lib/types/state.ts:108`) and the field-status fixture carries an entry for it, but no adapter or view model emits it and `validateScopeControls`'s `exactKeys` (`radio-view-model.ts:1513-1515`) lists twelve leaves without it | `by design` | exclusion reasoned in-file at `radio-view-model.ts:763-774` ("a composite validator with zero consumers is exactly the speculative surface 'no speculative keys' forbids"); restated at `ScopeControlsSurface.svelte:29` | **gap-register entry.** The wire field remains; the fact layer declines it deliberately |
| **P19** | **Status-bar scope indicator.** Retired on `desktop-v2` (`StatusBar.svelte:271`, `!declared.has('scopeDisplay')`). It carried a translated tooltip — `title={t('core.statusbar.indicator.scope', …)}` → *"Scope WebSocket: {state}"* — and a coloured dot (`:183-194`, green/yellow/red/dim). `ScopeDisplaySurface.svelte:44-55` reproduces the **tone taxonomy** but has no `title` (only `aria-label="Scope status"`, `:82`), and emits tone as a bare `data-tone` attribute with an explicitly colourless stylesheet (`:100-107`) | `recorded lack` (tooltip) + `changed derivation` (colour → design language) | file:line above | **owner batch.** The colour loss is the DL contract working as intended; the tooltip loss is not, and the readout is richer (SRC / health / HW on-off) than the dot it replaced |
| **P20** | **Spectrum-toolbar boundary.** Twelve fact-backed leaves retire under `hideScopeControls` (MODE, EDGE 1-4, SPAN, SPEED, HOLD, REF, DUAL + MAIN/SUB, the mobile REF row, and the settings-gear popover). **Nine** client-side view options render unconditionally: AVG, PEAK, BRT (×2 rows), colour scheme, BANDS, layers/region, EiBi, fullscreen — **plus `VIEW ON/OFF`**. The **STEP** tuning-step group (`:184-199`) also survives unconditionally but is not a view option and was never in the ruling's scope | `by design` | `SpectrumToolbar.svelte:40-73` states the rule; retiring set at `:213-284,323-330,393-401`, surviving set at `:180-212,293-347,348-406`; `SpectrumToolbar.component.test.ts` pins both directions | none. **Correction:** the S10 ruling names **eight** (`enableAvg`/`enablePeakHold`/`brtLevel`/`colorScheme`/`fullscreen`/`showBandPlan`/`hiddenLayers`/`showEiBi`, S10 §:93-94); there are **nine** — `VIEW ON/OFF` (`:205-212`) is not in the S10/S6b list. Do not count STEP into either number |
| **P21** | **11B derivation gaps — closed by verification.** The scope stepper clamps and the EDGE/SPAN predicate domain were unpinned; an unclamped `set_scope_span {span: 8}` could reach the wire. `ScopeControlsSurface.svelte:40,103,106,109` now routes every stepper through `clampSpan`/`clampSpeed`/`clampRef` and the predicates through `isSpanApplicable`/`isEdgeApplicable` | `parity` — closed | `verify-mor-1311` F1/F2; code as cited | **closed.** Kept in the matrix as evidence of method: a mutation battery, not a review, found it |
| **P22** | **Counting corrections that entered the record.** Broadcast presets are **16**, not 17 (`broadcast-presets.ts:11-16` 6 LW/MW + `:21-30` 10 SW; the 17th hit was the interface's own `name:` field at `:2`). Declared-retired panel ids on `desktop-v2` are **10**, not 11 (`band` still renders — it is retired *by prop*, `hamBands={!declared.has('band')}`, never by mount) | `parity` — bookkeeping | `BandSelector.svelte:18-19` states the 16 in-file; `semantic-desktop-migration.component.test.ts:1028-1029` is the authoritative ten-id literal | recorded; both corrections already propagated into the S8 and S11 artefacts |
| **P23** | **No drag-reorder for zone-owned surfaces.** `zoneOrder`/`visibleSurfaces` are workspace-settings-only; `SemanticRadioSurfaces` renders zone-owned surfaces in fixed source order with no drag handle. The operator loses the sidebars' reordering affordance for every surface a zone retires | `recorded lack` | S11 doc §1.4 / §4 | **deferred, unticketed.** Route through MOR-1162 if picked up — see §9 |

---

## 3. Where the evidence contradicted the seed

Six corrections. Each was a belief the program held going into this slice.

### 3.1 MOR-1282 peak-hold had already landed (P1)

Both seed matrices list it as a `recorded lack`. It is `parity` at `7612a4f8`. The behaviour did not
get re-implemented in `MetersSurface` — it moved down into the shared `BarGauge`, which both the
semantic surface and the legacy dock now sit on, and `MetersSurface.test.ts:493` asserts exactly
that ("…are on the same peak-hold channel"). Copying the seed row unread would have shipped a false
regression claim into the cutover review.

### 3.2 "42 `t()` calls" is a grep artefact (P5, §5)

The number is a raw substring count of `t(`. It counts `getShortcutHint(`, `parseInt(`, `setTimeout(`,
`repeat(`, `onBandSelect(`, `getBoundingClientRect(` and friends. Nine of the eleven "retired
components carrying `t()` calls" do not import `t` at all. Full method and the corrected numbers in §5.

### 3.3 The `?` placeholder belongs to the *new* surface, not the old one (P3)

S5-N1 was recorded as "the dock showed `?` placeholders, the surface shows nothing". The dock has no
`?` anywhere in its markup; its policy is omission (`MetersDockPanel.svelte:234-235`), and with zero
meter fields it renders a titled, empty `STATION METERS` shell. The `?` is `MetersSurface`'s own
per-field honest-unknown rule (`:21-22,132,150`). The honest statement of the row is therefore:
**whole-group absent → the dock showed empty chrome, the surface shows nothing; per-field absent →
the dock omitted the tile, the surface shows `?`.** The direction of the change is the opposite of
the one recorded on one axis and better than recorded on the other.

### 3.4 The receiver deck does not scroll (P7, §4)

Both seeds describe a "280px `overflow:auto` receiver deck". The 280px is real, but it is a
**grid-row height** (`RadioLayout.svelte:509-511`), and `.receiver-deck` is
`overflow: hidden` (`:558`). `overflow-y: auto` at `:581` belongs to `.content-left, .content-right`
— the sidebars. This matters: the accepted consequence is not "ten surfaces you can scroll through",
it is "ten surfaces in a fixed 280px box that clips".

### 3.5 The seed's MOR-1358 row conflated three different things (P13 / P13c / P13r)

Corrected by the independent MOR-1358 verification (`verify-mor-1358`, which adjudicated this row
against the built fix). The seed cited `verify-mor-1304` N1 and `verify-mor-1306` N1 under one
heading; those two reports are about different defects, and a third population was then swept in.

1. **Toggles emitting `aria-pressed="false"` over an unread reading — the real MOR-1358.** A toggle
   has "unread" and "off" as distinguishable states, so `"false"` there is a fabricated positive
   claim. **This is fixed.** All eleven toggles in the family emit `undefined`; the rule now lives in
   one shared helper with a unit pin plus DOM omission pins on four surfaces, and it survives an
   eight-mutant battery. Note `AntennaSurface.svelte:181` (`valueOf(ant.rxAnt)`) is an eleventh
   toggle that every `pressedOf` grep in this program missed — it was already correct.
2. **Choice groups are not the same defect.** For a mutually-exclusive group, `false` on a
   non-selected option is a *true* statement — "this option is not the current selection" holds
   whether or not the selection is known — and the unknown is communicated by `disabled` plus the
   adjacent `<output>` rendering `?`/`—`. Two further points make the "fix it the same way" reading
   wrong on the merits: `aria-checked` is a **required** property of `role="radio"`, so omitting it
   is invalid ARIA rather than honest; and `aria-checked="mixed"` is **not a supported value on
   `role="radio"`**. The in-repo exemplar the seed pointed at — `VfoSurface.svelte:244-247`'s
   `triState` — sits on `role="switch"` (`:385,:397`), a different role with a different contract,
   so it is not transferable to the ten radio sites. The three tests asserting `'false'` assert
   `disabled` alongside it: they pin intended behaviour, and MOR-1358's built fix deliberately and
   correctly excludes `FilterSurface` for exactly this reason.
3. **The filter-selection off-by-one (MOR-1304 N1 / MF6) is a coverage gap**, not an aria-semantics
   one, and it is still open. It is now P13r rather than being absorbed.

**Count correction:** the `aria-checked` population named here is **ten** sites, not nine (2 + 2 + 2
+ 3 + 1); the family carries twelve `aria-checked` bindings in total, the other two being
`VfoSurface`'s tri-state switches.

### 3.6 The sidebars keep `band`, not `tx` (P7, §4)

The planning brief said the sidebars are left holding `audio-scope`, `memory` and `tx`. `tx` is
suppressed on `desktop-v2` — `hideTxPanel` derives from `declared.has('vfo')`
(`RadioLayout.svelte:90,112`) and is true. The third survivor is `band`, in its broadcast half only.
The later handoff had this right; the brief did not.

---

## 4. Deck relocation — accepted, with the consequence written down

`desktopV2Layout` declares **14 zones, one surface each** (`desktop-declarations.ts:36-131`), which
is the entire `SEMANTIC_SURFACE_NAMES` vocabulary (`contract.ts:29-32`). In manifest terms the zone
literally named `receiver-deck` owns exactly one surface, `vfo` (`:37`); in DOM terms the
`<section class="receiver-deck">` hosts all fourteen, because it mounts `<SemanticRadioSurfaces />`
(`RadioLayout.svelte:314-316`). Both statements are true and they are routinely confused — the count
that matters to an operator is **fourteen surfaces in one box**.

Geometry, corrected (§3.4): `.radio-layout.semantic-deck` sets `grid-template-rows: 28px 280px
minmax(0,1fr) auto` (`RadioLayout.svelte:509-511`, restated for ≥1680px at `:515-517`; the
non-semantic baseline is 200px at `:507`), and `.receiver-deck` is `overflow: hidden` (`:558`).

What is left in the sidebars, verified gate by gate:

| sidebar | survivor | why it survives |
|---|---|---|
| left | **BAND** (broadcast half only) | `LeftSidebar.svelte:107-113` has no mount gate; the HAM half retires through the `hamBands={!declared.has('band')}` prop (`:111`), because a mount gate "would orphan the presets" (`:99-106`) |
| right | **AUDIO SCOPE** | `RightSidebar.svelte:51` — capability-gated on `hasAudioFft()` only, never on `declared` |
| right | **MEMORY** | `RightSidebar.svelte:75` — ungated; not a semantic family |

Everything else is gone: RF FRONT END, MODE, FILTER, AGC, RIT/XIT, ANTENNA, SCAN, RX AUDIO, DSP, CW
(the ten of `RETIRED_ON_DESKTOP_V2`, `semantic-desktop-migration.component.test.ts:1028-1029`), plus
TX on the separate R9 channel. The live-DOM inventory test at `:717-726` asserts the resulting
`desktop-v2` panel id list is `['band','desktop-language','desktop-vfo-ops','desktop-workspace',
'memory','memory']`.

**Disposition: accepted with consequence**, under the MOR-1263 owner decision. It is recorded here —
not resolved here — so that MOR-1099 reviews a stated trade rather than discovering it. The trade is:
fourteen control-bearing surfaces stacked in a clipped 280px band, two near-empty sidebars, and no
placement mechanism in this program. Placement is MOR-1162's problem; P23 is its companion row.

---

## 5. The translation delta, counted properly

**Method.** A real `t()` call site is `t(` not preceded by an identifier character or a `.`, in a
file that imports `t` from `$lib/i18n`. The seed's number is `grep -o 't(' | wc -l`. Both were run
over the same eleven files; the difference is the whole finding.

| component | seed (`raw t(`) | real call sites | imports `t`? |
|---|---:|---:|---|
| `ModePanel.svelte` | 9 | **3** | yes |
| `FilterPanel.svelte` | 8 | 0 | no |
| `DspPanel.svelte` | 9 | 0 | no |
| `BandSelector.svelte` | 6 | 0 | no |
| `RfFrontEnd.svelte` | 3 | 0 | no |
| `RitXitPanel.svelte` | 3 | 0 | no |
| `RxAudioPanel.svelte` | 3 | **1** | yes |
| `ScanPanel.svelte` | 1 | 0 | no |
| `AgcPanel` / `AntennaPanel` / `CwPanel` | 0 | 0 | no |
| **total** | **42** | **4** | 2 of 11 |

The 38 phantom hits are ordinary identifiers ending in `t`: `getShortcutHint(`, `onBandSelect(`,
`bandShortcut(`, `setTimeout(`, `clearTimeout(`, `toggleNrShort(`, `getBoundingClientRect(`,
`repeat(`, `gradient(`, `effect(`.

**The semantic family does not carry zero.** `VfoSurface.svelte` imports `t` (`:34`) and has **18**
real call sites over **14 distinct `core.vfo.*` keys**, all 14 translated in `ru-RU` and `ja-JP`.
(Re-counted independently in `verify-mor-1372`: 21 raw `t(` hits, 18 real, 14 keys.) The other
thirteen of the fourteen semantic surfaces hardcode English.

**What an operator on `ru-RU`/`ja-JP` actually loses on `desktop-v2`:** three keys, all currently
translated in both locales.

| key | en-US | where it was | replacement |
|---|---|---|---|
| `core.modePanel.modInputLabel` | `MOD IN` | `ModePanel.svelte:103` | `RxAudioSurface.svelte:188` renders `MOD:` — hardcoded |
| `core.modePanel.modInputAria` | `Modulation input source` | `ModePanel.svelte:107,108` | none (no aria-label on the semantic row) |
| `core.overlay.audioLinkLost` | `Audio link lost — reconnecting…` | `RxAudioPanel.svelte:54` | **none** — see P6 |

`npm run i18n:check` → `OK (3 locale file(s) checked, 204 source keys)`, with `ru-RU`/`ja-JP` each
missing the same 19 `core.settings.workspace.*` keys (informational; missing keys fall back to
English).

**Retarget MOR-1373 accordingly.** It is not "42 strings regressed". It is (a) three keys with no
translated home, one of which has no home at all, and (b) a policy question: thirteen semantic
surfaces hardcode English while `VfoSurface` proves the family *can* be translated. (b) is the
expensive half and the one that needs an owner decision.

---

## 6. Safety adjudications

P10, P11 and P12 are the rows that change what an operator may believe about whether the radio can
transmit. **This document's author must not be their reviewer** (S12 S-class rule). For the
independent reviewer, the three questions are:

1. **P10** — is "band-scoped TX permit computed under an unobserved active receiver, silently
   defaulting to MAIN" acceptable until MOR-1356 lands? Note the second-order finding: the freshness
   asymmetry is doubled, because `topFieldAvailable` → `isFieldAvailable`
   (`lib/state/field-status.ts:63-68`) checks only `availability`, dropping the `observed`/`fresh`
   legs `seen()` requires. No test exercises the ungated path.
2. **P11** — is an allow-list of blockers the right shape at all? The four omitted reasons are
   omitted deliberately and reasonably, but nothing binds a future eighth `KeyBlockedReason` to a
   decision. MOR-1361 should be scoped as "add an exhaustiveness gate", not "add the four".
3. **P12** — confirm the ruling stands: posture reports the **radio**, the controls report the
   **permit**, and showing "ARMED" next to disabled buttons is correct because the operator's radio
   can still key from its own front panel.

P9 (raw scan integers) is the fourth carry-forward and is an **owner batch item**, not a ticket: it
is deliberate, self-documented, and a label-table port is a v2-UI concern the facts-only doctrine
deliberately declined.

---

## 7. Harness evidence

Two full `capture.mjs` runs at `7612a4f8`, same worktree, **same port 5199**, out-dirs distinct.
Read §1 first: this measures the harness's determinism, **not** `desktop-v2` parity.

```
node fixtures/capture.mjs --out <dir> --port 5199
```

(There is no npm script; `capture.mjs` starts its own vite server from `vite.fixtures.config.ts` and
closes it, so no external dev server is needed.)

**Claim, in the MOR-1351 shape: 61 captures, 0 invalid, 1830/1830 assertions, zero ok-flips.**

Beyond the required claim, the two `manifest.json` files are **identical except `generatedAt`** — a
key-by-key diff of every top-level field, every capture record (`valid`, `assertionsPassed`,
`assertionsTotal`, `consoleErrors`, `focusPath`, `tokens`, `paint`) and every individual assertion
object produced **zero** differences. **The explicit list of assertions whose value moved is
therefore empty.**

The expected large movement the S12 brief predicted — 33 zone-less controls becoming zoned,
`zonelessControls` pins flipping, antenna ports going `[disabled]` with `tuner-not-ready` — **did not
occur, and could not have.** MOR-1355's plan-ful fixture is a single opt-in capture
(`topology-2-main-sub--planned--desktop`) resolving the **cockpit** manifest, and it was already the
baseline shape before these two runs. The prediction assumed a harness that resolves
`desktopV2Layout`; §1 explains why none exists.

**Environment note for whoever re-runs this:** one of three attempts died mid-run — once on a
Chromium crash during a screenshot (`capture.mjs:440`) and once on SIGKILL (exit 137). Neither is a
determinism signal; both are resource pressure. Re-run before drawing any conclusion from a partial
manifest.

Manifest metadata worth carrying: `ticket` is still `MOR-1070`, `harness.productionFilesChanged` is
`0`, and `harness.stubbedSeams` is four entries (`$lib/runtime`, the tx-controller app-host, the
mod-input TX guard, the command bus).

---

## 8. Closure statements

### 8.1 MOR-1317 — closed program-wide

Verified directly rather than inferred from an empty ledger (`verify-mor-1370` §3, re-checked here):

| assertion | result |
|---|---|
| `declaredSurfaces(desktopV2Layout)` ⊇ every `SEMANTIC_SURFACE_NAMES` member | zero missing |
| `owned.size === SEMANTIC_SURFACE_NAMES.length` (14) | ✔ |
| `desktopV2Layout.zones.length === 14` | ✔ — one zone per surface |
| `zoneOwning('scopeControls') === 'scope-controls'` on a default workspace | ✔ |
| the same on a pre-S6b-2 stored workspace that never heard of the zone id | ✔ (the migration case) |
| the cockpit does **not** own `scopeControls` | ✔ — manifest untouched |

`zone-ownership-coverage.test.ts` remains a live guard rather than a tautology: with zero excused
entries the partition still fails the moment a fifteenth name is appended to
`SEMANTIC_SURFACE_NAMES`.

### 8.2 MOR-1263 §3 — the planner's resolved-item list

| item | disposition at S12 |
|---|---|
| S6 split into S6a + S6b-1/S6b-2 | done as planned |
| Spectrum-toolbar boundary (client-side view options stay legacy) | done — P20, with the nine-not-eight correction |
| MOR-1355 sequenced immediately before S12 | landed (`fe01a1f1`); §1(2) records what it does and does not reach |
| MOR-1317 closes at S9 (sidebars) and S6b-2 (scope) | closed — §8.1 |
| MOR-1339 encoded as §1.5 in every zone brief | held; the exactly-one-instance pins are live (`semantic-desktop-migration.component.test.ts:1359,1432`; `cw-keyer-zone-lifecycle.component.test.ts:207`) |
| S10 first, S6b seventh | held |
| 3-file guardrail waived once, explicitly, in S6-pre | held — one waiver, named at landing |

### 8.3 This slice

0 production LOC. One new documentation file; no test, no source, no locale file changed.

---

## 9. Open items handed to the owner / MOR-1099

1. **Two unticketed deferrals must be filed or explicitly confirmed as deferred before MOR-1263
   closes.** (a) MOR-1162 routing for zone drag-reorder (P23 / S11 §1.4); (b) the MOR-1294
   confirmation named as S8's landing gate. Both currently read "route it if picked up", which is
   not a disposition.
2. **P6 (audio-link-lost) has no ticket.** It was found by this slice.
3. **P15 needs a ticket, and its language needs widening** past meters to the settings-modal family
   (S10 §9(2)), excluding that table's rows 8-10, which were never suppressed on any manifest.
4. **MOR-1373 needs retargeting** from "42 untranslated strings" to §5's actual shape.
5. **MOR-1380** — file the cross-skin channel pattern as one structural ticket with three instances
   (P16), not three pieces of trivia.
6. **MOR-1358 must land before this row closes.** The fix is built and independently verified at
   `0a1ecf60` but is not on `main` (P13). **MOR-1383 must be widened to two surfaces**
   (`ScopeControlsSurface` *and* `AntennaSurface`, which carries the same shape under the name
   `valueOf`). **P13r** — the unpinned filter-selection highlight — needs a one-line test pin.
7. **Harness widening** — mounting `RadioLayout` under `desktopV2Layout` in `fixtures/main.ts` is the
   only thing that would turn §7's zeroes into `desktop-v2` evidence. Not in this slice; MOR-1090's
   pixel gate is currently the only visual authority over the reworked skin.

---

## 10. Environment

Node `v26.4.0`, Playwright `1.58.2`, Svelte `5.55.8`, jsdom via vitest `4.1.0`.

- **This Node/jsdom pairing needs both `--experimental-webstorage` and `--localstorage-file`.**
  Without them a large class of pre-existing tests fails on a missing `localStorage` global, which
  reads as a mass regression. Always use a fresh dedicated file per run.
- **Three standing flakes**, each reproduced on unmodified `main` and none caused by this wave:
  `mod-input-tx-guard.isolated > arms the guard at startTx and still delegates to runtime`
  (green when run isolated); `semantic-lcd-migration > "scope" resolves to the migrated LCD
  entrypoint`; and an intermittent `semantic-desktop-migration` inventory row.
- **Gates run for this document** (fresh dedicated localStorage file):
  `npx vitest run src/semantic src/components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts src/components-v2/wiring`
  → **62 files, 1647 tests, 0 failures**; `npm run i18n:check` → `OK (204 source keys)`.
