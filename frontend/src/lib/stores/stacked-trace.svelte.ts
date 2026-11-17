/**
 * Stacked-trace ("3D") panadapter preference (MOR-4463).
 *
 * The flat spectrum is the default; the stacked view is on only after the
 * user switched it on, and the choice is kept per browser. `unavailable` is
 * set for the page's lifetime when the browser could not give the view a
 * WebGL2 context: the toggle then stays off and says why.
 */

import { parseStackedTraceSettings, type StackedTraceSettings } from '../renderers/stacked-trace-geometry';

const STORAGE_KEY = 'rigplane-spectrum-stacked-trace';
const SETTINGS_KEY = 'rigplane-spectrum-stacked-trace-settings';

function load(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem?.(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

/** The user's own look of the view; whatever is stored passes the settings parser. */
function loadSettings(): StackedTraceSettings {
  let stored: unknown = null;
  try {
    stored = typeof window === 'undefined' ? null : JSON.parse(localStorage.getItem?.(SETTINGS_KEY) ?? 'null');
  } catch {
    /* unreadable settings are the defaults */
  }
  return parseStackedTraceSettings(stored);
}

let enabled = $state(load());
let settings = $state.raw(loadSettings());
let unavailable = $state(false);

export function isStackedTraceEnabled(): boolean {
  return enabled;
}

export function setStackedTraceEnabled(next: boolean): void {
  enabled = next;
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem?.(STORAGE_KEY, next ? '1' : '0');
  } catch {
    /* persistence is best-effort */
  }
}

export function isStackedTraceUnavailable(): boolean {
  return unavailable;
}

export function markStackedTraceUnavailable(): void {
  unavailable = true;
}

export function stackedTraceSettings(): StackedTraceSettings {
  return settings;
}

/** Changes some settings, kept per browser; `null` returns to the defaults. */
export function setStackedTraceSettings(change: Partial<StackedTraceSettings> | null): void {
  settings = parseStackedTraceSettings(change === null ? null : { ...settings, ...change });
  if (typeof window === 'undefined') return;
  try {
    if (change === null) localStorage.removeItem?.(SETTINGS_KEY);
    else localStorage.setItem?.(SETTINGS_KEY, JSON.stringify(settings));
  } catch {
    /* persistence is best-effort */
  }
}
