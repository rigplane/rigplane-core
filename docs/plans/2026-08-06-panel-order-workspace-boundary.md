# The drag-order / workspace boundary (MOR-1371, v3-rework S11)

**Status:** ruling, landed with the minimal code it implies (test-only — see §2's evidence for why
no production file changed).
**Sibling of:** `docs/plans/2026-08-06-settings-modal-boundary.md` (S10) — same shape, applied to
the sidebars' `lib/drag-reorder.svelte.ts` instead of the settings modal.

## 0. What is being reconciled

Two ordering systems coexist on `desktop-v2` after S9 (MOR-1368) and do not know about each other:

- **Legacy:** `lib/drag-reorder.svelte.ts` — `localStorage['rigplane:panel-order']` (left) and
  `['rigplane:right-panel-order']` (right), each with a `:known-defaults` companion key, a
  module-level cross-sidebar registry, and variable-length stored orders. Keyed by **panelId**.
- **v3:** the workspace's `zoneOrder` / `visibleSurfaces` (MOR-1079/1082), keyed by **zone id** and
  **surface name**, resolved into a `SurfacePlan`. Zone-owned surfaces have no drag story at all —
  `SemanticRadioSurfaces` renders them in fixed source order.

After S9, most of `LeftSidebar`'s and `RightSidebar`'s `defaults` ids never render **on
desktop-v2**, because `desktopV2Layout` now unconditionally declares every zone whose semantic twin
retires them (`presentation/layouts/desktop-declarations.ts:36-111`). `loadPanelOrder`'s
`known-defaults` bookkeeping does not know that — it records every default the app "presented" to
a sidebar, and a permanently-suppressed panelId still gets recorded as known, which is
indistinguishable from "the user genuinely removed it" the next time the id set changes.

## 1. Rulings

### 1.1 — Does `zoneOrder` subsume `panel-order` for zone-owned surfaces?

**Yes, for the surface, not for the storage.** Once a zone declares a surface, its legacy panel's
render `{#if}` is permanently `false` on that manifest (the MOR-1364/S6-pre suppression channel),
so `zoneOrder`/`visibleSurfaces` is the only ordering system with any live effect for that surface.
`panel-order` does not gain a migration path INTO `zoneOrder` (§1.5) — the two id spaces stay
independent for good, and the render gate — not the stored order — is what makes the legacy
ordering system inert for a declared surface.

### 1.2 — Are the retired panelIds pruned from the sidebars' `defaults` arrays?

**No — reversed from the brief's tentative recommendation, on empirical evidence, not just
architectural argument.** Two shapes were considered and rejected during this slice's build, in
order:

**Attempt 1 (rejected on inspection): delete the ids from the literal.** `LeftSidebar` and
`RightSidebar` are the exact same component instances `LcdLayout.svelte` mounts for `lcd-cockpit`
(`components-v2/layout/LcdLayout.svelte:60,94`), and `lcd-cockpit` is a **live, default-routed**
skin — `resolveSkinId`'s auto rule sends any radio with no scope there (`skins/registry.ts:63`).
`LcdLayout` never passes a `declared` prop (it predates the MOR-1263 zone-ownership work and stays
on its pre-existing legacy presentation — see the file's own comment, "The amber glass keeps its
legacy presentation for this slice; MOR-1162 redesigns it"). `RightSidebar.component.test.ts`'s own
inertness baseline states the invariant a literal deletion would break: *"An omitting caller is a
guaranteed no-op ... the reason `LcdLayout` mounting this component without `declared` cannot
silently suppress anything."* `loadPanelOrder(storageKey, defaults)` returns `[...defaults]`
verbatim on a fresh load, so a deleted id would never enter `order` on **any** skin again, including
`lcd-cockpit`, with no UI path back (the "Reset panel order" button resets to the same pruned
`defaults`). Rejected before writing code.

**Attempt 2 (built, tested, and reverted): filter `defaults` by the `declared` prop already
threaded into both components**, using a small `panelId → retiring surface` map, so `desktop-v2`
gets a pruned literal while `sdr-test`/`lcd-cockpit` (smaller or empty `declared`) keep the full
one — no new prop, no third file. This looked safe (each mount evaluates `declared` once, and a
skin switch always remounts the sidebar tree — `DesktopSkin.svelte`/`SdrTestSkin.svelte`/
`LcdLayout.svelte` are separate dynamically-`import()`ed components, never the same live instance
under a changing `skinId`) and passed every targeted test written for it. **It failed the full
suite.** `rigplane:panel-order`/`rigplane:right-panel-order` are ONE storage key shared by every
skin that mounts these components, and `loadPanelOrder` always prefers a valid STORED order over
`defaults`. Filtering `defaults` by `declared` only changes what a brand-new user's first load
writes; it does nothing for a RETURNING user whose browser already holds a stored order from a
*different* skin. In production terms: a user whose radio routes to `desktop-v2` writes the pruned
order to the shared key; the next radio they connect that auto-routes to `lcd-cockpit` inherits that
same pruned order (the stored value wins over the fuller `lcd-cockpit` `defaults`), and `rx-audio`/
`dsp`/`cw` silently stop rendering on a skin that still needs them, with no `declared` gate to save
it because `lcd-cockpit` never sets `declared`.

This reproduced as a real full-suite failure, not a hypothesis: three assertions in
`semantic-lcd-migration.component.test.ts` (`... keeps the CW panel ...`, `... leaves the cockpit
variant's LCD chrome intact`, `... leaves the scope variant's LCD chrome intact`) flipped on a
third fresh-`--localstorage-file` run, expecting `[data-panel-id="rx-audio"]` and failing to find
it — a `desktop-v2`-shaped write from an earlier test in the same process leaking, through the
on-disk store the `--localstorage-file` harness shares across the whole run, into a later LCD-shaped
mount that never seeds its own `localStorage`. Two more fresh full-suite runs reproduced it
intermittently (order-dependent — see §2 for the run log). The identical hazard exists in production
between real skin switches on one browser profile; the test flake is a lucky early warning, not the
actual risk.

**Ruling: `defaults` stays exactly as it is today, in both files. No production file changes.** The
only safe suppression point remains the RENDER `{#if}` (`declared.has(...)`), which was already
correct before this slice. `known-defaults`'s imprecise recording of a declared-retired panelId as
"known" for `desktop-v2` users is real but lower severity than a silent cross-skin panel loss: the
worst case is that if a currently-suppressed surface's zone declaration were ever removed in the
future, a returning `desktop-v2` user's dormant legacy panel might not auto-reappear until they
click "Reset panel order" — a recoverable UX paper cut, not silent, un-recoverable data loss.
Correctly fixing the `known-defaults` imprecision needs a storage key (or `known-defaults` key)
scoped per active manifest id — a real change (new key scheme, migration of existing users' stored
preferences) that does not fit this slice's ≤2-production-file budget and is not required by this
ticket's actual mandate (define the boundary; do not regress the safety property). **Follow-up
ticket recommended if the `known-defaults` imprecision is ever worth fixing on its own — not filed
here.**

**What a stored order still naming a "retired" id does:** nothing changes, and never did — it is
ignored at render because `declared.has(...)` gates the `{#if}` independent of whether the id is
present in `order`. Pinned directly in §3.

### 1.3 — Does a surviving legacy panel (`audio-scope`, `memory`, `tx`) keep dragging?

**Yes**, unchanged — `defaults` is untouched, so this was never in question once §1.2 landed as a
no-op. None of the three has a semantic twin (`audio-scope`, `memory`) or is governed by the
`declared` channel at all (`tx` follows `hideTxPanel`, R9/MOR-1313, deliberately never folded into
`declared`).

### 1.4 — Does zone reordering get a drag affordance in v3?

**No — deferred, stated explicitly.** `zoneOrder`/`visibleSurfaces` are workspace-settings-only;
`SemanticRadioSurfaces` renders zone-owned surfaces in fixed source order with no drag handle.
Building a drag affordance for zones is a placement-and-interaction question that belongs with the
LCD/design-language rework (see memory precedent: LCD directions ride AFTER the v3 cutover), not
with this boundary ruling. **Ticket required if picked up: none filed yet — route through MOR-1162.**
Recorded as an S12 parity row (§4 below), per the ticket's own instruction.

### 1.5 — Is there a one-way migration from `panel-order` into `zoneOrder`?

**No.** The id spaces do not correspond 1:1: one panelId can map to zero surfaces (`memory`,
`audio-scope`) or effectively two (`agc`+`dsp` both retire under the single `dsp` zone); two
panelIds map to one surface (`mode`+`filter`→`filter`; `agc`+`dsp`→`dsp`). Any migration would be
lossy in both directions. The workspace already has forward-read/reset machinery (MOR-1080); a
`desktop-v2` user who wants the v3 ordering story gets it automatically the moment a zone declares
their surface — no import step needed.

## 2. Disposition of every legacy panelId, and the "11" correction

`LeftSidebar`'s literal `defaults` holds 8 ids, `RightSidebar`'s holds 6 — 14 total, no overlap in
the literals themselves (though `rx-audio`/`dsp`/`cw` are reachable in *either* sidebar via
cross-sidebar drag, which is why both `LeftSidebar` and `RightSidebar` gate them in their templates
even though only `RightSidebar` lists them in `defaults`).

| panelId | host(s) | declared-retired on desktop-v2 (render, not storage)? | disposition |
|---|---|---|---|
| `rf-front-end` | Left | yes (`rfFrontEnd`, S7) | never renders on desktop-v2; kept in `defaults` (§1.2) |
| `mode` | Left | yes (`filter`, S7) | same |
| `filter` | Left | yes (`filter`, S7) | same |
| `agc` | Left | yes (`dsp`, S9) | same |
| `rit-xit` | Left | yes (`ritXitScan`, S8) | same |
| `antenna` | Left | yes (`antenna`, S8) | same |
| `scan` | Left | yes (`ritXitScan`, S8) | same |
| `band` | Left | **no — partial only** | **kept and STILL RENDERS** — S10 row 10: `BandSelector` hosts the LW/MW+SWL broadcast tabs (16 presets), which are not facts and have no other host; the `band` zone retires only the HAM half by prop (`hamBands={!declared.has('band')}`), never by unmounting. |
| `rx-audio` | Right (+ Left template gate) | yes (`rxAudio`, S9) | never renders on desktop-v2; kept in `defaults` |
| `dsp` | Right (+ Left template gate) | yes (`dsp`, S9) | same |
| `cw` | Right (+ Left template gate) | yes (`cwKeyer`, S9) | same |
| `audio-scope` | Right | no semantic twin | unaffected (§1.3) |
| `tx` | Right | governed by `hideTxPanel`, not `declared` | unaffected (§1.3) |
| `memory` | Right | no semantic twin | unaffected (§1.3) |

**Ten** panelIds never render on `desktop-v2` today (7 gated in `LeftSidebar`'s template, 3 in
`RightSidebar`'s — `rx-audio`/`dsp`/`cw` are each counted once, at the sidebar whose `defaults`
literal lists them). The ticket brief and the S9 verify report's informal tally
(`verify-mor-1368.md` §10 N4: "eight ids... and RightSidebar's three") round `band` in as an
eleventh; direct read of `LeftSidebar.svelte`'s render block shows `band`'s `{#if}` carries no
`declared` predicate at all (S8/MOR-1367's explicit, commented exemption), so it renders on
`desktop-v2` today. **Corrected count: 10, and — per §1.2 — none of the 10 are removed from
`defaults` regardless, because the array is shared storage across skins, not a desktop-v2-scoped
concept.**

## 3. Guard: a stored order cannot resurrect a declared-retired panel, on any manifest shape

Pinned in `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`, describe
block `'a stored order naming every legacy panelId cannot resurrect a declared-retired one
(MOR-1371, S11)'`:

- A stored order naming **every** legacy id (the pre-S11 shape, and also what any future
  `known-defaults` state converges to since `defaults` is never pruned) does not crash on load and
  does not resurrect any of the 10 declared-retired ids in the rendered DOM on `desktop-v2` — the
  probe: reintroduce a `declared`-array-membership check into the resurrection path and this
  assertion dies.
- The IDENTICAL stored order, mounted on `sdr-test` (standing in for `lcd-cockpit`'s
  `declared`-less shape — both hit the same "nothing here suppresses anything" branch), renders all
  ten of those same ids normally — the reason `defaults` must not be pruned, made concrete: any
  future attempt to delete an id from the shared literal fails this test the moment it runs on a
  non-`desktop-v2` manifest.

Together the two are the bidirectional pin the ticket asked for (S9 N1a shape), scoped correctly to
what actually varies (the render gate) rather than to a shared literal that must not vary.

**Full-suite evidence for the reverted `defaults`-filter attempt** (fresh `--localstorage-file`
each run, `frontend/`):

| run | result |
|---|---|
| 1 | 286/289 files, 5928/5931 tests — 3 failures, all inside `semantic-desktop-migration.component.test.ts`/`semantic-lcd-migration.component.test.ts`/`mod-input-tx-guard.isolated.test.ts` |
| 2 | 289/289 files, 5931/5931 — clean |
| 3 | 288/289 files, 5929/5931 — 1 file, 2 failures, `semantic-lcd-migration.component.test.ts`: `[data-panel-id="rx-audio"]` expected non-null, found null |
| baseline (`git stash`, unmodified `origin/main`) | 289/289 files, 5928/5928 — clean |
| 4th run after `git stash` restore, same filter code | 288/289 files, 5928/5931 — 3 failures, same `semantic-lcd-migration.component.test.ts` assertions as run 1/3 |

The baseline run being clean while the filtered-`defaults` code flickers red across otherwise
identical fresh-storage runs is exactly the shared-storage-key leak described in §1.2, not
environment noise (`mod-input-tx-guard.isolated.test.ts` alone, in run 1, IS the previously
documented unrelated flake — see repo memory; it did not reproduce in the other three runs and is
unrelated to this ticket).

## 4. S12 parity row

| # | row | source | verdict |
|---|---|---|---|
| — | **No drag-reorder for zone-owned surfaces.** `zoneOrder`/`visibleSurfaces` are workspace-settings-only; `SemanticRadioSurfaces` renders zone-owned surfaces in fixed source order with no drag handle, and the operator loses the legacy sidebars' reordering affordance for every surface a zone retires. | this doc, §1.4 | **owner decision required / deferred** — no ticket filed yet; route through MOR-1162 if picked up. |

## 5. Files touched

- `frontend/src/components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts` — 2 new tests (test-only, reuses existing fixture plumbing). **No production file changed** — see §1.2/§3 for why the two attempted production changes were rejected before and after being built.
- This doc.
