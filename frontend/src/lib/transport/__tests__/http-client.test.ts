import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ServerState } from '../../types/state';

// ─── Helpers ─────────────────────────────────────────────────────────────────

function makeState(revision: number): ServerState {
  return {
    revision,
    updatedAt: new Date().toISOString(),
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
    sub: {
      freqHz: 7000000,
      mode: 'LSB',
      filter: 1,
      dataMode: 0,
      sMeter: 0,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 50,
      rfGain: 100,
      squelch: 0,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
  };
}

/** Flush the microtask queue without advancing fake timers. */
async function flushMicrotasks() {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

// ─── Tests ───────────────────────────────────────────────────────────────────

describe('fetchState', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns parsed ServerState on success', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { fetchState } = await import('../http-client');
    const result = await fetchState();
    expect(result).not.toBeNull();
    expect(result!.revision).toBe(1);
    expect(result!.main.freqHz).toBe(14074000);
  });

  it('throws on non-ok response', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 503, headers: { get: () => null } });
    const { fetchState } = await import('../http-client');
    await expect(fetchState()).rejects.toThrow('fetchState: 503');
  });

  it('returns null on 304 Not Modified', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 304,
      headers: { get: () => '"1"' },
    });

    const { fetchState } = await import('../http-client');
    const result = await fetchState();
    expect(result).toBeNull();
  });
});

describe('fetchCapabilities', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns parsed Capabilities', async () => {
    const caps = {
      model: 'IC-7610',
      scope: true,
      audio: true,
      tx: true,
      capabilities: ['scope', 'dual_rx'],
      freqRanges: [],
      modes: ['USB', 'LSB'],
      filters: ['FIL1'],
    };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(caps),
    });

    const { fetchCapabilities } = await import('../http-client');
    const result = await fetchCapabilities();
    expect(result.model).toBe('IC-7610');
  });
});

describe('fetchInfo', () => {
  afterEach(() => vi.restoreAllMocks());

  it('returns parsed InfoResponse', async () => {
    const info = { version: '0.1.0', revision: 5, updatedAt: '2026-03-07T00:00:00Z', uptime: 42 };
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: () => Promise.resolve(info),
    });

    const { fetchInfo } = await import('../http-client');
    const result = await fetchInfo();
    expect(result.version).toBe('0.1.0');
    expect(result.revision).toBe(5);
  });
});

describe('startPolling', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.resetModules();
  });

  it('calls callback with state when revision advances', async () => {
    let rev = 1;
    globalThis.fetch = vi.fn().mockImplementation(() =>
      Promise.resolve({ ok: true, status: 200, headers: { get: () => '"' + (rev - 1) + '"' }, json: () => Promise.resolve(makeState(rev++)) }),
    );

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s));

    // first tick fires immediately (via void tick()), flush its microtasks
    await flushMicrotasks();

    expect(received.length).toBeGreaterThanOrEqual(1);
    expect(received[0].revision).toBe(1);

    stop();
  });

  it('skips callback when revision does not advance', async () => {
    const fixed = makeState(42);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"42"' },
      json: () => Promise.resolve(fixed),
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    // first tick: revision 42, callback called
    await flushMicrotasks();
    expect(received).toHaveLength(1);

    // advance to trigger second poll
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    // second poll: still revision 42 → skipped
    expect(received).toHaveLength(1);

    stop();
  });

  it('calls callback when only healthRevision advances', async () => {
    const first = makeState(42);
    first.healthRevision = 1;
    const second = makeState(42);
    second.healthRevision = 2;
    second.connection = { rigConnected: true, radioReady: false, controlConnected: true };
    second.radioHealth = {
      serverReachable: true,
      radioLink: 'connected',
      readiness: 'delayed',
      likelyCause: 'radio_not_responding',
      sinceMs: 1500,
      lastError: null,
    };
    let index = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      const state = index++ === 0 ? first : second;
      return Promise.resolve({
        ok: true,
        status: 200,
        headers: { get: () => `"42-${state.healthRevision}"` },
        json: () => Promise.resolve(state),
      });
    });

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s), 200);

    await flushMicrotasks();
    vi.advanceTimersByTime(200);
    await flushMicrotasks();

    expect(received).toHaveLength(2);
    expect(received[1].revision).toBe(42);
    expect(received[1].healthRevision).toBe(2);
    stop();
  });

  it('does not crash on fetch error', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const received: ServerState[] = [];
    const stop = startPolling((s) => received.push(s));

    // flush — should not throw
    await flushMicrotasks();

    expect(received).toHaveLength(0);
    stop();
  });

  it('calls setHttpConnected(true) on successful poll', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { startPolling } = await import('../http-client');
    const { getHttpConnected } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {});
    await flushMicrotasks();

    expect(getHttpConnected()).toBe(true);
    stop();
  });

  it('clears reconnecting on successful poll', async () => {
    const state = makeState(1);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => '"1"' },
      json: () => Promise.resolve(state),
    });

    const { startPolling } = await import('../http-client');
    const { isReconnecting, setReconnecting } = await import('../../stores/connection.svelte');

    setReconnecting(true);
    const stop = startPolling(() => {});
    await flushMicrotasks();

    expect(isReconnecting()).toBe(false);
    stop();
  });

  it('calls setHttpConnected(false) after 3 consecutive errors', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { getHttpConnected } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);

    // Trigger 3 consecutive failures
    for (let i = 0; i < 3; i++) {
      await flushMicrotasks();
      vi.advanceTimersByTime(10);
    }
    await flushMicrotasks();

    expect(getHttpConnected()).toBe(false);
    stop();
  });

  it('classifies repeated HTTP failures as server_unreachable', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { getRadioHealth } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);

    for (let i = 0; i < 3; i++) {
      await flushMicrotasks();
      vi.advanceTimersByTime(10);
    }
    await flushMicrotasks();

    expect(getRadioHealth()?.likelyCause).toBe('server_unreachable');
    stop();
  });

  it('marks reconnecting when polling errors repeat', async () => {
    globalThis.fetch = vi.fn().mockRejectedValue(new Error('network gone'));

    const { startPolling } = await import('../http-client');
    const { isReconnecting } = await import('../../stores/connection.svelte');

    const stop = startPolling(() => {}, 10);
    await flushMicrotasks();

    expect(isReconnecting()).toBe(true);
    stop();
  });

  it('returns a stop function that halts polling', async () => {
    let callCount = 0;
    globalThis.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      return Promise.resolve({ ok: true, status: 200, headers: { get: () => '"' + callCount + '"' }, json: () => Promise.resolve(makeState(callCount)) });
    });

    const { startPolling } = await import('../http-client');
    const stop = startPolling(() => {}, 200);

    // first tick
    await flushMicrotasks();
    const countAfterFirst = callCount;
    expect(countAfterFirst).toBe(1);

    stop();

    // advance past interval — no new calls
    vi.advanceTimersByTime(400);
    await flushMicrotasks();

    expect(callCount).toBe(countAfterFirst);
  });

  it('does not pile up concurrent polls (inflight guard)', async () => {
    let resolvePending: (() => void) | undefined;
    let callCount = 0;

    globalThis.fetch = vi.fn().mockImplementation(() => {
      callCount++;
      return new Promise((resolve) => {
        resolvePending = () =>
          resolve({ ok: true, status: 200, headers: { get: () => '"' + callCount + '"' }, json: () => Promise.resolve(makeState(callCount)) });
      });
    });

    const { startPolling } = await import('../http-client');
    const stop = startPolling(() => {}, 200);

    // first tick is inflight (not yet resolved)
    await flushMicrotasks();
    expect(callCount).toBe(1);

    // advance well past interval — second tick should not fire while first is pending
    vi.advanceTimersByTime(600);
    await flushMicrotasks();

    // still only 1 fetch outstanding due to inflight guard
    expect(callCount).toBe(1);

    resolvePending?.();
    stop();
  });
});
