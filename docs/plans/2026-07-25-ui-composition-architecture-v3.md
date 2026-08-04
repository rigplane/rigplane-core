# UI Composition Architecture v3

**Date:** 2026-07-25

**Status:** Accepted

**Accepted:** 2026-07-26; refined by
[MOR-982](https://linear.app/morozsm/issue/MOR-982/decision-freeze-the-app-owned-tx-controller-contract),
[MOR-986](https://linear.app/morozsm/issue/MOR-986/decision-define-backend-tx-ownership-stale-queue-and-session-loss),
and
[MOR-988](https://linear.app/morozsm/issue/MOR-988/decision-freeze-receiver-vfo-scope-tx-permit-and-refresh-semantics)

**Implementation status:** In progress; acceptance of this target does not
claim that the repository already implements it

**Scope:** `frontend/` presentation architecture

**Extends:** `docs/plans/2026-04-12-target-frontend-architecture.md`

## Decision

RigPlane keeps the existing one-way dependency direction:

```text
backend capabilities + runtime state
              |
              v
runtime -> adapters/view models -> semantic radio UI -> presentation
                                                        |
                                                        +-- layout
                                                        +-- design language
                                                        +-- theme
                                                        +-- workspace
```

This decision completes the presentation layer from the April 2026 ADR. It
does not replace runtime, transport, state, command, or backend contracts.

`Capabilities` from `GET /api/v1/capabilities` remains the single source of
truth for radio features. UI code may derive selectors and view models from it,
but must not introduce a parallel capability schema, persist derived capability
data, or infer support from a selected presentation.

The diagram is the accepted target dependency direction, not a description of
the current mount graph. The MOR-972 discovery baseline remains the source for
current-state evidence until each bounded implementation slice lands.

## Motivation

The current implementation is transitional:

- `App.svelte` mounts `RadioLayout` rather than loading a skin;
- the registry's lazy skin loaders are not used by the app;
- skin entry points mostly delegate back to shared monolithic layouts;
- `RadioLayout.svelte` resolves skins, reads runtime, creates handlers, selects
  responsive branches, and composes the desktop UI.

Theme, skin, layout, viewport policy, and capabilities are therefore not yet
independent extension points. New visual directions would multiply conditions
inside existing layouts instead of composing stable radio behavior.

## Goals and non-goals

Goals:

1. Keep radio behavior independent from visual presentation.
2. Reuse existing Core capabilities without duplication.
3. Let layouts and design languages evolve independently.
4. Support constrained, versioned user workspaces.
5. Test every presentation against the same behavior and safety contract.

Non-goals:

- replacing Svelte or Vite;
- changing backend capability or radio protocol ownership;
- moving transport, audio, scope, state, or commands into skins;
- making an arbitrary JSON UI-tree editor;
- faithfully cloning physical radio faceplates.

## Independent presentation dimensions

### Semantic radio UI

Stable concepts such as VFO, receiver, mode, filter, band, RF front end, DSP,
spectrum, audio, TX, tuner, meters, and memories. Semantic components receive
typed data and callbacks and contain no transport or skin knowledge.

### Layout

Spatial information architecture: which semantic surfaces appear, their zones,
hierarchy, sizing, and responsive behavior. Candidate layout families include
`spectrum-first`, `dual-receiver-cockpit`, `classic-front-panel`,
`compact-desktop`, `mobile-tabs`, and `digital-mode`.

Layouts are compiled Svelte compositions, not serialized component trees.

### Design language

Visual grammar: typography, geometry, control families, meter and frequency
renderers, state indication, motion, density, and surface treatment. Use
product-owned identifiers such as `studioline`, `fieldline`,
`contest-console`, or `classic-instrument`. Public manufacturer naming requires
separate brand/legal review.

### Theme

CSS token values such as colors, fonts, spacing, shadows, and contrast. A theme
does not change component identity or interaction semantics. Existing custom
property themes remain supported.

### Workspace

A versioned user preference selecting layout, design language, theme, density,
and limited zone configuration. It may hide optional surfaces, reorder allowed
panels, resize declared regions, and pin favorite commands. It cannot alter
capability gates, TX safety, or command semantics.

## Capability integration

The raw, validated v1 object returned by `GET /api/v1/capabilities` is
authoritative. The current
`frontend/src/lib/types/capabilities.ts::Capabilities` interface is incomplete
and is not a second source of truth; after MOR-985, the frontend validator and
type must mirror the raw contract. The adapter layer may expose a non-persisted
presentation context with derived facts such as receiver count, VFO scheme,
hardware/audio scope availability, TX support, antenna count, and available
meters. This is an adapter result, not a new protocol. Existing selectors such
as `hasAnyScope()`, `hasDualReceiver()`, `hasCapability()`, and
`getControlRange()` must be reused or consolidated, not reimplemented per skin.

Capabilities answer **what is supported**. Layout answers **how supported
functions are arranged**. Design language answers **how they communicate
state**. No presentation choice may claim a capability.

## Accepted safety and semantic refinements

The following decisions refine this architecture without changing its
presentation-layer direction:

- [MOR-982](https://linear.app/morozsm/issue/MOR-982/decision-freeze-the-app-owned-tx-controller-contract)
  (accepted 2026-07-26) assigns one browser-tab TX controller to `App.svelte`.
  Presentations and input surfaces request intents and render its snapshot; they
  do not own PTT delivery, TX audio, MOD restoration, safety timers, or
  authoritative TX state.
- [MOR-986](https://linear.app/morozsm/issue/MOR-986/decision-define-backend-tx-ownership-stale-queue-and-session-loss)
  (accepted 2026-07-26) assigns one backend-neutral TX safety supervisor to
  each managed radio target runtime. It survives client, poller, and provider
  reconnect lifetimes and is separate from the App-owned UX/controller state
  machine.
- [MOR-988](https://linear.app/morozsm/issue/MOR-988/decision-freeze-receiver-vfo-scope-tx-permit-and-refresh-semantics)
  (accepted 2026-07-26) keeps the raw v1 capability document authoritative and
  permits only pure, transient derived presentation semantics. Receiver, VFO
  slot, TX target, hardware scope, audio FFT, browser TX audio, CAT PTT,
  native voice TX, MOD routing, permit, activity, and health remain distinct
  facts and fail closed when required evidence is unknown.

## Composition contract

The app-level composition root owns bootstrap, presentation resolution, the
persistent browser TX controller, and global overlays. Its selection contains
stable layout, design-language and theme IDs plus `comfortable`, `compact`, or
`dense` density.

A layout manifest declares identity, zones, responsive policy, and compatible
topology classes — a pure, transient derived presentation fact (MOR-988), not
a capability claim; its implementation is a lazy-loaded Svelte component. A
design language manifest declares compatible renderer families and token
bundles. Manifests support discovery and validation, never executable radio
behavior.

Resolution order:

1. bootstrap runtime and authoritative capabilities;
2. load and migrate the versioned workspace;
3. validate its layout against viewport and capabilities;
4. choose a compatible fallback when necessary;
5. load layout and design language;
6. provide semantic view models and product services;
7. render safety/status overlays outside the selected layout.

### Stage sizing (MOR-1160)

[MOR-1160](https://linear.app/morozsm/issue/MOR-1160/decision-freeze-the-surface-sizing-model-fluid-vs-fixed-native-uniform)
(frozen 2026-07-29) fixes the surface sizing model: a layout's instrument
stage is either `fluid` (reflows on declared breakpoints) or `fixed-native`
(scaled as one uniform, letterboxed block by `min(w/nativeW, h/nativeH)`,
falling back below `minScale`). Chrome — nav, control columns, sidebars,
anything that is not the instrument glass — is fluid by doctrine and is
never swept into the stage's fixed scale (constraint 2). It renders as a
sibling of the future `ScaledStage` primitive, which will own mechanical
enforcement of this split (constraint 1).

A layout manifest's sizing field is scoped to this instrument-stage axis, not
the whole layout —
[MOR-1247](https://linear.app/morozsm/issue/MOR-1247/scope-layout-manifest-sizing-to-the-instrument-stage-rename-sizing)
renamed it `stageSizing` to say so and froze it declaration-only (an
architecture test flags any direct textual read outside
`presentation/layouts/`) until `ScaledStage` exists to enforce it.

## Dependency and product invariants

1. Runtime imports no adapters or presentation.
2. Adapters are pure and depend only on runtime/domain contracts.
3. Semantic components depend on typed view models and shared primitives.
4. Layouts compose semantic surfaces and declared renderers.
5. Design languages import no transport, stores, runtime, or command code.
6. Workspaces reference stable IDs, never component module paths.
7. Presentation switching never reconnects transport, audio, or scope.
8. Capability checks live in adapters/composition policy, not CSS or skins.
9. RX/TX state, active TX receiver, connection loss, and dangerous-action
   guards remain unmistakable in every presentation.
10. Accessibility, focus, keyboard behavior, and RigPlane interaction grammar
    remain stable across presentations.
11. Presentation instances never own TX authority, PTT delivery, TX safety
    timers, or backend de-key obligations.
12. Availability, selected source, active resource, and health remain separate;
    a raw capability tag or presentation choice cannot manufacture a live
    service.

Enforce these rules with ESLint, architecture tests, behavior tests, and
representative screenshot regression coverage.

## Target ownership

Keep runtime and adapters under `lib/runtime/`; add stable `semantic/` and
`primitives/` surfaces; group selection policy, layouts, languages, themes, and
workspaces under `presentation/`; make `App.svelte` the composition root and
global-overlay owner. Existing `components-v2/`, `skins/`, and theme directories
migrate incrementally; there is no flag-day move.

## Migration sequence

1. Complete the MOR-972 inventory and parity baseline. This discovery item is
   done.
2. Land the bounded foundation evidence and fail-closed safety prerequisites:
   PTT transport asymmetry (MOR-980), presentation resolver characterization
   (MOR-981), authoritative capability-wire evidence (MOR-985), raw
   MediaSession PTT removal (MOR-989), split local audio stop from confirmed MOD
   restore (MOR-990), topology validation (MOR-991), and interim backend MOD
   teardown safety (MOR-993).
3. Complete the MOR-988 audio-FFT correction program in its explicit order:
   concrete relay and FFT fixtures (MOR-995 and MOR-996, parallel), broadcaster
   route/PCM predicate (MOR-997), then service construction and serialization
   (MOR-998). MOR-994 remains the Linear program owner.
4. Finish the App presentation-selection seam without activating lazy mounting,
   then implement the presentation-lifetime boundary (MOR-983), with MOR-971 as
   a required decision input. Runtime transport, scope, audio, feedback, and TX
   ownership must be outside replaceable presentations first.
5. Implement pure capability-derived presentation selectors (MOR-984) from the
   accepted MOR-988 semantics and validated MOR-985 raw wire. Do not change the
   backend source of truth or introduce persisted derived capabilities.
6. Implement the accepted App-owned TX controller and backend TX supervisor
   through the non-overlapping Linear slices emitted by MOR-982 and MOR-986.
7. Build and prove the VFO plus RX/TX reference vertical (MOR-975), then close
   the cross-surface automated and hardware evidence matrix in MOR-987.
8. Implement `dual-receiver-cockpit` (MOR-976), select and implement the
   reference design language (MOR-977), and prove a second language without
   behavior changes (MOR-978).
9. Add the constrained, versioned workspace only after layout and language
   contracts stabilize (MOR-979), then migrate remaining panels and retire
   delegation wrappers incrementally.

## Planning and execution control

Linear project `RigPlane Core UI Composition Architecture v3` is the control
plane for accepted decisions, dependencies, priorities, and implementation
status. GitHub contains only bounded repository execution issues, commits, PRs,
checks, and independent-review evidence linked back to their Linear owner.
GitHub state must not be used to invent or reorder architecture policy.

[MOR-992](https://linear.app/morozsm/issue/MOR-992/restore-repo-scoped-self-hosted-ci-runner-for-rigplane-core)
tracks the unavailable repo-scoped self-hosted runner. That can block CI-backed
execution evidence or merging, but it is an operational blocker, not unresolved
architecture and does not reopen the accepted decisions above.

## Acceptance criteria

The architecture is proven when:

- a design language requires no runtime, adapter, or command changes;
- a layout requires no transport or radio-domain duplication;
- one layout supports single- and dual-receiver radios from existing capabilities;
- presentation switching leaves active transports and audio sessions intact;
- unsupported controls cannot appear enabled;
- regression coverage spans capabilities, viewports, RX/TX states, layouts, and design languages;
- Core, Pro/Tauri, and Station keep using the same frontend artifact.

## Open decisions

1. Boundary between semantic components and language-specific renderers.
2. Whether density belongs to design language, workspace, or both.
3. Reference radios and viewport matrix for the first layout.
4. Workspace migration and forward-compatibility policy.
5. Component-example and screenshot-review tooling.
