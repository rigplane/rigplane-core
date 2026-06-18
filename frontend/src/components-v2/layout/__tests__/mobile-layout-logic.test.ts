import { describe, it, expect } from 'vitest';
import {
  SSB_STEPS, CW_STEPS, AM_STEPS, FM_STEPS, DEFAULT_STEPS,
  getStepsForMode, formatStep, formatSValue, formatDbm, formatPower,
} from '../mobile-layout-logic';

describe('getStepsForMode', () => {
  it('returns SSB_STEPS for USB', () => {
    expect(getStepsForMode('USB')).toBe(SSB_STEPS);
  });

  it('returns SSB_STEPS for LSB', () => {
    expect(getStepsForMode('LSB')).toBe(SSB_STEPS);
  });

  it('returns SSB_STEPS for lowercase usb', () => {
    expect(getStepsForMode('usb')).toBe(SSB_STEPS);
  });

  it('returns CW_STEPS for CW', () => {
    expect(getStepsForMode('CW')).toBe(CW_STEPS);
  });

  it('returns CW_STEPS for CW-R', () => {
    expect(getStepsForMode('CW-R')).toBe(CW_STEPS);
  });

  it('returns AM_STEPS for AM', () => {
    expect(getStepsForMode('AM')).toBe(AM_STEPS);
  });

  it('returns FM_STEPS for FM', () => {
    expect(getStepsForMode('FM')).toBe(FM_STEPS);
  });

  it('returns DEFAULT_STEPS for unknown mode', () => {
    expect(getStepsForMode('RTTY')).toBe(DEFAULT_STEPS);
  });

  it('returns DEFAULT_STEPS for empty string', () => {
    expect(getStepsForMode('')).toBe(DEFAULT_STEPS);
  });
});

describe('formatStep', () => {
  it('formats Hz for values below 1000', () => {
    expect(formatStep(100)).toBe('100 Hz');
    expect(formatStep(50)).toBe('50 Hz');
    expect(formatStep(10)).toBe('10 Hz');
  });

  it('formats kHz for values >= 1000', () => {
    expect(formatStep(1000)).toBe('1 kHz');
    expect(formatStep(5000)).toBe('5 kHz');
    expect(formatStep(10000)).toBe('10 kHz');
    expect(formatStep(12500)).toBe('12.5 kHz');
  });
});

describe('formatSValue', () => {
  it('returns S9 for a calibrated 0 dB-rel-S9 reading', () => {
    expect(formatSValue(0)).toBe('S9');
  });

  it('returns S0 at the calibrated floor', () => {
    expect(formatSValue(-54)).toBe('S0');
  });

  it('returns S-unit for calibrated sub-S9 values', () => {
    expect(formatSValue(-24)).toBe('S5');
  });

  it('returns S9+20 at +20 dB-rel-S9', () => {
    expect(formatSValue(20)).toBe('S9+20');
  });

  it('clamps strong readings to the top of the mobile scale', () => {
    expect(formatSValue(255)).toBe('S9+40');
  });

  it('returns S9+ for values above S9', () => {
    const result = formatSValue(33);
    expect(result).toMatch(/^S9\+/);
  });
});

describe('formatDbm', () => {
  it('returns -73 dBm at S9 (0 dB-rel-S9)', () => {
    expect(formatDbm(0)).toBe('-73 dBm');
  });

  it('returns lower dBm for weaker signals', () => {
    const result = formatDbm(-54);
    expect(result).toBe('-127 dBm');
  });

  it('returns higher dBm for strong signals', () => {
    const result = formatDbm(20);
    expect(result).toBe('-53 dBm');
  });

  it('clamps values above the top of the mobile scale', () => {
    expect(formatDbm(255)).toBe('-33 dBm');
  });
});

describe('formatPower', () => {
  // power_level is served normalized 0.0-1.0 (MOR-334 contract), not raw 0-255.
  it('returns 0W for zero', () => {
    expect(formatPower(0)).toBe('0W');
  });

  it('returns 100W for max (1.0)', () => {
    expect(formatPower(1)).toBe('100W');
  });

  it('returns approximate wattage for mid-range', () => {
    // 0.5 * 100 → 50
    expect(formatPower(0.5)).toBe('50W');
  });

  it('clamps out-of-range normalized input', () => {
    expect(formatPower(1.4)).toBe('100W');
  });
});
