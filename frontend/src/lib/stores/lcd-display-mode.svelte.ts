/**
 * LCD Display Mode store (#838).
 *
 * Chooses a visual treatment layered on top of the base LCD render:
 *  - `clean`    — default, no extra effects (current behavior).
 *  - `vintage`  — slight sepia + vignette for a "worn readout" feel.
 *  - `crt`      — scanlines amplified + curvature vignette.
 *  - `flicker`  — subtle brightness flicker on top of `crt`.
 *
 * Persisted to localStorage. Applied by `LcdLayout` as a class on
 * `.lcd-frame` — the CSS module `panels/lcd/lcd-vintage.css` handles
 * the visual treatment per class.
 */

export type LcdDisplayMode = 'clean' | 'vintage' | 'crt' | 'flicker';

export const LCD_DISPLAY_MODES: readonly LcdDisplayMode[] = [
  'clean',
  'vintage',
  'crt',
  'flicker',
] as const;

const STORAGE_KEY = 'rigplane-lcd-display-mode';

let mode = $state<LcdDisplayMode>(loadMode());

function loadMode(): LcdDisplayMode {
  if (typeof window === 'undefined') return 'clean';
  try {
    const saved = localStorage.getItem?.(STORAGE_KEY);
    if (
      saved === 'clean' ||
      saved === 'vintage' ||
      saved === 'crt' ||
      saved === 'flicker'
    ) {
      return saved;
    }
  } catch {
    // Test envs may stub localStorage with a partial shape — fall through.
  }
  return 'clean';
}

export function getLcdDisplayMode(): LcdDisplayMode {
  return mode;
}

export function setLcdDisplayMode(next: LcdDisplayMode): void {
  mode = next;
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem?.(STORAGE_KEY, next);
  } catch {
    /* test envs may stub localStorage; persistence best-effort */
  }
}
