// Meter formatting and normalization for the six TX/PA meters + s_meter.
//
// Domain contract (MOR-1470, finishing ADR level-meter-calibrated-domain
// Phase 3; mirrors the s_meter cutover from MOR-1451):
//
// - A meter whose explicit domain is engineering and whose active radio
//   profile declares a
//   `[[meters.<key>.calibration]]` table arrives here ALREADY in
//   engineering units — the backend interpolates raw→actual at the
//   observation boundary (MOR-469): power=W, swr=ratio, alc=normalized
//   0–1, comp=dB, vd=V, id=A. Formatters render that value directly and
//   level fns normalize it against the table's top knot. Re-running the
//   value through the curve would be a double conversion.
// - An explicit raw domain is authoritative even if local capability metadata
//   also has a table. Every function here degrades to an
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
import {
  calibratedToRaw,
  calibratedToSUnit,
  getCalibratedScaleMaxRaw,
  isSmeterCalibrated,
} from '../../primitives/meters/s-meter-scale';
import { valueToPosition } from '../../primitives/scalar/value-control-core';
import type {
  MeterEngineeringUnit,
  MeterValueDomain,
} from '../../semantic/radio-view-model';

export type MeterSource = 'S' | 'SWR' | 'POWER' | 'po';

/**
 * The two values a bar's ends stand for: `min` at empty, `max` at full.
 * Every `*Scale` function below returns the domain its matching `*Level`
 * function positions against; null means that function is not positioning
 * against a scale at all (an explicit raw domain, a domain it cannot serve,
 * or no usable calibration data).
 */
export interface MeterScaleDomain {
  readonly min: number;
  readonly max: number;
}

/**
 * The scale the supply-voltage bar is drawn against, in volts — a fixed
 * window rather than the profile's calibration range, so a sag moves the
 * bar by the same amount on every radio. The art-direction line chose these
 * ends on 2026-09-08 (11 V = a 12 V supply in trouble, 15 V = faulty); they
 * are an instrument choice, not a rating read off a radio.
 *
 * `vdScale` returns this and `vdLevel` positions against it, so a face
 * labelling the scale from `BarMeterProjection.scale` prints the numbers
 * the fill was computed from. Pinned by meter-utils.test.ts
 * "SUPPLY_VOLTAGE_WINDOW" and the three profile ladders that follow it.
 */
export const SUPPLY_VOLTAGE_WINDOW: MeterScaleDomain = { min: 11, max: 15 };

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

/** The active profile's calibration table for a meter, when usable. */
function getCal(meterType: string): MeterCalPoint[] | null {
  const cal = getMeterCalibration(meterType);
  return cal && cal.length >= 2 ? cal : null;
}

function topActual(cal: MeterCalPoint[]): number {
  return cal[cal.length - 1].actual;
}

/** Zero to the table's top knot. Null when the profile declares no table. */
function topScale(meterType: string): MeterScaleDomain | null {
  const cal = getCal(meterType);
  return cal ? { min: 0, max: topActual(cal) } : null;
}

/** A reading's place on its own scale: 0 at `min`, 1 at `max`, clamped to
 *  that interval. A scale with no positive span yields 0. */
function positionIn(value: number, scale: MeterScaleDomain): number {
  return scale.max > scale.min ? valueToPosition(value, scale.min, scale.max) : 0;
}

/**
 * Renders a one-decimal drain reading against its calibration table.
 *
 * `src/rigplane/runtime/meter_cal.py: interpolate_meter` returns the last
 * knot's `actual` for every raw at or above that knot's `raw`, so a value
 * that has reached the top is a clamp, not a reading — how far past the top
 * the rail actually went is not in the table. Such a value is rendered as
 * the top with a trailing "+" (the shape `formatSwr` already shows through
 * its top knot's own label); below the top, and with no table at all, the
 * value is rendered as itself.
 */
function formatAgainstTop(
  value: number,
  cal: MeterCalPoint[] | null,
  unit: string,
): string {
  const floored = Math.max(0, value);
  if (cal) {
    const top = topActual(cal);
    if (floored >= top) return `${top.toFixed(1)}+ ${unit}`;
  }
  return `${floored.toFixed(1)} ${unit}`;
}

/** Honest raw readout for an uncalibrated meter — the device-scale number
 *  tagged "raw" so it is never mistaken for an engineering-unit claim
 *  (MOR-1527: the pre-fix bug rendered this as a naked number, e.g. a Vd
 *  tile reading a bare "158" with no indication it wasn't volts). */
function formatRaw(value: number): string {
  return `${Math.round(Math.max(0, Math.min(RAW_SCALE_MAX, value)))} raw`;
}

const ENGINEERING_UNIT_LABEL = {
  db: 'dB',
  normalized: 'normalized',
  w: 'W',
  ratio: 'ratio',
  v: 'V',
  a: 'A',
} as const satisfies Readonly<Record<MeterEngineeringUnit, string>>;

function formatUnknownUnit(value: number): string {
  return `${value} unit unknown`;
}

function formatDeclaredEngineering(value: number, unit: MeterEngineeringUnit): string {
  return `${value} ${ENGINEERING_UNIT_LABEL[unit]}`;
}

function matchesEngineering(
  domain: MeterValueDomain | undefined,
  unit: MeterEngineeringUnit,
): boolean {
  return domain?.kind === 'engineering' && domain.unit === unit;
}

// ---- RF power (W when calibrated) ----

export function formatPowerWatts(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'w') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  const cal = getCal('power');
  if (!cal && domain === undefined) return formatRaw(value);
  const watts = Math.max(0, cal ? Math.min(topActual(cal), value) : value);
  return `${Math.round(watts)}W`;
}

/** The watt scale the power bar is drawn against, or null when there is none. */
export function powerScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'w')) return null;
  return topScale('power');
}

export function normalizePower(value: number): number;
export function normalizePower(value: number, domain: MeterValueDomain): number | null;
export function normalizePower(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'w')) return null;
  const scale = powerScale(domain);
  if (!scale) return domain === undefined ? normalize(value) : null;
  return positionIn(value, scale);
}

// ---- SWR (ratio when calibrated) ----

/**
 * The SWR ratio, or NaN when the domain cannot support a ratio claim.
 * Omitted-domain compatibility still uses the active profile table.
 */
export function swrRatio(value: number, domain?: MeterValueDomain): number {
  if (domain !== undefined) return matchesEngineering(domain, 'ratio') ? value : NaN;
  return getCal('swr') ? value : NaN;
}

/** Whether the lower SWR row has a physical ratio scale, independent of sample state. */
export function hasSwrRatioScale(domain?: MeterValueDomain): boolean {
  if (domain === undefined) return true;
  return matchesEngineering(domain, 'ratio') && getCal('swr') !== null;
}

export function formatSwr(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'ratio') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  const cal = getCal('swr');
  if (!cal) {
    return domain === undefined ? formatRaw(value) : Math.max(1, value).toFixed(1);
  }
  const top = topActual(cal);
  // At/beyond the table top the true ratio is off-scale — render the
  // profile's own top label (e.g. "6.0+") instead of a fake exact value.
  if (value >= top) return cal[cal.length - 1].label;
  return Math.max(cal[0].actual, value).toFixed(1);
}

/** The ratio scale the SWR bar is drawn against, or null when there is none. */
export function swrScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'ratio')) return null;
  return topScale('swr');
}

/** SWR level in its explicit raw domain or against a declared ratio scale. */
export function swrLevel(value: number): number;
export function swrLevel(value: number, domain: MeterValueDomain): number | null;
export function swrLevel(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'ratio')) return null;
  const scale = swrScale(domain);
  if (!scale) return domain === undefined ? normalize(value) : null;
  return positionIn(value, scale);
}

/** True when SWR exceeds 2.0 in an explicit or legacy-qualified ratio domain. */
export function isSwrFault(value: number, domain?: MeterValueDomain): boolean {
  const ratio = swrRatio(value, domain);
  return Number.isFinite(ratio) && ratio > 2.0;
}

// ---- ALC (normalized 0-1 when calibrated; redline-relative raw
//      otherwise; plain raw with no data at all) ----

export function formatAlc(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'normalized') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  if (getCal('alc') || matchesEngineering(domain, 'normalized')) {
    return `${Math.round(clamp01(value) * 100)}%`;
  }
  const redline = getMeterRedline('alc');
  if (redline !== null && redline > 0) {
    return `${Math.round((Math.max(0, Math.min(redline, value)) / redline) * 100)}%`;
  }
  return formatRaw(value);
}

/** ALC's calibrated scale — the value arrives normalized 0-1 (file header). */
const NORMALIZED_FULL_SCALE: MeterScaleDomain = { min: 0, max: 1 };

/** The ALC bar's scale: the normalized unit interval when calibrated, zero
 *  to the declared redline otherwise. Null when the profile declares
 *  neither. */
export function alcScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'normalized')) return null;
  if (getCal('alc') || matchesEngineering(domain, 'normalized')) return NORMALIZED_FULL_SCALE;
  const redline = getMeterRedline('alc');
  return redline !== null && redline > 0 ? { min: 0, max: redline } : null;
}

/** Redline-relative ALC level, neutral raw geometry, or no supported motion. */
export function alcLevel(value: number): number;
export function alcLevel(value: number, domain: MeterValueDomain): number | null;
export function alcLevel(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'normalized')) return null;
  const scale = alcScale(domain);
  return scale ? positionIn(value, scale) : normalize(value);
}

/** True when ALC is past 90% in an explicit normalized or legacy-qualified domain. */
export function isAlcFault(value: number, domain?: MeterValueDomain): boolean {
  if (domain !== undefined && !matchesEngineering(domain, 'normalized')) return false;
  if (matchesEngineering(domain, 'normalized')) return clamp01(value) > 0.9;
  if (!getCal('alc') && getMeterRedline('alc') === null) return false;
  return alcLevel(value) > 0.9;
}

// ---- Vd / Id / COMP (V / A / dB when calibrated) ----

export function formatVolts(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'v') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  const cal = getCal('vd');
  if (!cal && domain === undefined) return formatRaw(value);
  return formatAgainstTop(value, cal, 'V');
}

/**
 * The supply-voltage bar's scale: `SUPPLY_VOLTAGE_WINDOW`, not this profile's
 * calibration range. Null when the profile declares no vd table.
 */
export function vdScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'v')) return null;
  return getCal('vd') ? SUPPLY_VOLTAGE_WINDOW : null;
}

export function vdLevel(value: number): number;
export function vdLevel(value: number, domain: MeterValueDomain): number | null;
export function vdLevel(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'v')) return null;
  const scale = vdScale(domain);
  if (!scale) return domain === undefined ? normalize(value) : null;
  return positionIn(value, scale);
}

export function formatAmps(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'a') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  const cal = getCal('id');
  if (!cal && domain === undefined) return formatRaw(value);
  return formatAgainstTop(value, cal, 'A');
}

/** The drain-current bar's scale: zero to the profile table's top, unwindowed. */
export function idScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'a')) return null;
  return topScale('id');
}

export function idLevel(value: number): number;
export function idLevel(value: number, domain: MeterValueDomain): number | null;
export function idLevel(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'a')) return null;
  const scale = idScale(domain);
  if (!scale) return domain === undefined ? normalize(value) : null;
  return positionIn(value, scale);
}

export function formatCompDb(value: number, domain?: MeterValueDomain): string {
  if (domain?.kind === 'raw') return formatRaw(value);
  if (domain?.kind === 'unknown') return formatUnknownUnit(value);
  if (domain?.kind === 'engineering' && domain.unit !== 'db') {
    return formatDeclaredEngineering(value, domain.unit);
  }
  const cal = getCal('comp');
  if (!cal && domain === undefined) return formatRaw(value);
  return `${Math.round(Math.max(0, cal ? Math.min(topActual(cal), value) : value))} dB`;
}

/** The compression bar's scale: zero to the profile table's top, in dB. */
export function compScale(domain?: MeterValueDomain): MeterScaleDomain | null {
  if (domain?.kind === 'raw') return null;
  if (domain !== undefined && !matchesEngineering(domain, 'db')) return null;
  return topScale('comp');
}

export function compLevel(value: number): number;
export function compLevel(value: number, domain: MeterValueDomain): number | null;
export function compLevel(value: number, domain?: MeterValueDomain): number | null {
  if (domain?.kind === 'raw') return normalize(value);
  if (domain !== undefined && !matchesEngineering(domain, 'db')) return null;
  const scale = compScale(domain);
  if (!scale) return domain === undefined ? normalize(value) : null;
  return positionIn(value, scale);
}

// ---- S-meter (dB-rel-S9 when calibrated; MOR-1451) ----

/**
 * Formats calibrated S-meter value (dB relative to S9) as an S-unit string.
 * Falls back to the honest raw-tagged reading (`formatRaw`, e.g. "53 raw";
 * MOR-1527) when the radio has no s_meter calibration table — never a
 * reading borrowed from a different radio's curve (MOR-1451), and never a
 * naked number indistinguishable from a real S-unit claim (MOR-1535).
 *
 * The calibrated branch defers entirely to `smeter-scale.ts`'s
 * `calibratedToSUnit` (MOR-2024) — this file no longer runs its own S-unit
 * math. It previously assumed every radio's sub-S9 curve was a uniform
 * straight line (a hardcoded 6 dB/S-unit ladder), which disagreed with a
 * profile's own declared table wherever that table was NOT uniform (e.g.
 * FTX-1's real S0-S9 steps are 6/3/3/3/3/3/15/9/9 dB).
 */
export function formatSMeter(actual: number): string {
  const calibration = getMeterCalibration('s_meter') ?? [];
  if (!isSmeterCalibrated(calibration)) {
    return formatRaw(actual);
  }
  return calibratedToSUnit(actual, calibration);
}

/** Bar level for calibrated S-meter values relative to the UI scale full-scale. */
export function sLevel(actual: number): number {
  const calibration = getMeterCalibration('s_meter') ?? [];
  const scaleMaxRaw = getCalibratedScaleMaxRaw(calibration);
  const scaled = calibratedToRaw(actual, calibration);
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
