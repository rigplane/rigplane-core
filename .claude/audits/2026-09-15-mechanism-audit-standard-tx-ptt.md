# Mechanism audit — Standard TX indication and AGC cleanup

- **Method:** complete `.claude/skills/mechanism-audit/SKILL.md` method
- **Implementation:** `5fa07e1a1955cc63b5b7a16d5fb13dc735655ff3`
- **Base:** `f27f1132365b002232dd091cdac1518b741fd999`
- **Mode:** read-only; no tests, browser, radio, or PTT actions run

`design-qa.md` was already modified in the worktree and was left untouched by
the auditor. This report is pinned to the implementation commit above.

## Steps 0–3 — definitions, prior rulings, in-flight work, and liveness

### TX indication

- **Definition sites:** the Standard status row is
  `frontend/src/semantic/RxTxSurface.svelte:rx-tx-state`; its sole local PTT
  action/indicator is `frontend/src/semantic/RxTxSurface.svelte:rx-tx-key`.
  The server-owned TX snapshot is accepted by
  `frontend/src/semantic/RxTxSurface.svelte:Props.tx`; the change adds no
  controller, event source, or command path.
- **Consumer sets:** `rx-tx-state` is consumed by assistive technology through
  `role=status` and by component/E2E assertions.
  `rx-tx-key` is consumed by the operator as the sole local visible keyed
  indication and PTT action. The separate
  `frontend/src/AppGlobalHost.svelte:global-tx-indication` is consumed as a
  presentation-independent global warning.
- **Prior ruling:** `docs/plans/2026-07-25-ui-composition-architecture-v3.md`
  records MOR-982, accepted 2026-07-26: “one browser-tab TX controller” belongs
  to `App.svelte`; presentations “request intents and render its snapshot”.
  The same plan's invariants 9 and 11 require unmistakable TX state and forbid
  presentation-owned TX authority or PTT delivery.
- **In-flight work:** none. The production semantic surface is mounted once by
  `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:rxTxSurface`,
  which passes the existing snapshot and key/unkey callbacks unchanged.
- **Liveness:** `frontend/src/semantic/RxTxSurface.svelte:pressed` is read by
  both `data-active` and `aria-pressed`; `showTxState` remains consumed on
  non-Standard render paths. UNKEY remains an unconditional button bound to
  `onRequestUnkey`.

### NB Width placement

- **Definition sites:** the persistent binding is defined by
  `frontend/src/semantic/DspScalarHost.svelte:bindingFor` and eagerly created
  for `nbWidth`. Its typed presentation handle is
  `frontend/src/semantic/DspScalarHost.svelte:nbWidth`. Presentation placement
  is owned by `frontend/src/semantic/DspSurface.svelte:adjustableLevels` and
  `compactLevels`.
- **Consumer sets:** the one persistent `nbWidth` binding is consumed by the
  Standard DSP scalar layout
  (`frontend/src/components-v2/layout/RadioLayout.svelte:dspScalarLayout`), by
  `DspSurface.svelte:compactLevels` when NB settings are open, and by the
  default/grouped SDR `DspSurface` path. The Standard AGC consumer is
  `RadioLayout.svelte:standardServicePanels`, which calls the surface with
  `part='agc'` and no scalar layout.
- **Prior ruling:** the 2026-08-06 ruling table in
  `docs/plans/2026-08-06-settings-modal-boundary.md` says the AGC panel is the
  “AGC leaf” of the DSP surface (5A/MOR-1290), while NR/NB/notch belong to the
  DSP part.
- **In-flight work:** complete at the audited revision. The direct hosted
  `nbWidth` render now requires `part !== 'agc'`; its binding and other
  consumers are untouched.
- **Liveness:** command dispatch remains in
  `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:DSP_LEVEL_INTENT.nbWidth`;
  feedback remains in `dspScalarFeedback.nbWidth`; the persistent host still
  creates and exports the handle. Existing lifecycle coverage proves the
  binding survives Standard→SDR presentation changes.

## Step 3a — systematic dead-code census

- `RxTxSurface.svelte`: 1 module constant (`RF_BADGE`), 1 props destructure,
  and 16 derived bindings were enumerated; each has a template or downstream
  derived consumer. There are 0 locally defined functions, classes, or instance
  attributes.
- `DspSurface.svelte`: 1 exported value catalog, 3 exported type declarations,
  3 fallback constants, 7 module arrow helpers, 1 module function, 1 props
  destructure, 1 derived binding, 1 instance function, 2 instance constants,
  and 3 snippets were enumerated. Each is consumed in the module or by its
  exported public surface.
- `DspScalarHost.svelte`: its complete persistent field/command maps, binding
  registry, 10 local functions, 7 exported handle snippets, and lifecycle
  cleanup were checked. The changed `nbWidth` handle still has production
  consumers.
- Changed test modules: the component suite's added selector is directly
  asserted. The E2E result fields `txPanelFeedback`, `agcNbWidth`, and
  `browserErrors` each have downstream assertions.
- Dynamic-access guard: literal selectors and typed handle access are used; no
  string-built `rx-tx-state`, `rx-tx-key`, or `nbWidth` selector/handle
  access was found. Public `DspScalarHandles` retains every field. Possible
  sibling/private consumers therefore do not lose an API surface.

## Step 4 — steelman

A visible text/shape TX row is a defensible color-independent warning, and a
single shared DSP surface could defensibly receive all scalar handles. The
current arrangement nevertheless supplies the stronger ownership evidence:
the TX text remains available to assistive technology, `aria-pressed` remains
on PTT, and the global TX lamp remains visible; meanwhile the accepted
composition ruling makes NB Width a DSP/NB concern rather than AGC. Suppressing
one AGC presentation does not destroy the persistent scalar binding.

## Deletions

No `Dead` or `Vestigial fork` finding exists, so there is no D-ranked deletion
candidate.

The requested UI removals are presentation changes, not dead-code claims:
Standard's TX status DOM stays live for assistive technology, and `nbWidth`
stays live outside AGC. Dynamic access, out-of-repository/public API, and
tests-only-consumer guards were checked before making that determination.

## Consolidations

### F1 — Standard local keyed feedback: one visible indication on PTT

- **Verdict:** C — legitimately local
- **Rank:** parallel
- **Elements:** `frontend/src/semantic/RxTxSurface.svelte:rx-tx-state`;
  `frontend/src/semantic/RxTxSurface.svelte:rx-tx-key`;
  `frontend/src/AppGlobalHost.svelte:global-tx-indication`
- **Consumers:** `rx-tx-state` → assistive-technology status and tests;
  `rx-tx-key` → local operator PTT action and visible keyed state;
  `global-tx-indication` → presentation-independent global warning
- **Definition site:** local presentation elements are defined in
  `RxTxSurface.svelte`; global warning in `AppGlobalHost.svelte`; authoritative
  snapshot/controller remains App-owned
- **Divergence:** intended scope only — semantic local status, actionable local
  control, and persistent global warning all project the same authority snapshot
- **Prior ruling:** MOR-982, accepted 2026-07-26, assigns one browser-tab TX
  controller to `App.svelte`; v3 invariants 9 and 11 preserve unmistakable
  feedback without presentation-owned authority
- **In-flight:** complete; the existing semantic mount and callbacks remain
  unchanged
- **Required surface:** exists — one status channel, one local PTT/UNKEY action
  surface, and one global warning
- **Depends on:** none
- **Confidence:** high
- **Falsifier:** another visible Standard keyed banner, a PTT style derived from a
  different state source, or a new TX handler/transport path
- **Fix class:** none
- **Actionable:** no — the former local visual duplication is removed and no
  mechanism duplication remains

### F2 — NB Width control: shared binding, correctly excluded from AGC

- **Verdict:** Already shared
- **Rank:** parallel
- **Elements:** `frontend/src/semantic/DspScalarHost.svelte:bindingFor`;
  `frontend/src/semantic/DspScalarHost.svelte:nbWidth`;
  `frontend/src/semantic/DspSurface.svelte:adjustableLevels`;
  `frontend/src/semantic/DspSurface.svelte:compactLevels`;
  `frontend/src/components-v2/layout/RadioLayout.svelte:dspScalarLayout`
- **Consumers:** persistent binding → Standard DSP, NB settings, and grouped/SDR
  presentations; AGC composition → AGC finite choices only; command consumer →
  `SemanticRadioSurfaces.svelte:DSP_LEVEL_INTENT.nbWidth`; feedback consumer →
  `SemanticRadioSurfaces.svelte:dspScalarFeedback.nbWidth`
- **Definition site:** one persistent scalar is defined by
  `DspScalarHost.svelte:bindingFor('nbWidth')`; all presentation sites consume
  its exported handle
- **Divergence:** none — presentations choose placement while sharing identity,
  command, and feedback mechanisms
- **Prior ruling:** 2026-08-06 settings-boundary ruling, row 2: AGC is the AGC
  leaf of the DSP surface; NR/NB/notch remain the DSP part
- **In-flight:** complete; `part='agc'` has one production caller with no
  `scalarLayout`, and the default direct render is now guarded
- **Required surface:** exists — persistent typed handle plus part-specific
  presentation layouts
- **Depends on:** none
- **Confidence:** high
- **Falsifier:** more than one live `nbWidth` binding/handler, loss of the handle
  from DSP/NB/SDR, or any current AGC caller rendering it through a scalar layout
- **Fix class:** none
- **Actionable:** no — ownership and consumers are already consolidated

## Verification boundary

The read-only auditor did not run tests. Coordinator-supplied Mac Mini evidence
is separate: 133/133 component tests, Svelte/TypeScript check with 0 errors and
0 warnings, and production build passed at `ea3f2fac`; exact
`5fa07e1a` targeted Playwright passed while asserting AGC `nbWidth` count
zero, hidden Standard status geometry, active PTT visuals, zero radio commands,
and zero page/console errors.

## Weakest link

**F2 is the verdict most likely to need revisiting:** `DspScalarLayout`
intentionally receives the full handle record. A future `part='agc'` caller
could supply a layout that deliberately renders `nbWidth`. Check new
`instruments.dsp(..., 'agc')` call sites first. At this revision the one AGC
caller supplies no scalar layout.

## Cleared

- App-owned TX authority and PTT delivery
- Standard local TX status/PTT presentation
- unconditional UNKEY recovery action
- global TX/TX? warning
- persistent NB Width binding identity
- NB Width command and feedback routes
- Standard DSP, NB settings, and grouped/SDR NB Width consumers
- Standard AGC finite-choice presentation
- changed-module dead code

**Final merge-readiness verdict: PASS.**
