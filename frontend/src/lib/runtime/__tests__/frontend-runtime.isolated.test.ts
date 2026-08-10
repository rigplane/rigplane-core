/**
 * Unit tests for FrontendRuntime.bootstrap().
 *
 * Uses vi.mock to stub transport + store modules.  This file lives in the
 * `isolated` vitest project (see vite.config.ts) so its mocks don't leak
 * into the shared module cache used by the `fast` project.
 */

import { readFileSync } from 'node:fs';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { flushSync } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its test-only effect harness.
import { effect_root, render_effect } from 'svelte/internal/client';

// ── Mock transport and store modules before importing the runtime ──

vi.mock('$lib/transport/http-client', () => ({
  fetchCapabilities: vi.fn(),
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
vi.mock('$lib/runtime/commands/radio-intents', () => ({
  dispatchRadioIntent: vi.fn(() => ({ id: 'test-lifecycle', status: 'pending' })),
  isNormalizedLevel: (value: unknown) =>
    typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1,
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => null),
  setCapabilities: vi.fn(),
  subscribeCapabilities: vi.fn(),
  hasSpectrum: vi.fn(() => false),
  hasAnyScope: vi.fn(() => false),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  // `panel-commands.ts` (a frozen A09b seam) reads these directly.
  getRadioState: vi.fn(() => null),
  getActiveReceiver: vi.fn(() => null),
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
  markScopeFrame: vi.fn(),
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

import { fetchCapabilities } from '$lib/transport/http-client';
import { connect, getChannel, onMessage, sendRaw } from '$lib/transport/ws-client';
import { setCapabilities, subscribeCapabilities } from '$lib/stores/capabilities.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { clearLegacyPendingModInputRestore } from '../adapters/mod-input-auto.svelte';
import { PresentationResourceHost } from '../resource-host';
import { presentationResources } from '../frontend-runtime';
import { scopeController } from '../scope-controller.svelte';
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
    _capabilitiesUnsubscribe: (() => void) | null;
  };
  rt._dxControlUnsubscribe?.();
  rt._capabilitiesUnsubscribe?.();
  rt._dxSubscribers?.clear();
  rt._bootstrapCleanup = null;
  rt._bootstrapInFlight = null;
  rt._rxAudioLease = null;
  rt._ended = false;
  rt._dxControlUnsubscribe = null;
  rt._capabilitiesUnsubscribe = null;
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
  stateContractVersion: 1,
  providerGeneration: 0,
} as any;

function configureAcceptedCapabilities(caps: any = fakeCaps): void {
  (subscribeCapabilities as ReturnType<typeof vi.fn>).mockImplementation((listener) => {
    listener(caps);
    return vi.fn();
  });
}
const deferred = <T>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
};
const settle = async () => { await Promise.resolve(); await Promise.resolve(); };
function makeScopeChannel() {
  const binary = new Set<(data: ArrayBuffer) => void>();
  const states = new Set<(state: any) => void>();
  let state = 'disconnected';
  return {
    get state() { return state; },
    connect: vi.fn(), disconnect: vi.fn(),
    onBinary: vi.fn((handler) => { binary.add(handler); return () => binary.delete(handler); }),
    onStateChange: vi.fn((handler) => { states.add(handler); return () => states.delete(handler); }),
    setState(next: any) { state = next; for (const handler of states) handler(next); },
    frame() {
      const data = new ArrayBuffer(20), view = new DataView(data);
      view.setUint8(0, 1); view.setUint32(3, 14_100_000, true);
      view.setUint32(7, 14_200_000, true); view.setUint16(14, 4, true);
      for (const handler of binary) handler(data);
    },
  };
}
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
    configureAcceptedCapabilities();
  });

  it('waits for accepted capability-store generations before configuring resources', async () => {
    const teardown = vi.spyOn(presentationResources, 'teardown')
      .mockImplementation(async () => {});
    const capabilityUnsubscribe = vi.fn();
    (subscribeCapabilities as ReturnType<typeof vi.fn>).mockImplementation((listener) => {
      listener(null);
      return capabilityUnsubscribe;
    });
    const rt = await freshRuntime();

    const cleanup = await rt.bootstrap();

    expect(fetchCapabilities).not.toHaveBeenCalled();
    expect(setCapabilities).not.toHaveBeenCalled();
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: false, selected: false, demand: 0,
    });
    expect(presentationResources.snapshot('audio-fft')).toMatchObject({
      available: false, selected: false, demand: 0,
    });
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      available: false, demand: 0,
    });

    const listener = (subscribeCapabilities as ReturnType<typeof vi.fn>).mock.calls.at(-1)?.[0];
    expect(listener).toEqual(expect.any(Function));
    listener({
      ...fakeCaps,
      stateContractVersion: 1,
      providerGeneration: 7,
      scope: true,
      capabilities: ['audio', 'scope'],
      scopeSource: 'hardware',
    });
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: true, selected: true, demand: 0,
    });
    expect(presentationResources.snapshot('audio-fft')).toMatchObject({
      available: true, selected: true, demand: 0,
    });
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      available: true, demand: 0,
    });
    expect(audioManager.startRx).not.toHaveBeenCalled();
    expect(getChannel).not.toHaveBeenCalled();

    listener(null);
    expect(presentationResources.snapshot('hardware-scope').available).toBe(false);
    expect(presentationResources.snapshot('audio-fft').available).toBe(false);
    expect(presentationResources.snapshot('rx-audio').available).toBe(false);
    expect(audioManager.startRx).not.toHaveBeenCalled();
    expect(getChannel).not.toHaveBeenCalled();

    await cleanup();
    expect(capabilityUnsubscribe).toHaveBeenCalledTimes(1);
    teardown.mockRestore();
  });

  it('runs the full bootstrap sequence on the first call', async () => {
    const rt = await freshRuntime();

    const cleanup = await rt.bootstrap();

    // 1. Accepted capability-store listener registered; no HTTP capability writer.
    expect(subscribeCapabilities).toHaveBeenCalledTimes(1);
    expect(fetchCapabilities).not.toHaveBeenCalled();
    expect(clearLegacyPendingModInputRestore).toHaveBeenCalledTimes(1);
    expect(setCapabilities).not.toHaveBeenCalled();

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
    expect(subscribeCapabilities).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both calls return the same handle
    expect(cleanup1).toBe(cleanup2);
  });

  it('cleanup function tears down presentation resources (no polling to stop, MOR-1409 A09b)', async () => {
    const order: string[] = [];
    const teardown = vi.spyOn(presentationResources, 'teardown')
      .mockImplementation(async () => { order.push('resources'); });
    const rt = await freshRuntime();
    const cleanup = await rt.bootstrap();
    await cleanup();
    await cleanup();
    expect(teardown).toHaveBeenCalledTimes(1);
    expect(order).toEqual(['resources']);
    teardown.mockRestore();
  });

  // MOR-1060 — the App remount seam. A second App instance must get a live
  // runtime, not the cached cleanup of an instance that already tore down.
  it('re-bootstraps after cleanup so a remounted App gets a live runtime', async () => {
    const teardown = vi.spyOn(presentationResources, 'teardown')
      .mockImplementation(async () => {});
    const rt = await freshRuntime();
    const internals = rt as unknown as { _ended: boolean };

    const cleanup = await rt.bootstrap();
    await cleanup();
    expect(internals._ended).toBe(true);

    const second = await rt.bootstrap();

    expect(subscribeCapabilities).toHaveBeenCalledTimes(2);
    expect(connect).toHaveBeenCalledTimes(2);
    expect(sendRaw).toHaveBeenCalledTimes(2);
    expect(second).not.toBe(cleanup);
    // The facades are re-armed, not latched closed by the previous teardown.
    expect(internals._ended).toBe(false);
    const lease = rt.acquireHardwareScope('remount-probe');
    presentationResources.release(lease);

    // MUTATION KILLED: clearing the cache from a cleanup body that can run
    // more than once. Re-invoking the first instance's cleanup must stay a
    // no-op and must not drop the SECOND instance's registration — otherwise
    // a third bootstrap silently re-runs the transport chain on a live
    // runtime.
    await cleanup();
    expect(subscribeCapabilities).toHaveBeenCalledTimes(2);
    expect(await rt.bootstrap()).toBe(second);

    await second();
    teardown.mockRestore();
  });

  it('propagates capability-listener setup error and allows retry', async () => {
    const rt = await freshRuntime();
    const error = new Error('network failure');
    (subscribeCapabilities as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw error;
    });

    await expect(rt.bootstrap()).rejects.toThrow('network failure');

    // connect and sendRaw must NOT have been called
    expect(connect).not.toHaveBeenCalled();
    expect(sendRaw).not.toHaveBeenCalled();

    // Runtime is not latched — retry should work
    configureAcceptedCapabilities();
    const cleanup = await rt.bootstrap();
    expect(typeof cleanup).toBe('function');
    expect(subscribeCapabilities).toHaveBeenCalledTimes(2);
  });

  // MOR-1409 A13b (correction 5246842617 §5): the inert no-op stub is
  // deleted in the same head that removes `send()`'s last two production
  // callers (RadioLayout.svelte's scope-dual/scope-receiver toggles) — no
  // separate post-A13 micro-slice is needed for either deletion.
  it('no longer exposes setPollingMultiplier (MOR-1409 A13b — the inert A09b stub is deleted)', async () => {
    const rt = await freshRuntime();
    const surface = rt as unknown as Record<string, unknown>;

    expect(surface.setPollingMultiplier).toBeUndefined();
  });

  it('serializes concurrent callers — both share single in-flight bootstrap', async () => {
    const rt = await freshRuntime();

    // Invoke bootstrap concurrently (not sequentially)
    const [cleanup1, cleanup2] = await Promise.all([rt.bootstrap(), rt.bootstrap()]);

    // Each transport function called exactly once, not twice
    expect(subscribeCapabilities).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);

    // Both callers get the same cleanup function
    expect(cleanup1).toBe(cleanup2);
  });
});

describe('FrontendRuntime command dispatch and state-hatch removal (MOR-1409 A08)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    configureAcceptedCapabilities();
  });

  // MOR-1409 A13b (correction 5246842617 §5): `send()` is deleted in the
  // same head that migrates its last two production callers
  // (RadioLayout.svelte's scope-dual/scope-receiver toggles) to
  // `bindSemanticSurfaceHandlers().scopeControls`. Same surface-absence
  // idiom as the `patchActiveReceiver`/`patchState` pin below (A08).
  it('no longer exposes send() (MOR-1409 A13b — the typed-facade delegator is deleted)', async () => {
    const rt = await freshRuntime();
    const surface = rt as unknown as Record<string, unknown>;

    expect(surface.send).toBeUndefined();
  });

  it('no longer exposes the patchActiveReceiver/patchState escape hatches', async () => {
    const rt = await freshRuntime();
    const surface = rt as unknown as Record<string, unknown>;

    expect(surface.patchActiveReceiver).toBeUndefined();
    expect(surface.patchState).toBeUndefined();
  });
});

describe('FrontendRuntime hardware scope and DX facades', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    configureAcceptedCapabilities();
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

describe('FrontendRuntime canonical default scope status', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    configureAcceptedCapabilities();
  });
  it('keeps capability-denied hardware inert', async () => {
    const rt = await freshRuntime();
    await rt.bootstrap();
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: false, selected: false, demand: 0, health: 'inactive',
    });
  });
  it('keeps both sources eligible while summarizing only the hardware default', async () => {
    const hardware = makeScopeChannel(), audio = makeScopeChannel();
    (getChannel as ReturnType<typeof vi.fn>).mockImplementation(
      (name) => name === 'scope' ? hardware : audio,
    );
    configureAcceptedCapabilities({
      ...fakeCaps, capabilities: ['audio', 'scope'], scopeSource: 'hardware',
    });
    const rt = await freshRuntime();
    await rt.bootstrap();
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: true, selected: true, demand: 0,
    });
    expect(presentationResources.snapshot('audio-fft')).toMatchObject({
      available: true, selected: true, demand: 0,
    });
    expect(rt.defaultScopeStatus).toEqual({
      source: 'hardware', available: true, resourceSelected: true, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    });
    presentationResources.configure('hardware-scope', { available: false, selected: true });
    expect(rt.defaultScopeStatus).toMatchObject({ source: 'hardware', available: false, resourceSelected: true });
    presentationResources.configure('hardware-scope', { available: true, selected: true });
    const hardwareLease = rt.acquireHardwareScope('viewer');
    const hardwareShared = rt.acquireHardwareScope('shared');
    const audioLease = presentationResources.acquire('audio-fft', 'supplemental');
    expect(rt.defaultScopeStatus).toMatchObject({
      source: 'hardware', demand: 2, lifecycle: 'starting',
      transport: 'disconnected', frameSeen: false,
    });
    await settle();
    expect(presentationResources.snapshot('audio-fft')).toMatchObject({
      selected: true, demand: 1, health: 'streaming',
    });
    expect(rt.defaultScopeStatus.lifecycle).toBe('streaming');
    for (const state of ['connecting', 'connected', 'reconnecting', 'disconnected'] as const) {
      hardware.setState(state);
      expect(rt.defaultScopeStatus.transport).toBe(state);
      expect(rt.defaultScopeStatus.frameSeen).toBe(false);
    }
    hardware.setState('connected');
    hardware.frame();
    expect(rt.defaultScopeStatus.frameSeen).toBe(true);
    hardware.setState('reconnecting');
    hardware.setState('connected');
    expect(rt.defaultScopeStatus.frameSeen).toBe(false);
    rt.releaseHardwareScope(hardwareLease);
    expect(rt.defaultScopeStatus.demand).toBe(1);
    expect(hardware.disconnect).not.toHaveBeenCalled();
    rt.releaseHardwareScope(hardwareShared);
    presentationResources.release(audioLease);
    await settle();
    expect(hardware.disconnect).toHaveBeenCalledTimes(1);
  });
  it('publishes the audio default and inert facts without fallback', async () => {
    (getChannel as ReturnType<typeof vi.fn>).mockReturnValue(makeScopeChannel());
    configureAcceptedCapabilities({
      ...fakeCaps, scope: true, capabilities: ['audio', 'scope'], scopeSource: 'audio_fft',
    });
    const rt = await freshRuntime();
    await rt.bootstrap();
    expect(rt.defaultScopeStatus).toEqual({
      source: 'audio_fft', available: true, resourceSelected: true, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    });
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({ available: true, selected: true, demand: 0 });
    expect(getChannel).not.toHaveBeenCalled();
    (getChannel as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new Error('offline');
    });
    const failedLease = presentationResources.acquire('audio-fft', 'failed');
    await settle();
    expect(rt.defaultScopeStatus.lifecycle).toBe('failed');
    presentationResources.release(failedLease);
    configureAcceptedCapabilities({
      ...fakeCaps, scope: true, capabilities: ['audio', 'scope'], scopeSource: 'invalid',
      audioFftAvailable: false,
    });
    const invalid = await freshRuntime();
    await invalid.bootstrap();
    expect(invalid.defaultScopeStatus).toEqual({
      source: null, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    });
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      available: true, selected: true, demand: 0,
    });
    expect(presentationResources.snapshot('audio-fft')).toMatchObject({
      available: false, selected: false, demand: 0,
    });
    expect(getChannel).toHaveBeenCalledExactlyOnceWith('audio-scope');
  });
  it('shares, coalesces, and exactly tears down the reactive bridge', async () => {
    const hardware = makeScopeChannel();
    (getChannel as ReturnType<typeof vi.fn>).mockReturnValue(hardware);
    configureAcceptedCapabilities({
      ...fakeCaps, capabilities: ['audio', 'scope'], scopeSource: 'hardware',
    });
    const rt = await freshRuntime();
    const hostListeners = (presentationResources as any).listeners as Set<unknown>;
    const healthListeners = (scopeController as any)._healthSubscribers as Map<unknown, unknown>;
    let reads = 0;
    const observe = () => effect_root(() => {
      render_effect(() => { rt.defaultScopeStatus; reads += 1; });
    });
    expect(rt.defaultScopeStatus.demand).toBe(0);
    expect([hostListeners.size, healthListeners.size]).toEqual([0, 0]);
    const stopFirst = observe(), stopShared = observe();
    expect([hostListeners.size, healthListeners.size]).toEqual([1, 1]);
    (subscribeCapabilities as ReturnType<typeof vi.fn>).mockImplementationOnce(() => {
      throw new Error('offline');
    });
    await expect(rt.bootstrap()).rejects.toThrow('offline');
    expect([hostListeners.size, healthListeners.size]).toEqual([1, 1]);
    configureAcceptedCapabilities({
      ...fakeCaps, capabilities: ['audio', 'scope'], scopeSource: 'hardware',
    });
    await rt.bootstrap();
    const lease = rt.acquireHardwareScope('viewer');
    await settle();
    const before = reads;
    hardware.setState('connecting');
    hardware.setState('connected');
    flushSync();
    expect(reads).toBe(before + 2);
    expect(rt.defaultScopeStatus).toMatchObject({
      demand: 1, lifecycle: 'streaming', transport: 'connected', frameSeen: false,
    });
    stopFirst(); await settle();
    expect([hostListeners.size, healthListeners.size]).toEqual([1, 1]);
    stopShared(); await settle();
    expect([hostListeners.size, healthListeners.size]).toEqual([0, 0]);
    const stopRemount = observe();
    expect([hostListeners.size, healthListeners.size]).toEqual([1, 1]);
    rt.releaseHardwareScope(lease);
    stopRemount(); await settle();
    expect([hostListeners.size, healthListeners.size]).toEqual([0, 0]);
  });
});
describe('FrontendRuntime RX LIVE intent', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    configureAcceptedCapabilities();
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
    configureAcceptedCapabilities({
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
    configureAcceptedCapabilities();
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
    configureAcceptedCapabilities({
      ...fakeCaps,
      scope: true,
      capabilities: ['audio', 'scope'],
      scopeSource: 'hardware',
    });
    const unsubscribeDx = vi.fn();
    (onMessage as ReturnType<typeof vi.fn>).mockReturnValue(unsubscribeDx);
    const rt = await freshRuntime();
    await rt.bootstrap();
    const hostListeners = (presentationResources as any).listeners as Set<unknown>;
    const healthListeners = (scopeController as any)._healthSubscribers as Map<unknown, unknown>;
    const stopStatus = effect_root(() => {
      render_effect(() => { rt.defaultScopeStatus; });
    });
    expect([hostListeners.size, healthListeners.size]).toEqual([1, 1]);
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
    expect([hostListeners.size, healthListeners.size]).toEqual([0, 0]);
    expect(rt.releaseHardwareScope(hardwareLease)).toBe(false);
    expect(() => rt.acquireHardwareScope('late')).toThrow('torn down');
    expect(() => rt.subscribeDx(vi.fn())).toThrow('torn down');
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 0,
      health: 'inactive',
      activeHandle: undefined,
    });
    stopStatus(); await settle();
    const stopAfterFinal = effect_root(() => {
      render_effect(() => { rt.defaultScopeStatus; });
    });
    expect([hostListeners.size, healthListeners.size]).toEqual([0, 0]);
    stopAfterFinal(); await settle();
  });
});
