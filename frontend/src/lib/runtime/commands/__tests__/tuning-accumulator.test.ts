/**
 * MOR-1425 — unit tests for the rapid tuning-step accumulator in isolation
 * from the store/transport wiring (see `panel-commands.intent.isolated.test.ts`
 * for the end-to-end wiring coverage through `makeVfoHandlers()`).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createTuningAccumulator,
  getSharedTuningAccumulator,
  resetSharedTuningAccumulatorForTests,
  type TuningAccumulator,
} from '../tuning-accumulator';

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

  it('(e) expires when the retained lifecycle OBJECT mutates to a terminal status after emit — the real dispatchRadioIntent shape (review B3)', () => {
    // `dispatchRadioIntent` returns a `CommandLifecycle` object that starts
    // 'pending' and is MUTATED IN PLACE later, once the transport resolves
    // it — never replaced with a fresh object carrying the final status.
    // A mock that returns `{status:'failed'}` straight away (the pre-fix
    // test) exercises a shape production never produces.
    const lifecycle = { status: 'pending' };
    emit.mockReturnValueOnce(lifecycle);
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate — still pending

    // The async failure arrives later by mutating the SAME retained object,
    // exactly like `failCommand`/`acknowledgeCommand` do in production.
    lifecycle.status = 'failed';

    acc.step(0, confirmed, confirmed + 2_000);
    // Treated as a fresh cold start (the prior burst is dead), not an
    // accumulation onto a target that will never be retried. If the
    // accumulator had snapshotted `.status` at emit time instead of
    // retaining the object, this step would see the STALE 'pending' value
    // and wrongly accumulate (paced, not immediate) instead.
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 2_000);
  });

  it('tracks receivers independently', () => {
    acc.step(0, 14_074_000, 14_075_000);
    acc.step(1, 7_100_000, 7_101_000);
    expect(emit).toHaveBeenNthCalledWith(1, 0, 14_075_000);
    expect(emit).toHaveBeenNthCalledWith(2, 1, 7_101_000);
  });

  it('(g) jump(receiver, freq) clears accumulator state and emits the exact frequency immediately, unpaced — even mid-burst (review B1)', () => {
    const confirmed = 14_074_000;
    acc.step(0, confirmed, confirmed + 1_000); // cold, immediate
    acc.step(0, confirmed, confirmed + 1_000); // hot, accumulates, paced flush pending
    expect(emit).toHaveBeenCalledTimes(1);

    acc.jump(0, 18_100_000); // absolute target — must land EXACTLY, not accumulate
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, 18_100_000);

    // The pending paced flush from the pre-jump burst must not fire
    // afterward and corrupt the jump target.
    vi.advanceTimersByTime(60);
    expect(emit).toHaveBeenCalledTimes(2);

    // The next step is a fresh cold start off the jumped-to frequency, not
    // a continuation of the pre-jump burst.
    acc.step(0, 18_100_000, 18_101_000);
    expect(emit).toHaveBeenCalledTimes(3);
    expect(emit).toHaveBeenLastCalledWith(0, 18_101_000);
  });

  it('(h) an intermediate own-write echo mid-burst rebases without reset — accumulation survives and emitted targets never go backward (review B2)', () => {
    const confirmed = 14_074_000;
    const step = 1_000;

    acc.step(0, confirmed, confirmed + step); // cold, immediate: target C+1_000
    acc.step(0, confirmed, confirmed + step); // hot: target C+2_000, paced
    acc.step(0, confirmed, confirmed + step); // hot: target C+3_000, same paced flush
    vi.advanceTimersByTime(60); // flush carries the latest target: C+3_000

    acc.step(0, confirmed, confirmed + step); // hot, still no echo yet: target C+4_000, paced
    vi.advanceTimersByTime(60); // flush: C+4_000 already sent to the radio

    // The echo of the VERY FIRST write (C+1_000 — long superseded by the
    // C+4_000 already in flight) finally lands: an INTERMEDIATE target, not
    // the frozen baseline (C) and not the current pending target either —
    // exactly the failure mode from the live capture (review B2).
    acc.step(0, confirmed + step, confirmed + 2 * step);
    vi.advanceTimersByTime(60);

    const targets = emit.mock.calls.map(([, freq]) => freq);
    expect(targets).toEqual([
      confirmed + step,
      confirmed + 3 * step,
      confirmed + 4 * step,
      confirmed + 5 * step,
    ]);
    for (let i = 1; i < targets.length; i++) {
      expect(targets[i]).toBeGreaterThanOrEqual(targets[i - 1]);
    }
  });

  it('(i) resets accumulation when the control-session epoch changes mid-burst (review B4)', () => {
    let epoch = 1;
    const local = createTuningAccumulator({ emit, paceMs: 60, quietWindowMs: 4_000, epoch: () => epoch });
    const confirmed = 14_074_000;
    local.step(0, confirmed, confirmed + 1_000); // cold, immediate
    local.step(0, confirmed, confirmed + 1_000); // hot, paced

    epoch = 2; // reconnect — a NEW control session
    local.step(0, confirmed, confirmed + 1_000);

    // Treated as a fresh cold start under the new epoch, not a continuation
    // of the pre-reconnect accumulation.
    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 1_000);
  });

  it('(j) resets accumulation when the capabilities generation changes mid-burst (review B4)', () => {
    let generation: number | null = 7;
    const local = createTuningAccumulator({ emit, paceMs: 60, quietWindowMs: 4_000, generation: () => generation });
    const confirmed = 14_074_000;
    local.step(0, confirmed, confirmed + 1_000); // cold, immediate
    local.step(0, confirmed, confirmed + 1_000); // hot, paced

    generation = 8; // radio switch / re-negotiated capabilities
    local.step(0, confirmed, confirmed + 1_000);

    expect(emit).toHaveBeenCalledTimes(2);
    expect(emit).toHaveBeenLastCalledWith(0, confirmed + 1_000);
  });
});

describe('MOR-1425 getSharedTuningAccumulator (review B5)', () => {
  afterEach(() => resetSharedTuningAccumulatorForTests());

  it('returns the SAME instance across repeated calls, independent of caller options — the fix for two live accumulators per receiver', () => {
    vi.useFakeTimers();
    const sharedEmit = vi.fn((_r: number, _f: number) => ({ status: 'pending' }));
    const first = getSharedTuningAccumulator({ emit: sharedEmit, paceMs: 60, quietWindowMs: 4_000 });
    // A second "composition root" constructs its OWN options object (its
    // own emit spy) — this must not create a second, independently-tracked
    // accumulator for the same receiver.
    const second = getSharedTuningAccumulator({ emit: vi.fn(), paceMs: 60, quietWindowMs: 4_000 });
    expect(second).toBe(first);

    const confirmed = 14_074_000;
    const step = 1_000;
    first.step(0, confirmed, confirmed + step); // cold, immediate
    expect(sharedEmit).toHaveBeenCalledTimes(1);

    // If `second` tracked independent state, this would ALSO be a cold,
    // immediate start (on `second`'s own emit spy). Because it shares
    // `first`'s pending burst instead, it accumulates and paces.
    second.step(0, confirmed, confirmed + step);
    expect(sharedEmit).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(60);
    expect(sharedEmit).toHaveBeenCalledTimes(2);
    expect(sharedEmit).toHaveBeenLastCalledWith(0, confirmed + 2 * step);
    vi.useRealTimers();
  });
});
