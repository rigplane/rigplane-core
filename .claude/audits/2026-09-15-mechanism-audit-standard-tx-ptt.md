# Mechanism audit — Standard TX indication and AGC cleanup

- **Method:** complete `.claude/skills/mechanism-audit/SKILL.md` method
- **Implementation:** `5fa07e1a1955cc63b5b7a16d5fb13dc735655ff3`
- **Base:** `f27f1132365b002232dd091cdac1518b741fd999`
**Mode:** read-only; no tests, browser, radio, or PTT actions run.

`design-qa.md` was already modified in the worktree and was left untouched by
the auditor. This report is pinned to the implementation commit above.

## Scope verdict

- **VERIFIED — DSP/AGC:** Standard's `part='agc'` call has no `scalarLayout`.
  The formerly unconditional direct hosted `nbWidth` render is guarded by
  `part !== 'agc'`. The component test and exact-head browser test both assert
  that the AGC panel contains no `nbWidth` control.
- **VERIFIED — preservation:** the persistent `nbWidth` binding remains eager
  and identity-stable in `DspScalarHost.svelte`, retains its command/feedback
  mapping and wiring in `SemanticRadioSurfaces.svelte`, and remains present in
  the Standard DSP layout, NB settings, and generic/SDR path.
- **VERIFIED — TX → PTT:** Standard's authority row is semantic-only. Keyed
  visual state is consolidated on the existing PTT through the pre-existing
  `pressed` value exposed as `data-active` and `aria-pressed`, with strong
  opaque red styling. No request handler or authority predicate changed.

## Definitions, consumers, rulings, and in-flight proof

- `DspSurface` owns presentation only and receives `part`, persistent handles,
  and optional layouts. The single composition seam passes those handles
  unchanged.
- The concrete AGC consumer is `instruments.dsp(..., 'agc')` in
  `frontend/src/components-v2/layout/RadioLayout.svelte`. It supplies no scalar
  layout, so the direct-render guard is decisive.
- The persistent consumer chain remains `DspScalarHost.bindingFor('nbWidth')`
  → `nbWidth` snippet → `dspScalars` → Standard DSP / NB settings / default SDR
  presentation.
- The prior settings-boundary ruling treats AGC as a leaf of the DSP semantic
  surface, not as a competing control family. The v3 composition contract also
  requires unmistakable TX state without presentation-owned TX authority.
- The existing lifecycle test positively proves the same persistent bindings,
  a current post-face-switch lease, retained pending evidence, and inert stale
  lease. The new assertion tests AGC absence only; it does not remove the
  binding.

## Dead-code census

| Area | Count / result |
|---|---|
| New production named symbols | **0** |
| Changed `DspSurface` behavior branches | **1** — direct `nbWidth` render guard |
| Live `nbWidth` render sites remaining | **2** — ordinary non-AGC/default path and NB settings |
| Eager persistent NB bindings | **2 total**, including `nbWidth`; unchanged |
| New TX handlers/authority paths | **0** |
| New E2E result fields | `txPanelFeedback`, `agcNbWidth`, `browserErrors`; all asserted |
| Unconsumed changed production symbols | **0 found** |

`DspScalarHandles` remains a typed full-handle record; no public field was
removed or renamed. A scalar layout can theoretically render any supplied
handle, but no current `part='agc'` production caller supplies one. That is a
future composition constraint, not a present leak.

## Deletions

1. **Visible Standard TX row:** removed only as paint. Its status DOM,
   `role=status`, RF/session/intent attributes, and assistive-technology path
   remain live.
2. **Direct AGC NB Width disclosure:** removed only from the default hosted
   `part='agc'` branch. Binding, feedback, command path, DSP/NB settings, and
   SDR/default consumers remain intact.

## Consolidations

1. **TX feedback:** one local visible keyed indication, the existing PTT. It
   derives from the same `pressed` predicate already used for accessibility;
   no parallel TX state machine or command path was created.
2. **NB Width:** one persistent scalar binding and one feedback/intent route
   serve Standard DSP, NB settings, and default/SDR rendering. AGC simply stops
   consuming that handle visually.

## Steelman

The strongest contrary reading is that hiding the Standard TX row could
conceal safety status, or that suppressing NB Width in AGC could tear down its
control. Source evidence contradicts both: TX semantic data stays available and
PTT remains visibly and semantically active; `nbWidth` is owned outside the
surface by the persistent host with a cross-presentation identity/lifecycle
witness. The exact-head E2E checks hidden status geometry, active/pressed PTT,
opaque background and shadow, AGC count zero, zero captured commands, and zero
page/console errors.

## Weakest link

**INFERRED, non-blocking:** `DspScalarLayout` intentionally receives a complete
handle set. A future `part='agc'` caller could supply a layout that itself
chooses to render `nbWidth`. Current production composition has one AGC caller
and no scalar layout, so this is not a defect at the audited head.

## Cleared

- No accidental deletion of the persistent `nbWidth` binding or DSP/NB/SDR
  consumers.
- No duplicate local visible TX indicator in Standard.
- No TX command, safety, or authority-route change.
- No changed-module dead code found.
- Coordinator-supplied Mac Mini evidence: 133/133 component tests, check 0/0,
  and build passed at `ea3f2fac`; exact `5fa07e1` E2E passed for AGC absence,
  TX/PTT visuals, and zero commands/errors. The auditor did not reproduce it.

**Final merge-readiness verdict: PASS.**
