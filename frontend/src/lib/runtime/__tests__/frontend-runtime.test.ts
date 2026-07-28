/**
 * Unit tests for FrontendRuntime.bootstrap().
 *
 * Uses vi.mock to stub transport + store modules.  This file lives in the
 * `isolated` vitest project (see vite.config.ts) so its mocks don't leak
 * into the shared module cache used by the `fast` project.
 */

import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach } from 'vitest';

// ── Mock transport and store modules before importing the runtime ──

vi.mock('$lib/transport/http-client', () => ({
  fetchCapabilities: vi.fn(),
  startPolling: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  connect: vi.fn(),
  sendRaw: vi.fn(),
  sendCommand: vi.fn(),
  disconnect: vi.fn(),
  disconnectAll: vi.fn(),
  reconnectAll: vi.fn(),
  isConnected: vi.fn(() => false),
  onMessage: vi.fn(() => () => {}),
  addMessageHandler: vi.fn(() => () => {}),
  getChannel: vi.fn(),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  setCapabilities: vi.fn(),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getRadioState: vi.fn(() => null),
  setRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  resetRadioState: vi.fn(),
}));

vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'disconnected'),
  isConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => ''),
  getRadioPowerOn: vi.fn(() => null),
  setHttpConnected: vi.fn(),
  setWsConnected: vi.fn(),
  setRadioStatus: vi.fn(),
  setReconnecting: vi.fn(),
  setRadioPowerOn: vi.fn(),
  setRigConnected: vi.fn(),
  setRadioReady: vi.fn(),
  setControlConnected: vi.fn(),
  markStateUpdated: vi.fn(),
}));

vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({})),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  toggleMute: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    rxEnabled: false,
    startRx: vi.fn(),
    stopRx: vi.fn(),
    startTx: vi.fn(),
    stopTx: vi.fn(),
    setRxVolume: vi.fn(),
    destroy: vi.fn(),
  },
}));

vi.mock('$lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));

vi.mock('../adapters/mod-input-auto.svelte', () => ({
  clearLegacyPendingModInputRestore: vi.fn(),
}));

// system-controller uses imports from above mocks — provide a lightweight stub
vi.mock('./system-controller', async () => {
  const { systemController: _sc } = await vi.importActual<typeof import('../system-controller')>(
    '../system-controller',
  );
  return { systemController: _sc };
});

// ── Import modules under test after mocks are hoisted ──

import { fetchCapabilities, startPolling } from '$lib/transport/http-client';
import { connect, getChannel, onMessage, sendRaw } from '$lib/transport/ws-client';
import { setCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioState } from '$lib/stores/radio.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { systemController } from '../system-controller';
import { clearLegacyPendingModInputRestore } from '../adapters/mod-input-auto.svelte';
import { PresentationResourceHost } from '../resource-host';
import { presentationResources } from '../frontend-runtime';
import { makeRxAudioHandlers } from '../commands/panel-commands';

// FrontendRuntime is a singleton — re-import fresh each time via a factory helper
// so we can reset _bootstrapCleanup and _bootstrapInFlight between tests.
async function freshRuntime() {
  // Dynamic import after vi.mock registrations ensures mocks are active.
  const mod = await import('../frontend-runtime');
  // Reset private state between tests by casting to access both sentinels
  const rt = mod.runtime as unknown as {
    _bootstrapCleanup: null;
    _bootstrapInFlight: null;
    _rxAudioLease: null;
    _ended: boolean;
    _dxSubscribers: Map<number, unknown>;
    _dxControlUnsubscribe: (() => void) | null;
  };
  rt._dxControlUnsubscribe?.();
  rt._dxSubscribers?.clear();
  rt._bootstrapCleanup = null;
  rt._bootstrapInFlight = null;
  rt._rxAudioLease = null;
  rt._ended = false;
  rt._dxControlUnsubscribe = null;
  return mod.runtime;
}

// ── Fixtures ──

const fakeCaps = {
  modes: ['USB', 'LSB'],
  scope: true,
  audio: true,
  capabilities: ['audio'],
  receivers: 1,
  vfoScheme: 'ab',
  scopeSource: 'audio_fft',
  audioFftAvailable: true,
} as any;
const fakeStopPolling = vi.fn();
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
describe('PresentationResourceHost', () => {
  it('is inert until first demand, shares the handle, and stops on last release', async () => {
    const handle = {};
    const driver = { start: vi.fn(async () => handle), stop: vi.fn(async () => {}) };
    const host = new PresentationResourceHost<object>('session');
    host.configure('audio-fft', { available: true, selected: true, driver });
    expect(driver.start).not.toHaveBeenCalled();
    const first = host.acquire('audio-fft', 'first');
    const shared = host.acquire('audio-fft', 'shared');
    await settle();
    expect(driver.start).toHaveBeenCalledTimes(1);
    expect(host.snapshot('audio-fft').activeHandle).toBe(handle);
    host.release(first);
    expect(driver.stop).not.toHaveBeenCalled();
    host.release(shared);
    await settle();
    expect(driver.stop).toHaveBeenCalledWith(handle);
  });
  it('publishes rejected stops and reclaims completed bindings', async () => {
    const host = new PresentationResourceHost<object>('session');
    let id = 0;
    const driver = { start: vi.fn(async () => ({ id: ++id })), stop: vi.fn(async (handle: { id: number }) => { if (handle.id === 1) throw new Error('stop failed'); }) };
    const health: string[] = [];
    host.subscribe((_resource, state) => { health.push(state.health); });
    host.configure('audio-fft', { available: true, selected: true, driver });
    for (let cycle = 0; cycle < 3; cycle++) {
      const lease = host.acquire('audio-fft', String(cycle));
      await settle();
      host.release(lease);
      await settle();
      expect.soft(health.at(-1)).toBe(host.snapshot('audio-fft').health);
    }
    expect.soft(driver.stop.mock.calls.map(([handle]) => handle.id)).toEqual([1, 2, 3]);
    expect.soft((host as unknown as { bindings: object[] }).bindings).toHaveLength(0);
  });
  it('disposes only a late abandoned handle and survives A→B→A completions', async () => {
    const startA = deferred<object>(), startB = deferred<object>(), startFinal = deferred<object>();
    const stopB = deferred<void>();
    const driver = {
      start: vi.fn().mockImplementationOnce(() => startA.promise)
        .mockImplementationOnce(() => startB.promise).mockImplementationOnce(() => startFinal.promise),
      stop: vi.fn((handle: { id?: string }) => handle.id === 'B' ? stopB.promise : Promise.resolve()),
      dispose: vi.fn(async () => {}),
    };
    const host = new PresentationResourceHost<object>('session');
    const listener = vi.fn();
    host.subscribe(listener);
    host.configure('hardware-scope', { available: true, selected: true, driver });
    const leaseA = host.acquire('hardware-scope', 'A');
    host.release(leaseA);
    const leaseB = host.acquire('hardware-scope', 'B');
    const a = { id: 'stale-A' }, b = { id: 'B' }, currentA = { id: 'current-A' };
    startB.resolve(b); await settle();
    startA.resolve(a); await settle();
    expect(driver.dispose).toHaveBeenCalledWith(a);
    host.release(leaseB);
    host.acquire('hardware-scope', 'A-again');
    startFinal.resolve(currentA); await settle();
    stopB.resolve(); await settle();
    expect(host.snapshot('hardware-scope').activeHandle).toBe(currentA);
    expect(driver.stop).toHaveBeenCalledWith(b);
    const publications = listener.mock.calls.length;
    await host.teardown(); await host.teardown();
    expect(() => host.acquire('hardware-scope', 'late')).toThrow('torn down');
    expect(listener).toHaveBeenCalledTimes(publications);
    expect(driver.stop).toHaveBeenCalledWith(currentA);
    expect(driver.stop).toHaveBeenCalledTimes(2);
  });
  it('keeps unavailable and failed selections honest and requires retry', async () => {
    const handle = {};
    const driver = { start: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue(handle), stop: vi.fn(async () => {}) };
    const host = new PresentationResourceHost<object>('session');
    host.configure('rx-audio', { available: false, selected: true, driver });
    host.acquire('rx-audio', 'panel');
    expect(driver.start).not.toHaveBeenCalled();
    host.configure('rx-audio', { available: true, selected: true, driver });
    await settle();
    expect(host.snapshot('rx-audio')).toMatchObject({ selected: true, health: 'failed' });
    expect(driver.start).toHaveBeenCalledTimes(1);
    host.retry('rx-audio');
    await settle();
    expect(driver.start).toHaveBeenCalledTimes(2);
  });
});
// ── Tests ──

describe('FrontendRuntime.bootstrap()', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    (startPolling as ReturnType<typeof vi.fn>).mockReturnValue(fakeStopPolling);
  });

  it('runs the full bootstrap sequence on the first call', async () => {
    const rt = await freshRuntime();

    const cleanup = await rt.bootstrap();

    // 1. fetchCapabilities called
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(clearLegacyPendingModInputRestore).toHaveBeenCalledTimes(1);

    // 2. capabilities pushed into store
    expect(setCapabilities).toHaveBeenCalledWith(fakeCaps);

    // 3. polling started once (initial startPolling call; registerPolling registers
    //    a factory that calls startPolling only when systemController.connect() fires)
    expect(startPolling).toHaveBeenCalledTimes(1);
    expect(startPolling).toHaveBeenCalledWith(expect.any(Function), 1000);

    // 4. WebSocket connected
    expect(connect).toHaveBeenCalledWith('/api/v1/ws');

    // 5. subscribe message sent
    expect(sendRaw).toHaveBeenCalledWith({ type: 'subscribe', streams: ['events'] });

    // returns a callable cleanup
    expect(typeof cleanup).toBe('function');
  });

  it('is idempotent — second call is a no-op and returns the same cleanup', async () => {
    const rt = await freshRuntime();

    const cleanup1 = await rt.bootstrap();
    const cleanup2 = await rt.bootstrap();

    // Transport functions only invoked once total
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both calls return the same handle
    expect(cleanup1).toBe(cleanup2);
  });

  it('cleanup function stops polling', async () => {
    const order: string[] = [];
    const teardown = vi.spyOn(presentationResources, 'teardown')
      .mockImplementation(async () => { order.push('resources'); });
    fakeStopPolling.mockImplementation(() => { order.push('control'); });
    const rt = await freshRuntime();
    const cleanup = await rt.bootstrap();
    await cleanup();
    await cleanup();
    expect(fakeStopPolling).toHaveBeenCalledTimes(1);
    expect(teardown).toHaveBeenCalledTimes(1);
    expect(order).toEqual(['resources', 'control']);
    teardown.mockRestore();
  });

  it('propagates fetchCapabilities error and allows retry', async () => {
    const rt = await freshRuntime();
    const error = new Error('network failure');
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockRejectedValueOnce(error);

    await expect(rt.bootstrap()).rejects.toThrow('network failure');

    // connect and sendRaw must NOT have been called
    expect(connect).not.toHaveBeenCalled();
    expect(sendRaw).not.toHaveBeenCalled();

    // Runtime is not latched — retry should work
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    const cleanup = await rt.bootstrap();
    expect(typeof cleanup).toBe('function');
    expect(fetchCapabilities).toHaveBeenCalledTimes(2);
  });

  it('startPolling callback calls setRadioState with the received state', async () => {
    const rt = await freshRuntime();
    await rt.bootstrap();

    // Capture the callback passed to the (single) startPolling call
    const calls = (startPolling as ReturnType<typeof vi.fn>).mock.calls;
    const pollCallback = calls[0][0] as (s: unknown) => void;

    const fakeState = { revision: 1 } as any;
    pollCallback(fakeState);

    expect(setRadioState).toHaveBeenCalledWith(fakeState);
  });

  it('registers polling factory with systemController', async () => {
    const registerSpy = vi.spyOn(systemController, 'registerPolling');
    const rt = await freshRuntime();
    await rt.bootstrap();

    expect(registerSpy).toHaveBeenCalledTimes(1);
    expect(registerSpy).toHaveBeenCalledWith(expect.any(Function));
  });

  it('serializes concurrent callers — both share single in-flight bootstrap', async () => {
    const rt = await freshRuntime();

    // Invoke bootstrap concurrently (not sequentially)
    const [cleanup1, cleanup2] = await Promise.all([rt.bootstrap(), rt.bootstrap()]);

    // Each transport function called exactly once, not twice
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(startPolling).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both callers get the same cleanup function
    expect(cleanup1).toBe(cleanup2);
    expect(cleanup1).not.toBe(fakeStopPolling);
  });
});

describe('FrontendRuntime hardware scope and DX facades', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    (startPolling as ReturnType<typeof vi.fn>).mockReturnValue(fakeStopPolling);
  });

  it('derives unavailable/default selection and keeps bootstrap inert', async () => {
    const rt = await freshRuntime();
    await rt.bootstrap();

    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: false,
      selected: false,
      demand: 0,
      health: 'inactive',
    });
    expect(getChannel).not.toHaveBeenCalled();
  });

  it('starts only for exact shared demand and stops on the last valid release', async () => {
    const channel = {
      state: 'disconnected',
      connect: vi.fn(),
      disconnect: vi.fn(),
      onBinary: vi.fn(() => vi.fn()),
      onStateChange: vi.fn(() => vi.fn()),
    };
    (getChannel as ReturnType<typeof vi.fn>).mockReturnValue(channel);
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...fakeCaps,
      scope: true,
      capabilities: ['audio', 'scope'],
      scopeSource: 'hardware',
    });
    const rt = await freshRuntime();
    await rt.bootstrap();

    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: true,
      selected: true,
      demand: 0,
    });
    expect(getChannel).not.toHaveBeenCalled();

    const first = rt.acquireHardwareScope('first');
    const shared = rt.acquireHardwareScope('shared');
    await settle();
    expect(getChannel).toHaveBeenCalledExactlyOnceWith('scope');
    expect(channel.connect).toHaveBeenCalledExactlyOnceWith('/api/v1/scope');
    expect(presentationResources.snapshot('hardware-scope').demand).toBe(2);

    expect(rt.releaseHardwareScope(first)).toBe(true);
    expect(rt.releaseHardwareScope(first)).toBe(false);
    expect(channel.disconnect).not.toHaveBeenCalled();
    expect(rt.releaseHardwareScope(shared)).toBe(true);
    await settle();
    expect(channel.disconnect).toHaveBeenCalledTimes(1);
  });

  it('shares one filtered control subscription and unsubscribes exactly', async () => {
    let controlHandler: ((message: unknown) => void) | undefined;
    const controlUnsubscribe = vi.fn();
    (onMessage as ReturnType<typeof vi.fn>).mockImplementation((handler) => {
      controlHandler = handler;
      return controlUnsubscribe;
    });
    const rt = await freshRuntime();
    const first = vi.fn();
    const second = vi.fn();
    const unsubscribeFirst = rt.subscribeDx(first);
    const unsubscribeSecond = rt.subscribeDx(second);

    expect(onMessage).toHaveBeenCalledTimes(1);
    controlHandler?.({ type: 'state', data: {} });
    controlHandler?.({ type: 'dx_spot', spot: { call: 'K1ABC' } });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(1);

    unsubscribeFirst();
    unsubscribeFirst();
    controlHandler?.({ type: 'dx_spots', spots: [] });
    expect(first).toHaveBeenCalledTimes(1);
    expect(second).toHaveBeenCalledTimes(2);
    expect(controlUnsubscribe).not.toHaveBeenCalled();
    unsubscribeSecond();
    expect(controlUnsubscribe).toHaveBeenCalledTimes(1);
  });
});

describe('FrontendRuntime RX LIVE intent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue(fakeCaps);
    (startPolling as ReturnType<typeof vi.fn>).mockReturnValue(fakeStopPolling);
    presentationResources.retry('rx-audio');
  });

  it('keeps panel commands behind the runtime authority', () => {
    const source = readFileSync('src/lib/runtime/commands/panel-commands.ts', 'utf8');
    expect(source).not.toMatch(/audioManager\.(startRx|stopRx)\(/);
  });

  it('shares one LIVE lease and ignores duplicate and late exits', async () => {
    const rt = await freshRuntime();
    await rt.bootstrap();
    const panelA = makeRxAudioHandlers();
    const panelB = makeRxAudioHandlers();

    panelA.onMonitorModeChange('live');
    panelA.onMonitorModeChange('live');
    panelB.onMonitorModeChange('live');
    await settle();

    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 1,
      health: 'streaming',
      activeHandle: audioManager,
    });

    panelB.onMonitorModeChange('radio');
    panelB.onMonitorModeChange('radio');
    panelA.onMonitorModeChange('radio');
    await settle();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(audioManager.stopRx).toHaveBeenCalledTimes(1);
    expect(presentationResources.snapshot('rx-audio').demand).toBe(0);

    panelA.onMonitorModeChange('mute');
    await settle();
    expect(presentationResources.snapshot('rx-audio').demand).toBe(0);
    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(audioManager.stopRx).toHaveBeenCalledTimes(1);
  });

  it('does not start while unavailable or implicitly retry a rejected start', async () => {
    const unavailable = await freshRuntime();
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ...fakeCaps,
      audio: false,
      capabilities: [],
    });
    await unavailable.bootstrap();
    unavailable.setRxLive(true);
    await settle();
    expect(audioManager.startRx).not.toHaveBeenCalled();
    unavailable.setRxLive(false);

    const failed = await freshRuntime();
    await failed.bootstrap();
    (audioManager.startRx as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new Error('offline');
    });
    failed.setRxLive(true);
    await settle();
    failed.setRxLive(true);
    await settle();

    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 1,
      health: 'failed',
      activeHandle: undefined,
    });
    failed.setRxLive(false);
  });

  it('tears down once, in order, and never restarts after final cleanup', async () => {
    const channel = {
      state: 'disconnected',
      connect: vi.fn(),
      disconnect: vi.fn(),
      onBinary: vi.fn(() => vi.fn()),
      onStateChange: vi.fn(() => vi.fn()),
    };
    (getChannel as ReturnType<typeof vi.fn>).mockReturnValue(channel);
    (fetchCapabilities as ReturnType<typeof vi.fn>).mockResolvedValue({
      ...fakeCaps,
      scope: true,
      capabilities: ['audio', 'scope'],
      scopeSource: 'hardware',
    });
    const unsubscribeDx = vi.fn();
    (onMessage as ReturnType<typeof vi.fn>).mockReturnValue(unsubscribeDx);
    const rt = await freshRuntime();
    await rt.bootstrap();
    rt.setRxLive(true);
    const hardwareLease = rt.acquireHardwareScope('SpectrumPanel');
    rt.subscribeDx(vi.fn());
    await settle();

    const cleanup = await rt.bootstrap();
    await cleanup();
    await cleanup();
    rt.setRxLive(true);
    await settle();
    await new Promise((resolve) => setTimeout(resolve, 10));

    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(audioManager.stopRx).toHaveBeenCalledTimes(1);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);
    expect(unsubscribeDx).toHaveBeenCalledTimes(1);
    expect(rt.releaseHardwareScope(hardwareLease)).toBe(false);
    expect(() => rt.acquireHardwareScope('late')).toThrow('torn down');
    expect(() => rt.subscribeDx(vi.fn())).toThrow('torn down');
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 0,
      health: 'inactive',
      activeHandle: undefined,
    });
  });
});
