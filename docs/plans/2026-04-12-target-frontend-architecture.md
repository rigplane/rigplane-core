# Target Frontend Architecture — ADR

**Date:** 2026-04-12
**Status:** Approved for implementation
**Synthesizes:** `#643` (runtime contract), `#646` (presentation architecture)
**Informed by:** `#641` (audit), `#642` (audio trace), `#644` (parity matrix), `#645` (migration plan)
**Base commit:** `d87f50a` (`feat/642-audio-debug-logging`)

## Purpose

Single authoritative reference for the unified frontend architecture. All implementation issues (`#647`–`#653`) should be judged against this document.

---

## Architecture overview

Four layers, strict one-way dependency:

```
┌────────────────────────────────────────────────────┐
│  SKIN + LAYOUT + THEME                              │  ← visual shell
│  (one Svelte component per SkinId — see Layer 4)   │
├──────────────────────────────────────────────────────┤
│  SEMANTIC COMPONENTS                                │  ← behavior contracts
│  (VfoSurface, MetersSurface, RxAudioSurface, ...)   │
├──────────────────────────────────────────────────────┤
│  VIEW-MODEL ADAPTERS                                │  ← pure functions
│  (toVfoProps, toMeterProps, toRxAudioProps, ...)     │
├──────────────────────────────────────────────────────┤
│  RUNTIME                                            │  ← singleton, no DOM
│  (transport, audio, scope, state, commands)          │
└─────────────────────────────────────────────────────┘
```

**Rule:** each layer depends only on the layer below. Never upward, never across.

---

## Layer 1: Runtime

### What it owns

All stateful side effects:

- HTTP bootstrap (polling, capabilities fetch)
- Control WebSocket (`/api/v1/ws`) lifecycle
- Audio WebSocket (`/api/v1/audio`) lifecycle
- Scope WebSocket (`/api/v1/scope`, `/api/v1/audio-scope`) lifecycle
- Browser `AudioContext` and playback/mic lifecycle
- Binary frame parsing (scope, audio)
- Command dispatch and optimistic state patches
- Reconnect logic and connection health
- Capability normalization

### What it must not do

- Import Svelte components
- Reference DOM elements
- Know about skins, themes, or layouts
- Contain presentation logic

### FrontendRuntime

See `FrontendRuntime` in `frontend/src/lib/runtime/frontend-runtime.ts`.

### Audio

Owned by `AudioManager` (`frontend/src/lib/audio/audio-manager.ts`),
`RxPlayer` (`frontend/src/lib/audio/rx-player.ts`), and `TxMic`
(`frontend/src/lib/audio/tx-mic.ts`).

### Scope

See `ScopeController` in `frontend/src/lib/runtime/scope-controller.svelte.ts`.

### System

See `SystemController` in `frontend/src/lib/runtime/system-controller.ts`.

---

## Layer 2: View-model adapters

### Contract

Adapters are **pure functions**. No side effects, no subscriptions, no imports from transport/audio/WS.

### View-model contract

See `frontend/src/semantic/radio-view-model.ts` (the adapter/semantic seam)
and its producer, `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`.

### Adapter location

`frontend/src/lib/runtime/adapters/`, plus `frontend/src/lib/runtime/props/panel-props.ts`
— `toVfoProps`, `toMeterProps`, and `toRxAudioProps` (the diagram's own
examples above) are exported from the latter, not the former.

---

## Layer 3: Semantic components

### Contract

A semantic component represents a **radio concept** with a stable typed interface.

Rules:

- receives only semantic props and callbacks
- no transport imports (`sendCommand`, `getChannel`, `audioManager`)
- no backend endpoint knowledge
- no concrete skin assumptions
- framework-level logic only (event handling, conditional rendering, a11y)

### Semantic component list

See `frontend/src/semantic/` for the current set.

### Svelte 5 pattern

Semantic components use `children: Snippet` or typed props — no `<slot>`. Skins provide concrete rendering.

```svelte
<script lang="ts">
  interface Props {
    monitorMode: 'live' | 'radio' | 'mute';
    hasLiveAudio: boolean;
    volume: number;
    muted: boolean;
    onMonitorModeChange: (mode: string) => void;
    onVolumeChange: (v: number) => void;
  }
  let { monitorMode, hasLiveAudio, volume, muted,
        onMonitorModeChange, onVolumeChange }: Props = $props();
</script>

<!-- Semantic components can render directly or delegate to skin -->
<!-- The key constraint: no runtime imports above -->
```

---

## Layer 4: Presentation (Skin + Theme + Layout)

### Three independent knobs

| Knob | Changes | Mechanism | Example |
|---|---|---|---|
| **Theme** | Colors, fonts, spacing, shadows | CSS custom properties | `dracula.css`, `nord.css`, `crt-green.css` |
| **Skin** | Visual implementation of semantic components | Concrete Svelte components | (skin-specific) |
| **Layout** | Spatial arrangement of components on screen | Grid/flex composition in skin top-level | (skin-specific) |

**Theme** does not change component identity.
**Skin** does not change behavior contracts.
**Layout** does not change transport ownership.

### Skin resolution and lazy loading

See `SkinId`, `resolveSkinId()`, and `SKIN_LOADERS`/`loadSkin()` in
`frontend/src/skins/registry.ts`.

### Layout zones

See `LayoutManifest`/`LayoutZone` in `frontend/src/presentation/layouts/contract.ts`.

### Theme architecture

Unchanged from current system — CSS variable themes, applied via `data-theme` attribute:

```css
[data-theme="dracula"] {
  --bg: #282a36;
  --panel: #44475a;
  --text: #f8f8f2;
  --accent: #bd93f9;
  /* ... */
}
```

Themes are orthogonal to skins. Any theme can be applied to any skin (within reason — amber-lcd may have its own locked theme token set).

---

## Top-level layout

- Runtime: `frontend/src/lib/runtime/`
- Adapters: `frontend/src/lib/runtime/adapters/`
- Semantic components: `frontend/src/semantic/`
- Skins: `frontend/src/skins/`
- Layout manifests: `frontend/src/presentation/layouts/`
- Themes: `frontend/src/components-v2/theme/`
- Primitives: `frontend/src/primitives/`
- Legacy layouts/panels (not yet replaced): `frontend/src/components-v2/`

---

## Architectural invariants

These must hold at all times after migration. Violation = regression.

### INV-1: Single RX playback path

All layouts route through the same audio subsystem. Codec negotiation, WS subscription, decoding, volume, and mute are layout-independent.

### INV-2: Single scope ownership per type

Hardware scope and audio FFT are owned by `ScopeController`. Layouts choose whether and where to render — never whether to connect.

### INV-3: `scope=false + audio=true` is first-class

Absence of hardware scope must never alter audio behavior. LCD fallback is a presentation concern, not a runtime concern.

### INV-4: Capability gating precedes presentation

Panels do not independently decide whether backend capability exists. Runtime/adapters expose stable booleans (`hasLiveAudio`, `hardwareScopeAvailable`, `hasTx`).

### INV-5: Mount/unmount does not change transport

Swapping layouts or mounting/unmounting visual components must not start or stop transports.

### INV-6: No runtime imports in presentation

Presentation components (semantic, skins, primitives) must not import from
`runtime/`, `$lib/transport/`, or `$lib/audio/audio-manager`. Enforced by
eslint; see `docs/internals/skins-presentation-boundary-gate.md` (MOR-2034)
for what is actually checked.

---

## Import boundary rules

### Allowed

| Component type | May import from |
|---|---|
| Runtime | `$lib/transport`, `$lib/audio`, stores, types |
| Adapters | Runtime types (read-only), utilities |
| Semantic | Adapter types, primitives, Svelte, types |
| Skins | Semantic, primitives, adapters, themes, Svelte |
| Primitives | Themes, Svelte, types |

### Forbidden

| Component type | Must NOT import from |
|---|---|
| Semantic | `runtime/`, `$lib/transport/`, `$lib/audio/audio-manager` |
| Skins | `runtime/` (except `FrontendRuntime` type via props), transport, audio |
| Primitives | Runtime, adapters, transport, audio, stores |

### Enforcement

See `frontend/eslint.config.js` and
`docs/internals/skins-presentation-boundary-gate.md` (MOR-2034).

---

## Anti-patterns

### 1. Skin imports runtime directly

Turns skins into hidden behavior forks. Skin receives `FrontendRuntime` as a prop from `App.svelte` — it never imports the singleton.

### 2. Theme contains behavior flags

Theme is for tokens only. Feature decisions belong in runtime/adapters.

### 3. Layout decides transport based on mount state

This is the current root cause of drift. Runtime owns transport lifecycle; layout only renders data.

### 4. Semantic component accepts escape hatches

Props like `rawWsData` or `onRawCommand` destroy the semantic boundary. If a semantic component needs new behavior, extend the adapter.

### 5. LCD treated as special case

LCD is a skin + layout variant over shared runtime. It is not a separate code path. No `if (isLcd)` in runtime or adapters.

### 6. Duplicated protocol parsing

Scope frame parsing, audio header parsing — all binary protocol logic lives in runtime controllers. Never in rendering components.

---

## Migration status

See `docs/plans/2026-07-25-ui-composition-architecture-v3.md` (extends this
ADR) and `frontend/src/presentation/layouts/contract.ts` for how skins are
actually composed today.

---

## Evidence gates

`docs/internals/skins-presentation-boundary-gate.md`
(MOR-2034) is the permanent, post-migration release-gate check for this ADR's
central guarantee — a skin depends only on the presentation layer, and never
derives radio truth locally — naming the mechanisms that enforce it and
recording where their reach currently ends.

---

## Summary

This architecture guarantees:

- **New radio capability** = changes in runtime + adapter, zero changes to skins
- **Behavioral parity** = enforced architecturally (one runtime, one adapter set) and by eslint (`docs/internals/skins-presentation-boundary-gate.md`, MOR-2034)
- **No code duplication** = protocol parsing once in runtime, semantic logic once in adapters, visual variants only in skins

---

## Addendum 2026-09-02: instrument components and their single home

### Decision

Layer 4 above predates design languages; since the design-language contract landed (`DesignLanguageManifest` in `frontend/src/presentation/languages/contract.ts`, registered per family in `frontend/src/presentation/languages/declarations.ts`) presentation has four knobs, not three; this addendum does not reconcile the Layer 4 table. There is a finite set of instrument components — button/toggle, slider/knob, S-meter and gauges, frequency display, state feedback — each receiving and sending data only through the frozen semantic contract: `RadioViewModel` in `frontend/src/semantic/radio-view-model.ts` plus typed intents (callback props). How an instrument looks is decided outside the instrument itself: the design language supplies the display model and the stylesheet, the skin composes the instruments and chooses which language applies (see the decision below). The 14 semantic surfaces under `frontend/src/semantic/` are panels that stay where they are; today only `MetersSurface.svelte` (importing `BarGauge`/`LinearSMeter` from `components-v2/meters/`) and `VfoSurface.svelte` (importing `FrequencyDisplayInteractive` from `primitives/frequency/`) mount instrument components, and of the other twelve, eleven write instrument markup inline while `ScopeDisplaySurface.svelte` renders text only. Instruments live today across many homes (census below); the single home going forward is `frontend/src/primitives/`. Legacy v1/v2 presentation under `frontend/src/components-v2/` is to be removed — MOR-1099 covers legacy-wrapper and duplicate-ownership retirement, and the tree's removal is an owner decision recorded in MOR-2215 — but `components-v2/wiring/SemanticRadioSurfaces.svelte` and `components-v2/theme/` are live v3 infrastructure, not legacy, and stay put regardless. The home is not renamed. Each migration PR re-points every consumer and deletes the old file in the same change (no compatibility re-exports); `frontend/src/components/` (Toast, spectrum) is left as is. Recorded in MOR-2215. Migration proceeds one instrument per PR, adapting each to the conformance checklist below, with no compatibility re-exports left behind.

### Where instruments live today

A name census at `fef3431e` found instrument-shaped components in twelve directories (among them `frontend/src/lib/Button/`, `components-v2/controls/value-control/`, `components-v2/meters/`, `components-v2/panels/lcd/`, `primitives/frequency/`). The authoritative, current enumeration is MOR-2216; the single home going forward is `frontend/src/primitives/`.

### Conformance checklist

1. **No runtime/transport/audio imports.** Enforced by `FORBIDDEN_PRIMITIVES_IMPORTS` in `frontend/eslint.config.js`, which bans primitives from `$lib/runtime/adapters/*`, transport, audio, and stores, and separately from `semantic/`/`components-v2/` — primitives depend on nothing above them.
2. **Semantic props and callbacks only.** `FrequencyDisplayInteractive.svelte`'s `Props` interface is the existing precedent — flat values (`freq: number`) and callback props (`onFreqChange`), never a runtime or store object — the same rule this ADR's Layer 3 "Contract" section states for semantic components above.
3. **Honest unknown.** Never fabricate a value or `aria-pressed="false"` on a toggle over an unread reading (rule: MOR-1358, `docs/validation/desktop-v2-v3-parity.md` P13; reference implementation `frontend/src/semantic/pressed-of.ts`, which a primitive cannot import — the migration moves or re-derives the helper under `primitives/`). Choice groups per P13c.
4. **Look from outside.** Simple styling via stable classes selected by the language stylesheet, e.g. `.rx-tx-surface` under `[data-design-language='segmentline']` in `frontend/src/presentation/languages/segmentline/segmentline.css`. Instrument geometry goes through a display model the instrument consumes as a component prop; supplied by the design language chosen for the skin (see the decision below).
5. **Accessibility.** Name, focus, and aria state consistent with unknown — the aria half is the same rule as item 3; name and focus are checked per instrument by its own component test (item 6).
6. **Pinned coverage.** An isolated component test (naming precedent: `frontend/src/primitives/stage/__tests__/ScaledStage.isolated.test.ts`), an entry in the fixture-harness catalog (`frontend/fixtures/catalog.ts`, MOR-1070), and one reddening mutation — the mutation-kill convention already used in this codebase, e.g. row P13's "mutation-killed 8×" in `docs/validation/desktop-v2-v3-parity.md`.

### Order of work

1. Read-only inventory of existing instruments against the six-item checklist above (MOR-2216).
2. Migrate one instrument per PR into `primitives/`, adapting it to the checklist and re-pointing every consumer, with no compatibility re-exports. A migration PR carries its directory-scoped gates with it (the meters census, `METER_REGISTRY` in `frontend/src/components-v2/meters/__tests__/meter-contract.ts`) and updates `docs/architecture/building-a-skin.md`'s meter section in the same PR.

### Decision on look vs layout (2026-09-02)

Decided 2026-09-02 (MOR-2215): each skin declares the design languages it supports and one default; the operator may switch only among the current skin's declared languages; the language preference is stored per skin, not globally, so an incompatible skin/language pair cannot exist by construction. The display model an instrument consumes as a prop is supplied by the design language (renderer slots in `RENDERER_SLOT_NAMES`, `frontend/src/presentation/languages/contract.ts`); the skin composes. `layoutCompatibility` (`DesignLanguageManifest`, populated in `frontend/src/presentation/languages/declarations.ts`) is consulted only over the pairs a skin declares. The `data-dl-*` attribute channel `annotate()` writes (`frontend/src/semantic/design-language-renderers.ts`) is internal (MOR-2214): of the three registered design-language stylesheets, only `frontend/src/presentation/languages/segmentline/segmentline.css` selects on any `data-dl-*` attribute, and only on `[data-dl-unknown]` and `[data-dl-hot]` — `fieldline.css` and `studioline.css` select on none.
