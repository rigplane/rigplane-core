/**
 * S-meter scale mapping utilities.
 *
 * Calibration loaded from /api/v1/capabilities → meterCalibrations.s_meter,
 * itself sourced from the active radio's `rigs/<rig>.toml` `[meters.s_meter]`
 * table. Every function below is a GENERIC piecewise-linear reader over
 * whatever anchor table the profile supplies — there is no vendor branch and
 * no hardcoded per-radio curve here (MOR-1451).
 *
 * A radio whose profile has not declared a curve is UNCALIBRATED:
 * `isSmeterCalibrated()` is false, and the S-unit/dBm text below degrades to
 * an honest raw-scale label instead of borrowing another radio's numbers.
 * Mirrors the backend's own `(value, calibrated)` convention
 * (`runtime/meter_cal.py interpolate_meter`) on the display side.
 */

import { getSmeterCalibration, getSmeterRedline } from '$lib/stores/capabilities.svelte';
import {
  calibratedToDbm as calibratedToDbmForCalibration,
  calibratedToRaw as calibratedToRawForCalibration,
  calibratedToSegments as calibratedToSegmentsForCalibration,
  calibratedToSUnit as calibratedToSUnitForCalibration,
  formatDbm as formatDbmForCalibration,
  getS9Raw as getS9RawForCalibration,
  getScaleMaxRaw as getScaleMaxRawForCalibration,
  interpolateRaw,
  isSmeterCalibrated as isSmeterCalibratedForCalibration,
  rawToDbm as rawToDbmForCalibration,
  rawToSFloat,
  rawToSegments as rawToSegmentsForCalibration,
  rawToSUnit as rawToSUnitForCalibration,
  type SmeterCalibrationPoint,
} from '../../primitives/meters/s-meter-scale';
import type { MeterValueDomain } from '../../semantic/radio-view-model';

export interface SmeterMark {
  raw: number;
  actual: number;
  text: string;
  color: string;
}

/**
 * One knot of the piecewise-linear transfer from the calibrated motion
 * fraction (the `motionFraction` ballistics run on) to the evenly spaced
 * 1..9 / +20..+60 display scale (MOR-2509 mock-up v7): `at` is a position
 * on the calibrated fraction axis, `to` the matching position on the
 * evenly spaced axis. Knots are monotone in both columns.
 */
export interface SignalScaleKnot {
  readonly at: number;
  readonly to: number;
}

export interface SignalMeterProjectionMark {
  readonly actual: number;
  readonly fraction: number;
  readonly text: string;
  readonly color: string;
}

export interface SignalMeterProjectionTick {
  readonly fraction: number;
  readonly kind: 'major' | 'mid' | 'minor';
  readonly color: string;
}

export interface SignalMeterProjection {
  readonly scaleMode: 's' | 'raw' | 'none';
  readonly motionFraction: number | null;
  readonly primaryText: string;
  readonly secondaryText: string;
  readonly accessibleDescription: string;
  /** S9 crossover for calibrated S or omitted-domain compatibility only. */
  readonly crossoverFraction: number | null;
  /** MOR-2509: calibrated-fraction → evenly-spaced-scale transfer knots. */
  readonly uniformScaleKnots: readonly SignalScaleKnot[];
  readonly marks: readonly SignalMeterProjectionMark[];
  readonly ticks: readonly SignalMeterProjectionTick[];
}

const SEGMENT_DOMAIN = 20;
const S9_UNIFORM_FRACTION = 4 / 7;
const OVER_S9_SPAN_FRACTION = 3 / 7;
const IDENTITY_SCALE_KNOTS: readonly SignalScaleKnot[] = [
  { at: 0, to: 0 },
  { at: 1, to: 1 },
];

function getCal(): SmeterCalibrationPoint[] {
  return getSmeterCalibration() ?? [];
}

/** True when the active radio profile declared an s_meter calibration table
 *  with at least two knots. Interpolation needs two points to define a
 *  line; a single knot cannot support a calibrated reading, so it counts
 *  as uncalibrated the same as zero knots (MOR-2024) rather than resolving
 *  every input to that one knot's value. False means the S-unit/dBm text
 *  below must fall back to an honest raw-scale label instead of
 *  fabricating a reading against a borrowed curve (MOR-1451). */
export function isSmeterCalibrated(): boolean {
  return isSmeterCalibratedForCalibration(getCal());
}

/** Find S9 raw value from calibration; the raw-scale midpoint when
 *  uncalibrated — a neutral bar-geometry anchor, not a claimed threshold. */
export function getS9Raw(): number {
  return getS9RawForCalibration(getCal());
}

/** Get redline raw value. */
export function getRedlineRaw(): number {
  return getSmeterRedline() ?? getS9Raw();
}

/** Last calibration raw knot, used as the right edge of visual S-meter scales. */
export function getScaleMaxRaw(): number {
  return getScaleMaxRawForCalibration(getCal());
}

/** Map raw 0-255 to fractional segment count 0-20. */
export function rawToSegments(raw: number): number {
  return rawToSegmentsForCalibration(raw, getCal());
}

/** Map raw 0-255 to S-unit string, e.g. "S7", "S9+20". Falls back to the
 *  plain raw number (no "S" claim) when the radio has no calibration table
 *  (MOR-1451) — never a reading borrowed from a different radio's curve. */
export function rawToSUnit(raw: number): string {
  return rawToSUnitForCalibration(raw, getCal());
}

/** Map raw 0-255 to dBm value (linear interpolation between calibration
 *  points). Passes the raw value straight through when uncalibrated — the
 *  honest-fallback text functions below detect that state themselves and
 *  never present the passthrough as a real dBm reading (MOR-1451). */
export function rawToDbm(raw: number): number {
  return rawToDbmForCalibration(raw, getCal());
}

/** Map calibrated dB-rel-S9 from backend state to the raw axis used by the
 *  UI scale. Identity passthrough when uncalibrated, matching `rawToDbm`. */
export function calibratedToRaw(actual: number): number {
  return calibratedToRawForCalibration(actual, getCal());
}

/** Map calibrated dB-rel-S9 to fractional segment count 0-20 for the top S-meter. */
export function calibratedToSegments(actual: number): number {
  return calibratedToSegmentsForCalibration(actual, getCal());
}

/** Map calibrated dB-rel-S9 to an S-unit label, e.g. "S7", "S9+20". */
export function calibratedToSUnit(actual: number): string {
  return calibratedToSUnitForCalibration(actual, getCal());
}

/** Map calibrated dB-rel-S9 to user-facing dBm referenced to S9=-73 dBm.
 *  `null` when uncalibrated — a dBm figure with no calibration behind it
 *  would be a fabricated physical unit, not a passthrough (MOR-1451);
 *  `formatDbm` renders this as an explicit "uncalibrated" label. */
export function calibratedToDbm(actual: number): number | null {
  return calibratedToDbmForCalibration(actual, getCal());
}

function colorForActual(actual: number): string {
  if (actual <= 0) return 'var(--v2-text-bright)';
  if (actual <= 20) return 'var(--v2-accent-yellow)';
  if (actual <= 40) return 'var(--v2-accent-orange-alt)';
  return 'var(--v2-accent-red-alt)';
}

function markText(label: string): string {
  if (label.startsWith('S9+')) return `+${label.slice(3)}`;
  return label;
}

function scaleMarks(calibration: readonly SmeterCalibrationPoint[]): SmeterMark[] {
  return calibration
    .filter((p) => /^S[13579]$/.test(p.label) || /^S9\+/.test(p.label))
    .map((p) => ({
      raw: p.raw,
      actual: p.actual,
      text: markText(p.label),
      color: colorForActual(p.actual),
    }));
}

/** Dense subdivisions projected from the same complete calibration snapshot
 * as the labels and reading. Hidden calibration knots still shape every
 * intermediate raw position even though they are not themselves labeled. */
function scaleTicks(
  marks: readonly SmeterMark[],
  calibration: readonly SmeterCalibrationPoint[],
): SignalMeterProjectionTick[] {
  const ticks: SignalMeterProjectionTick[] = [];
  const anchors = marks.map(({ raw, actual }) => ({ raw, actual }));
  const first = anchors[0];

  if (!first || first.raw > 0) {
    anchors.unshift({ raw: 0, actual: -54 });
  }

  function tick(raw: number, actual: number, kind: SignalMeterProjectionTick['kind']) {
    ticks.push({
      fraction: rawToSegmentsForCalibration(raw, calibration) / SEGMENT_DOMAIN,
      kind,
      color: colorForActual(actual),
    });
  }

  function addSubdivisions(
    startRaw: number,
    endRaw: number,
    startActual: number,
    endActual: number,
  ) {
    tick(startRaw, startActual, 'major');
    const rawStep = (endRaw - startRaw) / 10;
    const actualStep = (endActual - startActual) / 10;
    for (let j = 1; j <= 9; j++) {
      tick(
        startRaw + rawStep * j,
        startActual + actualStep * j,
        j === 5 ? 'mid' : 'minor',
      );
    }
  }

  for (let i = 0; i < anchors.length - 1; i++) {
    addSubdivisions(
      anchors[i].raw,
      anchors[i + 1].raw,
      anchors[i].actual,
      anchors[i + 1].actual,
    );
  }

  const last = anchors[anchors.length - 1];
  tick(last.raw, last.actual, 'major');
  return ticks;
}

/** Major S-meter marks derived from the active calibration table. */
export function getScaleMarks(): SmeterMark[] {
  return scaleMarks(getCal());
}

/** The dB-rel-S9 level at which the table's own S-unit interpolation
 *  reaches `unit`, or null when the table cannot bracket it. */
function sUnitLevel(unit: number, calibration: readonly SmeterCalibrationPoint[]): number | null {
  const sPoints = calibration.filter((point) => /^S\d$/.test(point.label));
  for (let index = 0; index < sPoints.length - 1; index += 1) {
    const startUnit = Number.parseInt(sPoints[index].label.slice(1), 10);
    const endUnit = Number.parseInt(sPoints[index + 1].label.slice(1), 10);
    if (unit >= startUnit && unit <= endUnit) {
      const t = (unit - startUnit) / (endUnit - startUnit);
      const raw = sPoints[index].raw + t * (sPoints[index + 1].raw - sPoints[index].raw);
      return interpolateRaw(raw, calibration);
    }
  }
  return null;
}

/** A level's position on the evenly spaced 1..9 / +20..+60 scale: S-units
 *  linear between S1 and S9 over 0..4/7 (the S-unit number comes from the
 *  radio's own table, so non-uniform tables stay truthful), dB-over-S9
 *  linear over 4/7..1, clamped at both ends. */
function uniformFraction(level: number, calibration: readonly SmeterCalibrationPoint[]): number {
  if (level > 0) {
    return S9_UNIFORM_FRACTION
      + Math.min(1, Math.max(0, level / 60)) * OVER_S9_SPAN_FRACTION;
  }
  const sFloat = rawToSFloat(
    calibratedToRawForCalibration(level, calibration), calibration,
  );
  return Math.min(1, Math.max(0, (sFloat - 1) / 8)) * S9_UNIFORM_FRACTION;
}

/**
 * MOR-2509: the knots of the piecewise-linear transfer from the calibrated
 * motion fraction to the evenly spaced display scale, over the same table
 * every other position in the projection uses. The knot levels are the
 * table's own kinks (its knot levels above S9, the S1 level, the domain
 * ends) plus the v7 scale's own kinks (S9 at 0 dB, the +60 dB clamp).
 */
function uniformScaleKnotsFor(
  calibration: readonly SmeterCalibrationPoint[],
): readonly SignalScaleKnot[] {
  if (!isSmeterCalibratedForCalibration(calibration)) return IDENTITY_SCALE_KNOTS;
  const calMin = calibration[0].actual;
  const calMax = calibration[calibration.length - 1].actual;
  const s1Level = sUnitLevel(1, calibration);
  const levels = new Set<number>([calMin, 0, Math.min(calMax, 60)]);
  if (calMax > 60) levels.add(calMax);
  if (s1Level !== null && s1Level > calMin) levels.add(s1Level);
  for (const point of calibration) {
    if (point.actual > 0 && point.actual < calMax) levels.add(point.actual);
  }
  const knots: SignalScaleKnot[] = [];
  for (const level of [...levels].sort((left, right) => left - right)) {
    const at = calibratedToSegmentsForCalibration(level, calibration) / SEGMENT_DOMAIN;
    const to = uniformFraction(level, calibration);
    const previous = knots[knots.length - 1];
    if (previous && at <= previous.at) continue;
    knots.push({ at, to });
  }
  return knots.length >= 2 ? knots : IDENTITY_SCALE_KNOTS;
}

/** Map a calibrated motion fraction onto the evenly spaced scale through
 *  the projection's knots; monotone, so ballistics over the calibrated
 *  fraction transfer without overshoot. */
export function lerpScaleKnots(
  knots: readonly SignalScaleKnot[], fraction: number,
): number {
  const value = Math.min(1, Math.max(0, fraction));
  if (knots.length === 0) return value;
  if (value <= knots[0].at) return knots[0].to;
  for (let index = 0; index < knots.length - 1; index += 1) {
    const start = knots[index];
    const end = knots[index + 1];
    if (value <= end.at) {
      const span = end.at - start.at;
      return span <= 0 ? end.to : start.to + ((value - start.at) / span) * (end.to - start.to);
    }
  }
  return knots[knots.length - 1].to;
}

/**
 * Resolve every display-facing S-meter value from one capability snapshot.
 * The facade remains the only store reader; the pure primitives above own all
 * calibration, interpolation, labeling, and raw fallback behavior.
 */
export function projectSignalMeter(
  value: number | null, domain?: MeterValueDomain,
): SignalMeterProjection {
  const calibration = getCal();
  // Omission is a compatibility path for pre-MOR-2425 internal callers. Live
  // adapter-produced facts always pass an explicit domain, including unknown.
  const calibrated = isSmeterCalibratedForCalibration(calibration);
  const scaleMode: SignalMeterProjection['scaleMode'] = domain === undefined
    ? (calibrated ? 's' : 'raw')
    : domain.kind === 'raw' ? 'raw'
      : domain.kind === 'engineering' && domain.unit === 'db' && calibrated ? 's' : 'none';
  const projectionCalibration = scaleMode === 's' ? calibration : [];
  const scale = scaleMode === 's' ? scaleMarks(calibration) : [];
  const marks = scale.map((mark) => ({
    actual: mark.actual,
    fraction: rawToSegmentsForCalibration(mark.raw, projectionCalibration) / SEGMENT_DOMAIN,
    text: mark.text,
    color: mark.color,
  }));
  const ticks = scaleMode === 's' ? scaleTicks(scale, projectionCalibration) : [];
  const crossoverFraction = scaleMode === 's' || domain === undefined
    ? rawToSegmentsForCalibration(
        getS9RawForCalibration(projectionCalibration), projectionCalibration,
      ) / SEGMENT_DOMAIN
    : null;
  const uniformScaleKnots = scaleMode === 's'
    ? uniformScaleKnotsFor(projectionCalibration) : IDENTITY_SCALE_KNOTS;

  if (value === null) {
    const legacy = domain === undefined;
    const primaryText = legacy || scaleMode === 's' ? 'S ?' : '?';
    const secondaryText = legacy ? ''
      : scaleMode === 'raw' ? 'uncalibrated'
        : scaleMode === 'none' && domain?.kind === 'engineering' ? 'scale unavailable'
          : scaleMode === 'none' ? 'unit unknown' : '';
    return {
      scaleMode,
      motionFraction: null,
      primaryText,
      secondaryText,
      accessibleDescription: `S meter reading unknown${secondaryText ? `, ${secondaryText}` : ''}`,
      crossoverFraction,
      uniformScaleKnots,
      marks,
      ticks,
    };
  }

  if (scaleMode === 'raw') {
    const primaryText = calibratedToSUnitForCalibration(value, projectionCalibration);
    return {
      scaleMode,
      motionFraction: calibratedToSegmentsForCalibration(value, projectionCalibration) / SEGMENT_DOMAIN,
      primaryText,
      secondaryText: 'uncalibrated',
      accessibleDescription: `S meter ${primaryText} raw, uncalibrated`,
      crossoverFraction,
      uniformScaleKnots,
      marks,
      ticks,
    };
  }

  if (scaleMode === 'none') {
    const engineeringDb = domain?.kind === 'engineering' && domain.unit === 'db';
    const signedValue = `${value < 0 ? '\u2212' : value > 0 ? '+' : ''}${Math.abs(value)}`;
    const valueText = engineeringDb
      ? `${signedValue} dB rel S9`
      : String(value);
    const stateText = engineeringDb ? 'scale unavailable' : 'unit unknown';
    return {
      scaleMode,
      motionFraction: null,
      primaryText: valueText,
      secondaryText: stateText,
      accessibleDescription: engineeringDb
        ? `S meter ${signedValue} decibels relative to S9, ${stateText}`
        : `S meter ${valueText}, ${stateText}`,
      crossoverFraction,
      uniformScaleKnots,
      marks,
      ticks,
    };
  }

  const primaryText = calibratedToSUnitForCalibration(value, projectionCalibration);
  const secondaryText = formatDbmForCalibration(
    calibratedToDbmForCalibration(value, projectionCalibration),
  );
  return {
    scaleMode,
    motionFraction: calibratedToSegmentsForCalibration(value, projectionCalibration) / SEGMENT_DOMAIN,
    primaryText,
    secondaryText,
    accessibleDescription: `S meter ${primaryText}, ${secondaryText}`,
    crossoverFraction,
    uniformScaleKnots,
    marks,
    ticks,
  };
}

/** Format dBm value as display string, e.g. "−67 dBm". Uses Unicode minus. */
export function formatDbm(dbm: number | null): string {
  return formatDbmForCalibration(dbm);
}

/** Get full calibration table for rendering scale ticks. */
export function getCalibrationPoints(): SmeterCalibrationPoint[] {
  return getCal();
}
