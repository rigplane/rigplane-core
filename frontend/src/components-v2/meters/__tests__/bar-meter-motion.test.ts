import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  MeterContinuitySession,
  MeterSourceIdentity,
} from '../../../primitives/meters/meter-ballistics.svelte';
import { createBarMeterMotion } from '../bar-meter-motion.svelte';

const MAIN_SOURCE = {
  providerGeneration: 1,
  scope: 'receiver',
  receiver: 'MAIN',
  path: 'main.rfPower',
} as const satisfies MeterSourceIdentity;
const SUB_SOURCE = {
  providerGeneration: 1,
  scope: 'receiver',
  receiver: 'SUB',
  path: 'sub.rfPower',
} as const satisfies MeterSourceIdentity;
const SESSION_1 = { controlSessionEpoch: 1 } as const satisfies MeterContinuitySession;
const SESSION_2 = { controlSessionEpoch: 2 } as const satisfies MeterContinuitySession;

interface MotionHarness {
  readonly activeFrames: number;
  readonly activeIntervals: number;
  readonly listenerCount: number;
  readonly intervalDelays: readonly number[];
  flushFrame(elapsedMilliseconds: number): void;
  setReduced(reduced: boolean): void;
  restore(): void;
}

function installMotionHarness(initialReduced = false): MotionHarness {
  let reduced = initialReduced;
  let nextFrameId = 1;
  let nextIntervalId = 1;
  const frames = new Map<number, FrameRequestCallback>();
  const intervals = new Map<number, () => void>();
  const intervalDelays: number[] = [];
  const listeners = new Set<() => void>();
  const mql = {
    get matches() { return reduced; },
    addEventListener: (_type: string, callback: () => void) => { listeners.add(callback); },
    removeEventListener: (_type: string, callback: () => void) => { listeners.delete(callback); },
  } as unknown as MediaQueryList;
  const originalMatchMedia = window.matchMedia;
  const originalSetInterval = window.setInterval;
  const originalClearInterval = window.clearInterval;
  window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
  window.setInterval = ((callback: TimerHandler, delay?: number) => {
    const id = nextIntervalId++;
    intervals.set(id, callback as () => void);
    intervalDelays.push(delay ?? 0);
    return id;
  }) as typeof window.setInterval;
  window.clearInterval = ((id: number) => { intervals.delete(id); }) as typeof window.clearInterval;
  const requestFrame = vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
    const id = nextFrameId++;
    frames.set(id, callback);
    return id;
  });
  const cancelFrame = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
    frames.delete(id);
  });
  return {
    get activeFrames() { return frames.size; },
    get activeIntervals() { return intervals.size; },
    get listenerCount() { return listeners.size; },
    get intervalDelays() { return intervalDelays; },
    flushFrame(elapsedMilliseconds) {
      const next = frames.entries().next().value as [number, FrameRequestCallback] | undefined;
      if (next === undefined) throw new Error('No animation frame is scheduled');
      frames.delete(next[0]);
      next[1](performance.now() + elapsedMilliseconds);
    },
    setReduced(next) {
      reduced = next;
      listeners.forEach((callback) => callback());
    },
    restore() {
      window.matchMedia = originalMatchMedia;
      window.setInterval = originalSetInterval;
      window.clearInterval = originalClearInterval;
      requestFrame.mockRestore();
      cancelFrame.mockRestore();
    },
  };
}

let harness: MotionHarness | undefined;

beforeEach(() => {
  harness = installMotionHarness();
});

afterEach(() => {
  harness?.restore();
  harness = undefined;
});

describe('createBarMeterMotion', () => {
  it('exposes one stable passive fractions-only frame', () => {
    const binding = createBarMeterMotion({
      value: 0.257,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    const frame = binding.frame;
    expect(Object.keys(frame)).toEqual(['smoothedFraction', 'peakFraction']);
    expect(frame.smoothedFraction).toBe(0.257);
    expect(frame.peakFraction).toBe(0.257);

    binding.sync({
      value: 0.1234,
      peakEnabled: true,
      source: SUB_SOURCE,
      session: SESSION_1,
    });
    expect(binding.frame).toBe(frame);
    expect(frame.smoothedFraction).toBe(0.1234);
  });

  it('clamps only the smoothing fraction while retaining a finite over-range peak sample', () => {
    const binding = createBarMeterMotion({
      value: 1.6,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    expect(binding.frame.smoothedFraction).toBe(1);
    expect(binding.frame.peakFraction).toBe(1.6);

    binding.sync({
      value: -0.5,
      peakEnabled: true,
      source: SUB_SOURCE,
      session: SESSION_1,
    });
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBe(-0.5);
  });

  it('clears both channels for null samples and explicit null continuity', () => {
    const binding = createBarMeterMotion({
      value: 0.8,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    binding.sync({ value: null, peakEnabled: true, source: MAIN_SOURCE, session: SESSION_1 });
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();

    binding.sync({ value: 0.6, peakEnabled: true, source: MAIN_SOURCE, session: SESSION_1 });
    binding.sync({ value: 0.9, peakEnabled: true, source: null, session: SESSION_1 });
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();
  });

  it('preserves the existing non-finite arithmetic instead of inventing absence', () => {
    const nan = createBarMeterMotion({
      value: Number.NaN,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    expect(Number.isNaN(nan.frame.smoothedFraction)).toBe(true);
    expect(Number.isNaN(nan.frame.peakFraction)).toBe(true);

    const infinity = createBarMeterMotion({
      value: Number.POSITIVE_INFINITY,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    expect(infinity.frame.smoothedFraction).toBe(1);
    expect(infinity.frame.peakFraction).toBe(Number.POSITIVE_INFINITY);
  });

  it('observes source and session boundaries synchronously', () => {
    const binding = createBarMeterMotion({
      value: 0.8,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    binding.sync({ value: 0.2, peakEnabled: true, source: MAIN_SOURCE, session: SESSION_1 });
    expect(binding.frame.smoothedFraction).toBe(0.8);
    expect(binding.frame.peakFraction).toBe(0.8);

    binding.sync({ value: 0.3, peakEnabled: true, source: SUB_SOURCE, session: SESSION_1 });
    expect(binding.frame.smoothedFraction).toBe(0.3);
    expect(binding.frame.peakFraction).toBe(0.3);
    binding.sync({ value: 0.4, peakEnabled: true, source: MAIN_SOURCE, session: SESSION_2 });
    expect(binding.frame.smoothedFraction).toBe(0.4);
    expect(binding.frame.peakFraction).toBe(0.4);
  });

  it('owns the existing schedules and tears them down idempotently', () => {
    const binding = createBarMeterMotion({ value: 0.8, peakEnabled: true });
    binding.start();
    binding.start();
    expect(harness!.activeFrames).toBe(1);
    expect(harness!.activeIntervals).toBe(1);
    expect(harness!.intervalDelays).toEqual([100]);
    expect(harness!.listenerCount).toBe(2);

    binding.stop();
    binding.stop();
    expect(harness!.activeFrames).toBe(0);
    expect(harness!.activeIntervals).toBe(0);
    expect(harness!.listenerCount).toBe(0);
  });

  it('retains the 0.08 attack and 0.2 release response', () => {
    const attack = createBarMeterMotion({
      value: 0,
      peakEnabled: false,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    attack.sync({ value: 1, peakEnabled: false, source: MAIN_SOURCE, session: SESSION_1 });
    attack.start();
    harness!.flushFrame(50);
    expect(attack.frame.smoothedFraction).toBeCloseTo(1 - Math.exp(-0.05 / 0.08), 2);
    attack.stop();

    const release = createBarMeterMotion({
      value: 1,
      peakEnabled: false,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    release.sync({ value: 0, peakEnabled: false, source: MAIN_SOURCE, session: SESSION_1 });
    release.start();
    harness!.flushFrame(50);
    expect(release.frame.smoothedFraction).toBeCloseTo(Math.exp(-0.05 / 0.2), 2);
    release.stop();
  });

  it('keeps the peak schedule disabled when peak is disabled', () => {
    const binding = createBarMeterMotion({ value: 0.8, peakEnabled: false });
    binding.start();
    expect(harness!.activeFrames).toBe(1);
    expect(harness!.activeIntervals).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();
    binding.stop();
  });

  it('resets the peak independently of its passive frame', () => {
    const binding = createBarMeterMotion({
      value: 0.8,
      peakEnabled: true,
      source: MAIN_SOURCE,
      session: SESSION_1,
    });
    const frame = binding.frame;
    binding.resetPeak();
    expect(binding.frame).toBe(frame);
    expect(frame.smoothedFraction).toBe(0.8);
    expect(frame.peakFraction).toBeNull();
  });

  it('stops both schedules for reduced motion and restores them afterwards', () => {
    const binding = createBarMeterMotion({ value: 0.5, peakEnabled: true });
    binding.start();
    expect(harness!.activeFrames).toBe(1);
    expect(harness!.activeIntervals).toBe(1);

    harness!.setReduced(true);
    expect(harness!.activeFrames).toBe(0);
    expect(harness!.activeIntervals).toBe(0);
    harness!.setReduced(false);
    expect(harness!.activeFrames).toBe(1);
    expect(harness!.activeIntervals).toBe(1);
    binding.stop();
  });
});
