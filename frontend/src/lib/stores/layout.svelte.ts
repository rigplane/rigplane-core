/**
 * Layout preference store.
 * 'auto'        = standard layout when any scope available (HW or audio FFT), LCD otherwise
 * 'lcd'         = legacy alias for 'lcd-cockpit'
 * 'lcd-cockpit' = force LCD cockpit (TS-990S-style dual-cockpit)
 * 'lcd-scope'   = force LCD scope (IC-7300-style scope-dominant)
 * 'standard'    = force standard layout
 *
 * Raw persisted aliases are normalized here before presentation policy reads
 * the preference.
 */

const STORAGE_KEY = 'rigplane-layout';
const LEGACY_SKIN_STORAGE_KEY = 'rigplane-skin';

export type LayoutMode = 'auto' | 'lcd' | 'lcd-cockpit' | 'lcd-scope' | 'standard' | 'sdr-test';
export type CanonicalLayoutMode = Exclude<LayoutMode, 'lcd'>;

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

let mode = $state<CanonicalLayoutMode>(loadMode());

function loadMode(): CanonicalLayoutMode {
  if (typeof window === 'undefined') return 'auto';
  const saved = localStorage.getItem(STORAGE_KEY)
    ?? localStorage.getItem(LEGACY_SKIN_STORAGE_KEY);
  return normalizeLayoutMode(saved);
}

export function getLayoutMode(): LayoutMode {
  return mode;
}

export function setLayoutMode(m: LayoutMode): void {
  mode = normalizeLayoutMode(m);
  if (typeof window !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, mode);
  }
}

export function cycleLayoutMode(hasAnyScope: boolean): void {
  if (hasAnyScope) {
    // auto → LCD cockpit → standard → auto
    const order: CanonicalLayoutMode[] = ['auto', 'lcd-cockpit', 'standard'];
    const idx = order.indexOf(mode);
    setLayoutMode(order[(idx + 1) % order.length]);
  } else {
    // No scope at all: always LCD, no toggle needed
    setLayoutMode('lcd-cockpit');
  }
}

// useLcdLayout() removed — layout resolution now handled by skins/registry.ts resolveSkinId()
