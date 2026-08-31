# Target Frontend Architecture — ADR

**Date:** 2026-04-12
**Status:** Approved for implementation
**Synthesizes:** `#643` (runtime contract), `#646` (presentation architecture)
**Informed by:** `#641` (audit), `#642` (audio trace), `#644` (parity matrix), `#645` (migration plan)
**Base commit:** `d87f50a` (`feat/642-audio-debug-logging`)

## Purpose

Single authoritative reference for the unified frontend architecture. All implementation issues (`#647`–`#653`) should be judged against this document.

This ADR is executable: it contains concrete TypeScript interfaces, Svelte 5 patterns, file layout, and enforcement rules. It is not a vision doc — it is a build spec.

---

## Architecture overview

Four layers, strict one-way dependency:

```
┌────────────��─────────────��──────────────────────────┐
│  SKIN + LAYOUT + THEME                              │  ← visual shell
│  (amber-lcd, desktop-v2, mobile, future skins)      │
├─────────────────��───────────────────────────��───────┤
│  SEMANTIC COMPONENTS                                │  ← behavior contracts
│  (VfoDisplay, MetersSurface, RxAudioControl, ...)   │
├───────────────���─────────────────────────��───────────┤
│  VIEW-MODEL ADAPTERS                                │  ← pure functions
│  (toVfoProps, toMeterProps, toRxAudioProps, ...)     │
├───────────────────────────────────────────────���─────┤
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

### FrontendRuntime interface

```typescript
interface FrontendRuntime {
  // Reactive state (Svelte 5 $state internally)
  readonly state: RadioState;
  readonly capabilities: Capabilities;
  readonly connection: ConnectionStatus;
  readonly layoutPreference: 'auto' | 'lcd' | 'standard';
  readonly isMobile: boolean;

  // Controllers
  readonly audio: AudioController;
  readonly scope: ScopeController;
  readonly system: SystemController;

  // Single command dispatch entry point
  cmd(command: string, params?: Record<string, unknown>): void;
}
```

### AudioController

```typescript
interface AudioController {
  readonly rxEnabled: boolean;
  readonly txEnabled: boolean;
  readonly monitorMode: 'live' | 'radio' | 'mute';
  readonly volume: number;          // 0..1
  readonly wsConnected: boolean;
  readonly txSupported: boolean;

  setMonitorMode(mode: 'live' | 'radio' | 'mute'): void;
  setVolume(v: number): void;
  startTx(): Promise<string | null>;
  stopTx(): void;
}
```

Owns: `/api/v1/audio` WS, `RxPlayer`, `TxMic`, codec negotiation, `AudioContext` lifecycle.

Does not own: AF level radio command — that goes through `cmd()`.

### ScopeController

```typescript
interface ScopeController {
  readonly hardwareScopeAvailable: boolean;
  readonly audioScopeAvailable: boolean;
  readonly activeScope: 'hardware' | 'audio-fft' | null;

  readonly scopeFrame: ScopeFrame | null;
  readonly audioScopeFrame: AudioScopeFrame | null;
}
```

Owns: `/api/v1/scope` and `/api/v1/audio-scope` WS connections, binary frame parsing.

Presentation components consume `scopeFrame`/`audioScopeFrame` — they never open channels.

### SystemController

```typescript
interface SystemController {
  powerOn(): Promise<void>;
  powerOff(): Promise<void>;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  identifyFrequency(freqHz: number): Promise<EibiResult | null>;
}
```

Owns: all HTTP calls to backend (`/api/v1/radio/power`, `/api/v1/eibi/identify`, etc.).

---

## Layer 2: View-model adapters

### Contract

Adapters are **pure functions**. No side effects, no subscriptions, no imports from transport/audio/WS.

```typescript
type Adapter<T> = (
  state: RadioState,
  caps: Capabilities,
  audio: AudioController,
  receiver?: 'main' | 'sub',
) => T;
```

Each adapter returns a typed view-model with:

- derived display values
- semantic callbacks (bound from runtime controllers)

### Examples

```typescript
interface VfoViewModel {
  freqHz: number;
  mode: string;
  filter: number;
  dataMode: boolean;
  isActive: boolean;
  badges: Badge[];
  onFreqChange: (hz: number) => void;
  onModeChange: (mode: string) => void;
  onFilterChange: (filter: number) => void;
}

interface RxAudioViewModel {
  monitorMode: 'live' | 'radio' | 'mute';
  hasLiveAudio: boolean;
  volume: number;
  muted: boolean;
  onMonitorModeChange: (mode: string) => void;
  onVolumeChange: (v: number) => void;
}

interface MeterViewModel {
  sMeter: number;
  swr: number | null;
  power: number | null;
  alc: number | null;
  comp: number | null;
  activeTxMeter: 'swr' | 'power' | 'alc' | 'comp';
}

interface ScopeViewModel {
  available: boolean;
  frame: ScopeFrame | null;
  onTuneToFreq: (hz: number) => void;
  onSpanChange: (span: number) => void;
}
```

### Adapter location

`frontend/src/adapters/` — one file per domain (vfo, audio, meter, scope, rf, tx, memory, system).

### What adapters replace

The existing `state-adapter.ts` and `command-bus.ts` are the embryonic form of this layer. Migration should refactor them into the adapter pattern, not duplicate alongside.

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

| Component | Props (key fields) | Callbacks |
|---|---|---|
| `VfoDisplay` | freq, mode, filter, dataMode, badges | onFreqChange, onModeChange, onFilterChange |
| `RxAudioControl` | monitorMode, hasLiveAudio, volume, muted | onMonitorModeChange, onVolumeChange |
| `TxControl` | pttActive, txEnabled, txSupported | onPttToggle, onStartTx, onStopTx |
| `MetersSurface` | sMeter, swr, power, alc, comp | — |
| `ScopeSurface` | available, frame | onTuneToFreq, onSpanChange |
| `BandSelector` | currentBand, bands | onBandChange |
| `ModeSelector` | currentMode, modes | onModeChange |
| `FilterSelector` | currentFilter, filters | onFilterChange |
| `RfFrontEnd` | att, preamp, nb, nr, rfGain, squelch | onChange per field |
| `DspControl` | notch, tpf, contour | onChange per field |
| `MemoryControl` | channels, activeChannel | onRecall, onStore, onClear |
| `StatusIndicator` | connected, radioReady, wsHealth | onReconnect |

### Svelte 5 pattern

Semantic components use `children: Snippet` or typed props — no `<slot>`. Skins provide concrete rendering.

```svelte
<!-- semantic/RxAudioControl.svelte -->
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
| **Skin** | Visual implementation of semantic components | Concrete Svelte components | `AmberFrequency` vs `IndustrialFrequency` |
| **Layout** | Spatial arrangement of components on screen | Grid/flex composition in skin top-level | `DesktopGrid` vs `MobileTabs` vs `LcdColumn` |

**Theme** does not change component identity.
**Skin** does not change behavior contracts.
**Layout** does not change transport ownership.

### Skin implementation (Svelte 5)

Each skin is a concrete Svelte component receiving `FrontendRuntime` as its single prop:

```svelte
<!-- skins/lcd-cockpit/LcdCockpitSkin.svelte -->
<script lang="ts">
  import type { FrontendRuntime } from '../../runtime/types';
  import { toVfoProps, toRxAudioProps, toMeterProps } from '../../adapters';

  import AmberFrequency from './AmberFrequency.svelte';
  import AmberMeter from './AmberMeter.svelte';
  import AmberAudioStrip from './AmberAudioStrip.svelte';
  import LcdFrame from './LcdFrame.svelte';

  interface Props { runtime: FrontendRuntime }
  let { runtime }: Props = $props();

  const vfo = $derived(toVfoProps(runtime.state, runtime.capabilities, runtime.audio, 'main'));
  const audio = $derived(toRxAudioProps(runtime.state, runtime.capabilities, runtime.audio));
  const meter = $derived(toMeterProps(runtime.state, runtime.capabilities));
</script>

<LcdFrame>
  <AmberFrequency freq={vfo.freqHz} mode={vfo.mode} />
  <AmberMeter sMeter={meter.sMeter} />
  <AmberAudioStrip
    mode={audio.monitorMode}
    volume={audio.volume}
    onModeChange={audio.onMonitorModeChange}
    onVolumeChange={audio.onVolumeChange}
  />
</LcdFrame>
```

### Skin resolution and lazy loading

```typescript
// skins/registry.ts
type SkinId = 'desktop-v2' | 'amber-lcd' | 'mobile';

function resolveSkinId(ctx: {
  capabilities: Capabilities;
  userPreference: 'auto' | 'lcd' | 'standard';
  isMobile: boolean;
}): SkinId {
  if (ctx.isMobile) return 'mobile';
  if (ctx.userPreference === 'lcd') return 'amber-lcd';
  if (ctx.userPreference === 'standard') return 'desktop-v2';
  return ctx.capabilities.scope ? 'desktop-v2' : 'amber-lcd';
}

const SKIN_LOADERS: Record<SkinId, () => Promise<{ default: Component }>> = {
  'desktop-v2': () => import('./desktop-v2/DesktopSkin.svelte'),
  'amber-lcd':  () => import('./lcd-cockpit/LcdCockpitSkin.svelte'),
  'mobile':     () => import('./mobile/MobileSkin.svelte'),
};
```

### App entry point (target)

```svelte
<!-- App.svelte -->
<script lang="ts">
  import { runtime } from './runtime';
  import { resolveSkinId, loadSkin } from './skins/registry';

  const skinId = $derived(resolveSkinId({
    capabilities: runtime.capabilities,
    userPreference: runtime.layoutPreference,
    isMobile: runtime.isMobile,
  }));

  let SkinComponent = $state(null);
  $effect(() => {
    loadSkin(skinId).then(c => { SkinComponent = c; });
  });
</script>

{#if SkinComponent}
  <SkinComponent {runtime} />
{:else}
  <div class="loading">Loading...</div>
{/if}
```

### Layout slot model

Each skin's layout arranges semantic components into named zones:

| Slot | Desktop V2 | LCD | Mobile |
|---|---|---|---|
| `header` | VfoHeader + StatusBar | StatusBar | Compact VFO + status |
| `leftRail` | Band, mode, filter, RF groups | LeftSidebar | — (tabbed) |
| `centerPrimary` | Hardware scope | AmberLcdDisplay | Scope or LCD |
| `centerSecondary` | Audio FFT / waterfall | Audio FFT (if available) | — |
| `rightRail` | Audio, DSP, TX, CW groups | RightSidebar | — (tabbed) |
| `bottomDock` | Meters, DX cluster | — | Bottom bar |
| `overlay` | Modals, freq entry, settings | Same | Same |

Skins are not required to fill all slots. Empty slots render nothing.

### Theme architecture

Unchanged from current system — 20+ CSS variable themes, applied via `data-theme` attribute:

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

## Target file structure

```
frontend/src/
├── runtime/                          # Layer 1
│   ├── index.ts                      # createRuntime(), singleton export
│   ├── types.ts                      # FrontendRuntime, controller interfaces
│   ├── audio-controller.ts           # AudioController implementation
│   ├── scope-controller.ts           # ScopeController implementation
│   ├── system-controller.ts          # SystemController implementation
│   ├── command-dispatcher.ts         # cmd() + optimistic patches
│   ├── connection-monitor.ts         # health, reconnect, backoff
│   └── capability-resolver.ts        # normalize caps into stable flags
│
├── adapters/                         # Layer 2
│   ├── index.ts                      # re-export all adapters
│   ├── vfo.ts
│   ├── audio.ts
│   ├── meter.ts
│   ├── scope.ts
│   ├── rf.ts
│   ├── tx.ts
│   ├── memory.ts
│   └── system.ts
│
├── semantic/                         # Layer 3
│   ├── VfoDisplay.svelte
│   ├── RxAudioControl.svelte
│   ├── TxControl.svelte
│   ├── MetersSurface.svelte
│   ├── ScopeSurface.svelte
│   ├── BandSelector.svelte
│   ├── ModeSelector.svelte
│   ├── FilterSelector.svelte
│   ├── RfFrontEnd.svelte
│   ├── DspControl.svelte
│   ├── MemoryControl.svelte
│   └── StatusIndicator.svelte
│
├── primitives/                       # Shared visual atoms
│   ├── SegmentedButton.svelte
│   ├── Knob.svelte
│   ├── LinearMeter.svelte
│   ├── SevenSegment.svelte
│   ├── FrequencyDigits.svelte
│   ├── CanvasRenderer.svelte
│   ├── PanelFrame.svelte
│   └── StatusPill.svelte
│
├── skins/                            # Layer 4 — visual implementations
│   ├── registry.ts                   # resolveSkinId(), loadSkin()
│   ├── desktop-v2/
│   │   ├── DesktopSkin.svelte
│   │   ├── DesktopVfo.svelte
│   │   ├── DesktopMeter.svelte
│   │   └── ...
│   ├── lcd-cockpit/
│   │   ├── LcdCockpitSkin.svelte
│   │   ├── AmberFrequency.svelte
│   │   ├── AmberMeter.svelte
│   │   └── ...
│   └── mobile/
│       ├── MobileSkin.svelte
│       ├── CompactVfo.svelte
│       └── ...
│
├── themes/                           # CSS tokens
│   ├── tokens.css                    # base design tokens
│   ├── dark.css
│   ├── dracula.css
│   ├── nord.css
│   └── ...
│
├── stores/                           # Svelte 5 reactive stores (kept)
│   └── ...                           # migrated into runtime/ over time
│
├── lib/                              # Shared utilities (non-runtime)
│   ├── renderers/                    # Canvas engines (framework-agnostic)
│   ├── utils/                        # Formatting, smoothing, etc.
│   ├── data/                         # ARRL band plan, etc.
│   └── gestures/                     # Touch gesture recognizers
│
└── App.svelte                        # Entry point → skin resolver
```

---

## Architectural invariants

These must hold at all times after migration. Violation = regression.

### INV-1: Single RX playback path

All layouts route through the same `AudioController`. Codec negotiation, WS subscription, decoding, volume, and mute are layout-independent.

### INV-2: Single scope ownership per type

Hardware scope and audio FFT are owned by `ScopeController`. Layouts choose whether and where to render — never whether to connect.

### INV-3: `scope=false + audio=true` is first-class

Absence of hardware scope must never alter audio behavior. LCD fallback is a presentation concern, not a runtime concern.

### INV-4: Capability gating precedes presentation

Panels do not independently decide whether backend capability exists. Runtime/adapters expose stable booleans (`hasLiveAudio`, `hasHardwareScope`, `hasTx`).

### INV-5: Mount/unmount does not change transport

Swapping layouts or mounting/unmounting visual components must not start or stop transports.

### INV-6: No runtime imports in presentation

Presentation components (semantic, skins, primitives) must not import from `runtime/`, `$lib/transport/`, or `$lib/audio/audio-manager`. Enforced by eslint.

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

### eslint enforcement

```jsonc
{
  "rules": {
    "no-restricted-imports": ["error", {
      "patterns": [{
        "group": ["$lib/transport/*", "$lib/audio/audio-manager"],
        "message": "Use adapter props. See ADR 2026-04-12."
      }]
    }]
  },
  "overrides": [{
    "files": ["src/runtime/**", "src/adapters/**"],
    "rules": { "no-restricted-imports": "off" }
  }]
}
```

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

## Migration coexistence

During Phases 4-5, old and new code coexist:

1. New skins live in `skins/` — existing `components-v2/` untouched initially
2. Feature flag `?skin=unified` in `App.svelte` activates new path
3. Old layouts remain default until parity gate passes
4. Phase 6 removes old layouts after confirmation

---

## Evidence gates

| After phase | Gate |
|---|---|
| Phase 0 | RX audio failure classified with evidence; eslint guardrails active |
| Phase 2 | One controller-owned audio/scope path; no presentation-owned WS connections |
| Phase 3 | Semantic component contracts stable for both desktop and LCD |
| Before Phase 6 | All 4 capability scenarios pass; Scenario B (`scope=false + audio=true`) explicitly verified; manual hardware validation |

These phase gates cover the migration. `docs/internals/skins-presentation-boundary-gate.md`
(MOR-2034) is the permanent, post-migration release-gate check for this ADR's
central guarantee — a skin depends only on the presentation layer, and never
derives radio truth locally — naming the mechanisms that enforce it and
recording where their reach currently ends.

---

## Relationship to existing code

| Current code | Target equivalent | Migration action |
|---|---|---|
| `lib/audio/audio-manager.ts` | `runtime/audio-controller.ts` | Refactor into controller with same internal logic |
| `components-v2/wiring/state-adapter.ts` | `adapters/*.ts` | Split into per-domain adapters |
| `components-v2/wiring/command-bus.ts` | `runtime/command-dispatcher.ts` + adapter callbacks | Extract command dispatch to runtime, bind callbacks in adapters |
| `lib/stores/*.svelte.ts` | `runtime/` internal state | Stores become implementation detail of runtime |
| `lib/transport/ws-client.ts` | `runtime/` internal | Transport is private to runtime |
| `components-v2/layout/RadioLayout.svelte` | `skins/desktop-v2/DesktopSkin.svelte` | Rewrite as skin |
| `components-v2/layout/LcdLayout.svelte` | `skins/lcd-cockpit/LcdCockpitSkin.svelte` + `skins/lcd-scope/LcdScopeSkin.svelte` | Rewrite as skin |
| `components-v2/panels/*.svelte` | `semantic/*.svelte` + `skins/*/` visual parts | Split semantic contract from visual implementation |
| `components-v2/theme/` | `themes/` | Move as-is |

---

## Summary

This architecture guarantees:

- **New skin** = new directory in `skins/`, zero changes to runtime/adapters/semantic
- **New radio capability** = changes in runtime + adapter, zero changes to skins
- **Audio bug** = fixed in one place (`runtime/audio-controller.ts`), works identically in all skins
- **Behavioral parity** = enforced architecturally (one runtime, one adapter set) and by eslint
- **No code duplication** = protocol parsing once in runtime, semantic logic once in adapters, visual variants only in skins
