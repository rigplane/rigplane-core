/**
 * MOR-1425 — rapid tuning-step accumulator.
 *
 * Digit-tuning gestures (wheel ticks, arrow presses) compute their target as
 * `displayed value + step`, but the displayed value is last-confirmed radio
 * truth and only advances after a full `set_freq` round trip (~0.5-2s on
 * live hardware). A burst inside one round trip all computes the SAME
 * target off the same stale display, so N steps net one write — the
 * collapse this module fixes by holding an ephemeral PENDING TARGET per
 * receiver: while a burst is "hot" (within `quietWindowMs` of the last
 * step), each gesture's delta from the FROZEN baseline accumulates onto the
 * target instead. Emission is paced just above the server's 50ms
 * same-command coalescing window (MOR-1427).
 *
 * `step()` is for RELATIVE gestures only (wheel/arrow, one fixed increment
 * per call). ABSOLUTE targets (click-to-tune, station/QSY recall) go
 * through `jump()` instead (review B1): a burst delta computed against an
 * absolute target is meaningless, so `jump()` clears any in-flight burst
 * for the receiver and emits the exact frequency immediately, unpaced.
 *
 * Invariants:
 *  - this module never patches a store — it only calls the injected `emit`.
 *  - a confirmed observation on the CLOSED SEGMENT between the frozen
 *    baseline and the current target (direction of travel) is one of our
 *    own writes landing mid-burst — including an INTERMEDIATE target
 *    already superseded by further accumulation, not just the final one
 *    (review B2) — and rebases the baseline without discarding the target.
 *    Off-segment is a genuine contradiction (the operator moved the
 *    physical dial): reset, and rebuild the baseline from the new truth.
 *  - the burst expires after `quietWindowMs` of inactivity, immediately on
 *    a terminal (failed/cancelled/timed-out) lifecycle — the lifecycle
 *    OBJECT is retained and `.status` read fresh each step/flush, since
 *    it's mutated in place after `emit` returns, not replaced (review B3) —
 *    or on a control-session epoch / capabilities-generation change (review
 *    B4), so a burst never carries across a reconnect or radio switch.
 *  - the first step of a cold window emits immediately, unpaced.
 *  - ONE accumulator per receiver, globally (review B5): `createTuningAccumulator`
 *    stays a plain per-call factory (used by tests); production code goes
 *    through `getSharedTuningAccumulator`, a module singleton so every
 *    caller shares the same pending-burst state per receiver.
 */

const DEFAULT_PACE_MS = 60; // just above the server's 50ms coalescing window
const DEFAULT_QUIET_WINDOW_MS = 4_000; // ~2x the observed confirm round trip

const TERMINAL_STATUSES = new Set(['failed', 'cancelled', 'timed-out']);

/** Structural — matches `CommandLifecycle` without importing it (this
 *  module stays store/transport-free; see the file-level invariants). */
interface EmittedLifecycle { status: string }

interface PendingTuning {
  /** Confirmed radio truth this burst's deltas are measured against. */
  baseline: number;
  /** Latest accumulated target — the honest prediction of where we're heading. */
  target: number;
  /** Wall-clock deadline; no further activity past this and the burst expires. */
  quietUntil: number;
  /** Non-null while a paced flush is scheduled. */
  flushTimer: ReturnType<typeof setTimeout> | null;
  /** Wall-clock time the last frame was actually emitted, for pacing. */
  lastEmitAt: number;
  /** Last emitted lifecycle OBJECT, mutated in place by the caller — read
   *  fresh, never snapshotted (review B3). */
  lastLifecycle: EmittedLifecycle | null;
  /** Session epoch / capabilities generation this burst started under; a
   *  mismatch at any later step/flush expires it (review B4). */
  epoch: number;
  generation: number | null;
}

export interface TuningAccumulatorOptions {
  /** Sends one `set_freq` for `receiver` at `freq`; returns its lifecycle
   *  object when known, so a NAK/failure can expire the burst early. */
  emit: (receiver: number, freq: number) => EmittedLifecycle | void;
  now?: () => number;
  paceMs?: number;
  quietWindowMs?: number;
  /** Current control-session epoch / capabilities generation (review B4);
   *  default to a constant so options that omit them never force a reset. */
  epoch?: () => number;
  generation?: () => number | null;
}

export interface TuningAccumulator {
  /** Feed one RELATIVE gesture: `confirmedFreq` is the current confirmed
   *  radio truth for `receiver`, `requestedFreq` the absolute target the
   *  presentation layer computed off its own (possibly stale) display. */
  step(receiver: number, confirmedFreq: number, requestedFreq: number): void;
  /** Feed one ABSOLUTE-target gesture (click-to-tune, station/QSY recall):
   *  clears any in-flight burst and emits `freq` unpaced (review B1). */
  jump(receiver: number, freq: number): void;
}

/** True when `observed` lies on the closed segment between `baseline` and
 *  `target`, in the direction of travel (review B2) — covers both
 *  endpoints and every intermediate target a burst has already emitted. */
function isOnSegment(baseline: number, target: number, observed: number): boolean {
  return target >= baseline
    ? observed >= baseline && observed <= target
    : observed <= baseline && observed >= target;
}

export function createTuningAccumulator(options: TuningAccumulatorOptions): TuningAccumulator {
  const {
    emit,
    // A plain `now = Date.now` default would capture a reference to
    // whatever `Date.now` IS AT CONSTRUCTION TIME. `makeVfoHandlers()` is
    // called once at module import for the singleton accessor
    // (`getVfoHandlers()` in `panel-adapters.ts`) — well before any test's
    // `vi.useFakeTimers()` swaps the global — so a captured reference would
    // silently keep reading real wall-clock time forever. Looking up
    // `Date.now` fresh on every call instead tracks whatever is currently
    // installed as the global, faked or not.
    now = () => Date.now(),
    paceMs = DEFAULT_PACE_MS,
    quietWindowMs = DEFAULT_QUIET_WINDOW_MS,
    epoch: getEpoch = () => 0,
    generation: getGeneration = () => null,
  } = options;
  const pending = new Map<number, PendingTuning>();

  function clear(receiver: number): void {
    const state = pending.get(receiver);
    if (state?.flushTimer) clearTimeout(state.flushTimer);
    pending.delete(receiver);
  }

  function sessionChanged(state: PendingTuning): boolean {
    return state.epoch !== getEpoch() || state.generation !== getGeneration();
  }

  function flush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state) return;
    if (sessionChanged(state)) { clear(receiver); return; }
    state.flushTimer = null;
    state.lastEmitAt = now();
    state.lastLifecycle = emit(receiver, state.target) ?? null;
  }

  function scheduleFlush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state || state.flushTimer !== null) return;
    const wait = Math.max(0, paceMs - (now() - state.lastEmitAt));
    state.flushTimer = setTimeout(() => flush(receiver), wait);
  }

  function beginFresh(receiver: number, t: number, baseline: number, target: number): PendingTuning {
    clear(receiver);
    const fresh: PendingTuning = {
      baseline,
      target,
      quietUntil: t + quietWindowMs,
      flushTimer: null,
      lastEmitAt: t,
      lastLifecycle: null,
      epoch: getEpoch(),
      generation: getGeneration(),
    };
    pending.set(receiver, fresh);
    return fresh;
  }

  return {
    step(receiver, confirmedFreq, requestedFreq) {
      const t = now();
      const state = pending.get(receiver);
      let hot = state !== undefined
        && t < state.quietUntil
        && !sessionChanged(state)
        && !TERMINAL_STATUSES.has(state.lastLifecycle?.status ?? '');

      if (hot) {
        if (isOnSegment(state!.baseline, state!.target, confirmedFreq)) {
          // Our own write landing mid-burst — the frozen baseline or any
          // intermediate target we already emitted — rebase and continue.
          state!.baseline = confirmedFreq;
        } else {
          // Off the segment: a physical observation we did not cause.
          hot = false;
        }
      }

      if (!hot) {
        const fresh = beginFresh(receiver, t, confirmedFreq, requestedFreq);
        fresh.lastLifecycle = emit(receiver, requestedFreq) ?? null;
        return;
      }

      state!.target += requestedFreq - state!.baseline;
      state!.quietUntil = t + quietWindowMs;
      scheduleFlush(receiver);
    },
    jump(receiver, freq) {
      // No pending burst to leave behind: an absolute target carries no
      // "baseline" a future relative step could honestly accumulate from.
      clear(receiver);
      emit(receiver, freq);
    },
  };
}

let singleton: TuningAccumulator | null = null;

/** Module-level singleton (review B5): options are captured from the FIRST
 *  caller only — every real caller wires functionally identical ones, so
 *  every `makeVfoHandlers()` instance shares one pending-burst state. */
export function getSharedTuningAccumulator(options: TuningAccumulatorOptions): TuningAccumulator {
  return singleton ??= createTuningAccumulator(options);
}

/** Test-only: clears the shared singleton for a clean slate between tests. */
export function resetSharedTuningAccumulatorForTests(): void {
  singleton = null;
}
