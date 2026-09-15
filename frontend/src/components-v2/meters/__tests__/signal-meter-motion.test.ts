import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  MeterContinuitySession,
  MeterSourceIdentity,
} from '../../../primitives/meters/meter-ballistics.svelte';
import type { SignalMeterProjection } from '../smeter-scale';
import { createSignalMeterMotion } from '../signal-meter-motion.svelte';

const MAIN_SOURCE = {
  providerGeneration: 1,
  scope: 'receiver',
  receiver: 'MAIN',
  path: 'main.sMeter',
} as const satisfies MeterSourceIdentity;
const SUB_SOURCE = {
  providerGeneration: 1,
  scope: 'receiver',
  receiver: 'SUB',
  path: 'sub.sMeter',
} as const satisfies MeterSourceIdentity;
const SESSION_1 = { controlSessionEpoch: 1 } as const satisfies MeterContinuitySession;
const SESSION_2 = { controlSessionEpoch: 2 } as const satisfies MeterContinuitySession;

function projection(
  motionFraction: number | null,
  primaryText = motionFraction === null ? 'S ?' : 'S5',
  scaleMode: SignalMeterProjection['scaleMode'] = motionFraction === null ? 'none' : 's',
): SignalMeterProjection {
  return {
    scaleMode,
    motionFraction,
    primaryText,
    secondaryText: motionFraction === null ? '' : '\u2212121 dBm',
    accessibleDescription: primaryText,
    crossoverFraction: scaleMode === 's' ? 0.55 : null,
    marks: [],
    ticks: [],
  };
}

interface MotionHarness {
  readonly activeFrames: number;
  readonly listenerCount: number;
  setReduced(reduced: boolean): void;
  restore(): void;
}

function installMotionHarness(initialReduced = false): MotionHarness {
  let reduced = initialReduced;
  let nextFrameId = 1;
  const frames = new Map<number, FrameRequestCallback>();
  const listeners = new Set<() => void>();
  const mql = {
    get matches() { return reduced; },
    addEventListener: (_type: string, callback: () => void) => { listeners.add(callback); },
    removeEventListener: (_type: string, callback: () => void) => { listeners.delete(callback); },
  } as unknown as MediaQueryList;
  const originalMatchMedia = window.matchMedia;
  window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
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
    get listenerCount() { return listeners.size; },
    setReduced(next) {
      reduced = next;
      listeners.forEach((callback) => callback());
    },
    restore() {
      window.matchMedia = originalMatchMedia;
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

describe('createSignalMeterMotion', () => {
  it('owns exactly the existing smoother and peak schedules with idempotent lifecycle', () => {
    const binding = createSignalMeterMotion({ projection: projection(0.75), present: true });
    expect(harness!.activeFrames).toBe(0);

    binding.start();
    binding.start();
    expect(harness!.activeFrames).toBe(2);
    expect(harness!.listenerCount).toBe(2);

    binding.stop();
    binding.stop();
    expect(harness!.activeFrames).toBe(0);
    expect(harness!.listenerCount).toBe(0);
  });

  it('keeps projection atomic while sample-only updates retain motion history', () => {
    const high = projection(0.8, 'S9+20');
    const low = projection(0.2, 'S2');
    const binding = createSignalMeterMotion({
      projection: high, present: true, source: MAIN_SOURCE, session: SESSION_1,
    });
    expect(binding.frame.projection).toBe(high);
    expect(binding.frame.smoothedFraction).toBe(0.8);
    expect(binding.frame.peakFraction).toBe(0.8);

    binding.sync({ projection: low, present: true, source: MAIN_SOURCE, session: SESSION_1 });
    expect(binding.frame.projection).toBe(low);
    expect(binding.frame.smoothedFraction).toBe(0.8);
    expect(binding.frame.peakFraction).toBe(0.8);
  });

  it('observes synchronous source and session A-B-A boundaries without a render flush', () => {
    const binding = createSignalMeterMotion({
      projection: projection(0.8), present: true, source: MAIN_SOURCE, session: SESSION_1,
    });
    binding.sync({
      projection: projection(0.2), present: true, source: SUB_SOURCE, session: SESSION_1,
    });
    expect(binding.frame.smoothedFraction).toBe(0.2);
    binding.sync({
      projection: projection(0.6), present: true, source: MAIN_SOURCE, session: SESSION_2,
    });
    expect(binding.frame.smoothedFraction).toBe(0.6);
    expect(binding.frame.peakFraction).toBe(0.6);
  });

  it('preserves independent undefined, null, and object continuity semantics', () => {
    const binding = createSignalMeterMotion({
      projection: projection(0.7), present: true, source: MAIN_SOURCE, session: SESSION_1,
    });
    binding.sync({ projection: projection(0.3), present: true, source: SUB_SOURCE });
    expect(binding.frame.smoothedFraction).toBe(0.3);
    binding.sync({ projection: projection(0.2), present: true, session: SESSION_2 });
    expect(binding.frame.smoothedFraction).toBe(0.3);

    binding.sync({
      projection: projection(0.4), present: true, source: MAIN_SOURCE, session: SESSION_1,
    });
    expect(binding.frame.smoothedFraction).toBe(0.4);
    binding.sync({
      projection: projection(0.9), present: true, source: null, session: SESSION_1,
    });
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();
    binding.sync({
      projection: projection(0.6), present: true, source: MAIN_SOURCE, session: SESSION_1,
    });
    binding.sync({
      projection: projection(0.9), present: true, source: MAIN_SOURCE, session: null,
    });
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();
  });

  it('keeps known raw projection text but resets motion for unknown or absent samples', () => {
    const raw = projection(0.4, 'raw 103', 'raw');
    const binding = createSignalMeterMotion({ projection: raw, present: true });
    expect(binding.frame.projection.primaryText).toBe('raw 103');

    const unknown = projection(null, '103, unit unknown', 'none');
    binding.sync({ projection: unknown, present: true });
    expect(binding.frame.projection).toBe(unknown);
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();

    binding.sync({ projection: raw, present: false });
    expect(binding.frame.projection).toBe(raw);
    expect(binding.frame.smoothedFraction).toBe(0);
    expect(binding.frame.peakFraction).toBeNull();
  });

  it('stops both channels when reduced motion turns on and restores them when it turns off', () => {
    const binding = createSignalMeterMotion({ projection: projection(0.5), present: true });
    binding.start();
    expect(harness!.activeFrames).toBe(2);

    harness!.setReduced(true);
    expect(harness!.activeFrames).toBe(0);
    expect(binding.frame.smoothedFraction).toBe(0.5);
    harness!.setReduced(false);
    expect(harness!.activeFrames).toBe(2);
  });
});
