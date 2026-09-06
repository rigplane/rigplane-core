# Mechanism audit: legacy RF HBar request retirement

Date: 2026-09-06. Control-plane owner: MOR-2410 under MOR-2215.
Audit pin: accepted main `12444a3f5c977da790cd2f84123732cf53f6666c`.
Method: mid-tier mechanical collection followed by top-tier adjudication.

## Decision

The shared command-feedback scalar lacks a completed post-gesture request-retirement path for the prospective optimistic RF-only HBar adopter. The current legacy RF panel still supplies reading evidence, so this is not a demonstrated defect in that shipping panel. It is a source-established gap that must be reproduced and resolved before its full-feedback adoption.

The accepted native-only correction is correctly bounded and does not settle HBar pointer/key/reset semantics. Reuse and complete the existing scalar's request-reconciliation mechanism. Do not add an RF-local timer, command owner, wire quantizer, confirmation tolerance or presentation history. Preserve active pointer interaction, explicit preview policies and the established wheel hold.

This audit authorizes no product edit or test execution by itself. A separately frozen correction should precede the already substantial legacy RF/SQL adoption package. Its mounted reproduction is still unperformed.

## Method and current status

Read the actual mechanism-audit skill. Followed definitions, prior rulings, in-flight work, liveness and systematic collection, then steelman and per-element verdicts. Adjudication used the complete collection and scope, inventory totals and critical symbol rows, and current scalar, HBar, panel, pair, adapter and test definitions. The per-symbol collection and its explicit attribution limits are the evidence used here.

The collection describes aggregate CI as pending because that was its dispatch-time status. Root subsequently inspected main quick run 34033001617 at the same SHA: SUCCESS, including executed frontend install/check/tests/build, browser smoke and fixture capture. This changes acceptance status, not the source pin or collection observations. Historical 9b27e582 remains a comparison only.

No product/test edit, test/build/install, Git mutation or hardware operation was performed as part of adjudication. Source traces below are not substituted for mounted verification.

## Definitions and consumers

All paths below are repository-relative.

| Responsibility | Definition | Actual consumer evidence |
| --- | --- | --- |
| Numeric coordinate mapping | `frontend/src/primitives/scalar/value-control-core.ts:snapToStep,calculateClickValue` | HBar pointer geometry calls calculateClickValue; policies use shared math with distinct increments. |
| HBar policy | `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:createHBarContinuousScalarPolicy` (line 157) | ValueControl's reading compatibility and current command-bound Filter/CW consumers; caller chooses optimistic/confirmed preview and debounce. |
| Shared lifetime | Same scalar file: `createContinuousScalar` (459), `reconcile` (498), `dispatch` (552), `applyCandidate` (560), `interactionBase` (605), `viewOf` (609), `makeLease.endPointer` (673) | Renderer leases consume the shared owner; the owner alone stores scalar draft, timers and validity tokens. |
| Native handoff | Same scalar file: `nativeRequest` (478), reconciliation (506–528), candidate dispatch (587–596) | Native RF/SQL and CW inputs use the native policy; non-native sources explicitly clear this marker. Native-only tests now prove quantized target handoff and older-A/newer-B behavior. |
| HBar appearance and input surface | `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte`: one lease, one captured view, pointer handlers | Built-in and selected custom HBar receive the binding. They display view.displayed and retain canonical ARIA; pointer capture and geometry are local. |
| Current legacy panel | `frontend/src/components-v2/panels/RfFrontEnd.svelte`: RF-only ValueControl and combined DualParamRenderer branches | RF-only supplies raw rfGain/min/max/step/onChange, not command feedback. MOR-2410 intends to adopt scalar/pair bindings at these live branches. |
| Pair precedent | `frontend/src/primitives/scalar/continuous-pair.svelte.ts:LocalRequest,laneObservation,reconcile,effectiveLane,requestAxis` | The accepted semantic pair uses independently qualified lanes. Its inner scalar receives a derived reading axis, so this is precedent, not a drop-in command-scalar remedy. |
| Wire and truth | `panel-adapters.ts:withNormalizedRfLevels,getRfSqlControlFeedback`; `stores/commands.svelte.ts:normalizedLevelCommand,RF_GAIN_COMMAND_DESCRIPTOR,SQUELCH_COMMAND_DESCRIPTOR` | Legacy handlers round normalized candidate times 255; descriptors/projector supply exact level/255 target and exact admitted radio confirmation. No caller-side canonical snapping. |

## Prior rulings and in-flight distinctions

- Scalar-pair audit, 2026-09-06, F6: “C - legitimately local geometry and explicit task policy”. Different wheel increments, pointer geometry, optimistic versus confirmed preview and reset behavior are accepted distinctions.
- RF/SQL-feedback audit, 2026-09-06, F3: “Use exact confirmed===target”. Its prohibition on epsilon/nearest-step confirmation remains binding.
- Native-scalar-normalization audit, 2026-09-06, F1 separated local request retirement from radio confirmation and explicitly required that active pointer drafts survive ordinary pending feedback. MOR-2409 now implements and verifies its native-source correction; it intentionally does not claim a universal HBar correction.
- MOR-2412 accepted HBar one-snapshot acquisition, truthful Filter Hz geometry and localized presentation. Its confirmed preview and exact catalog requests do not prove the optimistic normalized RF case.
- MOR-2410 is the remaining legacy pair/HBar adoption owner. Its proposed seven-file package already approaches 1000 A+D and has no scalar source lease.
- MOR-2413 changes native Filter announcement authority only; it does not supply this HBar behavior. DSP and IF/RIT preflights are read-only, not competing lifetime implementations.

## Systematic enumeration and liveness

The corrected inventory contains 805 rows: 355 production and 450 direct-test definitions, across four production and four direct-test modules. Reference scan covers 657 production and 895 test paths, with 586 unique leaf names, no missing paths and no reported lexical parse errors.

| Bounded module | Rows | Production W/R row sum | Test W/R row sum |
| --- | ---: | ---: | ---: |
| continuous-scalar.svelte.ts | 161 | 4462 / 13596 | 8868 / 20718 |
| value-control-core.ts | 102 | 2281 / 5303 | 3811 / 10079 |
| HBarRenderer.svelte | 52 | 2201 / 6645 | 4024 / 7616 |
| RfFrontEnd.svelte | 40 | 235 / 790 | 984 / 1121 |
| continuous-scalar.test.ts | 154 | 2927 / 9498 | 6424 / 14589 |
| value-control-core.test.ts | 122 | 1000 / 2689 | 3524 / 18985 |
| ValueControl.test.ts | 140 | 8653 / 17551 | 44960 / 104631 |
| RfFrontEnd.component.test.ts | 34 | 122 / 1176 | 2749 / 7198 |

These are lexical leaf-token counts with overlapping declaration kinds, not resolved call-graph totals. Row sums repeat same-name references; owner-attributed counts remain null. The 332 zero lower-bound rows mostly include synthetic anonymous-arrow names and cannot imply dead code. Root traced the actual draft, nativeRequest, activeGesture, timer and reconciliation reads/writes in source; they are live.

Two named exported math functions have no in-repo production read established: calculateDragValue (production W/R 1/0; tests 0/9) and handleWheelStep (1/0; tests 0/6). Literal quoted-name registry search found none. Public exports, direct tests and unknown downstream/dynamic use prevent deletion authority. The initialProjectionContext zero-looking row is a scanner overlap artifact with an actual production read. Null-returning native wheel/key callbacks express unsupported input policy, not unreachable unrelated behavior.

Reproduce the collection with the command recorded in mor2410-hbar-scope.json and mor2410-hbar-handoff-collection.md. The full per-symbol rows are mor2410-hbar-inventory.json. Pair, store, projector, ValueControl and controlled mounting tests are dependency evidence, not additional complete-module sweeps.

## Steelman

The strongest case for the current arrangement is that an optimistic draft is intentional interaction state. Canceling it whenever pending feedback arrives would interrupt live pointer drag. A wheel burst deliberately keeps its local base for 300 ms. Debounced keys/reset can have a newer local candidate before its command exists. Radio evidence can legitimately differ from a UI candidate after quantization. None of these facts justifies treating draft as confirmed or forcing all policies to use identical cleanup.

That case succeeds for active gesture/hold/debounce and for keeping exact radio evidence. It does not justify an idle draft surviving the completed command and subsequent fresh canonical values indefinitely. Current command reconciliation clears on exact canonical==draft, authority/failure cancellation or source-specific cleanup. HBar pointer end only makes interaction idle; keyboard/reset have no wheel timer. Non-native input explicitly removes the only newly implemented request-observation marker.

The no-change alternative would be adequate if a current owner/renderer callback retired the draft after qualified representation or confirmation. No such path was found. Merely recreating the binding on every observation would discard intentional lifetime and add command/announcement churn. RF-local rounding would hide the gap by conflating candidate and radio truth. The narrow shared-owner completion is therefore warranted.

## Deletions

No actionable deletion is established. The two public test-consumed exports remain undetermined under dynamic, downstream, public-API and tests-only guards. No retirement or test deletion is proposed.

## Consolidations and boundary decisions

### F1 — Post-gesture command request retirement is incomplete

Verdict: B — bounded gap within the already shared scalar owner, not displacement into HBar.
Rank: diverged.
Elements: continuous-scalar.svelte.ts:reconcile,applyCandidate,dispatch,makeLease.endPointer,interactionBase,viewOf; HBarRenderer.svelte captured-view consumer.
Consumers: current command-feedback scalars and the planned RF-only HBar adopter; reading-mode legacy RF is a separate existing path and not proven broken.
Definition site: shared scalar, lines 459–742; HBar owns no command lifetime.
Divergence: prospective HBar candidate near .7 becomes raw 179 and exact target/confirmation 179/255. Pointer release leaves draft; key/reset have no idle timer; exact canonical equality does not retire that candidate. A subsequent same-authority .8 canonical can remain masked. Wheel's 300 ms cleanup and merged native handoff are distinct.
Prior ruling: native-scalar F1 and scalar-pair F6, both 2026-09-06; active pointer preservation and exact radio confirmation are binding.
In-flight: nativeRequest already solves the native-source subset. MOR-2410 is an unimplemented consumer adoption; MOR-2412's confirmed Filter policy is not this missing path.
Required surface: complete local-request reconciliation in the existing owner for the HBar command-feedback sequence. Distinguish current local candidate from an older retained lifecycle, active gesture/hold/debounce from completed interaction, and supplied active target from requested terminal history and canonical truth. An ended gesture or dispatched key/reset must eventually relinquish an obsolete candidate when its qualified evidence represents/completes that request and must follow later truth. Reuse the existing mechanism rather than introducing a second parallel request owner. The exact internal representation and moment of pending handoff require the discriminating tests below; this audit does not mandate a broad target-precedence change for every policy.
Depends on: accepted MOR-2409 native correction and existing qualified RF descriptors/accessor; already present. This correction precedes MOR-2410's full-feedback RF-only adoption.
Confidence: high on source path; medium on minimum implementation until mounted RED/GREEN.
Falsifier: a mounted command-bound HBar showing pointer end or key request, actual normalized target/exact confirmation and later canonical movement retire the candidate without new authority, branch replacement or extra commands; or an overlooked current callback performing that retirement.
Fix class: design, bounded completion inside the existing shared owner.
Actionable: yes after an explicit scalar correction lease and RED witness; no current-panel regression claim.

### F2 — Pointer geometry, gesture retention and wheel timing are legitimately local policy

Verdict: C for HBar pointer capture/geometry and explicit policy; already-shared for scalar timers and lease validity.
Rank: parallel, candidate cleared.
Elements: HBarRenderer.svelte:pointerValue,handlePointerDown/Move/Up/Cancel; scalar createHBarContinuousScalarPolicy,makeLease.
Consumers: built-in/custom HBar, existing reading consumers, current Filter/CW binding adopters.
Definition site: geometry/capture in HBar; scheduling and transient lifetime in scalar.
Divergence: immediate pointer/wheel, caller-debounced key/reset, 300 ms wheel hold and explicit optimistic/confirmed display are intentional.
Prior ruling: scalar-pair F6, 2026-09-06; existing scalar tests preserve pending drag and preview/base distinctions.
In-flight: renderer separation and one-snapshot lease acquisition are accepted.
Required surface: exists; F1 must preserve these contracts, including active token validity, deferred cancellation and renderer replacement.
Depends on: none for ownership; preserve while correcting F1.
Confidence: high.
Falsifier: actual renderer-owned command timer/draft or callback bypassing lease authority, neither found.
Fix class: none.
Actionable: no relocation or forced all-policy unification.

### F3 — RF conversion and canonical confirmation are already correctly separated

Verdict: already-shared for descriptors/projector; C for existing normalized-to-wire adapter.
Rank: parallel, candidate cleared.
Elements: panel-adapters.ts:withNormalizedRfLevels,getRfSqlControlFeedback; commands.svelte.ts:normalizedLevelCommand,RF_GAIN_COMMAND_DESCRIPTOR,SQUELCH_COMMAND_DESCRIPTOR.
Consumers: legacy RF handlers and qualified semantic/next legacy feedback consumers.
Definition site: adapter performs raw conversion; command store defines exact normalized target/matching.
Divergence: .7 and 179/255 are intentionally distinct; this is byte quantization, not floating-point tolerance.
Prior ruling: RF/SQL-feedback F3 and native-scalar F3, 2026-09-06.
In-flight: these mechanisms are accepted. Legacy default-session accessor adaptation remains MOR-2410's planned work.
Required surface: exists. Keep units, post-ACK observation qualification, exact matching and radio truth unchanged.
Depends on: none; F1 must conform.
Confidence: high for source contract, no new hardware claim.
Falsifier: a demonstrated unit or provider contract mismatch; not established here.
Fix class: none.
Actionable: no epsilon, UI-step snapping or RF-local quantization to conceal F1.

## Prospective correction

The bounded correction should normally touch only continuous-scalar.svelte.ts, its existing test and ValueControl.controlled.svelte.test.ts. The last test already mounts actual HBar with a caller-owned command-feedback binding and supports built-in/custom appearances. No HBar production edit, new public API, generic math change, pair rewrite, radio adapter/store or semantic surface change is established as necessary.

First discriminate the source finding with pointer -> pointer end -> quantized target/confirmation -> later canonical and a debounced key equivalent; include a nonzero reset domain and exact-zero reset control. Preserve active drag on pending evidence, rapid A/B requests, retained or delayed older terminal evidence, unissued debounce, wheel 300 ms hold, reading compatibility, confirmed-preview policy and replacement/authority cancellation. Test both synchronous and delayed supplied feedback where material. Current actual RF panel wiring must be proved after MOR-2410 adoption, rather than faked as already adopted in this correction.

## Weakest link

F1's minimum post-gesture pending presentation rule is the weakest link. A naïve all-source copy of nativeRequest may clear a live pointer; a blanket success clear may erase a newer B when A completes; a universal target-first projection may change unrelated accepted policy. Run the small RED witnesses first and choose the smallest correction that closes the stale-candidate path without those changes. Return any required public/policy contract expansion to root before widening the lease.

## Cleared

- HBar geometry, pointer capture, explicit preview and wheel/debounce policies are correctly located.
- Scalar lifetime and renderer-lease validity are already shared; no second owner belongs in RF or its skin.
- The accepted native-only request handoff remains valid for its tested scope.
- RF byte conversion, exact normalized targets and post-ACK confirmation stay unchanged.
- Public test-consumed math exports are not authorized deletion candidates.
- Current legacy RF reading behavior is not falsely reported as a demonstrated command-feedback regression.
