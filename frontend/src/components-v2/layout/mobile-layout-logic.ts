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

export function formatSValue(dbRelS9: number): string {
  if (dbRelS9 > 0) return `S9+${Math.round(dbRelS9)}`;
  const s = Math.round(9 + dbRelS9 / 6);
  return `S${Math.min(9, Math.max(0, s))}`;
}

export function formatDbm(dbRelS9: number): string {
  return `${Math.round(-73 + dbRelS9)} dBm`;
}

// ── RF Power display ──

export function formatPower(raw: number): string {
  const pct = Math.round(Math.max(0, Math.min(1, raw)) * 100);
  return `${pct}%`;
}
