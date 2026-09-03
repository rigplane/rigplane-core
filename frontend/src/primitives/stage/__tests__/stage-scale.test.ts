import { describe, it, expect } from 'vitest';
import { computeStageCenterOffset, computeStageScale, MAX_STAGE_SCALE } from '../stage-scale';

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

describe('computeStageCenterOffset', () => {
  it('returns zero offset when the scaled box exactly fills the host (scale 1, matching size)', () => {
    // Scale-1 control: a centred box that already fills its host needs no
    // shift — the formula must degrade to a no-op here, not just at some
    // shrunk scale.
    const offset = computeStageCenterOffset({ width: 200, height: 200 }, { width: 200, height: 200 }, 1);
    expect(offset).toEqual({ x: 0, y: 0 });
  });

  it('splits the leftover host space evenly on both axes for a square shrink', () => {
    // native 200x200 at scale 0.5 -> 100x100 scaled box inside a 300x300
    // host: 200px of leftover space per axis, 100px on each side.
    const offset = computeStageCenterOffset({ width: 300, height: 300 }, { width: 200, height: 200 }, 0.5);
    expect(offset).toEqual({ x: 100, y: 100 });
  });

  it('computes independent offsets per axis for a non-square host (letterboxing)', () => {
    // native 1280x540 at scale 0.5 -> 640x270 scaled box. Host 1000x400:
    // leftover width 360 (180 each side), leftover height 130 (65 each side).
    const offset = computeStageCenterOffset({ width: 1000, height: 400 }, { width: 1280, height: 540 }, 0.5);
    expect(offset).toEqual({ x: 180, y: 65 });
  });

  it('returns zero offset for a degenerate (unmeasured) host', () => {
    const offset = computeStageCenterOffset({ width: 0, height: 0 }, { width: 200, height: 200 }, 0);
    expect(offset).toEqual({ x: 0, y: 0 });
  });
});
