/**
 * Layout preference store.
 * 'auto'        = standard layout when any scope available (HW or audio FFT), LCD otherwise
 * 'lcd'         = force LCD layout — cockpit variant (legacy alias for 'lcd-cockpit')
 * 'lcd-cockpit' = force LCD cockpit (TS-990S-style dual-cockpit)
 * 'lcd-scope'   = force LCD scope (IC-7300-style scope-dominant)
 * 'standard'    = force standard layout
 *
 * Legacy 'lcd' persisted values are mapped to 'lcd-cockpit' by resolveSkinId().
 */

const STORAGE_KEY = 'rigplane-layout';

export type LayoutMode = 'auto' | 'lcd' | 'lcd-cockpit' | 'lcd-scope' | 'standard' | 'sdr-test';

let mode = $state<LayoutMode>(loadMode());

function loadMode(): LayoutMode {
  if (typeof window === 'undefined') return 'auto';
  const saved = localStorage.getItem(STORAGE_KEY);
  if (saved === 'lcd' || saved === 'lcd-cockpit' || saved === 'lcd-scope' || saved === 'standard' || saved === 'sdr-test') {
    return saved;
  }
  // Migrate old 'spectrum' value to 'standard'
  if (saved === 'spectrum') return 'standard';
  return 'auto';
}

export function getLayoutMode(): LayoutMode {
  return mode;
}

export function setLayoutMode(m: LayoutMode): void {
  mode = m;
  if (typeof window !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, m);
  }
}

export function cycleLayoutMode(hasAnyScope: boolean): void {
  if (hasAnyScope) {
    // auto → lcd → standard → auto
    const order: LayoutMode[] = ['auto', 'lcd', 'standard'];
    const idx = order.indexOf(mode);
    setLayoutMode(order[(idx + 1) % order.length]);
  } else {
    // No scope at all: always LCD, no toggle needed
    setLayoutMode('lcd');
  }
}

// useLcdLayout() removed — layout resolution now handled by skins/registry.ts resolveSkinId()
