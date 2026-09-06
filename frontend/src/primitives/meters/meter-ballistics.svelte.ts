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

export interface MeterPeakGroupChannelView {
  readonly liveValue: number | null;
  readonly displayedValue: number | null;
  readonly peakValue: number | null;
}

export interface MeterBallisticsGroup<Key extends PropertyKey> {
  readonly reducedMotion: boolean;
  view(key: Key): Readonly<MeterPeakGroupChannelView>;
  sync(samples: Readonly<Record<Key, number | null>>): void;
  resetPeak(key: Key): void;
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

interface PeakChannel<State> {
  readonly hasPeak: boolean;
  observe(current: number, now: number, reduced: boolean): void;
  advance(current: number, now: number): void;
  project(current: number, now: number, reduced: boolean): number | null;
  reset(): void;
}

function createPeakChannel<State>(strategy: MeterPeakStrategy<State>): PeakChannel<State> {
  let state = $state<State | undefined>(undefined);
  return {
    get hasPeak() { return state !== undefined; },
    observe(current, now, reduced) {
      state = state === undefined
        ? strategy.seed(current, now)
        : strategy.observe(state, current, now, reduced);
    },
    advance(current, now) {
      if (state !== undefined) state = strategy.advance(state, current, now);
    },
    project(current, now, reduced) {
      return state === undefined ? null : strategy.project(state, current, now, reduced);
    },
    reset() { state = undefined; },
  };
}

interface TickerLifecycle {
  readonly reducedMotion: boolean;
  reconcile(): void;
  start(): void;
  stop(): void;
}

interface PeakGroupSlot<State> {
  liveValue: number | null;
  readonly channel: PeakChannel<State>;
}

function createPeakGroupSlot<State>(strategy: MeterPeakStrategy<State>): PeakGroupSlot<State> {
  let liveValue = $state<number | null>(null);
  const channel = createPeakChannel(strategy);
  return {
    get liveValue() { return liveValue; },
    set liveValue(value) { liveValue = value; },
    channel,
  };
}

function createTickerLifecycle(
  host: MeterMotionHost,
  ticker: MeterTicker,
  enabled: () => boolean,
  hooks: Readonly<{
    onTick(now: number): void;
    onStart?(reduced: boolean): void;
    onReducedMotionChange?(reduced: boolean): void;
  }>,
): TickerLifecycle {
  let reduced = $state(host.prefersReducedMotion());
  let started = false;
  let scheduleId: number | MeterIntervalId | undefined;
  let unsubscribe: (() => void) | undefined;

  function clearSchedule(): void {
    if (scheduleId === undefined) return;
    if (ticker.kind === 'animation-frame') host.cancelFrame(scheduleId as number);
    else host.clearInterval(scheduleId as MeterIntervalId);
    scheduleId = undefined;
  }

  function ensureSchedule(): void {
    if (!started || reduced || !enabled() || scheduleId !== undefined) return;
    if (ticker.kind === 'animation-frame') {
      scheduleId = host.requestFrame((now) => {
        scheduleId = undefined;
        if (!started || reduced || !enabled()) return;
        hooks.onTick(now);
        ensureSchedule();
      });
      return;
    }
    scheduleId = host.setInterval(() => hooks.onTick(host.now()), ticker.milliseconds);
  }

  function reconcile(): void {
    if (!started || reduced || !enabled()) clearSchedule();
    else ensureSchedule();
  }

  return {
    get reducedMotion() { return reduced; },
    reconcile,
    start() {
      if (started) return;
      started = true;
      reduced = host.prefersReducedMotion();
      hooks.onStart?.(reduced);
      unsubscribe = host.onReducedMotionChange((nextReduced) => {
        const changed = nextReduced !== reduced;
        reduced = nextReduced;
        if (changed) hooks.onReducedMotionChange?.(nextReduced);
        reconcile();
      });
      reconcile();
    },
    stop() {
      if (!started) return;
      started = false;
      clearSchedule();
      unsubscribe?.();
      unsubscribe = undefined;
    },
  };
}

export function createMeterBallistics<State>(
  smoother: MeterSmoother,
  host: MeterMotionHost,
  policy: Readonly<MeterBallisticsPolicy<State>>,
): MeterBallistics {
  const channel = createPeakChannel(policy.peak);
  let sample = $state(0);
  let peakEnabled = $state(false);
  let projectionNow = $state(host.now());
  let started = false;

  function peakCurrent(): number {
    return policy.peakSource === 'sample' ? sample : smoother.value;
  }

  const lifecycle = createTickerLifecycle(host, policy.ticker, () => peakEnabled, {
    onTick(now) {
      projectionNow = now;
      channel.advance(peakCurrent(), now);
    },
    onReducedMotionChange(nextReduced) {
      if (nextReduced && peakEnabled && channel.hasPeak && policy.peakSource === 'smoothed') {
        projectionNow = host.now();
        channel.observe(smoother.value, projectionNow, true);
      }
    },
  });

  return {
    get view() {
      const peakValue = peakEnabled
        ? channel.project(peakCurrent(), projectionNow, lifecycle.reducedMotion)
        : null;
      return { smoothedValue: smoother.value, peakValue };
    },
    sync(input) {
      peakEnabled = input.peakEnabled;
      projectionNow = host.now();
      if (input.sample === null || input.smoothTarget === null) {
        sample = 0;
        smoother.reset(0);
        channel.reset();
        lifecycle.reconcile();
        return;
      }
      sample = input.sample;
      smoother.update(input.smoothTarget);
      if (peakEnabled) {
        channel.observe(peakCurrent(), projectionNow, lifecycle.reducedMotion);
      }
      lifecycle.reconcile();
    },
    resetPeak() {
      channel.reset();
      projectionNow = host.now();
    },
    start() {
      if (started) return;
      started = true;
      smoother.start();
      lifecycle.start();
    },
    stop() {
      if (!started) return;
      started = false;
      lifecycle.stop();
      smoother.stop();
    },
  };
}

export function createMeterBallisticsGroup<Key extends PropertyKey, State>(
  keys: readonly Key[],
  host: MeterMotionHost,
  policy: Readonly<{
    ticker: Extract<MeterTicker, { kind: 'interval' }>;
    peak: MeterPeakStrategy<State>;
  }>,
): MeterBallisticsGroup<Key> {
  const slots = new Map<Key, PeakGroupSlot<State>>();
  for (const key of keys) {
    slots.set(key, createPeakGroupSlot(policy.peak));
  }
  let projectionNow = $state(host.now());
  let sampleSource: Readonly<Record<Key, number | null>> | undefined;

  function slotFor(key: Key) {
    const slot = slots.get(key);
    if (slot === undefined) throw new Error(`Unknown meter peak channel: ${String(key)}`);
    return slot;
  }

  function sampleAll(now: number): void {
    projectionNow = now;
    for (const key of keys) {
      const slot = slotFor(key);
      if (sampleSource !== undefined) slot.liveValue = sampleSource[key];
      if (slot.liveValue === null) {
        slot.channel.reset();
        continue;
      }
      slot.channel.observe(slot.liveValue, now, false);
      slot.channel.advance(slot.liveValue, now);
    }
  }

  const lifecycle = createTickerLifecycle(host, policy.ticker, () => true, {
    onTick: sampleAll,
    onStart(reduced) {
      if (!reduced) sampleAll(host.now());
    },
    onReducedMotionChange(reduced) {
      if (!reduced) sampleAll(host.now());
    },
  });

  return {
    get reducedMotion() { return lifecycle.reducedMotion; },
    view(key) {
      const slot = slotFor(key);
      const liveValue = slot.liveValue;
      if (liveValue === null) return { liveValue: null, displayedValue: null, peakValue: null };
      const peakValue = slot.channel.project(
        liveValue,
        projectionNow,
        lifecycle.reducedMotion,
      );
      return {
        liveValue,
        displayedValue: lifecycle.reducedMotion ? liveValue : (peakValue ?? liveValue),
        peakValue,
      };
    },
    sync(samples) {
      sampleSource = samples;
      const reduced = lifecycle.reducedMotion;
      const now = reduced ? host.now() : projectionNow;
      for (const key of keys) {
        const slot = slotFor(key);
        const liveValue = samples[key];
        slot.liveValue = liveValue;
        if (liveValue === null) slot.channel.reset();
        else if (reduced) slot.channel.observe(liveValue, now, true);
      }
      if (reduced) projectionNow = now;
    },
    resetPeak(key) { slotFor(key).channel.reset(); },
    start: lifecycle.start,
    stop: lifecycle.stop,
  };
}
