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
  it('returns S9 for the calibrated S9 point', () => {
    expect(formatSValue(0)).toBe('S9');
  });

  it('returns S0 for the calibrated low end', () => {
    expect(formatSValue(-54)).toBe('S0');
  });

  it('returns S-unit below S9', () => {
    expect(formatSValue(-24)).toBe('S5');
  });

  it('clamps very weak readings at S0', () => {
    expect(formatSValue(-80)).toBe('S0');
  });

  it('returns S9+ for values above S9', () => {
    expect(formatSValue(20)).toBe('S9+20');
  });
});

describe('formatDbm', () => {
  it('returns -73 dBm at S9', () => {
    expect(formatDbm(0)).toBe('-73 dBm');
  });

  it('returns lower dBm for weaker signals', () => {
    expect(formatDbm(-54)).toBe('-127 dBm');
  });

  it('returns higher dBm for strong signals', () => {
    expect(formatDbm(20)).toBe('-53 dBm');
  });
});

describe('formatPower', () => {
  it('returns 0W for zero', () => {
    expect(formatPower(0)).toBe('0%');
  });

  it('returns 100% for max normalized value', () => {
    expect(formatPower(1)).toBe('100%');
  });

  it('returns percent for mid-range normalized value', () => {
    expect(formatPower(0.5)).toBe('50%');
  });
});
