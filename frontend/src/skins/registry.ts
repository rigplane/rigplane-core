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
import {
  normalizeLayoutMode,
  type LayoutMode,
} from '$lib/stores/layout.svelte';

export type SkinId = 'desktop-v2' | 'lcd-cockpit' | 'lcd-scope' | 'mobile' | 'sdr-test';

/**
 * Persisted skin IDs include the legacy `amber-lcd` alias, which routes to
 * `lcd-cockpit`. Kept indefinitely for backwards compatibility with stored
 * user preferences (see docs/plans/archive/2026-04-19-lcd-twin-skins.md §2).
 */
export type PersistedSkinId = SkinId | 'amber-lcd';

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
 * - User forced 'lcd' or 'lcd-cockpit' → lcd-cockpit
 * - User forced 'lcd-scope' → lcd-scope
 * - User forced 'standard' → desktop-v2
 * - Auto: use desktop-v2 if any scope is available, lcd-cockpit otherwise
 */
export function resolveSkinId(ctx: SkinResolutionContext): SkinId {
  if (ctx.isMobile) return 'mobile';
  const layoutPreference = normalizeLayoutMode(ctx.layoutPreference);
  if (layoutPreference === 'sdr-test') return 'sdr-test';
  if (layoutPreference === 'lcd-cockpit') return 'lcd-cockpit';
  if (layoutPreference === 'lcd-scope') return 'lcd-scope';
  if (layoutPreference === 'standard') return 'desktop-v2';
  // auto: scope available → desktop, otherwise LCD
  return ctx.hasAnyScope ? 'desktop-v2' : 'lcd-cockpit';
}

/**
 * Lazy-load a skin component by ID.
 *
 * Returns the default export of the skin's entry Svelte component.
 * Skins are code-split — only the active skin is loaded.
 *
 * Each LCD variant has its own wrapper that mounts `LcdLayout` with the
 * appropriate `variant` prop — this is how the cockpit/scope selection
 * reaches LcdLayout (registry → skin wrapper → LcdLayout). The legacy
 * `amber-lcd` alias is accepted via `resolvePersistedSkinId()`.
 */
const SKIN_LOADERS: Record<SkinId, () => Promise<{ default: Component }>> = {
  'desktop-v2': () => import('./desktop-v2/DesktopSkin.svelte'),
  'lcd-cockpit': () => import('./lcd-cockpit/LcdCockpitSkin.svelte'),
  'lcd-scope': () => import('./lcd-scope/LcdScopeSkin.svelte'),
  'mobile': () => import('./mobile/MobileSkin.svelte'),
  'sdr-test': () => import('./sdr-test/SdrTestSkin.svelte'),
};

/**
 * Normalize a persisted skin ID, mapping the legacy `amber-lcd` alias to
 * `lcd-cockpit`. Use this when reading from localStorage or other stored
 * preferences.
 */
export function resolvePersistedSkinId(id: PersistedSkinId): SkinId {
  if (id === 'amber-lcd') return normalizeLayoutMode(id);
  return id;
}

export async function loadSkin(id: SkinId): Promise<Component> {
  return (await SKIN_LOADERS[id]()).default;
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
 *   mobile layouts; the LCD layouts have none.
 * - `audio-fft` — `AudioSpectrumPanel` (right sidebar, desktop/sdr-test/LCD)
 *   and `AmberCockpit` / `AmberScope` (LCD); the mobile layout has none.
 *
 * `rx-audio` is deliberately absent: its lease is held by the runtime
 * (`setRxLive`), not by a presentation subtree, so it already survives a swap.
 *
 * Membership here only permits bridging — `App.svelte` bridges a resource
 * solely while it is already demanded, so a presentation choice can never
 * manufacture a live service (v3 ADR invariant 12).
 */
const SKIN_RESOURCE_PLAN: Record<SkinId, readonly AppResource[]> = {
  'desktop-v2': ['hardware-scope', 'audio-fft'],
  'lcd-cockpit': ['audio-fft'],
  'lcd-scope': ['audio-fft'],
  'mobile': ['hardware-scope'],
  'sdr-test': ['hardware-scope', 'audio-fft'],
};

export function presentationResourcePlan(id: SkinId): readonly AppResource[] {
  return SKIN_RESOURCE_PLAN[id] ?? [];
}
