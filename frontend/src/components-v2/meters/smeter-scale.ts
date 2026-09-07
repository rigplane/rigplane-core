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
  isSmeterCalibrated as isSmeterCalibratedForCalibration,
  rawToDbm as rawToDbmForCalibration,
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
  /** S9 crossover in `s` mode; absent for raw/unprojectable geometry. */
  readonly crossoverFraction: number | null;
  readonly marks: readonly SignalMeterProjectionMark[];
  readonly ticks: readonly SignalMeterProjectionTick[];
}

const SEGMENT_DOMAIN = 20;

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
  const crossoverFraction = scaleMode === 's'
    ? rawToSegmentsForCalibration(
        getS9RawForCalibration(projectionCalibration), projectionCalibration,
      ) / SEGMENT_DOMAIN
    : null;

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
