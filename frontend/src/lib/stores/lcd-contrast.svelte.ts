/**
 * LCD contrast preset store (amber-lcd skin).
 *
 * Exposes a 5-step preset ladder (DIM/LOW/MID/HIGH/MAX) that maps to a
 * triplet of alpha values (active / inactive / ghost) driving the
 * `--lcd-alpha-*` custom properties on the `.lcd-screen` root. This is
 * the foundation mechanism — see docs/plans/2026-04-18-lcd-display-enhancements.md §2.
 *
 * Persistence is intentionally separate from theme (`rigplane:lcd-contrast`):
 * contrast tracks ambient light, theme is aesthetic.
 */

const STORAGE_KEY = 'rigplane:lcd-contrast';

export type LcdContrastPreset = 'DIM' | 'LOW' | 'MID' | 'HIGH' | 'MAX';

export const LCD_CONTRAST_PRESETS: LcdContrastPreset[] = [
  'DIM',
  'LOW',
  'MID',
  'HIGH',
  'MAX',
];

export interface LcdAlphaTriplet {
  active: number;
  inactive: number;
  ghost: number;
}

// Per-preset alpha triplets. See plan §2.2.
// MID is calibrated to the pre-refactor hardcoded values so first-load
// users see no visible change — active ink `#1A1000` (α=1.00), inactive
// indicators at α≈0.08, ghost "888" digits at α=0.06.
const PRESET_ALPHAS: Record<LcdContrastPreset, LcdAlphaTriplet> = {
  DIM:  { active: 0.55, inactive: 0.04, ghost: 0.03 },
  LOW:  { active: 0.75, inactive: 0.06, ghost: 0.045 },
  MID:  { active: 1.00, inactive: 0.08, ghost: 0.06 },
  HIGH: { active: 1.00, inactive: 0.18, ghost: 0.09 },
  MAX:  { active: 1.00, inactive: 0.30, ghost: 0.14 },
};

function isPreset(v: string | null): v is LcdContrastPreset {
  return v !== null && (LCD_CONTRAST_PRESETS as string[]).includes(v);
}

function readStoredPreset(): LcdContrastPreset {
  if (typeof localStorage === 'undefined') return 'MID';
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return isPreset(raw) ? raw : 'MID';
  } catch {
    return 'MID';
  }
}

let preset = $state<LcdContrastPreset>(readStoredPreset());

export function getLcdContrastPreset(): LcdContrastPreset {
  return preset;
}

export function getLcdAlphas(p: LcdContrastPreset = preset): LcdAlphaTriplet {
  return PRESET_ALPHAS[p];
}

export function setLcdContrastPreset(next: LcdContrastPreset): void {
  preset = next;
  if (typeof localStorage !== 'undefined') {
    try {
      localStorage.setItem(STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
  }
  applyLcdContrast();
}

export function stepLcdContrast(direction: 'up' | 'down'): LcdContrastPreset {
  const idx = LCD_CONTRAST_PRESETS.indexOf(preset);
  const nextIdx = direction === 'up'
    ? Math.min(LCD_CONTRAST_PRESETS.length - 1, idx + 1)
    : Math.max(0, idx - 1);
  const next = LCD_CONTRAST_PRESETS[nextIdx];
  if (next !== preset) setLcdContrastPreset(next);
  return next;
}

/**
 * Paint the three `--lcd-alpha-*` custom properties onto every
 * `.lcd-screen` element on the page. Called from onMount and on
 * every preset change.
 */
export function applyLcdContrast(): void {
  if (typeof document === 'undefined') return;
  const alphas = PRESET_ALPHAS[preset];
  const roots = document.querySelectorAll<HTMLElement>('.lcd-screen');
  roots.forEach((el) => {
    el.style.setProperty('--lcd-alpha-active', String(alphas.active));
    el.style.setProperty('--lcd-alpha-inactive', String(alphas.inactive));
    el.style.setProperty('--lcd-alpha-ghost', String(alphas.ghost));
  });
}
