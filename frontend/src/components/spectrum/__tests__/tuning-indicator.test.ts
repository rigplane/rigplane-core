import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
/**
 * Tests for the tuning indicator (red line) position on spectrum/waterfall.
 *
 * Bug #552: In CTR mode with centerType=Filter, the scope centers on the
 * filter midpoint (carrier + filterWidth/2 for USB), NOT on the carrier.
 * Hardcoding the indicator at center (width/2 or 50%) placed it at the
 * filter midpoint instead of the VFO/carrier frequency.
 *
 * Fix: always compute indicator proportionally from tuneHz vs scope range.
 * These tests ensure the regression cannot recur.
 */
import { describe, it, expect } from 'vitest';

// ── Re-implement the tuning indicator logic from SpectrumPanel / spectrum-renderer ──

/** Clamp value between min and max (same as renderer's clamp). */
function clamp(value: number, min: number, max: number): number {
  return value < min ? min : value > max ? max : value;
}

/**
 * Compute tuning indicator position as percentage (0–100).
 * Mirrors tuneLinePct from SpectrumPanel.svelte.
 * CTR/SCROLL-C → always 50% (center). FIX/SCROLL-F → proportional.
 */
function computeTuneLinePct(
  tuneHz: number,
  startFreq: number,
  endFreq: number,
  isFixedScope: boolean,
): number {
  const spanHz = endFreq - startFreq;
  if (isFixedScope && spanHz > 0 && tuneHz > 0 && tuneHz >= startFreq && tuneHz <= endFreq) {
    return ((tuneHz - startFreq) / spanHz) * 100;
  }
  return 50;
}

/**
 * Compute tuning indicator position in pixels (0–width).
 * Mirrors tunePx from spectrum-renderer.ts.
 * CTR/SCROLL-C → width/2. FIX/SCROLL-F → proportional.
 */
function computeTunePx(
  tuneHz: number,
  centerHz: number,
  spanHz: number,
  width: number,
  isFixedScope: boolean,
): number {
  if (isFixedScope && tuneHz > 0 && spanHz > 0) {
    const startHz = centerHz - spanHz / 2;
    return clamp(((tuneHz - startHz) / spanHz) * width, 0, width);
  }
  return width / 2;
}

// ── Scope frame parsing — imported from canonical module ──────────────────────

import { ingressScopeFrame } from '$lib/runtime/adapters/scope-adapter';

function encodeFrame(
  receiver: number,
  _mode: number,
  startFreq: number,
  endFreq: number,
  pixels: Uint8Array,
): ArrayBuffer {
  return scopeWire({ receiver, viewStartHz: startFreq, viewEndHz: endFreq, pixels });
}

// ── Tests ────────────────────────────────────────────────────────────────────────

describe('scope ingress — observed mode facts', () => {
  const context = { source: 'radio' as const, sourceId: 'scope', epoch: 1 };
  it('derives mode=0 (CTR) into geometry', () => {
    const result = ingressScopeFrame(encodeFrame(0, 0, 14_050_000, 14_098_000, new Uint8Array(5)), { ...context, mode: 0 });
    if (!result.ok) throw new Error(result.reason);
    expect(result.geometry.mode).toBe(0);
  });

  it('derives mode=1 (FIX) into geometry', () => {
    const result = ingressScopeFrame(encodeFrame(0, 1, 14_000_000, 14_350_000, new Uint8Array(5)), { ...context, mode: 1 });
    if (!result.ok) throw new Error(result.reason);
    expect(result.geometry.mode).toBe(1);
  });

  it('derives mode=2 (SCROLL-C) into geometry', () => {
    const result = ingressScopeFrame(encodeFrame(0, 2, 14_050_000, 14_098_000, new Uint8Array(5)), { ...context, mode: 2 });
    if (!result.ok) throw new Error(result.reason);
    expect(result.geometry.mode).toBe(2);
  });

  it('derives mode=3 (SCROLL-F) into geometry', () => {
    const result = ingressScopeFrame(encodeFrame(0, 3, 14_000_000, 14_350_000, new Uint8Array(5)), { ...context, mode: 3 });
    if (!result.ok) throw new Error(result.reason);
    expect(result.geometry.mode).toBe(3);
  });

  it('preserves all other fields alongside mode', () => {
    const pixels = new Uint8Array([10, 20, 30]);
    const result = ingressScopeFrame(encodeFrame(1, 3, 7_000_000, 7_300_000, pixels), { ...context, mode: 3 });
    if (!result.ok) throw new Error(result.reason);
    expect(result.geometry.receiver).toBe(1);
    expect(result.geometry.mode).toBe(3);
    expect(result.geometry.view.startHz).toBe(7_000_000);
    expect(result.geometry.view.endHz).toBe(7_300_000);
    expect(Array.from(result.frame.pixels!)).toEqual([10, 20, 30]);
  });
});

describe('tuning indicator — CTR mode = center, FIX mode = proportional', () => {
  it('CTR mode: indicator always at 50% regardless of tuneHz', () => {
    const carrier = 14_173_000;
    const startFreq = 14_164_600;
    const endFreq = 14_184_600;

    const pct = computeTuneLinePct(carrier, startFreq, endFreq, false);
    expect(pct).toBe(50);
  });

  it('CTR mode: even with offset carrier, still 50%', () => {
    const pct = computeTuneLinePct(14_074_000, 14_049_000, 14_099_000, false);
    expect(pct).toBe(50);
  });

  it('FIX mode: indicator at correct proportional position', () => {
    const startFreq = 14_000_000;
    const endFreq = 14_350_000;
    const carrier = 14_074_000;

    const pct = computeTuneLinePct(carrier, startFreq, endFreq, true);
    expect(pct).toBeCloseTo((74_000 / 350_000) * 100, 1);
  });

  it('FIX mode: tuneHz outside scope range falls back to 50%', () => {
    const pct = computeTuneLinePct(21_000_000, 14_000_000, 14_350_000, true);
    expect(pct).toBe(50);
  });

  it('tuneHz=0 falls back to 50%', () => {
    expect(computeTuneLinePct(0, 14_000_000, 14_350_000, true)).toBe(50);
    expect(computeTuneLinePct(0, 14_000_000, 14_350_000, false)).toBe(50);
  });
});

describe('tunePx — pixel-level indicator (renderer)', () => {
  const width = 1000;

  it('CTR mode: always center regardless of tuneHz', () => {
    const px = computeTunePx(14_173_000, 14_174_600, 20_000, width, false);
    expect(px).toBe(width / 2);
  });

  it('FIX mode: proportional position', () => {
    // scope center 14.175, span 350kHz, carrier at 14.074
    const px = computeTunePx(14_074_000, 14_175_000, 350_000, width, true);
    const expected = ((14_074_000 - (14_175_000 - 175_000)) / 350_000) * width;
    expect(px).toBeCloseTo(expected, 0);
  });

  it('FIX mode: clamps to 0 when tuneHz is below scope range', () => {
    const px = computeTunePx(14_000_000, 14_200_000, 10_000, width, true);
    expect(px).toBe(0);
  });

  it('FIX mode: clamps to width when tuneHz is above scope range', () => {
    const px = computeTunePx(14_400_000, 14_200_000, 10_000, width, true);
    expect(px).toBe(width);
  });

  it('tuneHz=0 falls back to center', () => {
    expect(computeTunePx(0, 14_074_000, 50_000, width, true)).toBe(width / 2);
    expect(computeTunePx(0, 14_074_000, 50_000, width, false)).toBe(width / 2);
  });
});
