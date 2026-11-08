import { describe, expect, it, vi } from 'vitest';
import { createLocalExtensionHostApi } from '../host-api';
import { bindHostUi, getHostUi, publishHostSnapshot, subscribeHostSnapshot, parseHostDeclaration, getLayoutOptions, publishListening, getListening } from '../host-ui';
import { runtime } from '$lib/runtime/frontend-runtime';
import { setRxEnabled, setVolume, getAudioState } from '$lib/stores/audio.svelte';
import { mount, unmount, flushSync } from 'svelte';
import LocalExtensionsHost from '../LocalExtensionsHost.svelte';
import { subscribeListening } from '../host-ui';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { setWsConnected, setRadioPowerOn } from '$lib/stores/connection.svelte';
import { readListeningSnapshot } from '../host-listening';

describe('local extension host v3', () => {
  it('reads current gating synchronously and routes listening mode through the UI runtime', () => {
    const api = createLocalExtensionHostApi({ getState: () => null,
      getCapabilities: () => ({ capabilities: ['audio'] }) as never,
      subscribeState: vi.fn(), dispatchCommand: vi.fn(), setKeyboardScope: vi.fn(), register: vi.fn() });
    setCapabilities({ capabilities: ['audio'], stateContractVersion: 1, providerGeneration: 1 } as never);
    setWsConnected(true);
    setRadioPowerOn(true);
    const live = vi.spyOn(runtime, 'setRxLive').mockImplementation(() => {});
    expect(api.setListeningMode('live')).toBe(true);
    expect(live).toHaveBeenCalledWith(true);
    expect(api.setListeningMode('local')).toBe(true);
    expect(live).toHaveBeenLastCalledWith(false);
    setRadioPowerOn(false);
    expect(api.setListeningMode('live')).toBe(false);
    setRadioPowerOn(true);
    setWsConnected(false);
    expect(api.setListeningMode('live')).toBe(false);
    expect(readListeningSnapshot().enabled).toBe(false);
    live.mockRestore();
    setRadioPowerOn(null);
    clearCapabilities();
  });
  it('publishes listening changes made by the UI store', async () => {
    const enabled = vi.spyOn(runtime, 'rxEnabled', 'get').mockReturnValue(true);
    setCapabilities({ capabilities: ['audio'], stateContractVersion: 1, providerGeneration: 1 } as never);
    const target = document.createElement('div');
    const instance = mount(LocalExtensionsHost, { target });
    const handler = vi.fn();
    const stop = subscribeListening(handler);
    flushSync();
    // Browser monitoring is a UI store input, independent from API writes.
    setRxEnabled(true);
    setVolume(35);
    flushSync();
    expect(handler.mock.lastCall?.[0]).toMatchObject({ mode: 'live', target: 'browser', level: 0.35 });
    stop();
    await unmount(instance);
    setRxEnabled(false);
    setVolume(100);
    clearCapabilities();
    enabled.mockRestore();
  });
  it('routes browser listening gain through the same audio handlers as the UI and rejects invalid levels', () => {
    const api = createLocalExtensionHostApi({ getListening, getState: () => null,
      getCapabilities: () => ({ capabilities: ['audio'] }) as never,
      subscribeState: vi.fn(), dispatchCommand: vi.fn(), setKeyboardScope: vi.fn(), register: vi.fn() });
    publishListening({ mode: 'live', target: 'browser', level: 1, enabled: true, maxLevel: 2 });
    setRxEnabled(true);
    const enabled = vi.spyOn(runtime, 'rxEnabled', 'get').mockReturnValue(true);
    const volume = vi.spyOn(runtime, 'setRxVolume').mockImplementation(() => {});
    expect(api.setListeningLevel(0.42)).toBe(true);
    expect(volume).toHaveBeenCalledWith(0.42);
    expect(getAudioState().volume).toBe(42);
    expect(api.setListeningLevel(Number.NaN)).toBe(false);
    expect(api.setListeningLevel(-1)).toBe(false);
    expect(api.setListeningLevel(99)).toBe(false);
    publishListening({ mode: 'live', target: 'browser', level: 1, enabled: false, maxLevel: 2 });
    expect(api.setListeningLevel(0.8)).toBe(false);
    expect(api.setListeningMode('local')).toBe(false);
    expect(volume).toHaveBeenCalledTimes(1);
    volume.mockRestore();
    enabled.mockRestore();
    setRxEnabled(false);
    setVolume(100);
  });
  it('retains v2 operations and delegates host actions to the exact bound UI callbacks', () => {
    const connect = vi.fn(() => true);
    const disconnect = vi.fn(() => true);
    const setRadioPower = vi.fn(() => false);
    const openRadioControls = vi.fn(() => true);
    const unbind = bindHostUi({ connect, disconnect, setRadioPower, openRadioControls });
    const dispatchCommand = vi.fn(() => true);
    const api = createLocalExtensionHostApi({ getState: () => null, getCapabilities: () => null,
      subscribeState: vi.fn(), dispatchCommand, setKeyboardScope: vi.fn(), register: vi.fn() });
    expect(api.version).toBe(3);
    expect(api.sendCommand('set_freq', { freq: 123 })).toBe(true);
    expect(api.connect()).toBe(true);
    expect(api.disconnect()).toBe(true);
    expect(api.setRadioPower(true)).toBe(false);
    expect(api.openRadioControls()).toBe(true);
    expect(getHostUi()?.connect).toBe(connect);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(disconnect).toHaveBeenCalledTimes(1);
    expect(setRadioPower).toHaveBeenCalledWith(true);
    expect(openRadioControls).toHaveBeenCalledTimes(1);
    unbind();
    expect(api.connect()).toBe(false);
  });

  it('delivers UI-originated snapshots and unsubscribes', () => {
    const handler = vi.fn();
    const stop = subscribeHostSnapshot(handler);
    const initial = handler.mock.calls.length;
    publishHostSnapshot({ connected: true, power: null, powerSupported: false, layout: 'standard', theme: 'default' });
    expect(handler).toHaveBeenCalledTimes(initial + 1);
    expect(handler.mock.lastCall?.[0]).toMatchObject({ connected: true });
    stop();
    publishHostSnapshot({ connected: false, power: null, powerSupported: false, layout: 'standard', theme: 'default' });
    expect(handler).toHaveBeenCalledTimes(initial + 1);
  });

  it('accepts only the typed versioned host declaration and defaults to no supplied controls', () => {
    expect(parseHostDeclaration(null)).toEqual({ version: 1, report: false, language: false });
    expect(parseHostDeclaration({ version: 1, report: true, language: true })).toEqual({ version: 1, report: true, language: true });
    expect(parseHostDeclaration({ version: 2, report: true })).toEqual({ version: 1, report: false, language: false });
    expect(parseHostDeclaration({ version: 1, report: 'true' }).report).toBe(false);
  });

  it('hides test layouts from release options while preserving development access', () => {
    expect(getLayoutOptions(false).map(x => x.value)).not.toContain('sdr-test');
    expect(getLayoutOptions(false).map(x => x.value)).not.toContain('dual-receiver-cockpit');
    expect(getLayoutOptions(true).map(x => x.value)).toContain('sdr-test');
    expect(getLayoutOptions(false).map(x => x.value)).toContain('dual-sdr-face');
  });
});
