import { describe, it, expect } from 'vitest';
import { computeStageScale, MAX_STAGE_SCALE } from '../stage-scale';

describe('computeStageScale', () => {
  it('is width-constrained when the host is relatively narrow', () => {
    // host fits 0.5x on width, 5x on height — the narrower ratio wins.
    const scale = computeStageScale({ width: 100, height: 1000 }, { width: 200, height: 200 });
    expect(scale).toBe(0.5);
  });

  it('is height-constrained when the host is relatively short', () => {
    // host fits 5x on width, 0.5x on height — the narrower ratio wins.
    const scale = computeStageScale({ width: 1000, height: 100 }, { width: 200, height: 200 });
    expect(scale).toBe(0.5);
  });

  it('returns exactly 1 when the host matches the native size', () => {
    const scale = computeStageScale({ width: 200, height: 200 }, { width: 200, height: 200 });
    expect(scale).toBe(1);
  });

  it('caps at MAX_STAGE_SCALE (1) instead of growing past the native size', () => {
    const scale = computeStageScale({ width: 2000, height: 2000 }, { width: 200, height: 200 });
    expect(scale).toBe(MAX_STAGE_SCALE);
    expect(scale).toBe(1);
  });

  it('returns 0 when the host has not been measured yet (zero size)', () => {
    const scale = computeStageScale({ width: 0, height: 0 }, { width: 200, height: 200 });
    expect(scale).toBe(0);
  });

  it('returns 0 when native width is zero', () => {
    const scale = computeStageScale({ width: 200, height: 200 }, { width: 0, height: 200 });
    expect(scale).toBe(0);
  });

  it('returns 0 when native height is zero', () => {
    const scale = computeStageScale({ width: 200, height: 200 }, { width: 200, height: 0 });
    expect(scale).toBe(0);
  });

  // MOR-2147: every non-degenerate case above uses a square 200x200 native
  // box, so an axis swap (host.width/native.height paired with
  // host.height/native.width) survives all of them undetected — a square
  // box can't tell width from height apart.
  // `presentation/layouts/lcd-declarations.ts` declares a 1280x540 native
  // stage; these two pin that non-square shape.
  describe('non-square native size (as declared in lcd-declarations.ts)', () => {
    it('returns exactly 1 when a non-square host matches a non-square native size', () => {
      const scale = computeStageScale({ width: 1280, height: 540 }, { width: 1280, height: 540 });
      expect(scale).toBe(1);
    });

    it('is width-constrained on a non-square box', () => {
      // host fits 0.5x on width, 1x on height — the narrower ratio wins.
      const scale = computeStageScale({ width: 640, height: 540 }, { width: 1280, height: 540 });
      expect(scale).toBe(0.5);
    });
  });
});
