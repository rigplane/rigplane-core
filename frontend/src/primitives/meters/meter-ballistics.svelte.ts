export type MeterBehaviorInput = Readonly<{
  sample: number | null;
  smoothTarget: number | null;
  peakEnabled: boolean;
}>;

export interface MeterBallisticsView {
  readonly smoothedValue: number;
  readonly peakValue: number | null;
}

export interface MeterSmoother {
  readonly value: number;
  update(value: number): void;
  reset(value: number): void;
  start(): void;
  stop(): void;
}

export type MeterTicker =
  | Readonly<{ kind: 'animation-frame' }>
  | Readonly<{ kind: 'interval'; milliseconds: number }>;

type MeterIntervalId = ReturnType<typeof setInterval>;

export interface MeterMotionHost {
  now(): number;
  requestFrame(callback: FrameRequestCallback): number;
  cancelFrame(id: number): void;
  setInterval(callback: () => void, milliseconds: number): MeterIntervalId;
  clearInterval(id: MeterIntervalId): void;
  prefersReducedMotion(): boolean;
  onReducedMotionChange(callback: (reduced: boolean) => void): () => void;
}

export interface MeterPeakStrategy<State> {
  seed(current: number, now: number): State;
  observe(state: State, current: number, now: number, reduced: boolean): State;
  advance(state: State, current: number, now: number): State;
  project(state: State, current: number, now: number, reduced: boolean): number;
}

export interface MeterBallisticsPolicy<State> {
  readonly peakSource: 'sample' | 'smoothed';
  readonly ticker: MeterTicker;
  readonly peak: MeterPeakStrategy<State>;
}

export interface MeterBallistics {
  readonly view: Readonly<MeterBallisticsView>;
  sync(input: MeterBehaviorInput): void;
  resetPeak(): void;
  start(): void;
  stop(): void;
}

type FrameStepPeak = Readonly<{ value: number; latchedAt: number }>;

export function createFrameStepPeakStrategy(options: Readonly<{
  holdMilliseconds: number;
  decrementPerFrame: () => number;
}>): MeterPeakStrategy<FrameStepPeak> {
  const latch = (current: number, now: number): FrameStepPeak => ({ value: current, latchedAt: now });
  return {
    seed: latch,
    observe(state, current, now, reduced) {
      if (current >= state.value || (reduced && now - state.latchedAt > options.holdMilliseconds)) {
        return latch(current, now);
      }
      return state;
    },
    advance(state, current, now) {
      if (current >= state.value) return latch(current, now);
      if (now - state.latchedAt <= options.holdMilliseconds) return state;
      return { ...state, value: Math.max(current, state.value - options.decrementPerFrame()) };
    },
    project: (state) => state.value,
  };
}

export function createElapsedEnvelopePeakStrategy<State extends { readonly latchedPeak: number }>(
  options: Readonly<{
    decayMilliseconds: number;
    updatePeakHold(state: State | undefined, current: number, now: number, decayMilliseconds: number): State;
    peakHoldDisplay(state: State, current: number, now: number, decayMilliseconds: number): number;
  }>,
): MeterPeakStrategy<State> {
  return {
    seed: (current, now) => options.updatePeakHold(undefined, current, now, options.decayMilliseconds),
    observe: (state, current, now) => (
      options.updatePeakHold(state, current, now, options.decayMilliseconds)
    ),
    advance: (state) => state,
    project: (state, current, now, reduced) => (
      reduced
        ? state.latchedPeak
        : options.peakHoldDisplay(state, current, now, options.decayMilliseconds)
    ),
  };
}

export function createMeterBallistics<State>(
  smoother: MeterSmoother,
  host: MeterMotionHost,
  policy: Readonly<MeterBallisticsPolicy<State>>,
): MeterBallistics {
  let peakState = $state<State | undefined>(undefined);
  let sample = $state(0);
  let peakEnabled = $state(false);
  let projectionNow = $state(host.now());
  let reduced = $state(host.prefersReducedMotion());
  let started = false;
  let scheduleId: number | MeterIntervalId | undefined;
  let unsubscribe: (() => void) | undefined;

  function peakCurrent(): number {
    return policy.peakSource === 'sample' ? sample : smoother.value;
  }

  function clearSchedule(): void {
    if (scheduleId === undefined) return;
    if (policy.ticker.kind === 'animation-frame') host.cancelFrame(scheduleId as number);
    else host.clearInterval(scheduleId as MeterIntervalId);
    scheduleId = undefined;
  }

  function advance(now: number): void {
    projectionNow = now;
    if (peakState !== undefined) {
      peakState = policy.peak.advance(peakState, peakCurrent(), now);
    }
  }

  function ensureSchedule(): void {
    if (!started || reduced || !peakEnabled || scheduleId !== undefined) return;
    if (policy.ticker.kind === 'animation-frame') {
      scheduleId = host.requestFrame((now) => {
        scheduleId = undefined;
        if (!started || reduced || !peakEnabled) return;
        advance(now);
        ensureSchedule();
      });
      return;
    }
    scheduleId = host.setInterval(() => advance(host.now()), policy.ticker.milliseconds);
  }

  function reconcileSchedule(): void {
    if (!started || reduced || !peakEnabled) clearSchedule();
    else ensureSchedule();
  }

  return {
    get view() {
      const peakValue = peakEnabled && peakState !== undefined
        ? policy.peak.project(peakState, peakCurrent(), projectionNow, reduced)
        : null;
      return { smoothedValue: smoother.value, peakValue };
    },
    sync(input) {
      peakEnabled = input.peakEnabled;
      projectionNow = host.now();
      if (input.sample === null || input.smoothTarget === null) {
        sample = 0;
        smoother.reset(0);
        peakState = undefined;
        reconcileSchedule();
        return;
      }
      sample = input.sample;
      smoother.update(input.smoothTarget);
      if (peakEnabled) {
        const current = peakCurrent();
        peakState = peakState === undefined
          ? policy.peak.seed(current, projectionNow)
          : policy.peak.observe(peakState, current, projectionNow, reduced);
      }
      reconcileSchedule();
    },
    resetPeak() {
      peakState = undefined;
      projectionNow = host.now();
    },
    start() {
      if (started) return;
      started = true;
      smoother.start();
      reduced = host.prefersReducedMotion();
      unsubscribe = host.onReducedMotionChange((nextReduced) => {
        reduced = nextReduced;
        if (nextReduced && peakEnabled && peakState !== undefined && policy.peakSource === 'smoothed') {
          projectionNow = host.now();
          peakState = policy.peak.observe(peakState, smoother.value, projectionNow, true);
        }
        reconcileSchedule();
      });
      ensureSchedule();
    },
    stop() {
      if (!started) return;
      started = false;
      clearSchedule();
      unsubscribe?.();
      unsubscribe = undefined;
      smoother.stop();
    },
  };
}
