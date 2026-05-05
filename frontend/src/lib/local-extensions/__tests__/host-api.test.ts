import { afterEach, describe, expect, it, vi } from 'vitest';

import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import {
  createLocalExtensionHostApi,
  installLocalExtensionHostApi,
  LOCAL_EXTENSION_HOST_API_VERSION,
  type LocalExtensionHostApiV1,
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
  function makeApi(): LocalExtensionHostApiV1 {
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
    // Same instance under both names — Pro extensions written against v1.x
    // see exactly the same object as new code reading the v2.x global.
    expect(fakeWindow.rigplaneExtensionHost).toBe(fakeWindow.icomLanExtensionHost);

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
