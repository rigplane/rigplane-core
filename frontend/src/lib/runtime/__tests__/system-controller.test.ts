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

describe('SystemController reconnect discovery', () => {
  beforeEach(() => { vi.resetModules(); vi.useFakeTimers(); });
  afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

  it.each([
    { mode: 'local', grant: null, expectedRemote: false },
    { mode: 'remote', grant: { controller_key: 'a'.repeat(64), generation: 7, ttl_ms: 6000, heartbeat_ms: 2000, controller_role: 'auxiliary' }, expectedRemote: true },
  ])('rediscovers stale local mode as $mode before opening channels', async ({ mode, grant, expectedRemote }) => {
    let discovered = 'local';
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) =>
      new Response(JSON.stringify(init?.method === 'POST' ? grant : { protocol_version: 1, mode: discovered }),
        { status: init?.method === 'POST' ? 201 : 200 }));
    vi.stubGlobal('fetch', fetcher);
    const { controllerClient } = await import('../../transport/controller-client');
    const { SystemController } = await import('../system-controller');
    await controllerClient.prepare();
    const calls: string[] = [];
    const controller = new SystemController({
      destroyAudio: () => {}, disconnectWebSockets: () => {}, destroyMediaSession: () => {},
      setRadioDisconnected: () => {}, resetRadioState: () => {},
      initMediaSession: () => calls.push('media'), reconnectWebSockets: () => calls.push('ws'),
    });
    await controller.disconnect();
    discovered = mode;
    controller.connect();
    await vi.waitFor(() => expect(calls).toEqual(['media', 'ws']));
    expect(controllerClient.remote).toBe(expectedRemote);
    expect(fetcher.mock.calls.filter(([, init]) => !init?.method)).toHaveLength(2);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(expectedRemote ? 1 : 0);
    if (expectedRemote) await controllerClient.release();
  });

  it.each([409, 503])('keeps channels closed on remote admission %i after stale local mode', async (statusCode) => {
    let remote = false;
    const fetcher = vi.fn(async (_url: string, init?: RequestInit) => new Response(JSON.stringify(
      init?.method === 'POST' ? { error: statusCode === 409 ? 'controller_busy' : 'controller_not_ready' }
        : { protocol_version: 1, mode: remote ? 'remote' : 'local' },
    ), { status: init?.method === 'POST' ? statusCode : 200 }));
    vi.stubGlobal('fetch', fetcher);
    const { controllerClient } = await import('../../transport/controller-client');
    const { SystemController } = await import('../system-controller');
    const { onMessage } = await import('../../transport/ws-client');
    const received = vi.fn();
    const stop = onMessage(received);
    await controllerClient.prepare();
    const reconnectWebSockets = vi.fn();
    const controller = new SystemController({
      destroyAudio: () => {}, disconnectWebSockets: () => {}, destroyMediaSession: () => {},
      setRadioDisconnected: () => {}, resetRadioState: () => {},
      initMediaSession: () => {}, reconnectWebSockets,
    });
    await controller.disconnect();
    remote = true;
    controller.connect();
    await vi.waitFor(() => expect(controller.clientConnected).toBe(false));
    expect(reconnectWebSockets).not.toHaveBeenCalled();
    expect(controllerClient.active).toBe(false);
    expect(fetcher.mock.calls.filter(([, init]) => init?.method === 'POST')).toHaveLength(1);
    expect(received.mock.calls.some(([note]) => note.code === (statusCode === 409 ? 'controller_busy' : 'controller_not_ready'))).toBe(true);
    stop();
  });

  it('cancels an in-flight reconnect so its late mode cannot override a newer connect', async () => {
    let finish!: (value: Response) => void;
    const delayed = new Promise<Response>((resolve) => { finish = resolve; });
    const local = () => new Response(JSON.stringify({ protocol_version: 1, mode: 'local' }));
    const fetcher = vi.fn().mockImplementationOnce(async () => local())
      .mockImplementationOnce(() => delayed).mockImplementationOnce(async () => local());
    vi.stubGlobal('fetch', fetcher);
    const { controllerClient } = await import('../../transport/controller-client');
    const { SystemController } = await import('../system-controller');
    await controllerClient.prepare();
    const reconnectWebSockets = vi.fn();
    const controller = new SystemController({
      destroyAudio: () => {}, disconnectWebSockets: () => {}, destroyMediaSession: () => {},
      setRadioDisconnected: () => {}, resetRadioState: () => {},
      initMediaSession: () => {}, reconnectWebSockets,
    });
    await controller.disconnect();
    controller.connect();
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2));
    await controller.disconnect();
    controller.connect();
    await vi.waitFor(() => expect(reconnectWebSockets).toHaveBeenCalledOnce());
    finish(new Response(JSON.stringify({ protocol_version: 1, mode: 'remote' })));
    await Promise.resolve();
    expect(controllerClient.remote).toBe(false);
    expect(controller.clientConnected).toBe(true);
    expect(reconnectWebSockets).toHaveBeenCalledOnce();
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});
