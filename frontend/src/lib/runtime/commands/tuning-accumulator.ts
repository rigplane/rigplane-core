/** Rapid relative-tuning accumulator (MOR-1425, MOR-1864). */

import { SvelteMap } from 'svelte/reactivity';

const DEFAULT_PACE_MS = 60;
const DEFAULT_QUIET_WINDOW_MS = 4_000;
// MOR-2464: display-only idle for the burst publication — the window the
// panorama keeps chasing the local gesture target after the LAST accepted
// input, before reconciling to pending/confirmed truth. 200ms sits ~3x the
// 60ms paced-emit interval (every paced-unsent gap stays covered), well
// above key-repeat/wheel inter-event gaps (~30-50ms), and well under the
// ~500ms field poll cadence, so reconciliation lands within about one poll
// of input ending. It is deliberately NOT the accumulation quiet window:
// `quietWindowMs` is command-authority state, this is presentation state.
const DEFAULT_VISUAL_HOLD_MS = 200;
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
  /** Display-only idle for the burst publication (MOR-2464). */
  visualHoldMs?: number;
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

// ── Display-only burst publication (MOR-2464) ──
// The reactive read side for presentation consumers (the spectrum
// panorama). Entries are written only from accepted local gesture input
// (`step`/`jump`) and self-retire on a short idle timer, so a stale visual
// target can never outlive its gesture by more than the hold.
interface BurstPublication {
  target: number;
  lastInputAt: number;
  holdMs: number;
  /** The accumulator's own clock — staleness must read the same `now` that stamped the input. */
  now: () => number;
  lifecycle: EmittedLifecycle | null;
  stillRelevant: () => boolean;
  releaseTimer: ReturnType<typeof setTimeout> | null;
}

const burstPublications = new SvelteMap<number, BurstPublication>();

/**
 * The per-gesture tuning target for `receiver` while local input is
 * visually live, or `null` otherwise. Includes paced-unsent steps — the
 * accumulated target, not only the last emitted command. Display-only:
 * it never gates, paces, or reorders command emission.
 */
export function getTuningBurstTargetHz(receiver: number): number | null {
  const entry = burstPublications.get(receiver);
  if (entry === undefined) return null;
  if (TERMINAL_STATUSES.has(entry.lifecycle?.status ?? '')) return null;
  if (!entry.stillRelevant()) return null;
  return entry.now() - entry.lastInputAt <= entry.holdMs ? entry.target : null;
}

export function createTuningAccumulator(options: TuningAccumulatorOptions): TuningAccumulator {
  const {
    emit,
    now = () => Date.now(),
    paceMs = DEFAULT_PACE_MS,
    quietWindowMs = DEFAULT_QUIET_WINDOW_MS,
    visualHoldMs = DEFAULT_VISUAL_HOLD_MS,
    epoch: getEpoch = () => 0,
    generation: getGeneration = () => null,
    context: getContext = (receiver) => `receiver:${receiver}`,
    acceptedTarget = () => false,
  } = options;
  const pending = new Map<number, PendingTuning>();

  function publishBurst(
    receiver: number,
    target: number,
    lifecycle: EmittedLifecycle | null,
    context: string | null,
  ): void {
    const previous = burstPublications.get(receiver);
    if (previous?.releaseTimer) clearTimeout(previous.releaseTimer);
    const epoch = getEpoch();
    const generation = getGeneration();
    const entry: BurstPublication = {
      target,
      lastInputAt: now(),
      holdMs: visualHoldMs,
      now,
      lifecycle,
      stillRelevant: () => epoch === getEpoch() && generation === getGeneration()
        && context === getContext(receiver),
      releaseTimer: null,
    };
    entry.releaseTimer = setTimeout(() => {
      entry.releaseTimer = null;
      burstPublications.delete(receiver);
    }, visualHoldMs);
    burstPublications.set(receiver, entry);
  }

  function clear(receiver: number): void {
    const state = pending.get(receiver);
    if (state?.flushTimer) clearTimeout(state.flushTimer);
    pending.delete(receiver);
    const burst = burstPublications.get(receiver);
    if (burst?.releaseTimer) clearTimeout(burst.releaseTimer);
    burstPublications.delete(receiver);
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
    // The publication must judge liveness by the FRESHEST emitted
    // lifecycle, not stay tied to the one captured at the last input: a
    // terminal failure of the just-flushed target invalidates the
    // displayed burst immediately. The SvelteMap is shallow — a plain
    // field mutation would never notify `$derived` consumers — so the
    // record is REPLACED, keeping every other field (hold window, release
    // timer) untouched. Display-only bookkeeping: the emit above is the
    // unchanged paced scheduling.
    const burst = burstPublications.get(receiver);
    if (burst && burst.target === state.target) {
      burstPublications.set(receiver, { ...burst, lifecycle: state.lastLifecycle });
    }
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
    publishBurst(receiver, requestedFreq, lifecycle, context);
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
      publishBurst(receiver, state.target, state.lastLifecycle, state.context);
    },
    jump(receiver, freq) {
      clear(receiver);
      const lifecycle = emit(receiver, freq) ?? null;
      publishBurst(receiver, freq, lifecycle, getContext(receiver));
    },
    cancel(receiver) {
      if (receiver !== undefined) { clear(receiver); return; }
      // Publications can exist for receivers with no pending entry (jump,
      // or a cold step without a valid marker) — cancel-all retires those
      // visual targets too.
      for (const key of new Set([...pending.keys(), ...burstPublications.keys()])) clear(key);
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
  for (const [receiver, entry] of [...burstPublications]) {
    if (entry.releaseTimer) clearTimeout(entry.releaseTimer);
    burstPublications.delete(receiver);
  }
}
