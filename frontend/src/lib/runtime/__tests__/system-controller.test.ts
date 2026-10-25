import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SystemController } from '../system-controller';
import { getAudioState, setRxError, setTxError } from '../../stores/audio.svelte';

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
  const audioOptions: Array<{ automatic?: boolean } | undefined> = [];
  const mark = (name: string) => () => calls.push(name);
  const controller = new SystemController({
    destroyAudio: (options?: { automatic?: boolean }) => {
      audioOptions.push(options);
      calls.push('audio');
    },
    disconnectWebSockets: mark('ws:disconnect'),
    destroyMediaSession: mark('media:destroy'),
    setRadioDisconnected: mark('radio:disconnect'),
    resetRadioState: mark('radio:reset'),
    initMediaSession: mark('media:init'),
    reconnectWebSockets: mark('ws:reconnect'),
  });
  return { calls, audioOptions, controller };
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

// MOR-3753 PR A — an automatic disconnect forwards its reason to the audio
// teardown and preserves latched audio errors; an explicit disconnect
// acknowledges them state-only (setRxError/setTxError), never by repeating
// media cleanup. The teardown call sequence stays exactly the original one.
describe('SystemController automatic-reason propagation (MOR-3753 A)', () => {
  beforeEach(() => {
    setRxError(false);
    setTxError(false);
  });

  it('forwards the automatic reason to destroyAudio and preserves latched errors', async () => {
    const { calls, audioOptions, controller } = fixture();
    setRxError(true);
    setTxError(true);

    await controller.disconnect({ automatic: true });

    expect(calls).toEqual(teardown);
    expect(audioOptions).toEqual([{ automatic: true }]);
    expect(getAudioState().rxError).toBe(true);
    expect(getAudioState().txError).toBe(true);
  });

  it('an automatic disconnect with nothing latched manufactures no error', async () => {
    const { calls, controller } = fixture();

    await controller.disconnect({ automatic: true });

    expect(calls).toEqual(teardown);
    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(false);
  });

  it('an explicit disconnect after a completed automatic one clears state only', async () => {
    const { calls, controller } = fixture();
    await controller.disconnect({ automatic: true });
    setRxError(true);
    setTxError(true);

    await controller.disconnect();

    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(false);
    // State-only acknowledgement: exactly the original teardown ran — no
    // second audio destroy, no repeated WS/MediaSession/radio pass.
    expect(calls).toEqual(teardown);
  });

  it('an explicit disconnect joining an in-flight automatic one coalesces and clears by completion', async () => {
    const { calls, audioOptions, controller } = fixture();
    const gate = deferred();
    controller.registerPreDisconnectBarrier(() => gate.promise);
    setRxError(true);

    const automatic = controller.disconnect({ automatic: true });
    const joined = controller.disconnect();
    expect(joined).toBe(automatic);

    gate.resolve();
    await joined;

    expect(calls).toEqual(teardown);
    expect(audioOptions).toEqual([{ automatic: true }]);
    expect(getAudioState().rxError).toBe(false);
  });

  it('an automatic disconnect joining an in-flight one stays coalesced and preserves errors', async () => {
    const { calls, controller } = fixture();
    const gate = deferred();
    controller.registerPreDisconnectBarrier(() => gate.promise);
    setTxError(true);

    const first = controller.disconnect({ automatic: true });
    const second = controller.disconnect({ automatic: true });
    expect(second).toBe(first);

    gate.resolve();
    await first;
    expect(calls).toEqual(teardown);
    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(true);
  });

  it('an explicit join acknowledges failures latched after the join once the barrier resolves', async () => {
    const { calls, audioOptions, controller } = fixture();
    const gate = deferred();
    controller.registerPreDisconnectBarrier(() => gate.promise);

    const automatic = controller.disconnect({ automatic: true });
    const joined = controller.disconnect();
    expect(joined).toBe(automatic);
    // The retiring session's socket/capture fails AFTER the join, while the
    // barrier still holds: the entry clear alone would strand this latch.
    setRxError(true);
    setTxError(true);

    gate.resolve();
    await joined;

    expect(calls).toEqual(teardown);
    expect(audioOptions).toEqual([{ automatic: true }]);
    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(false);
  });

  it('an explicit join acknowledges a failure latched after the join even when the barrier rejects', async () => {
    const { calls, audioOptions, controller } = fixture();
    const failure = new Error('release uncertain');
    controller.registerPreDisconnectBarrier(() => Promise.reject(failure));

    const automatic = controller.disconnect({ automatic: true });
    const joined = controller.disconnect();
    expect(joined).toBe(automatic);
    setRxError(true);
    setTxError(true);

    await expect(joined).rejects.toBe(failure);
    expect(calls).toEqual(teardown);
    expect(audioOptions).toEqual([{ automatic: true }]);
    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(false);
  });

  it('an automatic-only operation keeps a failure latched after it completes', async () => {
    const { calls, controller } = fixture();
    const gate = deferred();
    controller.registerPreDisconnectBarrier(() => gate.promise);

    const automatic = controller.disconnect({ automatic: true });
    setTxError(true);
    gate.resolve();
    await automatic;

    expect(calls).toEqual(teardown);
    expect(getAudioState().rxError).toBe(false);
    expect(getAudioState().txError).toBe(true);
  });

  it('a failure of a later new attempt after completion is not erased by the earlier acknowledgement', async () => {
    const { calls, controller } = fixture();
    const gate = deferred();
    controller.registerPreDisconnectBarrier(() => gate.promise);

    const automatic = controller.disconnect({ automatic: true });
    const joined = controller.disconnect();
    gate.resolve();
    await joined;
    expect(getAudioState().txError).toBe(false);

    // A deliberate new attempt after the completed disconnect fails: its
    // latch is new state, not the acknowledged operation's, and no delayed
    // clear from the finished operation may erase it.
    setTxError(true);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
    expect(getAudioState().txError).toBe(true);
    expect(calls).toEqual(teardown);
  });

  it('a rejected automatic disconnect preserves the error; an explicit call then clears it', async () => {
    const { calls, audioOptions, controller } = fixture();
    const failure = new Error('release uncertain');
    controller.registerPreDisconnectBarrier(() => Promise.reject(failure));
    setRxError(true);

    const result = controller.disconnect({ automatic: true });
    await expect(result).rejects.toBe(failure);
    expect(calls).toEqual(teardown);
    expect(audioOptions).toEqual([{ automatic: true }]);
    expect(getAudioState().rxError).toBe(true);

    await expect(controller.disconnect()).resolves.toBeUndefined();
    expect(calls).toEqual(teardown);
    expect(getAudioState().rxError).toBe(false);
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
