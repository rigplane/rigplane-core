import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  createPttGesture,
  type Guard,
  type PttGestureCommands,
  type PttGestureDeps,
  type PttGestureView,
} from '../tx-ptt-gesture';

function guard(leaseId: string, generation = 1, authorityEpoch = 1): Guard {
  return { leaseId, generation, authorityEpoch };
}

interface Harness {
  view: PttGestureView;
  commands: PttGestureCommands;
  deps: PttGestureDeps;
  setGuard(g: Guard | null): void;
  setLatched(v: boolean): void;
  getGuard(): Guard | null;
}

/**
 * Builds a fully-controllable fake view/commands/deps triple. `start()`
 * simulates a synchronous, successful lease acquisition (as the real
 * TxController does) by installing `startResult` as the live guard — pass
 * `startResult: null` to simulate a refused start.
 */
function makeHarness(options: { startResult?: Guard | null } = {}): Harness {
  let currentGuard: Guard | null = null;
  let latched = false;
  const startResult = options.startResult === undefined ? guard('lease-1') : options.startResult;

  const view: PttGestureView = {
    guard: () => currentGuard,
    latched: () => latched,
  };
  const commands: PttGestureCommands = {
    start: vi.fn(() => {
      currentGuard = startResult;
    }),
    latch: vi.fn(() => {
      latched = true;
    }),
    release: vi.fn(() => {
      latched = false;
    }),
  };
  const deps: PttGestureDeps = {
    schedule: vi.fn((callback: () => void, ms: number) => setTimeout(callback, ms)),
    cancel: vi.fn((handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>)),
  };

  return {
    view,
    commands,
    deps,
    setGuard: (g) => {
      currentGuard = g;
    },
    setLatched: (v) => {
      latched = v;
    },
    getGuard: () => currentGuard,
  };
}

describe('createPttGesture', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it('arms nothing on a hold — only up() schedules the release window', () => {
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();

    expect(h.commands.start).toHaveBeenCalledTimes(1);
    expect(h.deps.schedule).not.toHaveBeenCalled();

    vi.advanceTimersByTime(10_000);
    expect(h.commands.release).not.toHaveBeenCalled();
  });

  it('arms the window from up(), not down() — long hold then quick re-press still latches', () => {
    // Pins the timing source: the double-tap window must be measured from
    // up() (arm time), never from down()-to-down() elapsed time. A
    // down-to-down gate would treat this long hold + fast re-press as two
    // unrelated fresh presses (start() again) instead of a latch.
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    vi.advanceTimersByTime(5_000); // hold far longer than the window
    gesture.up();
    vi.advanceTimersByTime(50); // re-press inside the up()-armed window
    gesture.down();

    expect(h.commands.latch).toHaveBeenCalledTimes(1); // legacy timing would start() again
    expect(h.commands.start).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(10_000);
    expect(h.commands.release).not.toHaveBeenCalled();
  });

  it('releases exactly once at the window boundary (nothing at 299ms, fires at 300ms)', () => {
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.up();

    vi.advanceTimersByTime(299);
    expect(h.commands.release).not.toHaveBeenCalled();

    vi.advanceTimersByTime(1);
    expect(h.commands.release).toHaveBeenCalledTimes(1);
    expect(h.commands.release).toHaveBeenCalledWith(h.getGuard());
  });

  it('a second down() inside the window cancels the pending release and latches once', () => {
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.up();
    gesture.down();

    expect(h.deps.cancel).toHaveBeenCalledTimes(1);
    expect(h.commands.latch).toHaveBeenCalledTimes(1);
    expect(h.commands.start).toHaveBeenCalledTimes(1);

    vi.advanceTimersByTime(10_000);
    expect(h.commands.release).not.toHaveBeenCalled();
  });

  it('a tap while latched releases directly, without calling start() again', () => {
    const h = makeHarness();
    h.setGuard(guard('lease-1'));
    h.setLatched(true);
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();

    expect(h.commands.start).not.toHaveBeenCalled();
    expect(h.commands.release).toHaveBeenCalledTimes(1);
    expect(h.commands.release).toHaveBeenCalledWith(guard('lease-1'));

    // The up() that follows the unlatch tap must not arm a window either.
    gesture.up();
    expect(h.deps.schedule).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10_000);
    expect(h.commands.release).toHaveBeenCalledTimes(1);
  });

  it('fires the release window with the guard captured at arm time, not the live one', () => {
    const captured = guard('lease-1', 1, 1);
    const h = makeHarness({ startResult: captured });
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.up();

    // Simulate the live lease moving on (e.g. re-keyed to a new generation)
    // by some other path entirely, without going through this recognizer.
    const moved = guard('lease-1', 2, 1);
    h.setGuard(moved);

    vi.advanceTimersByTime(300);

    expect(h.commands.release).toHaveBeenCalledTimes(1);
    const releasedWith = (h.commands.release as ReturnType<typeof vi.fn>).mock.calls[0]![0];
    expect(releasedWith).toBe(captured);
    expect(releasedWith).not.toBe(moved);
  });

  it('a bumped token suppresses a stale pending fire even if cancel() is ineffective', () => {
    const h = makeHarness();
    // Make cancel a no-op spy so it records the call but does NOT actually
    // clear the real timer — isolating the token defence from the
    // explicit-cancellation defence.
    h.deps.cancel = vi.fn();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.up();
    gesture.down();

    expect(h.commands.latch).toHaveBeenCalledTimes(1);
    expect(h.deps.cancel).toHaveBeenCalledTimes(1);

    // The original real timer is still alive (cancel didn't clear it), but
    // the token no longer matches — it must no-op instead of releasing.
    vi.advanceTimersByTime(300);
    expect(h.commands.release).not.toHaveBeenCalled();
  });

  it('destroy() cancels a pending window, releases the live guard once, then goes inert', () => {
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.up();

    gesture.destroy();

    expect(h.deps.cancel).toHaveBeenCalledTimes(1);
    expect(h.commands.release).toHaveBeenCalledTimes(1);
    expect(h.commands.release).toHaveBeenCalledWith(h.getGuard());

    gesture.destroy();
    gesture.down();
    gesture.up();
    gesture.cancel();

    expect(h.commands.release).toHaveBeenCalledTimes(1);
    expect(h.commands.start).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(10_000);
    expect(h.commands.release).toHaveBeenCalledTimes(1);
  });

  it('cancel() behaves exactly like up()', () => {
    const h = makeHarness();
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    gesture.cancel();

    expect(h.deps.schedule).toHaveBeenCalledTimes(1);
    vi.advanceTimersByTime(300);
    expect(h.commands.release).toHaveBeenCalledTimes(1);
  });

  it('a refused start arms nothing, and the next press is a fresh start, not a latch', () => {
    const h = makeHarness({ startResult: null });
    const gesture = createPttGesture(h.view, h.commands, h.deps);

    gesture.down();
    expect(h.commands.start).toHaveBeenCalledTimes(1);

    gesture.up();
    expect(h.deps.schedule).not.toHaveBeenCalled();
    expect(h.commands.release).not.toHaveBeenCalled();

    gesture.down();
    expect(h.commands.start).toHaveBeenCalledTimes(2);
    expect(h.commands.latch).not.toHaveBeenCalled();
  });
});
