// Pure helper functions and constants extracted from MobileRadioLayout.svelte

// ── Tuning step presets (mode-aware) ──

export const SSB_STEPS = [10, 50, 100, 500, 1000];
export const CW_STEPS = [10, 50, 100, 500];
export const AM_STEPS = [1000, 5000, 9000, 10000];
export const FM_STEPS = [5000, 10000, 12500, 25000];
export const DEFAULT_STEPS = [10, 50, 100, 500, 1000, 5000, 10000, 100000];

export function getStepsForMode(m: string): number[] {
  const upper = (m || '').toUpperCase();
  if (upper === 'USB' || upper === 'LSB') return SSB_STEPS;
  if (upper === 'CW' || upper === 'CW-R') return CW_STEPS;
  if (upper === 'AM') return AM_STEPS;
  if (upper === 'FM') return FM_STEPS;
  return DEFAULT_STEPS;
}

export function formatStep(hz: number): string {
  if (hz >= 1000) return `${hz / 1000} kHz`;
  return `${hz} Hz`;
}

// ── S-meter formatting ──

export function formatSValue(actual: number): string {
  const v = Math.max(-54, Math.min(40, actual));
  if (v >= 0) {
    const over = Math.round(v);
    return over > 0 ? `S9+${over}` : 'S9';
  }
  const s = Math.max(0, Math.min(9, Math.floor((v + 54) / 6)));
  return `S${s}`;
}

export function formatDbm(actual: number): string {
  const v = Math.max(-54, Math.min(40, actual));
  return `${Math.round(-73 + v)} dBm`;
}

// ── RF Power display ──

export function formatPower(raw: number): string {
  // 0-255 → 0-100W (approx for IC-7610)
  const watts = Math.round(raw / 255 * 100);
  return `${watts}W`;
}
