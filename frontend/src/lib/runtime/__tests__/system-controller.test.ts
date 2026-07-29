import { describe, expect, it, vi } from 'vitest';

import { SystemController } from '../system-controller';

const teardown = [
  'audio',
  'ws:disconnect',
  'http:disconnect',
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
    setHttpDisconnected: mark('http:disconnect'),
    destroyMediaSession: mark('media:destroy'),
    setRadioDisconnected: mark('radio:disconnect'),
    resetRadioState: mark('radio:reset'),
    initMediaSession: mark('media:init'),
    clearEtag: mark('etag'),
    reconnectWebSockets: mark('ws:reconnect'),
  });
  return { calls, controller };
}

describe('SystemController disconnect lifecycle', () => {
  it('keeps the no-barrier teardown synchronous and in legacy order', async () => {
    const { calls, controller } = fixture();
    controller.setStopPolling(() => calls.push('polling'));

    const result = controller.disconnect();

    expect(calls).toEqual([...teardown.slice(0, 2), 'polling', ...teardown.slice(2)]);
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
