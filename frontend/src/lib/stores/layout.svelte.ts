/**
 * Layout preference — since MOR-1081 a thin façade over the single workspace
 * store (`presentation/workspace/store.svelte.ts`), which owns the selection,
 * its validation and its persistence.
 *
 * 'auto'        = desktop-v2 (the v3 default) on every non-mobile viewport
 * 'lcd'         = legacy alias for 'lcd-cockpit'
 * 'lcd-cockpit' = force LCD cockpit (TS-990S-style dual-cockpit)
 * 'lcd-scope'   = force LCD scope (IC-7300-style scope-dominant)
 * 'standard'    = force standard layout
 * 'dual-receiver-cockpit' = QA-ONLY (MOR-1257): reachable solely via the
 *                 exact `?layout=dual-receiver-cockpit` query param (see
 *                 `lib/stores/qa-cockpit-override.ts`) — deliberately NOT a
 *                 CanonicalLayoutMode, so normalizeLayoutMode below falls it
 *                 through to 'auto'. It can therefore never be persisted via
 *                 setLayoutMode/the workspace and never appears in the
 *                 StatusBar skin selector.
 *
 * SINGLE WRITER (MOR-1081). This module no longer reads or writes
 * `rigplane-layout` / `rigplane-skin`. The legacy keys are neither written nor
 * deleted: MOR-1079's repository froze them at their migration-time value so
 * an older build can still READ them during the rollback window, while the
 * workspace object is the only thing this build writes. Reconciliation is
 * therefore "the workspace wins, once migrated" by construction — the legacy
 * keys are simply off the selection path.
 *
 * `normalizeLayoutMode` stays here, pure and unchanged: MOR-1042's canonical
 * alias behavior is the contract several callers (skins/registry.ts,
 * StatusBar) depend on, and the workspace validator applies the same alias
 * table on its own read path.
 */
import {
  getWorkspace,
  setLayout as setWorkspaceLayout,
} from '../../presentation/workspace/store.svelte';

export type LayoutMode =
  | 'auto' | 'lcd' | 'lcd-cockpit' | 'lcd-scope' | 'standard' | 'sdr-test'
  | 'dual-receiver-cockpit';
export type CanonicalLayoutMode = Exclude<LayoutMode, 'lcd' | 'dual-receiver-cockpit'>;

const CANONICAL_LAYOUT_MODES = new Set<CanonicalLayoutMode>([
  'auto',
  'lcd-cockpit',
  'lcd-scope',
  'standard',
  'sdr-test',
]);

const LEGACY_LAYOUT_ALIASES: Record<string, CanonicalLayoutMode> = {
  lcd: 'lcd-cockpit',
  'amber-lcd': 'lcd-cockpit',
  spectrum: 'standard',
  'desktop-v2': 'standard',
};

export function normalizeLayoutMode(value: 'lcd' | 'amber-lcd'): 'lcd-cockpit';
export function normalizeLayoutMode(value: 'spectrum' | 'desktop-v2'): 'standard';
export function normalizeLayoutMode(value: unknown): CanonicalLayoutMode;
export function normalizeLayoutMode(value: unknown): CanonicalLayoutMode {
  if (typeof value !== 'string') return 'auto';
  if (Object.hasOwn(LEGACY_LAYOUT_ALIASES, value)) {
    return LEGACY_LAYOUT_ALIASES[value];
  }
  if (CANONICAL_LAYOUT_MODES.has(value as CanonicalLayoutMode)) {
    return value as CanonicalLayoutMode;
  }
  return 'auto';
}

/** Reactive: the workspace store's `$state` is the backing cell. */
export function getLayoutMode(): LayoutMode {
  return getWorkspace().layout;
}

export function setLayoutMode(m: LayoutMode): void {
  setWorkspaceLayout(normalizeLayoutMode(m));
}

export function cycleLayoutMode(hasAnyScope: boolean): void {
  if (hasAnyScope) {
    // auto → LCD cockpit → standard → auto
    const order: CanonicalLayoutMode[] = ['auto', 'lcd-cockpit', 'standard'];
    const idx = order.indexOf(normalizeLayoutMode(getLayoutMode()));
    setLayoutMode(order[(idx + 1) % order.length]);
  } else {
    // No scope: the legacy cycle shortcut explicitly selects LCD. This is an
    // opt-out from the auto desktop-v2 default, not auto's resolution policy.
    setLayoutMode('lcd-cockpit');
  }
}

// useLcdLayout() removed — layout resolution now handled by skins/registry.ts resolveSkinId()
