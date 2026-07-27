# UI Composition Discovery and Parity Baseline

**Date:** 2026-07-25

**Status:** Accepted discovery baseline; target decisions accepted; implementation
remains partial

**Accepted:** MOR-972 completed 2026-07-26; target contract refined by
[MOR-982](https://linear.app/morozsm/issue/MOR-982/decision-freeze-the-app-owned-tx-controller-contract),
[MOR-986](https://linear.app/morozsm/issue/MOR-986/decision-define-backend-tx-ownership-stale-queue-and-session-loss),
and
[MOR-988](https://linear.app/morozsm/issue/MOR-988/decision-freeze-receiver-vfo-scope-tx-permit-and-refresh-semantics)
on 2026-07-26

**Linear item:** MOR-972

**Scope:** Current `frontend/` composition, capability, lifecycle, safety, and parity evidence

**Related accepted architecture:** `docs/plans/2026-07-25-ui-composition-architecture-v3.md`

## Purpose and evidence labels

This document records the current state discovered for MOR-972 and bounds the
evidence needed for the first VFO plus RX/TX reference vertical. It is not an
implementation specification. The v3 target was accepted after this discovery;
this baseline still identifies which premises were proven, partial, missing, or
unresolved at the recorded evidence date.

Parity means equivalent runtime behavior, capability semantics, command side
effects, safety behavior, and resource ownership. It does not require identical
DOM or visuals.

Evidence labels used below:

- **Automated** — a current automated test proves the stated narrow contract.
- **Manual** — browser or radio/hardware observation is still required.
- **Missing** — the required test or contract does not exist.
- **Unknown** — the frontend evidence cannot establish the behavior.

These labels describe the 2026-07-25 baseline and are not silently upgraded by
an open branch, PR, or Linear status. Current execution status is recorded
separately below; a requirement becomes proven here only when its specified
evidence lands.

## Accepted target decisions and implementation status

Three decision items closed the main contract questions exposed by discovery:

- [MOR-982](https://linear.app/morozsm/issue/MOR-982/decision-freeze-the-app-owned-tx-controller-contract)
  assigns one persistent browser-tab TX controller to `App.svelte`; layouts,
  MediaSession, keyboard, and future surfaces are input adapters, not TX owners.
- [MOR-986](https://linear.app/morozsm/issue/MOR-986/decision-define-backend-tx-ownership-stale-queue-and-session-loss)
  assigns backend TX ownership and durable de-key policy to one supervisor per
  managed radio target runtime, outside client/poller/provider lifetimes.
- [MOR-988](https://linear.app/morozsm/issue/MOR-988/decision-freeze-receiver-vfo-scope-tx-permit-and-refresh-semantics)
  keeps `/api/v1/capabilities` as the authoritative raw v1 document and defines
  pure fail-closed presentation semantics for topology, TX target, scope/audio
  services, browser TX eligibility, permit, and refresh.

Acceptance fixes the target contract; it does not mean the target is already
implemented. As of 2026-07-27, MOR-972 and the three decisions above are Done;
the MOR-973/MOR-974 foundation programs are In Progress; bounded safety,
capability, topology, and fixture slices are in review; MOR-983, MOR-984,
MOR-987, MOR-997, and MOR-998 remain downstream work.

Linear is the control plane for decisions, dependencies, priority, and status.
GitHub is the evidence plane for atomic implementation issues, commits, PRs,
checks, and independent review. The self-hosted runner restoration tracked in
[MOR-992](https://linear.app/morozsm/issue/MOR-992/restore-repo-scoped-self-hosted-ci-runner-for-rigplane-core)
may block CI execution evidence or merging; it is an operational blocker, not
architecture uncertainty.

## Executive findings

### Proven current state

1. `App.svelte` owns bootstrap, retry, cleanup wiring, bootstrap failure, and
   the local-extension host, but statically mounts `RadioLayout`; it does not
   resolve or lazy-load a presentation (`frontend/src/App.svelte:22-72`,
   `frontend/src/App.svelte:79-98`).
2. `RadioLayout` is the effective presentation composition root. It reads
   runtime and capability state, resolves the skin, creates handlers, chooses
   viewport branches, and owns desktop/global surfaces
   (`frontend/src/components-v2/layout/RadioLayout.svelte:59-145`,
   `frontend/src/components-v2/layout/RadioLayout.svelte:215-404`).
3. `MobileRadioLayout` is a second behavior/composition root. It derives its own
   models and handlers and owns presentation-local PTT state
   (`frontend/src/components-v2/layout/MobileRadioLayout.svelte:58-100`,
   `frontend/src/components-v2/layout/MobileRadioLayout.svelte:307-408`).
4. The skin registry contains stable IDs, resolution rules, and lazy loaders,
   but `loadSkin()` has no application caller
   (`frontend/src/skins/registry.ts:31-80`).
5. `GET /api/v1/capabilities` is the existing authoritative frontend feature
   payload. Runtime protocols and the selected radio profile are normalized
   before the server serializes it (`src/rigplane/web/runtime_helpers.py:479-517`,
   `src/rigplane/web/server.py:2725-2817`).
6. Capability policy is distributed across raw fields, tags, selectors,
   adapters, commands, and presentation components. Two state-to-props
   implementations coexist and already differ
   (`frontend/src/components-v2/wiring/state-adapter.ts:1-16`,
   `frontend/src/lib/runtime/props/panel-props.ts:1-24`).
7. Presentation mounts own or influence hardware scope, audio FFT scope, saved
   audio routing, global feedback subscriptions, and TX state. A presentation
   switch is therefore not resource-neutral.
8. Current TX safety is presentation- and input-path-dependent. Desktop and
   mobile active-TX state has no proven unmount de-key contract; MediaSession
   bypasses those state machines; degraded health can reject `ptt_off`
   (`frontend/src/components-v2/panels/TxPanel.svelte:56-118`,
   `frontend/src/lib/media/media-session.ts:87-96`,
   `frontend/src/lib/transport/ws-client.ts:431-443`).

### Accepted target architecture, not current implementation

The accepted v3 target makes `App.svelte` the composition root, keeps capabilities
authoritative, consolidates non-persisted selectors/view models, makes runtime
resources session-owned, renders global safety state outside layouts, and splits
layout from design language and theme. Discovery supports those boundaries but
does not prove they are implemented. In particular, TX safety and transport
continuity are prerequisites, not presentation refactoring details.

## Current composition ownership

| Concern                       | Current owner                                                   | Proven coupling or gap                                                       | Accepted v3 boundary                                               |
| ----------------------------- | --------------------------------------------------------------- | ---------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| Bootstrap and bootstrap error | `App.svelte`                                                    | Root-owned and already outside layouts                                       | Retain at app/session level                                        |
| Presentation resolution       | `RadioLayout.svelte`                                            | Reads viewport, preference, scope capability and branches directly           | App-level composition policy                                       |
| Lazy registry                 | `skins/registry.ts`                                             | Defined but inactive at the application seam                                 | Reuse only after internal double-resolution is removed             |
| Desktop composition           | `RadioLayout.svelte`                                            | Runtime reads, adapters, handlers, panels, settings, overlays                | Typed presentation context plus semantic surfaces                  |
| Mobile composition            | `MobileRadioLayout.svelte`                                      | Duplicates model/handler construction and owns TX/session UI behavior        | Responsive layout over the same persistent contracts               |
| LCD composition               | `LcdLayout.svelte`                                              | Reads runtime/capability state and duplicates Toast/power surfaces           | Renderer/layout only                                               |
| Capability gating             | Capability store plus layouts, panels, commands                 | Raw booleans, tags, metadata and live fields are mixed                       | One derived, non-persisted selector surface                        |
| Command behavior              | Old command bus, runtime panel commands, spectrum, MediaSession | At least four command paths with different safety/lifecycle behavior         | One typed semantic command boundary                                |
| Scope resources               | `SpectrumPanel` and subscriber-counted `scopeController`        | Mount/unmount can close or reopen sockets                                    | Session-owned resource, presentation subscribes to frames          |
| RX/TX audio                   | Singleton `audioManager`, but presentation restores config      | Mounting audio routing can open a socket                                     | Session-owned audio service                                        |
| TX safety                     | `TxPanel`, `MobileRadioLayout`, `PttFab`, MediaSession          | State, timers, preflight and cleanup differ by input/presentation            | One persistent authoritative TX state machine                      |
| Global feedback               | Parent and child Toast/power/status surfaces                    | Duplicate subscribers and presentation-dependent status                      | One root feedback/overlay host                                     |
| Keyboard/help                 | Mounted per selected layout                                     | Global listener and help surface remount                                     | Root action/navigation host                                        |
| Theme                         | Layout mount/module actions                                     | Theme can leak across in-session presentation changes                        | Composition-owned token application                                |
| Workspace-like state          | Layout, theme, sidebar order and extension dock keys            | Fragmented persistence without shared migration policy                       | Later versioned workspace with stable IDs                          |
| Local extensions              | `App.svelte` sibling host                                       | Correct root lifetime, but z-stack and raw capability API need compatibility | Keep app-level; integrate through explicit global/workspace policy |

Current dependency shape:

```text
App
  -> runtime bootstrap
  -> RadioLayout
       -> runtime + capability/layout stores
       -> state adapters + multiple command paths
       -> desktop | LCD | mobile | SDR branch
       -> scope/audio side effects
       -> TX and feedback surfaces
  -> LocalExtensionsHost
```

## Capability flow and contract freeze

### Current flow

```text
rig profile + backend runtime protocols
  -> runtime_capabilities()
  -> GET /api/v1/capabilities
  -> unchecked TypeScript cast
  -> singleton capability store
  -> selectors + raw reads + duplicated adapters/commands/presentations
```

The backend accepts `ab`, `main_sub`, `ab_shared`, and `single` VFO schemes
(`src/rigplane/profiles/rig_loader.py:43`,
`src/rigplane/profiles/rig_loader.py:1008-1015`). The frontend payload mirror
documents only part of that domain, is not runtime-validated, omits backend
`webrtc`, adds `hasLan` that the endpoint does not emit, and models nullable
`txBands` as non-nullable (`frontend/src/lib/types/capabilities.ts:79-112`,
`frontend/src/lib/transport/http-client.ts:113-117`).

### Known divergences

| Domain                  | Current divergence                                                                                                   | Evidence                                                                                                              |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| Receiver structure      | `receivers` is unused in production UI; `dual_rx` alone gates dual operation                                         | `frontend/src/lib/stores/capabilities.svelte.ts:39-41`                                                                |
| Receiver vs VFO slot    | Store separates identities, but `VfoPanel` maps MAIN/SUB to A/B                                                      | `frontend/src/lib/stores/capabilities.svelte.ts:95-115`; `frontend/src/components-v2/vfo/VfoPanel.svelte:41-43`       |
| VFO topology            | Backend has four schemes; presentation reduces non-`main_sub` behavior to A/B                                        | `frontend/src/components-v2/vfo/vfo-ops-utils.ts:3-23`                                                                |
| Scope                   | Hardware scope, audio FFT availability, selected source, and controller availability have different owners/semantics | `frontend/src/lib/stores/capabilities.svelte.ts:14-33`; `frontend/src/lib/runtime/scope-controller.svelte.ts:26-34`   |
| TX                      | `tx`, `voice_tx`, audio, permit bands and modulation-input support are not one semantic fact                         | `src/rigplane/web/server.py:2756-2817`                                                                                |
| Selector implementation | Flat booleans, tags, raw metadata, live fields and model-specific defaults are mixed                                 | `frontend/src/lib/stores/capabilities.svelte.ts:14-155`                                                               |
| State mapping           | Old and new prop mappers diverge in availability and TX behavior                                                     | `frontend/src/components-v2/wiring/state-adapter.ts:205-231`; `frontend/src/lib/runtime/props/panel-props.ts:200-250` |
| Refresh                 | Capabilities load once during idempotent bootstrap; no profile-change refresh contract exists                        | `frontend/src/lib/runtime/frontend-runtime.ts:128-187`                                                                |

### Contract-freeze table

| Contract question             | Freeze for work after MOR-972                                                                                            | Status                                         |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| Authoritative source          | Preserve `/api/v1/capabilities`; do not introduce a parallel capability protocol                                         | Accepted architectural constraint              |
| Presentation profile          | May be derived and non-persisted only; it must retain source semantics and live availability separately                  | Accepted architectural constraint              |
| Receiver count vs `dual_rx`   | Treat structural count and operational support as distinct until their disagreement behavior is documented               | Decision required in MOR-974                   |
| VFO scheme                    | Cover all four backend values; do not default unknown vendors to `main_sub` as a semantic decision                       | Decision and tests required in MOR-974         |
| Receiver vs slot identity     | Keep receiver selection separate from A/B slot operations and split TX target                                            | Decision and tests required in MOR-974/MOR-975 |
| Scope semantics               | Distinguish hardware availability, audio FFT availability, selected/default source, active resource and connected health | Decision required in MOR-974                   |
| TX semantics                  | Separate CAT PTT, browser voice TX, TX permit, audio and MOD-input support                                               | Decision required before MOR-975 TX acceptance |
| `txBands=null`                | Do not silently equate unknown with no bands or a US default in the reference contract                                   | Decision required before MOR-975               |
| Wire parity                   | Freeze nullability and full field set with a backend/frontend fixture or parser contract                                 | Missing evidence; MOR-974 gate                 |
| Capability refresh            | Until explicitly changed, assume capability identity is session/bootstrap-bound                                          | Current behavior; future policy unresolved     |
| Local-extension compatibility | Preserve the versioned raw capability exposure while internal selectors consolidate                                      | Compatibility constraint                       |

## Runtime, transport, command, and TX safety lifecycle

| Finding                   | Proven current behavior                                                                      | v3 invariant affected                                               | Evidence state                                                                 |
| ------------------------- | -------------------------------------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| Root cleanup              | App bootstrap cleanup stops polling but does not close all session resources                 | Runtime/session ownership must be explicit                          | **Automated** narrow bootstrap lifecycle; complete teardown policy **Missing** |
| Named WS registry         | Disconnected named channels remain registered and `reconnectAll()` can reconnect all entries | Only currently owned resources may reconnect                        | **Automated** generic reconnect; stale-resource case **Missing**               |
| Hardware scope            | `SpectrumPanel` connects and disconnects the shared named channel on mount/unmount           | Presentation switching must not reconnect scope                     | **Automated** mount connect; shared/switch continuity **Missing**              |
| Audio FFT scope           | First subscriber connects and last unsubscribe disconnects                                   | Presentation subscriber count must not own transport lifetime       | **Automated** current mount-sensitive behavior                                 |
| Audio routing             | Restoring saved focus/split from a mounted panel can open `/api/v1/audio`                    | Mounting presentation must not create session transport             | Current code path proven; prevention test **Missing**                          |
| Command boundary          | Old bus, runtime commands, spectrum direct calls and MediaSession direct PTT coexist         | Semantic behavior must have one command boundary                    | Proven by imports/call sites; equivalence **Missing**                          |
| Desktop/mobile TX unmount | Active TX state and timers are presentation-local with no proven destroy de-key              | Layout unmount must not compromise TX safety                        | Safety cleanup **Missing**                                                     |
| Health gate               | Generic degraded-health gate can reject `ptt_off`                                            | Emergency de-key is prioritized, idempotent and best-effort         | Current rejection **Automated**; safe replacement **Missing**                  |
| Outbound queue            | Disconnected commands queue; TX commands lack safety-specific stale-on policy                | Reconnect must never replay stale PTT-on                            | Current queueing **Automated**; safe policy **Missing**                        |
| MediaSession              | Sends PTT directly, bypassing audio/MOD/permit/timer paths                                   | All input sources use one TX state machine                          | Direct bypass proven; unified safety **Missing**                               |
| Backend de-key            | Frontend does not prove radio de-key on link/session loss                                    | Backend watchdog/session-loss contract is required defense-in-depth | **Unknown**; hardware/integration evidence required                            |
| Global feedback           | Nested Toast and power surfaces and mobile status differences exist                          | TX/connection/safety state must be global and consistent            | Composition proven; singleton behavior **Missing**                             |

The de-key, queue, health-gate, session-loss, and unmount findings are safety
defects or unknowns. They must not be classified as code-cleanliness refactors.

## Bounded parity matrix

### Presentation and viewport coverage

| Presentation / viewport        | Current selection or render evidence                             | RX/TX behavior evidence                                                                      | Switching/resource evidence |
| ------------------------------ | ---------------------------------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------- |
| Desktop wide (`desktop-v2`)    | **Automated** structure and dual-RX rendering                    | **Automated** narrow adapter/panel contracts; cross-surface parity **Missing**               | **Missing**                 |
| LCD cockpit                    | **Automated** partial layout/availability; wrapper exists        | Shared panels provide code-path evidence; end-to-end parity **Missing**                      | **Missing**                 |
| LCD scope                      | Wrapper and renderer exist; full scenario evidence **Missing**   | RX/TX parity **Missing**                                                                     | **Missing**                 |
| Mobile portrait                | **Automated** structure, capability gating and selected TX cases | **Automated** early-release/order cases; active-TX unmount **Missing**                       | **Missing**                 |
| Mobile landscape/orientation   | Code path and orientation release guard exist                    | Touch/orientation behavior requires **Manual** validation; parent-unmount safety **Missing** | **Missing**                 |
| SDR test                       | **Automated** top-row snapshot only                              | RX/TX parity **Missing**                                                                     | **Missing**                 |
| Narrow/touch resolution        | Current resolver gives mobile precedence                         | Direct resolver/alias matrix **Missing**                                                     | **Missing**                 |
| Desktop responsive breakpoints | Structural/top-row checks exist                                  | Semantic parity across breakpoints **Missing**                                               | **Missing**                 |

### Capability scenarios A-D

| Scenario                          | Presentation/selection                                                                          | RX monitor and AF                                                    | VFO / MAIN-SUB                                                 | PTT/TX                                                              | Reconnect/remount and transport invariance |
| --------------------------------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------------- | ------------------------------------------ |
| A: hardware scope + live audio    | Auto desktop code path; forced surfaces exist; actual A fixture through composition **Missing** | Local contracts **Automated**; cross-presentation **Missing**        | Local adapters **Automated**; visible parity **Missing**       | Desktop/mobile narrow cases **Automated**; all surfaces **Missing** | **Missing**                                |
| B: no hardware scope + live audio | Auto cockpit intended; direct scenario test **Missing**                                         | Unit semantics **Automated**; browser LIVE-without-scope **Missing** | Local adapters **Automated**; cross-surface **Missing**        | Local cases **Automated**; matrix **Missing**                       | **Missing**; primary regression case       |
| C: hardware scope + no live audio | Auto desktop intended; composition fixture **Missing**                                          | LIVE-hidden/local AF contracts **Automated**                         | Local adapters **Automated**; cross-surface **Missing**        | Capability-local behavior partial; parity **Missing**               | **Missing**                                |
| D: no scope + no live audio       | Auto cockpit intended; composition fixture **Missing**                                          | LIVE-hidden **Automated**                                            | Core adapter behavior **Automated**; cross-surface **Missing** | Conditional local behavior partial; parity **Missing**              | **Missing**                                |

### Cross-cutting state and transport cases

| Case                                   | Required parity                                                                                            | Current evidence                                                                |
| -------------------------------------- | ---------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------- |
| RX local → LIVE → MUTE → local         | Same state, AF target and visibility in all compatible presentations                                       | Mapper/panel sequence **Automated**; browser/cross-presentation **Missing**     |
| MAIN ↔ SUB                            | Same active receiver, focus, frequency, mode and meter semantics                                           | Adapter/wiring **Automated**; all presentations **Missing**                     |
| Split TX target                        | TX VFO is correct and visually unmistakable independent of selected RX                                     | Mapper cases **Automated**; topology/presentation matrix **Missing**            |
| RX ↔ TX                               | Audio-before-key, safe release, authoritative indication, same guard policy                                | Narrow desktop/mobile **Automated**; all inputs/surfaces **Missing**            |
| Presentation remount                   | View models rehydrate without changing runtime state                                                       | **Missing**                                                                     |
| Control reconnect                      | No stale PTT-on; de-key remains possible under degraded health                                             | Generic reconnect/queue **Automated**; safety policy **Missing**                |
| Hardware/audio scope transport         | Socket identity/count remains stable across presentation changes                                           | Current mount-sensitive behavior **Automated**; required invariance **Missing** |
| Audio transport                        | No socket opens only because a presentation mounted; active RX survives a switch                           | **Missing**                                                                     |
| Backend/radio session loss while keyed | Local RX state, best-effort remote de-key, audio stop, backend watchdog                                    | Frontend/backend outcome **Unknown**; integration/hardware **Manual**           |
| Visual state                           | Selected reference surfaces visibly distinguish RX, TX, disconnected, powered-off and unsupported controls | Limited i18n screenshots; reference baselines **Missing**                       |

## Prioritized gaps

### P0 — safety and architecture gates

1. Implement and prove the accepted emergency de-key behavior: `ptt_off`
   bypasses ordinary health rejection, stale `ptt_on` is never replayed,
   session loss forces local RX/audio stop, and backend supervisor/watchdog
   limits remain explicit.
2. Implement the accepted App-owned TX controller and remove presentation
   ownership from active TX lifecycle before enabling
   runtime presentation switching; cover held, latched, pending-audio and timer
   states on unmount/remount.
3. Prove presentation switching is transport-neutral for control, hardware
   scope, audio FFT scope, and RX audio.
4. Prove Scenario B in a real composition/browser path: audio available without
   hardware scope, including LIVE, AF semantics and MAIN/SUB focus.
5. Establish one global safety/health feedback contract so TX, degraded
   connection, power and critical errors cannot disappear or duplicate by
   presentation.

### P1 — contract and reference-vertical gates

1. Implement and prove the accepted capability wire, nullability,
   receiver/slot/VFO topology, scope-source, TX support, permit, and session
   refresh semantics.
2. Add direct registry selection coverage for aliases, forced modes, viewport
   precedence, A-D fallback, and lazy-loader identity.
3. Consolidate the duplicated capability/prop decisions behind accepted
   selectors while preserving the existing payload and extension contract.
4. Establish cross-surface VFO, receiver, RX monitor, split-TX and unsupported
   control acceptance for the reference vertical.
5. Add resource/architecture enforcement that prevents presentation packages
   from importing transports, stores, audio manager or command factories.

### P2 — completion and migration evidence

1. Add approved screenshots by reference surface, viewport, capability fixture,
   RX/TX/connection state and selected design language.
2. Validate real browser audio, touch/orientation and hardware scope/audio
   coexistence manually.
3. Define migration/precedence for the existing layout, theme, panel-order and
   extension-dock persistence only when MOR-979 begins.

## Acceptance matrix for the next reference vertical

| ID  | Surface / fixture                                                    | Required observable                                                                             | Required evidence                                     |
| --- | -------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| R1  | Desktop wide, A, MAIN RX local                                       | Correct VFO values/identity, active receiver, meter, TX receiver indication; LIVE visible       | Selector + component + browser automated              |
| R2  | Desktop wide, **B**, MAIN→SUB and local→LIVE→MUTE→local              | No scope dependency; correct AF target; audio focus follows receiver                            | Selector + adapter + browser automated                |
| R3  | LCD cockpit and LCD scope, B                                         | Same VFO, receiver and monitor semantics as R2; unsupported controls never appear enabled       | Component + browser automated                         |
| R4  | Mobile portrait/landscape, A/B, dual RX, TX permitted                | Receiver selection; audio-before-key; safe release/orientation; persistent global TX indication | Component + browser automated; manual touch           |
| R5  | Desktop/LCD/SDR, C and D                                             | LIVE absent; supported AF/VFO behavior remains; TX safety/indication is consistent              | Selector + component automated                        |
| R6  | Two presentations over one runtime, A and B, active LIVE then switch | Stable control/audio/scope ownership and connection identity; state rehydrates                  | Runtime integration + browser automated               |
| R7  | Reference surfaces, split and MAIN/SUB permutations                  | RX and TX identities remain distinct; TX target remains correct                                 | Adapter + component + browser automated               |
| R8  | Any presentation, keyed then degraded/disconnected/unmounted         | Immediate local RX/audio stop, prioritized de-key attempt, no stale replay, global error state  | Unit + integration automated; backend/hardware manual |
| R9  | Stable R1-R8 fixtures                                                | Approved screenshots for RX, TX, disconnected, powered-off and unsupported states               | Automated capture + manual visual approval            |

## MOR-972 exit and current sequencing

MOR-972 is complete as a discovery item because:

- this artifact is accepted as the bounded current-state and parity baseline;
- proven behavior is kept distinct from proposals, missing tests, and unknown
  backend/radio outcomes;
- capability contract questions are explicitly handed to MOR-974 rather than
  silently resolved in presentation code;
- P0 TX/de-key/health and transport-continuity findings are treated as gates in
  the existing work sequence;
- the R1-R9 matrix is the acceptance baseline for MOR-975 and later proof work;
- GitHub implementation work is created only after an existing Linear item is
  refined into a bounded, ready-to-implement repository slice.

Current Linear dependency order:

1. **MOR-973 and MOR-974 — composition/capability foundation:** land their
   bounded prerequisites and evidence. MOR-980, MOR-981, and MOR-985 are the
   current transport/resolver/wire chains; MOR-982 and MOR-988 are accepted
   decision inputs.
2. **Immediate fail-closed slices:** MOR-989 and MOR-990 consume MOR-982;
   MOR-991 consumes MOR-988; MOR-993 consumes MOR-986. They improve safety or
   structural correctness without claiming the final controller/supervisor.
3. **MOR-994 audio-FFT program:** MOR-995 and MOR-996 may run in parallel;
   MOR-997 depends on both, and MOR-998 depends on MOR-997. This order keeps
   fixture truth, route admission, service construction, and serialization
   consistent.
4. **MOR-983 and MOR-984 — lifetime and derived-selector boundaries:** MOR-983
   follows the presentation seam plus accepted TX/capability ownership;
   MOR-984 follows the validated raw wire and accepted MOR-988 semantics.
5. **MOR-975 — VFO plus RX/TX reference vertical:** consume the completed
   MOR-973/MOR-974 contracts and accepted MOR-986 backend policy, implement and
   prove R1-R8, then use MOR-987 for the independent cross-surface,
   fake-backend, browser, and hardware evidence.
6. **MOR-976 — dual-receiver cockpit:** consume the proven reference contract;
   do not introduce receiver/VFO-slot semantics locally.
7. **MOR-977 — reference design language:** select visual semantics over the
   same reference contract; do not own commands or safety state.
8. **MOR-978 — second design language proof:** demonstrate behavior and
   transport invariance without runtime, adapter, or command changes.
9. **MOR-979 — workspace:** begin only after stable layout/surface IDs exist;
   migrate fragmented persistence without altering capability or safety policy.

MOR-992 may delay CI-backed proof for any atomic GitHub slice, but does not
change these dependencies or reopen accepted architecture.

## Evidence index

- App and presentation ownership: `frontend/src/App.svelte:22-98`;
  `frontend/src/skins/registry.ts:31-80`;
  `frontend/src/components-v2/layout/RadioLayout.svelte:59-145`;
  `frontend/src/components-v2/layout/RadioLayout.svelte:215-404`;
  `frontend/src/components-v2/layout/MobileRadioLayout.svelte:58-100`;
  `frontend/src/components-v2/layout/MobileRadioLayout.svelte:191-486`;
  `frontend/src/components-v2/layout/LcdLayout.svelte:63-121`.
- Global feedback: `frontend/src/components/shared/Toast.svelte:55-107`;
  `frontend/src/components-v2/layout/StatusBar.svelte:51-104`;
  `frontend/src/components-v2/layout/StatusBar.svelte:128-221`.
- Capability source and wire: `src/rigplane/web/runtime_helpers.py:479-517`;
  `src/rigplane/web/server.py:2725-2817`;
  `frontend/src/lib/types/capabilities.ts:79-112`;
  `frontend/src/lib/transport/http-client.ts:113-117`.
- Capability use and mapping: `frontend/src/lib/stores/capabilities.svelte.ts:14-155`;
  `frontend/src/components-v2/wiring/state-adapter.ts:1-16`;
  `frontend/src/components-v2/wiring/state-adapter.ts:205-231`;
  `frontend/src/lib/runtime/props/panel-props.ts:1-24`;
  `frontend/src/lib/runtime/props/panel-props.ts:200-250`;
  `frontend/src/components-v2/vfo/VfoPanel.svelte:41-43`;
  `frontend/src/components-v2/vfo/vfo-ops-utils.ts:3-23`.
- Runtime/session lifecycle: `frontend/src/lib/runtime/frontend-runtime.ts:128-191`;
  `frontend/src/lib/runtime/system-controller.ts:65-105`.
- Transport queue, gate and registry: `frontend/src/lib/transport/ws-client.ts:137-169`;
  `frontend/src/lib/transport/ws-client.ts:431-443`;
  `frontend/src/lib/transport/ws-client.ts:630-656`.
- Scope and audio ownership: `frontend/src/components/spectrum/SpectrumPanel.svelte:323-364`;
  `frontend/src/lib/runtime/scope-controller.svelte.ts:46-90`;
  `frontend/src/lib/audio/audio-manager.ts:56-188`;
  `frontend/src/lib/audio/audio-manager.ts:204-358`.
- TX paths: `frontend/src/components-v2/panels/TxPanel.svelte:56-118`;
  `frontend/src/components-v2/layout/MobileRadioLayout.svelte:307-433`;
  `frontend/src/lib/media/media-session.ts:87-96`.
- Backend capability tests: `tests/test_web_capability_guards.py:66-210`;
  `tests/test_web_capability_guards.py:244-288`;
  `tests/test_web_runtime_helpers.py:101-175`.
- Frontend capability/runtime tests:
  `frontend/src/lib/stores/__tests__/capabilities.test.ts:18-225`;
  `frontend/src/lib/runtime/__tests__/frontend-runtime.test.ts:128-201`;
  `frontend/src/lib/runtime/__tests__/scope-controller.test.ts:44-153`.
- Presentation and semantic tests:
  `frontend/src/components-v2/layout/__tests__/RadioLayout.test.ts:309-434`;
  `frontend/src/components-v2/layout/__tests__/MobileRadioLayout.component.svelte.test.ts:182-409`;
  `frontend/src/components-v2/panels/__tests__/RxAudioPanel.test.ts:43-198`;
  `frontend/src/components-v2/wiring/__tests__/state-adapter.test.ts:272-312`;
  `frontend/src/components-v2/wiring/__tests__/vfo-wiring.test.ts:205-217`;
  `frontend/src/components-v2/panels/__tests__/TxPanel.test.ts:333-392`.
- Transport/visual tests: `frontend/src/lib/audio/__tests__/audio-manager.test.ts:103-287`;
  `frontend/src/lib/transport/__tests__/ws-client.test.ts:317-469`;
  `frontend/tests/e2e/i18n/i18n-visual.spec.ts:260-318`.
- Prior intent: `docs/plans/2026-04-12-target-frontend-architecture.md:1-52`;
  `docs/plans/2026-04-12-target-frontend-architecture.md:483-515`;
  `docs/plans/2026-04-11-parity-matrix.md:20-120`;
  `docs/plans/2026-07-25-ui-composition-architecture-v3.md:88-139`.
