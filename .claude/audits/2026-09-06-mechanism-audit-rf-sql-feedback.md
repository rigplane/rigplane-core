# Supplemental mechanism audit: RF/SQL feedback decision

Audited revision: `5a4cee2cd734ae31c76eb48397b6df6a062043ea` (2026-09-06).

Method: `.claude/skills/mechanism-audit/SKILL.md` and `.claude/agents/auditor.md`, both read in full at the audited revision, followed by `AGENTS.md` and `CLAUDE.md`. Live owner acceptance was read. Entry HEAD was `5a4cee2cd`; the working tree was clean. All code evidence below comes from immutable Git objects, not moving working-tree files.

This is a bounded architectural decision audit, not implementation approval, code review, or the final whole-path audit. It supplements F3 of `.claude/audits/2026-09-06-mechanism-audit-scalar-pair.md`, whose audited revision was `f2e7969708d5c93890cf1b24d833ce820b36dc15`. The accepted pair foundation landed at `3a1ce54da32c8c7aaf64dbe491d4e830055223e3`. A subsequent meter-adapter change is present at this audit's pin; the relevant current identity functions were reread. No source edits, Git writes, tests, builds, installs, browser, service, hardware, public comment or merge operations occurred. This report is staged privately; publication requires its separate review.

The supplied preflight's final source-pinned correction is the proposal under examination. Its superseded first-pass statements are not current recommendations and are not counted as findings against the corrected proposal. No internal implementation topology follows merely from the preflight's use of the word "frozen."

## Steps 0-3a: evidence before judgement

### Definitions and actual consumers

| Capability | Definition sites | Actual consumers at this pin |
| --- | --- | --- |
| Frontend descriptor contract | `frontend/src/lib/stores/commands.svelte.ts:StateBackedCommandDescriptor,STATE_BACKED_COMMAND_DESCRIPTORS` | Filter Width and Break-in Delay descriptors; store reconciliation and adapter projection. RF/SQL descriptors are absent. |
| Lifecycle and post-ACK reconciliation | Same file: `beginCommand,transition,reconcileStateBackedCommands,cancelPendingCommands` | `frontend/src/lib/runtime/commands/radio-intents.ts` delivery/session handlers; accepted radio-state subscription. RF/SQL dispatch is already lifecycle-tracked, but lacks descriptor-driven confirmation coverage. |
| Read-only feedback projection | `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback,sameFeedbackScope` | `getFilterWidthControlFeedback,getBreakInDelayControlFeedback`; no RF/SQL accessor exists. |
| Normalized-to-raw command adaptation | Same adapter: `withNormalizedRfLevels`; `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:RF_FRONT_END_LEVEL_INTENT` | Legacy RF panel and semantic RF surface respectively; both reach `frontend/src/lib/runtime/commands/panel-commands.ts:makeRfFrontEndHandlers`. |
| Canonical receiver choice | `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts:activeReceiverId,toRadioViewModel,deriveReceiverIndicators`; `frontend/src/lib/runtime/adapters/presentation-capabilities.ts:derivePresentationCapabilities` | Semantic view model, band/filter families and receiver indicators. These are existing authorities, not new helpers to invent. |
| Provider and field admission | `frontend/src/lib/stores/radio.svelte.ts:setRadioState,matchesCurrentCapabilityTopology`; `frontend/src/lib/stores/capabilities.svelte.ts:capabilitiesMatchGeneration`; `frontend/src/lib/runtime/adapters/display-observation.ts:validIdentity,qualifyDisplayObservation,qualifyEvidence`; `frontend/src/lib/state/field-status.ts:getFieldAvailability` | State ingress, qualified display consumers and current control guards. The feedback projector does not call all these admission mechanisms. |
| Pair behavior | `frontend/src/primitives/scalar/continuous-pair.svelte.ts:createContinuousPair,authorityOf,canonicalLane,effectiveLane,pairEditable` | One production owner in `frontend/src/semantic/RfFrontEndSurface.svelte:rfSqlPair`; direct pair tests. It reuses the accepted scalar lifetime and feedback-presentation projector. |
| Native pair adoption | `frontend/src/semantic/RfFrontEndSurface.svelte:rfSqlInput,rfSqlPair,rfSqlLease,changeLevel` | Actual combined-profile native range, mounted by SemanticRadioSurfaces. Currently reading evidence only. Separate RF/SQL sliders remain a different, unadopted branch. |
| Legacy pair | `frontend/src/components-v2/controls/value-control/DualParamRenderer.svelte:localRf,localSql,emitPair,handleWheel,handleKeyDown` | One production importer/mount: `frontend/src/components-v2/panels/RfFrontEnd.svelte`. Panel mounts are in `LeftSidebar.svelte`, `MobileRadioLayout.svelte`, and `RadioLayout.svelte`, all under `frontend/src/components-v2/layout/`. |
| Renderer lease precedent | `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:initialBinding,attachedBinding,lease` and lifecycle effects | Binding-driven HBar rendering: renderer attaches/replaces/disposes its lease; does not destroy the supplied binding. |
| Backend normalized expectation | `src/rigplane/core/command_service.py:_expected_value_for_path,_normalize_raw_level_value,_normalized_observation_value_for_expectation` | Shared command service readback expectations and correlated observation normalization. |
| Actual normalized observations | `src/rigplane/runtime/_civ_rx.py:_NORMALIZED_CMD14_OBSERVATION_SUBS,_observations_from_frame`; `src/rigplane/backends/yaesu_cat/observations.py:_normalize_level_255`; `src/rigplane/backends/rigctld_client/observations.py:_normalize_level_255` | Provider observations admitted to the canonical StateStore. These are protocol adapters, not frontend confirmation owners. |

Observation: the production importer census was independently checked with pinned literal searches. The legacy barrel `frontend/src/components-v2/controls/value-control/index.ts` exports DualParamRenderer; public importability remains a compatibility boundary. A demo mention is not a mount. Neither `RF_GAIN_COMMAND_DESCRIPTOR`, `SQUELCH_COMMAND_DESCRIPTOR` nor `getRfSqlControlFeedback` is an implementation at the pin.

### Prior rulings and in-flight work

- `docs/plans/2026-07-25-ui-composition-architecture-v3.md` (2026-07-25) and the frontend layer map in `CLAUDE.md` place runtime mapping below presentation and leave transport/command execution in their existing owners.
- `.claude/audits/2026-08-30-mechanism-audit-command-path.md:Cleared` identifies CommandService as the shared command spine, including level normalization. This is historical ownership evidence, not blanket clearance of current confirmation admission.
- `.claude/audits/2026-09-06-mechanism-audit-scalar-pair.md:F3` explicitly left exact numeric matching unadjudicated; it required qualified field descriptors and real command receiver/session boundaries. F7 cleared both existing `Math.round(value * 255)` adapter sites.
- Accepted pair commit `3a1ce54da32c8c7aaf64dbe491d4e830055223e3` (2026-09-06) supplies the missing pair interface and a native semantic reading adopter. This supersedes the old report's absence-of-pair observation, not its outstanding full-feedback finding.
- Current live acceptance read on 2026-09-06 requires replaceable appearances, independent canonical/requested/error information and authority-change behavior, while prohibiting a second truth store, confirmation owner, poller, queue or transport. The current dispatch expressly excludes a compatibility adapter for legacy DualParamRenderer. An intentional source API break must still be recorded; absence of downstream consumers was not proven.
- No P2 implementation is present in the inspected paths. Between the accepted pair commit and this pin, the inspected RF/SQL owner, descriptor, projector and backend paths are unchanged; the view-model adapter has the accepted meter qualification change and was inspected at the new pin.

### Bounded enumeration and liveness

This supplement does not repeat the earlier full scalar inventory. It enumerates the decision-bearing source modules/ranges and traces the actual consumers of every mechanism used to justify a verdict. Full scripts/templates were read for the semantic owner and legacy renderer. Backend inspection is explicitly limited to RF/SQL actuation, readback, normalization, provider-generation/clock admission and cited tests, not every provider method.

Independent anchored declaration-line enumeration from pinned source:

| Scope | Named function declarations | const/let/var declaration lines |
| --- | ---: | ---: |
| commands.svelte.ts, complete module | 12 | 60 |
| continuous-pair.svelte.ts, complete module | 20 | 66 |
| panel-adapters.ts, feedback types/projector/two accessors, pinned lines 279-360 | 3 | 10 |
| presentation-capabilities.ts, complete module | 2 | 17 |
| display-observation.ts, complete module | 6 | 7 |
| RfFrontEndSurface.svelte, before stylesheet | 2 | 22 |
| DualParamRenderer.svelte, before stylesheet | 10 | 33 |

These are declaration lines, not AST binding totals. Arrow functions are included in the second column of counts, destructuring is one line, and type-only method signatures are not executable methods. The pair's policy objects and renderer wrapper methods were read directly through their interface consumers. Local drafts, authority identity, terminal identity, projected candidates and announcement state all have visible reads/writes in reconciliation or projection. The legacy wheel timer and debounced callbacks have consumers; they are migration targets, not dead code. Native policy wheel/key/reset returning null is an intentional no-op capability policy, not proof that corresponding legacy gesture machinery is unreachable.

A pinned whole-word search across `frontend/src`, partitioned by `/__tests__/`, screened 24 named decision-bearing symbols. Selected reproducible lexical counts:

| Name | Source occurrences | Test occurrences |
| --- | ---: | ---: |
| projectControlFeedback | 3 | 0 |
| STATE_BACKED_COMMAND_DESCRIPTORS | 2 | 3 |
| reconcileStateBackedCommands | 2 | 0 |
| beginCommand | 3 | 40 |
| createContinuousPair | 3 | 3 |
| createLegacyContinuousPairPolicy | 1 | 6 |
| nativeRangeContinuousPairPolicy | 3 | 2 |
| canonicalLane | 10 | 0 |
| effectiveLane | 5 | 0 |
| pairEditable | 2 | 0 |
| rfSqlInput | 2 | 0 |
| activeReceiverId | 3 | 3 |

Method: `git grep -n -w -e <name> ... <audited-full-SHA> -- frontend/src`, followed by token counting in source/test partitions. Definitions/comments and same-name collisions remain included; these are not independent semantic read/write counts. A zero test token count does not mean untested: public accessors exercise the private projector and store subscription. One source occurrence of exported `createLegacyContinuousPairPolicy` means no production adoption yet, not deletion of the accepted next adopter's public policy. Dynamic object methods, exports, barrel consumers and unavailable private/downstream consumers defeat any blanket dead-code inference. No deletion candidate is established.

Tests read, not run: pair target/lane-independence/authority cases in `frontend/src/primitives/scalar/__tests__/continuous-pair.test.ts`; descriptor/post-ACK/projection cases in `frontend/src/lib/runtime/adapters/__tests__/filter-width-command-lifecycle.isolated.test.ts`; relevant command-store/delivery tests; backend raw-parameter/normalized-expectation cases in `tests/test_command_service.py`; and the provider-replacement delivery test in `frontend/src/lib/transport/__tests__/ws-client.isolated.test.ts`. The preflight's proposed new isolated RF/SQL and controlled-renderer tests do not exist yet. Existing RF panel adapter mocks were checked as affected consumers, not end-to-end feedback proof.

### Numeric and authority facts

Observation: `StateBackedCommandDescriptor.target` supplies both `ControlFeedback.target` and `requestedTarget` through `projectControlFeedback` unchanged. `continuous-pair.svelte.ts:effectiveLane` consumes target directly beside canonical normalized values and pair math. Dividing only inside `matches` would leave the rest of the instrument in mixed units. The corrected normalized target contract fixes that conceptual error.

Observation: `src/rigplane/core/state_pipeline_contracts.py:_receiver_specs` declares receiver RF gain and squelch as normalized float fields. Icom CMD14 RF/SQL observations decode an integer then divide by 255. Yaesu `radio.py:read_rf_gain,read_squelch` return parsed integers without mutating legacy state; the observation adapter divides them by 255 separately for MAIN/SUB. `rigs/ftx1.toml` names distinct RG0/RG1 and SQ0/SQ1 commands. A setter's legacy mirror assignment is not independent radio observation.

Observation: external rigctld `radio.py:get_rf_gain,set_rf_gain,_level_255_to_float,_parse_level_255,_float_to_level_255` is MAIN-only. It formats raw n/255 to three decimals and converts readback with round(value*255), then its observation adapter divides the recovered integer by 255. The arithmetic bound 0.0005*255 = 0.1275 proves recovery of the same integer only if the provider returns the corresponding value. It does not prove hardware/Hamlib echo, lack of quantization, or every target's eventual confirmation. `supports_command` checks both the explicit supported set and callability; a receiver-validation mention of `set_squelch` is not support. There is no admitted squelch method, capability or observation path in that backend at this pin.

Observation: the backend expectation normalizer uses exact equality after permitted unit conversion. Its correlated readback path also checks command/path/source/session context. The frontend store instead uses its existing acknowledged record, field path, fresh/available/observed leaf and strictly advancing field marker. Equal numeric predicates do not make those whole correlation mechanisms identical. ACK alone does not confirm, and a cold ACK with no field boundary uses the first admitted field marker as a boundary rather than confirmation.

Observation: `src/rigplane/core/state_store.py:StateStore._apply_one,_strictly_older_than_entry` accepts equal observation timestamps and only orders entries inside matching provider and non-null clock domains. `src/rigplane/web/state_schema.py:FieldStatusPublic` has no typed clock-domain field. Consequently the existing frontend strict-marker rule is a conservative confirmation admission rule, not proof that every legitimate readback must advance that marker or that markers can be compared across providers. No cross-clock ordering, universal deduplication or new confirmation floor is earned by this supplement.

Observation: `projectControlFeedback` requires observed/fresh/available leaf status and a finite marker, plus a descriptor-accepted confirmed value. It does not check nonnegative marker, ancestors, capabilities, state/caps provider equality, transport connected state or provider identity on the lifecycle record. The receiver indicator's top-level `availability.operational` is computed from capability topology, not connection health or freshness of that receiver's RF/SQL fields. The active-receiver function provides the correct single-receiver tautology and multi-receiver observed identity, but is not a provider/session validator.

Observation: normal `setRadioState` ingress validates provider/topology against then-current capabilities. WebSocket disconnect clears state/capabilities, and provider reset also clears them. These are meaningful upstream protections. Nevertheless `setCapabilities` can replace the current capability object independently, and the read-only adapter itself does not validate matching captured generations. The stricter existing display qualifier checks matching generation, nonnegative evidence and present ancestors. No new truth source is needed to express such admission.

Observation: `WsChannel.transportEpoch` advances on WebSocket open. `resetForProviderGeneration` does not advance it. On successful response, `WsChannel._emitCommandResult` removes the generic transport tracker; `cancelNonPtt` iterates those remaining generic trackers, then clears lifecycle transport tracking. If no generic trackers remain, it emits no cancellation event. `radio-intents.ts` cancels frontend records on a cancelled delivery or disconnected transition, not on a standalone provider-generation notification. The frontend lifecycle records, projector filter and pair command authority have no provider-generation member. A pinned production-only search of `cancelPendingCommands,resetCommandLifecycle` found no additional reset caller covering that boundary.

Observation: the test named "session/provider replacement" in the Break-in Delay projection suite changes both epoch and provider generation. It does not prove same-WebSocket provider replacement. The transport provider-reset test uses a still-in-flight command, not an acknowledged command after response. No executed reproduction is claimed here.

## Step 4: steelman

The strongest case for the corrected proposal is substantial. It reuses the actual descriptor table, command store, projector, pair owner, scalar lifetime and numerical helpers. Its normalized targets are necessary for the accepted interface, and exact matching avoids silently claiming that a neighboring device value is the requested value. Two live production endpoints justify one pair feedback projection. Their distinct native/legacy gesture policies are already explicit. Neither a new store nor an RF/SQL-specific confirmation engine is needed.

The current stores and WebSocket admission already perform generation resets and topology checks. Synchronous read-only projection with one captured state/caps/list/epoch set avoids gratuitously mixing lane inputs; there is no reason to copy those stores into an accessor. It is also reasonable to require a canonical operational receiver before projecting either lane. An unavailable sentinel expressed with the accepted reading union can be honest if it contains no reading and is visibly distinct from omitted integration.

The legacy renderer is not a supported second owner merely because it is exported. The authorized binding-only direction and existing HBar lease pattern allow an explicit source API break without inventing a compatibility layer. The proposal correctly adds the previously omitted semantic owner and direct test, and separately migrates the legacy production panel instead of claiming completion from primitive tests.

These arguments clear the mechanism direction, units and ownership. They do not prove the proposed admission predicates are equivalent to their prose, that transport epoch identifies provider generation, that an unobserved transient null resets all retained owners, or that old command outcomes cannot reappear after same-session provider replacement. A pure projection cannot infer identity that its input or record does not contain. Those are bounded missing contracts, not permission to duplicate authorities.

## Deletions

None established. The legacy lifetime has a production consumer and must be migrated, not called a vestigial fork. Public policy/helper exports and interface methods remain guarded. Removal of local timer/draft code follows an accepted migration; it is not an independent deletion batch from this audit.

## Consolidations

### F1 - A leaf projector plus topology does not establish the proposed full authority

Verdict: B - missing admission coverage in the proposed RF/SQL accessor contract.
Rank: diverged.
Elements / definition sites: `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback`; `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts:activeReceiverId,deriveReceiverIndicators,toRadioViewModel`; `frontend/src/lib/runtime/adapters/presentation-capabilities.ts:derivePresentationCapabilities`; `frontend/src/lib/runtime/adapters/display-observation.ts:validIdentity,qualifyEvidence`; `frontend/src/lib/state/field-status.ts:getFieldAvailability`.
Consumers: projector serves Filter Width/Break-in Delay; canonical identity and topology serve semantic families; qualifier serves accepted display paths; field-availability helper serves actual command and semantic guards. Proposed RF/SQL accessor would compose these for native and legacy pair consumers.
Divergence: observation: the proposed identity/indicator checks can pass with a fresh leaf under a PRESENT parent fieldStatus record whose freshness is 'stale' or availability is 'missing', finite negative marker, or matching topology but mismatched state/caps provider generations. An ABSENT parent fieldStatus record is not this counterexample: qualifyEvidence skips it, and field-status.ts searches for an existing ancestor without treating absence alone as a veto; this finding requires no new absent-parent admission rule. A single topology carrying a contradictory dual_rx tag retains a topology/MAIN operational entry while recording a diagnostic; toRadioViewModel only rejects absent topology. Inference: the claim that all contradictory capabilities, invalid evidence and nonoperational source contexts fail closed is broader than the named checks. These are source-level counterexamples, not a claim that every one passes normal network ingress.
Prior ruling: 2026-09-06 scalar/pair F3 requires qualified scope/evidence; accepted adapter/display contracts already separate identity, observation and availability. No prior ruling was found making top-level receiver operational a connection-health predicate.
In-flight: existing qualified observation and availability predicates are available; no RF/SQL accessor yet exists. Current ingress admission is a protection to preserve, not an accessor-level proof for arbitrary captured inputs.
Required surface: one read-only captured RF/SQL projection that establishes matching actual provider identity, supported canonical receiver topology, explicit unresolved identity, relevant receiver operability and each lane's valid observed field evidence, including authoritative ancestor vetoes and valid markers. State what source-liveness/session condition is required; do not equate topology with live connection. Reuse existing admission contracts and command projector rather than add a truth owner or globally tighten unrelated compatibility helpers. The same admitted authority must govern pair readout and whether requests may proceed.
Depends on: none for admission adjudication; production completion also requires F2's continuity boundary.
Confidence: high on predicate mismatches; medium on which additional contradictory diagnostic classes the product must reject.
Falsifier: an explicit, tested upstream invariant covering every admitted accessor invocation and each counterexample, or a narrower accepted contract that removes the unsupported guarantees. A topology-only fixture with fresh leaf status is not that proof.
Fix class: design.
Actionable: yes, as a bounded correction to the required admission surface before P2 implementation is leased.

### F2 - Provider identity is missing from command-feedback continuity

Verdict: B - provider-boundary gap; not a reason for another command or pair owner.
Rank: diverged.
Elements / definition sites: `frontend/src/lib/stores/commands.svelte.ts:CommandLifecycle,reconcileStateBackedCommands`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:ControlFeedback,projectControlFeedback`; `frontend/src/primitives/scalar/continuous-pair.svelte.ts:authorityOf,reconcile`; `frontend/src/lib/transport/ws-client.ts:WsChannel._open,WsChannel.cancelNonPtt,WsChannel._emitCommandResult,resetForProviderGeneration`; `frontend/src/lib/runtime/commands/radio-intents.ts` delivery/session subscriptions.
Consumers: frontend lifecycle store already tracks RF/SQL delivery; projector serves current descriptors; pair command-feedback variant has tests but no production adopter yet. Native P1 uses reading ownerKey. Transport reset serves the real same-socket provider transition path.
Divergence: observation: epoch/name/scope filters and pair authority distinguish receiver and WebSocket epoch, but not provider generation. After a successful response removes all generic trackers, a provider reset can clear browser state without cancelling the acknowledged frontend record or advancing epoch. A later matching scoped field with a higher marker can satisfy the existing reconciliation rule for that older record. Terminal records can also remain eligible for projection at unchanged epoch/scope. Inference: the corrected accessor's captured state/list/epoch is insufficient to guarantee that prior-provider requests, confirmations, drafts or announcements never enter the new provider's instrument. Depending on every consumer observing an intermediate null is not an established contract.
Prior ruling: 2026-09-06 F2/F3 and current acceptance require authority-change invalidation; backend `StateStore` already distinguishes provider generation from clock-domain ordering. Accepted P1 tests cover reading ownerKey and command receiver/epoch replacement, not this missing dimension.
In-flight: existing provider reset, frontend cancellation and pair cancellation mechanisms exist. None of the proposed RF/SQL pair snapshot, ControlFeedback identity or stored command scope carries the missing provider boundary at the pin.
Required surface: preserve actual provider identity across projection, lifecycle admission and local pair authority, with deterministic invalidation/rejection of prior-provider work even when receiver, values and WebSocket epoch are unchanged. Reuse the existing command owner and pair cancellation/lifetime; do not synthesize a new session counter, put provider identity into an unrelated receiver slot, or build a second confirmation store. An explicit existing-owner provider invalidation contract may satisfy this instead of a new public field, but must be evidenced end to end.
Depends on: F1 for admission; any lifecycle-boundary change must precede claiming RF/SQL state confirmation complete. Whether the accepted pair input needs extension depends on the chosen existing-owner invalidation contract, not a predetermined helper design.
Confidence: high on missing identity and the response/reset counterexample; medium on the minimal repair's file extent.
Falsifier: a source-backed provider-reset notification that always cancels/retires all relevant acknowledged and terminal records and invalidates pair transient state before reuse, including the no-generic-tracker/same-socket case, or existing provider identity carried through the complete path.
Fix class: design.
Actionable: yes as a prerequisite contract. It is not authorization to edit transport or expand the current proposed lease silently.

### F3 - Normalized descriptor values and exact equality fit the existing mechanism

Verdict: already shared for normalization conventions and lifecycle ownership; B only for missing RF/SQL descriptor/projection adoption. Universal hardware round-trip success remains undetermined.
Rank: displaced.
Elements / definition sites: `frontend/src/lib/stores/commands.svelte.ts:StateBackedCommandDescriptor,STATE_BACKED_COMMAND_DESCRIPTORS`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback`; `frontend/src/primitives/scalar/continuous-pair.svelte.ts:effectiveLane`; `src/rigplane/core/command_service.py:_expected_value_for_path,_normalized_observation_value_for_expectation`; provider normalization functions listed above.
Consumers: existing descriptors serve Filter Width and Break-in Delay; actual RF/SQL intents reach the same store from both adapter sites and keyboard handlers. Backend normalizers serve their own admitted provider observations. Proposed normalized RF/SQL descriptors would serve both production pair adopters.
Divergence: observation: command levels are raw safe integers, canonical pair values are normalized, and the projector forwards targets without conversion. Exact n/255 equality matches the readback units of the examined paths. Inference: normalized descriptor target plus exact equality is supported; putting raw n in target/requestedTarget is not. Provider quantization returning another integer means a different value, not an epsilon-confirmed target.
Prior ruling: prior scalar/pair F3 left numeric equality open; F7 preserved the two command conversion sites. Backend normalized receiver field contracts and raw-parameter tests establish the units. This supplement adjudicates that bounded numerical question on 2026-09-06.
In-flight: RF/SQL descriptors are absent; descriptor registration, reconciliation, immutable feedback projection and pair effective-target handling exist.
Required surface: validate the exact level/receiver parameter shape, reject missing/extra/malformed values and raw levels outside integer 0..255, resolve only receiver 0/1, and return level/255 from target. Confirmed values must be finite 0..1 from the exact receiver/field, without fallback. Use exact confirmed===target; all target/requested/confirmed values entering pair geometry share normalized units. Keep RF and SQL intent/scope/field identities and latest-target supersession independent. Preserve post-ACK observation admission in the existing store; no epsilon, nearest-step, percentage or raw-value re-rounding is earned.
Depends on: F1/F2 for trustworthy authority and field admission. The unit contract itself has no new mechanism dependency.
Confidence: high on units and exact-value predicate; undetermined on each physical device's eventual exact return.
Falsifier: a supported provider's declared actuation/readback contract explicitly equating a different reported value with the target, or a descriptor/projector interface that converts targets before pair use. Neither was found.
Fix class: consolidate for descriptor/projection adoption after required authority decisions.
Actionable: yes for normalized exact descriptors; no universal hardware-success claim or shared tolerance policy.

### F4 - The three integration states are valid, but must govern the whole native pair path

Verdict: C for distinct omitted/unresolved/command evidence; B for completing that contract through the current semantic consumer.
Rank: displaced.
Elements / definition sites: `frontend/src/primitives/scalar/continuous-pair.svelte.ts:ContinuousPairInput,canonicalLane,pairEditable,authorityOf`; `frontend/src/semantic/RfFrontEndSurface.svelte:rfSqlInput,changeLevel,combinedUsable` and combined readout/observed markup; `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:rfFrontEndSurface,RF_FRONT_END_LEVEL_INTENT`.
Consumers: the native combined range consumes P1's reading pair; direct omitted-prop mounts/test harnesses consume its reading contract. Proposed production wiring would always supply object or null. Separate RF/SQL sliders and preamp/toggle controls are neighboring, distinct consumers.
Divergence: observation: the existing command union requires real lane scopes and epoch; the reading union can express disabled unknown lanes without a receiver. Current combined readout/data-observed and changeLevel still consult view.rfFrontEnd, whose adapter follows raw state.active. Inference: changing only rfSqlInput would leave another authority in presentation or request gating. Undefined and explicit null must not collapse into the same fallback branch.
Prior ruling: accepted P1 on 2026-09-06 is explicitly reading-only; current full-feedback acceptance requires truthful availability, targets and failure. The canonical active identity rule forbids a fabricated MAIN when genuine receiver choice is unknown.
In-flight: accepted pair union already expresses an enabled:false, both-lanes-unknown/unavailable sentinel. No new unavailable command scope or pair confirmation variant is necessary solely for null.
Required surface: undefined means intentionally absent integration and preserves P1 reading compatibility; null means integrated but unresolved, with disabled unknown lanes, no MAIN scope, no copied readings and an explicit integration-state distinction; an object is authoritative per-lane command evidence, with no unavailable-to-reading fallback. Pair readout, observed/phase/status markers, separate announcements and request eligibility must consume that authoritative pair view. Preserve the separate-slider branch's declared remaining scope. An owner key for null must reflect real authority/invalidation, not invent an identity. Existing command callbacks remain downstream authorities; captured display inputs do not by themselves prove gesture-time scope consistency, which must fail closed on replacement.
Depends on: F1/F2 for the authoritative object and replacement semantics; F3 for normalized lane values.
Confidence: high.
Falsifier: a current combined consumer that already routes all readout and gating exclusively through supplied pair evidence, or a supported union invariant proving null cannot be represented honestly without a new type. Neither is present.
Fix class: design for the input distinction, then consolidate actual consumer adoption.
Actionable: yes; no compatibility fallback from explicit null and no synthetic command feedback scope.

### F5 - Binding-only legacy rendering reuses an accepted owner and policy

Verdict: A - incomplete legacy lifetime migration; C for renderer-local geometry and explicit source API break.
Rank: displaced.
Elements / definition sites: `frontend/src/components-v2/controls/value-control/DualParamRenderer.svelte:localRf,localSql,wheelUnlockTimer,debouncedRf,debouncedSql,emitPair`; `frontend/src/primitives/scalar/continuous-pair.svelte.ts:createContinuousPair,createLegacyContinuousPairPolicy,wrapLease`; `frontend/src/components-v2/panels/RfFrontEnd.svelte`; `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:lease` and lifecycle effects.
Consumers: DualParamRenderer is live through the RF panel and three layout mounts. Shared pair is live through the semantic surface; legacy policy currently has direct tests but no production consumer. HBar demonstrates the intended renderer lease responsibility.
Divergence: observation: legacy renderer still owns draft/debounce/wheel retention while the accepted pair supplies those behaviors through an explicit legacy policy and scalar lifetime. Inference: no second owner, compatibility wrapper or reimplemented pair math is justified for this adopter.
Prior ruling: accepted pair foundation on 2026-09-06; previous F2 required independent lane semantics and F7 preserved adapter conversions; current directed scope explicitly permits binding-only API migration without a compatibility adapter.
In-flight: createContinuousPair and legacy policy already exist. No P2b migration exists at the pin.
Required surface: panel owns/destroys one binding; renderer receives it, owns exactly its renderer lease, disposes/replaces that lease on binding change and disposes it on teardown. Panel does not attach a competing lease; renderer does not destroy the supplied binding. Gesture geometry delegates through the lease; lifecycle timers, command targets and lane state do not return as raw renderer props. Preserve declared numerical/gesture behavior and independent lane presentation. Record the barrel-exported component's intentional source API break and absence of a compatibility adapter explicitly.
Depends on: F1-F4's production evidence contract; no dependency on unrelated scalar/skin migration.
Confidence: high on ownership and in-repository consumer census; downstream impact remains bounded by explicit API-break acceptance, not absence proof.
Falsifier: another supported direct consumer requiring the old API under a newer compatibility decision, or a required legacy gesture unsupported by the existing policy/lease surface.
Fix class: consolidate.
Actionable: yes after the evidence contract is settled; no implementation lease is granted by this report.

### F6 - The 8+5 split represents real adopters, but is not yet a proven complete lease

Verdict: C for two production-adopting batches; undetermined for completeness and measured size after F1/F2 resolution.
Rank: parallel.
Elements / definition sites: the descriptor/store and adapter definitions; SemanticRadioSurfaces and RfFrontEndSurface; RfFrontEnd and DualParamRenderer; their direct tests and existing RF panel adapter mocks identified above.
Consumers: P2a ends at the real semantic owner/native range; P2b ends at the real legacy panel/renderer and inherited layout mounts. Neither is an owner-only or gallery-only adoption. Existing Filter Width/Break-in Delay tests also consume any changed shared admission/lifecycle behavior.
Divergence: observation: the earlier ten-file census omitted the semantic owner/direct test; the corrected 13-file union includes them and the known legacy mock. Inference: the proposed 8+5 organization is coherent, but findings involving provider/lifecycle identity or shared admission may require additional source/test paths. A+D estimates are proposal estimates, not measurements from a candidate diff.
Prior ruling: current AGENTS/CLAUDE batching limits and public compatibility rule; 2026-09-06 acceptance requires all actual production consumers and discriminating evidence.
In-flight: no P2 diff exists to measure. Existing integration and pair tests provide regression consumers, not proof of the proposed new cases.
Required surface: preserve semantic production adoption in the first finishing batch and legacy production adoption in the second. Reconcile the lease against the chosen F1/F2 mechanism before dispatch; include any shared boundary source/tests honestly rather than hiding them in an eight-file promise. Preserve focus, existing mounts, API-break documentation and independent outcomes. Separate native scalar RF/SQL sliders, unrelated instruments, CI/review and final whole-path audit remain outside this bounded completion claim.
Depends on: F1/F2 decisions, then actual candidate diff size and affected-test census.
Confidence: high on the two adopter boundaries; medium on eventual file count and low on exact A+D before implementation.
Falsifier: a complete source-backed dependency analysis showing all required authority guarantees fit the 13 named paths, followed by a measured candidate within the limits; or a newly discovered production importer requiring a wider lease.
Fix class: none for implementation until scope is reconciled.
Actionable: yes for honest re-scoping; no frozen 13-file completeness or under-ceiling guarantee from this audit.

The proposed union is eight paths plus five paths, not ten. For traceability, all paths below are relative to `frontend/src/`; "new" means proposed, absent at the pin:

| Batch | Proposed path |
| --- | --- |
| P2a | `lib/stores/commands.svelte.ts` |
| P2a | `lib/stores/__tests__/commands.test.ts` |
| P2a | `lib/runtime/adapters/panel-adapters.ts` |
| P2a | `lib/runtime/adapters/__tests__/rf-sql-command-feedback.isolated.test.ts` (new) |
| P2a | `components-v2/wiring/SemanticRadioSurfaces.svelte` |
| P2a | `components-v2/wiring/__tests__/semantic-rf-front-end-wiring.component.test.ts` |
| P2a | `semantic/RfFrontEndSurface.svelte` |
| P2a | `semantic/__tests__/RfFrontEndSurface.test.ts` |
| P2b | `components-v2/controls/value-control/DualParamRenderer.svelte` |
| P2b | `components-v2/controls/value-control/__tests__/DualParamRenderer.controlled.svelte.test.ts` (new) |
| P2b | `components-v2/panels/RfFrontEnd.svelte` |
| P2b | `components-v2/panels/__tests__/RfFrontEnd.component.test.ts` |
| P2b | `components-v2/panels/__tests__/mor1536-armed-adoption.test.ts` |

The proposal estimates 647-925 A+D for P2a and 663-870 for P2b, 1,310-1,795 in total. Those figures were not verified against a diff. The authority findings can change both path count and size; the table is evidence of the proposed split, not an accepted lease. In particular, existing shared lifecycle regression coverage also lives in `lib/runtime/adapters/__tests__/filter-width-command-lifecycle.isolated.test.ts`, beyond the proposed new RF/SQL tests.

## Minimal decision boundary

The numerical decision can be accepted independently: strict raw envelope, normalized descriptor targets/confirmed/requested values, exact normalized equality, distinct RF/SQL scopes, and no promise of universal hardware echo. The existing descriptor/store/projector and pair/scalar/presentation owners remain the reuse targets.

The corrected P2 plan is not yet sufficient to accept its full authority guarantees. Before an implementation lease, resolve F1's admission boundary and F2's same-session provider invalidation through the existing owners, then carry object/null/undefined semantics through the entire native pair consumer. The legacy one-binding/renderer-one-lease direction is sound. The two-adopter batching direction is sound, but its exact file union depends on those unresolved decisions.

## Weakest link

F2's minimal file extent is the least certain part, not the absence of provider identity. Check the exact sequence first: acknowledge and successfully respond to the only command, replace provider on the same WebSocket, then supply a matching field value with a greater marker. Confirm whether any existing owner emits a cancellation or retires the old record in that sequence, and whether a mounted pair sees a durable authority change without relying on an intermediate render of null. The inspected reset/delivery/store code does not supply that guarantee; no runtime reproduction was authorized. This is the first discriminator for a narrower existing-owner solution versus a genuinely required interface extension.

## Cleared

- Shared command tracking, descriptor registration/reconciliation and pure feedback projection are real mechanisms, not missing infrastructure to rebuild.
- Normalized target/requested/confirmed units and exact value equality are supported by the inspected provider adapters and backend expectation contract.
- Codec round-trip arithmetic is conditional; device clamping/quantization is not confirmation of an unequal target. External rigctld SQL remains unsupported.
- Canonical single-receiver MAIN tautology and observed multi-receiver identity are the right receiver-selection sources, distinct from raw active defaults.
- Existing ingress generation resets, field qualification and ancestor availability are reusable authorities; this audit does not require another truth store.
- Accepted P1 pair lifetime, exact lane candidate handling, independent lane feedback/presentation and explicit native/legacy policies.
- Honest omitted versus unresolved integration states can use the existing pair union; no fake MAIN command scope is needed.
- Panel-owned binding and renderer-owned single lease, explicit API break without a compatibility adapter, and preservation of both existing normalized-to-raw adapter sites.
- Real semantic and legacy production endpoints justify the two-batch direction. No source implementation, exhaustive liveness clearance, full RF/SQL scalar adoption or final whole-path completion is claimed.
