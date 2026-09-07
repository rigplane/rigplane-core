/**
 * Skin registry — resolves which skin to load based on capabilities and user preference.
 *
 * Each skin is a top-level Svelte component that composes semantic components
 * into a layout. Skins are lazy-loaded to keep the initial bundle small.
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md
 */

import type { Component } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { AppResource } from '$lib/runtime/resource-demand';
import type { InstrumentComposition } from '../components-v2/wiring/instrument-composition';
import {
  normalizeLayoutMode,
  type LayoutMode,
} from '$lib/runtime/adapters/layout-mode-adapter';

export type SkinId =
  | 'desktop-v2' | 'dual-receiver-cockpit' | 'lcd-cockpit' | 'lcd-scope' | 'mobile' | 'peer-split'
  | 'sdr-test' | 'dual-sdr-face' | 'unified-instrument' | 'panadapter-first';

export type PresentationHostMode = 'self-contained' | 'instrument-handles';
export type InstrumentHandlesPresentation = Component<{ instruments: InstrumentComposition }>;
export type SelfContainedPresentation = Component;
export type PresentationComponent = InstrumentHandlesPresentation | SelfContainedPresentation;

type BuiltInPresentationRecord<Id extends SkinId> =
  | {
      readonly id: Id;
      readonly kind: 'built-in-instrument-layout';
      readonly loader: () => Promise<{ default: InstrumentHandlesPresentation }>;
      readonly resources: readonly AppResource[];
    }
  | {
      readonly id: Id;
      readonly kind: 'built-in-self-contained';
      readonly loader: () => Promise<{ default: SelfContainedPresentation }>;
      readonly resources: readonly AppResource[];
    };

type BuiltInPresentationCatalog = {
  readonly [Id in SkinId]: BuiltInPresentationRecord<Id>;
};

export function presentationHostMode(id: SkinId): PresentationHostMode {
  return SKIN_LOADERS[id].kind === 'built-in-instrument-layout'
    ? 'instrument-handles'
    : 'self-contained';
}

export interface SkinResolutionContext {
  capabilities: Capabilities | null;
  layoutPreference: LayoutMode;
  isMobile: boolean;
  hasAnyScope: boolean;
}

/**
 * Determine which skin to use based on context.
 *
 * Rules:
 * - Mobile viewport → mobile skin
 * - QA-only: layoutPreference === 'dual-receiver-cockpit' → dual-receiver-cockpit
 *   (MOR-1257 — only reachable via the exact `?layout=dual-receiver-cockpit`
 *   query param; see `lib/stores/qa-cockpit-override.ts`)
 * - User forced 'sdr-test' → sdr-test
 * - User forced 'lcd' or 'lcd-cockpit' → lcd-cockpit
 * - User forced 'lcd-scope' → lcd-scope
 * - User forced 'standard' → desktop-v2
 * - User forced 'peer-split' → peer-split
 * - Auto: use desktop-v2 (the v3 default); explicit LCD choices are the
 *   recoverable compatibility-window opt-out
 */
export function resolveSkinId(ctx: SkinResolutionContext): SkinId {
  if (ctx.isMobile) return 'mobile';
  // MOR-1257: interim QA reachability. `ctx.layoutPreference` can only be
  // this value when the caller resolved it from the exact
  // `?layout=dual-receiver-cockpit` query param — it is deliberately NOT a
  // `CanonicalLayoutMode` (lib/stores/layout.svelte.ts), so
  // `normalizeLayoutMode` below would fall it straight through to 'auto'.
  // Checked here, before normalization, for that reason.
  if (ctx.layoutPreference === 'dual-receiver-cockpit') return 'dual-receiver-cockpit';
  const layoutPreference = normalizeLayoutMode(ctx.layoutPreference);
  if (layoutPreference === 'sdr-test') return 'sdr-test';
  if (layoutPreference === 'lcd-cockpit') return 'lcd-cockpit';
  if (layoutPreference === 'lcd-scope') return 'lcd-scope';
  if (layoutPreference === 'standard') return 'desktop-v2';
  if (layoutPreference === 'peer-split') return 'peer-split';
  if (layoutPreference === 'unified-instrument') return 'unified-instrument';
  if (layoutPreference === 'panadapter-first') return 'panadapter-first';
  if (layoutPreference === 'dual-sdr-face') return 'dual-sdr-face';
  // MOR-1097 cutover: every non-mobile auto start uses the reworked
  // desktop-v2 composition. Scope availability remains presentation data, not
  // default-selection policy; explicit LCD preferences stay selectable.
  return 'desktop-v2';
}

/**
 * One total built-in catalog keeps the lazy loader, host contract and resource
 * bridge plan on the same discriminated record. A new SkinId cannot silently
 * inherit any of those three decisions from a separate table.
 *
 * Resource membership only permits bridging: `App.svelte` bridges a resource
 * solely while it is already demanded, so presentation choice cannot start a
 * service. `rx-audio` is absent because the runtime owns that lease.
 * Loaders remain lazy, so only the selected skin's entry component is loaded.
 */
const SKIN_LOADERS = {
  'desktop-v2': {
    id: 'desktop-v2',
    kind: 'built-in-instrument-layout',
    loader: () => import('./desktop-v2/DesktopSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  // MOR-1068 (F8): the cockpit's layout manifest registers under this exact
  // id, so it needs the matching loadable SkinId — it was the only registered
  // manifest the App had no way to load. `resolveSkinId` does not yet return
  // it (that needs a `LayoutMode` preference and a picker affordance, tracked
  // separately); the entry here is what makes the id addressable at all.
  'dual-receiver-cockpit': {
    id: 'dual-receiver-cockpit',
    kind: 'built-in-self-contained',
    loader: () => import('./dual-receiver-cockpit/DualReceiverCockpit.svelte'),
    resources: [],
  },
  'lcd-cockpit': {
    id: 'lcd-cockpit',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-cockpit/LcdCockpitSkin.svelte'),
    resources: ['audio-fft'],
  },
  'lcd-scope': {
    id: 'lcd-scope',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-scope/LcdScopeSkin.svelte'),
    resources: ['audio-fft'],
  },
  'mobile': {
    id: 'mobile',
    kind: 'built-in-self-contained',
    loader: () => import('./mobile/MobileSkin.svelte'),
    resources: ['hardware-scope'],
  },
  // MOR-2155 made `peer-split` addressable and loadable; MOR-2152 (see the
  // `resolveSkinId` branch below) is what makes a forced 'peer-split'
  // preference actually resolve to it. MOR-2153 PR-1 retargeted the loader
  // from the bare glass (`segmentline/PeerSplitLayout.svelte`) to the LCD
  // shell wrapper: `lcd-peer-split/LcdPeerSplitSkin.svelte` mounts
  // `LcdLayout` with `variant="peer-split"`, which renders the glass inside
  // the shell rather than loading it standalone.
  'peer-split': {
    id: 'peer-split',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-peer-split/LcdPeerSplitSkin.svelte'),
    resources: ['audio-fft'],
  },
  'unified-instrument': {
    id: 'unified-instrument',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-unified-instrument/LcdUnifiedInstrumentSkin.svelte'),
    resources: ['audio-fft'],
  },
  'panadapter-first': {
    id: 'panadapter-first',
    kind: 'built-in-self-contained',
    loader: () => import('./lcd-panadapter-first/LcdPanadapterFirstSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  'sdr-test': {
    id: 'sdr-test',
    kind: 'built-in-instrument-layout',
    loader: () => import('./sdr-test/SdrTestSkin.svelte'),
    resources: ['hardware-scope', 'audio-fft'],
  },
  'dual-sdr-face': {
    id: 'dual-sdr-face',
    kind: 'built-in-self-contained',
    loader: () => import('./dual-sdr-face/DualSdrFaceSkin.svelte'),
    resources: ['hardware-scope'],
  },
} satisfies BuiltInPresentationCatalog;

type LoadedPresentation<Id extends SkinId> =
  Awaited<ReturnType<(typeof SKIN_LOADERS)[Id]['loader']>>['default'];

export function loadSkin<Id extends SkinId>(id: Id): Promise<LoadedPresentation<Id>>;
export async function loadSkin(id: SkinId): Promise<PresentationComponent> {
  return (await SKIN_LOADERS[id].loader()).default;
}

/**
 * Which App-session resources a presentation's subtree can demand (MOR-1060).
 *
 * This is a private composition-root detail, NOT a public workspace or
 * design-language API: it exists so `App.svelte` can hold demand across a
 * presentation swap and stop the outgoing subtree's release from bouncing a
 * stream the incoming subtree is about to ask for again.
 *
 * Read off the actual component trees:
 * - `hardware-scope` — `SpectrumPanel`, mounted by the desktop/sdr-test and
 *   mobile layouts, plus the selected display in `panadapter-first`.
 * - `audio-fft` — `AudioSpectrumPanel` (right sidebar, desktop/sdr-test/LCD,
 *   `RightSidebar.svelte` behind `hasAudioFft()`) and `AmberCockpit` /
 *   `AmberScope` (LCD `cockpit`/`scope`); `peer-split` demands it purely
 *   through the same `RightSidebar` it now shares with `cockpit`/`scope` —
 *   `unified-instrument` selects that source for its display. The
 *   `panadapter-first` LCD shell still contains the existing AF inset in its
 *   right sidebar even though the selected display uses hardware scope. The
 *   mobile layout has none.
 *
 * `rx-audio` is deliberately absent: its lease is held by the runtime
 * (`setRxLive`), not by a presentation subtree, so it already survives a swap.
 *
 * Membership here only permits bridging — `App.svelte` bridges a resource
 * solely while it is already demanded, so a presentation choice can never
 * manufacture a live service (v3 ADR invariant 12).
 */
export function presentationResourcePlan(id: SkinId): readonly AppResource[] {
  return SKIN_LOADERS[id]?.resources ?? [];
}
