/**
 * Stacked-trace ("3D") panadapter preference (MOR-4463).
 *
 * The flat spectrum is the default; the stacked view is on only after the
 * user switched it on, and the choice is kept per browser. `unavailable` is
 * set for the page's lifetime when the browser could not give the view a
 * WebGL2 context: the toggle then stays off and says why.
 */

const STORAGE_KEY = 'rigplane-spectrum-stacked-trace';

function load(): boolean {
  if (typeof window === 'undefined') return false;
  try {
    return localStorage.getItem?.(STORAGE_KEY) === '1';
  } catch {
    return false;
  }
}

let enabled = $state(load());
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
