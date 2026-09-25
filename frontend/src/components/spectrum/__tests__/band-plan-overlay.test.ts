/**
 * Tests for BandPlanOverlay pure logic extracted to band-plan-logic.ts.
 */
import { describe, it, expect } from 'vitest';
import {
  formatFreq,
  hexToRgb,
  computePosition,
  filterOverlapping,
  shouldSkipFetch,
  type RemoteSegment,
} from '../band-plan-logic';

// ── Tests ──

describe('formatFreq', () => {
  it('formats MHz correctly', () => {
    expect(formatFreq(14_074_000)).toBe('14.074 MHz');
    expect(formatFreq(7_000_000)).toBe('7.000 MHz');
    expect(formatFreq(28_500_000)).toBe('28.500 MHz');
  });

  it('formats kHz correctly', () => {
    expect(formatFreq(500_000)).toBe('500.0 kHz');
    expect(formatFreq(1_000)).toBe('1.0 kHz');
  });

  it('formats Hz correctly', () => {
    expect(formatFreq(500)).toBe('500 Hz');
    expect(formatFreq(0)).toBe('0 Hz');
  });
});

describe('hexToRgb', () => {
  it('converts hex with hash', () => {
    expect(hexToRgb('#FF6A00')).toBe('255,106,0');
    expect(hexToRgb('#000000')).toBe('0,0,0');
    expect(hexToRgb('#FFFFFF')).toBe('255,255,255');
  });

  it('converts hex without hash', () => {
    expect(hexToRgb('4ADE80')).toBe('74,222,128');
  });
});

describe('computePosition', () => {
  const start = 14_000_000;
  const end = 14_350_000;

  it('segment fully inside range', () => {
    const pos = computePosition(14_070_000, 14_150_000, start, end);
    expect(pos.leftPct).toBeCloseTo(20, 0);
    expect(pos.widthPct).toBeCloseTo(22.86, 1);
  });

  it('segment matches full range', () => {
    const pos = computePosition(start, end, start, end);
    expect(pos.leftPct).toBeCloseTo(0, 5);
    expect(pos.widthPct).toBeCloseTo(100, 5);
  });

  it('segment extends beyond left edge is clamped', () => {
    const pos = computePosition(13_900_000, 14_100_000, start, end);
    expect(pos.leftPct).toBe(0); // clamped
    expect(pos.widthPct).toBeCloseTo(28.57, 1);
  });

  it('segment extends beyond right edge is clamped', () => {
    const pos = computePosition(14_300_000, 14_500_000, start, end);
    expect(pos.widthPct).toBeGreaterThan(0);
    // rightPct clamped to 100
    const expectedLeft = ((14_300_000 - start) / (end - start)) * 100;
    expect(pos.leftPct).toBeCloseTo(expectedLeft, 1);
    expect(pos.leftPct + pos.widthPct).toBeCloseTo(100, 1);
  });

  it('segment fully outside returns zero width', () => {
    const pos = computePosition(15_000_000, 15_100_000, start, end);
    // both clamped to 100 → width = 0
    expect(pos.widthPct).toBe(0);
  });
});

describe('filterOverlapping (overlap suppression)', () => {
  const ham: RemoteSegment = {
    start: 14_000_000, end: 14_350_000, mode: 'phone', label: 'Phone',
    color: '#60A5FA', opacity: 0.2, band: '20m', layer: 'ham', priority: 1,
  };

  it('ham segments always pass', () => {
    const result = filterOverlapping([ham], 14_000_000, 14_350_000);
    expect(result).toHaveLength(1);
  });

  it('non-ham segment with >30% overlap is suppressed', () => {
    const broadcast: RemoteSegment = {
      start: 14_100_000, end: 14_300_000, mode: 'broadcast', label: 'BC',
      color: '#C084FC', opacity: 0.2, band: 'BC', layer: 'eibi', priority: 5,
    };
    const result = filterOverlapping([ham, broadcast], 14_000_000, 14_350_000);
    expect(result).toHaveLength(1);
    expect(result[0].layer).toBe('ham');
  });

  it('non-ham segment with <30% overlap is kept', () => {
    // 50kHz overlap out of 500kHz segment = 10%
    const broadcast: RemoteSegment = {
      start: 14_300_000, end: 14_800_000, mode: 'broadcast', label: 'BC',
      color: '#C084FC', opacity: 0.2, band: 'BC', layer: 'eibi', priority: 5,
    };
    const result = filterOverlapping([ham, broadcast], 14_000_000, 14_800_000);
    expect(result).toHaveLength(2);
  });

  it('non-ham segment with zero overlap is kept', () => {
    const broadcast: RemoteSegment = {
      start: 15_000_000, end: 15_500_000, mode: 'broadcast', label: 'BC',
      color: '#C084FC', opacity: 0.2, band: 'BC', layer: 'eibi', priority: 5,
    };
    const result = filterOverlapping([ham, broadcast], 14_000_000, 16_000_000);
    expect(result).toHaveLength(2);
  });

  it('hidden layers are excluded', () => {
    const result = filterOverlapping([ham], 14_000_000, 14_350_000, ['ham']);
    expect(result).toHaveLength(0);
  });

  it('segments outside display range are excluded', () => {
    const outside: RemoteSegment = {
      start: 7_000_000, end: 7_300_000, mode: 'cw', label: 'CW',
      color: '#FF6A00', opacity: 0.2, band: '40m', layer: 'ham', priority: 1,
    };
    const result = filterOverlapping([ham, outside], 14_000_000, 14_350_000);
    expect(result).toHaveLength(1);
    expect(result[0].band).toBe('20m');
  });
});

describe('shouldSkipFetch (containment)', () => {
  it('first fetch is never skipped', () => {
    expect(shouldSkipFetch(14_000_000, 14_350_000, 0, 0)).toBe(false);
  });

  it('identical range is skipped', () => {
    expect(shouldSkipFetch(14_000_000, 14_350_000, 14_000_000, 14_350_000)).toBe(true);
  });

  it('viewport inside the widened fetch range is skipped', () => {
    // The component fetches the viewport plus a half-span margin on each side.
    expect(shouldSkipFetch(14_000_000, 14_350_000, 13_825_000, 14_525_000)).toBe(true);
  });

  it('viewport past the left fetch edge triggers fetch', () => {
    expect(shouldSkipFetch(13_800_000, 14_350_000, 13_825_000, 14_525_000)).toBe(false);
  });

  it('viewport past the right fetch edge triggers fetch', () => {
    expect(shouldSkipFetch(14_000_000, 14_550_000, 13_825_000, 14_525_000)).toBe(false);
  });
});
