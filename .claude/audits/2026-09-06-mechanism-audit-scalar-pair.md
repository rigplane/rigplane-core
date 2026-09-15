# Mechanism audit: scalar and RF/SQL pair

Audited revision: `f2e7969708d5c93890cf1b24d833ce820b36dc15` (2026-09-06, `feat: unify HBar scalar ownership (#3244)`).

Method: `.claude/skills/mechanism-audit/SKILL.md`, read in full from the audited repository, with `.claude/agents/auditor.md`. `AGENTS.md`, `CLAUDE.md`, and the live acceptance criteria were also read. This is a decision audit of the bounded scalar/pair tract, not the final integrated whole-path audit. Source conclusions use immutable Git objects at the full revision above. HEAD and checkout status were inspected at entry; subsequent conclusions do not depend on the mutable checkout. No audited-tree edits, Git writes, tests, builds, installs, browser work, or hardware operations were performed.

## Steps 0-3a: evidence before judgement

### Definitions and consumers

All paths below are repository-relative. In the tables, `controls/` means `frontend/src/components-v2/controls/value-control/`; `scalar/` means `frontend/src/primitives/scalar/`. These abbreviations do not denote additional modules.

| Capability | Actual definition | Consumers per implementation |
| --- | --- | --- |
| Continuous scalar lifetime and feedback | `scalar/continuous-scalar.svelte.ts:createContinuousScalar` | `controls/ValueControl.svelte:createRawBinding` for built-in HBar; `frontend/src/semantic/FilterSurface.svelte:filterWidthScalar` as collected; HBar consumes a renderer lease |
| Committed scalar editing | `scalar/committed-scalar.svelte.ts:createCommittedScalar` | `frontend/src/components-v2/panels/CwPanel.svelte:breakInDelayScalar` and `frontend/src/semantic/CwKeyerSurface.svelte:breakInDelayScalar` as collected |
| Feedback presentation | `frontend/src/primitives/control-feedback/control-feedback-presentation.ts:projectControlFeedbackPresentation` | Both scalar factories; their renderers consume the result |
| Scalar local interaction | `controls/BipolarRenderer.svelte:emitChange,markWheelActive,handleKeyDown`; `controls/DiscreteRenderer.svelte:emitChange,markWheelActive`; `controls/KnobRenderer.svelte:emitChange,handlePointerMove` | Bipolar: ValueControl routes in FilterPanel and RitXitPanel; Discrete: CwPanel, DspPanel, VoxPanel; Knob: ValueControl demo and lab routes and tests. No production Knob adoption is established by those demo routes |
| Custom knob behavior | `controls/skins/ProfessionalKnob.svelte:emit,onMove,onWheel,onKey` | Three direct demo mounts in `frontend/src/components-v2/controls/ControlButtonDemo.svelte`; public registry and barrel exports. `frontend/src/App.svelte` dynamically loads that demo |
| Combined axis math | `scalar/value-control-core.ts:dualParamValuesFromNormX,dualParamNormXFromValues,dualParamStepAlongAxis` | Forward/inverse: semantic RF/SQL and DualParamRenderer, with thumb/deviation wrappers for the latter; stepping: DualParamRenderer; direct math tests |
| Pair interaction and lane dispatch | `frontend/src/semantic/RfFrontEndSurface.svelte:changeCombined`; `controls/DualParamRenderer.svelte:emitPair,handleWheel,handleKeyDown` | SemanticRadioSurfaces mounts the semantic surface; legacy RfFrontEnd mounts DualParamRenderer. RfFrontEnd has mount sites in LeftSidebar, MobileRadioLayout and RadioLayout |
| RF/SQL command dispatch | `frontend/src/lib/runtime/commands/panel-commands.ts:makeRfFrontEndHandlers`; `frontend/src/lib/runtime/commands/radio-intents.ts:dispatchRadioIntentWithResult` | Legacy normalized adapter, semantic handler binder/wiring, keyboard intent path. Each actual intent calls `beginCommand` and `sendCommand` |
| State-backed command confirmation and projection | `frontend/src/lib/stores/commands.svelte.ts:STATE_BACKED_COMMAND_DESCRIPTORS,reconcileStateBackedCommands`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback` | Registered Filter Width and Break-in Delay. No RF gain or squelch descriptor or corresponding full-feedback accessor at this revision |

Observation: the core factories, four remaining renderer scripts, custom knob script, pair implementations, ValueControl, skin contracts, feedback projector, command descriptors/reconciliation, RF handler factory and dispatch envelope were opened. Source searches verified the named mount sites and public exports. The Filter/CW adopter sites above are collection-backed; their whole component bodies were not independently reopened for this tract. Imports and interface method signatures are not counted as independent implementations.

### Prior rulings and accepted target

- The architecture addendum of 2026-09-02, `docs/plans/2026-04-12-target-frontend-architecture.md:Addendum 2026-09-02`, records: "the single home going forward is `frontend/src/primitives/`" and "the skin composes." Its instrument conformance criteria require honest unknown and externally supplied appearance. The document identifier and dated section are the public ruling reference.
- `docs/internals/skins-presentation-boundary-gate.md` (2026-08-31) protects custom instruments and explicitly bounds the meter census to its directory. A custom visual implementation is permitted; shared radio truth remains below presentation.
- `docs/release-notes/2026-beta-known-limitations.md`, combined RF/SQL limitation, records the absence of local gesture tracking on the semantic path. This is evidence of a difference, not a prescribed remedy.
- `frontend/src/semantic/RfFrontEndSurface.svelte:changeCombined` and `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:RF_FRONT_END_LEVEL_INTENT` preserve the combined profile mode, independently usable lanes and per-lane change guards. The collection dates the original change to 2026-08-11; current source independently establishes the contract, not that historical date.
- The live owner acceptance, read on 2026-09-06, requires a stable instrument model/intent interface with replaceable rendering, permits materially different algorithms behind it, and requires decision adjudication before pair architectural implementation. Earlier scalar/pair plans are not premises here.

Observation: the accepted revision already contains `ContinuousScalarBinding`, HBar's lease adoption and the HBar render-presentation projection. This is an existing migration target, not a proposed foundation. A pair binding does not exist at this revision. Full feedback exists for Filter Width and Break-in Delay, not RF/SQL. No proposed implementation outside the pin is counted as accepted source.

### Sweep coverage and limits

The repaired systematic inventory enumerates 833 executable entries over 18 implementation files, plus the interface-only `controls/skin.ts`: 157 functions, 274 constants, 351 mutable bindings and 51 object methods. It separates source, source-tree tests, other test files, browser-test paths and fixtures. Interfaces/type signatures and import/re-export declarations are excluded from executable definitions. Counts below are enumeration coverage, not proof of liveness:

| Module | Named entries |
| --- | ---: |
| value-control-core | 89 |
| continuous-scalar | 95 |
| committed-scalar | 29 |
| control-feedback-presentation | 11 |
| ValueControl | 60 |
| HBarRenderer | 45 |
| BipolarRenderer | 64 |
| DiscreteRenderer | 65 |
| KnobRenderer | 69 |
| ProfessionalKnob | 51 |
| DualParamRenderer | 64 |
| scalar-render-presentation | 2 |
| skins/index | 2 |
| RfFrontEndSurface | 34 |
| RfFrontEnd | 33 |
| SemanticRadioSurfaces, RF range only | 7 |
| panel-adapters, RF/feedback ranges only | 28 |
| commands.svelte | 85 |

This supersedes the earlier 352-entry extraction, which omitted closure state/object methods and misclassified test references. Repaired positive witnesses include `clamp` with 31 resolved source reads and 4 test reads, `snapToStep` with 24/7, and separately enumerated scalar drafts and timers. These are collector measurements, not rerun tests. There are 403 entries with unresolved source attribution, especially shadowed identifiers and Svelte markup. Object properties also require manual tracing: nominal zero-count methods such as policy `wheel` and lease `pointer` are called through interfaces, as the opened owner and HBar code show. Public functions such as `confirmCommand` and `getCommandLifecycleHold` likewise cannot be deleted from direct-import counts. The repaired inventory improves systematic coverage; it does not authorize a blanket zero-read or exhaustive dead-code verdict.

The zero-read candidate `KnobRenderer:indicatorPos` was independently checked with:

```sh
git grep -n -w -e indicatorPos -e indicatorLength f2e7969708d5c93890cf1b24d833ce820b36dc15 -- frontend/src frontend/tests src tests
```

It returns exactly the two declarations and the one `indicatorLength` use embedded in the `indicatorPos` declaration. The full KnobRenderer template was inspected; it uses `indicatorEnd`. Literal public/export/dynamic checks and an available sibling source search found no exposure of either local binding. The sibling search is a limited literal check, not a census of all downstream code.

Public functions such as `calculateDragValue`, or exported skin registry entries, do not become dead merely because an in-repository production call was not established. Public, dynamic and external-consumer guards prevent that verdict. No tests-only behavior is authorized for deletion by this report. No constant-return guard was established to make an additional scoped branch unreachable.

## Step 4: steelman

The strongest case for retaining the arrangement is substantial. A horizontal pointer position, vertical relative knob drag and browser-native range input need different geometry. Bipolar keyboard increments have a default-centered lattice; Discrete intentionally has its own notch presentation, reset opt-in and wheel increment; Knob reads incoming values rather than providing the bars' optimistic preview. HBar and native Filter already share a lifecycle while selecting different policies. There is no reason to make these algorithms identical merely because all edit numbers.

RF/SQL is not an ordinary scalar truth value. Its axis maps onto two independent radio fields, and the inverse loses information when both fields are away from the physical knob's compatible state. A projected thumb therefore cannot serve as both lanes' confirmed state. Separate commands can have separate availability, pending, failure and confirmation outcomes. Keeping lane truth independent is the strongest case against imposing a single command-feedback scalar. Conversely, native range movement and the legacy axis's wheel/key stepping need not share one numerical stepping algorithm.

A skin can legitimately own its graphics and pointer geometry. ProfessionalKnob is a real reachable demonstration of that facility, although it is not evidence of production adoption. A public registry is also a valid extension point with unknown external users. These facts defeat a vestigial-fork verdict.

The strongest case for change is narrower: renderer-owned debounce, local drafts and late callback validity are behavior, while appearance selection currently replaces those mechanisms and can omit the full feedback contract. The scalar owner already supplies most of this lifetime. Pair math is already shared, but no accepted contract combines a gesture with independently preserved lane evidence. The missing RF/SQL descriptors are separate from that instrument contract and cannot be replaced by draft state. The following per-element decisions preserve those distinctions.

## Deletions

### D1 - KnobRenderer indicatorPos: dead

Verdict: dead.
Elements / definition site: `frontend/src/components-v2/controls/value-control/KnobRenderer.svelte:indicatorPos`.
Consumers: none. Rendered indicator geometry uses `indicatorEnd`.
Written / read: one declaration assignment, zero subsequent writes, zero source reads, zero test reads/writes. Established by the exact literal whole-word search above and inspection of the full component.
Guards checked: dynamic access absent in the component; no export, prop or serialization path exposes this lexical local; the available sibling literal search has no hit; no tests-only consumer. Unknown external component consumers cannot access this unexported local through the declared component interface.
Collateral: D2 is its sole supporting calculation. Keep `calculateIndicatorPosition`, which has live callers.
Prior ruling: none found for this local.
In-flight: none found.
Required surface: none.
Depends on: none.
Confidence: high.
Falsifier: a template or executable local access to `indicatorPos`, or an explicit exposure mechanism in this pinned component.
Fix class: delete.
Actionable: yes.

### D2 - KnobRenderer indicatorLength: dead after D1

Verdict: dead, dependent calculation.
Elements / definition site: `frontend/src/components-v2/controls/value-control/KnobRenderer.svelte:indicatorLength`.
Consumers: only D1's initializer; none after D1.
Written / read: one declaration assignment, one source read solely in D1, zero test reads/writes. Same exact search as D1.
Guards checked: same lexical-local, dynamic, public, sibling and tests-only checks as D1.
Collateral: none; `radius` and `trackWidth` remain live.
Prior ruling: none found.
In-flight: none found.
Required surface: none.
Depends on: D1; deleting this alone would leave a reference.
Confidence: high.
Falsifier: another live reader or exposed binding.
Fix class: delete.
Actionable: yes, together with or after D1.

## Consolidations

### F1 - Remaining scalar lifetime: migration incomplete with diverged local behavior

Verdict: A - displaced behavior; existing shared target is only partly adopted.
Rank: diverged.
Elements / definition sites: `frontend/src/components-v2/controls/value-control/BipolarRenderer.svelte:emitChange,markWheelActive`; `DiscreteRenderer.svelte:emitChange,markWheelActive`; `KnobRenderer.svelte:emitChange`; `skins/ProfessionalKnob.svelte:emit`; target `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:createContinuousScalar`.
Consumers: Bipolar reaches FilterPanel/RitXitPanel; Discrete reaches CW/DSP/Vox panels through ValueControl. Knob reaches demo/lab; ProfessionalKnob reaches its direct demo and public registry. Target reaches HBar and native Filter. These are not four established production knob paths.
Divergence: observation: local bar drafts and 300 ms wheel locks differ from Knob's incoming-value display. Old debounced callbacks have no renderer lease or authority-generation check; the shared owner checks both. Pointer cancellation is handled as pointer-up on old renderers. Inference: late-input validity and feedback consistency currently depend on chosen appearance; those lifetime responsibilities belong behind the instrument interface.
Prior ruling: 2026-09-02 architecture addendum, "single home going forward" under primitives; 2026-09-06 acceptance permits explicit differing policies.
In-flight: accepted `createContinuousScalar` with HBar/Filter consumers; remaining listed paths do not consume it. This is not a missing scalar foundation.
Required surface: the existing continuous scalar lifetime and full evidence view, with explicit policies capable of preserving each live consumer's normalization, preview, dispatch, keyboard and reset behavior. Renderer attachment, cancellation and authority invalidation must survive appearance replacement. Geometry remains an input to that behavior.
Depends on: F1a for preserving Bipolar's invalid keyboard-hint fallback; F4 for the public custom-skin contract. Legitimate policies in F6 must remain explicit during adoption.
Confidence: high on displacement; the exact existing domain contract is insufficient for unchanged Bipolar keyboard hints, as F1a establishes.
Falsifier: an existing nonlocal owner already controls these renderer timers/drafts, or source proof that their callbacks are invalidated outside the renderer across replacement/context changes.
Fix class: consolidate.
Actionable: yes for behavior migration; no blanket instruction to copy HBar policy into all renderers or claim production migration through demo changes.

### F1a - Invalid keyboard hint versus invalid radio domain: policy compatibility gap

Verdict: B - gap in the accepted surface's ability to preserve a live policy unchanged.
Rank: diverged.
Elements / definition sites: `frontend/src/components-v2/controls/value-control/BipolarRenderer.svelte:getKeyboardIncrement,isLatticeCompatible`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:ScalarDomain,validDomain,editable`.
Consumers: Bipolar's keyboard handlers in Filter/RIT routes and `frontend/src/components-v2/controls/value-control/__tests__/ValueControl.test.ts` invalid-keyboardStep cases; shared validation currently serves HBar and Filter.
Divergence: observation: Bipolar falls back to the radio step for hints 0, -50, NaN, 2.5 on a step-5 lattice, and Infinity. The named tests were opened and assert this fallback, including emission of 5 for Infinity. Shared `validDomain` rejects nonpositive/nonfinite keyboardStep before policy invocation, disabling the scalar; it accepts positive 2.5 regardless of lattice compatibility. Inference: copying these hint values into the current shared domain cannot preserve Bipolar behavior merely by changing its key policy.
Prior ruling: 2026-09-06 acceptance requires preservation of existing input behavior and permits task-dependent policies; these executable test cases are the specific compatibility evidence.
In-flight: accepted domain validation exists for HBar/Filter; no Bipolar-compatible hint boundary is present.
Required surface: preserve valid canonical min/max/step independently of an invalid optional keyboard interaction hint, and permit the existing Bipolar fallback policy without weakening canonical-domain honesty or silently changing HBar's accepted behavior. This names the required distinction, not an implementation topology.
Depends on: none; F1's Bipolar adoption depends on resolving it.
Confidence: high.
Falsifier: an already-consumed adapter/policy boundary that normalizes or separates these hints before shared validation while preserving all cited cases.
Fix class: design.
Actionable: yes, as a bounded compatibility requirement for scalar migration.

### F2 - RF/SQL gesture plus two-lane evidence: a bounded shared surface is missing

Verdict: B - gap.
Rank: diverged.
Elements / definition sites: `frontend/src/semantic/RfFrontEndSurface.svelte:combinedNormX,changeCombined`; `frontend/src/components-v2/controls/value-control/DualParamRenderer.svelte:localRf,localSql,emitPair,handleWheel,handleKeyDown`; existing `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:ContinuousScalarInput,ContinuousScalarView,createContinuousScalar`.
Consumers: semantic path is mounted by SemanticRadioSurfaces; legacy pair is mounted by RfFrontEnd in the layout paths named above. Existing scalar owner serves HBar/Filter, not either pair implementation.
Divergence: observation: semantic input projects readings and has no owned draft; legacy retains local lane drafts and custom wheel/key behavior. Both use existing pair math and independently suppress unchanged lanes. The current scalar feedback variant carries one command, one scope and one lifecycle. Inference: neither moving the whole renderer nor representing the pair by one synthetic confirmed axis supplies independent two-lane truth.
Prior ruling: combined-control source contract and dated 2026-08-11 change evidence; current known-limitations entry records gesture-retention difference. 2026-09-06 acceptance permits distinct algorithms behind a stable interface.
In-flight: accepted scalar gesture lifetime and shared RF/SQL math exist; no pair contract exists at the pin.
Required surface: one replaceable pair-instrument interface that retains each lane's domain, known/unknown, availability, confirmed reading, requested/target, lifecycle phase and failure independently; represents the combined axis as a projection/draft; accepts native and explicit axis gestures with declared policies; preserves per-lane write guards and invalidates transient work when authority changes. Reading-only input must remain explicitly reading-only until F3 is available. The existing scalar lifecycle, feedback presentation and pair math must be accounted for before adding any new lifetime mechanism.
Depends on: F3 for full state-backed RF/SQL evidence, but not for an honestly labeled reading-only interface. F6's numerical/geometry distinctions are constraints.
Confidence: high that the surface is missing; medium on internal reuse topology.
Falsifier: an existing binding, consumed by both pair paths, already exposes independently scoped lane evidence and controls combined gesture lifetime.
Fix class: design.
Actionable: yes for the required surface. This finding does not select one scalar axis plus coordinator, two scalar owners, or a wholly independent pair owner. Their internal topology is undetermined by consumer evidence; a second confirmation owner is not earned.

### F3 - RF/SQL full feedback: descriptor and projection adoption gap

Verdict: B - gap in field coverage of an existing shared mechanism.
Rank: displaced (shared ownership pressure, not duplicate transport).
Elements / definition sites: `frontend/src/lib/stores/commands.svelte.ts:STATE_BACKED_COMMAND_DESCRIPTORS,reconcileStateBackedCommands`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback`; `frontend/src/lib/runtime/commands/panel-commands.ts:makeRfFrontEndHandlers`; `frontend/src/lib/runtime/commands/radio-intents.ts:dispatchRadioIntentWithResult`.
Consumers: Filter Width and Break-in Delay use descriptor reconciliation and projection. RF/SQL handlers use the actual tracked command dispatch, reached independently from legacy, semantic and keyboard paths; neither RF/SQL surface consumes a full per-lane feedback accessor.
Divergence: observation: RF/SQL commands already have real IDs, epochs, pending/delivery/failure lifecycle tracking. Their absence from the descriptor map means the descriptor-driven state confirmation/projector path used by Filter/CW is not provided to RF/SQL. Local drafts and changed readings are not that lifecycle evidence.
Prior ruling: 2026-09-06 instrument acceptance requires honest confirmed/requested/error information and reuse of command ownership; current shared descriptor/projector is the accepted source target.
In-flight: `projectControlFeedback` and descriptor-based reconciliation exist with the two named adopters; RF/SQL registrations/accessors do not.
Required surface: qualified per-field descriptors and read-only feedback projections for RF gain and squelch, using actual command receiver/session scope, normalized observation versus integer target semantics, and real field observation boundaries. Preserve two independent outcomes; no synthetic pair confirmation, new transport, queue or lifecycle store.
Depends on: none for descriptor/projection coverage; F2 consumes it. Actual RF/SQL confirmation matching and observation contracts must be verified in implementation review.
Confidence: high on missing coverage; exact numeric matching policy remains unadjudicated here.
Falsifier: existing registered descriptors and wired per-lane accessors at the pinned revision.
Fix class: design.
Actionable: yes, bounded to existing confirmation/projection extension; not proof that RF/SQL commands are currently untracked.

### F4 - ValueControl skin substitution: behavioral contract is part of the override

Verdict: B - missing appearance-level contract adoption.
Rank: displaced.
Elements / definition sites: `frontend/src/components-v2/controls/value-control/skin.ts:SkinRendererProps,KnobSkinRendererProps,Skin`; `ValueControl.svelte:skinComponent,commonProps`; `skins/ProfessionalKnob.svelte:emit,onWheel,onKey`; existing `HBarRenderer.svelte:lease` and `scalar-render-presentation.ts:projectScalarRenderPresentation`.
Consumers: built-in HBar takes a binding. Custom renderer dispatch takes raw props; ProfessionalKnob is a direct demo consumer plus the exported registry's knob component. No production registry adoption was established.
Divergence: observation: custom skin props include onChange and debounceMs but no scalar evidence view; ValueControl's full binding variant is HBar-only and prohibits a skin. Raw skin routes do not receive the HBar feedback projection. Inference: the current override replaces behavior, not merely appearance, so it cannot meet behavior-preserving substitution as currently typed.
Prior ruling: 2026-09-02 "the skin composes"; 2026-08-31 boundary document permits custom instruments; 2026-09-06 acceptance forbids appearance selection from dropping feedback or changing policy.
In-flight: scalar binding/view and HBar render projection already exist. No need for a second feedback presentation helper has been demonstrated.
Required surface: custom and built-in renderers must consume the same instrument evidence/lifetime interface and receive the presentation inputs needed for their declared geometry. The public override must preserve confirmed/target/unknown/availability/feedback and command policy. Whether this requires any new helper beyond the existing view/projector is undetermined.
Depends on: agreement on the shared behavioral contract identified in F1, not completion of F1's renderer migration. This skin-boundary decision and F1a's hint policy precede that migration; no dependency on deleting public skin exports.
Confidence: high.
Falsifier: a public custom-renderer route accepting the existing complete binding and preserving its feedback, with actual consumer evidence.
Fix class: design.
Actionable: yes for contract adoption; no for inventing a separate generic skin behavior helper solely from this finding.

### F5 - Pair math, scalar lifetime and feedback projection: already shared

Verdict: already-shared.
Rank: parallel (candidate cleared).
Elements / definition sites: `frontend/src/primitives/scalar/value-control-core.ts:dualParamValuesFromNormX,dualParamNormXFromValues,dualParamStepAlongAxis`; `continuous-scalar.svelte.ts:createContinuousScalar`; `frontend/src/primitives/control-feedback/control-feedback-presentation.ts:projectControlFeedbackPresentation`.
Consumers: pair math has semantic and legacy renderer consumers as listed above; scalar lifetime has HBar and Filter; feedback projection is called by continuous and committed scalars.
Divergence: their clients choose different policies, but imports are not additional definitions. The nonlinear pair inverse and stepping are distinct operations in one shared module.
Prior ruling: 2026-09-02 primitives home; accepted HBar adoption on 2026-09-06.
In-flight: exists and consumed.
Required surface: exists; F2/F3 add consumer contracts, not replacement math/confirmation implementations.
Depends on: none.
Confidence: high.
Falsifier: a separately defined equivalent pair formula or feedback projector in one of the cited client paths.
Fix class: none.
Actionable: no consolidation of already-shared definitions.

### F6 - Scalar policies, pair stepping and visual grammar: legitimately distinct

Verdict: C - legitimately local geometry and explicit task policy.
Rank: name-collision (same family name is not algorithm identity).
Elements / definition sites: `BipolarRenderer.svelte:getKeyboardIncrement,handleBipolarKeyboardStep,handleWheel`; `DiscreteRenderer.svelte:handleDoubleClick,handleWheel,tickItems,notchPercents`; `KnobRenderer.svelte:handlePointerMove`; `ProfessionalKnob.svelte:onMove`; `DualParamRenderer.svelte:handleKeyDown,handleWheel`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:createHBarContinuousScalarPolicy,nativeRangeContinuousScalarPolicy` (renderer paths use the controls prefix defined above).
Consumers: respective renderer routes in the consumer table; native range policy is consumed by Filter. Pair custom stepping serves DualParamRenderer; semantic RF/SQL uses native input.
Divergence: observation: Bipolar wheel uses one step unless the range exceeds 500 steps; HBar uses four times an adaptive multiplier. Bipolar's explicit keyboard increment validates lattice compatibility and snaps around default. Discrete's reset requires an explicit default. Knob uses relative vertical movement with distinct fine sensitivity and incoming-value preview. Pair step advances lane values directly, while pointer/native input maps a normalized position through the center dead zone. Ticks, arcs, fills, dimensions and pointer coordinates are visual/input grammar.
Prior ruling: 2026-09-06 acceptance explicitly permits materially different algorithms and gesture geometry; 2026-09-02 look/layout ruling separates display model and composition.
In-flight: HBar/native policies already demonstrate explicit differences under one owner.
Required surface: exists conceptually in the policy and input-geometry boundary; migration must carry these distinctions explicitly. Identical family names do not require identical stepping.
Depends on: none to retain; F1/F2 must preserve the declared distinctions.
Confidence: high on observed difference and valid ownership distinction; this is not a blanket endorsement of every historical edge case.
Falsifier: a consumer contract requiring the same policy that these implementations demonstrably violate, or radio truth/confirmation hidden inside a claimed geometric calculation.
Fix class: none.
Actionable: no consolidation of visual grammar or forced algorithm unification.

### F7 - Normalized-to-wire RF conversion: correctly located adapter work

Verdict: C - legitimately local boundary adaptation.
Rank: parallel (small identical conversion, cleared as an architectural defect).
Elements / definition sites: `frontend/src/lib/runtime/adapters/panel-adapters.ts:withNormalizedRfLevels`; `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:RF_FRONT_END_LEVEL_INTENT`.
Consumers: legacy RfFrontEnd handler accessor; semantic RfFrontEndSurface callback respectively. Both call the same raw `makeRfFrontEndHandlers` factory.
Divergence: none in the `Math.round(value * 255)` conversion. The semantic binder preserves the raw factory object's identity; source comments and named binder tests document why wrapping it there is not interchangeable.
Prior ruling: combined-control conversion recorded in source with the 2026-08-11 historical reference; conversion occurs at wiring, not presentation.
In-flight: common raw handlers and typed intent contract already exist.
Required surface: exists. Sharing a tiny pure conversion is optional maintenance, not a prerequisite for an instrument owner and not evidence for a new pair mechanism.
Depends on: none.
Confidence: high.
Falsifier: divergent conversions for identical domains or an existing canonical domain converter whose omission violates a required policy.
Fix class: none.
Actionable: no required architectural consolidation.

### F8 - Public/demo-only renderer and helper deletion: not established

Verdict: undetermined for alleged dead public API; ProfessionalKnob itself is demonstrably reachable.
Rank: name-collision (no established dead fork).
Elements / definition sites: `frontend/src/components-v2/controls/value-control/skins/index.ts:professionalSkin,skins`; `skin.ts:Skin`; `skins/ProfessionalKnob.svelte`; `frontend/src/primitives/scalar/value-control-core.ts:calculateDragValue` and other exported helpers with weak inventory counts.
Consumers: ProfessionalKnob has direct demo mounts; registry has public barrel exposure. External consumers of exported helpers/registry are unknown. Test counts from the collection are not authoritative.
Divergence: production adoption, demo reachability and public availability are different facts.
Prior ruling: 2026-08-31 boundary gate preserves legitimate custom instruments; no public retirement ruling found for these symbols.
In-flight: none that removes their public consumers.
Required surface: evidence of public retirement and reliable symbol-resolved source/test/external consumer census before deletion.
Depends on: none; does not block D1/D2 because their locals are inaccessible through this API.
Confidence: high that deletion is not presently justified.
Falsifier: explicit accepted API retirement plus verified absent consumers, or newly located real consumers settling the liveness question positively.
Fix class: none.
Actionable: no deletion.

## Weakest link

F2's internal reuse topology is the weakest decision boundary. The evidence earns an interface retaining two lane outcomes and one combined gesture, but does not choose how many internal scalar owners implement it. First check discriminating cases: one lane confirms while the other fails, receiver/session changes during a gesture, both observed lane values lie outside the single-axis image, and a policy change replaces the renderer. Any proposed topology that synthesizes a pair confirmation or duplicates accepted lifecycle ownership fails the required surface. The incomplete lexical sweep also prevents a claim that all dead code in the tract has been cleared.

## Cleared

- Shared RF/SQL axis mapping and step helpers are actual shared definitions.
- ContinuousScalarBinding is the accepted scalar lifetime target; HBar is an adopter, not an independent lifetime copy.
- The existing feedback presentation projector is shared by continuous and committed scalar mechanisms.
- Committed editing and continuous gestures have distinct actual contracts; their coexistence does not itself justify merging them.
- Relative knob geometry, native range input, bipolar lattice policy, discrete reset policy and visual grammar may legitimately differ.
- Normalized RF/SQL conversion at adapter/wiring boundaries is correctly located.
- ProfessionalKnob is demo/public reachable; it is neither proven dead nor proof of production migration.

This report completes the bounded decision adjudication only. Implementation, independent verification, public archive review and the final integrated whole-path audit remain separate work.
