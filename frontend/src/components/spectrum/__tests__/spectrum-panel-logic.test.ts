/**
 * SpectrumPanel pure logic tests: frame parsing edge cases, frequency formatting,
 * scale ticks, drag rate limiting, scope mode classification, click-to-tune.
 */
import { describe, it, expect } from 'vitest';
import {
  freqTickDecimals,
  getDragInterval,
  isRadioOwnedWindowScope,
  freqFromPixel,
  nativeWidthFromSpanChoice,
} from '../spectrum-logic';
import { isSpanApplicable } from '../spectrum-toolbar-logic';
import fixture from '../../../../../contracts/scope-mode-families.json';

describe('freqTickDecimals', () => {
  it('keeps kHz resolution for wide windows', () => {
    expect(freqTickDecimals(25_000)).toBe(3);
    expect(freqTickDecimals(1000)).toBe(3);
  });
  it('adds decimals so sub-kHz tick spacing stays distinct', () => {
    expect(freqTickDecimals(195.3125)).toBe(4);
    expect(freqTickDecimals(20)).toBe(5);
    expect(freqTickDecimals(0.5)).toBe(6);
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

describe('isRadioOwnedWindowScope', () => {
  it('CTR=0 is client-centered', () => {
    expect(isRadioOwnedWindowScope(0)).toBe(false);
  });
  it('FIX=1, SCROLL-C=2 and SCROLL-F=3 are radio-owned', () => {
    expect(isRadioOwnedWindowScope(1)).toBe(true);
    expect(isRadioOwnedWindowScope(2)).toBe(true);
    expect(isRadioOwnedWindowScope(3)).toBe(true);
  });
  it('agrees with the shared server fixture', () => {
    const family: readonly number[] = fixture.radio_owned_window_scope_modes;
    for (const mode of [0, 1, 2, 3]) {
      expect(isRadioOwnedWindowScope(mode)).toBe(family.includes(mode));
    }
  });
});

describe('isSpanApplicable', () => {
  it('CTR=0 and SCROLL-C=2 take the span preset, FIX=1 and SCROLL-F=3 the edge number', () => {
    expect(isSpanApplicable(0)).toBe(true);
    expect(isSpanApplicable(2)).toBe(true);
    expect(isSpanApplicable(1)).toBe(false);
    expect(isSpanApplicable(3)).toBe(false);
  });
  it('agrees with the shared server fixture', () => {
    const family: readonly number[] = fixture.span_applicable_scope_modes;
    for (const mode of [0, 1, 2, 3]) {
      expect(isSpanApplicable(mode)).toBe(family.includes(mode));
    }
  });
});

describe('nativeWidthFromSpanChoice (MOR-4451)', () => {
  // span_choices_hz states FULL view widths: the reader returns the published
  // value unchanged — reintroducing a client ×2 breaks this test.
  const viewWidths = [5000, 10000, 20000, 50000, 100000, 200000, 500000, 1000000];
  it('returns the published view width unchanged', () => {
    expect(nativeWidthFromSpanChoice(viewWidths, { status: 'known', value: 5 })).toBe(200000);
    expect(nativeWidthFromSpanChoice(viewWidths, { status: 'known', value: 0 })).toBe(5000);
  });
  it('returns null for unknown/out-of-range/invalid readings', () => {
    expect(nativeWidthFromSpanChoice(viewWidths, { status: 'stale', value: 5 })).toBeNull();
    expect(nativeWidthFromSpanChoice(viewWidths, { status: 'known', value: 8 })).toBeNull();
    expect(nativeWidthFromSpanChoice(viewWidths, { status: 'known', value: -1 })).toBeNull();
    expect(nativeWidthFromSpanChoice([], { status: 'known', value: 0 })).toBeNull();
    expect(nativeWidthFromSpanChoice(undefined, { status: 'known', value: 0 })).toBeNull();
    expect(nativeWidthFromSpanChoice(viewWidths, undefined)).toBeNull();
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
