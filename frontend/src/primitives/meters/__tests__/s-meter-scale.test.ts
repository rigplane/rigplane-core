import { describe, expect, it } from 'vitest';
import {
  calibratedToDbm,
  calibratedToRaw,
  calibratedToSegments,
  calibratedToSUnit,
  getCalibratedScaleMaxRaw,
  formatDbm,
  getS9Raw,
  getScaleMaxRaw,
  isSmeterCalibrated,
  rawToSegments,
  rawToSUnit,
  type SmeterCalibrationPoint,
} from '../s-meter-scale';

const FTX1_CALIBRATION: readonly SmeterCalibrationPoint[] = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 13, actual: -51, label: 'S0.5' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 39, actual: -45, label: 'S2' },
  { raw: 52, actual: -42, label: 'S3' },
  { raw: 65, actual: -39, label: 'S4' },
  { raw: 78, actual: -36, label: 'S5' },
  { raw: 91, actual: -33, label: 'S6' },
  { raw: 103, actual: -18, label: 'S7' },
  { raw: 117, actual: -9, label: 'S8' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

describe('pure S-meter calibration', () => {
  it('uses the nonuniform profile for inverse mapping, labels, and dBm', () => {
    expect(calibratedToRaw(-33, FTX1_CALIBRATION)).toBe(91);
    expect(calibratedToRaw(0, FTX1_CALIBRATION)).toBe(130);
    expect(calibratedToRaw(40, FTX1_CALIBRATION)).toBe(240);
    expect(calibratedToSUnit(-33, FTX1_CALIBRATION)).toBe('S6');
    expect(formatDbm(calibratedToDbm(-33, FTX1_CALIBRATION))).toBe('\u2212106 dBm');
  });

  it('preserves both S-axis breakpoints and endpoints', () => {
    expect(getS9Raw(FTX1_CALIBRATION)).toBe(130);
    expect(getScaleMaxRaw(FTX1_CALIBRATION)).toBe(240);
    expect(rawToSegments(129, FTX1_CALIBRATION)).toBeLessThan(11);
    expect(rawToSegments(130, FTX1_CALIBRATION)).toBe(11);
    expect(rawToSegments(131, FTX1_CALIBRATION)).toBeGreaterThan(11);
    expect(rawToSegments(240, FTX1_CALIBRATION)).toBe(20);
    expect(calibratedToSegments(0, FTX1_CALIBRATION)).toBe(11);
    expect(calibratedToSegments(40, FTX1_CALIBRATION)).toBe(20);
  });

  it('keeps empty and single-knot profiles uncalibrated', () => {
    const single = [{ raw: 130, actual: 0, label: 'S9' }] as const;

    expect(isSmeterCalibrated([])).toBe(false);
    expect(isSmeterCalibrated(single)).toBe(false);
    expect(getCalibratedScaleMaxRaw(single)).toBe(255);
    expect(calibratedToRaw(53, [])).toBe(53);
    expect(calibratedToRaw(53, single)).toBe(53);
    expect(rawToSUnit(53, single)).toBe('53');
    expect(calibratedToDbm(53, single)).toBeNull();
  });

  it('retains input-order semantics for an unsorted profile', () => {
    const unsorted = [FTX1_CALIBRATION[0], FTX1_CALIBRATION[10], FTX1_CALIBRATION[7]];

    expect(calibratedToRaw(0, unsorted)).toBeCloseTo(130 * (21 / 54));
  });
});
