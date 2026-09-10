/** Rapid relative-tuning accumulator (MOR-1425, MOR-1864). */

const DEFAULT_PACE_MS = 60;
const DEFAULT_QUIET_WINDOW_MS = 4_000;
const TERMINAL_STATUSES = new Set(['failed', 'cancelled', 'timed-out']);

export interface EmittedLifecycle {
  status: string;
  id?: string;
  createdAt?: number;
}

export interface BurstAnchor {
  readonly id: string | null;
  readonly createdAt: number;
}

export interface AcceptedTargetQuery {
  readonly receiver: number;
  readonly context: string;
  readonly frequency: number;
  readonly observationMarker: number;
  readonly anchor: BurstAnchor;
}

interface PendingTuning {
  target: number;
  quietUntil: number;
  flushTimer: ReturnType<typeof setTimeout> | null;
  lastEmitAt: number;
  lastLifecycle: EmittedLifecycle | null;
  epoch: number;
  generation: number | null;
  context: string;
  lastObservationMarker: number;
  lastObservedFrequency: number;
  anchor: BurstAnchor;
}

export interface TuningAccumulatorOptions {
  emit: (receiver: number, freq: number) => EmittedLifecycle | void;
  now?: () => number;
  paceMs?: number;
  quietWindowMs?: number;
  epoch?: () => number;
  generation?: () => number | null;
  /** Selected-VFO identity, read at every step and again at paced flush. */
  context?: (receiver: number) => string | null;
  /** Exact accepted-target association for a changed frequency observation. */
  acceptedTarget?: (query: AcceptedTargetQuery) => boolean;
}

export interface TuningAccumulator {
  step(receiver: number, confirmedFreq: number, requestedFreq: number, observationMarker?: number | null): void;
  jump(receiver: number, freq: number): void;
  /** Retire queued relative intent before an explicit VFO identity command. */
  cancel(receiver?: number): void;
}

function validMarker(value: number | null): value is number {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0;
}

export function createTuningAccumulator(options: TuningAccumulatorOptions): TuningAccumulator {
  const {
    emit,
    now = () => Date.now(),
    paceMs = DEFAULT_PACE_MS,
    quietWindowMs = DEFAULT_QUIET_WINDOW_MS,
    epoch: getEpoch = () => 0,
    generation: getGeneration = () => null,
    context: getContext = (receiver) => `receiver:${receiver}`,
    acceptedTarget = () => false,
  } = options;
  const pending = new Map<number, PendingTuning>();

  function clear(receiver: number): void {
    const state = pending.get(receiver);
    if (state?.flushTimer) clearTimeout(state.flushTimer);
    pending.delete(receiver);
  }

  function contextChanged(receiver: number, state: PendingTuning): boolean {
    return state.epoch !== getEpoch()
      || state.generation !== getGeneration()
      || state.context !== getContext(receiver);
  }

  function stillLive(receiver: number, state: PendingTuning, t: number): boolean {
    return t < state.quietUntil
      && !contextChanged(receiver, state)
      && !TERMINAL_STATUSES.has(state.lastLifecycle?.status ?? '');
  }

  function flush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state) return;
    const t = now();
    if (!stillLive(receiver, state, t)) { clear(receiver); return; }
    state.flushTimer = null;
    state.lastEmitAt = t;
    state.lastLifecycle = emit(receiver, state.target) ?? null;
  }

  function scheduleFlush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state || state.flushTimer !== null) return;
    const wait = Math.max(0, paceMs - (now() - state.lastEmitAt));
    state.flushTimer = setTimeout(() => flush(receiver), wait);
  }

  function beginFresh(
    receiver: number,
    t: number,
    confirmedFreq: number,
    requestedFreq: number,
    observationMarker: number | null,
  ): void {
    clear(receiver);
    const context = getContext(receiver);
    const lifecycle = emit(receiver, requestedFreq) ?? null;
    if (context === null || !validMarker(observationMarker)) return;
    pending.set(receiver, {
      target: requestedFreq,
      quietUntil: t + quietWindowMs,
      flushTimer: null,
      lastEmitAt: t,
      lastLifecycle: lifecycle,
      epoch: getEpoch(),
      generation: getGeneration(),
      context,
      lastObservationMarker: observationMarker,
      lastObservedFrequency: confirmedFreq,
      anchor: {
        id: typeof lifecycle?.id === 'string' ? lifecycle.id : null,
        createdAt: typeof lifecycle?.createdAt === 'number' ? lifecycle.createdAt : t,
      },
    });
  }

  function observationContinues(
    receiver: number,
    state: PendingTuning,
    frequency: number,
    marker: number | null,
  ): boolean {
    if (!validMarker(marker) || marker < state.lastObservationMarker) return false;
    if (marker === state.lastObservationMarker) return frequency === state.lastObservedFrequency;
    if (frequency !== state.lastObservedFrequency && !acceptedTarget({
      receiver,
      context: state.context,
      frequency,
      observationMarker: marker,
      anchor: state.anchor,
    })) return false;
    state.lastObservationMarker = marker;
    state.lastObservedFrequency = frequency;
    return true;
  }

  return {
    step(receiver, confirmedFreq, requestedFreq, observationMarker = null) {
      const t = now();
      const state = pending.get(receiver);
      const hot = state !== undefined
        && stillLive(receiver, state, t)
        && observationContinues(receiver, state, confirmedFreq, observationMarker);

      if (!hot) {
        beginFresh(receiver, t, confirmedFreq, requestedFreq, observationMarker);
        return;
      }

      state.target += requestedFreq - confirmedFreq;
      state.quietUntil = t + quietWindowMs;
      scheduleFlush(receiver);
    },
    jump(receiver, freq) {
      clear(receiver);
      emit(receiver, freq);
    },
    cancel(receiver) {
      if (receiver !== undefined) { clear(receiver); return; }
      for (const key of [...pending.keys()]) clear(key);
    },
  };
}

let singleton: TuningAccumulator | null = null;

export function getSharedTuningAccumulator(options: TuningAccumulatorOptions): TuningAccumulator {
  return singleton ??= createTuningAccumulator(options);
}

export function resetSharedTuningAccumulatorForTests(): void {
  singleton?.cancel();
  singleton = null;
}
