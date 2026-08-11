/**
 * MOR-1425 — rapid tuning-step accumulator.
 *
 * Digit-tuning gestures (wheel ticks, arrow presses) compute their target as
 * `displayed value + step` in the presentation layer (see
 * `primitives/frequency/FrequencyDisplayInteractive.svelte`), but the
 * displayed value is last-confirmed radio truth and only advances after a
 * full `set_freq` round trip (~0.5-2s measured on live hardware, operator
 * evidence on MOR-1425). A burst of gestures inside one round trip all
 * compute the SAME target relative to the same stale display, so N steps
 * net one write — the collapse this module fixes.
 *
 * This holds an ephemeral PENDING TARGET per receiver: while a burst is
 * "hot" (within `quietWindowMs` of the last step), each new gesture's delta
 * from the FROZEN baseline (confirmed truth at the start of the burst)
 * accumulates onto the pending target instead of being computed against —
 * and discarded relative to — the same stale baseline every time. Emission
 * is paced just above the server's 50ms same-command coalescing window
 * (MOR-1427, `control.py` `_CMD_MIN_INTERVAL`) so a burst collapses to a
 * few paced `set_freq` frames carrying the latest accumulated target, not
 * one per gesture.
 *
 * Invariants (design-bearing, see MOR-1425):
 *  - the DISPLAY stays last-confirmed truth; this module never patches a
 *    store and has no store dependency — it only calls the injected `emit`.
 *  - a confirmed observation that matches neither the frozen baseline nor
 *    our own accumulated target is a contradiction (the operator moved the
 *    physical dial) and resets accumulation immediately; the next step
 *    rebuilds its baseline from that new truth.
 *  - a confirmed observation that DOES match our own accumulated target is
 *    our own write landing mid-burst — not a contradiction — and simply
 *    rebases the frozen baseline so further deltas stay correct once the
 *    presentation layer re-renders against it.
 *  - the accumulator quietly expires `quietWindowMs` after the last step
 *    (default ~2x the observed confirm round trip) or immediately on a
 *    terminal (failed/cancelled/timed-out) command lifecycle.
 *  - the very first step of a cold window emits immediately, unpaced —
 *    single-step tuning behaves exactly as it did before this module.
 */

const DEFAULT_PACE_MS = 60; // just above the server's 50ms coalescing window
const DEFAULT_QUIET_WINDOW_MS = 4_000; // ~2x the observed confirm round trip

const TERMINAL_STATUSES = new Set(['failed', 'cancelled', 'timed-out']);

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
  /** Status of the last emitted command's lifecycle, if known. */
  lastStatus: string | null;
}

export interface TuningAccumulatorOptions {
  /** Sends one `set_freq` for `receiver` at `freq`; returns its lifecycle
   *  status when known, so a NAK/failure can expire the burst early. */
  emit: (receiver: number, freq: number) => { status: string } | void;
  now?: () => number;
  paceMs?: number;
  quietWindowMs?: number;
}

export interface TuningAccumulator {
  /**
   * Feed one gesture. `confirmedFreq` is the CURRENT confirmed radio truth
   * for `receiver`; `requestedFreq` is the ABSOLUTE target the presentation
   * layer computed off its own (possibly stale) displayed value.
   */
  step(receiver: number, confirmedFreq: number, requestedFreq: number): void;
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
  } = options;
  const pending = new Map<number, PendingTuning>();

  function clear(receiver: number): void {
    const state = pending.get(receiver);
    if (state?.flushTimer) clearTimeout(state.flushTimer);
    pending.delete(receiver);
  }

  function flush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state) return;
    state.flushTimer = null;
    state.lastEmitAt = now();
    state.lastStatus = emit(receiver, state.target)?.status ?? null;
  }

  function scheduleFlush(receiver: number): void {
    const state = pending.get(receiver);
    if (!state || state.flushTimer !== null) return;
    const wait = Math.max(0, paceMs - (now() - state.lastEmitAt));
    state.flushTimer = setTimeout(() => flush(receiver), wait);
  }

  return {
    step(receiver, confirmedFreq, requestedFreq) {
      const t = now();
      const state = pending.get(receiver);
      let hot = state !== undefined
        && t < state.quietUntil
        && !TERMINAL_STATUSES.has(state.lastStatus ?? '');

      if (hot && confirmedFreq !== state!.baseline) {
        if (confirmedFreq === state!.target) {
          // Our own accumulated write landed mid-burst: rebase and continue.
          state!.baseline = confirmedFreq;
        } else {
          // Matches neither the frozen baseline nor our own predicted
          // target: a physical observation we did not cause (invariant 3).
          hot = false;
        }
      }

      if (!hot) {
        clear(receiver);
        const fresh: PendingTuning = {
          baseline: confirmedFreq,
          target: requestedFreq,
          quietUntil: t + quietWindowMs,
          flushTimer: null,
          lastEmitAt: t,
          lastStatus: null,
        };
        pending.set(receiver, fresh);
        fresh.lastStatus = emit(receiver, requestedFreq)?.status ?? null;
        return;
      }

      state!.target += requestedFreq - state!.baseline;
      state!.quietUntil = t + quietWindowMs;
      scheduleFlush(receiver);
    },
  };
}
