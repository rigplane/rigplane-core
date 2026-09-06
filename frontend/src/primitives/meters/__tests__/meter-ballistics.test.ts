import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import {
  createElapsedEnvelopePeakStrategy,
  createFrameStepPeakStrategy,
  createMeterBallistics,
  type MeterMotionHost,
  type MeterSmoother,
} from '../meter-ballistics.svelte';

interface TestHost extends MeterMotionHost {
  advance(milliseconds: number): void;
  flushFrame(milliseconds?: number): void;
  flushIntervals(): void;
  setReduced(reduced: boolean): void;
  readonly activeFrames: number;
  readonly activeIntervals: number;
  readonly listenerCount: number;
}

function createHost(initialReduced = false): TestHost {
  let now = 0;
  let reduced = initialReduced;
  let nextId = 1;
  const frames = new Map<number, FrameRequestCallback>();
  const intervals = new Map<number, () => void>();
  const listeners = new Set<(value: boolean) => void>();
  return {
    now: () => now,
    requestFrame: (callback) => {
      const id = nextId++;
      frames.set(id, callback);
      return id;
    },
    cancelFrame: (id) => { frames.delete(id); },
    setInterval: (callback) => {
      const id = nextId++;
      intervals.set(id, callback);
      return id as unknown as ReturnType<typeof setInterval>;
    },
    clearInterval: (id) => { intervals.delete(id as unknown as number); },
    prefersReducedMotion: () => reduced,
    onReducedMotionChange: (callback) => {
      listeners.add(callback);
      return () => { listeners.delete(callback); };
    },
    advance: (milliseconds) => { now += milliseconds; },
    flushFrame: (milliseconds = 16.67) => {
      now += milliseconds;
      const pending = [...frames.values()];
      frames.clear();
      pending.forEach((callback) => callback(now));
    },
    flushIntervals: () => { intervals.forEach((callback) => callback()); },
    setReduced: (value) => {
      reduced = value;
      listeners.forEach((callback) => callback(value));
    },
    get activeFrames() { return frames.size; },
    get activeIntervals() { return intervals.size; },
    get listenerCount() { return listeners.size; },
  };
}

function createFakeSmoother(initial = 0): MeterSmoother & { set(value: number): void } {
  let current = initial;
  return {
    get value() { return current; },
    update: vi.fn((value: number) => { current = value; }),
    reset: vi.fn((value: number) => { current = value; }),
    start: vi.fn(),
    stop: vi.fn(),
    set: (value) => { current = value; },
  };
}

function createMotionAwareSmoother(host: TestHost): MeterSmoother {
  let current = 0;
  let target = 0;
  let unsubscribe: (() => void) | undefined;
  return {
    get value() { return current; },
    update(value) {
      target = value;
      if (host.prefersReducedMotion()) current = target;
    },
    reset(value) { current = target = value; },
    start() {
      unsubscribe = host.onReducedMotionChange((reduced) => {
        if (reduced) current = target;
      });
    },
    stop() {
      unsubscribe?.();
      unsubscribe = undefined;
    },
  };
}

function frameSetup(options: { reduced?: boolean; decrement?: () => number } = {}) {
  const host = createHost(options.reduced);
  const smoother = createFakeSmoother();
  const meter = createMeterBallistics(smoother, host, {
    peakSource: 'smoothed',
    ticker: { kind: 'animation-frame' },
    peak: createFrameStepPeakStrategy({
      holdMilliseconds: 1000,
      decrementPerFrame: options.decrement ?? (() => 1),
    }),
  });
  return { host, smoother, meter };
}

function elapsedSetup(reduced = false) {
  const host = createHost(reduced);
  const smoother = createFakeSmoother();
  const update = vi.fn((state: { latchedPeak: number; latchedAt: number } | undefined, value: number, now: number) => (
    state === undefined || value > state.latchedPeak || now - state.latchedAt > 1500
      ? { latchedPeak: value, latchedAt: now }
      : state
  ));
  const display = vi.fn((state: { latchedPeak: number; latchedAt: number }, value: number, now: number) => (
    Math.max(value, state.latchedPeak * (1 - (now - state.latchedAt) / 1500))
  ));
  const meter = createMeterBallistics(smoother, host, {
    peakSource: 'sample',
    ticker: { kind: 'interval', milliseconds: 100 },
    peak: createElapsedEnvelopePeakStrategy({
      decayMilliseconds: 1500,
      updatePeakHold: update,
      peakHoldDisplay: display,
    }),
  });
  return { host, smoother, meter, update, display };
}

describe('createMeterBallistics frame-step strategy', () => {
  it('is the shared owner without importing renderer or smoother implementations', () => {
    const primitive = readFileSync('src/primitives/meters/meter-ballistics.svelte.ts', 'utf8');
    expect(primitive).not.toMatch(/components-v2|createSmoother|Math\.exp|calibrated|SEG_COUNT|runtime\//);
    for (const component of ['LinearSMeter.svelte', 'BarGauge.svelte']) {
      const source = readFileSync(`src/components-v2/meters/${component}`, 'utf8');
      expect(source.match(/createMeterBallistics\(/g)).toHaveLength(1);
      expect(source.match(/createSmoother\(/g)).toHaveLength(1);
    }
  });

  it('delegates smoothing and clears both outputs when either coordinate is absent', () => {
    const { meter, smoother } = frameSetup();
    meter.sync({ sample: 8, smoothTarget: 12, peakEnabled: true });
    expect(smoother.update).toHaveBeenCalledExactlyOnceWith(12);
    expect(meter.view).toEqual({ smoothedValue: 12, peakValue: 12 });

    meter.sync({ sample: null, smoothTarget: 12, peakEnabled: true });
    expect(smoother.reset).toHaveBeenLastCalledWith(0);
    expect(meter.view).toEqual({ smoothedValue: 0, peakValue: null });
    meter.sync({ sample: 8, smoothTarget: null, peakEnabled: true });
    expect(meter.view).toEqual({ smoothedValue: 0, peakValue: null });
  });

  it('relatches equality, holds after separation, then decrements once per delivered frame', () => {
    const { host, meter, smoother } = frameSetup();
    meter.sync({ sample: 10, smoothTarget: 10, peakEnabled: true });
    meter.start();
    host.advance(900);
    host.flushFrame(0);
    smoother.set(8);
    host.flushFrame(900);
    expect(meter.view.peakValue).toBe(10);
    host.flushFrame(101);
    expect(meter.view.peakValue).toBe(9);
    host.flushFrame(400);
    expect(meter.view.peakValue).toBe(8);
  });

  it('uses the current caller decrement and never falls below the smoother', () => {
    let decrement = 1;
    const { host, meter, smoother } = frameSetup({ decrement: () => decrement });
    meter.sync({ sample: 12, smoothTarget: 12, peakEnabled: true });
    meter.start();
    smoother.set(3);
    host.flushFrame(1001);
    expect(meter.view.peakValue).toBe(11);
    decrement = 20;
    host.flushFrame();
    expect(meter.view.peakValue).toBe(3);
  });

  it('keeps a static reduced-motion peak and re-seats it on the next expired sync', () => {
    const { host, meter } = frameSetup({ reduced: true });
    meter.sync({ sample: 10, smoothTarget: 10, peakEnabled: true });
    meter.start();
    expect(host.activeFrames).toBe(0);
    host.advance(1001);
    expect(meter.view.peakValue).toBe(10);
    meter.sync({ sample: 2, smoothTarget: 2, peakEnabled: true });
    expect(meter.view.peakValue).toBe(2);
  });

  it('captures the smoother snap when reduced motion turns on before the next frame', () => {
    const host = createHost(false);
    const smoother = createMotionAwareSmoother(host);
    const meter = createMeterBallistics(smoother, host, {
      peakSource: 'smoothed',
      ticker: { kind: 'animation-frame' },
      peak: createFrameStepPeakStrategy({
        holdMilliseconds: 1000,
        decrementPerFrame: () => 1,
      }),
    });
    meter.sync({ sample: 10, smoothTarget: 10, peakEnabled: true });
    meter.start();
    expect(meter.view).toEqual({ smoothedValue: 0, peakValue: 0 });

    host.setReduced(true);
    expect(meter.view).toEqual({ smoothedValue: 10, peakValue: 10 });
    meter.sync({ sample: 2, smoothTarget: 2, peakEnabled: true });
    expect(meter.view).toEqual({ smoothedValue: 2, peakValue: 10 });
    expect(host.activeFrames).toBe(0);
    meter.stop();
    expect(host.listenerCount).toBe(0);
  });
});

describe('createMeterBallistics elapsed-envelope strategy', () => {
  it('delegates observe and projection to the injected envelope functions', () => {
    const { meter, update, display } = elapsedSetup();
    meter.sync({ sample: 0.8, smoothTarget: 6, peakEnabled: true });
    expect(update).toHaveBeenCalledExactlyOnceWith(undefined, 0.8, 0, 1500);
    expect(meter.view).toEqual({ smoothedValue: 6, peakValue: 0.8 });
    expect(display).toHaveBeenCalledExactlyOnceWith({ latchedPeak: 0.8, latchedAt: 0 }, 0.8, 0, 1500);
  });

  it('advances wall time without observing or resampling input', () => {
    const { host, meter, update, display } = elapsedSetup();
    meter.sync({ sample: 1, smoothTarget: 10, peakEnabled: true });
    meter.sync({ sample: 0.1, smoothTarget: 1, peakEnabled: true });
    meter.start();
    expect(host.activeIntervals).toBe(1);
    update.mockClear();
    display.mockClear();
    host.advance(750);
    host.flushIntervals();
    expect(update).not.toHaveBeenCalled();
    expect(meter.view.peakValue).toBe(0.5);
    expect(display).toHaveBeenCalledTimes(1);
  });

  it('hides without clearing state and reset remains clear until a new sync', () => {
    const { meter } = elapsedSetup();
    meter.sync({ sample: 1, smoothTarget: 10, peakEnabled: true });
    meter.sync({ sample: 0.2, smoothTarget: 2, peakEnabled: false });
    expect(meter.view.peakValue).toBeNull();
    meter.sync({ sample: 0.2, smoothTarget: 2, peakEnabled: true });
    expect(meter.view.peakValue).toBe(1);
    meter.resetPeak();
    expect(meter.view.peakValue).toBeNull();
    expect(meter.view.peakValue).toBeNull();
    meter.sync({ sample: 0.2, smoothTarget: 2, peakEnabled: true });
    expect(meter.view.peakValue).toBe(0.2);
  });

  it('uses static reduced-motion projection and re-seats only on genuine sync', () => {
    const { host, meter } = elapsedSetup(true);
    meter.sync({ sample: 1, smoothTarget: 10, peakEnabled: true });
    meter.start();
    expect(host.activeIntervals).toBe(0);
    host.advance(2000);
    expect(meter.view.peakValue).toBe(1);
    meter.sync({ sample: 0.2, smoothTarget: 2, peakEnabled: true });
    expect(meter.view.peakValue).toBe(0.2);
  });

  it('does not observe Bar sample state on preference flips', () => {
    const { host, meter, update } = elapsedSetup();
    meter.sync({ sample: 1, smoothTarget: 10, peakEnabled: true });
    meter.start();
    update.mockClear();
    host.setReduced(true);
    host.setReduced(false);
    expect(update).not.toHaveBeenCalled();
    expect(host.activeIntervals).toBe(1);
  });
});

describe('createMeterBallistics scheduling lifecycle', () => {
  it.each([
    ['frame', frameSetup, 'activeFrames'],
    ['interval', elapsedSetup, 'activeIntervals'],
  ] as const)('keeps one %s schedule across preference flips and tears down once', (_name, setup, count) => {
    const { host, meter, smoother } = setup();
    meter.sync({ sample: 1, smoothTarget: 1, peakEnabled: true });
    meter.start();
    expect(smoother.start).toHaveBeenCalledTimes(1);
    expect(host[count]).toBe(1);
    expect(host.listenerCount).toBe(1);
    host.setReduced(true);
    expect(host[count]).toBe(0);
    host.setReduced(false);
    expect(host[count]).toBe(1);
    host.setReduced(false);
    expect(host[count]).toBe(1);
    meter.stop();
    expect(host[count]).toBe(0);
    expect(host.listenerCount).toBe(0);
    expect(smoother.stop).toHaveBeenCalledTimes(1);
  });

  it('stops an interval while peak is hidden and resumes it on re-enable', () => {
    const { host, meter } = elapsedSetup();
    meter.sync({ sample: 1, smoothTarget: 10, peakEnabled: true });
    meter.start();
    meter.sync({ sample: 0.5, smoothTarget: 5, peakEnabled: false });
    expect(host.activeIntervals).toBe(0);
    meter.sync({ sample: 0.5, smoothTarget: 5, peakEnabled: true });
    expect(host.activeIntervals).toBe(1);
  });
});
