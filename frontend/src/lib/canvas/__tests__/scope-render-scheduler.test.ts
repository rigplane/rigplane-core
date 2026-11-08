import { describe, expect, it, vi } from 'vitest';
import { ScopeRenderScheduler, type ScopeRenderTiming } from '../scope-render-scheduler';

function fakeTiming() {
  let nextId = 0;
  let requested = 0;
  const pending = new Map<number, (now: number) => void>();
  const timing: ScopeRenderTiming = {
    requestFrame: (cb) => {
      requested += 1;
      const id = (nextId += 1);
      pending.set(id, cb);
      return id;
    },
    cancelFrame: (id) => {
      pending.delete(id);
    },
  };
  return {
    timing,
    get pendingCount() {
      return pending.size;
    },
    get requestedCount() {
      return requested;
    },
    flush(now = 16) {
      const callbacks = [...pending.values()];
      pending.clear();
      for (const cb of callbacks) cb(now);
    },
  };
}

describe('ScopeRenderScheduler', () => {
  it('does not paint before the animation frame fires', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing });
    scheduler.wake();
    expect(paint).not.toHaveBeenCalled();
    expect(t.pendingCount).toBe(1);
    t.flush(32);
    expect(paint).toHaveBeenCalledTimes(1);
    expect(paint).toHaveBeenCalledWith(32);
  });

  it('coalesces 60 wakes inside one animation frame into exactly one draw', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing });
    for (let i = 0; i < 60; i += 1) scheduler.wake('frame');
    expect(scheduler.pending).toBe(true);
    expect(t.requestedCount).toBe(1);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
    expect(scheduler.pending).toBe(false);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
  });

  it('requests no animation frame while inactive and repaints on the visibility wake', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    let active = false;
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing, active: () => active });
    scheduler.wake('frame');
    scheduler.wake('size');
    scheduler.wake('view');
    expect(t.requestedCount).toBe(0);
    expect(paint).not.toHaveBeenCalled();
    active = true;
    scheduler.wake('visibility');
    expect(t.requestedCount).toBe(1);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
  });

  it('schedules nothing for wakes that arrive after the surface went inactive', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    let active = true;
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing, active: () => active });
    scheduler.wake();
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
    active = false;
    scheduler.wake('frame');
    expect(t.requestedCount).toBe(1);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
  });

  it('a reentrant wake from inside paint schedules exactly one more frame', () => {
    const t = fakeTiming();
    let scheduler!: ScopeRenderScheduler;
    let first = true;
    const paint = vi.fn(() => {
      if (first) {
        first = false;
        scheduler.wake('view');
      }
    });
    scheduler = new ScopeRenderScheduler(paint, { timing: t.timing });
    scheduler.wake();
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
    expect(t.pendingCount).toBe(1);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(2);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(2);
  });

  it('dispose cancels a pending frame and ignores later wakes', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing });
    scheduler.wake();
    scheduler.dispose();
    expect(t.pendingCount).toBe(0);
    t.flush();
    expect(paint).not.toHaveBeenCalled();
    scheduler.wake();
    expect(t.requestedCount).toBe(1);
  });

  it('cancel clears the pending frame and dirtiness but stays usable', () => {
    const t = fakeTiming();
    const paint = vi.fn();
    const scheduler = new ScopeRenderScheduler(paint, { timing: t.timing });
    scheduler.wake();
    expect(scheduler.pending).toBe(true);
    scheduler.cancel();
    expect(scheduler.pending).toBe(false);
    expect(t.pendingCount).toBe(0);
    t.flush();
    expect(paint).not.toHaveBeenCalled();
    // A wake after the cancelled frame paints exactly once.
    scheduler.wake('view');
    expect(t.requestedCount).toBe(2);
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
    // Dirtiness recorded before the cancel died with it: a bare flush after
    // the (already-cancelled) frame paints nothing further.
    t.flush();
    expect(paint).toHaveBeenCalledTimes(1);
  });
});
