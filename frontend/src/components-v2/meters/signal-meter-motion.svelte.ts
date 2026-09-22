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
  /**
   * MOR-2509: the trailing envelope the bar's afterglow is drawn from —
   * the recent maximum of the displayed level, decaying back onto it. It
   * is always at or above `smoothedFraction` and is computed from the same
   * smoothed value that draws the bar, never animated independently. Null
   * under reduced motion (no afterglow) or when no sample is present.
   */
  readonly afterglowFraction: number | null;
  /** MOR-2509: live reduced-motion state, so faces can suppress the peak
   *  marker and afterglow without their own matchMedia subscription. */
  readonly reducedMotion: boolean;
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

/** MOR-2509 v7: attack ≤ 50 ms — the rise reads as immediate. */
const ATTACK_SECONDS = 0.05;
/** MOR-2509 v7: decay time constant ≈ 300 ms — the fall lags the reading. */
const RELEASE_SECONDS = 0.3;
const PEAK_HOLD_MILLISECONDS = 1000;
const PEAK_DECREMENT_PER_FRAME = (0.0195 / 20) * 16.67;
/**
 * MOR-2509 v7: a segment the falling bar has left keeps glowing until this
 * long after it was last lit — the afterglow is the displayed level as it
 * stood one fade window ago, so the trail shortens onto the bar as the bar
 * settles and never outlives it.
 */
export const AFTERGLOW_FADE_MILLISECONDS = 250;
const AFTERGLOW_EPSILON = 0.001;

type AfterglowSample = Readonly<{ time: number; value: number }>;

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

  // MOR-2509 afterglow envelope: a trail of the displayed level's recent
  // samples. The frame getter reports max(displayed, trail) — the glow rises
  // with the bar for free — and a frame loop runs only while the trail is
  // still ahead of the bar, so the steady state schedules nothing beyond the
  // smoother and peak ticker.
  let afterglowValue = $state(0);
  let afterglowTrail: AfterglowSample[] = [];
  let afterglowFrameId = 0;
  let afterglowUnsubscribe: (() => void) | undefined;
  let started = false;

  function cancelAfterglow(): void {
    if (afterglowFrameId) {
      cancelAnimationFrame(afterglowFrameId);
      afterglowFrameId = 0;
    }
  }

  /** The displayed level as it stood one fade window ago (linear between
   *  retained samples; clamps to the oldest kept sample). */
  function afterglowDelayed(now: number): number {
    const target = now - AFTERGLOW_FADE_MILLISECONDS;
    if (afterglowTrail.length === 0 || target <= afterglowTrail[0].time) {
      return afterglowTrail[0]?.value ?? smoother.value;
    }
    for (let index = afterglowTrail.length - 1; index >= 0; index -= 1) {
      const sample = afterglowTrail[index];
      if (sample.time <= target) {
        const next = afterglowTrail[index + 1];
        if (next === undefined) return sample.value;
        const span = next.time - sample.time;
        const t = span <= 0 ? 0 : (target - sample.time) / span;
        return sample.value + t * (next.value - sample.value);
      }
    }
    return afterglowTrail[0].value;
  }

  function afterglowTick(now: number): void {
    afterglowFrameId = 0;
    const displayed = smoother.value;
    afterglowTrail.push({ time: now, value: displayed });
    while (afterglowTrail.length > 1
      && now - afterglowTrail[1].time > AFTERGLOW_FADE_MILLISECONDS) {
      afterglowTrail.shift();
    }
    const delayed = afterglowDelayed(now);
    afterglowValue = Math.max(displayed, delayed);
    if (afterglowValue > displayed + AFTERGLOW_EPSILON && !prefersReducedMotion()) {
      afterglowFrameId = requestAnimationFrame(afterglowTick);
    } else {
      afterglowValue = displayed;
      afterglowTrail = [];
    }
  }

  function ensureAfterglowLoop(sample: number): void {
    if (afterglowFrameId || !started || prefersReducedMotion()) return;
    // Seed the trail so the fade window looks back past the drop: the level
    // one window ago was the pre-drop displayed value.
    const now = performance.now();
    const preDrop = smoother.value;
    afterglowTrail = [
      { time: now - AFTERGLOW_FADE_MILLISECONDS, value: preDrop },
      { time: now, value: preDrop },
    ];
    afterglowValue = Math.max(preDrop, sample);
    afterglowFrameId = requestAnimationFrame(afterglowTick);
  }

  const frame: SignalMeterFrame = {
    get projection() { return projection; },
    get smoothedFraction() { return ballistics.view.smoothedValue; },
    get peakFraction() { return ballistics.view.peakValue; },
    get afterglowFraction() {
      if (prefersReducedMotion()) return null;
      return Math.max(ballistics.view.smoothedValue, afterglowValue);
    },
    get reducedMotion() { return ballistics.view.reducedMotion; },
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
      if (sample === null) {
        cancelAfterglow();
        afterglowValue = 0;
        afterglowTrail = [];
      } else if (sample < afterglowValue - AFTERGLOW_EPSILON
        || sample < smoother.value - AFTERGLOW_EPSILON) {
        ensureAfterglowLoop(sample);
      }
    },
    start: () => {
      ballistics.start();
      started = true;
      afterglowUnsubscribe = onReducedMotionChange((reduced) => {
        if (reduced) {
          cancelAfterglow();
          afterglowValue = smoother.value;
        }
      });
    },
    stop: () => {
      ballistics.stop();
      started = false;
      cancelAfterglow();
      afterglowUnsubscribe?.();
      afterglowUnsubscribe = undefined;
    },
  };
  binding.sync(initial);
  return binding;
}
