# Mechanism audit: meter source and context

Audited revision: `f2e7969708d5c93890cf1b24d833ce820b36dc15`, 2026-09-06.

Method: `.claude/skills/mechanism-audit/SKILL.md`, read in full from the audited repository, and `.claude/agents/auditor.md`. Repository guidance and live owner acceptance were read. This is the bounded meter source/context decision tract. It does not accept all alternate meter implementations, scopes, the integrated product, or the final whole-path audit. Source inspection used immutable Git objects; no audited-tree edits, Git writes, tests, builds, installs, browser work or hardware operations occurred.

## Steps 0-3a: evidence before judgement

### Actual definitions and consumer edges

| Capability | Definition | Actual consumers |
| --- | --- | --- |
| Display observation qualification | `frontend/src/lib/runtime/adapters/display-observation.ts:qualifyDisplayObservation,qualifyRadioDisplayObservation,qualifyEvidence` | Radio view-model adapter and scope-passband display adapter; qualifier tests |
| Active receiver identity | `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts:activeReceiverId` | `toRadioViewModel`; single receiver is MAIN by topology, multiple receivers require observed active identity |
| Canonical meters | Same adapter: `deriveMeters,meterField,displayTxMeter` | `frontend/src/semantic/MetersSurface.svelte`; `frontend/src/semantic/radio-display-model.ts:projectPeerSplitDisplay` for six telemetry fields |
| Receiver indicator S-meter | Same adapter: `deriveReceiverIndicators` | `frontend/src/semantic/VfoIndicatorRow.svelte`; Standard `frontend/src/semantic/VfoSurface.svelte:standardInstrument` passes its S-meter to `frontend/src/components-v2/vfo/VfoPanel.svelte` |
| Semantic meter projection | `frontend/src/semantic/MetersSurface.svelte:observed,signalDisplay,txPresentation,swrLowerScale` | One MetersSurface mount in SemanticRadioSurfaces, placed through three zone paths; root consumers include desktop, mobile, LCD and alternative skin compositions |
| TX presentation semantics | `frontend/src/semantic/tx-meter-display.ts:projectTxMeterDisplay` | MetersSurface and radio-display-model's TX telemetry projection |
| PBT continuity | `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:pbtEvidence,pbtFloorKey,pbtObservationFloors` | Local PBT retained/canonical overlay; no meter consumer of this state |
| Shared ballistics | `frontend/src/primitives/meters/meter-ballistics.svelte.ts:createMeterBallistics,createMeterBallisticsGroup,createTickerLifecycle,createPeakChannel` | LinearSMeter, BarGauge, and MetersDockPanel respectively; direct lifecycle tests |
| Peak algorithms | Same primitive: `createFrameStepPeakStrategy,createElapsedEnvelopePeakStrategy` | Frame strategy: LinearSMeter. Elapsed envelope: BarGauge and dock group |
| Imported math | `frontend/src/lib/utils/smoothing.svelte.ts:createSmoother`; `frontend/src/components-v2/panels/meter-utils.ts:updatePeakHold,peakHoldDisplay`; meter scale/calibration helpers | Imported into ballistics consumers; imports are not additional definitions |

Observation: qualifier, relevant adapter ranges, receiver-indicator derivation, semantic meter surface, both shared renderer scripts/templates, complete ballistics module, PBT continuity range, TX display projector, peer-split projection and Standard VFO mount were independently read. Backend observation admission and public field-status schema were also read. The independent collection supplies the broader layout importer census and imported math definition locations; this tract did not reopen every alternate layout or every math body.

Observation: Standard's `standardInstrument` now directly mounts VfoPanel with `indicator.sMeter` reading and availability. The meter implementation files being unchanged across a merge does not make the consumer graph unchanged. VfoPanel is not a dead legacy consumer. The legacy adapter paths and dock fallback remain additional consumers in the collection; no deletion of either component follows from accepted layouts preferring semantic meters.

### Dated rulings and accepted work

- `docs/plans/2026-07-25-ui-composition-architecture-v3.md` (2026-07-25): "Adapters are pure and depend only on runtime/domain contracts." Pure transient presentation semantics are allowed; capabilities stay authoritative below presentation.
- `docs/architecture/level-meter-calibrated-domain.md`, accepted calibrated-domain decision: "the DOMAIN, the unit vocabulary, and the interpolation ALGORITHM are GLOBAL"; correction curves are per rig. Its acceptance is recorded in that ADR; no new acceptance date is inferred here.
- `.claude/audits/2026-08-30-mechanism-audit-state-feedback.md` distinguishes governed migration from duplicate truth stores, clears the shared loose availability helper, and records legitimate local visual grammar. Its old findings are a point-in-time record, not proof of the current tree.
- `docs/plans/2026-04-12-target-frontend-architecture.md:Addendum 2026-09-02` locates common instruments under primitives and makes display models/geometry presentation inputs.
- Source and collection identify accepted radio-wide P/SWR/ALC display qualification and PBT fenced retention on 2026-09-05. Accepted shared ballistics and dock adoption followed on 2026-09-06. These mechanisms exist in the pin and are not proposals to rebuild.
- Live acceptance read on 2026-09-06 permits different internal algorithms, requires honest source/context behavior and reserves a later full-path audit. Prior meter context notes are unaccepted proposals and do not select the result below.

### Systematic sweep coverage

The collection enumerates named functions, constants, mutable/assigned locals, component props and method names across seven modules. For the two large modules it bounds definitions to active selection/meter helpers and session/PBT context, respectively; their other control/scope families are excluded. Source and test lexical counts are separated, with exported API consumer tables.

Independent declaration-line checks over those same scopes give the following reproducible coverage counts. Function declarations and declaration lines are distinct categories; the latter includes constants, mutable bindings and arrow-valued declarations, not an AST total of bindings:

| Module or bounded range at the pin | Named function declarations | const/let/var declaration lines |
| --- | ---: | ---: |
| display-observation.ts | 6 | 7 |
| radio-view-model-adapter.ts, lines 121-195 and 269-339 | 10 | 20 |
| MetersSurface.svelte, before stylesheet | 2 | 15 |
| SemanticRadioSurfaces.svelte, lines 473-477 and 598-696 | 0 | 30 |
| meter-ballistics.svelte.ts | 13 | 29 |
| LinearSMeter.svelte, before stylesheet | 10 | 78 |
| BarGauge.svelte, before stylesheet | 2 | 21 |

The counts were obtained from pinned source text with anchored function and declaration patterns. The collection's per-name lists supplement these counts with markup references and object methods. Method signatures are not executable method implementations. Broad names such as `state`, `view`, `sample` and `now` collide across closures; ballistics aggregate counts and local assignment totals use different denominators and must not be summed or interpreted as data flow. Source/test absence in a lexical row is not proof of an absent interface consumer.

The only independently established dead candidates are D1/D2. Reproduction:

```sh
git grep -n -w -e DBM_Y -e S_UNIT_Y f2e7969708d5c93890cf1b24d833ce820b36dc15 -- frontend/src frontend/tests src tests
```

Exactly two declaration hits were returned. Full LinearSMeter script/template inspection found actual readout positions expressed directly using TRACK_Y/TRACK_H. No runtime export, dynamic lookup, serialization, or public prop exposes either local. An available sibling source tree had no literal hits. Unknown external consumers remain a guard for public APIs, but cannot reach these component lexical locals through their public interface. No tests-only deletion and no additional constant-guard unreachable branch was established.

This sweep is systematic but bounded and partly lexical. It does not clear all alternate meter paths or prove all remaining local bindings live.

### Source and time contracts

Observation: `display-observation.ts:validIdentity` requires matching safe nonnegative state/caps provider generations and contract version 1. Receiver qualification also validates topology/receiver object/path consistency. `qualifyEvidence` checks the leaf, any present ancestors, finite nonnegative field markers, availability/freshness and valid scalar values. Its return value exposes current/stale/unknown/unsupported plus value, not reusable provider/receiver/path/session/marker context.

Observation: `radio-view-model-adapter.ts:deriveMeters` chooses SUB only from raw `state.active === 'SUB'` and otherwise MAIN. It does not consume the `activeId` already computed by `toRadioViewModel`. `meterField` combines finite numeric values with the loose `topFieldAvailable` gate. `deriveReceiverIndicators` uses canonical structural/operational receivers and strict per-field observation gates. P/SWR/ALC additionally carry qualified `display`; signal, compression, voltage and current do not have that display path.

Observation: `src/rigplane/core/state_store.py:StateStore._strictly_older_than_entry,_strictly_older_than_observation` compares markers only when provider generation and non-null clock domain match, using strict `<`. `StateStore._apply_one` accepts an equal marker and increments observation sequence. Therefore marker equality is not a deduplication contract. `src/rigplane/web/state_schema.py:FieldStatusPublic` and `frontend/src/lib/types/state.ts:FieldStatusPublic` expose lastObservedMonotonic, but no typed clock-domain field. An open-ended `source` object is not a documented equivalent clock identity.

Observation: LinearSMeter supplies `performance.now()`/animation-frame time to frame ballistics; BarGauge supplies `Date.now()` to interval ballistics. Both are local animation clocks. No backend-to-browser epoch conversion is established. Comparing a backend marker directly with either clock would invent a contract.

## Step 4: steelman

The strongest case for the current arrangement is that a station dock and a receiver strip answer different presentation questions. A dock chooses an active receiver, applies TX relevance and combines telemetry. A receiver strip has a fixed receiver identity and preserves a shell for each structural receiver. Those differences justify distinct projections and structural policies. The old loose field-availability helper also has an explicit compatibility history; tightening it globally would affect many unrelated controls.

Display evidence and editable/current readings need not be identical. Stale information may remain available as marked display evidence while interaction and current meter motion are unavailable. The accepted P/SWR/ALC projector already distinguishes idle, relevant and indeterminate RF state. Flattening everything to one known/unknown number would erase that policy.

Ballistics is already shared, with deliberately different peak algorithms. Frame-step behavior and elapsed envelopes need not become one formula. A design-language segment-count or calibration change can change the smooth target without another radio observation; an observation-only update gate would freeze valid visual remapping. A marker's equality also does not imply no new sample under the backend contract.

PBT has real retention requirements and a local floor mechanism. But it has no meter consumers, its policy is field-specific, and lifting its strict marker floor wholesale would import a monotonic uniqueness assumption the meter source contract does not supply. Its existence proves that real session evidence is accessible at composition, not that PBT's whole retention algorithm is the generic meter solution.

These arguments clear presentation differences and existing shared mechanisms. They do not justify a canonical signal meter selecting a receiver when canonical active identity is unknown, nor do they supply a way for a persistent ballistics instance to distinguish a new provider/session/receiver from the old one. Those narrower gaps are the findings.

## Deletions

### D1 - LinearSMeter S_UNIT_Y: dead

Verdict: dead.
Elements / definition site: `frontend/src/components-v2/meters/LinearSMeter.svelte:S_UNIT_Y`.
Consumers: none; actual readout positioning uses inline TRACK_Y expressions.
Written / read: one declaration, zero further writes, zero source reads, zero test reads/writes, using the exact whole-word search above and full component inspection.
Guards checked: no local dynamic access or exposure, no export/prop/serialization path, no sibling literal consumer, no tests-only consumer. External renderer consumers cannot access the lexical binding.
Collateral: none; keep S_UNIT_FS and TRACK_Y.
Prior ruling: no retention requirement found for this coordinate; collection traces its introduction to 2026-03-17.
In-flight: none found.
Required surface: none.
Depends on: none.
Confidence: high.
Falsifier: a live template/reference or explicit component exposure of this binding.
Fix class: delete.
Actionable: yes.

### D2 - LinearSMeter DBM_Y: dead

Verdict: dead.
Elements / definition site: `frontend/src/components-v2/meters/LinearSMeter.svelte:DBM_Y`.
Consumers: none; actual readout positioning uses TRACK_Y/TRACK_H directly.
Written / read: one declaration, zero further writes, zero source reads, zero test reads/writes, same exact search as D1.
Guards checked: dynamic, external, public and tests-only guards as D1; no binding exposure exists.
Collateral: none; keep DBM_FS and TRACK_H.
Prior ruling: none found requiring this local; collection traces introduction to 2026-03-17.
In-flight: none found.
Required surface: none.
Depends on: none; independent of D1.
Confidence: high.
Falsifier: an actual reader or exposure mechanism.
Fix class: delete.
Actionable: yes.

## Consolidations

### F1 - Meter source qualification: shared predicates exist, consumer coverage is inconsistent

Verdict: B - a consistent meter source/context projection is missing; not a missing global availability helper.
Rank: diverged.
Elements / definition sites: `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts:deriveMeters,meterField,deriveReceiverIndicators,activeReceiverId`; `frontend/src/lib/runtime/adapters/display-observation.ts:qualifyDisplayObservation,qualifyRadioDisplayObservation`.
Consumers: deriveMeters feeds MetersSurface and peer-split telemetry. Receiver indicators feed VfoIndicatorRow and the now-mounted Standard VfoPanel. The existing qualifiers serve P/SWR/ALC and other display families, plus scope-passband qualification; signal/current/voltage/compression use the older reading path.
Divergence: observation: raw active selection and loose availability can yield a known canonical meter while strict receiver indicators remain unknown; state/caps identity mismatch is checked by qualified display, not by meterField. Inference: two presentation questions legitimately differ in placement/relevance, but qualification of a value as belonging to a known source must not be silently selected by that placement.
Prior ruling: 2026-07-25 pure adapter contract; strict multi-receiver identity and single-receiver MAIN rule recorded in adapter history on 2026-08-11; 2026-08-30 audit clears the shared loose helper rather than mandating global tightening; 2026-09-06 full instrument acceptance requires source/context honesty.
In-flight: existing qualifiers and canonical active identity are available; qualified P/SWR/ALC is accepted. No all-meter source/context contract is present at this pin.
Required surface: a read-only meter projection using actual matching provider identity, actual canonical receiver selection for active-receiver meters, appropriate radio-wide identity for telemetry, field/path observation evidence and explicit current/stale/unknown/unsupported semantics. It must make qualified continuity context available without persisting another truth store. Preserve each consumer's structural shell/relevance policy and distinguish stale display from current reading. Existing qualifiers/identity derivation remain the source of admission rules.
Depends on: none for qualification; F2 consumes continuity evidence. Any global availability semantic change is outside this finding.
Confidence: high on inconsistent qualification; medium on the precise scope of the shared context return type.
Falsifier: an upstream guarantee or actual common projection making these raw/strict paths equivalent for all admitted provider, active-identity and freshness states.
Fix class: design.
Actionable: yes for bounded qualification/context coverage; not authorization to merge all adapters or silently rewrite unrelated legacy availability behavior.

### F2 - Shared ballistics cannot represent a change of source

Verdict: B - continuity input gap in an already-shared lifecycle.
Rank: displaced (missing shared surface would otherwise force client reset mechanisms).
Elements / definition sites: `frontend/src/primitives/meters/meter-ballistics.svelte.ts:MeterBehaviorInput,createMeterBallistics.sync,createMeterBallisticsGroup.sync`; `frontend/src/components-v2/meters/LinearSMeter.svelte:ballistics` and sync effect; `frontend/src/components-v2/meters/BarGauge.svelte:ballistics` and sync effect.
Consumers: LinearSMeter uses frame-step/smoothed input; BarGauge uses sample/envelope input; MetersDockPanel uses the group. Semantic meter props pass values/presence/display, and Standard's VfoPanel passes a numeric S-meter. None supplies a meter stream identity to ballistics.
Divergence: observation: null input resets smoother and peak, but changing source with a non-null value has no identity input and can retain the old peak/smooth history. A session/receiver/provider-only change can leave a component's numeric dependency unchanged. Inference: the shared owner cannot enforce continuity boundaries it is never told about; relocating another copy of peak math would not solve this.
Prior ruling: accepted shared ballistics on 2026-09-06; same-day instrument acceptance includes reconnect/context changes. The calibrated-domain ADR preserves existing math ownership.
In-flight: `createMeterBallistics` and group are actual accepted mechanisms, not replacements to invent.
Required surface: supplied, qualified source identity and invalidation semantics sufficient for the existing shared lifecycle to reset/reseed on real provider/session/receiver/field changes and unavailable evidence. Observation progression and visual smooth-target remapping must remain distinct inputs/semantics: geometry/calibration changes can need remapping without a new observation. If an observation marker is used, its clock domain/order/uniqueness must come from a real contract; equal markers cannot automatically suppress updates. Preserve existing frame/envelope/reduced-motion policies.
Depends on: F1's qualified context for production adoption. Any stronger sequencing promise requires evidence covered by F5.
Confidence: high on absent input and possible retained cross-source state; medium on precisely which local remap/reset policy preserves each visual strategy.
Falsifier: every actual source change always unmounts the meter or delivers a guaranteed null/reset before any non-null replacement, with explicit production contracts covering provider and session changes as well as receiver switches.
Fix class: design.
Actionable: yes at the shared lifecycle input boundary. No second smoother, new poller or client-specific duplicated reset owner is earned.

### F3 - Current versus stale TX display: intentional distinct projection

Verdict: C - legitimately distinct display semantics, using an already-shared projector.
Rank: name-collision (related facts are not interchangeable).
Elements / definition sites: `frontend/src/semantic/tx-meter-display.ts:projectTxMeterDisplay`; `frontend/src/semantic/MetersSurface.svelte:txPresentation,swrLowerScale`; `frontend/src/semantic/radio-display-model.ts:txTelemetry,projectPeerSplitDisplay`; adapter `displayTxMeter`.
Consumers: MetersSurface P/ALC bars and SWR overlay; peer-split TX telemetry. Separate ordinary reading/availability remains available to other consumers.
Divergence: observation: P/SWR/ALC display qualification can produce stale/unknown while the older reading projection differs. The shared TX projector respects an explicit display value first, with a reading fallback only when display is absent. MetersSurface turns stale/idle into marked text and null motion input, preserving RF relevance cues. Inference: retaining marked display evidence is not duplicate confirmation truth.
Prior ruling: accepted radio-wide TX display qualification on 2026-09-05; 2026-07-25 allowance for pure transient display semantics.
In-flight: exists and is consumed by both projections.
Required surface: exists; F1 must preserve display/current-reading distinctions and avoid upgrading stale display to current motion or editable truth.
Depends on: none.
Confidence: high.
Falsifier: a consumer treats marked stale/unknown display as a current measurement or substitutes it for command confirmation.
Fix class: none.
Actionable: no consolidation of these distinct meanings; no clearance of all fallback consumers outside C1.

### F4 - Peak algorithms and renderer geometry: already shared or correctly local

Verdict: already-shared for lifetime and imported math; C for explicit peak policy and geometry.
Rank: parallel (candidate cleared).
Elements / definition sites: `frontend/src/primitives/meters/meter-ballistics.svelte.ts:createTickerLifecycle,createPeakChannel,createFrameStepPeakStrategy,createElapsedEnvelopePeakStrategy`; `frontend/src/components-v2/meters/LinearSMeter.svelte:SEG_COUNT,segX,generateTicks`; `frontend/src/components-v2/meters/BarGauge.svelte:segX`.
Consumers: frame strategy in LinearSMeter; envelope in BarGauge and dock group. Renderer geometry serves the respective gauge; imported smoothing/peak math has the definition sites listed above.
Divergence: frame-step decay and elapsed-time envelope are deliberately different algorithms behind the shared lifecycle. LinearSMeter smooth targets are scaled by dynamic segment count; BarGauge uses its own segment normalization. Different visual grammar does not imply duplicate radio truth.
Prior ruling: accepted ballistics adoption on 2026-09-06; 2026-09-02 display-model geometry ruling; calibrated-domain ADR reserves global interpolation math.
In-flight: exists and consumed; C1 context work is a bounded addition, not unfinished peak-math consolidation.
Required surface: exists for current algorithms; F2 supplies continuity without erasing their policy differences or freezing visual remaps.
Depends on: none.
Confidence: high.
Falsifier: independent peak lifetime/math definitions bypassing the cited owner in these exact scoped consumers, or an undeclared numeric-domain conversion mismatch.
Fix class: none.
Actionable: no algorithm unification or copied smoother implementation.

### F5 - Marker deduplication and reconnect floors: evidence insufficient for a universal rule

Verdict: undetermined for proposed universal ordering/deduplication; C for retaining PBT's bounded existing policy pending separate evidence.
Rank: diverged (potentially drops valid observations if generalized).
Elements / definition sites: `src/rigplane/core/state_store.py:StateStore._apply_one,_strictly_older_than_entry,_strictly_older_than_observation`; `src/rigplane/web/state_schema.py:FieldStatusPublic`; `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:pbtEvidence,pbtObservationFloors`; `frontend/src/primitives/meters/meter-ballistics.svelte.ts:MeterMotionHost`.
Consumers: backend admission accepts field observations; frontend field status feeds qualifiers. PBT floor state serves its local overlay only; ballistics consumes local animation clocks, not backend clock identity.
Divergence: observation: backend accepts equal markers and only compares ordered markers inside matching generation/clock domain. Public frontend field status lacks typed clock-domain identity. PBT's strict greater-than floor is not consumed by meters. Inference: neither that floor nor Date.now/performance.now proves a generic meter deduplication or post-reconnect freshness contract. A newer global observation sequence also does not alone prove a particular unchanged field was reobserved.
Prior ruling: PBT retained-display acceptance on 2026-09-05; backend/public contracts at the audited pin. No universal field-marker uniqueness ruling found.
In-flight: actual control session/epoch is already available at composition and used by PBT/scope. No proven universal meter floor/sequence contract exists.
Required surface: explicit evidence for field-specific ordering, clock comparability and reconnect admission before any strict marker filter is applied to meters. Local animation timing stays separate from radio observation identity. Until then, context invalidation can be specified honestly without fabricating per-field freshness or suppressing equal-marker observations.
Depends on: none to reject an unsupported assumption; F1/F2 may use only the context promises actually available.
Confidence: high that stronger guarantees are unestablished.
Falsifier: a published producer/transport contract guaranteeing field-specific unique progress and comparable marker clocks across the exact reset boundaries, with actual consumer evidence.
Fix class: none for unproven generalization.
Actionable: no universal floor/deduplication implementation from the present evidence; no deletion or wholesale extraction of PBT state.

## Weakest link

F2's production continuity guarantee is the weakest link. The missing identity input is certain, but the exact policy for geometry remapping versus reseeding, and for admitting an old-looking field after reconnect, needs a real consumer/producer contract. First trace one persistent canonical S-meter through an active-receiver change with equal numeric values, then a provider/session change with unchanged field markers, and finally a segment-count change without a radio observation. A solution must not carry old-source peaks, invent new observation evidence, or discard a valid visual remap. No runtime test was performed in this read-only audit.

## Cleared

- Display qualification and canonical active-receiver derivation already exist and must be reused.
- Single-receiver MAIN by valid topology is legitimate; raw MAIN fallback on unresolved multiple receivers is a different condition.
- Shared meter ballistics, ticker lifecycle, smoother and peak strategies are not duplicate implementations merely because multiple renderers import them.
- Frame-step and elapsed-envelope strategies, reduced-motion behavior and visual geometry may legitimately differ.
- Current readings and marked stale TX display evidence are distinct valid contracts.
- Standard VfoPanel is an actual current consumer; unchanged meter bytes do not establish unchanged liveness.
- PBT continuity is live, bounded behavior, not proven dead and not a ready-made universal meter mechanism.
- Equal backend markers are permitted; frontend animation clocks do not define backend observation time.

Implementation, alternate meter/scope tracts, public archive review and the final integrated whole-path audit remain separate work.
