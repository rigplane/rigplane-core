# Mechanism audit: native scalar normalization and RF/SQL draft retirement

Date: 2026-09-06. Live owner: MOR-2409, under MOR-2215.
Audited candidate: `1f5d5e74774c8aa481937271f4ca400fcbf5ebb8`, PR #3261, unaccepted.
Candidate base: `e9f6800adb4ffad83b2d1b70fbb0680b707d3fe9`.
Accepted main observed separately: `6aa81795518a76f8d57e5dc4f72f7c38214c129e`.

## Decision

Two distinct corrections are warranted; fixing the callback's floating-point representation does not fix the command draft's lifetime.

1. **Native request retirement is incomplete.** The candidate's separate RF/SQL surface really selects `draft` ahead of the supplied command target and canonical value. Its command-feedback scalar retains that draft until exact equality with canonical, a failure/authority change, cancellation or disposal. A successful request of UI 0.7 becomes raw 179 and exact normalized target/confirmation 179/255. Neither representation of the UI candidate equals that confirmation. No success or later-idle cleanup path was found. This is a source-established stale-draft path, not a descriptor confirmation failure; mounted reproduction remains unperformed.
2. **The exact callback assertion is legitimate compatibility coverage.** Previously the surface forwarded the browser's value; native scalar normalization now reconstructs 0.7 as 0.7000000000000001. Both produce raw 179, so no different radio command is demonstrated by this example. Nevertheless, that equivalence does not waive the explicit unchanged normalized callback contract. The smallest warranted numerical boundary is the existing native policy's input normalization, preserving valid on-lattice input identity while retaining off-lattice snapping and guards. A universal decimal normalizer or a blanket change to every snapToStep consumer is not established as necessary.

Keep command targets and canonical observations exact. A narrowly justified machine-arithmetic bound used only to classify an input candidate as already on its declared lattice is categorically different from tolerating an unequal radio observation. No particular epsilon formula or new arithmetic framework is selected by this audit.

This is independent read-only adjudication, not a revised PR gate verdict, implementation lease, publication approval, or completion of MOR-2409/all RF/SQL. The earlier review's statement that all other acceptance is satisfied is not supported for the local-input-to-confirmation sequence below.

## Method and evidence

Read the actual `.claude/skills/mechanism-audit/SKILL.md`, including helper-level mode, current `AGENTS.md` and `CLAUDE.md`, and the complete live MOR-2409 acceptance. Followed definitions -> prior rulings -> in-flight targets -> liveness -> bounded enumeration -> steelman -> verdicts. The new normalization collection and independent review result were read as evidence/proposals, not conclusions. Published scalar-pair and RF/SQL-feedback audits supplied prior rulings, with their older pins kept distinct.

All product sources below were read using immutable candidate Git objects. The candidate diff has exactly three files, 309 additions and 16 deletions: the RF surface and its semantic/wiring tests. It changes none of the four enumerated helper/test modules. A separate base-to-observed-main comparison of those four paths was empty. The candidate remains the audit pin because its RF surface adoption is not accepted main code.

No product or Git writes, tests, builds, installs, browser/hardware operations, PR/issue mutations, or task creation occurred. Private report writing is the only artifact change. The collection's and independent review's CI numbers are supplied evidence, not independently rerun validation. Standalone JavaScript arithmetic was evaluated in the tool runtime from the read formula; no product module or test was executed.

## Step 0: definitions

All paths are repository-relative; identifiers following a colon are definitions, not merely imports.

| Capability | Definition | Relevant contract |
| --- | --- | --- |
| Numeric snapping | `frontend/src/primitives/scalar/value-control-core.ts:snapToStep,clamp` | For positive step, returns `min + Math.round((value - min) / step) * step`; nonpositive step returns the input. clamp is separate. No input-identity or decimal-representation guarantee is encoded. |
| Scalar candidate normalization | `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:snap,nativeRangePolicy,applyCandidate,dispatch` | snap validates domain/input/quantum, then snaps and clamps. Native policy uses domain.step, optimistic preview, immediate dispatch and no custom key/wheel interpretation. applyCandidate stores the normalized number as draft and dispatches it. |
| Canonical evidence | Same scalar file: `canonicalOf,ContinuousScalarView` | Available finite confirmed values are retained without UI-step normalization; the full feedback object is also exposed. Candidate and confirmed truth are separate fields. |
| Scalar lifetime | Same scalar file: `reconcile,terminalIdentity,clearTransient,interactionBase,viewOf,makeLease` | Authority/failure invalidation is shared. Command draft retirement compares canonical to draft exactly. Reading-mode reconciliation instead responds to changed incoming canonical evidence outside pointer/wheel interaction. |
| Pair request handoff | `frontend/src/primitives/scalar/continuous-pair.svelte.ts:LocalRequest,laneObservation,reconcile,effectiveLane,requestAxis` | A local request records its pre-dispatch lane observation. A changed observation retires that local record; supplied targets have explicit effective-value precedence. Pair's inner scalar receives reading evidence for a projected axis, not a command-feedback lane. |
| Separate RF/SQL adoption | `frontend/src/semantic/RfFrontEndSurface.svelte:separateLevelInput,rfGainScalar,squelchScalar,separateLevelValue,separateLevelText` | Two independent native scalars; actual input and output choose draft, then feedback.target, then canonical. The view effects read leases but do not cancel on successful confirmation. |
| Command boundary | `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:RF_FRONT_END_LEVEL_INTENT`; `frontend/src/lib/runtime/commands/panel-commands.ts:makeRfFrontEndHandlers` | Wiring rounds normalized value times 255; raw handlers require an integer/known receiver and dispatch the corresponding intent. |
| Expected target and confirmation | `frontend/src/lib/stores/commands.svelte.ts:normalizedLevelCommand,RF_GAIN_COMMAND_DESCRIPTOR,SQUELCH_COMMAND_DESCRIPTOR,reconcileStateBackedCommands` | Validated raw integer level becomes level/255. Exact receiver/field equality and a strictly newer admitted post-ACK field marker govern confirmation. |
| Feedback delivery | `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback,getRfSqlControlFeedback`; `frontend/src/lib/runtime/commands/radio-intents.ts:dispatchRadioIntentWithResult` | Actual command creation is synchronous before sendCommand. Projector supplies current confirmed evidence, target while nonterminal, requestedTarget and lifecycle/transition identity. It does not call scalar cancellation. |

## Steps 1-2: prior rulings and existing targets

- `.claude/audits/2026-09-06-mechanism-audit-scalar-pair.md:F6`, dated 2026-09-06 at `f2e7969708d5c93890cf1b24d833ce820b36dc15`, rules "C - legitimately local geometry and explicit task policy" and rejects forced algorithm unification. Its F5 identifies scalar lifetime, pair math and feedback projection as already shared. Its then-missing pair/descriptor implementation is historical, not a present gap.
- The same audit's F7 clears the existing normalized-to-wire adapter work. It does not grant a presentation component permission to duplicate wire quantization merely to make its local draft compare equal.
- `.claude/audits/2026-09-06-mechanism-audit-rf-sql-feedback.md:F3`, dated 2026-09-06 at `5a4cee2cd734ae31c76eb48397b6df6a062043ea`, requires "Use exact confirmed===target" and "no epsilon, nearest-step, percentage or raw-value re-rounding is earned." This is a rule about radio confirmation, not a ban on analyzing floating-point error in browser candidate arithmetic.
- Accepted pair behavior now exists at the candidate, including LocalRequest/laneObservation and independent target handling. Accepted RF/SQL descriptors, qualified object/null feedback and provider fencing also exist. They must be consumed rather than rebuilt.
- Live MOR-2409 requires compatible normalized callbacks, two independent existing native scalar owners, one immediate request per input, unchanged sole wire conversion, independent authoritative lane presentation, and complete mounted observation/confirmation coverage. Its numerical correction is not yet leased. The earlier independent review at this exact candidate proposes native normalization repair but does not establish the separate draft-retirement sequence.

## Step 3: liveness and behavioral search

Two vocabulary families were used: `snap/quantize/lattice/step`, and `normalize/canonicalize/round/clamp/precision/epsilon`. Definition reads, not matching names, distinguish the results.

- Core snapToStep serves position/click mapping, keyboard/wheel helpers, discrete enumeration and combined-axis helpers. Production calls also occur in Discrete tick presentation, Knob/ProfessionalKnob pointer handling and committed Break-in Delay policies. These callers have different contracts. DualParamRenderer's import alone is not counted as an independent implementation or a proven call.
- Scalar snap is private and live through HBar, Knob, Discrete wheel and native normalization. Bipolar has its own clamp-only normalization, with stepping elsewhere; Discrete also does not resnap every candidate. This is intentional policy composition, not five absent generic normalizers.
- Native scalar policy has two RF/SQL production bindings in the candidate and the existing FilterSurface width binding. The combined RF/SQL control uses nativeRangeContinuousPairPolicy instead. That pair's internal scalar normalization is clamp-only on the projected axis, a distinct policy rather than evidence that native scalar can drop its accepted off-lattice behavior.
- `frontend/src/lib/stores/tuning.svelte.ts:snapToStep` is a separately defined tuning-store helper, taking the store's own step and frequency context. It does not solve decimal input identity or RF/SQL request retirement and is not the canonical replacement for the pure scalar helper.
- Scalar draft, draftCanonical, handledTerminal, authority, timers and renderer tokens have actual reads and writes in reconciliation, dispatch and projection. The RF surface's separateLevelValue consumes draft directly. There is no hidden success cleanup in its snapshot effects, requestPairLevel, callback or status formatter. The two scalar owners are destroyed only at surface teardown; visibility/authority changes can clear transient state but ordinary same-scope confirmation does not imply such a change.
- Pair localRf/localSql are read by effectiveLane and laneView and written/retired in requestAxis/reconcile. Its observation identity includes confirmed, target, requestedTarget, phase, lifecycleId, transitionId and outcome. These are local request-presentation records, not another radio confirmation engine. That existing implementation is supporting precedent, not an automatic scalar fix.

### Enumeration and deletion guards

The candidate-pinned row-level inventory has 518 rows: 255 production and 263 tests. Production: continuous-scalar.svelte.ts 153; value-control-core.ts 102. Focused tests under the same scalar directory: continuous-scalar.test.ts 141; value-control-core.test.ts 122. Kind counts: 203 anonymous arrows, 8 arrow bindings, 2 assigned-state attributes, 22 assigned-state bindings, 218 constants, 51 functions, 14 methods/object methods. Reference scope is 657 production paths and 894 test paths, including frontend/tests, with zero missing modules and zero reported parse errors.

Separate lexical writes/reads remain in every row. All owner-attributed counts are null; leaf-token collisions, aliases, interfaces, callbacks, dynamic access, strings and markup limit attribution. Rows may overlap declaration patterns. Zero parse errors does not mean TypeScript semantic validation. The 229 zero production non-definition lower bounds are not deletion candidates by themselves.

The full named production zero-lower-bound set contains two exports, independently searched literally across production/test paths:

| Symbol in value-control-core.ts | Collection production writes/reads | Test writes/reads | Guards and conclusion |
| --- | --- | --- | --- |
| calculateDragValue | 1 / 0 | 0 / 9 | Public exported helper; direct tests exercise bar/knob geometry, clamping and snapping. No production caller established. Dynamic/private downstream consumers are not exhaustively excluded. No public retirement authorization; deletion undetermined. |
| handleWheelStep | 1 / 0 | 0 / 6 | Public exported helper; direct tests exercise direction, fine stepping and bounds. Same downstream/public guard; deleting its behavior and tests requires an explicit retirement decision. |

Policy methods returning null are intentionally unsupported input paths, not unreachable proof against other policies' live methods. No further constant-guard deletion was established. The supporting pair/surface/store paths were traced for these questions, not represented as additionally enumerated whole modules.

## Numerical and lifecycle witnesses

### Input arithmetic

Standalone evaluation of the read snap formula and outer clamp produced:

| Min / max / step | Input | Candidate after current native normalization |
| --- | ---: | ---: |
| 0 / 1 / .01 | .7 | .7000000000000001 |
| .2 / 1 / .01 | .3 | .30000000000000004 |
| -1 / 1 / .01 | -.3 | -.29999999999999993 |
| 0 / 5000 / 100 | 2561 | 2600 |
| 0 / 1 / .01 | .705 | .71 |
| -1000 / 1000 / 100 | -150 | -100 |
| 0 / 1 / .01 | -.1 / 1.1 | 0 / 1 |

For the shifted-minimum .3 case, `(value-min)/step` is 9.999999999999998, not an integer. Therefore a repair checking only Number.isInteger of that quotient does not establish the full already-on-lattice contract. A decimal-place shortcut likewise needs evidence for arbitrary domain/quantum, ties and bounds; none was selected here. The standalone values are arithmetic observations, not executed component tests.

The existing `semantic-controls.component.svelte.test.ts` test "preserves native inputs and emits the existing RF intent once" sets input.value to '0.7' and expects exactly one callback with 0.7. The collection/review records the natural CI mismatch. The scalar test "makes native input immediate and tokenless without wheel or key reinterpretation" explicitly requires 2561 -> 2600. Both contracts matter; blanket passthrough and weakening the callback assertion each discard one.

### Request, target and observation are different numbers

For one valid same-authority RF request:

1. Browser candidate u = 0.7; scalar draft d = 0.7000000000000001 at the candidate (or 0.7 after the proposed precision correction).
2. Wiring sends raw n = Math.round(u * 255) = 179 for either d representation. The existing descriptor supplies t = n/255 = 0.7019607843137254 as target/requestedTarget.
3. While submitted/awaiting confirmation, RF surface selects d before t. Shared scalar reconciliation sees no changed authority or terminal failure, and canonical is not d.
4. A valid newer exact observation c = t causes the existing store to confirm. The projector then supplies target=null, requestedTarget=t, phase/outcome=confirmed and canonical=c. `terminalIdentity` deliberately excludes confirmed, and Object.is(c,d) is false. The draft survives.
5. Terminal record retention/GC later yields idle feedback without changing numeric domain, provider, receiver, command or availability. This is not scalar authority replacement and still does not retire d. A later same-authority observed value, such as 204/255 = 0.8, can leave the separate readout at 70% while its confirmed status reports 80%.

This last observation is a stronger visible discriminator than the immediate 0.7 versus 179/255 case, whose percent display rounds both to 70%. Native browser range sanitization can also obscure fine numeric differences; no actual browser behavior was measured here. The owner fields and source precedence establish the retention defect without asserting that a particular browser renders every off-step number verbatim.

Current candidate wiring tests do not close this gap. "projects independent lifecycle outcomes into the two separate controls" creates commands before mounting and manually confirms/fails them, never generating a local native draft. Endpoint-routing tests generate drafts but do not carry them through accepted observation. The provider-replacement test clears drafts through a different authority, not successful same-authority confirmation. The old scalar confirmation test uses identical input/canonical 2700, which also cannot discriminate wire quantization.

## Step 4: steelman

The best case for the current arithmetic is ordinary binary floating-point snapping: snapToStep returns a nearest computed lattice point, and the eventual radio byte is unchanged. Demanding a particular decimal representation from every geometric calculation would overconstrain legitimate implementations. That argument clears the universal-helper accusation, but not this newly changed callback whose prior source and exact compatibility test both forward the native input unchanged.

The best case for retaining scalar drafts is equally important: a local optimistic request is not radio truth, ACK is not confirmation, old feedback may survive a new gesture, and live pointer movement must not be cancelled by ordinary pending updates. The test preserving draft 2700 and a usable pointer token through submitted/awaiting feedback explicitly protects that behavior. It would be wrong to clear every scalar draft on every new feedback object or every confirmed phase.

However, the native request's draft is not a indefinitely authoritative value. Once the accepted command evidence represents it, the owner needs a defined handoff from local candidate to supplied target and then canonical observation. The pair already models this distinction with local-request observation records and effective targets, while separating its axis gesture state. Reusing that principle inside the existing scalar owner is materially different from copying pair lifecycle state into RfFrontEndSurface or adding a second confirmation store. The scalar's and pair's gesture semantics still prevent blind code movement or forced identical algorithms.

## Step 5: Deletions

None established. The two public test-consumed helpers fail the public/downstream guards; they are not an independent deletion package. No whole-inventory dead-code clearance is claimed.

## Consolidations

### F1 - Native command draft retirement: missing handoff inside an existing owner

Steelman: preserving a draft while a request is pending is intentional optimistic behavior, and exact-match retirement prevents unrelated polling from falsely confirming it. But retiring a local presentation request is not confirming a command; exact descriptor confirmation can succeed for the wire target while this draft remains forever. Neither renderer projection nor command GC supplies the missing handoff.

Verdict: already-shared lifetime/confirmation owners; B for the missing bounded native-request-to-feedback handoff behavior, not for a new state store or necessarily a new public API.
Rank: diverged.
Elements: `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:applyCandidate,reconcile,terminalIdentity,viewOf`; `frontend/src/semantic/RfFrontEndSurface.svelte:separateLevelValue,separateLevelText`; `frontend/src/primitives/scalar/continuous-pair.svelte.ts:LocalRequest,laneObservation,reconcile,effectiveLane`.
Consumers: separate RF and SQL use command-feedback native scalars and consume their drafts directly. Native Filter uses the same policy. HBar/Discrete/Bipolar/Knob use the scalar lifetime with distinct source/preview policies. Combined RF/SQL uses the pair's separate local-request and reading-axis mechanisms; that path does not clear the separate scalar drafts.
Definition site: draft lifetime is in createContinuousScalar; pair local observation handoff is in createContinuousPair; surface only selects the supplied fields.
Divergence: source observation: scalar requires Object.is(c,d) to retire successful command draft, while the RF descriptor correctly confirms c===t, where t=round(u*255)/255 and d approximates u. Source inference: same-authority success and later idle/changed canonical reads can retain stale displayed input. This is not merely a concern about an unused scalar field; the actual surface chooses draft first.
Prior ruling: RF/SQL-feedback F3, 2026-09-06, "Use exact confirmed===target"; scalar-pair F6 preserves distinct gesture policies. Live MOR-2409 requires the separate controls' actual presentation to consume accepted lane evidence.
In-flight: pair LocalRequest/observation/effective-target handling already exists. There is no scalar request-observation association or returned request receipt in the current void request callback. Successful descriptor confirmation and qualified feedback already exist; do not rebuild either.
Required surface: a bounded handoff of the native local candidate to subsequent authoritative command evidence in the existing scalar owner. Associate local request state with the feedback observation/identity present when issued, or an equivalently evidenced existing-owner contract; distinguish local u, supplied target t, and canonical c without reconstructing wire conversion in the scalar. A still-retained old confirmed/failed outcome must not erase newer local input just because of its phase. Qualified new same-lane feedback must replace stale local request presentation without manufacturing confirmation. Preserve active-pointer tokens/drafts, debounce/wheel behavior and existing terminal/authority cancellation contracts for other sources. A changed JS object reference alone is not observation identity. Blindly copying pair reconciliation or clearing all drafts on pending updates conflicts with existing scalar tests.
Depends on: existing qualified descriptor/projector/provider boundary, already present. F2's callback precision correction is independent and does not satisfy this requirement. No SRS or backend edit is needed to establish the defect.
Confidence: high on the source path and missing cleanup; medium on the smallest finished handoff design and reactive scheduling until a mounted witness runs.
Falsifier: drive a real mounted separate input through request -> raw 179 -> same-scope ACK -> newer exact 179/255 feedback without authority/branch replacement, then a fresh canonical 204/255 observation. Show the local candidate retired and presentation follows supplied target/canonical, with no extra command or second owner. An existing callback/effect performing that retirement at this pin would overturn the finding; none was found. Also test B issued while A's terminal feedback is retained, and delayed A updates versus B's latest-target evidence.
Fix class: design, narrowly within the existing scalar request-reconciliation boundary; no general lifecycle consolidation or pair rewrite is warranted.
Actionable: yes, must be resolved before declaring full separate-feedback acceptance. A narrowly authorized primitive and mounted reproduction should precede the repair lease; no reproduction was run here.

### F2 - Native on-lattice callback identity: policy compatibility correction

Steelman: the error is tiny and the emitted raw byte is identical, so asserting exact output from generic snapping can be brittle. But the failing assertion checks an existing source API: one native input, one unchanged normalized callback. Adoption introduced an extra snap despite the original input already representing an allowed range choice. Dropping all snapping would instead violate the existing 2561 -> 2600 contract.

Verdict: C for policy-specific native candidate preservation; already-shared for snap/clamp machinery. No evidence requires a generic snapToStep redesign or an RF-specific presentation normalizer.
Rank: diverged.
Elements: `frontend/src/primitives/scalar/value-control-core.ts:snapToStep`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:snap,nativeRangePolicy.normalize,applyCandidate`; `frontend/src/components-v2/layout/__tests__/semantic-controls.component.svelte.test.ts` native-input compatibility test.
Consumers: core snap has heterogeneous math/rendering/policy consumers listed above. Native policy serves Filter and both candidate separate RF/SQL scalars. The compatibility test mounts RF through its semantic layout with omitted feedback, so the regression also exists in reading compatibility, independently of command confirmation.
Definition site: generic arithmetic is value-control-core; native acceptance/normalization is continuous-scalar policy. The RF surface has no legitimate ownership of another generic decimal normalizer.
Divergence: observed standalone arithmetic and supplied CI failure: .7 becomes .7000000000000001. Shifted/negative minima show other representations of the same problem. Generic snap's current contract does not promise input identity, but native callback compatibility requires it for already-admissible choices. Final raw conversion being unchanged bounds impact; it does not remove the API regression.
Prior ruling: scalar-pair F6, 2026-09-06, permits distinct policies; MOR-2409 explicitly preserves source/API props and native callbacks. The existing off-lattice native test and exact semantic callback test were read; no prior universal decimal canonicalization ruling was found.
In-flight: one existing native normalize method can own the correction. No other inspected helper already supplies the required stable-input classification. The tuning-store snap is a different context; pair's clamp-only axis policy does not preserve native scalar's off-lattice requirement.
Required surface: preserve finite in-bounds candidates already representing a point of the declared min-relative lattice; retain existing nearest-step behavior for true off-lattice input, including 2561 -> 2600, ties, clamping, negative/shifted domains, invalid-domain and nonfinite rejection. Numerical classification must account for arithmetic error in subtraction/division, not merely exact integer quotient. Any machine-error allowance needs a bounded derivation and tests showing genuinely off-lattice inputs still snap; it must never touch canonicalOf, descriptor.matches, markers or feedback values. Do not add blanket decimal rounding, hardcode hundredths/255, or weaken the semantic callback assertion to approximate equality by default.
Depends on: none for native normalization. F1 remains a separate acceptance blocker even after this correction. Broader generic snap changes would require separately proving compatibility across its other consumers, not silently inheriting this lease.
Confidence: high on the regression and native policy ownership; medium on a particular numerical implementation, which is intentionally not prescribed.
Falsifier: an accepted source contract permitting normalized callback changes despite this preserved exact test, or a supported native path proving input resnapping is essential and equivalence-only callbacks are the actual API. Conversely, a bounded native-policy fix passing shifted/negative/tie/off-lattice/guard witnesses without changing other policies supports this finding. Equality of final raw 179 alone does not falsify it.
Fix class: none for mechanism relocation; bounded behavioral correction in the existing native policy. No new shared arithmetic framework is required by the evidence.
Actionable: yes after a primitive-owner lease expansion; not an assertion relaxation or local RF patch.

### F3 - Exact wire target and confirmation: correctly located and not the repair target

Steelman: making feedback or candidate equal by snapping canonical to .01 would apparently repair both display and retirement. It would also incorrectly treat nearby wire values as the same requested radio state. The difference 179/255 - .7 is about .00196078, a real wire-quantization effect, not floating-point reconstruction noise.

Verdict: already-shared descriptor/confirmation mechanism; C for normalized-to-wire boundary conversion.
Rank: parallel, candidate cleared.
Elements: `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:RF_FRONT_END_LEVEL_INTENT`; `frontend/src/lib/stores/commands.svelte.ts:normalizedLevelCommand,RF_GAIN_COMMAND_DESCRIPTOR,SQUELCH_COMMAND_DESCRIPTOR,reconcileStateBackedCommands`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback,getRfSqlControlFeedback`.
Consumers: separate and combined semantic callbacks reach the same wire conversion and raw handlers. RF/SQL descriptors/projector feed both lane consumers with qualified normalized evidence. Scalar/pair presentation consumes rather than confirms it.
Definition site: wire conversion in wiring; normalized expected value and exact match in descriptors; admitted post-ACK reconciliation in command store.
Divergence: none established in this candidate's units or equality rule. u and t are legitimately distinct because the command envelope is an integer. No proof of every physical provider echoing every request is claimed.
Prior ruling: RF/SQL-feedback F3 and scalar-pair F7, 2026-09-06, preserve exact normalized matching and existing wire conversion.
In-flight: descriptors and qualified feedback are implemented and unchanged by the candidate.
Required surface: exists. Keep ACK/equal-or-older marker/newer mismatch nonconfirming, and newer exact same-scope observation confirming, independently for RF and SQL. Preserve full canonical/target/requested values without UI-step snapping.
Depends on: none; F1 and F2 must conform to this existing boundary.
Confidence: high for inspected frontend contract; physical-provider outcomes outside this audit.
Falsifier: a source-backed unit mismatch in the actual descriptor or an accepted provider contract equating different values, neither shown by the supplied callback failure.
Fix class: none.
Actionable: no change to matching, wire conversion, timing or feedback projection to conceal either finding.

## Smallest prospective file boundary

This is a prospective scope decision, not an implementation lease or measured patch size.

- `frontend/src/primitives/scalar/continuous-scalar.svelte.ts`: native candidate normalization and the narrowly selected local-request handoff inside the existing owner. Keep other policy/source behavior explicit; do not assume every policy must change.
- `frontend/src/primitives/scalar/__tests__/continuous-scalar.test.ts`: exact .7 plus shifted/negative/tie/off-lattice/finite/bounds cases; quantized target retirement; retained older terminal/new request; rapid retargeting; existing active-pointer and deferred cancellation cases unchanged in meaning.
- `frontend/src/semantic/__tests__/RfFrontEndSurface.test.ts`: mounted two-lane input-then-feedback sequence, not a pre-populated outcome without a draft; independent sibling behavior and later canonical movement.
- `frontend/src/components-v2/wiring/__tests__/semantic-rf-front-end-wiring.component.test.ts`: input through the actual unchanged conversion followed by the store/projector observation path. Its current handler spies and manual confirmCommand calls alone are not that proof. A focused fixture adjustment or another explicitly leased observation harness may be required; do not claim existing routing tests prove full command execution.
- `frontend/src/semantic/RfFrontEndSurface.svelte`: already in the candidate's lease; change only if the selected owner contract requires consumer projection alignment. Adding panel transition/request history or wire quantization is not an acceptable fix. Merely setting preview='confirmed' in the policy does not fix this surface's explicit draft-first selection.

The smallest prospective source repair is therefore the existing scalar module, with the candidate's existing surface retained unless alignment is demonstrably needed. That expands the original three-file candidate by the scalar source and its focused test, not automatically by value-control-core. If a generic helper change is chosen instead, explicitly add `frontend/src/primitives/scalar/value-control-core.ts` and `frontend/src/primitives/scalar/__tests__/value-control-core.test.ts` and justify the wider consumer contract before implementation.

Read/run as regression consumers under a separately authorized verification step, without silently editing them: the failed semantic-controls test, existing FilterSurface/scalar and pair suites, focus/feedback ownership/debt tests required by the owner. No source lease is earned for SemanticRadioSurfaces, command store, adapter, backend, pair or legacy renderer. Existing exact observation tests remain binding. If the new mounted proof cannot fit the named fixture paths, return the precise additional test path for lease review before creating it.

## Weakest link

F1's minimum reconciliation design is the least certain judgment, not the existence of the stale-draft branch. The pair's observation-associated LocalRequest is persuasive prior art but cannot be copied wholesale: scalar tests explicitly retain active pointer drafts through pending feedback, and older terminal evidence must not erase a newer native request. The first authorized reproduction should mount one separate lane, generate .7 through its input, deliver the actual quantized target and newer exact confirmation under unchanged authority, then deliver .8 canonical. Add a rapid-retarget version with retained older outcome evidence. This distinguishes local request retirement from both radio confirmation and gesture cancellation before selecting any new internal state or public surface.

## Cleared

- Existing shared scalar owner, native input dispatch surface, policy distinctions, and canonicalOf's exact evidence preservation.
- Existing pair LocalRequest/observation handling as real request-presentation ownership, not a second radio truth store; no forced pair/scalar algorithm merge.
- Existing raw integer conversion, normalized descriptor targets and exact post-ACK confirmation. No epsilon, percentage or UI-step confirmation fix.
- Exact normalized callback compatibility as a valid test contract, while bounding the demonstrated arithmetic defect to pre-wire identity rather than a different transmitted integer.
- Public test-consumed calculateDragValue and handleWheelStep against unapproved deletion; null-returning native key/wheel policy against false dead-code findings.
- No blanket all-policy numerical rewrite, new decimal framework, duplicated RF normalizer, SRS edit, backend change, hardware claim, publication or full-acceptance claim.
