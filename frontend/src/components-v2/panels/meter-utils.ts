// Meter formatting and normalization for the six TX/PA meters + s_meter.
//
// Domain contract (MOR-1470, finishing ADR level-meter-calibrated-domain
// Phase 3; mirrors the s_meter cutover from MOR-1451):
//
// - A meter whose active radio profile declares a
//   `[[meters.<key>.calibration]]` table arrives here ALREADY in
//   engineering units — the backend interpolates raw→actual at the
//   observation boundary (MOR-469): power=W, swr=ratio, alc=normalized
//   0–1, comp=dB, vd=V, id=A. Formatters render that value directly and
//   level fns normalize it against the table's top knot. Re-running the
//   value through the curve would be a double conversion.
// - A meter with NO declared table arrives as the raw device byte,
//   flagged uncalibrated server-side. Every function here degrades to an
//   honest raw-scale reading tagged "raw" (e.g. "158 raw"; MOR-1527 — a
//   naked number here was previously indistinguishable from a real
//   engineering-unit reading), a neutral raw/255 bar, and no fault claims
//   — never a unit claim through a borrowed radio's curve. There are NO
//   hardcoded per-radio fallback curves in this module.
//
// Capability-derived calibration and redline data is routed through the
// runtime adapter (Tier 2 batch 2) so this helper does not reach into
// `$lib/stores/*` directly.
import {
  getMeterCalibration,
  getMeterRedline,
} from '$lib/runtime/adapters/capabilities-adapter';
import type { MeterCalPoint } from '$lib/runtime/adapters/capabilities-adapter';

export type MeterSource = 'S' | 'SWR' | 'POWER' | 'po';

export interface Mark {
  pos: number;
  label: string;
  color?: string;
}

/** Raw device-scale ceiling (CI-V meter byte range). Used only as the
 *  neutral bar-geometry edge for uncalibrated meters — never a claimed
 *  reading. */
const RAW_SCALE_MAX = 255;

/**
 * Clamps and normalizes a raw device-scale value to 0-1.
 */
export function normalize(raw: number): number {
  return Math.max(0, Math.min(RAW_SCALE_MAX, raw)) / RAW_SCALE_MAX;
}

function clamp01(value: number): number {
  return Math.max(0, Math.min(1, value));
}

/** The declared calibration table for a meter, or null when the radio's
 *  profile does not declare one (the honest-raw domain). */
function getCal(meterType: string): MeterCalPoint[] | null {
  const cal = getMeterCalibration(meterType);
  return cal && cal.length >= 2 ? cal : null;
}

function topActual(cal: MeterCalPoint[]): number {
  return cal[cal.length - 1].actual;
}

/** Honest raw readout for an uncalibrated meter — the device-scale number
 *  tagged "raw" so it is never mistaken for an engineering-unit claim
 *  (MOR-1527: the pre-fix bug rendered this as a naked number, e.g. a Vd
 *  tile reading a bare "158" with no indication it wasn't volts). */
function formatRaw(value: number): string {
  return `${Math.round(Math.max(0, Math.min(RAW_SCALE_MAX, value)))} raw`;
}

// ---- RF power (W when calibrated) ----

export function formatPowerWatts(value: number): string {
  const cal = getCal('power');
  if (!cal) return formatRaw(value);
  const watts = Math.max(0, Math.min(topActual(cal), value));
  return `${Math.round(watts)}W`;
}

export function normalizePower(value: number): number {
  const cal = getCal('power');
  if (!cal) return normalize(value);
  const max = topActual(cal);
  return max > 0 ? clamp01(value / max) : 0;
}

// ---- SWR (ratio when calibrated) ----

/**
 * The SWR ratio, or NaN when the radio declares no swr table — an
 * uncalibrated raw byte has no honest ratio interpretation.
 */
export function swrRatio(value: number): number {
  return getCal('swr') ? value : NaN;
}

export function formatSwr(value: number): string {
  const cal = getCal('swr');
  if (!cal) return formatRaw(value);
  const top = topActual(cal);
  // At/beyond the table top the true ratio is off-scale — render the
  // profile's own top label (e.g. "6.0+") instead of a fake exact value.
  if (value >= top) return cal[cal.length - 1].label;
  return Math.max(cal[0].actual, value).toFixed(1);
}

/** Bar level for SWR: ratio relative to the table's top knot. */
export function swrLevel(value: number): number {
  const cal = getCal('swr');
  if (!cal) return normalize(value);
  const max = topActual(cal);
  return max > 0 ? clamp01(value / max) : 0;
}

/** True when SWR exceeds 2.0 — only claimable in the calibrated ratio
 *  domain; an uncalibrated radio never asserts a fault it cannot
 *  measure. */
export function isSwrFault(value: number): boolean {
  const ratio = swrRatio(value);
  return Number.isFinite(ratio) && ratio > 2.0;
}

// ---- ALC (normalized 0-1 when calibrated; redline-relative raw
//      otherwise; plain raw with no data at all) ----

export function formatAlc(value: number): string {
  if (getCal('alc')) {
    return `${Math.round(clamp01(value) * 100)}%`;
  }
  const redline = getMeterRedline('alc');
  if (redline !== null && redline > 0) {
    return `${Math.round((Math.max(0, Math.min(redline, value)) / redline) * 100)}%`;
  }
  return formatRaw(value);
}

/** Redline-relative ALC level (0-1). The calibrated domain is already
 *  redline-relative (the table's top knot is the redline). */
export function alcLevel(value: number): number {
  if (getCal('alc')) return clamp01(value);
  const redline = getMeterRedline('alc');
  if (redline !== null && redline > 0) {
    return Math.max(0, Math.min(redline, value)) / redline;
  }
  return normalize(value);
}

/** True when ALC is driven past 90% of the redline — only claimable when
 *  the profile declared a table or a redline. */
export function isAlcFault(value: number): boolean {
  if (!getCal('alc') && getMeterRedline('alc') === null) return false;
  return alcLevel(value) > 0.9;
}

// ---- Vd / Id / COMP (V / A / dB when calibrated) ----

export function formatVolts(value: number): string {
  const cal = getCal('vd');
  if (!cal) return formatRaw(value);
  return `${Math.max(0, Math.min(topActual(cal), value)).toFixed(1)} V`;
}

export function vdLevel(value: number): number {
  const cal = getCal('vd');
  if (!cal) return normalize(value);
  const max = topActual(cal);
  return max > 0 ? clamp01(value / max) : 0;
}

export function formatAmps(value: number): string {
  const cal = getCal('id');
  if (!cal) return formatRaw(value);
  return `${Math.max(0, Math.min(topActual(cal), value)).toFixed(1)} A`;
}

export function idLevel(value: number): number {
  const cal = getCal('id');
  if (!cal) return normalize(value);
  const max = topActual(cal);
  return max > 0 ? clamp01(value / max) : 0;
}

export function formatCompDb(value: number): string {
  const cal = getCal('comp');
  if (!cal) return formatRaw(value);
  return `${Math.round(Math.max(0, Math.min(topActual(cal), value)))} dB`;
}

export function compLevel(value: number): number {
  const cal = getCal('comp');
  if (!cal) return normalize(value);
  const max = topActual(cal);
  return max > 0 ? clamp01(value / max) : 0;
}

// ---- S-meter (dB-rel-S9 when calibrated; MOR-1451) ----

function getSmeterKnots(): [number, number][] {
  const cal = getMeterCalibration('s_meter');
  if (!cal || cal.length < 2) return [];
  return cal.map((p) => [p.raw, p.actual] as [number, number]);
}

/** True when the active radio profile declared a real s_meter calibration
 *  table. False means `formatSMeter`/`sLevel` below must fall back to an
 *  honest raw-scale reading instead of fabricating one against a borrowed
 *  curve (MOR-1451) — mirrors `smeter-scale.ts`'s `isSmeterCalibrated`. */
function isSmeterCalibrated(): boolean {
  return getSmeterKnots().length > 0;
}

function getSmeterMaxRaw(): number {
  const knots = getSmeterKnots();
  return knots.length > 0 ? knots[knots.length - 1][0] : RAW_SCALE_MAX;
}

/** Identity passthrough when uncalibrated, matching `smeter-scale.ts`'s
 *  `calibratedToRaw`. */
function calibratedSmeterToRaw(actual: number): number {
  const knots = getSmeterKnots();
  if (knots.length === 0) return Math.max(0, Math.min(RAW_SCALE_MAX, actual));
  const minActual = knots[0][1];
  const maxActual = knots[knots.length - 1][1];
  const clamped = Math.max(minActual, Math.min(maxActual, actual));

  for (let i = 0; i < knots.length - 1; i++) {
    const [raw0, actual0] = knots[i];
    const [raw1, actual1] = knots[i + 1];
    if (clamped <= actual1) {
      const span = actual1 - actual0;
      const t = span === 0 ? 0 : (clamped - actual0) / span;
      return raw0 + t * (raw1 - raw0);
    }
  }

  return knots[knots.length - 1][0];
}

/**
 * Formats calibrated S-meter value (dB relative to S9) as an S-unit string.
 * Falls back to the plain raw-scale number (no "S" claim) when the radio
 * has no s_meter calibration table — never a reading borrowed from a
 * different radio's curve (MOR-1451).
 */
export function formatSMeter(actual: number): string {
  if (!isSmeterCalibrated()) {
    return String(Math.round(Math.max(0, Math.min(RAW_SCALE_MAX, actual))));
  }
  const knots = getSmeterKnots();
  const minActual = knots[0][1];
  const maxActual = knots[knots.length - 1][1];
  const clamped = Math.max(minActual, Math.min(maxActual, actual));

  if (clamped >= 0) {
    const over = Math.round(clamped);
    return over > 0 ? `S9+${over}` : 'S9';
  }

  const s = Math.max(0, Math.min(9, Math.floor((clamped - minActual) / 6)));
  return `S${s}`;
}

/** Bar level for calibrated S-meter values relative to the UI scale full-scale. */
export function sLevel(actual: number): number {
  const scaleMaxRaw = getSmeterMaxRaw();
  const scaled = calibratedSmeterToRaw(actual);
  return scaleMaxRaw > 0 ? Math.max(0, Math.min(1, scaled / scaleMaxRaw)) : 0;
}

// ---- Peak hold ----

/**
 * Peak-hold state tracker (#823).
 *
 * Holds the latched peak value and its timestamp. The decayed display value
 * is computed per-render from the elapsed time (see `peakHoldDisplay`) so
 * the decay is strictly linear across the `decayMs` window — storing a
 * pre-decayed value and repeatedly decaying it would produce exponential
 * (compounding) decay instead.
 *
 * Pure function over state — callers schedule the tick. Domain-agnostic:
 * it latches/decays whatever quantity (engineering or raw) flows through.
 */
export interface PeakHoldState {
  latchedPeak: number;
  latchedAt: number;
}

/**
 * Shared peak-hold decay window (MOR-1282), in milliseconds.
 *
 * Both `BarGauge` and `MetersDockPanel` import this single constant instead
 * of each declaring their own literal — a raw sample must decay identically
 * regardless of which surface is rendering it. Do not re-declare a local
 * `PEAK_DECAY_MS` in either consumer; that would silently reintroduce the
 * drift this constant exists to close.
 */
export const PEAK_DECAY_MS = 1500;

export function updatePeakHold(
  state: PeakHoldState | undefined,
  current: number,
  now: number,
  decayMs = 2000,
): PeakHoldState {
  if (!state || current > state.latchedPeak) {
    return { latchedPeak: current, latchedAt: now };
  }
  // Once the decay window has fully elapsed the latched peak is no longer
  // visible; re-seat the anchor to `current` so future samples decay from a
  // fresh baseline.
  if (now - state.latchedAt >= decayMs) {
    return { latchedPeak: current, latchedAt: now };
  }
  return state;
}

/**
 * Computes the displayed peak value for the current render frame.
 * The latched peak decays linearly to 0 across `decayMs`; the live
 * `current` sample floors the result so a rising signal is never masked
 * by the hold marker.
 */
export function peakHoldDisplay(
  state: PeakHoldState | undefined,
  current: number,
  now: number,
  decayMs = 2000,
): number {
  if (!state) return current;
  const elapsed = now - state.latchedAt;
  if (elapsed >= decayMs) return current;
  const factor = 1 - elapsed / decayMs;
  const decayed = state.latchedPeak * factor;
  return Math.max(current, decayed);
}

/**
 * Needle gauge marks. Positions live in the same domain as the matching
 * level fn (`sLevel` / `swrLevel` / `normalizePower`) so the needle and
 * its scale always agree. Labels come from the profile's own calibration
 * table; a radio with no declared curve gets no marks — there is nothing
 * honest to draw.
 */
export function getNeedleMarks(source: MeterSource): Mark[] {
  switch (source) {
    case 'S': {
      const maxRaw = getSmeterMaxRaw();
      return getSmeterKnots()
        .filter(([, actual]) => [-48, -36, -24, -12, 0, 20, 40].includes(Math.round(actual)))
        .map(([raw, actual]) => ({
          pos: maxRaw > 0 ? Math.max(0, Math.min(1, raw / maxRaw)) : 0,
          label: actual > 0 ? `+${Math.round(actual)}` : `S${Math.round((actual + 54) / 6)}`,
        }));
    }
    case 'SWR': {
      const cal = getCal('swr');
      if (!cal) return [];
      const max = topActual(cal);
      return cal.map((p) => ({
        pos: max > 0 ? clamp01(p.actual / max) : 0,
        label: p.label,
      }));
    }
    case 'POWER':
    case 'po': {
      const cal = getCal('power');
      if (!cal) return [];
      const max = topActual(cal);
      return cal.map((p) => ({
        pos: max > 0 ? clamp01(p.actual / max) : 0,
        label: p.label,
      }));
    }
  }
}
