/**
 * SpectrumPanel pure logic tests: frame parsing edge cases, frequency formatting,
 * scale ticks, drag rate limiting, scope mode classification, click-to-tune.
 */
import { describe, it, expect } from 'vitest';
import {
  formatFreqOffset,
  deriveFreqTicks,
  getDragInterval,
  isFixedScope,
  freqFromPixel,
} from '../spectrum-logic';

describe('formatFreqOffset', () => {
  it('formats zero', () => expect(formatFreqOffset(0)).toBe('0'));
  it('formats sub-kHz', () => {
    expect(formatFreqOffset(500)).toBe('+500');
    expect(formatFreqOffset(-750)).toBe('-750');
  });
  it('formats kHz with rounding', () => {
    expect(formatFreqOffset(12_500)).toBe('+13k');
    expect(formatFreqOffset(-87_500)).toBe('-88k');
  });
  it('formats exact kHz', () => expect(formatFreqOffset(25_000)).toBe('+25k'));
  it('formats MHz with one decimal', () => {
    expect(formatFreqOffset(1_500_000)).toBe('+1.5M');
    expect(formatFreqOffset(-2_000_000)).toBe('-2.0M');
  });
  it('boundary: 1000 Hz → kHz, 1 MHz → MHz', () => {
    expect(formatFreqOffset(1000)).toBe('+1k');
    expect(formatFreqOffset(1_000_000)).toBe('+1.0M');
  });
});

describe('deriveFreqTicks', () => {
  it('returns empty for spanHz <= 0', () => {
    expect(deriveFreqTicks(0)).toEqual([]);
    expect(deriveFreqTicks(-1000)).toEqual([]);
  });

  it('produces 5 ticks at correct positions', () => {
    const ticks = deriveFreqTicks(100_000);
    expect(ticks).toHaveLength(5);
    expect(ticks.map((t) => t.position)).toEqual([0, 25, 50, 75, 100]);
  });

  it('center tick is "0", edges are symmetric', () => {
    const ticks = deriveFreqTicks(350_000);
    expect(ticks[2].label).toBe('0');
    expect(ticks[0].label).toBe('-175k');
    expect(ticks[4].label).toBe('+175k');
  });

  it('quarter ticks match half of edge offsets', () => {
    const ticks = deriveFreqTicks(200_000);
    expect(ticks[1].label).toBe('-50k');
    expect(ticks[3].label).toBe('+50k');
  });
});

describe('getDragInterval — adaptive rate limiting', () => {
  it('slow (<= 200 px/s) → 200ms', () => {
    expect(getDragInterval(0)).toBe(200);
    expect(getDragInterval(200)).toBe(200);
  });
  it('medium (201-600 px/s) → 400ms', () => {
    expect(getDragInterval(201)).toBe(400);
    expect(getDragInterval(600)).toBe(400);
  });
  it('fast (> 600 px/s) → 700ms', () => {
    expect(getDragInterval(601)).toBe(700);
    expect(getDragInterval(1000)).toBe(700);
  });
});

describe('isFixedScope', () => {
  it('CTR=0 and SCROLL-C=2 are not fixed', () => {
    expect(isFixedScope(0)).toBe(false);
    expect(isFixedScope(2)).toBe(false);
  });
  it('FIX=1 and SCROLL-F=3 are fixed', () => {
    expect(isFixedScope(1)).toBe(true);
    expect(isFixedScope(3)).toBe(true);
  });
});

describe('freqFromPixel — click-to-tune calculation', () => {
  it('maps left/center/right to correct frequencies', () => {
    expect(freqFromPixel(0, 0, 1000, 14_000_000, 350_000)).toBe(14_000_000);
    expect(freqFromPixel(500, 0, 1000, 14_000_000, 350_000)).toBe(14_175_000);
    expect(freqFromPixel(1000, 0, 1000, 14_000_000, 350_000)).toBe(14_350_000);
  });
  it('accounts for rect offset', () => {
    expect(freqFromPixel(300, 200, 1000, 14_000_000, 350_000)).toBe(14_035_000);
  });
  it('narrow span → higher resolution per pixel', () => {
    const f1 = freqFromPixel(500, 0, 1000, 14_000_000, 10_000);
    const f2 = freqFromPixel(501, 0, 1000, 14_000_000, 10_000);
    expect(f2 - f1).toBe(10);
  });
});
