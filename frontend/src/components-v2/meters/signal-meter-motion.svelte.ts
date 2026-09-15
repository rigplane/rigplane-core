import {
  createSmoother,
  onReducedMotionChange,
  prefersReducedMotion,
} from '$lib/utils/smoothing.svelte';
import {
  createFrameStepPeakStrategy,
  createMeterBallistics,
  type MeterContinuitySession,
  type MeterMotionHost,
  type MeterSourceIdentity,
} from '../../primitives/meters/meter-ballistics.svelte';
import type { SignalMeterProjection } from './smeter-scale';

export interface SignalMeterFrame {
  readonly projection: SignalMeterProjection;
  readonly smoothedFraction: number;
  readonly peakFraction: number | null;
}

export type SignalMeterMotionInput = Readonly<{
  projection: SignalMeterProjection;
  present: boolean;
  source?: MeterSourceIdentity | null;
  session?: MeterContinuitySession | null;
}>;

export interface SignalMeterMotionBinding {
  readonly frame: SignalMeterFrame;
  sync(input: SignalMeterMotionInput): void;
  start(): void;
  stop(): void;
}

const ATTACK_SECONDS = 0.06;
const RELEASE_SECONDS = 0.1;
const PEAK_HOLD_MILLISECONDS = 1000;
const PEAK_DECREMENT_PER_FRAME = (0.0195 / 20) * 16.67;

const browserMotionHost: MeterMotionHost = {
  now: () => performance.now(),
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (id) => cancelAnimationFrame(id),
  setInterval: (callback, milliseconds) => setInterval(callback, milliseconds),
  clearInterval: (id) => clearInterval(id),
  prefersReducedMotion,
  onReducedMotionChange,
};

export function createSignalMeterMotion(
  initial: SignalMeterMotionInput,
): SignalMeterMotionBinding {
  let projection = $state.raw(initial.projection);
  const smoother = createSmoother(ATTACK_SECONDS, RELEASE_SECONDS);
  const ballistics = createMeterBallistics(smoother, browserMotionHost, {
    peakSource: 'smoothed',
    ticker: { kind: 'animation-frame' },
    peak: createFrameStepPeakStrategy({
      holdMilliseconds: PEAK_HOLD_MILLISECONDS,
      decrementPerFrame: () => PEAK_DECREMENT_PER_FRAME,
    }),
  });
  const frame: SignalMeterFrame = {
    get projection() { return projection; },
    get smoothedFraction() { return ballistics.view.smoothedValue; },
    get peakFraction() { return ballistics.view.peakValue; },
  };
  const binding: SignalMeterMotionBinding = {
    frame,
    sync(input) {
      projection = input.projection;
      const sample = input.present ? input.projection.motionFraction : null;
      ballistics.sync({
        sample,
        smoothTarget: sample,
        peakEnabled: true,
        source: input.source,
        session: input.session,
      });
    },
    start: () => ballistics.start(),
    stop: () => ballistics.stop(),
  };
  binding.sync(initial);
  return binding;
}
