# Mechanism audit: desktop and mobile scope consumers

Audited revision: `c356a85ab24389efe1b8320d1061035001e4b8ed`; tree `0e4e4aba0359e5b96904c7f44e5504aa4442bf74`, equal to main `08b650d3fb24207abf760f96a7346c5aab13efee`. Date: 2026-09-10.

Method: `.claude/skills/mechanism-audit/SKILL.md`. A separate researcher performed definition/consumer collection and a scoped liveness census; the coordinator checked consequential callsites and adjudicated. Source inspection was read-only, with clean initial/final status. No tests, source changes, service actions or radio commands were part of this audit. Separate coordinator RX captures supplied timing observations, and separate MOR-2441 implementation work began after the source diagnosis. Neither is an audit test run.

## Scope and evidence

The capability is acquisition and recovery of the hardware panorama frequency marker/passband in Standard and both mobile orientations. Audio FFT, LCD/dual-SDR composition, backend polling implementation and unrelated panel controls are outside this census; their adjacent consumers were considered before suggesting deletion.

| Element | Definition and live consumers | Observation |
| --- | --- | --- |
| Resource owner | `frontend/src/lib/runtime/scope-controller.svelte.ts:ScopeController`; runtime facade, `ScopeFrameHost`, unmanaged `SpectrumPanel` | One shared controller and resource host. Both UI paths reach it. |
| Frame qualification | `frontend/src/lib/runtime/adapters/scope-adapter.ts:qualifyScopeFrameEnvelope` and `toScopeDisplayFrame`; controller and `scope-frame-host.ts:ScopeFrameHost` | Shared evidence and display-frame primitives. |
| Strict radio authority | `frontend/src/lib/runtime/adapters/scope-adapter.ts:toSpectrumAuthority`; projector, panel and controls | Shared definition; importing it from multiple consumers is not duplication. |
| Continuity reducer | `frontend/src/lib/runtime/adapters/scope-passband-display.ts:projectScopePassbandDisplay`; `SemanticRadioSurfaces.svelte:refreshScopePassband` | One production reducer callsite, in semantic wiring. Mobile's direct panel path does not invoke it. |
| Projection contract | `frontend/src/lib/runtime/adapters/scope-display-projection.ts:ManagedScopeRegion`; `SemanticRadioSurfaces.svelte:managedScopeRegion` to `RadioLayout.svelte:scopeRegion` to `SpectrumPanel` props | Existing contract transports projection, demand and callback. It contains no second mutable controller. |
| Renderer | `frontend/src/components/spectrum/SpectrumPanel.svelte` | One DOM/canvas renderer. Its `managed` binding branches on whether `scopeProjection` is undefined. |
| Mobile composition | `frontend/src/components-v2/layout/MobileRadioLayout.svelte` portrait and landscape `SpectrumPanel` mounts; `mobile-declarations.ts:MOBILE_ZONES` | Both mutually exclusive mounts omit projection. The separate zero-prop semantic host supplies no managed region channel. Manifest scope is VFO/RX-TX. |

Definition sites and calls were established with `rg --files`, bidirectional symbol searches and complete reads of the relevant blocks. The coordinator separately read `SpectrumPanel` management props, display bindings, `completeGestureAuthority`, managed projection effect and subscription effect; `RadioLayout:scopeRegion`; both mobile mounts; `SemanticRadioSurfaces:managedScope`, `refreshScopePassband`, `managedScopeRegion`; and `mobile-declarations:MOBILE_ZONES`.

The researcher enumerated nine modules. Whole-module lexical function/method counts were respectively 1, 23, 22, 34, 24, 8, 12, 0 and 11 for RadioLayout, MobileRadioLayout, SpectrumPanel, SemanticRadioSurfaces, ScopeController, ScopeFrameHost, passband reducer, projection contract and scope adapter. Large components additionally received a scope-specific census: mobile has zero management props and two raw mounts; SpectrumPanel has three management props, eight scope lifecycle/projection bindings and three demand functions; semantic wiring has twelve scope bindings and five scope functions. Each scope-specific binding/function has a read outside its definition. Svelte lexical counts are an enumeration aid, not execution counts or a repository-wide dead-code proof.

Live legacy consumers also include a RadioLayout fallback and the flagship diagnostic probe. Public controller getters with tests-only reads, exported types and dynamic component/skin boundaries are not cleared for deletion. External consumer absence was not established.

## Prior rulings and in-flight work

- `docs/plans/2026-04-12-target-frontend-architecture.md`, INV-2 and INV-5 (2026-04-12), assigns scope ownership to ScopeController and states: “Swapping layouts or mounting/unmounting visual components must not start or stop transports.”
- `docs/plans/2026-07-25-ui-composition-discovery-and-parity.md`, mobile composition and resource rows (2026-07-25), identifies mount-dependent resource ownership as a gap and targets persistent contracts. This is architectural intent, not proof of current completion.
- `mobile-declarations.ts:MOBILE_ZONES` explicitly describes only VFO/RX-TX for this migration slice, and no landscape semantic zone. Live Linear MOR-1094, read 2026-09-10, is Done for TX/actions/resources/focus; its criteria do not claim coherent passband adoption.
- Existing tests deliberately support omitted/undefined `scopeProjection` as legacy mode (`SpectrumPanel.component.test.ts`). This is evidence against deleting the mode immediately.
- MOR-2368 already supplied frame provenance on the existing host. MOR-2437 changed managed continuity. MOR-2441 now owns the idle A/B recovery regression; MOR-2440 and MOR-2439 retain their separate continued-tuning scopes. MOR-1343 is a fixture-matrix follow-up, not a mandate to build another scope surface.

## Steelman

The current arrangement preserves compatibility while layouts migrate in slices. Mobile deliberately retains different portrait/landscape markup and gestures; its older panel mode is explicit and tested. Both paths still share the resource controller, radio adapters, geometry math and renderer. The managed reducer adds evidence-qualified continuity; strict command authority serves a different purpose. Therefore two props paths alone do not justify merging controllers, deleting a renderer, or using display fallback as command authority.

This defense explains why legacy support remains live. It does not justify permanent differences in the recovery of the same radio passband across layouts. The shared managed contract already exists, and mobile currently has no delivery channel for it. The missing work is consumer composition, with lifecycle acceptance, rather than a new shared primitive.

## Deletions

None cleared. No scope-specific definition-only candidate survived the bounded census. Legacy mode still has production consumers. Tests-only public getters and potential external consumers remain undetermined for deletion; no test or API removal is authorized by this report.

## Consolidations

### F1 — Hardware passband recovery: incomplete migration leaves divergent live policies

Verdict: A — display acquisition/continuity policy remains in the legacy renderer path while its shared target already exists.

Rank: diverged.

Elements: `SpectrumPanel.svelte:managed`, `displayFrequencyHz`, `confirmedPassband` and raw subscription effect; `scope-passband-display.ts:projectScopePassbandDisplay`; `SemanticRadioSurfaces.svelte:refreshScopePassband` and `managedScopeRegion`.

Consumers: managed reducer -> semantic wiring -> Standard scope region -> panel; legacy policy -> mobile portrait, mobile landscape, legacy RadioLayout fallback and diagnostic probe.

Definition site: reducer in runtime adapters; remaining legacy policy in the panel; composition delivery in semantic wiring and layouts.

Divergence: managed display waits for a coherent post-retirement observation set; legacy display derives directly from current authority fields and raw frame geometry. Separate coordinator captures on the audited tree observed four long Standard blanks and brief mobile alias-hydration blanks. Mobile displayed geometry whose observation markers preceded selection; identical captured values do not prove a wrong numerical width, or correctness when slots differ.

Prior ruling: INV-2/INV-5 and the July parity plan above favor persistent shared behavior. The scoped mobile migration declaration explains compatibility but does not establish intentional permanent passband divergence.

In-flight: shared target exists and serves Standard. MOR-2441 fixes producer/projector delay without mobile edits. MOR-2442 owns the mobile consumer cutover.

Required surface: existing `ManagedScopeRegion` is sufficient as a contract. Mobile needs one composition channel delivering it to both orientations and retaining the existing resource ownership boundary.

Depends on: MOR-2441 recovery validation before claiming mobile/desktop sequence parity; inventory of other legacy consumers before deleting any fallback.

Confidence: high for the live consumer split; medium for a small mobile-only cutover size until lifecycle tests are implemented.

Falsifier: a reachable mobile managed-projection delivery channel at the audited revision, or an explicit later owner ruling requiring different passband recovery semantics by layout.

Fix class: consolidate onto the existing projection; do not build another controller, cache or renderer.

Actionable: yes, as MOR-2442 with portrait/landscape, layout-switch, demand-toggle, reconnect and audio-FFT compatibility tests. Keep it separate from the bounded A/B bug fix because changing a mobile resource lease is additional behavior.

### F2 — Controller, adapters and renderer: already shared

Verdict: already-shared.

Rank: parallel premise rejected.

Elements: `ScopeController`, `ScopeFrameHost`, `scope-adapter` primitives, `SpectrumPanel` renderer.

Consumers: both managed and legacy paths converge on the same definitions; host is an evidence projection over the controller, not a second transport.

Definition site: runtime controller/host/adapters and shared spectrum component, as listed above.

Divergence: none establishing duplicate transport, parser, geometry math or complete renderer. Their responsibilities differ.

Prior ruling: target frontend layering and INV-2 support these boundaries.

In-flight: current shared APIs already exist.

Required surface: exists.

Depends on: none.

Confidence: high within scope.

Falsifier: a distinct live controller/parser/renderer definition reached only from one audited UI path.

Fix class: none.

Actionable: no consolidation or deletion justified for these elements.

### F3 — Display continuity versus gesture authority: legitimately separate

Verdict: C — legitimately local concerns with shared strict authority input.

Rank: parallel premise rejected.

Elements: `scope-passband-display.ts:projectScopePassbandDisplay`; `SpectrumPanel.svelte:completeGestureAuthority`, `readAuthority`.

Consumers: managed overlay rendering versus pan/filter interactions in the same panel.

Definition site: display reducer in runtime adapters; live gesture capture checks at the interaction boundary.

Divergence: retained display geometry may remain visible while an interaction still requires current eligible data. These answer different questions.

Prior ruling: MOR-2367/MOR-2437 scope explicitly separates display fallback from command/TX authority; existing reducer and component tests enforce that boundary.

In-flight: MOR-2441 preserves this separation.

Required surface: exists.

Depends on: none.

Confidence: high.

Falsifier: duplicated radio authority derivation inside either branch instead of consuming `toSpectrumAuthority`, or a requirement that display continuity itself authorize commands.

Fix class: none.

Actionable: no unification of these two responsibilities; preserve their distinct tests.

## Weakest link

F1's expected cutover size is the weakest inference. The panel accepts the existing contract, but that alone does not prove a mobile composition change preserves lease ownership through orientation replacement, demand toggles and audio-FFT compatibility. Test those first in MOR-2442; do not create a new controller to work around missing composition wiring.

## Cleared

One ScopeController/resource owner; shared frame and radio-authority adapters; one SpectrumPanel renderer and geometry implementation; immutable managed projection contract; separate display continuity and gesture-authority responsibilities. No dead-code deletion cleared. This audit is not a release or hardware-acceptance verdict.
