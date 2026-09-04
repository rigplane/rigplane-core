import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { connect, disconnect } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { setRadioHealth, setRadioReady } from '$lib/stores/connection.svelte';
import {
  getCommandLifecycles,
  resetCommandLifecycle,
} from '$lib/stores/commands.svelte';
import {
  createDefaultLocalExtensionHostApi,
  createLocalExtensionHostApi,
  installLocalExtensionHostApi,
  LOCAL_EXTENSION_HOST_API_VERSION,
  type LocalExtensionHostApiV2,
  type LocalExtensionHostWindow,
  type RadioStateSubscriber,
} from '../host-api';
import {
  getLocalExtensionKeyboardScope,
  resetLocalExtensionKeyboardScope,
  setLocalExtensionKeyboardScope,
} from '../keyboard-scope';

describe('createLocalExtensionHostApi', () => {
  afterEach(() => {
    resetLocalExtensionKeyboardScope();
  });

  it('exposes a versioned state and capabilities API', () => {
    const state = { revision: 7 } as ServerState;
    const capabilities = { model: 'TEST', capabilities: [] } as unknown as Capabilities;
    const api = createLocalExtensionHostApi({
      getState: () => state,
      getCapabilities: () => capabilities,
      subscribeState: vi.fn(),
      dispatchCommand: vi.fn(),
      setKeyboardScope: vi.fn(),
      register: vi.fn(),
    });

    expect(api.version).toBe(LOCAL_EXTENSION_HOST_API_VERSION);
    expect(api.version).toBe(2);
    expect(api.getState()).toBe(state);
    expect(api.getCapabilities()).toBe(capabilities);
  });

  it('subscribes to radio state updates', () => {
    let subscriber: RadioStateSubscriber | null = null;
    const unsubscribe = vi.fn();
    const api = createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: (handler) => {
        subscriber = handler;
        return unsubscribe;
      },
      dispatchCommand: vi.fn(),
      setKeyboardScope: vi.fn(),
      register: vi.fn(),
    });
    const received: Array<ServerState | null> = [];

    const stop = api.subscribeState((state) => received.push(state));
    expect(subscriber).not.toBeNull();
    const emit = subscriber as unknown as RadioStateSubscriber;
    emit({ revision: 8 } as ServerState);
    stop();

    expect(received).toEqual([{ revision: 8 }]);
    expect(unsubscribe).toHaveBeenCalledTimes(1);
  });

  it('dispatches existing radio commands through the provided command path', () => {
    const dispatchCommand = vi.fn().mockReturnValue(true);
    const params = { freq: 14_074_000 };
    const api = createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: vi.fn(),
      dispatchCommand,
      setKeyboardScope: vi.fn(),
      register: vi.fn(),
    });

    expect(api.sendCommand('set_freq', params)).toBe(true);
    expect(dispatchCommand).toHaveBeenCalledWith('set_freq', { freq: 14_074_000 });

    params.freq = 7_074_000;
    expect(dispatchCommand.mock.calls[0][1]).toEqual({ freq: 14_074_000 });
    expect(api.dispatchCommand('set_mode', { mode: 'CW' })).toBe(true);
    expect(dispatchCommand).toHaveBeenLastCalledWith('set_mode', { mode: 'CW' });

    const { dispatchCommand: unboundDispatchCommand } = api;
    expect(unboundDispatchCommand('set_filter', { filter: 2 })).toBe(true);
    expect(dispatchCommand).toHaveBeenLastCalledWith('set_filter', { filter: 2 });
  });

  it('rejects empty command names', () => {
    const dispatchCommand = vi.fn();
    const api = createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: vi.fn(),
      dispatchCommand,
      setKeyboardScope: vi.fn(),
      register: vi.fn(),
    });

    expect(api.sendCommand('')).toBe(false);
    expect(dispatchCommand).not.toHaveBeenCalled();
  });

  it('sets and clears the extension keyboard scope', () => {
    const api = createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: vi.fn(),
      dispatchCommand: vi.fn(),
      setKeyboardScope: setLocalExtensionKeyboardScope,
      register: vi.fn(),
    });

    api.setKeyboardScope('meter-input');
    expect(getLocalExtensionKeyboardScope()).toBe('meter-input');

    api.setKeyboardScope(null);
    expect(getLocalExtensionKeyboardScope()).toBeNull();
  });

  it('registers extension renderers through the provided callback', () => {
    const register = vi.fn();
    const extension = {
      id: 'meter',
      render: vi.fn(),
    };
    const api = createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: vi.fn(),
      dispatchCommand: vi.fn(),
      setKeyboardScope: vi.fn(),
      register,
    });

    api.register(extension);

    expect(register).toHaveBeenCalledWith(extension);
  });
});

describe('installLocalExtensionHostApi', () => {
  function makeApi(): LocalExtensionHostApiV2 {
    return createLocalExtensionHostApi({
      getState: () => null,
      getCapabilities: () => null,
      subscribeState: vi.fn(),
      dispatchCommand: vi.fn().mockReturnValue(true),
      setKeyboardScope: vi.fn(),
      register: vi.fn(),
    });
  }

  it('exposes the api as both window.rigplaneExtensionHost and window.icomLanExtensionHost', () => {
    const api = makeApi();
    const fakeWindow = {} as LocalExtensionHostWindow;

    const uninstall = installLocalExtensionHostApi(fakeWindow, api);

    expect(fakeWindow.rigplaneExtensionHost).toBe(api);
    expect(fakeWindow.icomLanExtensionHost).toBe(api);
    expect(fakeWindow.rigplaneExtensionHost).toBe(fakeWindow.icomLanExtensionHost);
    expect(fakeWindow.icomLanExtensionHost?.version).toBe(2);

    uninstall();
  });

  it('clears both globals on uninstall when the api still matches', () => {
    const api = makeApi();
    const fakeWindow = {} as LocalExtensionHostWindow;

    const uninstall = installLocalExtensionHostApi(fakeWindow, api);
    uninstall();

    expect(fakeWindow.rigplaneExtensionHost).toBeUndefined();
    expect(fakeWindow.icomLanExtensionHost).toBeUndefined();
  });

  it('does not clear globals that have been re-bound to a different api', () => {
    const api1 = makeApi();
    const api2 = makeApi();
    const fakeWindow = {} as LocalExtensionHostWindow;

    const uninstall1 = installLocalExtensionHostApi(fakeWindow, api1);
    // Simulate a second installation that swaps the live api (HMR / reinit).
    fakeWindow.rigplaneExtensionHost = api2;
    fakeWindow.icomLanExtensionHost = api2;
    uninstall1();

    // The first uninstall must not blow away the live api2.
    expect(fakeWindow.rigplaneExtensionHost).toBe(api2);
    expect(fakeWindow.icomLanExtensionHost).toBe(api2);
  });
});

// MOR-1409 A08: the default extension dispatch is catalog-validated facade
// delegation. This block runs against the real intent facade and command
// lifecycle store (no module mocks):
// a facade dispatch is observable as a lifecycle record, while a raw
// transport bypass would leave no record.
describe('createDefaultLocalExtensionHostApi (MOR-1409 A08)', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('WebSocket', MockWebSocket);
    instances.length = 0;
    setRadioHealth(null);
    setRadioReady(false);
  });

  afterEach(() => {
    disconnect();
    resetCommandLifecycle();
    setRadioReady(false);
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it('reports offline non-idempotent refusal with a lifecycle record', () => {
    const api = createDefaultLocalExtensionHostApi();
    const before = getCommandLifecycles().length;

    expect(api.dispatchCommand('vfo_swap')).toBe(false);

    const records = getCommandLifecycles();
    expect(records).toHaveLength(before + 1);
    expect(records.at(-1)).toMatchObject({ name: 'vfo_swap', params: {}, status: 'failed' });
  });

  it('returns false with pending lifecycle for a queued offline command that drains on connection', () => {
    const api = createDefaultLocalExtensionHostApi();
    expect(api.sendCommand('set_freq', { freq: 14_074_000, receiver: 0 })).toBe(false);
    expect(getCommandLifecycles().at(-1)).toMatchObject({
      name: 'set_freq', params: { freq: 14_074_000, receiver: 0 }, status: 'pending',
    });
    const queued = getCommandLifecycles().at(-1)!;
    connect();
    const socket = instances[0];
    expect(socket.sent).toHaveLength(0);
    socket.simulateOpen();
    const commands = socket.sent.map((value) => JSON.parse(value)).filter((value) => value.type === 'cmd');
    expect(commands).toEqual([{
      type: 'cmd', name: 'set_freq', params: { freq: 14_074_000, receiver: 0 }, id: queued.id,
    }]);
    expect(getCommandLifecycles().at(-1)?.status).toBe('pending');
  });

  it.each(['sendCommand', 'dispatchCommand'] as const)('returns true for %s socket submission without admission or completion', (method) => {
    const api = createDefaultLocalExtensionHostApi();
    connect();
    const socket = instances[0];
    socket.simulateOpen();
    setRadioReady(true);
    socket.sent.length = 0;
    expect(api[method]('set_mode', { mode: 'CW', receiver: 1 })).toBe(true);
    const record = getCommandLifecycles().at(-1)!;
    expect(socket.sent.map((value) => JSON.parse(value))).toEqual([{
      type: 'cmd', name: 'set_mode', params: { mode: 'CW', receiver: 1 }, id: record.id,
    }]);
    expect(record.status).toBe('pending');
  });

  it('clones params so later caller mutation cannot alter the dispatched intent', () => {
    const api = createDefaultLocalExtensionHostApi();
    const params: Record<string, unknown> = { band: 20 };

    expect(api.sendCommand('set_band', params)).toBe(false);
    params.band = 40;

    expect(getCommandLifecycles().at(-1)).toMatchObject({
      name: 'set_band',
      params: { band: 20 },
    });
  });

  it('fails closed with false for unknown, PTT, and malformed commands', () => {
    const api = createDefaultLocalExtensionHostApi();
    const before = getCommandLifecycles().length;

    // Unknown command names are rejected by the catalog.
    expect(api.dispatchCommand('definitely_not_a_command')).toBe(false);
    // PTT is not in the intent catalog — extensions cannot key the radio.
    expect(api.dispatchCommand('ptt')).toBe(false);
    expect(api.dispatchCommand('ptt_on')).toBe(false);
    expect(api.dispatchCommand('ptt_off')).toBe(false);
    // Malformed params fail validation before any transport is reached.
    expect(api.sendCommand('set_band', { band: 'not-a-number' })).toBe(false);
    expect(api.sendCommand('set_band', { band: 20, extra: true })).toBe(false);
    expect(api.sendCommand('set_band')).toBe(false);

    // Fail-closed dispatches never create a lifecycle record.
    expect(getCommandLifecycles()).toHaveLength(before);
  });
});
