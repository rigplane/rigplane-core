import { describe, it, expect, vi, afterEach } from 'vitest';
import {
  PANORAMA_SETTLE_MS,
  PanoramaViewportCenter,
  panoramaEaseOutCubic,
} from '../panorama-motion';

// MOR-2587: must go through `vi.stubGlobal`, never a plain assignment —
// this file runs in the `fast` pool (`isolate: false`), where the
// file-level `afterEach(() => vi.unstubAllGlobals())` below only restores
// stubs registered with `vi.stubGlobal`. A plain `window.matchMedia = …`
// survived that hook and leaked `{ matches: true }` into every later
// `fast` file sharing the worker (observed victim: InstallPrompt.test.ts
// "isStandalone returns false in normal browser mode").
function enableReducedMotion(): void {
  vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({
    matches: true,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  }) as unknown as typeof window.matchMedia);
}

afterEach(() => vi.unstubAllGlobals());

describe('panoramaEaseOutCubic', () => {
  it('is zero at the start, one at the end, and never overshoots', () => {
    expect(panoramaEaseOutCubic(0)).toBe(0);
    expect(panoramaEaseOutCubic(1)).toBe(1);
    expect(panoramaEaseOutCubic(1.5)).toBe(1);
    expect(panoramaEaseOutCubic(-0.5)).toBe(0);
    let previous = 0;
    for (let t = 0; t <= 1; t += 0.05) {
      const value = panoramaEaseOutCubic(t);
      expect(value).toBeGreaterThanOrEqual(previous);
      expect(value).toBeLessThanOrEqual(1);
      previous = value;
    }
  });
});
describe('PanoramaViewportCenter', () => {
  it('keeps the settle duration inside the approved 80–120 ms band', () => {
    expect(PANORAMA_SETTLE_MS).toBeGreaterThanOrEqual(80);
    expect(PANORAMA_SETTLE_MS).toBeLessThanOrEqual(120);
  });

  it('starts settled at the initial center and lands on it after a long hidden gap', () => {
    const settled = new PanoramaViewportCenter(14_050_000);
    expect(settled.sample(0)).toBe(14_050_000);
    expect(settled.sample(10_000)).toBe(14_050_000);
    expect(settled.settling(0)).toBe(false);
    const animated = new PanoramaViewportCenter(0);
    animated.retarget(500, 0);
    expect(animated.sample(10_000)).toBe(500);
    expect(animated.settling(10_000)).toBe(false);
  });

  it('animates a retune through real intermediate frames, bounded and monotone', () => {
    const center = new PanoramaViewportCenter(14_050_000);
    center.retarget(14_051_000, 0);
    expect(center.sample(0)).toBe(14_050_000);
    const early = center.sample(PANORAMA_SETTLE_MS * 0.25);
    const mid = center.sample(PANORAMA_SETTLE_MS * 0.5);
    const late = center.sample(PANORAMA_SETTLE_MS * 0.75);
    expect(early).toBeGreaterThan(14_050_000);
    expect(early).toBeLessThan(14_051_000);
    expect(mid).toBeGreaterThan(early);
    expect(late).toBeGreaterThan(mid);
    expect(late).toBeLessThan(14_051_000);
    expect(center.settling(PANORAMA_SETTLE_MS * 0.5)).toBe(true);
    expect(center.sample(PANORAMA_SETTLE_MS)).toBe(14_051_000);
    expect(center.sample(PANORAMA_SETTLE_MS + 5_000)).toBe(14_051_000);
    expect(center.settling(PANORAMA_SETTLE_MS)).toBe(false);
    for (let t = 1; t < PANORAMA_SETTLE_MS * 2; t += 3) {
      expect(center.sample(t)).toBeGreaterThanOrEqual(14_050_000);
      expect(center.sample(t)).toBeLessThanOrEqual(14_051_000);
    }
  });

  it('a repeated same-target retarget does not restart the animation (late recentered frame)', () => {
    const center = new PanoramaViewportCenter(0);
    center.retarget(1_000, 0);
    // A same-target retarget mid-flight never restarts the animation.
    center.retarget(1_000, PANORAMA_SETTLE_MS * 0.6);
    const expected = 1_000 * panoramaEaseOutCubic(0.7);
    expect(center.sample(PANORAMA_SETTLE_MS * 0.7)).toBeCloseTo(expected, 9);
    expect(center.sample(PANORAMA_SETTLE_MS)).toBe(1_000);
  });

  it('retargets a rapid reversal from the current value without a jump or overshoot', () => {
    const center = new PanoramaViewportCenter(0);
    center.retarget(1_000, 0);
    const before = center.sample(PANORAMA_SETTLE_MS * 0.4);
    center.retarget(-1_000, PANORAMA_SETTLE_MS * 0.4);
    const after = center.sample(PANORAMA_SETTLE_MS * 0.4 + 1);
    expect(Math.abs(after - before)).toBeLessThan(150);
    expect(after).toBeGreaterThan(-1_000);
    expect(after).toBeLessThan(before + 1);
    let previous = after;
    for (let t = PANORAMA_SETTLE_MS * 0.4 + 2; t <= PANORAMA_SETTLE_MS * 1.4 + 50; t += 2) {
      const value = center.sample(t);
      expect(value).toBeLessThanOrEqual(previous + 1e-9);
      expect(value).toBeGreaterThanOrEqual(-1_000);
      previous = value;
    }
    expect(center.sample(PANORAMA_SETTLE_MS * 1.4)).toBe(-1_000);
  });

  it('reset snaps immediately and kills any running animation', () => {
    const center = new PanoramaViewportCenter(0);
    center.retarget(1_000, 0);
    center.reset(500, PANORAMA_SETTLE_MS * 0.5);
    expect(center.sample(PANORAMA_SETTLE_MS * 0.5)).toBe(500);
    expect(center.sample(PANORAMA_SETTLE_MS)).toBe(500);
    expect(center.settling(PANORAMA_SETTLE_MS * 0.5)).toBe(false);
  });

  it('snaps to the target under prefers-reduced-motion with no animated frames', () => {
    enableReducedMotion();
    const center = new PanoramaViewportCenter(0);
    center.retarget(1_000, 0);
    expect(center.sample(1)).toBe(1_000);
    expect(center.settling(1)).toBe(false);
    center.retarget(-500, 50);
    expect(center.sample(51)).toBe(-500);
  });
});
