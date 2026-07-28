/**
 * Unit tests for ScopeController.
 *
 * Uses constructor injection (channelFactory) so no vi.mock is needed —
 * safe to run in the fast (non-isolated) vitest pool.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ScopeController } from '../scope-controller.svelte';
import { PresentationResourceHost } from '../resource-host';
import type { ScopeFrame } from '../scope-controller.svelte';

// ── Helpers ──

function makeMockChannel() {
  const binaryHandlers = new Set<(buf: ArrayBuffer) => void>();
  const stateHandlers = new Set<(state: string) => void>();
  const binaryHistory: Array<(buf: ArrayBuffer) => void> = [];
  const stateHistory: Array<(state: string) => void> = [];
  let state = 'disconnected';
  return {
    get state() { return state; },
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
    /** Fire a binary message to all registered handlers. */
    _fire(buf: ArrayBuffer) {
      for (const h of binaryHandlers) h(buf);
    },
    _setState(next: string) {
      state = next;
      for (const h of stateHandlers) h(next);
    },
    _binaryHistory: binaryHistory, _stateHistory: stateHistory,
    _handlerCount() { return binaryHandlers.size; },
    _stateHandlerCount() { return stateHandlers.size; },
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

// ── Tests ──

describe('ScopeController', () => {
  let channel: ReturnType<typeof makeMockChannel>;
  let ctrl: ScopeController;

  beforeEach(() => {
    channel = makeMockChannel();
    ctrl = new ScopeController(() => channel as any);
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
});
