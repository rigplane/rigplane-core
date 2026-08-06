# Settings-modal boundary for the v3 zone-ownership wave

**Date:** 2026-08-06

**Status:** Accepted

**Ticket:** [MOR-1363](https://linear.app/morozsm/issue/MOR-1363) (v3-rework slice S10, tail of
MOR-1263)

**Implementation status:** Ruling only. Zero production LOC in this change. It governs the modal
half of S6-pre (`feat(MOR-1263): manifest-driven legacy-twin suppression channel`) and is consumed
unmodified by S7/S9. **S8 is the one exception:** beyond consuming this ruling, S8 also implements
the `BandSelector` `hamBands` component split and wires row 6's predicate (§4a) — a small, named
rider on S8's own `band`-zone scope, not a separate change.

**Scope:** the Core Settings modal hosted by `frontend/src/components-v2/layout/RadioLayout.svelte`
(mounted from `<StatusBar onSettings=…>`, lines 362-436 on `main`@`643e9cb2`).

**Extends:** `docs/plans/2026-07-25-ui-composition-architecture-v3.md` §"zone ownership";
supersedes nothing.

---

## 1. What the modal is

`RadioLayout.svelte` renders one settings modal, unconditionally on `settingsOpen`, regardless of
which manifest resolved the surrounding layout. Reading the file directly (not assumed) it contains
exactly **eight** `<CollapsiblePanel>` sections:

| `panelId` | title | content |
|---|---|---|
| `desktop-language` | LANGUAGE | `<LanguageSelector />` |
| `desktop-workspace` | WORKSPACE | `<WorkspaceSettingsPanel />` + `<WorkspaceImportExport />` |
| `desktop-vfo-ops` | VFO / BAND | a `SPLIT` / `A↔B` / `A=B` `<HardwareButton>` row, **then** `<BandSelector />` (itself a 3-tab switch — HAM grid, LW/MW presets, SWL presets — see §3 rows 6 and 10) |
| `desktop-dsp` | DSP | `<DspPanel />` |
| `desktop-agc` | AGC | `<AgcPanel />` |
| `desktop-rf` | RF FRONT END | `<RfFrontEnd />` |
| `desktop-rit` | RIT / XIT | `<RitXitPanel />` |
| `desktop-cw` | CW | `<CwPanel />` |

(`desktop-vfo-ops` carries **three** independently-ruled controls, not two — the button row (row 7),
and `BandSelector`, which itself splits into two independent rulings: its HAM grid (row 6, duplicates
`BandSurface`) and its LW/MW + SWL broadcast-preset tabs (row 10, deliberately *not* duplicated by any
semantic surface — see §4a). So the count of *rulings* below is **ten**, two wider than the six panels
the coordinator's brief named plus the VFO-ops row it flagged. See §5 for why "seven sections" in the
upstream brief undercounts: it did not separately count `desktop-language`/`desktop-workspace`, and it
did not know `desktop-vfo-ops` bundles three independent rulings rather than one.)

No `MODE`/`FILTER`, `ANTENNA`, `SCAN`, or `RX AUDIO` section exists in the modal — those five
sidebar families have **no** modal twin at all. `LeftSidebar.svelte`/`RightSidebar.svelte` were read
directly to confirm each `panelId` importing the identical component module the modal imports
(e.g. `RadioLayout.svelte` imports `BandSelector` from `../controls/BandSelector.svelte` — the same
path `LeftSidebar.svelte` imports). The modal is therefore not a parallel implementation of these
six controls; it is a **third literal mount of the same component**, alongside the sidebar twin and
(where landed) the semantic surface.

**Is the modal a `desktop-v2` concern only?** The component (`RadioLayout.svelte`) is shared with
`sdr-test` (`SdrTestSkin.svelte:19` mounts `<RadioLayout skinId="sdr-test" />`), so the *markup*
exists on both skins. `sdr-test`'s registered manifest (`presentation/layouts/declarations.ts:21`)
declares exactly one zone, `{ id: 'main', surfaces: ['vfo', 'rxTx'] }` — it declares none of `filter`,
`rfFrontEnd`, `band`, `antenna`, `ritXitScan`, `dsp`, `cwKeyer`, so rows 1-6's predicates
(`declared.has('dsp'|'rfFrontEnd'|'ritXitScan'|'cwKeyer'|'band')`) evaluate `false` forever on
`sdr-test` and those sections render unchanged there. **Row 7 is the one exception, and it is not
inert on `sdr-test` either: its predicate is `semanticDeck = declared.has('vfo')`
(`RadioLayout.svelte:90`), and `sdr-test` *does* declare `vfo` in its one zone — so row 7 retires on
`sdr-test` exactly as it does on `desktop-v2` (see §4). Do not describe row 7 as inert there; it is
the single row this doc changes on both skins at once.** Rows 8-10 have no predicate at all (n/a —
permanent, on every manifest). **Practical effect: this ruling retires row 7 on both `desktop-v2` and
`sdr-test`, and retires rows 1-6 on `desktop-v2` only; `sdr-test` keeps rows 1-6 plus the permanent
rows 8-10 — nine of this doc's ten ruled items — matching the S5-N2 precedent (`sdr-test` still
double-presents meters) for the six that remain suppressible there — track it as the same open class,
not a new one.**

`mobile`/`lcd-cockpit` render their main content through `MobileRadioLayout.svelte`/`LcdLayout.svelte`,
but the modal's `{#if settingsOpen}` block (`RadioLayout.svelte:363`) sits in `RadioLayout.svelte`
itself, **outside** that skin branch. The modal markup is therefore not structurally absent on
mobile/LCD — it is **unreachable**: `settingsOpen` is only ever set by `StatusBar`'s `onSettings`
handler (`RadioLayout.svelte:284`), which is mounted only on the desktop branch, so nothing on
mobile/LCD can flip it today. This is a reachability fact, not an absence fact — worth remembering
before a future change hoists `settingsOpen` or binds a shortcut to it, at which point the modal would
render on those skins with §3's predicates evaluated against **their** manifests.

---

## 2. Category definitions

- **(a) retires with zone ownership** — the modal section duplicates a semantic surface that a named
  rework slice declares (or has already declared) a `desktop-v2` zone for. Naming the slice is
  mandatory.
- **(b) stays modal-owned, client-side, no facts** — the section's controls have no backend wire
  field, no field-status entry, and never will (they configure the browser session or the UI itself,
  not the radio). Enumerated exactly, per the `S6b` scope-toolbar precedent (client-side view
  options — `enableAvg`/`enablePeakHold`/`brtLevel`/`colorScheme`/`fullscreen`/`showBandPlan`/
  `hiddenLayers`/`showEiBi` — stay legacy because they are not `scopeControls` facts).
- **(c) stays modal-owned pending a future facts family** — would retire if a named facts family
  existed, but none does yet. Name what facts would be required.

---

## 3. The ruling table

| # | section (`panelId`) | duplicates | category | zone / slice | suppression predicate |
|---|---|---|---|---|---|
| 1 | `desktop-dsp` (`DspPanel`) | `DspSurface` (NR/NB/notch) | **(a)** | `dsp` zone — **S9** | `declared.has('dsp')` |
| 2 | `desktop-agc` (`AgcPanel`) | `DspSurface` (AGC leaf — 5A/MOR-1290 folds AGC into `dsp`) | **(a)** | `dsp` zone — **S9**, same predicate as #1 | `declared.has('dsp')` |
| 3 | `desktop-rf` (`RfFrontEnd`) | `RfFrontEndSurface` | **(a)** | `rf-front-end` zone — **S7** | `declared.has('rfFrontEnd')` |
| 4 | `desktop-rit` (`RitXitPanel`) | `RitXitScanSurface` | **(a)** | `rit-xit-scan` zone — **S8** | `declared.has('ritXitScan')` |
| 5 | `desktop-cw` (`CwPanel`) | `CwKeyerSurface` | **(a)** | `cw-keyer` zone — **S9** | `declared.has('cwKeyer')` |
| 6 | `desktop-vfo-ops` → `BandSelector` **HAM tab + band grid only** | `BandSurface` (`band-choices`/`band-entry`) | **(a)** | `band` zone — **S8** (component split lands with S8, MOR-1367; see §4a) | `declared.has('band')` — wiring lands with S8, **not** S6-pre |
| 7 | `desktop-vfo-ops` → `SPLIT`/`A↔B`/`A=B` row | `VfoSurface`'s own split-toggle/swap/equalize controls (`data-vfo-split`/`data-vfo-swap`/`data-vfo-equalize`, `VfoSurface.svelte`) | **(a)** | `receiver-deck` zone — **already landed** (MOR-1313, v3-rework S1/S2) | `semanticDeck` (existing `RadioLayout.svelte:90` derived value) — **see §4, non-inert exception** |
| 8 | `desktop-language` (`LanguageSelector`) | — no sidebar twin, no semantic surface | **(b)** | n/a | n/a — never suppressed |
| 9 | `desktop-workspace` (`WorkspaceSettingsPanel` + `WorkspaceImportExport`) | — no sidebar twin, no semantic surface | **(b)** | n/a | n/a — never suppressed |
| 10 | `desktop-vfo-ops` → `BandSelector` **LW/MW tab + SWL tab** (16 curated presets, `broadcast-presets.ts`) | — no semantic surface; **deliberately** excluded (`semantic/radio-view-model.ts:494-496`, verbatim: *"UI convenience, not radio facts, … deliberately absent"*) | **(b)** | n/a | n/a — never suppressed, on any manifest; see §4a for the coordinator ruling |

Every semantic-surface copy the modal hosts appears in exactly one row. No row is category (c): every
duplicative control in this modal already has either a landed zone (#7) or a zone a named slice is
about to declare (#1-6, with #6 now scoped to the HAM half only); every non-duplicative control
(#8-10) is permanently outside the radio-facts vocabulary — LANGUAGE and WORKSPACE (#8-9) because
there is no radio-side concept of a UI-language or workspace/theme/layout choice, and the LW/MW/SWL
broadcast-preset tabs (#10) because the vocabulary itself excludes them by name, not by omission — so
(c) does not apply to any of them.

### Row 8/9 detail — why (b), not (c)

- **LANGUAGE** (`LanguageSelector.svelte`): a `<select>` over `SUPPORTED_LOCALES`, writing through
  `$lib/i18n`'s locale store. Purely a browser-session UI preference (BCP-47 locale code). No wire
  field, no field-status entry, no radio involvement of any kind.
- **WORKSPACE** (`WorkspaceSettingsPanel.svelte` + `WorkspaceImportExport.svelte`): layout /
  design-language / theme / density selection, workspace reset, and workspace JSON import/export —
  all through `presentation/workspace/store.svelte.ts`. This is configuration *of the UI
  configuration system itself* (MOR-1080). It is definitionally client-side and can never gain a
  facts family, because there is no radio-side concept of "which layout the operator's browser
  renders."

These two sections are the closest analogue in this modal to the S6b ruling's client-side view
options (`enableAvg`, `brtLevel`, `colorScheme`, …): no wire field now, and structurally none is ever
possible. Category (b) is exact, not a default.

### Row 10 detail — why (b), and why it is not row 6

`BandSelector.svelte` (`components-v2/controls/BandSelector.svelte:37-100`) is a three-tab switch —
`HAM` / `LW/MW` / `SWL` — over `bandMode = $state<'ham'|'broadcast'|'lwmw'>`. Only the `ham` branch
iterates `flattenBands(caps.freqRanges)`, the same source `deriveBand`
(`lib/runtime/adapters/radio-view-model-adapter.ts:668-692`) uses to build `BandSurface`'s
`band-choices`. The `LW/MW` and `SWL` branches iterate `BROADCAST_LW_MW_BANDS`/`BROADCAST_SW_BANDS`
from `components-v2/controls/broadcast-presets.ts` — 16 curated presets, each firing
`presetH.onPresetSelect(preset.freq, preset.mode)` (a frequency+mode intent, not a band selection).

The vocabulary comment for `bandChoices` states the exclusion directly
(`semantic/radio-view-model.ts:489-496`, verbatim): *"read from the `caps` ARGUMENT only, via the
shipped `flattenBands` — never a frontend band table (the shipped `BandSelector.svelte`'s hard-coded
`BROADCAST_SW_BANDS`/`BROADCAST_LW_MW_BANDS` presets are UI convenience, not radio facts, and are
deliberately absent)."* `grep -rn "BROADCAST_SW_BANDS|BROADCAST_LW_MW_BANDS|onPresetSelect"` over `src/`
confirms `BandSelector.svelte` is the only production consumer — there is no second host anywhere in
`desktop-v2` that could pick this up if row 6's suppression naively took the whole component with it.

This is why row 10 is not folded into row 6: row 6 is a genuine (a)-category duplicate (the HAM grid
mirrors `BandSurface` exactly), but the broadcast tabs are a genuine (b) — client-side curated data,
no wire field, no field-status entry, and a vocabulary comment that closes the door on a future facts
family by design (not a "not modelled yet" gap — see §4a for the ruling this forces on row 6's
implementation).

---

## 4. The row-7 exception — the one non-inert item S6-pre must land deliberately

Rows 1-5 are exactly the shape S6-pre's inertness proof expects: each predicate
(`declared.has('dsp')`, `declared.has('rfFrontEnd')`, `declared.has('ritXitScan')`,
`declared.has('cwKeyer')`) reads a zone **no manifest declares yet** (S7/S9 have not landed), so every
one evaluates `false` today and the rendered tree is unchanged the moment S6-pre lands — the whole
point of landing the channel inert. **Row 6 (`declared.has('band')`) is the same shape in spirit, but
S6-pre does not wire it at all — that wiring, and the `BandSelector` component split it requires, land
with S8 instead (§4a). S6-pre's inertness proof for the modal covers rows 1-5 and 7 only.**

**Row 7 is different, and S6-pre must say so explicitly rather than let it hide inside the "nothing
changes" claim.** `receiver-deck`/`vfo` is not a future zone — it is the zone MOR-1313 (v3-rework
S1/S2) already declared, and `semanticDeck = $derived(declared.has('vfo'))` (`RadioLayout.svelte:90`)
is **already `true` on `desktop-v2` today**. `RadioLayout.svelte:288` already branches on it: when
`semanticDeck` is true, the legacy `<VfoHeader>` — which is what used to own the `SPLIT`/`A↔B`/`A=B`
row via its `splitActive`/`onSplitToggle`/`onSwap`/`onEqual` props — is **already replaced** by
`<SemanticRadioSurfaces />`, whose `VfoSurface` already renders equivalent, translated
(`t('core.vfo.split.label')`, etc.) split/swap/equalize controls. Only the settings-modal's copy of
that row was never gated when MOR-1313 landed.

Wrapping the modal's row in `{#if !semanticDeck}` is therefore a **real, immediate, observable
change on `main` the day S6-pre merges** — not inert plumbing. It is a translation improvement (it
removes the modal's three hardcoded, untranslated `SPLIT`/`A↔B`/`A=B` strings in favor of the
semantic surface's already-translated equivalents, and it removes a redundant TX-adjacent-looking
control surface that is not one — see §6). **It also carries a second, independent behavioral delta,
not a translation one — a fail-closed disable on unknown split status — recorded as its own S12
parity-matrix row rather than folded into "strict improvement"; see §6.**

**Ruling: land it in S6-pre, in the same PR, but as a named, called-out exception — not folded
silently into the "nothing renders differently" inertness claim.** Concretely:

1. Gate the row on the existing `semanticDeck` boolean already in scope in `RadioLayout.svelte` — no
   new prop, no channel plumbing (this is a same-file `{#if}`, unlike rows 1-5 which cross into
   `LeftSidebar`/`RightSidebar`/`StatusBar`). Wrap only the inner `SPLIT`/`A↔B`/`A=B` row, **not** the
   `desktop-vfo-ops` `<CollapsiblePanel>` itself — see §7 for why that panel can never be wrapped as a
   whole once row 10 exists.
2. Add one dedicated before/after test asserting the row is present when `semanticDeck` is `false` and
   absent when `semanticDeck` is `true` (today's `desktop-v2`, and — per §1 — `sdr-test` too, since
   `sdr-test` already declares `vfo`). **No registered manifest has `semanticDeck === false` today**:
   `sdr-test`, `desktop-v2`, `dual-receiver-cockpit`, `mobile`, `lcd-cockpit`, and `lcd-scope` all
   declare `vfo` (verified directly against every file under `presentation/layouts/*.ts`). Do **not**
   use `sdr-test` as the `false` fixture. The `false` fixture must be synthetic — pick one and name it
   explicitly in the test file:
   - a synthetic manifest registered for the test only, declaring every other
     `requiredSemanticSurfaces` entry the fixture needs but omitting `vfo` from `surfaces` (the S2
     `probe-vfo-only`-style recipe, inverted); or
   - rendering `RadioLayout` with an unregistered `skinId`, so `getLayout(id)` returns `undefined`
     (`contract.ts:243`) and `declaredSurfaces(undefined)` resolves to the empty set
     (`contract.ts:289-297`).
3. State the exception in the S6-pre PR body next to the inertness-proof table, in the same style the
   12B waiver was recorded: one call-out line, not a footnote.
4. Do **not** report this as part of the "~55-70 net production LOC, nothing renders differently"
   estimate's *inertness* claim — report it as the one exception, with its own before/after evidence.

---

## 4a. The row-6/row-10 split — `BandSelector`'s HAM/broadcast boundary (coordinator ruling)

§3 row 6 originally ruled `BandSelector` as a whole (a): duplicated by `BandSurface`, retiring with
the `band` zone. That is wrong for two of the component's three tabs — see §3's Row 10 detail. The
vocabulary (`semantic/radio-view-model.ts:494-496`) *deliberately* excludes the LW/MW and SWL
broadcast-preset tabs (16 presets, `broadcast-presets.ts`) from `bandChoices`; ruling row 6 as (a) for
the whole component would have suppressed those tabs the moment `declared.has('band')` went true on
`desktop-v2`, and — since `BandSelector.svelte` is the **only** production consumer of
`BROADCAST_LW_MW_BANDS`/`BROADCAST_SW_BANDS`/`onPresetSelect` — deleted an operator affordance no
semantic surface replaces, with no remaining host anywhere in `desktop-v2`.

**Coordinator ruling (recorded here, not left open): option (A) — split the component.**
`BandSelector` gains a `hamBands?: boolean` prop (default `true`). The suppression predicate for row 6
becomes *pass `hamBands={!declared.has('band')}`* rather than *omit the component*: the `HAM` tab and
grid disappear when the `band` zone is declared, the `LW/MW`/`SWL` tabs and their 16 presets survive
unconditionally. This is the only one of the three options considered (§3 Row 10 detail; the
alternatives were retain-whole, which reopens double-presentation, and accept-the-loss, which deletes
the affordance) with **zero** operator loss.

**Implementation lands with S8, not S6-pre.** S8 already owns the `band` zone (row 6's `zone / slice`
column); the `hamBands` split is a one-file rider on that same slice, not a new S6-pre file. **S8's
brief (MOR-1367) is amended to include this item** — it previously listed five other band/RIT/scan/
antenna parity carry-forwards and did not include the broadcast tabs; this doc is the record that adds
it. Concretely, for S8:

- add `hamBands?: boolean = true` to `BandSelector.svelte`'s props; gate the `ham` tab/grid rendering
  and the `bandMode === 'ham'` default on it; leave the `LW/MW`/`SWL` branches unconditional.
- wire both hosts — the settings-modal row 6 (`RadioLayout.svelte`) and the `LeftSidebar` `band` panel
  twin — to pass `hamBands={!declared.has('band')}`.
- do **not** touch S6-pre's file set to do this; S6-pre ships without row 6 wired at all, and the modal
  (and `LeftSidebar`) render `<BandSelector />` with its default `hamBands={true}` — i.e. unchanged —
  until S8 lands.

**Consequently, row 6's suppression predicate is deferred from S6-pre to S8**, unlike rows 1-5 and 7
which S6-pre wires directly. Row 10 gets no predicate, ever, on any manifest, on any skin — it is
permanent by the same logic as rows 8-9.

---

## 5. Section-count note (resolves a brief imprecision, not an open question)

The planning brief's S10 acceptance line ("every one of the seven modal sections") undercounts by
one against the eight `CollapsiblePanel` blocks verified by direct source read (§1) — and, once
control families rather than sections are counted, the table in §3 rules **ten** items, not seven.
The mismatch traces to three things the brief's scope paragraph did not separate: `desktop-vfo-ops`
bundles **three** independent duplicates, not two (the split row, `BandSelector`'s HAM grid, and
`BandSelector`'s LW/MW+SWL broadcast tabs — rows 7, 6, and 10 respectively, ruled separately because
each retires, or never retires, on a different, unrelated basis); the brief's scope paragraph did not
enumerate `desktop-language`/`desktop-workspace` at all (it only named the six duplicating panels);
and the brief did not know `BandSelector` itself splits into a duplicating half and a non-duplicating
half (§4a). This section's table (§3) is the authoritative, directly-source-verified enumeration; the
"seven" in the upstream brief should be read as "six duplicating legacy panels plus the VFO-ops row,"
which is consistent with this ruling once `desktop-language`/`desktop-workspace` are counted as their
own, separately-ruled sections, and once `BandSelector` is recognized as two rulings rather than one.

---

## 6. Binding carry-forwards this ruling must restate

- **§1.3 (S5 manifest/plan asymmetry).** Every predicate in §3 reads the **manifest**
  (`declared.has(surface)`), never the workspace **plan** (`zoneOwning`/`visibleSurfaces`). A modal
  copy suppressed off the plan would be the same S2 back-door hazard already ruled out for the dock
  and the sidebars, in a third location: a workspace subtraction of a declared surface must cost the
  operator the zone wrapper, never resurrect the modal's legacy twin. S6-pre must use exactly the
  same `declared` set `RadioLayout.svelte` already derives for the sidebar/status-bar predicates —
  not a second, modal-local copy of the logic.
- **§1.4 (R9 key-authority asymmetry) — the row-7 row is VFO-ops, not TX key/unkey.** `SPLIT` here
  means "route TX to the other VFO," toggled through `vfoHandlers.onSplitToggle` /
  `VfoSurface`'s `data-vfo-split` — it is **not** a key/unkey control and does not touch
  `semanticRxTx` / the R9 quadrant test (`semantic-desktop-migration.component.test.ts:456`). Row 7's
  predicate is `semanticDeck` (the `vfo`/`receiver-deck` gate), a distinct derived value from
  `semanticRxTx = $derived(semanticDeck)` (the `rxTx`/TX-panel gate) even though today the two happen
  to share the same underlying boolean by construction. **Do not let a future refactor collapse them
  into one prop** — they read the same value today only because `vfo` and `rxTx` are both
  `requiredSemanticSurfaces` on every registered `desktop-v2`-family manifest so far; that is a
  coincidence of the current manifest set, not a rule, and §1.4's binding text already forbids
  "tidying" `semanticRxTx`'s derivation.
- **AGC pairing (S6-pre's own mapping table, restated here for the modal side).** Row 2
  (`desktop-agc`) must share row 1's predicate exactly (`declared.has('dsp')`). A `dsp`-only
  suppression that leaves the modal's `AgcPanel` visible ships the same half-double S6-pre's own
  mutation battery is designed to catch on the sidebar side — the modal needs the identical
  cross-check.
- **Untranslated-surface carry-forward (§1.11 / S12 row 5).** Retiring rows 1-6 does not create a new
  instance of the untranslated-surface regression — `DspPanel`/`AgcPanel`/`RfFrontEnd`/
  `RitXitPanel`/`CwPanel`/`BandSelector` already carry `t()` calls that S7/S8/S9 already price into
  S12's row 5. This doc adds nothing to that tally except row 7, which is a translation
  *improvement* (see §4) and should be noted as a small offset, not folded into the same regression
  row.
- **Row 7's fail-closed delta — new S12 parity-matrix row, separate from the translation offset
  above.** Beyond the translation improvement, row 7 changes operator-visible behavior a second,
  independent way: the modal's `SPLIT` `<HardwareButton>` is never disabled, but `VfoSurface`'s
  replacement is `disabled={viewModel.split.status === 'unknown'}` (`VfoSurface.svelte:383-389`) and
  `toggleSplit()` itself refuses when `status === 'unknown'` (`VfoSurface.svelte:254`). On a radio
  that never reports split status, the operator can press `SPLIT` in today's modal and cannot after
  S6-pre lands the semantic replacement. This is the wave's existing fail-closed doctrine on unknown
  field status, not a bug, and this doc does not propose changing it — but §4's "strict improvement"
  language covers only the translation delta, and this second delta must be **recorded as its own row
  in S12's parity matrix**, not silently absorbed into "improvement," so it reads as a deliberate,
  reviewed decision rather than a later-discovered regression report.

---

## 7. Instructions to S6-pre (authoritative; no further interpretation needed)

S6-pre's own brief lists a 9-row family-to-host mapping table covering the sidebars and the status
bar. For the settings-modal host specifically, S6-pre must wire exactly **six** of the ten rows in
§3 — rows 1-5 and row 7 — and touch no other modal section. **Row 6 (`desktop-vfo-ops` →
`BandSelector` HAM half) is explicitly out of S6-pre's scope: its predicate is wired by S8 alongside
the `hamBands` component split (§4a, MOR-1367). Row 10 (the LW/MW/SWL broadcast tabs) never gets a
predicate at all, on any manifest, by any slice — it is permanent (§3, §4a).**

**Wrap placement — the whole `<CollapsiblePanel>`, not its children.** For rows 1-5, the predicate
wraps the **entire `<CollapsiblePanel>` element**, following the shipped precedent at
`RightSidebar.svelte:54` (`{#if showTx && !hideTxPanel && drag.order.includes('tx')}` around
`<CollapsiblePanel title="TX" …>`), never an `{#if}` around only the panel's children. A
children-only wrap still mounts `CollapsiblePanel`'s own header — title text, collapse toggle,
pointer/swipe handlers, and a `localStorage` collapse-state entry keyed by `panelId` — leaving a
titled, clickable, **empty** section with a live storage entry behind it. That is strictly worse than
the panel simply not existing, and it is not what `hideTxPanel` does.

```text
desktop-dsp   (<DspPanel/>)        wrap the whole <CollapsiblePanel>: {#if !declared.has('dsp')}
desktop-agc   (<AgcPanel/>)        wrap the whole <CollapsiblePanel>: {#if !declared.has('dsp')}        ← same predicate as desktop-dsp
desktop-rf    (<RfFrontEnd/>)      wrap the whole <CollapsiblePanel>: {#if !declared.has('rfFrontEnd')}
desktop-rit   (<RitXitPanel/>)     wrap the whole <CollapsiblePanel>: {#if !declared.has('ritXitScan')}
desktop-cw    (<CwPanel/>)         wrap the whole <CollapsiblePanel>: {#if !declared.has('cwKeyer')}
desktop-vfo-ops → SPLIT/A↔B/A=B    wrap ONLY that inner row: {#if !semanticDeck}                        ← non-inert, see §4; ship as a named exception with its own test, not silently
```

**`desktop-vfo-ops` is the one panel that does *not* follow the "wrap the whole `<CollapsiblePanel>`"
rule, and S6-pre must not apply it there.** Unlike rows 1-5 (one panel, one component, one predicate),
`desktop-vfo-ops` hosts three independently-ruled controls under one `<CollapsiblePanel title="VFO /
BAND">`: the split/swap/equalize row (row 7, gated on `semanticDeck`), `BandSelector`'s HAM half (row
6, gated on `declared.has('band')`, wired by S8 not S6-pre), and `BandSelector`'s broadcast tabs (row
10, permanent, never gated). Because row 10 always renders, **the panel itself can never become fully
empty**, so it must never be wrapped in an outer suppression `{#if}` — doing so would also hide the
permanent broadcast tabs, which is the exact operator-affordance loss §4a exists to prevent. Only the
inner row-7 button row gets its own `{#if !semanticDeck}`, scoped to that row alone (§4 instruction 1);
`BandSelector` is always mounted and is passed `hamBands={!declared.has('band')}` once S8 lands (a prop
change, not a mount/unmount, so it needs no `{#if}` of its own either).

The panel's rendered shape under each combination, once both S6-pre (row 7) and S8 (row 6) have
landed:

| `semanticDeck` | `declared.has('band')` | `desktop-vfo-ops` renders |
|---|---|---|
| `false` | `false` | split/swap/equalize row + `BandSelector` (HAM grid + broadcast tabs) — today's shape |
| `true` | `false` | `BandSelector` only (HAM grid + broadcast tabs); split row hidden |
| `false` | `true` | split/swap/equalize row + `BandSelector` (broadcast tabs only, HAM grid hidden) |
| `true` | `true` | `BandSelector` only, broadcast tabs only — the panel's eventual `desktop-v2` steady state; never empty |

`desktop-language` and `desktop-workspace` receive **no** predicate and **no** wrapper — they are
permanent, per §3 row 8-9.

Do **not** add a suppression for `filter`, `antenna`, `rxAudio`, `scopeDisplay`/`scopeControls`, or
`BandSelector`'s LW/MW/SWL broadcast tabs (row 10) in the modal — none of the first four families has
a modal section to suppress (only their sidebar/status-bar twins exist, already covered by S6-pre's
own sidebar/status-bar mapping table), and row 10 is permanent by ruling (§4a).

The `dsp`→`agc` pairing (row 2) must be included in S6-pre's own "AGC-pairing mutant" gate
(`WORKSPACE_ZONE_IDS`/mapping-table mutation battery already specified in its brief): a mutant that
declares `dsp` and asserts `DspPanel` suppressed but `AgcPanel` still visible must go RED **for the
modal host**, exactly as it must for the sidebars.

---

## 8. Gate table

Docs-only slice — no production file changes.

| gate | result |
|---|---|
| `npm run lint` | N/A — no source file touched by this change; markdown is not eslint-scoped |
| `npm run lint:boundaries` | N/A |
| `npm run check` | N/A |
| `npm run i18n:check` | N/A — no locale file touched |
| `npm run build` | N/A |
| `git status` (worktree) | only the new doc file — verified below |

Verification run from `frontend/` on `~/Projects/rigplane-worktrees/mor-1363` (base `643e9cb2`, same
as the S10 brief's read commit) to prove the tree is genuinely untouched, per the task's gate
requirement — see the build log for command output.

---

## 9. Open ambiguities (2, both flagged rather than silently decided)

1. **Whether row 7's suppression belongs inside S6-pre's PR at all, versus its own tiny separate
   change.** §4 rules "land it in S6-pre, named as an exception" because it is small, safe, and a
   strict improvement, and because S6-pre already carries one explicit waiver (4 files instead of 3)
   — one more explicitly-flagged deviation is consistent with how this wave has handled exceptions
   (12B's precedent: "waive it explicitly at landing rather than let it pass silently"). But this is
   the **one place in this doc where inertness, S6-pre's defining property, is deliberately broken**,
   and an owner or the S6-pre reviewer may reasonably prefer to carve it into its own one-line PR so
   S6-pre's inertness claim is never even partially qualified. Both are implementable from §4 as
   written; only the PR boundary is open.
2. **Whether `sdr-test`'s retention of nine of this doc's ten ruled modal items (§1) should get its
   own tracking ticket now, or fold into the existing S5-N2/S12-row-13 "`sdr-test` double-presentation"
   entry.** The nine are rows 1-6 (inert on `sdr-test` — none of `dsp`/`rfFrontEnd`/`ritXitScan`/
   `cwKeyer`/`band` is declared there) plus the permanent rows 8-10; **row 7 is not among them** — it
   retires on `sdr-test` exactly as on `desktop-v2`, because `sdr-test` already declares `vfo` (§1,
   §4). Of the nine, only rows 1-6 are genuine "double-presentation" in S5-N2's sense (a semantic
   surface exists but the legacy twin is not suppressed there); rows 8-10 were never suppressed on any
   manifest and are not part of that class at all — whoever writes S12 should keep them out of
   row 13's tally, not fold them in by count alone. This doc treats the six-row residual as the same
   class and defers to S12's existing row 13, but S12 row 13 was written before this doc existed and
   currently only mentions meters; whoever writes S12 must decide whether to widen row 13's language
   to cover the modal family too or add a sibling row. Not blocking for S6-pre/S7/S8/S9, which do not
   depend on the answer.
