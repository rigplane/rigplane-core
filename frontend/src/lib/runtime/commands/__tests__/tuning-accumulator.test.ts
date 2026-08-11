/**
 * MOR-1425 — unit tests for the rapid tuning-step accumulator in isolation
 * from the store/transport wiring (see `panel-commands.intent.isolated.test.ts`
 * for the end-to-end wiring coverage through `makeVfoHandlers()`).
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createTuningAccumulator, type TuningAccumulator } from '../tuning-accumulator';

describe('MOR-1425 tuning accumulator', () => {
  let emit: ReturnType<typeof vi.fn<(receiver: number, freq: number) => { status: string }>>;
  let acc: TuningAccumulator;

  beforeEach(() => {
    vi.useFakeTimers();
    emit = vi.fn((_receiver: number, _freq: number) => ({ status: 'pending' }));
    acc = createTuningAccumulator({ emit, paceMs: 60, quietWindowMs: 4_000 });
  });

  it('(f) emits an isolated single step immediately, unpaced', () => {
    acc.step(0, 14_074_000, 14_075_000);
    expect(emit).toHaveBeenCalledExactlyOnceWith(0, 14_075_000);
  });

  it('(a) N rapid steps within one round trip accumulate onto the pending target, and the last frame carries confirmed + N*step', () => {
    const confirmed = 14_074_000;
    const step = 1_000;
    const N = 20;
    for (let i = 1; i <= N; i++) {
      // Each gesture computes target = displayed (still-stale confirmed) +
      // step — exactly the operator-captured mechanism from MOR-1425.
      acc.step(0, confirmed, confirmed + step);
    }
    // First (cold) step emitted immediately; the rest are paced.
    expect(emit).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(60);
    const lastCall = emit.mock.calls.at(-1)!;
    expect(lastCall).toEqual([0, confirmed + N * step]);
  });

  it('(b) paced emissions during a sustained burst are at least ~60ms apart', () => {
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate
    expect(emit).toHaveBeenCalledTimes(1);

    acc.step(0, confirmed, confirmed + 2_000); // hot, schedules a flush
    expect(emit).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(59);
    expect(emit).toHaveBeenCalledTimes(1); // not yet — under the pace window
    vi.advanceTimersByTime(1);
    expect(emit).toHaveBeenCalledTimes(2); // paced flush fires at >=60ms

    acc.step(0, confirmed, confirmed + 3_000); // hot again, schedules another
    vi.advanceTimersByTime(59);
    expect(emit).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1);
    expect(emit).toHaveBeenCalledTimes(3);
  });

  it('(c) a contradictory confirmed observation resets accumulation — the next step bases on the new truth', () => {
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate: target 14_075_000
    acc.step(0, confirmed, confirmed + 2_000); // hot: target 14_076_000, paced

    // The operator turned the physical knob: a confirmed freq that matches
    // neither our frozen baseline (14_074_000) nor our pending target
    // (14_076_000).
    const physical = 14_200_000;
    acc.step(0, physical, physical + 1_000);

    // The reset step is itself a cold start: immediate, unpaced, and its
    // delta is measured from the NEW physical truth, not the old baseline.
    expect(emit).toHaveBeenLastCalledWith(0, physical + 1_000);
  });

  it('(c) a confirmed observation that matches our own predicted target is NOT a contradiction', () => {
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate: target 14_075_000
    // Our own first write lands mid-burst — confirmed truth now equals what
    // we predicted. This must rebase, not reset: the next step's delta is
    // measured from the new (matching) baseline and continues accumulating.
    acc.step(0, confirmed + 1_000, confirmed + 1_000 + 1_000);
    vi.advanceTimersByTime(60);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 2_000);
  });

  it('(d) the accumulator expires after the quiet window and the next step is a fresh cold start', () => {
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000);
    expect(emit).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(4_001); // past quietWindowMs with no further activity

    acc.step(0, confirmed, confirmed + 1_000);
    // A fresh cold start emits immediately again — no pacing delay, and
    // the target is not double-accumulated from the expired burst.
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 1_000);
  });

  it('expires immediately on a terminal command-lifecycle status (NAK/error)', () => {
    emit.mockReturnValueOnce({ status: 'failed' });
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate — fails

    acc.step(0, confirmed, confirmed + 2_000);
    // Treated as a fresh cold start (the prior burst is dead), not an
    // accumulation onto a target that will never be retried.
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 2_000);
  });

  it('tracks receivers independently', () => {
    acc.step(0, 14_074_000, 14_075_000);
    acc.step(1, 7_100_000, 7_101_000);
    expect(emit).toHaveBeenNthCalledWith(1, 0, 14_075_000);
    expect(emit).toHaveBeenNthCalledWith(2, 1, 7_101_000);
  });
});
