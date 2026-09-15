# Mechanism audit: finite-choice instruments

Audited revision: `f2e7969708d5c93890cf1b24d833ce820b36dc15`, 2026-09-06.

Method: `.claude/skills/mechanism-audit/SKILL.md`, read in full from the audited repository, and `.claude/agents/auditor.md`. Repository guidance and live owner acceptance were read. This is the bounded finite-choice decision tract: shared behavior plus antenna, band and RX-audio choices. It is not a final integrated whole-path audit. Inspection used immutable Git objects. No audited-tree edits, Git writes, tests, builds, installs, browser work, service changes or hardware operations occurred. Publication remains a separate reviewed operation.

## Steps 0-3a: evidence before judgement

### Definitions and consumers

| Capability | Actual definition site | Per-implementation consumers |
| --- | --- | --- |
| Known-field choice binding | `frontend/src/primitives/control-instruments/control-instrument-behavior.ts:bindChoiceInstrument,canInvoke` | `frontend/src/semantic/CwKeyerSurface.svelte:apfChoice`; `DspSurface.svelte:notchBehavior,agcBehavior`; `FilterSurface.svelte` choice factories; `RfFrontEndSurface.svelte:preampBehavior,attenuatorBehavior`; `ScopeControlsSurface.svelte` choice factory. Paths abbreviated in this cell are under `frontend/src/semantic/`. Direct behavior/render tests also consume it. |
| Offered membership and selection | Same primitive: closure-local `offered`, returned `selected,isSelected,invoke` | The binding instances above. These are actual definitions, not imports. |
| Antenna port and RX-ANT | `frontend/src/semantic/AntennaSurface.svelte:ANTENNA_PORTS,selectPort,toggleRxAnt,antennaSwitchBlocks,tunerIdle,valueOf` | Port and RX-ANT controls in its template; production antenna snippet in `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte`; surface and wiring tests. |
| Band choice | `frontend/src/semantic/BandSurface.svelte:isCurrent,selectBand,receiverKnown` | Capability-derived `bandChoices` buttons; production band snippet in SemanticRadioSurfaces; surface and wiring tests. Frequency entry shares the module but is not a finite-choice implementation. |
| Audio monitor and routing | `frontend/src/semantic/RxAudioSurface.svelte:MONITOR_MODES,FOCUS_CHOICES,SPLIT_CHOICES,isValue,liveOffered,linkLost` | Monitor/focus/split controls and loss annotation; production RX-audio snippet in SemanticRadioSurfaces; surface and wiring tests. |
| MOD-input selection | Same RX-audio surface: `modInputValue,modInputUsable,changeModInput` | Native select in its template and existing `mode.onModInputChange` callback. Vocabulary is imported from `frontend/src/lib/radio/mod-input.ts:MOD_INPUT_SOURCES`, not redefined. |
| Command and audio execution | `frontend/src/lib/runtime/commands/panel-commands.ts:makeAntennaHandlers,makeBandHandlers,makeModeHandlers,makeRxAudioHandlers,makeAudioRoutingHandlers` | Existing runtime adapter bindings supplied to SemanticRadioSurfaces and other panel consumers. These are downstream owners, not extra semantic-surface implementations. |

Observation: all four scoped implementation modules were independently read, including complete semantic scripts/templates. Existing binding imports/calls, production mounts, relevant wiring callbacks, downstream handlers and targeted test bodies were inspected. The collection supplies the broader test and adapter census. Other adopted semantic families were checked as consumers, not independently audited in full.

### Dated rulings and accepted target

- `docs/plans/2026-07-25-ui-composition-architecture-v3.md` (2026-07-25): "Adapters are pure and depend only on runtime/domain contracts." Semantic presentation does not gain transport or truth ownership.
- `frontend/src/semantic/AntennaSurface.svelte:selectPort` and its comments, introduced in commit `9da14cc25` (2026-08-06), preserve absolute port selection without observing the current TX port. `antennaSwitchBlocks` limits RF blockers and adds the ATU constraint. `docs/validation/desktop-v2-v3-parity.md:P11` and antenna tests preserve that narrowed gate.
- `frontend/src/semantic/BandSurface.svelte:selectBand` and comments, introduced in commit `6c2a99364` (2026-08-06), treat TX permit as information for tuning, not permission to tune. The parity document's active-receiver correction and actual wiring preserve the independent known-receiver gate.
- `frontend/src/semantic/RxAudioSurface.svelte:linkLost`, commit `cacd12d4e` (2026-08-06), and `docs/validation/desktop-v2-v3-parity.md:P6` require the loss annotation only when live is structurally offered, selected, and nonoperational. The test preserves live as selectable while the link is down because selecting it opens the link.
- `docs/plans/2026-04-12-target-frontend-architecture.md:Addendum 2026-09-02` places common instruments in primitives and treats appearance as presentation. Live acceptance read on 2026-09-06 requires stable semantic behavior with replaceable geometry, permits distinct internal algorithms and forbids new truth/confirmation/transport owners.
- The accepted target already exists: `bindChoiceInstrument` has five production semantic-family consumers at the pin. RIT/scan/CW adoption is landed; RIT uses action/toggle bindings, not a choice binding. Older tracking prose calling that adoption unfinished does not supersede the landed source. Antenna, band and RX audio are not consumers at this pin. Prior remaining-adoption design notes are candidate explanations, not accepted decisions.

### Systematic sweep and limits

The collection's per-name inventory contains 64 rows: 5 shared-behavior functions, 16 antenna names, 27 band names and 16 RX-audio names. It separates source and test lexical occurrences and includes module constants, exported helpers, local handlers and Svelte state. Its declaration/manual-assignment column is not a semantic write count; its project-wide token counts include comments and same-name collisions. These are not 64 independently resolved read/write proofs.

Independent declaration enumeration of the full pinned modules supplements that inventory:

| Module | Named function declarations | const/let/var declaration lines |
| --- | ---: | ---: |
| control-instrument-behavior.ts | 3 | 7 |
| AntennaSurface.svelte | 4 | 15 |
| BandSurface.svelte | 8 | 28 |
| RxAudioSurface.svelte | 2 | 17 |

Arrow-valued helpers occur in the declaration-line column. Destructured props are one declaration line, not one binding. The shared module additionally implements 12 object getters/methods across its returned action/toggle/choice objects; their consumers are property accesses through the behavior interfaces, not necessarily same-name direct calls. Type-only interfaces/signatures are not executable methods.

The original 64-row inventory omits ordinary nested locals and does not separately enumerate returned object methods. Full-source inspection checked those omissions: antenna `atu,blocks`; band `text,value,asKhz,match,whole,frac,hz,hit,key`; RX-audio `value,source`; and shared binding `input,field` locals and behavior methods. They all feed return values, guards, calls or templates in their respective lexical scopes. The props are read by handlers/markup. Band entry state is read by parsing/readiness/hints and is assigned by input/cancel; it is not dead merely because frequency entry is excluded from the choice comparison.

One important counterexample to lexical counting is antenna `sequence`: its same-line `++sequence` is a read/write feeding the live `blockedId`; one project token line does not make it dead. Several helpers the inventory casually labels private, including antenna `valueOf` and semantic `usable` exports, are actually exported from module scripts. They cannot be declared dead from an absent external literal match.

No dead or constant-guard-unreachable candidate was established. Literal checks include dynamic indexed label maps, `ANTENNA_PORT_INTENT[port]`, dynamically chosen MOD-input command names and the local-extension boundary. Public imports and unavailable downstream/private consumers remain guards, not evidence of absence. Tests are positive consumers; no tests-only implementation was proposed for deletion. This is a bounded lexical/manual sweep, not complete binding-resolved data flow or clearance of every public API.

### Observed policy and execution paths

Observation: `control-instrument-behavior.ts:canInvoke` requires a present, structural, operational, known field and no blocker. `bindChoiceInstrument.selected` additionally requires current-value membership; `invoke` requires target membership. A known but unoffered current value yields no selection yet does not itself prevent invocation of an offered target. Optional `feedback` is forwarded unchanged; the binding owns no command lifecycle or confirmation store.

Observation: antenna ports are the two command-addressable values `[1,2]`, not an enumeration from arbitrary `antennaCount`. `selectPort` requires the antenna group and no RF/ATU blockers, not a known current TX port. `toggleRxAnt` additionally needs usable observed RX-ANT because it inverts a current value. Downstream `makeAntennaHandlers.onSelectAnt1/2` still requires at least two antennas and the corresponding observed boolean RX override. Thus unknown current TX port is allowed at the surface, but an entirely unobserved antenna state does not guarantee a radio command. The existing indexed wiring maps the offered port to the shipped command handler.

Observation: `BandSurface.selectBand` requires known active receiver, not known current band or allowed TX permit. `SemanticRadioSurfaces.selectBand` repeats the receiver guard. BSR choices use `band.onBandSelect`; bandless choices use the existing per-receiver `tuneFrequency(..., 'jump')`, bypassing the old MAIN-only fallback. `isCurrent` returns false for unread current band, and the pinned surface test explicitly expects `aria-pressed="false"` on every offered choice in that state. The separate current-band readout remains unknown.

Observation: RX focus and split are absolute choices available while their current preference is unread or degraded. Monitor `live` is omitted only if `liveAudio.structural` is false; link-down does not disable selection. `makeAudioRoutingHandlers` updates the audio manager and saved preferences. `makeRxAudioHandlers.onMonitorModeChange` owns live/mute runtime behavior and can issue AF changes/restoration. It is therefore neither a single radio-confirmed field nor entirely free of radio side effects.

Observation: RX MOD-input requires `usable(rx.modInputSource)` and a recognized current source. `changeModInput` resolves the offered target through `MOD_INPUT_SOURCES`, restores the DOM select to the observed value, and only then emits if usable. Tests cover unknown, degraded and unrecognized current sources. `makeModeHandlers.onModInputChange` independently obtains known active DATA group, validates it and selects the existing group-specific command. That command routing belongs below the renderer.

## Step 4: steelman

The strongest case for the existing arrangement is that these controls do not have the same authorization rule. A relative toggle cannot invert an unknown bit. An absolute antenna port can be chosen without observing the current port, but relay safety and preservation of the relevant RX override still matter. A band change needs an honest receiver target, not TX permission. Restoring an unread local routing preference is useful and does not pretend that its old value was known. A live-audio action must remain usable while the link is down. Applying the current known-field gate uniformly would remove intended behavior.

The shared binding already solves the known-field form and owns offered membership and current selection. Its real adopters disprove a claim that all choice behavior lacks a shared home. The optional blocker can express extra family restrictions, and feedback forwarding is not another truth store. The three remaining surfaces contain little gesture machinery: some duplication is simple markup and callback wiring, not competing complex state machines.

Canonical selection is distinct from requested target. No offered choice selected while the current value is unread is not a relative toggle reporting a known false state. The band test deliberately asserts false per-button selection alongside an unknown field readout. It must not be silently rewritten on the assumption that every boolean ARIA attribute means a known boolean radio fact.

These arguments clear the family gates, command handlers, local preference domain and rendering details. They do not supply a common choice interface capable of representing independent observed selection and absolute invocation eligibility. The accepted replaceable-instrument goal makes that narrower coverage gap actionable; it does not select a helper name, internal topology or universal policy.

## Deletions

None established. No zero-consumer or vestigial-fork verdict is supported by this tract. Exported helpers, dynamic maps, binding methods and frequency-entry neighbors remain live or externally guarded. Do not turn the lexical inventory into a deletion list.

## Consolidations

### F1 - Absolute choices need independent observation and invocation semantics

Verdict: B - missing shared-interface coverage, not evidence for one universal choice policy.
Rank: displaced.
Elements / definition sites: `frontend/src/primitives/control-instruments/control-instrument-behavior.ts:ChoiceInput,ChoiceInstrumentBehavior,canInvoke,bindChoiceInstrument`; `frontend/src/semantic/AntennaSurface.svelte:selectPort,currentPort`; `frontend/src/semantic/BandSurface.svelte:selectBand,isCurrent`; `frontend/src/semantic/RxAudioSurface.svelte:isValue,liveOffered` and monitor/focus/split callbacks.
Consumers: shared binding serves the five semantic families enumerated above. Antenna implementation serves port buttons; band implementation serves capability-derived band buttons; RX implementation serves monitor/focus/split controls. All three remaining surfaces have production mounts and test consumers, not abandoned twins.
Divergence: observation: known-field invocation is inseparable from current reading in the existing binding, while these absolute choices intentionally permit some unread states and use different eligibility facts. Inference: reusing that interface unchanged either rejects allowed actions or requires fabricated field evidence; neither satisfies truthful replaceable instruments. The current family differences themselves are not a regression.
Prior ruling: 2026-08-06 antenna/band/audio decisions and parity entries above; 2026-09-02 primitive ownership addendum; 2026-09-06 accepted semantic-interface goal.
In-flight: bindChoiceInstrument already exists with real adopters. No scoped absolute-choice adoption exists at the pin; unaccepted design notes do not establish an accepted replacement.
Required surface: a finite offered vocabulary and target membership, canonical selected/unknown evidence independent from invocation eligibility, fresh input evaluation, supplied family blockers/eligibility and existing callbacks, plus preservation of supplied feedback without manufacturing confirmation. It must support existing known-only adopters and these genuinely absolute consumers without weakening relative-toggle semantics or changing runtime command gates. Distinct internal strategies remain permitted; no new owner/store is required by this finding.
Depends on: none to adjudicate the missing surface; adoption depends on accepting that surface and preserving F3/F4 policies.
Confidence: high on the interface mismatch; medium on the eventual breadth of a common interface.
Falsifier: an existing supported common choice interface at this pin that represents unknown selection and independently permitted invocation without synthetic known fields, or a superseding accepted decision withdrawing those absolute behaviors.
Fix class: design.
Actionable: yes for the bounded interface gap and subsequent adoption; not authorization for a universal gate, new state machine or command vocabulary.

### F2 - MOD-input can adopt the existing known-field behavior with its explicit blocker

Verdict: A - incomplete adoption of an existing shared capability; no new primitive required for this element.
Rank: parallel.
Elements / definition sites: `frontend/src/semantic/RxAudioSurface.svelte:modInputValue,modInputUsable,changeModInput`; `frontend/src/primitives/control-instruments/control-instrument-behavior.ts:bindChoiceInstrument,canInvoke`; `frontend/src/lib/radio/mod-input.ts:MOD_INPUT_SOURCES`.
Consumers: local selection/guard serves the RX native select; shared known-field guard/membership serves the five existing semantic families; MOD_INPUT_SOURCES serves this select and its existing radio-domain consumers.
Divergence: observation: MOD-input additionally forbids invocation when the known current value is outside the vocabulary. The shared binding's selected value becomes undefined in that situation but its available/invoke gate remains open. Inference: the existing explicit blocker can preserve that extra restriction; merely passing the field and choices would regress it. String-to-vocabulary decoding and DOM restoration remain renderer adaptation.
Prior ruling: known/current and requested target remain separate under the live 2026-09-06 acceptance; the 2026-09-02 addendum locates reusable instrument behavior in primitives. Source/tests pin MOD-input's unknown/degraded/unrecognized restrictions; no ruling was found requiring a private membership mechanism.
In-flight: bindChoiceInstrument is the existing target with production consumers. RX MOD-input has not adopted it at the pin.
Required surface: exists, including optional blocked and offered membership. Preserve the recognized-current-source blocker, canonical selected state, DOM reset, current DATA-group command routing and existing remedy callback. No generic command selection inside the primitive.
Depends on: none; does not need F1's absolute-choice coverage decision.
Confidence: high on capability fit; medium on the marginal value of adopting such a small client.
Falsifier: a tested MOD-input interaction or observation requirement that cannot be represented by current field/choices/blocked/callback semantics without changing its behavior.
Fix class: consolidate.
Actionable: yes as narrow adoption under the accepted instrument goal; no standalone abstraction expansion is justified.

### F3 - Safety, receiver identity and audio availability are legitimate family policies

Verdict: C - correctly located domain policy supplied around common interaction.
Rank: diverged (intentional policy variation, not a divergence defect).
Elements / definition sites: `frontend/src/semantic/AntennaSurface.svelte:antennaSwitchBlocks,tunerIdle,toggleRxAnt`; `frontend/src/semantic/BandSurface.svelte:receiverKnown,selectBand`; `frontend/src/semantic/RxAudioSurface.svelte:liveOffered,linkLost`; `frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte:selectBand,tuneFrequency`; `frontend/src/lib/runtime/commands/panel-commands.ts:makeAntennaHandlers,makeModeHandlers,makeRxAudioHandlers,makeAudioRoutingHandlers`.
Consumers: antenna controls consume RF/ATU restrictions; band buttons and wiring consume known receiver; monitor UI consumes structural offering/link state; existing runtime handlers consume these callbacks and perform their own validated command/audio operations. Relative RX-ANT has its separate known-bit consumer.
Divergence: observation: relay switching requires RF/ATU conditions, tuning does not require TX permit, live selection can initiate a down link, routing writes absolute preferences, and MOD command selection depends on active DATA group. Inference: collapsing these into known/operational-current or TX-permitted gates would change accepted behavior.
Prior ruling: dated antenna/band commits and audio parity record from 2026-08-06; pure-adapter architecture on 2026-07-25; current accepted no-new-transport/truth-owner constraint.
In-flight: shared keyBlockedReasons and existing runtime handlers already exist; common choice behavior must consume the supplied outcomes, not replace those authorities.
Required surface: preserve these policies as real semantic/runtime inputs and callbacks; shared interaction needs no knowledge of relay commands, BSR fallback, DATA vocabulary, audio storage or connection opening.
Depends on: none. F1/F2 must preserve these constraints.
Confidence: high.
Falsifier: a newer accepted domain contract requiring the same authorization semantics across these families, supported by corresponding command and observation changes.
Fix class: none.
Actionable: no consolidation of the policies themselves; preservation is required during adoption.

### F4 - Choice selection is not a boolean observation, and feedback forwarding is not confirmation

Verdict: C for canonical per-choice selection and distinct audio preference ownership; undetermined for each family's missing end-to-end feedback projection.
Rank: diverged (different evidence domains, not a proven duplicate lifecycle).
Elements / definition sites: `frontend/src/semantic/BandSurface.svelte:isCurrent`; `frontend/src/semantic/AntennaSurface.svelte:currentPort`; `frontend/src/semantic/RxAudioSurface.svelte:isValue,modInputValue`; `frontend/src/primitives/control-instruments/control-instrument-behavior.ts:ChoiceInstrumentBehavior.feedback,bindChoiceInstrument.selected`; `frontend/src/lib/runtime/commands/panel-commands.ts:makeRxAudioHandlers,makeAudioRoutingHandlers,makeModeHandlers`.
Consumers: the respective buttons/select consume canonical or preference state; binding users receive optional caller-supplied feedback; radio-command execution and audio preference/runtime handlers serve their own downstream consumers. The three remaining surfaces do not currently consume a ControlFeedback binding/envelope.
Divergence: observation: unread band/antenna/routing gives no selected offered option, and band tests assert false per-button aria-pressed. Requested target is not substituted. Radio commands and audio preferences have different confirmation authorities; monitor may additionally have AF side effects. Inference: absence of an envelope in these surfaces is not proof of an absent command tracker, nor permission to present preference writes as radio confirmation.
Prior ruling: 2026-09-06 acceptance requires canonical truth and requested target to remain distinct; dated band/antenna/audio behavior above. No scoped accepted feedback descriptor contract was found for all of these families.
In-flight: feedback pass-through already exists in the shared binding; downstream command tracking already exists. Passing a generic feedback type does not implement field confirmation or prove adoption.
Required surface: preserve observed-selection knowledge independently from requested target and lifecycle. Where real field-specific feedback is supplied, carry it through unchanged and expose it separately. Before authorizing new feedback projection, establish the actual field identity, observation matching and authority for antenna/band/MOD and the distinct local/composite audio cases. No invented confirmation floor, truth store or merged audio/radio lifecycle.
Depends on: field-specific observation/command contracts for any new feedback adoption; selection preservation itself has no dependency.
Confidence: high on current source semantics; medium on completeness of the bounded feedback-contract evidence.
Falsifier: an existing scoped feedback projection/accepted descriptor proving these full paths already supply the required evidence, or an accepted selection contract superseding the pinned unknown-state tests.
Fix class: none pending evidence for additional feedback work.
Actionable: no blanket feedback architecture change from this tract; yes as a preservation constraint for choice adoption.

### F5 - Existing shared mechanism and renderer adaptation are not competing systems

Verdict: already shared for known-field eligibility, offered membership and feedback pass-through; C for finite markup, value decoding and readout formatting.
Rank: parallel.
Elements / definition sites: `frontend/src/primitives/control-instruments/control-instrument-behavior.ts:canInvoke,bindChoiceInstrument`; `frontend/src/lib/radio/mod-input.ts:MOD_INPUT_SOURCES`; `frontend/src/semantic/BandSurface.svelte:defaultPermitLabel,txDeniedReason,interpretFrequencyEntry`; `frontend/src/semantic/RxAudioSurface.svelte:changeModInput`; `frontend/src/semantic/AntennaSurface.svelte:ANTENNA_BLOCKED_LABEL`.
Consumers: shared eligibility serves action/toggle/choice bindings; choice membership serves its production consumers; MOD vocabulary serves RX and radio-domain code. Local formatting and parsing serve their own semantic templates/handlers; frequency entry is not another finite-choice owner.
Divergence: observation: select DOM restoration, label maps, permit hints and typed-frequency parsing differ from choice-button markup. Inference: common naming or nearby placement does not make these competing implementations of selection lifetime.
Prior ruling: 2026-07-25 pure presentation/adapters and 2026-09-02 primitive ownership; 2026-09-06 acceptance allows different geometry and internal strategies.
In-flight: these shared primitives and vocabulary are actual code at the pin; no replacement is warranted by their imports.
Required surface: exists for the shared parts; keep renderer-specific adaptation and informative labels local. F1 is the bounded missing semantic coverage, not a reason to extract every helper.
Depends on: none.
Confidence: high.
Falsifier: another live independent definition of the same domain vocabulary or eligibility mechanism, or a concrete second consumer requiring the supposedly renderer-local behavior unchanged.
Fix class: none.
Actionable: no; cleared except for the explicitly isolated F1/F2 adoption work.

## Weakest link

F4's feedback scope is the least settled conclusion. The source proves absent scoped envelope consumption and distinct runtime domains, not a complete census of every accepted field-specific feedback requirement. Check antenna/band/MOD observation identities and audio preference/composite completion contracts first. Do not convert that uncertainty into a new confirmation owner. F1's exact internal shape also remains deliberately undecided: the gap is proven, but neither one helper nor one algorithm is selected.

## Cleared

- Existing bindChoiceInstrument ownership, offered membership, current-value selection and stateless feedback pass-through.
- Production liveness of antenna, band and RX-audio surfaces and the already-landed choice adopters.
- Narrow antenna RF/ATU rules, observed RX-ANT inversion and existing downstream RX-override preservation.
- Known-receiver band routing, informational TX permit, BSR dispatch and per-receiver absolute jump fallback.
- Structurally offered live audio while disconnected, explicit link-loss annotation and absolute unread routing choices.
- Separate canonical selection and requested target, including the tested unread-band per-option false attributes.
- Existing MOD vocabulary/DATA command owner; local select decoding/reset and family-specific current-source blocker.
- Local labels, informative permit presentation and live frequency-entry neighbors. No deletion, universal choice gate, new truth store or integrated-product completion is authorized.
