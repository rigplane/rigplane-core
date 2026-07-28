import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';

const channel = vi.hoisted(() => {
  const handlers = new Set<(data: ArrayBuffer) => void>();
  return {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      handlers.add(handler);
      return () => { handlers.delete(handler); };
    }),
    handlerCount: () => handlers.size,
  };
});

vi.mock('$lib/transport/ws-client', () => ({
  getChannel: () => channel,
  sendCommand: vi.fn(() => true),
  connect: vi.fn(),
  sendRaw: vi.fn(),
  disconnectAll: vi.fn(),
  reconnectAll: vi.fn(),
}));

import AmberScope from '../AmberScope.svelte';
import AmberCockpit from '../AmberCockpit.svelte';
import { presentationResources, runtime } from '$lib/runtime/frontend-runtime';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';

const variants = [
  ['AmberCockpit', AmberCockpit],
  ['AmberScope', AmberScope],
] as const;
let revision = 0;
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  disconnect() {}
});

function capabilities(audioFftAvailable: boolean) {
  return {
    model: 'test', capabilities: [], receivers: 1, vfoScheme: 'single',
    scope: false, audio: true, audioFftAvailable,
    scopeSource: audioFftAvailable ? 'audio_fft' : null,
    tx: false, modes: ['USB'], filters: ['FIL1'],
  } as never;
}

function radioState() {
  revision += 1;
  const receiver = {
    freqHz: 14_074_000 + revision, mode: 'USB', filter: 1, filterWidth: 2400,
    dataMode: 0, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 128, rfGain: 255, squelch: 0, agc: 2,
  };
  return {
    revision, stateRevision: revision, active: 'MAIN',
    main: receiver, sub: { ...receiver },
  } as never;
}

function makeChannel() {
  const handlers = new Set<(data: ArrayBuffer) => void>();
  return {
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      handlers.add(handler);
      return () => { handlers.delete(handler); };
    }),
    handlerCount: () => handlers.size,
  };
}

describe('LCD audio-FFT demand ownership', () => {
  it('keeps each mounted LCD lease stable across unrelated state updates', async () => {
    const acquire = vi.spyOn(presentationResources, 'acquire');
    const release = vi.spyOn(presentationResources, 'release');
    const register = vi.spyOn(runtime.scope, 'registerPresentationDriver');
    const subscribe = vi.spyOn(runtime.scope, 'subscribe');

    for (const [name, Component] of variants) {
      setCapabilities(capabilities(true));
      setRadioState(radioState());
      const target = document.createElement('div');
      document.body.appendChild(target);
      const component = mount(Component, { target });
      flushSync();

      await vi.waitFor(() => expect(channel.connect).toHaveBeenCalledTimes(1));
      expect(acquire).toHaveBeenCalledExactlyOnceWith('audio-fft', name);
      expect(register).toHaveBeenCalledTimes(1);
      expect(subscribe).toHaveBeenCalledTimes(1);

      for (let update = 0; update < 5; update += 1) {
        setRadioState(radioState());
        flushSync();
      }
      await Promise.resolve();
      expect(acquire).toHaveBeenCalledTimes(1);
      expect(release).not.toHaveBeenCalled();
      expect(channel.connect).toHaveBeenCalledTimes(1);
      expect(channel.disconnect).not.toHaveBeenCalled();

      setCapabilities(capabilities(false));
      flushSync();
      await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
      expect(release).toHaveBeenCalledTimes(1);

      setCapabilities(capabilities(true));
      flushSync();
      await vi.waitFor(() => expect(channel.connect).toHaveBeenCalledTimes(2));
      expect(acquire).toHaveBeenCalledTimes(2);

      unmount(component);
      await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(2));
      expect(release).toHaveBeenCalledTimes(2);
      expect(channel.handlerCount()).toBe(0);

      target.remove();
      resetRadioState();
      acquire.mockClear();
      release.mockClear();
      register.mockClear();
      subscribe.mockClear();
      vi.clearAllMocks();
    }
  });

  it('shares one channel through panel removal, final release, and duplicate cleanup', async () => {
    const localChannel = makeChannel();
    const controller = new ScopeController(() => localChannel as never);
    const host = new PresentationResourceHost<unknown>('test');
    controller.registerPresentationDriver(host);
    const panel = host.acquire('audio-fft', 'AudioSpectrumPanel');
    const scope = host.acquire('audio-fft', 'AmberScope');
    const cockpit = host.acquire('audio-fft', 'AmberCockpit');
    const unsubscribeScope = controller.subscribe(vi.fn());
    const unsubscribeCockpit = controller.subscribe(vi.fn());

    expect(new Set([panel, scope, cockpit]).size).toBe(3);
    await vi.waitFor(() => expect(localChannel.connect).toHaveBeenCalledTimes(1));
    expect(host.release(panel)).toBe(true);
    expect(localChannel.disconnect).not.toHaveBeenCalled();
    unsubscribeScope();
    expect(host.release(scope)).toBe(true);
    expect(localChannel.disconnect).not.toHaveBeenCalled();
    unsubscribeCockpit();
    expect(host.release(cockpit)).toBe(true);
    await vi.waitFor(() => expect(localChannel.disconnect).toHaveBeenCalledTimes(1));
    expect(localChannel.handlerCount()).toBe(0);
    expect(host.release(cockpit)).toBe(false);
    expect(localChannel.disconnect).toHaveBeenCalledTimes(1);
  });

  it('opens nothing when unavailable and contains no hardware fallback', async () => {
    const localChannel = makeChannel();
    const controller = new ScopeController(() => localChannel as never);
    const host = new PresentationResourceHost<unknown>('test');
    host.configure('audio-fft', {
      available: false, selected: true, driver: controller.audioFftDriver,
    });
    const lease = host.acquire('audio-fft', 'AmberScope');
    await Promise.resolve();
    expect(localChannel.connect).not.toHaveBeenCalled();
    expect(host.release(lease)).toBe(true);
    expect(localChannel.disconnect).not.toHaveBeenCalled();
    for (const [name] of variants) {
      const source = readFileSync(
        resolve(`src/components-v2/panels/lcd/${name}.svelte`), 'utf8',
      );
      expect(source).not.toMatch(/\$lib\/transport|hardware-scope/);
    }
  });
});
