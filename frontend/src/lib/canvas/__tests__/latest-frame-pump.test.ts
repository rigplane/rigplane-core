import { describe, expect, it, vi } from 'vitest';
import { LatestFramePump, type FramePumpTiming } from '../latest-frame-pump';

// A controllable rAF: callbacks queue up until flush() fires them all, one
// "animation frame".
function fakeTiming() {
  const callbacks = new Map<number, FrameRequestCallback>();
  let nextId = 0;
  const timing: FramePumpTiming = {
    requestFrame: (cb) => { callbacks.set(++nextId, cb); return nextId; },
    cancelFrame: (id) => { callbacks.delete(id); },
  };
  const flush = (now = 16): void => {
    const pending = [...callbacks.values()];
    callbacks.clear();
    for (const cb of pending) cb(now);
  };
  return { timing, flush, callbacks };
}

describe('LatestFramePump (MOR-4206)', () => {
  it('paints nothing until the animation frame fires', () => {
    const { timing } = fakeTiming();
    const paint = vi.fn();
    const pump = new LatestFramePump<number>(paint, { timing });
    pump.push(1);
    expect(paint).not.toHaveBeenCalled();
    expect(pump.pending).toBe(true);
  });

  it('paints only the latest frame per animation frame and counts the dropped ones', () => {
    const { timing, flush, callbacks } = fakeTiming();
    const paint = vi.fn();
    const onDrop = vi.fn();
    const pump = new LatestFramePump<number>(paint, { timing, onDrop });
    // A render burst: 10 frames arrive inside one rAF window.
    for (let i = 1; i <= 10; i++) pump.push(i);
    // Exactly one animation frame is scheduled for the whole burst.
    expect(callbacks.size).toBe(1);
    flush();
    expect(paint).toHaveBeenCalledTimes(1);
    expect(paint).toHaveBeenCalledWith(10);
    expect(pump.droppedCount).toBe(9);
    expect(onDrop).toHaveBeenCalledTimes(9);
    expect(onDrop).toHaveBeenNthCalledWith(1, 1);
    expect(onDrop).toHaveBeenNthCalledWith(9, 9);
    expect(pump.pending).toBe(false);
  });

  it('paints every frame without drops when arrivals keep up with rAF', () => {
    const { timing, flush } = fakeTiming();
    const paint = vi.fn();
    const pump = new LatestFramePump<number>(paint, { timing });
    for (let i = 1; i <= 5; i++) {
      pump.push(i);
      flush(i * 16);
    }
    expect(paint.mock.calls.map((call) => call[0])).toEqual([1, 2, 3, 4, 5]);
    expect(pump.droppedCount).toBe(0);
  });

  it('a push from inside the paint callback schedules the next frame', () => {
    const { timing, flush, callbacks } = fakeTiming();
    const painted: number[] = [];
    let reentrant = true;
    const pump = new LatestFramePump<number>((frame) => {
      painted.push(frame);
      if (reentrant) { reentrant = false; pump.push(2); }
    }, { timing });
    pump.push(1);
    flush();
    expect(painted).toEqual([1]);
    expect(callbacks.size).toBe(1);
    flush();
    expect(painted).toEqual([1, 2]);
    expect(pump.droppedCount).toBe(0);
  });

  it('dispose cancels the scheduled frame and discards the pending one', () => {
    const { timing, flush, callbacks } = fakeTiming();
    const paint = vi.fn();
    const pump = new LatestFramePump<number>(paint, { timing });
    pump.push(1);
    pump.dispose();
    expect(callbacks.size).toBe(0);
    expect(pump.pending).toBe(false);
    flush();
    expect(paint).not.toHaveBeenCalled();
  });
});
