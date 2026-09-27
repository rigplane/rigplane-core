/**
 * MOR-2680 — whether a failed asset preload may reload the page, and the
 * sessionStorage record of the last reload it allowed.
 */

const MARKER_KEY = 'rigplane:preload-error-reload-at';
const RELOAD_WINDOW_MS = 60_000;

/**
 * `marker` is the recorded time of the last allowed reload, in the same
 * milliseconds as `now`, or `null` when none is recorded.
 */
export function shouldReloadAfterPreloadError(
  now: number,
  marker: string | null,
  layoutMounted: boolean,
): boolean {
  if (layoutMounted) return false;
  return marker === null || now - Number(marker) >= RELOAD_WINDOW_MS;
}

/**
 * Returns whether the page should reload now, recording `now` first when it
 * should. Returns `false` when sessionStorage throws.
 */
export function claimPreloadErrorReload(now: number, layoutMounted: boolean): boolean {
  try {
    if (!shouldReloadAfterPreloadError(now, sessionStorage.getItem(MARKER_KEY), layoutMounted)) {
      return false;
    }
    sessionStorage.setItem(MARKER_KEY, String(now));
    return true;
  } catch {
    return false;
  }
}

/** Forgets the recorded reload. Never throws. */
export function clearPreloadErrorReload(): void {
  try {
    sessionStorage.removeItem(MARKER_KEY);
  } catch {
    // Dropped: this function never throws.
  }
}
