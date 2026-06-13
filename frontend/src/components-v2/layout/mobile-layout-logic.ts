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

export function formatSValue(raw: number): string {
  const v = Math.max(0, Math.min(241, raw));
  if (v <= 0) return 'S0';
  if (v <= 120) {
    const s = Math.round((v / 120) * 9);
    return `S${Math.min(9, Math.max(0, s))}`;
  }
  const over = Math.round(((v - 120) / (241 - 120)) * 60);
  return `S9+${over}`;
}

export function formatDbm(raw: number): string {
  const v = Math.max(0, Math.min(241, raw));
  if (v <= 120) {
    const dbm = -127 + (v / 120) * 54;
    return `${Math.round(dbm)} dBm`;
  }
  const dbm = -73 + ((v - 120) / (241 - 120)) * 60;
  return `${Math.round(dbm)} dBm`;
}

// ── RF Power display ──

export function formatPower(raw: number): string {
  // 0-255 → 0-100W (approx for IC-7610)
  const watts = Math.round(raw / 255 * 100);
  return `${watts}W`;
}
