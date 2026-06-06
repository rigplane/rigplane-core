import { describe, it, expect } from 'vitest';
import {
  normalize,
  formatPowerWatts,
  normalizePower,
  formatSwr,
  formatAlc,
  formatSMeter,
  getNeedleMarks,
  swrLevel,
  alcLevel,
  idLevel,
  vdLevel,
  compLevel,
  sLevel,
} from './meter-utils';

describe('normalize', () => {
  it('returns 0 for raw=0', () => {
    expect(normalize(0)).toBe(0);
  });
  it('returns 1 for raw=255', () => {
    expect(normalize(255)).toBe(1);
  });
  it('clamps negative to 0', () => {
    expect(normalize(-10)).toBe(0);
  });
  it('clamps >255 to 1', () => {
    expect(normalize(300)).toBe(1);
  });
  it('interpolates midpoint', () => {
    expect(normalize(128)).toBeCloseTo(128 / 255);
  });
});

describe('formatPowerWatts', () => {
  it('returns 0W for raw=0', () => {
    expect(formatPowerWatts(0)).toBe('0W');
  });
  it('returns 50W at knot raw=143', () => {
    expect(formatPowerWatts(143)).toBe('50W');
  });
  it('returns 100W at knot raw=212', () => {
    expect(formatPowerWatts(212)).toBe('100W');
  });
  it('interpolates between 0 and 143', () => {
    // midpoint: raw=71.5 -> ~25W
    const result = formatPowerWatts(72);
    expect(result).toMatch(/^\d+W$/);
    const watts = parseInt(result);
    expect(watts).toBeGreaterThan(20);
    expect(watts).toBeLessThan(30);
  });
  it('clamps raw=255 to 100W (last knot)', () => {
    expect(formatPowerWatts(255)).toBe('100W');
  });
});

describe('normalizePower', () => {
  it('returns 0 for raw=0', () => {
    expect(normalizePower(0)).toBe(0);
  });
  it('returns 0.5 at raw=143 (50W/100)', () => {
    expect(normalizePower(143)).toBeCloseTo(0.5);
  });
  it('returns 1.0 at raw=212 (100W/100)', () => {
    expect(normalizePower(212)).toBeCloseTo(1.0);
  });
  it('interpolates linearly between knots', () => {
    const val = normalizePower(100);
    expect(val).toBeGreaterThan(0);
    expect(val).toBeLessThan(0.5);
  });
});

describe('formatSwr', () => {
  it('returns 1.0 for raw=0', () => {
    expect(formatSwr(0)).toBe('1.0');
  });
  it('returns 1.5 at raw=48', () => {
    expect(formatSwr(48)).toBe('1.5');
  });
  it('returns 2.0 at raw=80', () => {
    expect(formatSwr(80)).toBe('2.0');
  });
  it('returns 3.0 at raw=120', () => {
    expect(formatSwr(120)).toBe('3.0');
  });
  it('returns infinity symbol for raw=255', () => {
    expect(formatSwr(255)).toBe('\u221e');
  });
  it('interpolates between knots', () => {
    const val = parseFloat(formatSwr(64));
    expect(val).toBeGreaterThan(1.5);
    expect(val).toBeLessThan(2.0);
  });
});

describe('formatAlc', () => {
  it('returns 0% for raw=0', () => {
    expect(formatAlc(0)).toBe('0%');
  });
  it('returns 100% for raw=120', () => {
    expect(formatAlc(120)).toBe('100%');
  });
  it('returns 50% for raw=60', () => {
    expect(formatAlc(60)).toBe('50%');
  });
  it('clamps at 100% for raw>120', () => {
    expect(formatAlc(200)).toBe('100%');
  });
  it('clamps at 0% for negative raw', () => {
    expect(formatAlc(-5)).toBe('0%');
  });
});

describe('formatSMeter', () => {
  it('returns S0 for raw=0', () => {
    expect(formatSMeter(0)).toBe('S0');
  });
  it('returns S9 for raw=120', () => {
    expect(formatSMeter(120)).toBe('S9');
  });
  it('returns S9+60 for raw=241', () => {
    expect(formatSMeter(241)).toBe('S9+60');
  });
  it('returns S9+dB for values above 120', () => {
    const result = formatSMeter(180);
    expect(result).toMatch(/^S9\+\d+$/);
    const db = parseInt(result.replace('S9+', ''));
    expect(db).toBeGreaterThan(0);
    expect(db).toBeLessThan(60);
  });
  it('returns correct S-unit below S9', () => {
    // raw=60 -> S-unit = round((60/120)*9) = round(4.5) = 5
    expect(formatSMeter(60)).toBe('S5');
  });
  it('handles raw=255 (above S9+60 range)', () => {
    const result = formatSMeter(255);
    expect(result).toMatch(/^S9\+\d+$/);
  });
});

// ---------------------------------------------------------------------------
// Calibrated bar-fill level normalizers (MOR-482)
// ---------------------------------------------------------------------------

describe('swrLevel (calibrated bar)', () => {
  it('returns 1.0 at SWR 3.0 (raw=120), not 120/255=0.47', () => {
    expect(swrLevel(120)).toBeCloseTo(1.0);
  });
  it('returns ~0.667 at SWR 2.0 (raw=80) — ratio 2.0/3.0', () => {
    expect(swrLevel(80)).toBeCloseTo(2.0 / 3.0);
  });
  it('returns 1.0 for infinite SWR (raw=255)', () => {
    expect(swrLevel(255)).toBe(1.0);
  });
  it('returns ~0.333 at SWR 1.0 (raw=0)', () => {
    expect(swrLevel(0)).toBeCloseTo(1.0 / 3.0);
  });
});

describe('alcLevel (calibrated bar)', () => {
  it('returns 0 for raw=0', () => {
    expect(alcLevel(0)).toBe(0);
  });
  it('returns 1.0 at the redline (raw=120)', () => {
    expect(alcLevel(120)).toBeCloseTo(1.0);
  });
  it('returns 0.5 at half redline (raw=60)', () => {
    expect(alcLevel(60)).toBeCloseTo(0.5);
  });
});

describe('idLevel (calibrated bar)', () => {
  it('returns 1.0 at the 25 A full-scale knot (raw=212)', () => {
    expect(idLevel(212)).toBeCloseTo(1.0);
  });
  it('returns 0.4 at 10 A (raw=151) — 10/25', () => {
    expect(idLevel(151)).toBeCloseTo(10 / 25);
  });
});

describe('vdLevel (calibrated bar)', () => {
  it('returns 1.0 at the 16 V full-scale knot (raw=241)', () => {
    expect(vdLevel(241)).toBeCloseTo(1.0);
  });
  it('returns 0.625 at 10 V (raw=13), not 13/255=0.05', () => {
    expect(vdLevel(13)).toBeCloseTo(10 / 16);
  });
});

describe('compLevel (calibrated bar)', () => {
  it('returns 1.0 at the 30 dB full-scale knot (raw=150)', () => {
    expect(compLevel(150)).toBeCloseTo(1.0);
  });
  it('returns 0.5 at 15 dB (raw=75) — 15/30', () => {
    expect(compLevel(75)).toBeCloseTo(15 / 30);
  });
});

describe('sLevel (calibrated bar)', () => {
  it('returns ~1.0 at S9+60 (raw=241), not 241/255=0.945', () => {
    expect(sLevel(241)).toBeCloseTo(1.0);
  });
  it('returns ~0.498 at S9 (raw=120) — 120/241', () => {
    expect(sLevel(120)).toBeCloseTo(120 / 241);
  });
});

describe('getNeedleMarks', () => {
  it('returns S-meter marks for source "S"', () => {
    const marks = getNeedleMarks('S');
    expect(marks.length).toBe(7);
    expect(marks[0].label).toBe('S1');
    expect(marks[4].label).toBe('S9');
    expect(marks[5].label).toBe('+20');
    expect(marks[6].label).toBe('+40');
    // S9 pos should be 120/255
    expect(marks[4].pos).toBeCloseTo(120 / 255);
  });

  it('returns SWR marks for source "SWR"', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks.length).toBe(4);
    expect(marks[0].label).toBe('1.0');
    expect(marks[1].label).toBe('1.5');
    expect(marks[2].label).toBe('2.0');
    expect(marks[3].label).toBe('3.0');
    expect(marks[0].pos).toBe(0);
    expect(marks[1].pos).toBeCloseTo(48 / 255);
  });

  it('returns POWER marks for source "POWER"', () => {
    const marks = getNeedleMarks('POWER');
    expect(marks.length).toBe(5);
    expect(marks[0].label).toBe('0');
    expect(marks[4].label).toBe('100');
    expect(marks[2].pos).toBe(0.5);
  });

  it('returns same marks for "po" as "POWER"', () => {
    const po = getNeedleMarks('po');
    const power = getNeedleMarks('POWER');
    expect(po).toEqual(power);
  });

  it('all mark positions are in 0-1 range', () => {
    for (const source of ['S', 'SWR', 'POWER', 'po'] as const) {
      for (const mark of getNeedleMarks(source)) {
        expect(mark.pos).toBeGreaterThanOrEqual(0);
        expect(mark.pos).toBeLessThanOrEqual(1);
      }
    }
  });
});
