# Mechanism audit — Standard TX banner → PTT

**Method:** `.claude/skills/mechanism-audit/SKILL.md`  
**Audited revision:** `ed60a1219d44eccb3badd4ff650c1cce6fcc07a3`  
**Base:** `f27f1132365b002232dd091cdac1518b741fd999`  
**Mode:** strictly read-only; no edits, tests, browser, radio, or PTT actions.

The auditor observed a pre-existing modified `design-qa.md` and left it untouched.

## Definitions, rulings, and liveness

- **VERIFIED definition sites:** Standard's status row and its only local PTT
  action/indicator are both defined in
  `frontend/src/semantic/RxTxSurface.svelte`. The audited revision adds no TX
  controller, event source, or command path.
- **VERIFIED consumer:** production has one semantic mount in
  `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte`, passing the
  same `txState` and existing key/unkey callbacks.
- **VERIFIED prior ruling:** MOR-982 assigns the single browser-tab TX controller
  to `App.svelte`; presentations render its projection. The v3 composition
  contract requires unmistakable danger feedback while forbidding
  presentation-owned TX authority.
- **VERIFIED distinct global mechanism:** `AppGlobalHost.svelte` derives the
  persistent global TX/TX? lamp from the same App controller. It is not a
  duplicate local-panel banner.
- **VERIFIED in-flight status:** the existing `data-active={pressed}` and
  `aria-pressed={pressed}` PTT contract remains. Standard makes the status row
  semantic-only and intensifies the existing keyed PTT style.
- **VERIFIED added E2E consumer:**
  `frontend/tests/e2e/i18n/desktop-geometry.spec.ts` asserts the Standard status
  row is screen-reader-only and at most 1×1 px, while PTT is active, pressed,
  fully opaque, and has a non-transparent background and shadow. It also asserts
  no outgoing radio commands and no console/page errors.
- **UNKNOWN within this audit:** test outcome, because the audit itself did not
  execute tests. Mac Mini verification is a separate gate.

## Dead-code census

- `RxTxSurface.svelte`: all module, prop, derived, and presentation bindings are
  consumed. `showTxState` remains live for non-Standard paths; `pressed` remains
  live through `data-active` and `aria-pressed`.
- Component test: all top-level bindings are live.
- E2E module: all declarations are live. Newly added `browserErrors` and
  `txPanelFeedback` values are consumed by assertions.
- Dynamic-access guard: no dynamic selector construction for `rx-tx-state` or
  `rx-tx-key`; literal source and test consumers exist.
- **UNKNOWN:** out-of-repository style consumers. No deletion is proposed.

## Deletions

None. The change introduces no dead symbol, branch, or vestigial fork.

## Consolidations

### F1 — Standard local keyed feedback is correctly consolidated on PTT

- **Verdict:** C — legitimately local
- **Rank:** parallel
- **Elements:** the `rx-tx-state` status row, `rx-tx-key` PTT, and Standard
  styling in `frontend/src/semantic/RxTxSurface.svelte`
- **Consumers:** status row → assistive-technology `role=status`; PTT → sole
  visible local keyed feedback and action; `AppGlobalHost` → independent global
  TX/TX? warning
- **Definition site:** existing semantic surface/App host; no new producer
- **Divergence:** intended scope difference, not divergent truth calculation
- **Prior ruling:** MOR-982 and the v3 composition contract
- **In-flight:** complete source contract; E2E assertion added, execution kept
  outside this read-only audit
- **Required surface:** exists
- **Depends on:** none
- **Confidence:** high
- **Falsifier:** another visible Standard keyed banner, or a PTT style derived
  from view-model/radio state instead of the authority snapshot
- **Fix class:** none
- **Actionable:** no

## Steelman

A visible text/shape row could provide color-independent TX feedback. The change
preserves that semantic channel for assistive technology, retains
`aria-pressed`, uses a strongly red active PTT, and leaves the App-global TX/TX?
lamp intact. It removes only redundant visible local chrome.

## Weakest link

Visual rendering is covered by a source-level E2E assertion, including opaque
background/shadow and console/page-error collection, but its result remains a
separate Mac Mini gate. This does not affect TX authority or delivery.

## Cleared

- No second TX authority, PTT transport, or controller.
- No visible Standard local TX banner.
- No loss of semantic status, PTT active state, or unkey path.
- No duplication with the global lamp.

**Final verdict: PASS for merge readiness.**
