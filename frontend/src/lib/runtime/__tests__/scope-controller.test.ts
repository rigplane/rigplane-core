/**
 * Unit tests for ScopeController.
 *
 * Uses constructor injection (channelFactory) so no vi.mock is needed —
 * safe to run in the fast (non-isolated) vitest pool.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ScopeController } from '../scope-controller.svelte';
import { PresentationResourceHost } from '../resource-host';
import type { ScopeFrame } from '../scope-controller.svelte';

// ── Helpers ──

function makeMockChannel() {
  const binaryHandlers = new Set<(buf: ArrayBuffer) => void>();
  const stateHandlers = new Set<(state: string) => void>();
  const sessionHandlers = new Set<(transition: { state: string; epoch: number }) => void>();
  const binaryHistory: Array<(buf: ArrayBuffer) => void> = [];
  const stateHistory: Array<(state: string) => void> = [];
  const sessionHistory: Array<(transition: { state: string; epoch: number }) => void> = [];
  let state = 'disconnected';
  let sessionEpoch = 1;
  return {
    get state() { return state; },
    get sessionEpoch() { return sessionEpoch; },
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (buf: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      binaryHistory.push(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    onStateChange: vi.fn((handler: (next: string) => void) => {
      stateHandlers.add(handler);
      stateHistory.push(handler);
      return () => { stateHandlers.delete(handler); };
    }),
    onSessionTransition: vi.fn((handler: (transition: { state: string; epoch: number }) => void) => {
      sessionHandlers.add(handler);
      sessionHistory.push(handler);
      return () => { sessionHandlers.delete(handler); };
    }),
    /** Fire a binary message to all registered handlers. */
    _fire(buf: ArrayBuffer) {
      for (const h of binaryHandlers) h(buf);
    },
    _setState(next: string) {
      if (next === 'connected' && state !== 'connected') sessionEpoch += 1;
      state = next;
      for (const h of stateHandlers) h(next);
      for (const h of sessionHandlers) h({ state: next, epoch: sessionEpoch });
    },
    _binaryHistory: binaryHistory, _stateHistory: stateHistory, _sessionHistory: sessionHistory,
    _handlerCount() { return binaryHandlers.size; },
    _stateHandlerCount() { return stateHandlers.size; },
    _sessionHandlerCount() { return sessionHandlers.size; },
  };
}

/** Build a minimal valid scope frame ArrayBuffer (16-byte header + 4 pixels). */
function makeScopeFrameBuffer(pixelCount = 4): ArrayBuffer {
  const buf = new ArrayBuffer(16 + pixelCount);
  const view = new DataView(buf);
  view.setUint8(0, 0x01);          // magic
  view.setUint8(1, 0);             // receiver 0
  view.setUint32(3, 14_100_000, true); // startFreq
  view.setUint32(7, 14_200_000, true); // endFreq
  view.setUint16(14, pixelCount, true);
  return buf;
}

function makeTiming() {
  let now = 0;
  let nextId = 1;
  const tasks = new Map<number, { at: number; callback: () => void; cancelled: boolean }>();
  const timing = {
    now: () => now,
    setTimeout: (callback: () => void, delayMs: number) => {
      const id = nextId++;
      tasks.set(id, { at: now + delayMs, callback, cancelled: false });
      return id as unknown as ReturnType<typeof setTimeout>;
    },
    clearTimeout: (handle: ReturnType<typeof setTimeout>) => {
      const task = tasks.get(handle as unknown as number);
      if (task) task.cancelled = true;
    },
  };
  return {
    timing,
    advance(ms: number) {
      now += ms;
      let progressed = true;
      while (progressed) {
        progressed = false;
        for (const [id, task] of [...tasks]) {
          if (!task.cancelled && task.at <= now) {
            tasks.delete(id);
            task.callback();
            progressed = true;
          }
        }
      }
    },
    fireEvenIfCancelled(id: number) { tasks.get(id)?.callback(); },
    ids: () => [...tasks.keys()],
    activeCount: () => [...tasks.values()].filter((task) => !task.cancelled).length,
  };
}

// ── Tests ──

describe('ScopeController', () => {
  let channel: ReturnType<typeof makeMockChannel>;
  let ctrl: ScopeController;

  beforeEach(() => {
    channel = makeMockChannel();
    ctrl = new ScopeController(() => channel as any);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps frame subscriptions lifetime-neutral', () => {
    expect(channel.connect).not.toHaveBeenCalled();

    const unsubscribe = ctrl.subscribe(vi.fn());
    unsubscribe();
    unsubscribe();

    expect(channel.connect).not.toHaveBeenCalled();
    expect(channel.disconnect).not.toHaveBeenCalled();
  });

  it('registers one exact-handle driver for first/shared/last demand', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    ctrl.registerPresentationDriver(host);
    const first = host.acquire('audio-fft', 'first');
    const shared = host.acquire('audio-fft', 'shared');
    await vi.waitFor(() => expect(channel.connect).toHaveBeenCalledTimes(1));
    expect(channel.connect).toHaveBeenCalledWith('/api/v1/audio-scope');
    expect(host.snapshot('audio-fft').activeHandle).not.toBe(channel);

    expect(host.release(first)).toBe(true);
    expect(channel.disconnect).not.toHaveBeenCalled();
    expect(host.release(shared)).toBe(true);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
    expect(channel._handlerCount()).toBe(0);
    expect(host.release(shared)).toBe(false);
  });

  it('registers audio FFT once per host without overwriting central config', () => {
    const host = new PresentationResourceHost<unknown>('session');
    const configure = vi.spyOn(host, 'configure');

    ctrl.registerPresentationDriver(host, { available: false, selected: false });
    ctrl.registerPresentationDriver(host);

    expect(configure).toHaveBeenCalledTimes(1);
    expect(host.snapshot('audio-fft')).toMatchObject({ available: false, selected: false });
    host.acquire('audio-fft', 'panel');
    expect(channel.connect).not.toHaveBeenCalled();
  });

  it('publishes immutable audio health for the observed connected interval', async () => {
    const listener = vi.fn();
    const unsubscribe = ctrl.subscribeHealth(listener);
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
    expect(Object.isFrozen(ctrl.snapshotHealth('audio_fft'))).toBe(true);

    const handle = await ctrl.audioFftDriver.start();
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'disconnected', frameSeen: false,
    });
    for (const state of ['connecting', 'reconnecting', 'disconnected'] as const) {
      channel._setState(state);
      channel._fire(makeScopeFrameBuffer());
      expect(ctrl.snapshotHealth('audio_fft')).toEqual({
        demanded: true, transport: state, frameSeen: false,
      });
    }
    channel._setState('connected');
    channel._fire(makeScopeFrameBuffer());
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'connected', frameSeen: true,
    });
    channel._setState('reconnecting');
    expect(ctrl.snapshotHealth('audio_fft').frameSeen).toBe(false);

    unsubscribe();
    unsubscribe();
    const calls = listener.mock.calls.length;
    await ctrl.audioFftDriver.stop(handle);
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
    expect(listener).toHaveBeenCalledTimes(calls);
    expect(channel._handlerCount()).toBe(0);
    expect(channel._stateHandlerCount()).toBe(0);
  });

  it('keeps repeated health subscriptions exact and independently removable', async () => {
    const listener = vi.fn();
    const first = ctrl.subscribeHealth(listener);
    const second = ctrl.subscribeHealth(listener);
    const handle = await ctrl.audioFftDriver.start();
    expect(listener).toHaveBeenCalledTimes(2);
    first();
    first();
    channel._setState('connecting');
    expect(listener).toHaveBeenCalledTimes(3);
    second();
    channel._setState('connected');
    expect(listener).toHaveBeenCalledTimes(3);
    await ctrl.audioFftDriver.stop(handle);
  });

  it('isolates a throwing health listener from the same synchronous transition', async () => {
    const failure = new Error('observer failed');
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    const healthy = vi.fn();
    ctrl.subscribeHealth(() => { throw failure; });
    ctrl.subscribeHealth(healthy);

    const started = ctrl.audioFftDriver.start();

    expect(healthy).toHaveBeenCalledWith('audio_fft', {
      demanded: true, transport: 'disconnected', frameSeen: false,
    });
    expect(warn).toHaveBeenCalledWith('Scope health subscriber failed', failure);
    await ctrl.audioFftDriver.stop(await started);
  });

  it('uses a stable listener snapshot when subscriptions mutate during notification', async () => {
    const calls: string[] = [];
    let unsubscribeSelf = () => {};
    unsubscribeSelf = ctrl.subscribeHealth(() => {
      calls.push('self');
      unsubscribeSelf();
      unsubscribeSelf();
      ctrl.subscribeHealth(() => { calls.push('late'); });
    });
    ctrl.subscribeHealth(() => { calls.push('existing'); });

    const handle = await ctrl.audioFftDriver.start();
    expect(calls).toEqual(['self', 'existing']);

    channel._setState('connecting');
    expect(calls).toEqual(['self', 'existing', 'existing', 'late']);
    await ctrl.audioFftDriver.stop(handle);
  });

  it('preserves audio lifecycle cleanup when a health listener throws', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctrl.subscribeHealth(() => { throw new Error('observer failed'); });

    const handle = await ctrl.audioFftDriver.start();
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([1, 1]);
    expect(() => channel._setState('connected')).not.toThrow();
    expect(() => channel._fire(makeScopeFrameBuffer())).not.toThrow();
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'connected', frameSeen: true,
    });
    await ctrl.audioFftDriver.stop(handle);
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([0, 0]);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);
    expect((ctrl as any)._activeHandle).toBeNull();

    const connectFailure = new Error('audio connect failed');
    channel.connect.mockImplementationOnce(() => { throw connectFailure; });
    expect(() => ctrl.audioFftDriver.start()).toThrow(connectFailure);
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([0, 0]);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect((ctrl as any)._activeHandle).toBeNull();
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
  });

  it('preserves hardware lifecycle cleanup when a health listener throws', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctrl.subscribeHealth(() => { throw new Error('observer failed'); });

    const handle = await ctrl.hardwareScopeDriver.start();
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([1, 1]);
    expect(() => channel._setState('connected')).not.toThrow();
    expect(() => channel._fire(makeScopeFrameBuffer())).not.toThrow();
    expect(ctrl.snapshotHealth('hardware')).toEqual({
      demanded: true, transport: 'connected', frameSeen: true,
    });
    await ctrl.hardwareScopeDriver.stop(handle);
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([0, 0]);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);
    expect((ctrl as any)._activeHardwareHandle).toBeNull();

    const connectFailure = new Error('hardware connect failed');
    channel.connect.mockImplementationOnce(() => { throw connectFailure; });
    expect(() => ctrl.hardwareScopeDriver.start()).toThrow(connectFailure);
    expect([channel._handlerCount(), channel._stateHandlerCount()]).toEqual([0, 0]);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect((ctrl as any)._activeHardwareHandle).toBeNull();
    expect(ctrl.snapshotHealth('hardware')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
  });

  it('both subscribers receive parsed frames', async () => {
    const h1 = vi.fn<(frame: ScopeFrame) => void>();
    const h2 = vi.fn<(frame: ScopeFrame) => void>();

    ctrl.subscribe(h1);
    ctrl.subscribe(h2);
    await ctrl.audioFftDriver.start();
    channel._setState('connected');

    channel._fire(makeScopeFrameBuffer());

    expect(h1).toHaveBeenCalledTimes(1);
    expect(h2).toHaveBeenCalledTimes(1);

    const frame = h1.mock.calls[0][0];
    expect(frame.startFreq).toBe(14_100_000);
    expect(frame.endFreq).toBe(14_200_000);
  });

  it('unsubscribed handler no longer receives frames', async () => {
    const h1 = vi.fn();
    const h2 = vi.fn();

    const unsub1 = ctrl.subscribe(h1);
    ctrl.subscribe(h2);
    await ctrl.audioFftDriver.start();
    channel._setState('connected');

    unsub1();
    channel._fire(makeScopeFrameBuffer());

    expect(h1).not.toHaveBeenCalled();
    expect(h2).toHaveBeenCalledTimes(1);
  });

  it('keeps repeated subscriptions of the same handler independent', async () => {
    const handler = vi.fn();
    const first = ctrl.subscribe(handler);
    const second = ctrl.subscribe(handler);
    const handle = await ctrl.audioFftDriver.start();
    channel._setState('connected');
    first();
    channel._fire(makeScopeFrameBuffer());
    expect(handler).toHaveBeenCalledTimes(1);
    second();
    channel._fire(makeScopeFrameBuffer());
    expect(handler).toHaveBeenCalledTimes(1);
    await ctrl.audioFftDriver.stop(handle);
  });

  it('ignores malformed frames (wrong magic byte)', async () => {
    const handler = vi.fn();
    ctrl.subscribe(handler);
    await ctrl.audioFftDriver.start();
    channel._setState('connected');

    // Bad magic — parseScopeFrame returns null
    const bad = new ArrayBuffer(20);
    channel._fire(bad);

    expect(handler).not.toHaveBeenCalled();
  });

  it('disposes an orphaned repeated start on the memoized channel', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    ctrl.registerPresentationDriver(host);
    const warm = host.acquire('audio-fft', 'warm');
    await vi.waitFor(() => expect(host.snapshot('audio-fft').health).toBe('streaming'));
    const firstHandle = host.snapshot('audio-fft').activeHandle;
    host.release(warm);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));

    const orphan = host.acquire('audio-fft', 'orphan');
    host.release(orphan);
    await Promise.resolve();
    await Promise.resolve();

    expect(channel.connect).toHaveBeenCalledTimes(2);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect(channel._handlerCount()).toBe(0);
    expect(host.snapshot('audio-fft').activeHandle).toBeUndefined();
    const next = await ctrl.audioFftDriver.start();
    expect(next).not.toBe(firstHandle);
    await ctrl.audioFftDriver.stop(next);
  });

  it('remounts with one callback after an orphaned start', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    const subscriber = vi.fn();
    ctrl.subscribe(subscriber);
    ctrl.registerPresentationDriver(host);
    const warm = host.acquire('audio-fft', 'warm');
    await vi.waitFor(() => expect(host.snapshot('audio-fft').health).toBe('streaming'));
    host.release(warm);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
    const orphan = host.acquire('audio-fft', 'orphan');
    host.release(orphan);
    await Promise.resolve();
    await Promise.resolve();
    const remount = host.acquire('audio-fft', 'remount');
    await vi.waitFor(() => expect(host.snapshot('audio-fft').health).toBe('streaming'));

    expect(channel._handlerCount()).toBe(1);
    channel._setState('connected');
    channel._fire(makeScopeFrameBuffer());
    expect(subscriber).toHaveBeenCalledTimes(1);
    host.release(remount);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(3));
    expect(channel._handlerCount()).toBe(0);
  });

  it('teardown racing a repeated start prevents later publication', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    const subscriber = vi.fn();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctrl.subscribeHealth(() => { throw new Error('observer failed'); });
    ctrl.subscribe(subscriber);
    ctrl.registerPresentationDriver(host);
    const warm = host.acquire('audio-fft', 'warm');
    await vi.waitFor(() => expect(host.snapshot('audio-fft').health).toBe('streaming'));
    host.release(warm);
    await vi.waitFor(() => expect(channel.disconnect).toHaveBeenCalledTimes(1));
    host.acquire('audio-fft', 'racing');
    await host.teardown();

    channel._fire(makeScopeFrameBuffer());
    expect(channel.connect).toHaveBeenCalledTimes(2);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect(channel._handlerCount()).toBe(0);
    expect(subscriber).not.toHaveBeenCalled();
    expect(ctrl.audioScopeFrame).toBeNull();
  });

  it('keeps the panel on one demand lease without transport or hardware fallback', () => {
    const source = readFileSync(
      resolve('src/components-v2/panels/audio-scope/AudioSpectrumPanel.svelte'),
      'utf8',
    );
    expect(source).toContain("acquire('audio-fft'");
    expect(source).toContain('release(lease)');
    expect(source).toContain('registerPresentationDriver');
    expect(source).not.toMatch(/\$lib\/transport|hardware-scope/);
  });

  it('keeps default construction inert and resolves transport on first start', async () => {
    const source = readFileSync(resolve('src/lib/runtime/scope-controller.svelte.ts'), 'utf8');
    expect(source).not.toContain('channelFactory: ChannelFactory = getChannel');
    const defaultChannel = makeMockChannel();
    const lookup = vi.fn(() => defaultChannel as any);
    const defaultController = new ScopeController(lookup);
    expect(lookup).not.toHaveBeenCalled();

    const handle = await defaultController.audioFftDriver.start();
    expect(lookup).toHaveBeenCalledTimes(1);
    expect(lookup).toHaveBeenCalledWith('audio-scope');
    expect(defaultChannel.connect).toHaveBeenCalledWith('/api/v1/audio-scope');
    await defaultController.audioFftDriver.stop(handle);
  });

  it.each(['audioFftDriver', 'hardwareScopeDriver'] as const)(
    'keeps %s startup compatible with pre-session-transition channel doubles',
    async (driverName) => {
      const legacy = makeMockChannel();
      Reflect.deleteProperty(legacy, 'onSessionTransition');
      const controller = new ScopeController(() => legacy as any);
      const driver = controller[driverName];

      const handle = await driver.start();
      expect(legacy.connect).toHaveBeenCalledTimes(1);
      await driver.stop(handle);
      expect(legacy.disconnect).toHaveBeenCalledTimes(1);
    },
  );

  it('preserves raw delivery but keeps display evidence closed without a public session epoch', async () => {
    const legacy = makeMockChannel();
    Reflect.deleteProperty(legacy, 'onSessionTransition');
    Reflect.deleteProperty(legacy, 'sessionEpoch');
    const controller = new ScopeController(() => legacy as any);
    const subscriber = vi.fn();
    controller.subscribeHardware(subscriber);
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 1 });
    const handle = await controller.hardwareScopeDriver.start();

    legacy._setState('connected');
    legacy._fire(makeScopeFrameBuffer());
    expect(subscriber).toHaveBeenCalledTimes(1);
    expect(controller.snapshotHealth('hardware').frameSeen).toBe(true);
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();
    await controller.hardwareScopeDriver.stop(handle);
  });

  it('owns hardware first/shared/last demand without claiming frame liveness', async () => {
    const channels = { scope: makeMockChannel(), 'audio-scope': makeMockChannel() };
    const lookup = vi.fn((name: string) => channels[name as keyof typeof channels] as any);
    const controller = new ScopeController(lookup);
    const host = new PresentationResourceHost<unknown>('hardware');
    host.configure('hardware-scope', { available: true, selected: true, driver: controller.hardwareScopeDriver });
    expect(lookup).not.toHaveBeenCalled();

    const first = host.acquire('hardware-scope', 'first');
    const shared = host.acquire('hardware-scope', 'shared');
    await vi.waitFor(() => expect(channels.scope.connect).toHaveBeenCalledTimes(1));
    expect(lookup).toHaveBeenCalledWith('scope');
    expect(channels.scope.connect).toHaveBeenCalledWith('/api/v1/scope');
    expect([
      controller.hardwareScopeDemanded,
      controller.hardwareScopeConnected,
      controller.hardwareScopeFrameLive,
    ]).toEqual([true, false, false]);
    channels.scope._fire(makeScopeFrameBuffer());
    expect(controller.snapshotHealth('hardware')).toEqual({
      demanded: true, transport: 'disconnected', frameSeen: false,
    });

    channels.scope._setState('connected');
    expect(controller.hardwareScopeConnected).toBe(true);
    expect(controller.hardwareScopeFrameLive).toBe(false);
    channels.scope._fire(makeScopeFrameBuffer());
    expect(controller.scopeFrame?.startFreq).toBe(14_100_000);
    expect(controller.hardwareScopeFrameLive).toBe(true);

    expect(host.release(first)).toBe(true);
    expect(channels.scope.disconnect).not.toHaveBeenCalled();
    expect(host.release(shared)).toBe(true);
    await vi.waitFor(() => expect(channels.scope.disconnect).toHaveBeenCalledTimes(1));
    expect(host.release(shared)).toBe(false);
    expect([
      controller.hardwareScopeDemanded,
      controller.hardwareScopeConnected,
      controller.hardwareScopeFrameLive,
      controller.scopeFrame,
    ]).toEqual([false, false, false, null]);
    expect(channels.scope._handlerCount()).toBe(0);
    expect(channels.scope._stateHandlerCount()).toBe(0);
    expect(channels['audio-scope'].connect).not.toHaveBeenCalled();

    const pending = host.acquire('hardware-scope', 'pending');
    expect(host.release(pending)).toBe(true);
    await vi.waitFor(() => expect(channels.scope.disconnect).toHaveBeenCalledTimes(2));
    expect(channels.scope._handlerCount()).toBe(0);
    expect(controller.hardwareScopeDemanded).toBe(false);
  });

  it('qualifies hardware callbacks and cleanup across A to B to A handles', async () => {
    const hardware = makeMockChannel();
    const controller = new ScopeController(() => hardware as any);
    const subscriber = vi.fn();
    controller.subscribeHardware(subscriber);
    const firstA = await controller.hardwareScopeDriver.start();
    const b = await controller.hardwareScopeDriver.start();
    const currentA = await controller.hardwareScopeDriver.start();
    const [staleAFrame, staleBFrame, currentFrame] = hardware._binaryHistory;
    const [staleAState, staleBState, currentState] = hardware._stateHistory;

    await controller.hardwareScopeDriver.stop(firstA);
    await controller.hardwareScopeDriver.dispose?.(b);
    staleAState('connected');
    staleBState('connected');
    staleAFrame(makeScopeFrameBuffer());
    staleBFrame(makeScopeFrameBuffer());
    expect(controller.hardwareScopeConnected).toBe(false);
    expect(controller.scopeFrame).toBeNull();
    expect(subscriber).not.toHaveBeenCalled();
    expect(hardware.disconnect).not.toHaveBeenCalled();

    currentState('connected');
    currentFrame(makeScopeFrameBuffer());
    expect(controller.hardwareScopeConnected).toBe(true);
    expect(controller.hardwareScopeFrameLive).toBe(true);
    expect(subscriber).toHaveBeenCalledTimes(1);
    await controller.hardwareScopeDriver.stop(currentA);
    expect(hardware.disconnect).toHaveBeenCalledTimes(1);
  });

  it('does not let stale reconnect callbacks revive current hardware state', async () => {
    const hardware = makeMockChannel();
    const controller = new ScopeController(() => hardware as any);
    const stale = await controller.hardwareScopeDriver.start();
    const staleState = hardware._stateHistory[0];
    await controller.hardwareScopeDriver.stop(stale);
    const current = await controller.hardwareScopeDriver.start();
    const currentState = hardware._stateHistory[1];

    staleState('connected');
    expect(controller.hardwareScopeConnected).toBe(false);
    currentState('connected');
    hardware._fire(makeScopeFrameBuffer());
    expect(controller.hardwareScopeFrameLive).toBe(true);
    currentState('reconnecting');
    expect([
      controller.hardwareScopeConnected,
      controller.hardwareScopeFrameLive,
      controller.scopeFrame,
    ]).toEqual([false, false, null]);
    currentState('connected');
    expect(controller.hardwareScopeConnected).toBe(true);
    expect(controller.hardwareScopeFrameLive).toBe(false);
    await controller.hardwareScopeDriver.stop(current);
  });

  it('qualifies audio callbacks and cleanup across A to B to A handles', async () => {
    const audio = makeMockChannel();
    const controller = new ScopeController(() => audio as any);
    const subscriber = vi.fn();
    controller.subscribe(subscriber);
    const firstA = await controller.audioFftDriver.start();
    const b = await controller.audioFftDriver.start();
    const currentA = await controller.audioFftDriver.start();
    const [staleAFrame, staleBFrame, currentFrame] = audio._binaryHistory;
    const [staleAState, staleBState, currentState] = audio._stateHistory;

    await controller.audioFftDriver.stop(firstA);
    await controller.audioFftDriver.dispose?.(b);
    staleAState('connected');
    staleBState('connected');
    staleAFrame(makeScopeFrameBuffer());
    staleBFrame(makeScopeFrameBuffer());
    expect(controller.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'disconnected', frameSeen: false,
    });
    expect(subscriber).not.toHaveBeenCalled();

    currentFrame(makeScopeFrameBuffer());
    expect(subscriber).not.toHaveBeenCalled();
    currentState('connected');
    currentFrame(makeScopeFrameBuffer());
    expect(controller.snapshotHealth('audio_fft').frameSeen).toBe(true);
    currentState('reconnecting');
    staleAState('connected');
    staleAFrame(makeScopeFrameBuffer());
    expect(controller.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'reconnecting', frameSeen: false,
    });
    await controller.audioFftDriver.stop(currentA);
    expect(controller.snapshotHealth('audio_fft')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
    expect(audio.disconnect).toHaveBeenCalledTimes(1);
  });

  it.each([
    ['hardware', 'hardwareScopeDriver'],
    ['audio_fft', 'audioFftDriver'],
  ] as const)('owns a live %s receipt only below the exact 500 ms boundary', async (source, driverName) => {
    const timed = makeTiming();
    const local = makeMockChannel();
    const controller = new ScopeController(() => local as any, timed.timing);
    controller.setFrameAuthority({ source, receiver: 0, providerGeneration: 9 });
    const driver = controller[driverName];
    const handle = await driver.start();
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());

    const receipt = controller.snapshotFrameEvidence().envelope;
    expect(receipt).toMatchObject({
      source, receiver: 0, providerGeneration: 9,
      transportEpoch: local.sessionEpoch, receivedAt: 0, acceptedSequence: 1,
    });
    expect(controller.snapshotHealth(source).frameSeen).toBe(true);
    timed.advance(499);
    expect(controller.snapshotHealth(source).frameSeen).toBe(true);
    timed.advance(1);
    expect(controller.snapshotHealth(source).frameSeen).toBe(false);
    expect(controller.snapshotFrameEvidence().envelope).toBe(receipt);
    await driver.stop(handle);
  });

  it('does not let a delayed older expiry callback clear a newer receipt', async () => {
    const timed = makeTiming();
    const local = makeMockChannel();
    const controller = new ScopeController(() => local as any, timed.timing);
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 2 });
    const handle = await controller.hardwareScopeDriver.start();
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());
    const oldTimer = timed.ids()[0];
    timed.advance(100);
    local._fire(makeScopeFrameBuffer());
    const newer = controller.snapshotFrameEvidence().envelope;

    timed.fireEvenIfCancelled(oldTimer);
    expect(controller.snapshotFrameEvidence().envelope).toBe(newer);
    expect(controller.snapshotHealth('hardware').frameSeen).toBe(true);
    expect(newer?.acceptedSequence).toBe(2);
    await controller.hardwareScopeDriver.stop(handle);
  });

  it('invalidates source, receiver, provider, transport, and demand boundaries immediately', async () => {
    const timed = makeTiming();
    const local = makeMockChannel();
    const controller = new ScopeController(() => local as any, timed.timing);
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 3 });
    let handle = await controller.hardwareScopeDriver.start();
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());
    expect(controller.snapshotFrameEvidence().envelope).not.toBeNull();

    controller.setFrameAuthority({ source: 'audio_fft', receiver: 0, providerGeneration: 3 });
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();
    controller.setFrameAuthority({ source: 'hardware', receiver: null, providerGeneration: 3 });
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 4 });
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();

    await controller.hardwareScopeDriver.stop(handle);
    handle = await controller.hardwareScopeDriver.start();
    local._setState('reconnecting');
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());
    expect(controller.snapshotFrameEvidence().envelope).not.toBeNull();
    local._setState('reconnecting');
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();
    expect(controller.snapshotHealth('hardware').frameSeen).toBe(false);

    await controller.hardwareScopeDriver.stop(handle);
    expect(controller.snapshotFrameEvidence().authority.demanded).toBe(false);
    expect(timed.activeCount()).toBe(0);
  });

  it('rejects mismatched receivers and old provider callbacks without reviving or clearing current facts', async () => {
    const timed = makeTiming();
    const local = makeMockChannel();
    const controller = new ScopeController(() => local as any, timed.timing);
    controller.setFrameAuthority({ source: 'hardware', receiver: 1, providerGeneration: 5 });
    const oldHandle = await controller.hardwareScopeDriver.start();
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();

    await controller.hardwareScopeDriver.stop(oldHandle);
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 5 });
    const currentHandle = await controller.hardwareScopeDriver.start();
    const oldGenerationCallback = local._binaryHistory.at(-1)!;
    local._setState('connected');
    local._fire(makeScopeFrameBuffer());
    expect(controller.snapshotFrameEvidence().envelope?.providerGeneration).toBe(5);
    controller.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 6 });
    oldGenerationCallback(makeScopeFrameBuffer());
    expect(controller.snapshotFrameEvidence().envelope).toBeNull();
    await controller.hardwareScopeDriver.stop(currentHandle);
    expect([local._handlerCount(), local._stateHandlerCount(), local._sessionHandlerCount()])
      .toEqual([0, 0, 0]);
  });
});


describe.each([
  ['hardware', 'hardwareScopeDriver', 'hardware-scope', '/api/v1/scope'],
  ['audio_fft', 'audioFftDriver', 'audio-fft', '/api/v1/audio-scope'],
] as const)('authority rebind: %s', (source, driverName, resource, endpoint) => {
  function rig() {
    const channel = makeMockChannel();
    channel.disconnect.mockImplementation(() => channel._setState('disconnected'));
    channel.connect.mockImplementation(() => channel._setState('connecting'));
    const controller = new ScopeController(() => channel as never, makeTiming().timing);
    const driver = controller[driverName];
    const stop = vi.spyOn(driver, 'stop');
    const start = vi.spyOn(driver, 'start');
    const host = new PresentationResourceHost(`rebind-${source}`);
    host.configure(resource, { available: true, selected: true, driver });
    const authority = (providerGeneration: number, receiver: 0 | 1 = 0) =>
      controller.setFrameAuthority({ source, receiver, providerGeneration });
    const raw = vi.fn();
    if (source === 'hardware') controller.subscribeHardware(raw);
    else controller.subscribe(raw);
    const latest = () => source === 'hardware' ? controller.scopeFrame : controller.audioScopeFrame;
    const retain = () => {
      const binary = channel._binaryHistory.at(-1)!;
      const state = channel._stateHistory.at(-1)!;
      const session = channel._sessionHistory.at(-1)!;
      return () => {
        binary(makeScopeFrameBuffer());
        binary(new ArrayBuffer(16));
        state('connected');
        state('disconnected');
        session({ state: 'disconnected', epoch: 999 });
        session({ state: 'connected', epoch: 999 });
      };
    };
    const counts = () => [channel._handlerCount(), channel._stateHandlerCount(), channel._sessionHandlerCount()];
    return { channel, controller, host, authority, raw, latest, retain, counts, start, stop };
  }

  it('recovers demand two under the original handle and fences every retired callback', async () => {
    const r = rig();
    const independent = r.host.acquire(resource, 'independent-before-authority')!;
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
    const originalHandle = r.host.snapshot(resource).activeHandle;
    r.channel._setState('connected');
    r.channel._fire(makeScopeFrameBuffer());
    expect(r.latest()).not.toBeNull();
    expect(r.controller.snapshotFrameEvidence().envelope).toBeNull();
    let retired = r.retain();
    let epoch = r.channel.sessionEpoch;
    for (const generation of [1, 2]) {
      r.authority(generation);
      const managed = r.host.acquire(resource, `managed-${generation}`)!;
      await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
      expect(r.host.snapshot(resource)).toMatchObject({ demand: 2, activeHandle: originalHandle });
      expect(r.host.snapshot(resource).activeHandle).toBe(originalHandle);
      expect(r.start).toHaveBeenCalledTimes(1);
      expect(r.stop).not.toHaveBeenCalled();
      expect(r.channel.connect).toHaveBeenCalledTimes(generation + 1);
      expect(r.channel.connect).toHaveBeenLastCalledWith(endpoint);
      expect(r.channel.disconnect).toHaveBeenCalledTimes(generation);
      expect(r.counts()).toEqual([1, 1, 1]);
      expect(r.latest()).toBeNull();
      expect(r.controller.snapshotHealth(source)).toEqual({ demanded: true, transport: 'connecting', frameSeen: false });
      const evidence = vi.fn();
      const unsubscribe = r.controller.subscribeFrameEvidence(evidence);
      const rawCount = r.raw.mock.calls.length;
      retired();
      r.channel._fire(makeScopeFrameBuffer());
      expect(r.raw).toHaveBeenCalledTimes(rawCount);
      expect(evidence).not.toHaveBeenCalled();
      expect(r.controller.snapshotFrameEvidence().envelope).toBeNull();
      r.channel._setState('connected');
      expect(r.channel.sessionEpoch).toBeGreaterThan(epoch);
      r.channel._fire(makeScopeFrameBuffer());
      const current = r.controller.snapshotFrameEvidence();
      expect(current.envelope).toMatchObject({ providerGeneration: generation, transportEpoch: r.channel.sessionEpoch, acceptedSequence: generation + 1 });
      expect(r.raw).toHaveBeenCalledTimes(rawCount + 1);
      evidence.mockClear();
      retired();
      expect(r.controller.snapshotFrameEvidence()).toEqual(current);
      expect(r.controller.snapshotHealth(source)).toEqual({ demanded: true, transport: 'connected', frameSeen: true });
      expect(r.raw).toHaveBeenCalledTimes(rawCount + 1);
      expect(evidence).not.toHaveBeenCalled();
      unsubscribe();
      epoch = r.channel.sessionEpoch;
      retired = r.retain();
      r.host.release(managed);
      await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
      expect(r.channel.disconnect).toHaveBeenCalledTimes(generation);
    }
    r.host.release(independent);
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
    expect(r.stop).toHaveBeenCalledExactlyOnceWith(originalHandle);
    expect(r.host.snapshot(resource).demand).toBe(0);
    expect(r.channel.disconnect).toHaveBeenCalledTimes(3);
    expect(r.counts()).toEqual([0, 0, 0]);
    const final = r.controller.snapshotFrameEvidence();
    const rawCount = r.raw.mock.calls.length;
    retired();
    expect(r.controller.snapshotFrameEvidence()).toEqual(final);
    expect(r.latest()).toBeNull();
    expect(r.raw).toHaveBeenCalledTimes(rawCount);
    expect(r.controller.snapshotHealth(source)).toEqual({ demanded: false, transport: 'disconnected', frameSeen: false });
  });

  it('keeps fresh, receiver-only and null authority streams running; restores valid authority once', async () => {
    const r = rig();
    r.authority(1);
    const lease = r.host.acquire(resource, 'foreign')!;
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
    r.channel._setState('connected');
    r.authority(1);
    r.authority(1, 1);
    r.channel._fire(makeScopeFrameBuffer());
    expect(r.controller.snapshotFrameEvidence().envelope).toBeNull();
    r.authority(1);
    r.channel._fire(makeScopeFrameBuffer());
    expect(r.controller.snapshotFrameEvidence().envelope).not.toBeNull();
    r.controller.setFrameAuthority(null);
    r.controller.setFrameAuthority({ source, receiver: null, providerGeneration: 2 });
    r.controller.setFrameAuthority({ source, receiver: 0, providerGeneration: null });
    r.channel._fire(makeScopeFrameBuffer());
    expect(r.latest()).not.toBeNull();
    expect(r.raw).toHaveBeenCalledTimes(3);
    expect(r.controller.snapshotFrameEvidence().envelope).toBeNull();
    expect(r.channel.disconnect).not.toHaveBeenCalled();
    expect(r.channel.connect).toHaveBeenCalledTimes(1);
    expect(r.host.snapshot(resource).demand).toBe(1);
    r.authority(1);
    expect(r.channel.disconnect).toHaveBeenCalledTimes(1);
    expect(r.latest()).toBeNull();
    r.host.release(lease);
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
  });

  it.each(['onBinary', 'onStateChange', 'onSessionTransition', 'connect'] as const)('cleans failed %s while retaining original-handle final cleanup', async (failurePoint) => {
    const r = rig();
    const lease = r.host.acquire(resource, 'foreign')!;
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
    const originalHandle = r.host.snapshot(resource).activeHandle;
    r.channel._setState('connected');
    const retired = r.retain();
    const failure = new Error(`rebind ${failurePoint}`);
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    r.channel[failurePoint].mockImplementationOnce(() => { throw failure; });
    expect(() => r.authority(1)).not.toThrow();
    expect(warn).toHaveBeenCalledWith('Scope authority rebind failed', failure);
    expect(r.counts()).toEqual([0, 0, 0]);
    r.retain()(); // Also reject callbacks installed before the failed registration/connect.
    expect(r.controller.snapshotHealth(source)).toEqual({ demanded: true, transport: 'disconnected', frameSeen: false });
    expect(r.host.snapshot(resource).activeHandle).toBe(originalHandle);
    retired();
    expect(r.latest()).toBeNull();
    expect(r.controller.snapshotFrameEvidence().envelope).toBeNull();
    r.host.release(lease);
    await vi.waitFor(() => expect(r.host.snapshot(resource).pending).toBeUndefined());
    expect(r.stop).toHaveBeenCalledExactlyOnceWith(originalHandle);
    expect(r.counts()).toEqual([0, 0, 0]);
    expect(r.channel.disconnect).toHaveBeenCalledTimes(3);
    expect(r.controller.snapshotHealth(source).demanded).toBe(false);
    warn.mockRestore();
  });
});


it('rebinds only the stale selected source while foreign demand and fresh source bindings survive', async () => {
  const channels = { scope: makeMockChannel(), 'audio-scope': makeMockChannel() };
  for (const channel of Object.values(channels)) {
    channel.disconnect.mockImplementation(() => channel._setState('disconnected'));
    channel.connect.mockImplementation(() => channel._setState('connecting'));
  }
  const factory = vi.fn((name: string) => channels[name as keyof typeof channels] as never);
  const controller = new ScopeController(factory, makeTiming().timing);
  const host = new PresentationResourceHost('both');
  host.configure('hardware-scope', { available: true, selected: true, driver: controller.hardwareScopeDriver });
  host.configure('audio-fft', { available: true, selected: true, driver: controller.audioFftDriver });
  host.acquire('hardware-scope', 'foreign-hardware');
  host.acquire('audio-fft', 'foreign-audio');
  await vi.waitFor(() => expect(host.snapshot('audio-fft').activeHandle).toBeDefined());
  channels.scope._setState('connected');
  channels['audio-scope']._setState('connected');
  const select = (source: 'hardware' | 'audio_fft') =>
    controller.setFrameAuthority({ source, receiver: 0, providerGeneration: 1 });
  select('hardware');
  expect(channels.scope.disconnect).toHaveBeenCalledTimes(1);
  expect(channels['audio-scope'].disconnect).not.toHaveBeenCalled();
  channels['audio-scope']._fire(makeScopeFrameBuffer());
  expect(controller.audioScopeFrame).not.toBeNull();
  expect(controller.snapshotFrameEvidence().envelope).toBeNull();
  select('audio_fft');
  expect(channels['audio-scope'].disconnect).toHaveBeenCalledTimes(1);
  select('hardware');
  select('audio_fft');
  expect(channels.scope.disconnect).toHaveBeenCalledTimes(1);
  expect(channels['audio-scope'].disconnect).toHaveBeenCalledTimes(1);
  expect(host.snapshot('hardware-scope').demand).toBe(1);
  expect(host.snapshot('audio-fft').demand).toBe(1);
  expect(factory).toHaveBeenCalledTimes(2);
  await host.teardown();
});
