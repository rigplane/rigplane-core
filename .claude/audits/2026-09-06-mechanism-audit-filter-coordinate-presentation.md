# Mechanism audit: current Filter Width coordinates and presentation

Audit date: 2026-09-06. Owner: MOR-2407, under MOR-2215.
Audited revision: `e9f6800adb4ffad83b2d1b70fbb0680b707d3fe9`.
Collection revision: `0000e8fe67e8d4cde5b7f2d7df1a6716ab820fc7`.

## Decision

The preflight establishes limitations of the **unchanged built-in HBar**, not absence of a shared scalar capability. Existing scalar policy, full-feedback snapshots, cancellation, and replacing custom-renderer contracts can support local table geometry and localized presentation without creating another truth or transition owner. That is a source-level expressibility finding, not a completed Filter Width implementation proof.

For adoption that retains the existing HBar implementation and appearance, a narrow optional **renderer presentation** extension is warranted: value/position projection and formatting of an already-issued announcement. Keep choice stepping/normalization in the existing scalar policy, and transition deduplication in the existing scalar owner. A new scalar announcement-policy API, general coordinate mapper, fake index-valued feedback binding, or all-skin migration is not a prerequisite established by this audit. Reusing HBar through small explicit props is preferable to copying its complete renderer merely to avoid adding props; custom rendering remains a valid counterexample to the claimed missing shared mechanism, not a mandate to create that copy.

No implementation lease, publication approval, provider certification, or parent/whole-program completion follows. SemanticRadioSurfaces.svelte remains outside this change authority and exclusively MOR-2405; the preflight's generic abbreviation does not reserve that file.

## Method and pins

Applied `.claude/skills/mechanism-audit/SKILL.md`: definitions, prior rulings, in-flight targets, liveness, bounded enumeration, steelman, verdicts. The actual method file was read, not reconstructed. Previously read `AGENTS.md`, `CLAUDE.md`, and `.claude/agents/auditor.md` have unchanged blobs relative to the prior audit revision. Read the complete live MOR-2407 owner, new collection fact sheet and scope, structured row-level inventory, and preflight as a proposal. The preflight describes `d246b5c83038308d87e482c8122a4db6b640f64c`; it does not replace this audit's pin. Prior MOR-2401 rulings at `53b27aebbed6a8e27114c442a7420d589af23876` were reused, not its narrower enumeration as coverage of HBar or skins.

The checkout HEAD inspection returned `e9f6800ad` and status was clean. Immutable path-bounded comparison found no changes in any of the eight production and eight focused-test modules between collection and audit revisions. The wider repository did change, so the collection's full reference counts remain explicitly collection-pin evidence; targeted consumer and deletion searches were repeated at the audit pin. This report does not claim a rerun of the entire reference scanner at the later revision.

All product evidence was read through immutable Git objects. No product edits, Git writes, tests, builds, installs, browser, hardware, PR, issue, or baseline mutations were performed. Existing tests below were read, not run. Numerical witnesses are static evaluations or proposed tests, not observed hardware results.

## Step 0: definitions

Paths below are repository-relative, with owning symbols identified after the colon.

| Capability | Definition and relevant contract |
| --- | --- |
| Exact scalar truth and lifetime | `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:ContinuousScalarInput,ContinuousScalarView,ContinuousScalarPolicy,createContinuousScalar`. Command view retains the original feedback object and separate canonical/target/requested values. Policy normalizes candidates, not confirmed feedback. The owner manages drafts, timers, renderer leases, cancellation and announcement memory. |
| Transition decision and text | `frontend/src/primitives/control-feedback/control-feedback-presentation.ts:projectControlFeedbackPresentation,ControlFeedbackPresentationState`. The pure projector returns updated announced-ID history and nullable `politeAnnouncement` with phase/transition metadata. `PHASE_TEXT` supplies default English. The scalar retains the returned history. |
| Render projection | `frontend/src/components-v2/controls/value-control/scalar-render-presentation.ts:projectScalarRenderPresentation`. Command evidence forwards owner attributes, description, announcement and error; legacy decoration is allowed only for reading evidence. This helper owns no transition history. |
| Built-in HBar | `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:Props,fillPercent,displayValue,handlePointerDown,handlePointerMove,renderPresentation`. It reads a lease snapshot, maps displayed values linearly to fill, maps pointer position into the numeric domain, delegates wheel/key/reset to the lease, and emits its own conditional polite status. `displayFn` does not replace geometry or the status message. |
| Built-in discrete renderer | `frontend/src/components-v2/controls/value-control/DiscreteRenderer.svelte:tickItems,fillPercent,handlePointerDown`. Numeric min/max/step enumerate uniform ticks. Tick labels are presentation, not a nonuniform choice catalog. |
| Composition and skins | `frontend/src/components-v2/controls/value-control/ValueControl.svelte:RawProps,BoundContinuousProps,createRawBinding,rendererProps,skinComponent`; `frontend/src/components-v2/controls/value-control/skin.ts:SkinRendererProps,Skin`. Raw mode creates reading evidence; bound mode uses the supplied owner. A selected skin component replaces the built-in branch, receives the full binding, and is not an additional renderer mounted alongside it. No table or whole-status formatter prop currently exists in this wrapper contract. |
| Legacy current width | `frontend/src/components-v2/panels/FilterPanel.svelte:hzToTableIndex,tableIndexToHz,formatWidthDisplay,filterWidthLifecycle,lastFilterWidthTransitionId,filterWidthLiveStatus`. Current table input uses nearest indices as raw scalar readings; the mutually exclusive non-table BW row is read-only. The panel separately freezes localized lifecycle messages. |
| Supporting definitions | `frontend/src/primitives/scalar/value-control-core.ts:getFillPercent,calculateClickValue,enumerateDiscreteValues`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:getFilterWidthControlFeedback,getFilterWidthCommandLifecycle`; `frontend/src/semantic/FilterSurface.svelte:filterWidthInput,filterWidthScalar`. These explain existing geometry and adoption; they are dependencies, not additional enumerated modules or edit targets. |

## Step 1: prior rulings

- MOR-2401, 2026-09-06, `.claude/audits/2026-09-06-mechanism-audit-cw-filter-feedback.md:F2`: "Apply quantization to requested choices, not radio truth." Its weakest link explicitly requires a nonuniform-table adopter proof and acknowledges the absent HBar coordinate prop. That caveat is the question being adjudicated, not an accepted answer that all existing interfaces suffice unchanged.
- The same audit's F5 requires "one lifetime/announcement owner" and protects the live SpectrumPanel compatibility accessor. Localized copy was not ruled to be another transition owner by itself.
- MOR-2215, 2026-09-02, `docs/plans/2026-04-12-target-frontend-architecture.md:Addendum 2026-09-02`, conformance item 4: "Instrument geometry goes through a display model the instrument consumes as a component prop". This supports keeping geometric presentation outside shared behavior; it does not authorize a permanent copied instrument or a broad directory migration in this task.
- MOR-1641, 2026-08-14, `docs/plans/2026-08-14-state-backed-command-lifecycle.md:Decision`, as traced in the accepted prior audit: "Transport acceptance is never radio confirmation." The distinction remains binding; no tolerance, optimistic confirmed value, or deadline change is proposed here.
- MOR-1409 A12's unknown guard is recorded in `FilterPanel.svelte:formatWidthDisplay` and focused tests. Its original ruling date is not established here; pinned evidence was checked on 2026-09-06. The existing formatter cannot rescue unknown evidence after it has already become finite index zero.

## Step 2: in-flight and already-existing targets

The full Filter Width accessor already projects the active receiver's command feedback. `FilterSurface.filterWidthScalar` already uses that feedback through the continuous owner; legacy FilterPanel does not. Accepted MOR-2398 provider-generation fencing is present in `continuous-scalar.svelte.ts:authorityOf,reconcile`; this is consumed infrastructure, not a new prerequisite to invent.

`ValueControl.createRawBinding` and the built-in renderers already share interaction lifetime. `ValueControl.skinComponent` exposes replacing components with that same binding. `skins/index.ts:professionalSkin` registers only ProfessionalKnob; `ControlButtonDemo.svelte` uses it. Focused wrapper tests exercise all four typed renderer slots, and controlled tests exercise Standard-to-Professional replacement, preserved feedback, one-shot announcements and revoked old callbacks. These are concrete existing extension/lifetime contracts, not proof that a Filter-specific custom renderer has already been implemented.

MOR-2407 is In Progress with collection accepted and no implementation lease. The preflight's proposed six-file shared dependency and subsequent three-file adoption are estimates/hypotheses, not existing fixes or approved file authority. No all-skin coordination requirement was found in the live owner.

## Step 3: liveness and consumer separation

- FilterPanel has one current table ValueControl source site and one mutually exclusive read-only BW site. Both use the local lifecycle/text effect. Its settings modal separately consumes both index helpers; those helpers are not dead after current-width adoption. `handleTableReset` is a separate compound width/IF-shift command, not the HBar double-click reset policy; do not silently redefine it as part of scalar choice stepping.
- HBar and Discrete are live through ValueControl's built-in branches and typed skin tests. HBar's geometry helpers are actual called definitions, not local duplicate arithmetic owners. `displayFn` is live but only formats visible values. The built-in command status region is also live, as directly asserted in `ValueControl.controlled.svelte.test.ts`.
- The full feedback accessor serves semantic wiring; the compatibility accessor currently serves both FilterPanel and SpectrumPanel. Removing one consumer does not permit deleting the accessor. Semantic wiring was read only to establish that dependency, not brought into scope.
- `projectControlFeedbackPresentation` is called by the scalar owner; `projectScalarRenderPresentation` is called by the built-in renderers and ProfessionalKnob. The former determines whether a transition is new using caller-retained history; the latter does not independently decide transition identity. Shared definitions plus several imports are not several mechanisms.
- Local Filter Width message keys exist in `frontend/src/lib/i18n/locales/en-US.json` and `ru-RU.json` and are consumed by the panel's `t(...)` calls. They include confirmed and requested/target information absent from a mere translated phase prefix. Default scalar English and localized panel copy are distinct presentation policies; only their competing transition bookkeeping needs convergence.

Focused test bodies inspected include exact DTO retention and read-consumed announcements, provider/domain/deferred-work invalidation, renderer replacement, raw versus bound presentation precedence, all four skin slots, Filter Width pending/terminal presentation and canonical-only live-region immutability, and the FilterPanel armed-selector regressions. The ownership test checks import boundaries, not the number of mounted live regions. No inspected test proves the complete proposed nonuniform/localized adoption.

## Step 3a: bounded enumeration

The new row-level collection enumerates 1,331 rows: 390 production and 941 focused-test rows, with 657 production reference paths and 894 test reference paths, including `frontend/tests`. There are zero missing scoped modules and zero reported parse errors. Row kinds: 411 anonymous arrows, 54 arrow bindings, 93 assigned-state attributes, 108 assigned-state bindings, 561 constants, 79 functions, 25 methods/object methods. Rows are scanner entries, not 1,331 unique mechanisms; a declaration can appear under multiple patterns.

| Production module under `frontend/src/` | Rows |
| --- | ---: |
| `components-v2/panels/FilterPanel.svelte` | 90 |
| `components-v2/controls/value-control/ValueControl.svelte` | 41 |
| `components-v2/controls/value-control/HBarRenderer.svelte` | 36 |
| `components-v2/controls/value-control/DiscreteRenderer.svelte` | 52 |
| `components-v2/controls/value-control/skin.ts` | 1 |
| `components-v2/controls/value-control/scalar-render-presentation.ts` | 2 |
| `primitives/scalar/continuous-scalar.svelte.ts` | 153 |
| `primitives/control-feedback/control-feedback-presentation.ts` | 15 |

Focused-test rows, at their scoped paths: `FilterPanel.isolated.test.ts` 223; `mor1536-armed-adoption.test.ts` 166; `ValueControl.controlled.svelte.test.ts` 220; `ValueControl.test.ts` 140; `scalar-render-presentation.test.ts` 12; `continuous-scalar.test.ts` 141; `control-feedback-presentation.test.ts` 21; `control-feedback-ownership.test.ts` 18.

Every row retains separate lexical production/test writes and reads. All owner-attributed counts are null. The scanner excludes strings/comments, approximates TypeScript/Svelte definitions and references lexically, and cannot resolve aliases, receivers, dynamic access or all markup/template uses. Zero reported parse errors is not semantic TypeScript validation. The 561 zero production non-definition lower bounds are not 561 dead symbols.

The complete named production zero-lower-bound candidate set was inspected: both `fillPercent` bindings, `hasTickLabels`, `effectiveOnChange`, `mountId`, `filterArmedIdBase`, `snapToTable`, and the three `confirmedPressed/Checked/Selected` exports. Markup reads clear fill/tick candidates; the callback and template-string uses clear the other local bindings. Exported confirmed-state helpers have focused test consumers and public importable contracts, so production lexical zeros do not authorize deletion. Anonymous framework callbacks and test-local declarations are not dead merely for lacking production reads. Only D1 passes deletion guards. No constant-return reachability proof was established for any further deletion.

## Step 4: competing explanations

**Best case for leaving local concerns local.** Nonuniform choice spacing describes how this control is drawn; it need not redefine the radio's number. A policy can return table-choice Hz for key/wheel/reset/normalization while an explicit renderer maps Hz to positions. A pure formatter can use the owner-issued nullable announcement metadata plus the same view's full feedback, with no new ID history. The replacing skin branch means that custom rendering does not automatically leave a hidden English HBar emitter behind. This defense wins on ownership and expressibility.

**Best case for the preflight's narrow gap.** The current built-in HBar has no hook for either projection. Its display formatter cannot change fill or whole-message English, and command evidence deliberately ignores `feedbackStatus`. Remapping feedback to indices breaks the required contract. A custom renderer must actually receive reactive table/locale context, handle pointer capture, preserve appearance/accessibility, and consume announcements correctly; merely citing `Skin.hbar` does not implement those things. Avoiding a small prop extension by copying HBar would increase presentation maintenance and conflict with the single-instrument direction. This defense wins for a bounded built-in-renderer extension, not a missing scalar engine or mandatory all-skin API.

**Best case for migration incomplete.** All command and lifetime mechanisms already exist and have live consumers. What remains is correctly composing them for legacy current width. The distinction is falsifiable: a mounted adopter that needs a second transition history or a second numeric truth binding would fail, whereas pure projections over the existing owner do not. Snapshot acquisition and context changes, rather than broad new abstractions, are the highest-risk integration points.

## Step 5: Deletions

### D1 - FilterPanel.snapToTable: dead, independently revalidated

Steelman: a similarly named helper may support preset requests or external component consumers, and lexical zero counts miss dynamic use. Here the actual preset call sites use the other two helpers, while this function is private to the component script and has no exposed reference.

Verdict: dead.
Rank: first and only deletion.
Elements: `frontend/src/components-v2/panels/FilterPanel.svelte:snapToTable`.
Consumers: none established; current width and preset editing do not call it.
Written / read: collection row reports production 1 write/0 reads and tests 0/0. Independently, `git grep -n -w snapToTable <audit-revision> -- ':!*.md'` returns exactly the definition; the complete component reference scan finds no call or exposure.
Guards checked: literal search explicitly includes tracked source/test/config code; inspected component exports/access paths and found no export, registry, reflection or dynamic exposure of this private function. No exhaustive sibling-repository search is claimed; importing/mounting this component cannot access this unexported script-local function. No tests-only consumer exists. Documentation mentions do not execute it.
Collateral: remove the helper's own obsolete explanatory block with its body; no behavioral test or sibling helper deletion follows. Prior audit documentation remains historical evidence.
Depends on: none; does not unblock feedback adoption.
Confidence: high.
Falsifier: an exposed closure, runtime registry or actual call reaching this function at the pinned revision.
Fix class: delete.
Actionable: technically yes, but only if explicitly included in a later owner-approved lease; no deletion was performed.

## Consolidations

### F1 - Exact Hz and equal-spaced choices: local projection, conditional HBar extension

Steelman: nearest indices efficiently preserve the existing table gesture/visual convention, and policy/displayFn are intentional extension points. However, those points cannot change the built-in linear fill while leaving the scalar's canonical value in Hz. A replacing renderer can; copying a renderer solely to avoid props is not automatically the smallest maintenance surface.

Verdict: C for table geometry and choice policy; already-shared for scalar truth/lifetime. B only for the missing presentation surface if retaining the built-in HBar is selected, not for the scalar primitive or a universal mapper.
Rank: diverged.
Elements: `frontend/src/components-v2/panels/FilterPanel.svelte:hzToTableIndex,tableIndexToHz`; `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:fillPercent,handlePointerDown,handlePointerMove`; `frontend/src/components-v2/controls/value-control/skin.ts:SkinRendererProps,Skin`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:canonicalOf,applyCandidate,ContinuousScalarPolicy`.
Consumers: current table width uses local indices; excluded presets also use the helpers. HBar uses shared numeric geometry through ValueControl. Native FilterSurface consumes Hz directly. Optional custom renderers receive the full binding, but no existing Filter-specific custom HBar was established.
Definition site: local conversion in FilterPanel; projection in HBar/value-control-core; request policy and authoritative values in continuous-scalar.
Divergence: unknown reaches initialized nearest index zero; finite off-table canonical values become neighboring choices. Keeping an unmodified Hz binding fixes that truth substitution but the unchanged HBar remains linear in Hz. Policy can restrict requests, not rewrite its fill; DiscreteRenderer's uniform ticks do not solve this. Visible-value formatting is independent of both mechanisms.
Prior ruling: MOR-2401 F2, 2026-09-06, "Apply quantization to requested choices, not radio truth"; MOR-2215 geometry-as-display-model ruling, 2026-09-02.
In-flight: existing full-feedback accessor, custom-renderer binding contract and candidate policy are sufficient mechanisms; faithful legacy presentation remains unimplemented.
Required surface: keep confirmed/target/requested feedback and scalar values in Hz. A local, pure, explicit geometry model maps known Hz to bounded positions and pointer positions to candidate Hz. Off-table geometry may interpolate without snapping the numeric evidence; out-of-range visual clamping must not fabricate a boundary reading. Unknown or unusable geometry must not become the first choice. Choice policy uses the actual catalog for pointer normalization, ordinary/fine wheel, keyboard and scalar reset. Keyboard/wheel need no geometric hook in the owner. For built-in reuse, add only optional presentation props and necessary forwarding, with unchanged defaults. Exact numeric/ARIA evidence must remain Hz; any abbreviated visible formatter needs an explicit precision policy rather than a claim of lossless formatting for every Hz value.
Depends on: none to establish the geometry limitation; the feedback/provider fence already exists. Final current-width adoption must satisfy F2 and F3 alongside this contract, not wait for three separate foundations. Preset redesign and other skins are not dependencies.
Confidence: high on the built-in limitation and local ownership; medium on the minimum finished integration until a mounted witness exists.
Falsifier: on `[1800,2100,3000]`, keep canonical 2100 Hz and place it at 50%, not linear-Hz 25%; keep off-table 2500 Hz exact, with an explicitly selected between-choice projection (piecewise interpolation gives about 72.22%, versus linear-Hz 58.33%). Request actual choices from pointer/key/wheel/reset and preserve separate requested/target Hz. An unchanged-policy/displayFn-only HBar satisfying this would disprove its hook limitation. A replacing renderer doing so with the existing binding disproves a general scalar API gap. Also probe null and a non-round value such as 2501 Hz. The preflight's `[1800,2400,3000]` is uniformly spaced and cannot discriminate the geometry claim by itself.
Fix class: design only for the selected HBar presentation prop contract; none for moving local geometry into shared behavior.
Actionable: yes as a bounded design/adoption requirement; no unconditional new primitive, copied instrument, or all-skin migration approval.

### F2 - Localized messages: consolidate transition decisions, keep formatting local

Steelman: the existing panel freezes an informative localized message and prevents later canonical polls from rewriting historical status. Keeping it preserves more than English phase text. But retaining its own last-transition bookkeeping beside a bound HBar would add a second decision path and actual emitter. Conversely, a pure formatter using one owner-issued snapshot is not another lifecycle, and a replacing renderer need not mount the HBar emitter at all.

Verdict: already-shared for transition deduplication; C for localization. Migration incomplete for FilterPanel's local transition bookkeeping. No missing scalar announcement-policy API is established; the built-in renderer lacks only an explicit whole-status presentation override.
Rank: diverged.
Elements: `frontend/src/components-v2/panels/FilterPanel.svelte:lastFilterWidthTransitionId,filterWidthLiveStatus`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:viewOf,presentationState`; `frontend/src/primitives/control-feedback/control-feedback-presentation.ts:projectControlFeedbackPresentation`; `frontend/src/components-v2/controls/value-control/scalar-render-presentation.ts:projectScalarRenderPresentation`; `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:renderPresentation` and its status span.
Consumers: one panel effect feeds either current-width layout. The scalar serves native FilterSurface and bound renderers; render projection serves HBar, Discrete, Bipolar, Knob and ProfessionalKnob. The custom-renderer branch replaces, rather than decorates, a built-in branch. Existing en-US/ru-RU Filter Width keys serve the panel.
Definition site: announcement history is retained inside createContinuousScalar; its pure projector decides token novelty. Localized Filter Width copy lives in panel/i18n presentation. The render projection does not own history.
Divergence: panel copy distinguishes requested and confirmed width; scalar default English cannot be fully replaced by describeTarget alone. Legacy feedbackStatus is deliberately ignored for command evidence. Current panel dedup compares only its last ID; the shared owner remembers prior IDs within its authority. They are not equivalent implementations to leave running together.
Prior ruling: MOR-2401 F5, 2026-09-06, "one lifetime/announcement owner". `FilterPanel.isolated.test.ts` MOR-1665 coverage protects frozen terminal text across canonical-only updates; original issue date is not inferred from the test heading.
In-flight: view exposes both nullable politeAnnouncement metadata and the original full feedback. That is sufficient input for a stateless localized formatter; no English-message parsing, synthetic feedback, second projector state, or runtime import into the scalar is needed.
Required surface: acquire one authoritative view snapshot for the presentation update. Only an owner-issued non-null announcement may cause a new live message; format it with that same snapshot's confirmed, target/requested, phase and outcome. Keep requestedTarget distinct from target and label the evidence accurately. Do not re-read binding.view to obtain formatting fields: viewOf consumes announcement memory on reads. Freeze each issued message's inputs; later canonical/locale changes must not turn retained metadata into a new announcement. A cache of already-issued display text is not a second transition authority, but its DOM mutation/clearing behavior still must satisfy the existing frozen-message contract. Preserve errors once. Table mode must have one actual live emitter, not a hidden/default English region plus a localized panel region; non-table BW stays read-only with the same one-owner rule. For built-in reuse, place an explicit optional formatter at HBar or its pure render-presentation boundary, gated by the owner token, and forward it only where used. Do not revive implicit legacy overrides or place Filter-specific i18n inside the scalar.
Depends on: none to establish formatting expressibility. Final table adoption consumes F1's truthful geometry and F3's continuity constraints; they are joint acceptance conditions, not prerequisites to a new formatter foundation. Existing locale keys cover five outcome groups; full phase mapping, including superseded, needs an explicit truthful local policy, not an assumed existing translation key.
Confidence: high on the existing data/ownership surface; medium on mounted live-region delivery and retained-text behavior.
Falsifier: a mounted localized adopter with full DTO values (including distinct requested/target), one live region, no panel ID history, no English duplicate, and one message per new transition disproves the claimed scalar formatter gap. Exercise repeat reads, canonical-only updates, delayed older transitions, errors, locale changes and table/non-table replacement. A required semantic field absent from both the owner-issued metadata and that same full view would justify reopening only that precise formatting-input surface.
Fix class: consolidate transition bookkeeping; design only the selected built-in presentation extension, not a new shared transition mechanism.
Actionable: yes for one-owner/one-emitter adoption with local copy; no mandatory scalar-policy hook or all-renderer formatter retrofit.

### F3 - Table/mode replacement: existing cancellation, avoid accidental announcement resets

Steelman: destroying/recreating the binding reliably revokes its deferred work and causes HBar to release pointer capture, so the preflight does identify a usable cancellation mechanism. Yet a fresh binding also starts with empty announcement history. A presentation/catalog replacement within the same command authority is not automatically a new transition warranting replay.

Verdict: already-shared cancellation; C for identifying local catalog/mode changes. Undetermined for the preflight's complete recreate-on-every-change recipe satisfying once-only delivery; no new universal context-key API is established.
Rank: diverged.
Elements: `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:authorityOf,clearTransient,reconcile,cancel,destroy,attachRenderer`; `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:attachedBinding,activePointer` and replacement/cleanup effects; `frontend/src/components-v2/panels/FilterPanel.svelte:isTableMode,filterConfig`.
Consumers: the shared owner governs all scalar renderer leases. HBar owns actual DOM pointer capture. FilterPanel owns current table/mode context, which the scalar's numeric authority tuple does not encode.
Definition site: timer/token invalidation and announced-ID memory are scalar-owned; DOM capture is renderer-owned; catalog identity is composition-local.
Divergence: same-length tables with identical endpoints can change interior choices without changing numeric authority. Replacing only policy closure data will not cancel an already-normalized deferred candidate. Conversely, destroy/new-owner empties the ID history and can issue the retained transition again. Existing cancel clears timers/drafts/active gesture without clearing presentationState; renderer disposal likewise does not itself reset that history. cancel alone does not release HBar's DOM pointer capture.
Prior ruling: MOR-2401 F2/Weakest link, 2026-09-06, requires table/mode replacement to invalidate old gestures and deferred requests despite equal cardinality. Existing controlled-renderer tests protect one-shot identity across appearance replacement. No prior requirement to reset announcement memory for every geometry change was found.
In-flight: existing cancel, lease disposal/replacement and renderer cleanup provide the necessary operations. Domain/provider/session authority changes already reset state in reconcile; they must not be falsified with fake command names, receiver slots or numeric bounds.
Required surface: compare actual mode and complete relevant table content, not length/endpoints alone, and invalidate before stale work can dispatch. Within unchanged command/numeric authority, prefer existing owner cancellation with renderer capture cleanup or renderer replacement while retaining the binding. Supply context explicitly to the selected renderer/composition; do not read stores in a generic renderer. If actual domain/authority change or owner recreation is selected, specify and verify its announcement-reset semantics rather than claiming once-only preservation automatically. Local effect ordering, fresh choice policy, stale pointer moves and pending timers require a mounted witness.
Depends on: existing scalar cancellation and accepted provider fencing, both present. F1/F2 supply the adopter's geometry and announcement acceptance conditions, not separate changes that must land before cancellation can be used. No preset or all-skin work is required.
Confidence: high on source cancellation/reset distinctions; medium on the eventual reactive composition ordering.
Falsifier: announce transition T, queue a key request/start a drag, then replace `[1800,2100,3000]` with `[1800,2200,3000]` or change mode without changing the domain. Old deferred work and pointer tokens must be inert, capture released, a new gesture must use the new catalog, and retained T must not become a second live announcement solely because geometry changed. If the existing cancel/dispose operations cannot satisfy this with one owner, reopen only the demonstrated missing operation.
Fix class: none for a new mechanism; bounded composition/lifetime integration with existing operations.
Actionable: yes as a required falsifier and integration constraint, not as certification of the unimplemented recreate recipe.

## Smallest warranted boundary

1. Keep the existing Hz feedback/accessor, scalar owner and provider fence. The current adopter supplies full feedback and a catalog-specific policy, not index-remapped truth.
2. For the preferred built-in-reuse route, limit design to HBar's optional geometric projection and token-gated status formatting, plus necessary ValueControl forwarding and focused tests. A renderer-local value-text contract may carry truthful Hz units/precision. Scalar wheel/key/reset policy already handles choices; shared scalar internals need no new formatting or coordinate ownership by premise.
3. `skin.ts` changes are conditional on offering those optional props through the typed custom-HBar path. The existing custom-component binding API already permits bespoke rendering, but does not magically supply table/locale context. A typed HBar-specific extension need not enlarge Knob/Bipolar/Discrete contracts or require their implementations to adopt table geometry. No normal control must change its call site; omitted props must preserve existing behavior. No broad design-language/skin migration is authorized.
4. Current FilterPanel adoption and its focused fixtures consume the accepted contract. Preserve the read-only BW branch, live SpectrumPanel compatibility, excluded preset helpers/requests and other panel controls. Include D1 only by explicit lease. Do not accept the preflight's file counts or A+D estimates as a frozen package before the actual renderer/context/text design is selected.

## Weakest link

The least certain judgment is the smallest complete mounted presentation integration, not the location of command truth. Existing custom rendering is expressive, but no inspected component combines nonuniform geometry, explicit reactive catalog context, frozen localized transition copy and one live emitter for this Filter Width adopter. The built-in-reuse recommendation avoids an unnecessary renderer copy but still needs a narrowly specified prop contract.

Check first one mounted witness combining the nonuniform 2100-at-50% case, off-table/unknown full Hz feedback, distinct requested/target, localized terminal text, and an unchanged-endpoint catalog replacement during deferred input. Count actual live regions and their mutations, and ensure the first transition token is not consumed by an extra view read. Read-only source inspection cannot replace that proof. Failure may justify a particular HBar/composition extension; it does not automatically establish a scalar formatter API, universal mapper, or all-skin dependency.

## Cleared

- Existing full Filter Width feedback, shared scalar lifetime, provider authority fence, renderer leases and candidate-policy extension points. Legacy current-width feedback migration remains incomplete, not absent infrastructure.
- Explicit local coordinate projection that leaves numeric radio evidence unchanged; local request-choice policy; stateless localization of an owner-issued snapshot. None is a second truth/lifetime owner by definition.
- Replacing custom-renderer contract and its single mounted renderer branch. Its existence disproves mandatory shared-mechanism expansion, but not the need to implement/reactively supply the chosen presentation.
- Unchanged ordinary HBar/Discrete/Knob/Bipolar behavior as the default; no requirement that every skin implement Filter-specific geometry or messages.
- Live preset mapper consumers, SpectrumPanel compatibility accessor, armed-filter presentation and public confirmed-state helpers. Their deletion or migration is not licensed by lexical zeros or this current-width adoption.
- No backend/provider parity, CW work, timing-policy change, broad instrument relocation, SemanticRadioSurfaces edit, or parent completion claim.
