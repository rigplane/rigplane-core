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

export interface SmeterMark {
  raw: number;
  actual: number;
  text: string;
  color: string;
}

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

/** Major S-meter marks derived from the active calibration table. */
export function getScaleMarks(): SmeterMark[] {
  return getCal()
    .filter((p) => /^S[13579]$/.test(p.label) || /^S9\+/.test(p.label))
    .map((p) => ({
      raw: p.raw,
      actual: p.actual,
      text: markText(p.label),
      color: colorForActual(p.actual),
    }));
}

/** Format dBm value as display string, e.g. "−67 dBm". Uses Unicode minus. */
export function formatDbm(dbm: number | null): string {
  return formatDbmForCalibration(dbm);
}

/** Get full calibration table for rendering scale ticks. */
export function getCalibrationPoints(): SmeterCalibrationPoint[] {
  return getCal();
}
