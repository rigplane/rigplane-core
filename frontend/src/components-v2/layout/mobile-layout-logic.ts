// Pure helper functions and constants extracted from MobileRadioLayout.svelte

import { calibratedToSUnit, calibratedToDbm, formatDbm as formatDbmText } from '../meters/smeter-scale';

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
// Thin delegates to the shared calibration-aware helpers (MOR-1451): the
// mobile readouts follow the same profile-declared curve — and the same
// honest-uncalibrated raw fallback — as every other S-meter surface,
// instead of a third hardcoded copy of one radio's math.

export function formatSValue(actual: number): string {
  return calibratedToSUnit(actual);
}

export function formatDbm(actual: number): string {
  return formatDbmText(calibratedToDbm(actual));
}

// ── RF Power display ──

export function formatPower(level: number): string {
  // power_level arrives normalized 0.0-1.0 from the backend (CI-V raw 0-255 is
  // divided by 255 at the source — see runtime/_civ_rx.py; matches rf_gain /
  // af_level / squelch). 1.0 ≈ 100W (approx for IC-7610). Previously this
  // divided a 0-1 value by 255 again, collapsing 50% to ~0W (MOR-334 class).
  const watts = Math.round(Math.max(0, Math.min(1, level)) * 100);
  return `${watts}W`;
}
