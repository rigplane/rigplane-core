# Settings-modal boundary for the v3 zone-ownership wave

**Date:** 2026-08-06

**Status:** Accepted

**Ticket:** [MOR-1363](https://linear.app/morozsm/issue/MOR-1363) (v3-rework slice S10, tail of
MOR-1263)

**Implementation status:** Ruling only. Zero production LOC in this change. It governs the modal
half of S6-pre (`feat(MOR-1263): manifest-driven legacy-twin suppression channel`) and is
consumed unmodified by S7/S8/S9.

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
| `desktop-vfo-ops` | VFO / BAND | a `SPLIT` / `A↔B` / `A=B` `<HardwareButton>` row, **then** `<BandSelector />` |
| `desktop-dsp` | DSP | `<DspPanel />` |
| `desktop-agc` | AGC | `<AgcPanel />` |
| `desktop-rf` | RF FRONT END | `<RfFrontEnd />` |
| `desktop-rit` | RIT / XIT | `<RitXitPanel />` |
| `desktop-cw` | CW | `<CwPanel />` |

(`desktop-vfo-ops` carries **two** independently-duplicative controls — the button row and
`BandSelector` — so the count of *rulings* below is nine, one section wider than the six panels the
coordinator's brief named plus the VFO-ops row it flagged. See §5 for why "seven sections" in the
upstream brief undercounts: it did not separately count `desktop-language`/`desktop-workspace`, or
split `desktop-vfo-ops` into its two independent duplicates.)

No `MODE`/`FILTER`, `ANTENNA`, `SCAN`, or `RX AUDIO` section exists in the modal — those five
sidebar families have **no** modal twin at all. `LeftSidebar.svelte`/`RightSidebar.svelte` were read
directly to confirm each `panelId` importing the identical component module the modal imports
(e.g. `RadioLayout.svelte` imports `BandSelector` from `../controls/BandSelector.svelte` — the same
path `LeftSidebar.svelte` imports). The modal is therefore not a parallel implementation of these
six controls; it is a **third literal mount of the same component**, alongside the sidebar twin and
(where landed) the semantic surface.

**Is the modal a `desktop-v2` concern only?** The component (`RadioLayout.svelte`) is shared with
`sdr-test` (`SdrTestSkin.svelte:19` mounts `<RadioLayout skinId="sdr-test" />`), so the *markup*
exists on both skins. But `sdr-test`'s registered manifest
(`presentation/layouts/declarations.ts:21`) declares exactly one zone, `{ id: 'main', surfaces:
['vfo', 'rxTx'] }` — it declares none of `filter`, `rfFrontEnd`, `band`, `antenna`, `ritXitScan`,
`dsp`, `cwKeyer`. Every suppression predicate this doc specifies (§3) is `declared.has(<surface>)`
against the *active* manifest, so on `sdr-test` every one of them evaluates `false` forever and the
modal renders unchanged. **Practical effect: this ruling retires modal duplicates on `desktop-v2`
only; `sdr-test` keeps all of them, matching the S5-N2 precedent (`sdr-test` still double-presents
meters) — track it as the same open class, not a new one.** `mobile`/`lcd-cockpit` use
`MobileRadioLayout.svelte`/a different layout component and never mount this modal at all.

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
| 6 | `desktop-vfo-ops` → `BandSelector` half | `BandSurface` | **(a)** | `band` zone — **S8** | `declared.has('band')` |
| 7 | `desktop-vfo-ops` → `SPLIT`/`A↔B`/`A=B` row | `VfoSurface`'s own split-toggle/swap/equalize controls (`data-vfo-split`/`data-vfo-swap`/`data-vfo-equalize`, `VfoSurface.svelte`) | **(a)** | `receiver-deck` zone — **already landed** (MOR-1313, v3-rework S1/S2) | `semanticDeck` (existing `RadioLayout.svelte:90` derived value) — **see §4, non-inert exception** |
| 8 | `desktop-language` (`LanguageSelector`) | — no sidebar twin, no semantic surface | **(b)** | n/a | n/a — never suppressed |
| 9 | `desktop-workspace` (`WorkspaceSettingsPanel` + `WorkspaceImportExport`) | — no sidebar twin, no semantic surface | **(b)** | n/a | n/a — never suppressed |

Every semantic-surface copy the modal hosts appears in exactly one row. No row is category (c): every
duplicative control in this modal already has either a landed zone (#7) or a zone a named slice is
about to declare (#1-6); every non-duplicative control (#8-9) is permanently outside the radio-facts
vocabulary — there is no future facts family that would ever make a UI-language picker or a
workspace/theme/layout selector a radio fact, so (c) does not apply to them either.

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

---

## 4. The row-7 exception — the one non-inert item S6-pre must land deliberately

Rows 1-6 are exactly the shape S6-pre's inertness proof expects: each predicate
(`declared.has('dsp')`, `declared.has('rfFrontEnd')`, `declared.has('ritXitScan')`,
`declared.has('cwKeyer')`, `declared.has('band')`) reads a zone **no manifest declares yet** (S7/S8/S9
have not landed), so every one evaluates `false` today and the rendered tree is unchanged the moment
S6-pre lands — the whole point of landing the channel inert.

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
change on `main` the day S6-pre merges** — not inert plumbing. It is also a strict improvement (it
removes the modal's three hardcoded, untranslated `SPLIT`/`A↔B`/`A=B` strings in favor of the
semantic surface's already-translated equivalents, and it removes a redundant TX-adjacent-looking
control surface that is not one — see §6).

**Ruling: land it in S6-pre, in the same PR, but as a named, called-out exception — not folded
silently into the "nothing renders differently" inertness claim.** Concretely:

1. Gate the row on the existing `semanticDeck` boolean already in scope in `RadioLayout.svelte` — no
   new prop, no channel plumbing (this is a same-file `{#if}`, unlike rows 1-6 which cross into
   `LeftSidebar`/`RightSidebar`/`StatusBar`).
2. Add one dedicated before/after test asserting the row is present when `semanticDeck` is `false`
   (legacy `VfoHeader` path — e.g. `sdr-test` before its own zone eventually widens, or any manifest
   that does not declare `vfo`) and absent when `semanticDeck` is `true` (today's `desktop-v2`).
3. State the exception in the S6-pre PR body next to the inertness-proof table, in the same style the
   12B waiver was recorded: one call-out line, not a footnote.
4. Do **not** report this as part of the "~55-70 net production LOC, nothing renders differently"
   estimate's *inertness* claim — report it as the one exception, with its own before/after evidence.

---

## 5. Section-count note (resolves a brief imprecision, not an open question)

The planning brief's S10 acceptance line ("every one of the seven modal sections") undercounts by
one against the eight `CollapsiblePanel` blocks verified by direct source read (§1). The mismatch
traces to two things the brief's scope paragraph did not separate: `desktop-vfo-ops` bundles **two**
independent duplicates (the split row and `BandSelector`, ruled separately in rows 6-7 above because
they retire under different, unrelated zones), and the brief's scope paragraph did not enumerate
`desktop-language`/`desktop-workspace` at all (it only named the six duplicating panels). This
section's table (§3) is the authoritative, directly-source-verified enumeration; the "seven" in the
upstream brief should be read as "six duplicating legacy panels plus the VFO-ops row," which is
consistent with this ruling once `desktop-language`/`desktop-workspace` are counted as their own,
separately-ruled sections.

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

---

## 7. Instructions to S6-pre (authoritative; no further interpretation needed)

S6-pre's own brief lists a 9-row family-to-host mapping table covering the sidebars and the status
bar. For the settings-modal host specifically, S6-pre must wire exactly these seven predicates (rows
1-7 of §3) and touch no other modal section:

```text
desktop-dsp   (<DspPanel/>)        {#if !declared.has('dsp')}
desktop-agc   (<AgcPanel/>)        {#if !declared.has('dsp')}        ← same predicate as desktop-dsp
desktop-rf    (<RfFrontEnd/>)      {#if !declared.has('rfFrontEnd')}
desktop-rit   (<RitXitPanel/>)     {#if !declared.has('ritXitScan')}
desktop-cw    (<CwPanel/>)         {#if !declared.has('cwKeyer')}
desktop-vfo-ops → BandSelector     {#if !declared.has('band')}
desktop-vfo-ops → SPLIT/A↔B/A=B    {#if !semanticDeck}               ← non-inert, see §4; ship as a named exception with its own test, not silently
```

`desktop-language` and `desktop-workspace` receive **no** predicate and **no** wrapper — they are
permanent, per §3 row 8-9.

Do **not** add a suppression for `filter`, `antenna`, `rxAudio`, or `scopeDisplay`/`scopeControls` in
the modal — none of those five families has a modal section to suppress; only their sidebar/
status-bar twins exist, already covered by S6-pre's own sidebar/status-bar mapping table.

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
2. **Whether `sdr-test`'s permanent retention of all nine modal duplicates (§1) should get its own
   tracking ticket now, or fold into the existing S5-N2/S12-row-13 "`sdr-test` double-presentation"
   entry.** This doc treats it as the same class and defers to S12's existing row 13, but S12 row 13
   was written before this doc existed and currently only mentions meters; whoever writes S12 must
   decide whether to widen row 13's language to cover the modal family too or add a sibling row. Not
   blocking for S6-pre/S7/S8/S9, which do not depend on the answer.
