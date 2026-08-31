# Presentation Architecture For Skins, Themes, And Layout Variants

**Date:** 2026-04-11
**Status:** Draft for discussion
**Issue:** `#646`
**Depends on:** `#643`
**Base commit:** `f0e78cc0ea0fcfbafdadbb4a20167ba0bc110d6a`

## Objective

Define the ideal presentation architecture above the shared runtime so the app can support:

- standard `v2`
- LCD
- future skins
- small theme changes
- larger component-level visual swaps

without reintroducing logic drift.

## Core definitions

### Runtime

The stateful side-effect layer:

- backend transport
- audio/scope lifecycle
- commands
- capability normalization
- optimistic updates

Runtime must be presentation-agnostic.

### View-model adapter

A pure mapping layer:

- runtime state -> semantic UI props
- runtime actions -> semantic callbacks

Adapters know what the UI means, but not how it looks.

### Semantic component

A component representing a radio concept with stable behavior contract.

Examples:

- `VfoDisplay`
- `RxAudioControl`
- `ScopeSurface`
- `MeterPanel`
- `TxControl`

Semantic components receive semantic props and callbacks only.

### Primitive component

A low-level visual building block with no radio semantics.

Examples:

- button
- segmented control
- gauge
- card
- label/value row
- LED indicator

### Theme

A token set for colors, typography, spacing, shadows, borders, motion.

Theme changes appearance without changing component identity or layout structure.

### Skin

A mapping from semantic components to concrete visual implementations.

Examples:

- `industrial-v2`
- `amber-lcd`
- future `rack`, `retro`, `minimal`, `touch`

A skin may swap component internals, but must preserve semantic component contracts.

### Layout variant

A screen composition over semantic components.

Examples:

- desktop standard
- desktop LCD
- mobile
- compact monitor

Layout variants choose placement and grouping, not runtime behavior.

## Recommended layering

Dependency direction should be one-way:

`runtime -> adapters -> semantic components -> skin implementations -> primitives -> theme tokens`

And separately:

`layout variant -> semantic components`

Important:

- runtime must not import skins/themes/layouts
- themes must not know about runtime
- skin implementations must not talk to runtime directly
- layout variants must compose semantic components, not transports

## The three presentation knobs

These should be first-class and separate.

### 1. Theme = token switch

Use theme when only visual language changes:

- colors
- font families
- spacing scale
- border radius
- shadows
- animation style

Theme should be mostly CSS variables and token objects.

### 2. Skin = semantic renderer switch

Use skin when the same radio concept needs a different visual implementation:

- classic seven-segment frequency vs industrial frequency strip
- analog S-meter vs LCD bar meter
- hardware-style buttons vs flat touch controls

Skin swaps renderers, not behavior contracts.

### 3. Layout variant = composition switch

Use layout variants when the same semantic components need different arrangement:

- sidebars vs stacked mobile panels
- LCD center panel vs wide spectrum center panel
- docked meters vs embedded meters

Layout variants decide:

- which semantic components appear
- where they appear
- in which slot/group they appear

They must not decide:

- how to connect to backend transports
- how to decode audio
- how to dispatch commands

## Semantic component contract

Each semantic component should expose:

- semantic props only
- semantic callbacks only
- no transport knowledge
- no backend endpoint knowledge
- no concrete skin assumptions

Example:

`RxAudioControl` should receive:

- `monitorMode`
- `hasLiveAudio`
- `level`
- `onMonitorModeChange`
- `onLevelChange`

It should not know:

- `audioManager`
- `/api/v1/audio`
- codec names
- `sendCommand(...)`

## Skin architecture

Each skin should provide a component registry for semantic components.

Example shape:

- `skins/industrial-v2/registry.ts`
- `skins/amber-lcd/registry.ts`

Each registry maps semantic component ids to concrete implementations.

Example:

- `VfoDisplay -> IndustrialVfoDisplay`
- `MeterPanel -> NeedleMeterPanel`
- `RxAudioControl -> HardwareRxAudioPanel`

Another skin might map:

- `VfoDisplay -> AmberFrequencyDisplay`
- `MeterPanel -> AmberMeterStrip`
- `RxAudioControl -> LcdAudioStrip`

The semantic API stays the same.

## Primitive architecture

Primitives should be reusable visual building blocks, not "business logic widgets".

Good primitives:

- `ControlButton`
- `SegmentedControl`
- `Gauge`
- `Knob`
- `PanelFrame`
- `StatusPill`

Bad primitives:

- `WsAwareButton`
- `ScopeConnectedIndicator` that reads transport state itself
- `PttButton` that imports `audioManager`

## Slots and composition

Layouts should compose semantic components via named slots/zones.

Recommended slot model:

- `header`
- `leftRail`
- `centerPrimary`
- `centerSecondary`
- `rightRail`
- `bottomDock`
- `overlay`

Each layout variant assigns semantic components into these slots.

This keeps composition flexible without inventing new runtime paths.

## How standard V2 and LCD should differ in the target system

### Standard desktop

- skin: `industrial-v2`
- theme: one of the current token themes
- layout: `desktop-standard`

Composition:

- `centerPrimary` -> `HardwareScopeSurface`
- `header` -> `VfoHeader`
- `leftRail` -> semantic control groups
- `rightRail` -> semantic audio/DSP/TX groups
- `bottomDock` -> compact summaries/meters

### LCD desktop

- skin: `amber-lcd`
- theme: LCD token theme
- layout: `desktop-lcd`

Composition:

- `centerPrimary` -> `LcdReceiverDisplay`
- `centerSecondary` -> optional semantic scope surface if available
- same semantic sidebars or LCD-specific semantic groups

Important:

Both layouts still consume the same:

- `RxAudioControl` behavior contract
- `ScopeSurface` runtime data
- `TxControl` callbacks
- capability-derived view models

## Extension points that should be first-class

These are worth designing for explicitly.

### First-class

- theme tokens
- skin registry
- layout slot composition
- semantic component variants
- renderer swapping for meters/frequency/status blocks

### Deliberately not first-class

- per-skin transport ownership
- per-layout command dispatch
- per-component capability policy
- per-skin codec or audio lifecycle decisions

If an extension point can fork behavior, it belongs below the presentation layer or should be forbidden.

## Suggested directory model

One reasonable end state:

- `frontend/src/runtime/`
- `frontend/src/adapters/`
- `frontend/src/semantic/`
- `frontend/src/skins/<skin-id>/`
- `frontend/src/primitives/`
- `frontend/src/themes/`
- `frontend/src/layouts/`

Meaning:

- `runtime/` side effects and controllers
- `adapters/` pure mappers
- `semantic/` stable radio concepts
- `skins/` concrete renderers
- `primitives/` reusable low-level visuals
- `themes/` tokens
- `layouts/` screen composition

## Anti-patterns to avoid

### Anti-pattern 1

Skin components importing runtime modules directly.

That turns skins into hidden behavior forks.

### Anti-pattern 2

Theme objects containing behavior flags.

Theme is for tokens, not feature semantics.

### Anti-pattern 3

Layout deciding transport ownership based on whether a panel is mounted.

This is the current source of drift around scope/audio ownership.

### Anti-pattern 4

"Flexible" components that accept both semantic props and backend-specific escape hatches.

That destroys the semantic boundary over time.

### Anti-pattern 5

Treating LCD as a special-case app instead of a skin/layout expression over shared runtime.

## Design principles

### Principle 1

Optimize for correctness before flexibility.

The architecture should make the wrong thing hard.

### Principle 2

Different visuals should reuse the same semantics.

If two skins need different behavior contracts, the semantic component boundary is wrong.

### Principle 3

Presentation extensibility should come from composition, not from conditional logic spread across components.

### Principle 4

Model-assisted development needs hard boundaries.

If a future model can solve a UI request by importing `sendCommand(...)` into a panel, the architecture is not strict enough.

## Concrete TypeScript interfaces

### FrontendRuntime

```typescript
interface FrontendRuntime {
  // Reactive state (Svelte 5 $state under the hood)
  readonly state: RadioState;
  readonly capabilities: Capabilities;
  readonly connection: ConnectionStatus;

  // Controller facades
  readonly audio: AudioController;
  readonly scope: ScopeController;
  readonly system: SystemController;

  // Command dispatch (single entry point)
  cmd(command: string, params?: Record<string, unknown>): void;
}

interface AudioController {
  readonly rxEnabled: boolean;
  readonly txEnabled: boolean;
  readonly monitorMode: 'live' | 'radio' | 'mute';
  readonly volume: number;          // 0..1, browser playback volume
  readonly wsConnected: boolean;
  readonly txSupported: boolean;

  setMonitorMode(mode: 'live' | 'radio' | 'mute'): void;
  setVolume(v: number): void;
  startTx(): Promise<string | null>;
  stopTx(): void;
}

interface ScopeController {
  readonly hardwareScopeAvailable: boolean;
  readonly audioScopeAvailable: boolean;
  readonly activeScope: 'hardware' | 'audio-fft' | null;

  // Parsed scope data, ready for rendering
  readonly scopeFrame: ScopeFrame | null;
  readonly audioScopeFrame: AudioScopeFrame | null;
}

interface SystemController {
  powerOn(): Promise<void>;
  powerOff(): Promise<void>;
  connect(): Promise<void>;
  disconnect(): Promise<void>;
  identifyFrequency(freqHz: number): Promise<EibiResult | null>;
}
```

### View-model adapter contract

```typescript
// Adapters are pure functions — no side effects, no subscriptions.
// They receive runtime state and return typed view-model objects.

type Adapter<T> = (
  state: RadioState,
  caps: Capabilities,
  audio: AudioController,
  receiver?: 'main' | 'sub',
) => T;

// Example: VFO adapter
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

const toVfoProps: Adapter<VfoViewModel> = (state, caps, audio, receiver) => {
  // pure derivation — no imports from transport, audio, or WS
};

// Example: RX audio adapter
interface RxAudioViewModel {
  monitorMode: 'live' | 'radio' | 'mute';
  hasLiveAudio: boolean;
  volume: number;
  muted: boolean;
  onMonitorModeChange: (mode: string) => void;
  onVolumeChange: (v: number) => void;
}
```

### Semantic component contract (Svelte 5)

```svelte
<!-- semantic/RxAudioControl.svelte -->
<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    monitorMode: 'live' | 'radio' | 'mute';
    hasLiveAudio: boolean;
    volume: number;
    muted: boolean;
    onMonitorModeChange: (mode: string) => void;
    onVolumeChange: (v: number) => void;
    // Skin provides rendering via snippet
    children: Snippet;
  }

  let {
    monitorMode, hasLiveAudio, volume, muted,
    onMonitorModeChange, onVolumeChange, children,
  }: Props = $props();
</script>

<!-- Semantic component provides behavioral context;
     skin snippet provides visual rendering -->
{@render children()}
```

## Svelte 5 skin mechanism

### Why not a component registry

Traditional component registries (map of string → component class) require dynamic component instantiation, which in Svelte 5 means `{@component}` or `svelte:component`. This adds complexity and loses static type checking.

### Recommended: skin as a layout-level composition

Each skin is a concrete Svelte component that:

1. Imports the semantic components it needs
2. Imports its own visual primitives
3. Composes them into a layout using typed props

```svelte
<!-- skins/lcd-cockpit/LcdCockpitSkin.svelte -->
<script lang="ts">
  import type { FrontendRuntime } from '../../runtime/types';
  import { toVfoProps, toRxAudioProps, toMeterProps } from '../../adapters';

  // Skin-specific visual components
  import AmberFrequency from './AmberFrequency.svelte';
  import AmberMeter from './AmberMeter.svelte';
  import AmberAudioStrip from './AmberAudioStrip.svelte';
  import LcdFrame from './LcdFrame.svelte';

  interface Props {
    runtime: FrontendRuntime;
  }
  let { runtime }: Props = $props();

  // Derive view-models from runtime (reactive via $derived)
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

### Skin resolution

```typescript
// skins/registry.ts
import type { Component } from 'svelte';
import type { FrontendRuntime } from '../runtime/types';

export type SkinId = 'desktop-v2' | 'amber-lcd' | 'mobile';

interface SkinResolution {
  capabilities: Capabilities;
  userPreference: 'auto' | 'lcd' | 'standard';
  isMobile: boolean;
}

export function resolveSkinId(ctx: SkinResolution): SkinId {
  if (ctx.isMobile) return 'mobile';
  if (ctx.userPreference === 'lcd') return 'amber-lcd';
  if (ctx.userPreference === 'standard') return 'desktop-v2';
  // auto: use LCD when no hardware scope
  return ctx.capabilities.scope ? 'desktop-v2' : 'amber-lcd';
}

// Lazy-load skin components to keep bundle size manageable
const SKIN_LOADERS: Record<SkinId, () => Promise<{ default: Component<{ runtime: FrontendRuntime }> }>> = {
  'desktop-v2': () => import('./desktop-v2/DesktopSkin.svelte'),
  'amber-lcd':  () => import('./lcd-cockpit/LcdCockpitSkin.svelte'),
  'mobile':     () => import('./mobile/MobileSkin.svelte'),
};

export async function loadSkin(id: SkinId) {
  return (await SKIN_LOADERS[id]()).default;
}
```

### App entry point with skin resolution

```svelte
<!-- App.svelte (target state) -->
<script lang="ts">
  import { runtime } from './runtime';
  import { resolveSkinId, loadSkin } from './skins/registry';

  const skinId = $derived(resolveSkinId({
    capabilities: runtime.capabilities,
    userPreference: runtime.layoutPreference,
    isMobile: runtime.isMobile,
  }));

  // Reactive skin loading — changes when skinId changes
  let SkinComponent = $state<Component | null>(null);
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

## Layout slot model (concrete)

Each skin's top-level layout should use named slots for composition:

```typescript
interface LayoutSlots {
  header:           Component | null;  // VFO, status, connection
  leftRail:         Component | null;  // control groups (mode, band, filter)
  centerPrimary:    Component | null;  // scope surface or LCD display
  centerSecondary:  Component | null;  // secondary scope or audio FFT
  rightRail:        Component | null;  // audio, DSP, TX controls
  bottomDock:       Component | null;  // meters, compact summaries
  overlay:          Component | null;  // modals, freq entry, settings
}
```

Skins are not obligated to fill all slots. LCD fills `centerPrimary` with `AmberLcdDisplay` and may leave `centerSecondary` empty. Desktop fills both with scope + waterfall. Mobile may collapse rails into tabbed panels.

## Coexistence during migration

During Phases 4-5, old and new code must coexist:

### Strategy: parallel skin alongside existing layout

1. New skin components live in `skins/` — no changes to existing `components-v2/`
2. A feature flag (`?skin=unified`) enables the new path in `App.svelte`
3. Old layout components remain the default until parity gate passes
4. Once parity is confirmed, the flag defaults to `unified` and old layouts are deprecated
5. Phase 6 removes old layout components

### Import boundary enforcement

```jsonc
// .eslintrc (or eslint.config.js) — enforced from Phase 0
{
  "rules": {
    "no-restricted-imports": ["error", {
      "patterns": [
        {
          "group": ["$lib/transport/*", "$lib/audio/audio-manager"],
          "importNames": ["sendCommand", "getChannel", "audioManager"],
          "message": "Presentation components must not import runtime modules. Use adapter props instead."
        }
      ]
    }]
  },
  "overrides": [
    {
      "files": ["src/runtime/**", "src/adapters/**", "src/components-v2/wiring/**"],
      "rules": { "no-restricted-imports": "off" }
    }
  ]
}
```

## Testing skin parity

### Behavioral parity (automated)

Semantic component tests verify that props → rendered output is correct regardless of which skin wraps them. These tests use the semantic component directly with mock props.

### Visual parity (semi-automated)

Skins are not expected to look identical. Visual testing should verify:

1. All required props are consumed (TypeScript compilation catches this)
2. Interaction callbacks fire correctly (component tests)
3. No visual regressions within a single skin (Playwright screenshot diffing per skin)

Cross-skin visual comparison is explicitly **not** a goal — skins exist to look different.

## Provisional conclusion

The ideal frontend is not "one mega theme system". It is a layered system with three independent presentation controls:

- theme changes tokens
- skin changes semantic renderers
- layout variant changes composition

All three sit on top of one shared runtime and one semantic contract. That is the only shape that gives both visual freedom and behavioral stability.

The Svelte 5 implementation uses:

- `$derived()` for reactive adapter-to-view-model binding
- concrete skin components (not dynamic registries) for type safety
- lazy `import()` for skin loading to manage bundle size
- `$effect()` for reactive skin switching
- eslint import boundaries for architectural enforcement
