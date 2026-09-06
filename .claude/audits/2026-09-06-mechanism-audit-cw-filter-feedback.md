# Mechanism audit: CW scalars and legacy current Filter Width

Audited revision: `53b27aebbed6a8e27114c442a7420d589af23876`.
Audit date: 2026-09-06. Scope owner: MOR-2401, under MOR-2215.
Separately inspected accepted dependency: MOR-2398, `3a60221cd0f34e627296ea343d38c264571cad45`.

Method: `.claude/skills/mechanism-audit/SKILL.md`, read in full from the actual repository checkout, not reconstructed from the dispatch. Also read `.claude/agents/auditor.md`, `AGENTS.md`, `CLAUDE.md`, and live owner acceptance. The checkout HEAD inspection returned `3a60221cd`; the tree was clean. All product evidence below was read through immutable Git objects at the audited revision, except the explicitly identified later dependency. The later revision does not replace the audit pin.

This is independent read-only adjudication, not implementation approval, a publication change, or acceptance of the parent scope. No product edits, Git writes, tests, builds, installs, browser or hardware operations were performed. Scope is existing CW Pitch, Keyer Speed, and legacy CURRENT Filter Width. Preset editing, RF/SQL, other scalar parameters, and broader provider parity are excluded.

## Step 0: definitions

Paths are repository-relative. Symbols, rather than import counts, identify owners.

| Capability | Definition and contract |
| --- | --- |
| State-backed command lifecycle | `frontend/src/lib/stores/commands.svelte.ts:StateBackedCommandDescriptor,STATE_BACKED_COMMAND_DESCRIPTORS,transition,reconcileStateBackedCommands`. Exactly two registrations: Filter Width and Break-in Delay; neither CW scalar is registered. |
| Feedback projection | `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback,getFilterWidthControlFeedback,getFilterWidthCommandLifecycle`. The last is a compatibility projection of the full Filter Width feedback, not a second confirmation engine. |
| Continuous interaction | `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:createContinuousScalar,ContinuousScalarInput,ContinuousScalarPolicy`. One owner supplies draft, dispatch, cancellation, renderer leases, and feedback presentation. Input numbers and policy are supplied by its caller. |
| Legacy wrapper | `frontend/src/components-v2/controls/value-control/ValueControl.svelte:RawProps,BoundContinuousProps,createRawBinding`. Raw props create reading-only evidence; an external binding supplies the complete scalar contract. The two prop contracts are exclusive. |
| CW surfaces | `frontend/src/components-v2/panels/CwPanel.svelte:cwPitch,keySpeed,formatCwPitchDisplay,formatKeySpeedDisplay`; `frontend/src/semantic/CwKeyerSurface.svelte:CW_LEVELS,setLevel`. Legacy uses raw ValueControl; native CW ranges use direct reading/callbacks. Break-in Delay's separate adopted path is not either of these controls. |
| Filter surfaces | `frontend/src/components-v2/panels/FilterPanel.svelte:hzToTableIndex,tableIndexToHz,filterWidthLifecycle,lastFilterWidthTransitionId`; `frontend/src/semantic/FilterSurface.svelte:filterWidthInput,filterWidthScalar`. Legacy table coordinates are indices; the semantic scalar and shared descriptor use Hz. |
| CW command units | `frontend/src/lib/runtime/commands/panel-commands.ts:makeCwPanelHandlers`; `frontend/src/lib/runtime/commands/radio-intents.ts:dispatchRadioIntentWithResult`. CW Pitch sends `{value}` in Hz; Keyer Speed sends `{speed}` in WPM. Neither carries a receiver. |
| Icom CW readback | `src/rigplane/web/radio_poller.py:RadioPoller._execute,_confirm_global_operator_write,_apply_global_control_observation`; `src/rigplane/runtime/radio.py:IcomRadio.get_cw_pitch,set_cw_pitch,get_key_speed,set_key_speed`; `src/rigplane/commands/levels.py:_cw_pitch_to_level,_cw_pitch_from_level,_key_speed_to_level,_key_speed_from_level`. |
| Yaesu CW boundary | `src/rigplane/backends/yaesu_cat/radio.py:YaesuCatRadio.set_cw_pitch,set_key_pitch,read_cw_pitch,read_keyer_speed`; `src/rigplane/backends/yaesu_cat/poller.py:YaesuCatPoller._execute_command,_emit_slow_control_observations`; `src/rigplane/backends/yaesu_cat/observations.py:YaesuObservationAdapter.poll_tx_controls`. |
| Canonical delivery | `src/rigplane/core/state_store.py:StateStore.apply,_apply_one`; `src/rigplane/web/runtime_helpers.py:_apply_snapshot_field,_observed_field_status,build_public_state_payload_from_snapshot`; `src/rigplane/web/server.py:WebServer._build_public_state_for_delivery,_encode_state_update`; `src/rigplane/web/_delta_encoder.py:DeltaEncoder.encode`; `frontend/src/lib/transport/ws-client.ts:isRevisionAcceptable`; `frontend/src/lib/stores/radio.svelte.ts:setRadioState`. |

## Step 1: prior rulings

- The 2026-08-14 plan `docs/plans/2026-08-14-state-backed-command-lifecycle.md:Decision`, MOR-1641, states: "Transport acceptance is never radio confirmation." Its MOR-1643 Filter Width acceptance separately requires canonical value, unconfirmed target, and visible/accessibility feedback. Existing partial adoption is not absence of that prior implementation.
- MOR-1409 A12, recorded in the CW/Filter formatter comments and their component tests, protects the `---`-family unknown display and prohibits fabricated defaults. The original ruling date is not established by those comments; the pinned source and test evidence were checked on 2026-09-06.
- MOR-1682, created 2026-08-14 and still Todo when read, owns the FTX-1 pitch domain correction. `docs/release-notes/2026-beta-known-limitations.md:FTX-1 limitations` records the existing 300-900 Hz/5 Hz UI versus 300-1050 Hz/exact 10 Hz radio domain and software flooring. This audit does not authorize expansion, a new quantizer, or automatic tolerance. The independently traced queued-setter bypass in F1 is more specific than that documented limitation.
- `docs/internals/legacy-state-writer-inventory.md:Icom Runtime and CI-V,Other Backends` distinguishes canonical observations from compatibility mirrors and executor caches. Its retained CW mirror note does not negate the actual matching-readback emitter now present. The document's historical classifications are not a substitute for tracing current code.
- `.claude/audits/2026-09-06-mechanism-audit-scalar-pair.md` adjudicated an older revision, `f2e7969708d5c93890cf1b24d833ce820b36dc15`. Its shared lifetime versus local policy distinction remains relevant; its then-missing renderer adoption is not replayed as current. The supplemental RF/SQL decision audit was considered only for the provider-dependency boundary, not as a grant to expand this tract.

## Step 2: existing targets and subsequent dependency

Observation at the audit pin: `ValueControl.createRawBinding` already routes the built-in scalar renderers through the shared lifetime. `FilterSurface.filterWidthScalar` already consumes full Filter Width feedback supplied by `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:filterWidthFeedback`. CW Pitch/Keyer Speed lack their descriptor/accessor adoption; legacy Filter Width uses the compatibility lifecycle plus a reading-only control. There is no missing general scalar engine to rebuild.

Observation at the separate accepted MOR-2398 revision: `commands.svelte.ts:beginCommand` captures provider generation; `reconcileStateBackedCommands` rejects mismatched/unresolved generation. `panel-adapters.ts:projectControlFeedback` excludes prior-provider live AND terminal records and carries current generation. `continuous-scalar.svelte.ts:authorityOf` includes generation; the existing reconciliation clears draft, gesture, delayed work and announcement memory on authority change. The committed-scalar dependency likewise incorporates provider identity. Tests added at that revision explicitly cover an acknowledged successful command on the same socket and scalar invalidation without an intervening null read. These tests were read, not run.

Inference: provider continuity is a resolved implementation dependency to consume, not a new MOR-2401 foundation. This is narrowly the generation fence, not certification of every provider's domain, timing, source qualification, or consumer adoption. The Yaesu routing and FilterPanel source implicated below are unchanged between the two revisions, established by a path-bounded `git diff --name-only`.

## Step 3: liveness and observation paths

### Consumers and count method

Literal pinned-tree searches over frontend production and tests were followed by opening the owning implementations. These are source sites, not counts of simultaneously mounted controls.

- `CwPanel` has four production mount sites: LeftSidebar, MobileRadioLayout, RadioLayout, RightSidebar. It contains one raw Pitch and one raw Key Speed control. `CwKeyerSurface` has one production mount site in SemanticRadioSurfaces and one native-range branch per CW scalar. Both routes use the existing CW handler factory and tracked radio-intent envelope.
- `FilterPanel` has two production mounts: LeftSidebar and MobileRadioLayout. Its current table-width control and non-table read-only BW row are mutually exclusive. `FilterSurface` has one production mount in SemanticRadioSurfaces and an existing full-feedback scalar.
- `getFilterWidthControlFeedback` has two direct production call sites: SemanticRadioSurfaces and `getFilterWidthCommandLifecycle`. The compatibility accessor has two production consumers: FilterPanel AND `frontend/src/components/spectrum/SpectrumPanel.svelte:filterWidthLifecycle`. It is not deletable after migrating only FilterPanel.
- `FilterPanel.hzToTableIndex` has one definition and two production calls, current width and excluded preset editor. `tableIndexToHz` serves both as well. `lastFilterWidthTransitionId` has three lexical writes and one read; `filterWidthLiveStatus` feeds both current-width layouts. These are live, not deletion wins.
- `_confirm_global_operator_write` has one definition and three production calls: CW Pitch, Key Speed, and Break-in. Its dynamic getter/setter test coverage in `tests/test_radio_poller_coverage.py:test_cw_operator_write_requires_matching_radio_readback` would not be found by a literal helper-name search alone.

### Icom: write, independent observation, same-value delivery

Observation: `RadioPoller._execute` writes through `set_cw_pitch`/`set_key_speed`, then calls the corresponding getter through `_confirm_global_operator_write`. Getter failure/timeout, provider replacement, or value mismatch does not produce that confirming observation. A matching result enters global `operator_controls.cw_pitch` or `operator_controls.key_speed` with `poll_response`, provider `web_poller`, a named readback native ID, captured generation, and available command/session provenance. The compatibility mirror is written AFTER this independent observation. The helper does not skip equal-to-existing values.

Observation: `src/rigplane/runtime/_civ_rx.py:CivRuntime._observations_from_frame` separately decodes CI-V 0x14/0x09 and 0x14/0x0C into Hz/WPM observations. Its generic directed-frame `command_response` label alone is not the basis for clearing write authority; the explicit getter/match route above is independently present.

Observation: `StateStore._apply_one` increments observation sequence and replaces field timestamp/source on an accepted same-value observation, without requiring semantic revision advancement. The public snapshot emits those field markers; `DeltaEncoder.encode` compares the metadata object and includes observation sequence. Both frontend transport and radio store accept observation advancement at an unchanged semantic revision. `tests/test_web_server_coverage.py:test_same_value_observation_metadata_change_emits_web_delta` and the WS test named `accepts same-value fieldStatus metadata when only observationSeq advances` exercise this delivery property with a frequency field, not a complete CW UI transaction. The CW readback tests separately cover matching/mismatching reads and generation changes.

Inference: same-value canonical observation delivery is supported; a semantic-value deduplicator does not inherently starve these controls. That is not proof of every observation arriving after the frontend ACK or within its five-second deadline (F3). Icom codecs are provider-local and use exact displayed Hz/WPM comparisons; no tolerance is inferred from their internal 0-255 encoding.

### Yaesu: actual observation and actual cadence

Observation: `YaesuObservationAdapter.poll_tx_controls` reads `read_keyer_speed` and `read_cw_pitch`, guarded by CW capability and per-field pollability, and emits global observations. `read_cw_pitch` converts CAT index to `300 + idx * 10` Hz. `_emit_slow_control_observations` gathers the slow and TX-control reads, checks captured generation, and publishes their observations.

Observation: `YaesuCatRadio.create_observation_poller` uses `YaesuCatPoller` defaults. `_SLOW_INTERVAL` is one second; `_run_poll_cycle` takes the shared lock, awaits the full pass, THEN sleeps the interval. `_can_poll` checks capability, not elapsed field cadence. Thus the 30-second policies in `rigs/ftx1.toml:state_acquisition.field_policies` are not evidence that this loop waits 30 seconds between CW reads. Conversely, a one-second sleep is not a one-second end-to-end upper bound. Slow/TX reads are serially awaited, callbacks arrive after the combined pass, and lock contention/failure can delay them. The queued pitch write has the separate concrete unit defect in F1; Keyer Speed's queued setter and readback both use WPM.

### Current Filter Width and external-provider boundary

Observation: `FILTER_WIDTH_COMMAND_DESCRIPTOR` owns integer Hz targets, canonical `main.filterWidth`/`sub.filterWidth`, exact match, and receiver scope. Icom `RadioPoller._execute` writes width without a success mirror; `_request_post_write_readback` asks the acquisition scheduler for the active receiver's filter-width field. The CI-V observation path supplies canonical width. Yaesu's `YaesuObservationAdapter.poll_medium` separately obtains width through the provider read path.

Observation: the external Hamlib backend is `src/rigplane/backends/rigctld_client/`, not the internal `rigctld/` server. `RigctldClientObservationAdapter.read_mode` obtains `m` response mode/passband and emits canonical MAIN width; its acquisition profile explicitly sets `command_response_observable=False` for width. `RigctldClientRadio.get_mode` also writes a private compatibility mirror, but the observation adapter does not depend on treating that mirror as a command confirmation. `set_mode` can send mode/passband through `M`. The backend does NOT advertise CW, implement CW read/write paths, or expose a dedicated current `SetFilterWidth` dispatch arm in the inspected implementation. Width observation is not proof of that UI command's support. No blanket external-provider adoption is cleared.

## Step 3a: systematic enumeration verification

The corrected supplied inventory enumerates 62 modules: 32 production and 30 focused-test modules. The reproducer reads the pinned Git tree; Python uses AST definitions and assigned attributes, TypeScript/Svelte uses lexical declaration/arrow/method/assignment patterns. Reference scanning covers 657 production and 893 test paths, including all nine tracked TypeScript/Svelte files under `frontend/tests/`. It excludes worktree-only dependencies and build outputs; tracked generated source is not categorically excluded. The earlier unsupported aggregate and 141-zero claim are superseded.

| Emitted rows | Functions/methods/classes and arrows | Constants | Assigned attributes/bindings | Total |
| --- | ---: | ---: | ---: | ---: |
| Production | 2641 | 1048 | 1073 | 4762 |
| Focused tests | 3007 | 993 | 1684 | 5684 |
| Total | 5648 | 2041 | 2757 | 10446 |

Independent `jq` checks reconciled 10,446 unique row IDs, these class totals, zero inconsistent read/write sums, and zero non-null owner-attributed counts. The inventory reports no missing paths or Python parse errors. The generator, reference-scan correction, reproduction commands, schema, and representative rows (`snapToTable`, `hzToTableIndex`, `lastFilterWidthTransitionId`, `_confirm_global_operator_write`) were read. The full generator was not rerun and 10,446 rows were not individually adjudicated.

Limits matter: counts are leaf-token occurrences, not resolved receiver/import/alias ownership. Anonymous arrows, strings, template-string interpolation masking, reflection, dynamic calls, and lexical method classification prevent semantic completeness. Some rows are overlapping categories/sites, not distinct callable objects. The 3,435 zero production non-definition lower bounds are flags, NOT 3,435 dead symbols. The focused test corpus is enumeration coverage, not a claim that every test body was opened or passed. Outside that census, targeted reads of HBarRenderer, feedback presentation, SpectrumPanel, command_dispatch, the Yaesu parser, FTX-1 profile, direct FTX-1 API tests and the internal server's Yaesu CWPITCH caller resolved specific guards/contracts; they did not silently enlarge the exhaustive module census. The internal-server caller establishes liveness of the Yaesu Hz API only, never external Hamlib-provider support.

Deletion review was narrowed to a lexical local whose full component, whole-tree spelling search, visibility and dynamic-exposure guards can actually be established (D1). Public radio methods, legacy state mirrors and the still-consumed Filter Width compatibility accessor receive no deletion clearance. External sibling repositories were not scanned; D1's non-exported lexical scope, not an assumed absence of outside users, settles that guard.

## Step 4: steelman

The strongest case for the existing arrangement is substantial. Shared scalar lifetime and state-backed projection already exist. Raw ValueControl use is not a second gesture engine, and the legacy Filter Width lifecycle is already derived from the same descriptor. Its separate pending marker, confirmed-only preview and localized announcements satisfy an earlier bounded acceptance; replacing them with generic English output would be a regression, not consolidation. Native browser input and illuminated/discrete rendering may properly have different policies.

Filter table indices also have a legitimate purpose: nonuniform bandwidth choices occupy evenly spaced positions. Substituting a uniform Hz slider or stuffing Hz feedback into an index-domain control would silently change interaction. The strongest case for a new shared mapping primitive is that HBar consumes one numeric domain for geometry and canonical values and has no dedicated coordinate-transform prop. Against that, the current scalar input, request callback, normalization/step policy, formatter and cancellation surface already allow a bounded local representation boundary. No second independent owner of this particular table mapping has been established; a repository-wide mapper is not warranted merely by different units. A truthful treatment of unknown/off-table Hz still has to be proved.

Icom's compatibility mirror does not make confirmation fictitious: the matching getter and generation-bound observation exist independently. Yaesu similarly has genuine observations even though its write route and timing need separate scrutiny. External rigctld has genuine passband observation but no automatic CW or dedicated width-write parity. Provider generation is now owned by the accepted MOR-2398 foundation, not by each adopter.

The strongest defense of Yaesu's `set_key_pitch` queue arm is that it might receive an index or be bypassed by canonical command dispatch. That defense was tested against the actual Hz frontend envelope, legacy web enqueue, `command_descriptor`, and `canonicalize_level_command`: neither reroutes CW Pitch at the pin. The current queue arm is live. The backend already has a separate Hz-aware method, so this is not evidence for moving protocol conversion into the frontend.

These defenses clear the existing shared owners, protocol-local codecs, local geometry, and presentation policy. They do not clear the queued unit bypass, the loss of unknown/exact readings through nearest-index projection, or incomplete adoption of full feedback.

## Deletions

### D1 - FilterPanel.snapToTable: dead lexical helper

Verdict: dead.
Elements / definition site: `frontend/src/components-v2/panels/FilterPanel.svelte:snapToTable`.
Consumers: none, including the excluded preset editor.
Written / read: one definition binding, zero executable production calls/reads or reassignments, zero test reads/writes. The inventory row reports 1 production write/0 reads and 0/0 tests; independent `git grep -n -w snapToTable <audited-revision>` across the entire tracked tree returned only its definition. Full component inspection confirms no alias reference.
Guards checked: the function is inside the instance script, not exported, not a prop, not serialized or registered, and not exposed through eval/reflection. Literal dynamic-exposure searches and the full component were read. Sibling repositories were not searched, but external component consumers cannot reach this unexported local through the declared interface. No tests-only consumer exists.
Collateral: none. Keep the live `hzToTableIndex` and `tableIndexToHz`; no preset-editor behavior is being removed.
Prior ruling: none found for this helper.
In-flight: none found.
Required surface: none.
Depends on: none.
Confidence: high.
Falsifier: an executable lexical reference or an explicit export/exposure mechanism at the audited revision.
Fix class: delete.
Actionable: yes as an independent deletion candidate; this report grants no edit lease.

## Consolidations

### F1 - Yaesu queued CW Pitch bypasses the existing Hz boundary

Verdict: A - the queue route bypasses backend-owned semantic-to-native conversion; use the existing owner rather than another conversion.
Rank: diverged.
Elements: `src/rigplane/backends/yaesu_cat/poller.py:YaesuCatPoller._execute_command` (SetCwPitch arm); `src/rigplane/backends/yaesu_cat/radio.py:YaesuCatRadio.set_cw_pitch,set_key_pitch`; `src/rigplane/web/handlers/control.py:ControlHandler._execute_intent,_enqueue_rc_system`; `src/rigplane/runtime/_poller_types.py:canonicalize_level_command`.
Consumers: queued arm serves the web `set_cw_pitch` envelope from both scoped CW surfaces; the existing Hz method is the CwControlCapable provider API and is called by `src/rigplane/rigctld/routing.py:YaesuRouting.set_level` for CWPITCH. `tests/test_ftx1_radio.py:test_set_cw_pitch_accepts_hz,test_cw_pitch_round_trip_hz_idx` directly assert its 700 Hz -> KP40 contract and endpoints. These source tests were read, not run. The native index method is live both from that Hz method and the queue arm. Neither side is dead.
Definition site: Hz-to-index conversion is in `YaesuCatRadio.set_cw_pitch`; `set_key_pitch` only passes `idx` to `_write`.
Divergence: observation: frontend/web pass Hz unchanged into SetCwPitch; its queue arm calls `set_key_pitch(value)`. The general backend descriptor table contains no CW entry, and `canonicalize_level_command` only handles AF/RF/squelch. `set_cw_pitch(700)` maps to index 40; the queued 700 is passed as index 700. `rigs/ftx1.toml:commands.set_key_pitch` and `parser.py:format_command` would format the latter as `KP700;`, not `KP40;`. This wire example is a static inference, not a hardware trace. Radio response to that invalid input is unknown.
Prior ruling: MOR-1682 (2026-08-14) owns wider range/lattice correction; it does not authorize sending Hz as CAT indices. Its acceptance already names command/readback pins.
In-flight: the Hz-aware backend method exists. No correction of this route occurs in the inspected MOR-2398 dependency; MOR-1682 remains the related domain owner.
Required surface: exists: the provider's Hz-semantic CW method. Queue dispatch must preserve that semantic unit. Do not insert protocol index conversion into CW panels or disguise mismatches with tolerance.
Depends on: coordination with MOR-1682 for domain changes; no such expansion is necessary to identify the existing route defect. Provider-specific successful pitch adoption depends on this routing contract.
Confidence: high for the live bypass; hardware consequences untested.
Falsifier: a pinned conversion before this arm, or a production route proving the web envelope is an index rather than Hz. Both named interception points were opened and do not provide it.
Fix class: consolidate.
Actionable: yes as a concrete provider-routing prerequisite, not as permission to expand the CW UI domain or certify Yaesu pitch feedback.

### F2 - Current Filter Width coordinates are local; nearest-index truth is not

Verdict: C for table geometry/selection mapping; its current truth-loss behavior requires local correction. A missing general shared mapper is not established.
Rank: diverged.
Elements: `frontend/src/components-v2/panels/FilterPanel.svelte:hzToTableIndex,tableIndexToHz,formatWidthDisplay`; `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte:fillPercent,displayValue,handlePointerDown`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:ContinuousScalarInput,ContinuousScalarPolicy`.
Consumers: one current legacy table-width ValueControl; the mapping helpers also serve the excluded preset editor. HBar consumes the shared scalar lease. Native FilterSurface instead operates in Hz. No independent second implementation of this exact legacy table geometry was established.
Definition site: nearest-index conversion is local to FilterPanel; the scalar owner and HBar use their supplied numeric domain.
Divergence: observation: for a nonempty table `hzToTableIndex(NaN)` returns initialized index 0, because all distance comparisons fail. The raw binding therefore sees a finite known value, and its display maps back to the first width. Finite off-table readings are also replaced by the nearest entry. The non-table BW formatter preserves unknown instead. With the opened test table `[1800,2400,3000]`, a hypothetical observed 2500 becomes index 1 and displays 2400. These are static evaluations of source branches, not executed tests or claims about observed hardware frequency of the case.
Prior ruling: MOR-1409 A12 unknown/fabrication guard; existing confirmed-only table-preview tests in `FilterPanel.isolated.test.ts` and the 2026-08-14 Filter Width acceptance. Tests opened for unknown BW address the non-table `.bw-value` row; they do not establish honest unknown table mapping.
In-flight: shared numeric input/policy/request/formatter/cancel surfaces exist; no dedicated HBar coordinate-transform prop exists. An implementation of the required faithful table representation has not been verified.
Required surface: a bounded value/coordinate boundary preserving authoritative Hz separately from selection geometry: null/unknown cannot become index 0, requested/target/confirmed units cannot be mixed, and an off-table observed Hz must not become a nearest-table confirmed claim. Apply quantization to requested choices, not radio truth. Table/mode replacement must invalidate old gestures and deferred requests even when table cardinality stays equal. Preserve confirmed-only preview and actual table choices; do not turn this into a uniform Hz slider or infer correctness of every provider's table.
Depends on: none to establish or correct the local mapping defect. Full-feedback integration consumes the existing scalar cancellation and accepted provider fence; F5's table adoption depends on satisfying this requirement, not vice versa. Preset editor redesign remains excluded/nonblocking.
Confidence: high on local ownership and concrete truth loss; medium on sufficiency of an entirely local adapter with unchanged renderer geometry.
Falsifier: a pre-mapping finite/known guard or a faithful inverse preserving exact Hz at this pinned call site; or a bounded adopter proof showing the existing scalar surface cannot express the required local coordinate contract.
Fix class: none for mechanism relocation; local behavioral correction belongs in the bounded adoption contract. A generic design mandate is not cleared.
Actionable: yes for the local truth-preservation requirement; no for a speculative shared mapping framework or preset migration.

### F3 - Provider observation exists; a guaranteed post-ACK deadline remains unproved

Verdict: undetermined for unconditional in-window confirmation, not for existence of canonical observation.
Rank: diverged.
Elements / definition site: `commands.svelte.ts:transition,reconcileStateBackedCommands,DEFAULT_TIMEOUT_MS`; `RadioPoller._confirm_global_operator_write`; `YaesuCatPoller._run_poll_cycle,_emit_slow_control_observations`; `YaesuObservationAdapter.poll_tx_controls` at their full paths above.
Consumers: the shared reconciler currently serves two registered controls and is the intended CW target. Icom's helper serves Pitch/Speed/Break-in. Yaesu's slow observation pass serves CW among other controls.
Divergence: observation: ACK captures the per-field timestamp, and confirmation requires a strictly greater marker plus exact value. If no boundary exists, the first qualifying observation establishes it rather than confirming. The Icom readback is real, but queue ACK and state delivery are independent asynchronous paths. Yaesu's nominal one-second sleep follows a locked, awaited multi-read pass; it is not a five-second worst-case proof. Inference: a matching value or advancing global sequence alone must not settle an old/other-field command. Equal backend timestamps may be accepted yet fail the deliberately strict frontend boundary.
Prior ruling: MOR-1641, 2026-08-14: acceptance is not confirmation. MOR-1682's off-lattice limitation forbids inferring exact echo of every UI pitch request.
In-flight: accepted MOR-2398 solves provider identity, not marker ordering or cadence. Existing same-value transport and Icom matching-readback tests establish parts of the path, not the complete CW mounted transaction.
Required surface: exists for conservative confirmation and bounded timeout. Before claiming provider-specific success, establish the mounted command -> ACK -> later matching same-field marker sequence, including unchanged values and missing initial markers. Preserve truthful timeout/mismatch behavior; do not add tolerance, widen deadlines, or redesign acquisition from this audit alone.
Depends on: F1 for Yaesu pitch routing; F4 for registered CW reconciliation and consumer adoption; the accepted provider fence for generation continuity.
Confidence: high that the evidence does not establish a hard deadline; actual success/failure rates unknown.
Falsifier: a deterministic provider-path proof covering ordering and the deadline under the claimed conditions, including the actual serial read pass and unchanged-value delivery.
Fix class: none pending that evidence; no missing lifetime primitive established.
Actionable: no unconditional provider/cadence certification. This does not prevent implementing honest descriptor adoption with bounded outcomes under a separate lease.

### F4 - CW full feedback: missing registrations and consumers, not a missing scalar engine

Verdict: already shared; migration incomplete at the descriptor/accessor and surface boundaries.
Rank: displaced, by debugging impact of unadopted shared truth rather than duplicate renderer engines.
Elements: `frontend/src/lib/stores/commands.svelte.ts:STATE_BACKED_COMMAND_DESCRIPTORS`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:projectControlFeedback`; `frontend/src/components-v2/panels/CwPanel.svelte:cwPitch,keySpeed`; `frontend/src/semantic/CwKeyerSurface.svelte:CW_LEVELS,setLevel`.
Consumers: two existing descriptor registrations serve Filter Width/Break-in Delay. The two CW parameters each have a legacy ValueControl site and a native range site, reaching the mount sets in Step 3. Both already submit tracked intents. Neither has a full-feedback accessor/registration at this pin.
Definition site: lifecycle, descriptor registry and pure projector live in shared runtime modules; draft/input lifetime lives in `primitives/scalar/continuous-scalar.svelte.ts:createContinuousScalar`.
Divergence: observation: CW command records can acknowledge/time out, but have no state-backed descriptor reconciliation. Raw ValueControl adapts readings, not command evidence; native CW callbacks do not use a continuous binding. Break-in Delay adoption in the same files does not close either gap. Inference: registering and consuming the existing contract is sufficient architectural direction; this is not justification for another command store, provider mirror, or scalar lifetime.
Prior ruling: MOR-1641 and the 2026-09-06 scalar audit's separation of shared lifetime from local input policy; MOR-1682 preserves the separately owned domain limitation.
In-flight: existing `StateBackedCommandDescriptor`, `projectControlFeedback`, `createContinuousScalar`, native-range/HBar/discrete policies and ValueControl external binding. MOR-2398 is now accepted, not to be rebuilt.
Required surface: two explicit global command descriptors/accessors with exact envelope shapes (`value` Hz and `speed` WPM), canonical top-level `cwPitch`/`keySpeed`, strict observation qualification and exact semantic matching. Global scope must not follow the active receiver; Break-in Delay's stable receiver-0 convention demonstrates representability without inventing MAIN ownership of these global fields. Each adopted surface must consume full feedback, preserve unknown/capability gates and retain provider/session identity through the scalar input. Native and legacy renderers retain their declared policies and units, including no unreviewed range expansion.
Depends on: accepted MOR-2398; F1/F3 before provider-specific successful-confirmation claims. No dependency on RF/SQL or preset editing.
Confidence: high.
Falsifier: a registered equivalent CW descriptor/accessor already consumed by these precise sites, or a demonstrated global-scope requirement that the existing descriptor contract cannot express.
Fix class: consolidate, using registrations/adoption rather than a new foundation.
Actionable: yes as a bounded adoption direction; file leases, discriminating tests, and acceptance remain separate.

### F5 - Legacy current Filter Width: adopt the existing full feedback owner

Verdict: A for remaining panel-owned transition/announcement bookkeeping; already shared for confirmation and scalar lifetime. Migration incomplete, not a missing Filter Width descriptor.
Rank: displaced.
Elements: `frontend/src/components-v2/panels/FilterPanel.svelte:filterWidthLifecycle,lastFilterWidthTransitionId,filterWidthLiveStatus,lifecycleTarget`; `frontend/src/lib/runtime/adapters/panel-adapters.ts:getFilterWidthControlFeedback,getFilterWidthCommandLifecycle`; `frontend/src/primitives/control-feedback/control-feedback-presentation.ts:projectControlFeedbackPresentation`; `frontend/src/primitives/scalar/continuous-scalar.svelte.ts:createContinuousScalar`.
Consumers: local bookkeeping serves both current-width layouts; existing full-feedback scalar serves native FilterSurface. The compatibility accessor still serves SpectrumPanel as well as FilterPanel. Current table control uses raw reading evidence, and non-table current width is a read-only row, not another missing slider.
Definition site: matching confirmation remains in the command store, full DTO in the adapter, feedback transition memory in the shared scalar/presentation owner. The legacy panel keeps its own last-transition token and status switch.
Divergence: observation: full feedback carries availability, requested/target/confirmed, identity, phase and outcomes; the legacy wrapper narrows it and the panel independently retains announcements while reading canonical display props. At the separate accepted dependency, full feedback additionally carries provider generation. Inference: current-width adoption should converge on that owner, including lifetime invalidation, without promoting pending into canonical or maintaining two announcement emitters for the same control.
Prior ruling: MOR-1643's 2026-08-14 visible/accessibility acceptance; existing table tests explicitly preserve confirmed-only visuals; MOR-1409 A12 protects unknown. Localized wording, units and choice geometry are legitimate presentation, not dead behavior.
In-flight: descriptor, full accessor, scalar owner and native FilterSurface adoption already exist. MOR-2398 supplies the accepted provider boundary.
Required surface: exists, subject to F2's table-coordinate correctness. Carry the actual active receiver's full feedback into the current control; preserve the full DTO's canonical reading and identity, localized status and confirmed-only preview. Use one lifetime/announcement owner. For the non-table read-only row, consume truthful feedback presentation without inventing editability. Renderer attachment belongs to the renderer; owning a binding is not a second renderer lease. Keep SpectrumPanel's compatibility consumer and excluded preset behavior intact.
Depends on: F2 for the table branch; accepted MOR-2398. D1 is independent and does not unblock feedback. Preset editor is not a prerequisite.
Confidence: high on existing ownership and remaining adoption; medium on exact local table representation until proved.
Falsifier: the pinned current-width sites already consume the full feedback scalar, or their local transition machinery provides an indispensable semantic capability absent from the shared presentation owner rather than merely localized copy.
Fix class: consolidate.
Actionable: yes for current-width adoption; no deletion of the compatibility accessor, no forced native/legacy input-policy unification, and no preset-editor lease.

## Weakest link

F2's conclusion that a bounded local coordinate boundary can avoid a new shared interface is the least certain architectural judgement. Check it first with a nonuniform table, unknown and off-table canonical Hz, distinct requested/target Hz, and a mode/table change with unchanged cardinality during an active/deferred gesture. The existing API has numeric input/policy/cancellation flexibility, but no dedicated HBar coordinate-transform hook; source inspection is not a completed adopter proof. Failure to preserve both truthful Hz and established geometry would justify reopening only that precise surface requirement, not inventing a general mapper by default.

## Cleared

- Existing continuous scalar lifetime, explicit reading-versus-command evidence, renderer lease ownership, and distinct HBar/discrete/native policies. Raw use is incomplete feedback adoption, not a second scalar engine.
- Existing Filter Width descriptor, exact receiver-scoped Hz matching, full feedback accessor and native FilterSurface adoption.
- Independent Icom matching CW getter/readback observations and same-value StateStore-to-delta metadata delivery. Compatibility mirrors do not invalidate that route and receive no deletion clearance.
- Actual Yaesu global CW observations and WPM read/write units; neither nominal cadence nor observed pitch capability clears F1 or certifies an unconditional deadline.
- External rigctld passband observation through the client backend's `m` read, without pretending internal-server behavior proves external CW or dedicated width-write support.
- Accepted MOR-2398 provider-generation fence as a resolved dependency, kept separate from the audited pin.
- Local table geometry, localized formatting, honest unknown placeholders, and recorded CW domain limitations. No broader mapper, tolerance, provider parity, preset migration, or parent completion is authorized.
