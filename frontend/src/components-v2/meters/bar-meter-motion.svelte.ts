import {
  createSmoother,
  onReducedMotionChange,
  prefersReducedMotion,
} from '$lib/utils/smoothing.svelte';
import {
  createElapsedEnvelopePeakStrategy,
  createMeterBallistics,
  type MeterContinuitySession,
  type MeterMotionHost,
  type MeterSourceIdentity,
} from '../../primitives/meters/meter-ballistics.svelte';
import {
  PEAK_DECAY_MS,
  peakHoldDisplay,
  updatePeakHold,
  type PeakHoldState,
} from '../panels/meter-utils';

export interface BarMeterFrame {
  readonly smoothedFraction: number;
  readonly peakFraction: number | null;
}

export type BarMeterMotionInput = Readonly<{
  value: number | null;
  peakEnabled: boolean;
  source?: MeterSourceIdentity | null;
  session?: MeterContinuitySession | null;
}>;

export interface BarMeterMotionBinding {
  readonly frame: BarMeterFrame;
  sync(input: BarMeterMotionInput): void;
  start(): void;
  stop(): void;
  resetPeak(): void;
}

const ATTACK_SECONDS = 0.08;
const RELEASE_SECONDS = 0.2;
const PEAK_TICK_MILLISECONDS = 100;

const browserMotionHost: MeterMotionHost = {
  now: () => Date.now(),
  requestFrame: (callback) => requestAnimationFrame(callback),
  cancelFrame: (id) => cancelAnimationFrame(id),
  setInterval: (callback, milliseconds) => setInterval(callback, milliseconds),
  clearInterval: (id) => clearInterval(id),
  prefersReducedMotion,
  onReducedMotionChange,
};

function clampFraction(value: number): number {
  return Math.max(0, Math.min(1, value));
}

export function createBarMeterMotion(initial: BarMeterMotionInput): BarMeterMotionBinding {
  const smoother = createSmoother(ATTACK_SECONDS, RELEASE_SECONDS);
  const ballistics = createMeterBallistics(smoother, browserMotionHost, {
    peakSource: 'sample',
    ticker: { kind: 'interval', milliseconds: PEAK_TICK_MILLISECONDS },
    peak: createElapsedEnvelopePeakStrategy<PeakHoldState>({
      decayMilliseconds: PEAK_DECAY_MS,
      updatePeakHold,
      peakHoldDisplay,
    }),
  });
  const frame: BarMeterFrame = {
    get smoothedFraction() { return ballistics.view.smoothedValue; },
    get peakFraction() { return ballistics.view.peakValue; },
  };
  const binding: BarMeterMotionBinding = {
    frame,
    sync(input) {
      ballistics.sync({
        sample: input.value,
        smoothTarget: input.value === null ? null : clampFraction(input.value),
        peakEnabled: input.peakEnabled,
        source: input.source,
        session: input.session,
      });
    },
    start: () => ballistics.start(),
    stop: () => ballistics.stop(),
    resetPeak: () => ballistics.resetPeak(),
  };
  binding.sync(initial);
  return binding;
}
