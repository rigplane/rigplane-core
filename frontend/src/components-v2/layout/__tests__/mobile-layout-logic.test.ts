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
  it('returns S0 for zero', () => {
    expect(formatSValue(0)).toBe('S0');
  });

  it('returns S0 for negative values', () => {
    expect(formatSValue(-10)).toBe('S0');
  });

  it('returns S-unit for mid-range', () => {
    // raw 60 → 60/120*9 = 4.5 → round = 5
    expect(formatSValue(60)).toBe('S5');
  });

  it('returns S9 at raw 120', () => {
    expect(formatSValue(120)).toBe('S9');
  });

  it('returns S9+60 at raw 241', () => {
    expect(formatSValue(241)).toBe('S9+60');
  });

  it('clamps values above 241 to S9+60', () => {
    expect(formatSValue(255)).toBe('S9+60');
  });

  it('returns S9+ for values above 120', () => {
    const result = formatSValue(200);
    expect(result).toMatch(/^S9\+/);
  });
});

describe('formatDbm', () => {
  it('returns -73 dBm at S9 (raw=120)', () => {
    expect(formatDbm(120)).toBe('-73 dBm');
  });

  it('returns lower dBm for weaker signals', () => {
    const result = formatDbm(0);
    // raw=0 → calibration floor near -127 dBm in the mobile view.
    expect(result).toBe('-127 dBm');
  });

  it('returns higher dBm for strong signals', () => {
    const result = formatDbm(241);
    // raw=241 → S9+60 / about -13 dBm.
    expect(result).toBe('-13 dBm');
  });

  it('clamps values above 241 to the top of the mobile scale', () => {
    expect(formatDbm(255)).toBe('-13 dBm');
  });
});

describe('formatPower', () => {
  it('returns 0W for zero', () => {
    expect(formatPower(0)).toBe('0W');
  });

  it('returns 100W for max (255)', () => {
    expect(formatPower(255)).toBe('100W');
  });

  it('returns approximate wattage for mid-range', () => {
    // 128/255*100 ≈ 50.2 → round = 50
    expect(formatPower(128)).toBe('50W');
  });
});
