import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SystemController } from '../system-controller';

const teardown = [
  'audio',
  'ws:disconnect',
  'media:destroy',
  'radio:disconnect',
  'radio:reset',
];

function deferred() {
  let resolve!: () => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<void>((ok, fail) => {
    resolve = ok;
    reject = fail;
  });
  return { promise, resolve, reject };
}

function fixture() {
  const calls: string[] = [];
  const mark = (name: string) => () => calls.push(name);
  const controller = new SystemController({
    destroyAudio: mark('audio'),
    disconnectWebSockets: mark('ws:disconnect'),
    destroyMediaSession: mark('media:destroy'),
    setRadioDisconnected: mark('radio:disconnect'),
    resetRadioState: mark('radio:reset'),
    initMediaSession: mark('media:init'),
    reconnectWebSockets: mark('ws:reconnect'),
  });
  return { calls, controller };
}

describe('SystemController disconnect lifecycle', () => {
  it('keeps the no-barrier teardown synchronous and in legacy order', async () => {
    const { calls, controller } = fixture();

    const result = controller.disconnect();

    expect(calls).toEqual(teardown);
    await expect(result).resolves.toBeUndefined();
  });

  it('coalesces deferred and reentrant disconnects before teardown', async () => {
    const { calls, controller } = fixture();
    const gate = deferred();
    let reentrant: Promise<void> | undefined;
    const barrier = vi.fn(() => {
      calls.push('barrier');
      reentrant = controller.disconnect();
      return gate.promise;
    });
    controller.registerPreDisconnectBarrier(barrier);

    const first = controller.disconnect();
    const duplicate = controller.disconnect();
    controller.connect();

    expect(duplicate).toBe(first);
    expect(barrier).not.toHaveBeenCalled();
    expect(calls).toEqual([]);
    await Promise.resolve();
    expect(reentrant).toBe(first);
    expect(barrier).toHaveBeenCalledOnce();
    expect(calls).toEqual(['barrier']);

    gate.resolve();
    await first;
    expect(calls).toEqual(['barrier', ...teardown]);
  });

  it('tears down once before propagating a barrier rejection', async () => {
    const { calls, controller } = fixture();
    const failure = new Error('release uncertain');
    controller.registerPreDisconnectBarrier(() => Promise.reject(failure));

    const result = controller.disconnect();

    await expect(result).rejects.toBe(failure);
    expect(calls).toEqual(teardown);
    await expect(controller.disconnect()).resolves.toBeUndefined();
    expect(calls).toHaveLength(teardown.length);
  });

  it('uses exclusive exact registration and a captured barrier', async () => {
    const { controller } = fixture();
    const gate = deferred();
    const barrier = vi.fn(() => gate.promise);
    const replacement = vi.fn(() => Promise.resolve());
    const staleUnregister = controller.registerPreDisconnectBarrier(barrier);
    expect(() => controller.registerPreDisconnectBarrier(replacement)).toThrow();
    staleUnregister();
    staleUnregister();

    const unregister = controller.registerPreDisconnectBarrier(barrier);
    staleUnregister();
    const result = controller.disconnect();
    unregister();
    controller.registerPreDisconnectBarrier(replacement);
    unregister();
    await Promise.resolve();
    gate.resolve();
    await result;

    expect(barrier).toHaveBeenCalledOnce();
    expect(replacement).not.toHaveBeenCalled();
    controller.connect();
    await controller.disconnect();
    expect(replacement).toHaveBeenCalledOnce();
  });
});

// MOR-1409 A10 — causal RED for the retirement of the HTTP /state polling
// machinery. On exact base both hooks are present (the fixture above wires
// `setStopPolling`); this pins their removal.
describe('SystemController HTTP-polling registration removal (A10)', () => {
  it('no longer exposes registerPolling or setStopPolling', () => {
    const controller = new SystemController();
    const surface = controller as unknown as {
      registerPolling?: unknown;
      setStopPolling?: unknown;
    };
    expect(surface.registerPolling).toBeUndefined();
    expect(surface.setStopPolling).toBeUndefined();
  });
});

describe('SystemController credential-free HTTP', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    localStorage.clear();
    fetchMock.mockReset();
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  it('ignores retired tokens for power commands and preserves each POST body', async () => {
    fetchMock.mockResolvedValue({ ok: true } as Response);
    const { controller } = fixture();

    localStorage.setItem('rigplane-auth-token', 'power-one');
    await controller.powerOn();
    localStorage.setItem('rigplane-auth-token', 'power-two');
    await controller.powerOff();
    localStorage.setItem('rigplane-auth-token', 'identify-token');
    await controller.identifyFrequency(14_074_000);

    expect(fetchMock).toHaveBeenNthCalledWith(1, '/api/v1/radio/power', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ state: 'on' }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(2, '/api/v1/radio/power', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ state: 'off' }),
    });
    expect(fetchMock).toHaveBeenNthCalledWith(
      3,
      '/api/v1/eibi/identify?freq=14074000',
      { headers: {} },
    );
  });

  it('omits authorization without a token and preserves existing 401 behavior', async () => {
    const { controller } = fixture();
    fetchMock.mockResolvedValueOnce({
      ok: false,
      text: async () => 'Unauthorized',
    } as Response);
    await expect(controller.powerOn()).rejects.toThrow('Unauthorized');

    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 } as Response);
    await expect(controller.identifyFrequency(14_074_000)).resolves.toBeNull();
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/v1/eibi/identify?freq=14074000',
      { headers: {} },
    );
  });
});
