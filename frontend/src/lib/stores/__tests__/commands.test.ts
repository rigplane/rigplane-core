import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

describe('command lifecycle store', () => {
  let store: typeof import('../commands.svelte');

  beforeEach(async () => {
    vi.useFakeTimers();
    vi.resetModules();
    store = await import('../commands.svelte');
  });

  afterEach(() => {
    store.resetCommandLifecycle();
    vi.useRealTimers();
  });

  it('records only bounded diagnostic lifecycle metadata', () => {
    const command = store.beginCommand({
      id: 'freq-1',
      name: 'set_freq',
      params: { freq: 14_074_000, receiver: 0 },
      originalEpoch: 7,
    });

    expect(command).toMatchObject({
      id: 'freq-1',
      name: 'set_freq',
      params: { freq: 14_074_000, receiver: 0 },
      originalEpoch: 7,
      status: 'pending',
      timeoutMs: 5_000,
    });
    expect(command).not.toHaveProperty('confirmedValue');
    expect(command).not.toHaveProperty('radioState');
    expect(store.getCommandLifecycle('freq-1', 7)?.status).toBe('pending');
    expect(store.hasPendingCommands()).toBe(true);
    expect(() => store.beginCommand({ ...command })).toThrow(/duplicate command id/i);
  });

  it('fences acknowledgement and failure by command id and originating epoch', () => {
    store.beginCommand({ id: 'same', name: 'set_mode', params: { mode: 'USB' }, originalEpoch: 3 });
    store.acknowledgeCommand('same', 2, 3);
    expect(store.getCommandLifecycle('same', 3)?.status).toBe('pending');

    store.acknowledgeCommand('same', 3, 4);
    expect(store.getCommandLifecycle('same', 3)).toMatchObject({
      status: 'acknowledged',
      eventEpoch: 4,
    });
    store.failCommand('same', 3, 4, 'late NAK');
    expect(store.getCommandLifecycle('same', 3)?.status).toBe('failed');

    store.beginCommand({ id: 'failure', name: 'set_vfo', params: { vfo: 'B' }, originalEpoch: 5 });
    store.failCommand('failure', 5, 5, 'denied');
    expect(store.getCommandLifecycle('failure', 5)).toMatchObject({
      status: 'failed',
      error: 'denied',
    });
  });

  it('cancels all pending work from the disconnected session and ignores stale results', () => {
    store.beginCommand({ id: 'old-a', name: 'set_freq', params: { freq: 1 }, originalEpoch: 10 });
    store.beginCommand({ id: 'old-b', name: 'set_mode', params: { mode: 'CW' }, originalEpoch: 10 });
    store.beginCommand({ id: 'new', name: 'set_filter', params: { filter: 2 }, originalEpoch: 11 });

    store.cancelPendingCommands(10, 'session-disconnected');
    expect(store.getCommandLifecycle('old-a', 10)?.status).toBe('cancelled');
    expect(store.getCommandLifecycle('old-b', 10)?.status).toBe('cancelled');
    expect(store.getCommandLifecycle('new', 11)?.status).toBe('pending');

    store.acknowledgeCommand('old-a', 10, 11);
    expect(store.getCommandLifecycle('old-a', 10)?.status).toBe('cancelled');
  });

  it('uses the existing per-record timeout and isolates it from later commands', () => {
    store.beginCommand({ id: 'slow', name: 'set_freq', params: { freq: 1 }, originalEpoch: 1, timeoutMs: 25 });
    vi.advanceTimersByTime(10);
    store.beginCommand({ id: 'fresh', name: 'set_freq', params: { freq: 2 }, originalEpoch: 1, timeoutMs: 25 });
    vi.advanceTimersByTime(15);

    expect(store.getCommandLifecycle('slow', 1)?.status).toBe('timed-out');
    expect(store.getCommandLifecycle('fresh', 1)?.status).toBe('pending');
  });

  it('bounds retained lifecycle records and cleans up oldest terminal entries first', () => {
    for (let i = 0; i < 110; i += 1) {
      store.beginCommand({
        id: `cmd-${i}`,
        name: 'set_freq',
        params: { freq: i },
        originalEpoch: 1,
      });
      store.failCommand(`cmd-${i}`, 1, 1, 'fixture');
    }

    const records = store.getCommandLifecycles();
    expect(records).toHaveLength(100);
    expect(records.some((record) => record.id === 'cmd-0')).toBe(false);
    expect(records.some((record) => record.id === 'cmd-109')).toBe(true);
  });
});
