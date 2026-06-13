/**
 * S-meter scale mapping utilities.
 *
 * Calibration loaded from /api/v1/capabilities → meterCalibrations.s_meter.
 * Falls back to IC-7610 defaults if no calibration available.
 */

import { getSmeterCalibration, getSmeterRedline } from '$lib/stores/capabilities.svelte';

interface CalPoint {
  raw: number;
  actual: number;
  label: string;
}

// IC-7610 fallback (original hardcoded values)
const DEFAULT_CAL: CalPoint[] = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 18, actual: -48, label: 'S1' },
  { raw: 36, actual: -42, label: 'S2' },
  { raw: 54, actual: -36, label: 'S3' },
  { raw: 72, actual: -30, label: 'S4' },
  { raw: 90, actual: -24, label: 'S5' },
  { raw: 108, actual: -18, label: 'S6' },
  { raw: 126, actual: -12, label: 'S7' },
  { raw: 144, actual: -6, label: 'S8' },
  { raw: 162, actual: 0, label: 'S9' },
  { raw: 182, actual: 10, label: 'S9+10' },
  { raw: 202, actual: 20, label: 'S9+20' },
  { raw: 222, actual: 30, label: 'S9+30' },
  { raw: 241, actual: 40, label: 'S9+40' },
];

const MAX_RAW = 255;
const S9_DBM = -73;

function getCal(): CalPoint[] {
  return getSmeterCalibration() ?? DEFAULT_CAL;
}

/** Find S9 raw value from calibration. */
export function getS9Raw(): number {
  const cal = getCal();
  const s9 = cal.find(p => p.label === 'S9');
  return s9?.raw ?? 162;
}

/** Get redline raw value. */
export function getRedlineRaw(): number {
  return getSmeterRedline() ?? getS9Raw();
}

/** Piecewise linear interpolation over calibration table. */
function interpolate(raw: number, table: CalPoint[], outKey: 'actual'): number;
function interpolate(raw: number, table: CalPoint[], outKey: 'actual'): number {
  const v = Math.max(0, Math.min(MAX_RAW, raw));
  if (table.length === 0) return 0;
  if (v <= table[0].raw) return table[0][outKey];
  for (let i = 0; i < table.length - 1; i++) {
    const p0 = table[i];
    const p1 = table[i + 1];
    if (v <= p1.raw) {
      const t = (v - p0.raw) / (p1.raw - p0.raw);
      return p0[outKey] + t * (p1[outKey] - p0[outKey]);
    }
  }
  return table[table.length - 1][outKey];
}

/** Inverse interpolation from calibrated dB-rel-S9 back to the scale raw axis. */
function interpolateActual(actual: number, table: CalPoint[]): number {
  if (table.length === 0) return 0;
  const minActual = table[0].actual;
  const maxActual = table[table.length - 1].actual;
  const v = Math.max(minActual, Math.min(maxActual, actual));
  if (v <= minActual) return table[0].raw;
  for (let i = 0; i < table.length - 1; i++) {
    const p0 = table[i];
    const p1 = table[i + 1];
    if (v <= p1.actual) {
      const span = p1.actual - p0.actual;
      const t = span === 0 ? 0 : (v - p0.actual) / span;
      return p0.raw + t * (p1.raw - p0.raw);
    }
  }
  return table[table.length - 1].raw;
}

/** Map raw to fractional S-unit (0.0 - 9.0+ range). */
function rawToSFloat(raw: number): number {
  const cal = getCal();
  const s9Raw = getS9Raw();
  const v = Math.max(0, Math.min(MAX_RAW, raw));

  // Find S-unit points (labels like S0..S9)
  const sPoints = cal.filter(p => /^S\d$/.test(p.label));
  if (sPoints.length < 2) {
    // Fallback: linear
    return (v / s9Raw) * 9;
  }

  // Interpolate through S-unit points
  for (let i = 0; i < sPoints.length - 1; i++) {
    const p0 = sPoints[i];
    const p1 = sPoints[i + 1];
    const s0 = parseInt(p0.label.slice(1));
    const s1 = parseInt(p1.label.slice(1));
    if (v <= p1.raw) {
      const t = Math.max(0, (v - p0.raw) / (p1.raw - p0.raw));
      return s0 + t * (s1 - s0);
    }
  }
  return 9;
}

/** Map raw 0-255 to fractional segment count 0-20. */
export function rawToSegments(raw: number): number {
  const s9Raw = getS9Raw();
  const v = Math.max(0, Math.min(MAX_RAW, raw));
  if (v <= s9Raw) {
    return (rawToSFloat(v) / 9) * 11;
  }
  return 11 + ((v - s9Raw) / (MAX_RAW - s9Raw)) * 9;
}

/** Map raw 0-255 to S-unit string, e.g. "S7", "S9+20". */
export function rawToSUnit(raw: number): string {
  const cal = getCal();
  const s9Raw = getS9Raw();
  const v = Math.max(0, Math.min(MAX_RAW, raw));

  if (v <= s9Raw) {
    const s = Math.floor(rawToSFloat(v));
    return `S${Math.min(9, s)}`;
  }

  // Over S9: find matching calibration label
  const overPoints = cal.filter(p => p.raw > s9Raw);
  let label = 'S9+';
  for (let i = overPoints.length - 1; i >= 0; i--) {
    if (v >= overPoints[i].raw) {
      label = overPoints[i].label;
      break;
    }
  }
  return label;
}

/** Map raw 0-255 to dBm value (linear interpolation between calibration points). */
export function rawToDbm(raw: number): number {
  return Math.round(interpolate(raw, getCal(), 'actual'));
}

/** Map calibrated dB-rel-S9 from backend state to the raw axis used by the UI scale. */
export function calibratedToRaw(actual: number): number {
  return interpolateActual(actual, getCal());
}

/** Map calibrated dB-rel-S9 to fractional segment count 0-20 for the top S-meter. */
export function calibratedToSegments(actual: number): number {
  return rawToSegments(calibratedToRaw(actual));
}

/** Map calibrated dB-rel-S9 to an S-unit label, e.g. "S7", "S9+20". */
export function calibratedToSUnit(actual: number): string {
  return rawToSUnit(calibratedToRaw(actual));
}

/** Map calibrated dB-rel-S9 to user-facing dBm referenced to S9=-73 dBm. */
export function calibratedToDbm(actual: number): number {
  const cal = getCal();
  const minActual = cal[0]?.actual ?? -54;
  const maxActual = cal[cal.length - 1]?.actual ?? 40;
  const clamped = Math.max(minActual, Math.min(maxActual, actual));
  return Math.round(S9_DBM + clamped);
}

/** Format dBm value as display string, e.g. "−67 dBm". Uses Unicode minus. */
export function formatDbm(dbm: number): string {
  const sign = dbm < 0 ? '\u2212' : '+';
  return `${sign}${Math.abs(dbm)} dBm`;
}

/** Get full calibration table for rendering scale ticks. */
export function getCalibrationPoints(): CalPoint[] {
  return getCal();
}
