/**
 * Unit tests for ScopeController's channel lifecycle, demand health and
 * shared streams (MOR-4270). The old frame-authority/evidence suite was
 * retired with the deleted plumbing; stream ingress behaviour lives in
 * scope-stream.test.ts and scope-controller-stream.test.ts.
 *
 * Uses constructor injection (channelFactory) so no vi.mock is needed —
 * safe to run in the fast (non-isolated) vitest pool.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { ScopeController } from '../scope-controller.svelte';
import { PresentationResourceHost } from '../resource-host';
import { ScopeRenderTrace } from '../scope-render-trace';

// ── Helpers ──

function makeMockChannel() {
  const binaryHandlers = new Set<(buf: ArrayBuffer) => void>();
  const stateHandlers = new Set<(state: string) => void>();
  const sessionHandlers = new Set<(transition: { state: string; epoch: number }) => void>();
  let state = 'disconnected';
  let sessionEpoch = 1;
  return {
    get state() { return state; },
    get sessionEpoch() { return sessionEpoch; },
    connect: vi.fn(),
    disconnect: vi.fn(),
    onBinary: vi.fn((handler: (buf: ArrayBuffer) => void) => {
      binaryHandlers.add(handler);
      return () => { binaryHandlers.delete(handler); };
    }),
    onStateChange: vi.fn((handler: (next: string) => void) => {
      stateHandlers.add(handler);
      return () => { stateHandlers.delete(handler); };
    }),
    onSessionTransition: vi.fn((handler: (transition: { state: string; epoch: number }) => void) => {
      sessionHandlers.add(handler);
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
    _binaryHandlerCount() { return binaryHandlers.size; },
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

// Golden bytes emitted by rigplane.web.protocol.encode_scope_frame, with
// receiver/mode 0, 14.0–14.1 MHz and pixels [0, 128, 255]. Keep the complete
// wire header independent of the decoder: sequence bytes11–12, flags13.
const encodedScopeFrames: ReadonlyArray<{ sequence: number; flags: number; hex: string }> = [
  { sequence: 65535, flags: 0, hex: '010000809fd5002026d700ffff0003000080ff' },
  { sequence: 0, flags: 1, hex: '010000809fd5002026d70000000103000080ff' },
  { sequence: 1, flags: 0, hex: '010000809fd5002026d70001000003000080ff' },
  { sequence: 3, flags: 1, hex: '010000809fd5002026d70003000103000080ff' },
];
function encodedScopeBuffer(hex: string): ArrayBuffer {
  return Uint8Array.from(hex.match(/../g)!, pair => Number.parseInt(pair, 16)).buffer;
}

// ── Tests ──

describe('ScopeController', () => {
  let channel: ReturnType<typeof makeMockChannel>;
  let ctrl: ScopeController;

  beforeEach(() => {
    channel = makeMockChannel();
    ctrl = new ScopeController(() => channel as never);
  });

  afterEach(() => {
    vi.restoreAllMocks();
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
    expect(channel._stateHandlerCount()).toBe(0);
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
    expect(channel._stateHandlerCount()).toBe(1);
    expect(() => channel._setState('connected')).not.toThrow();
    expect(() => channel._fire(makeScopeFrameBuffer())).not.toThrow();
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: true, transport: 'connected', frameSeen: true,
    });
    await ctrl.audioFftDriver.stop(handle);
    expect(channel._stateHandlerCount()).toBe(0);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);

    const connectFailure = new Error('audio connect failed');
    channel.connect.mockImplementationOnce(() => { throw connectFailure; });
    expect(() => ctrl.audioFftDriver.start()).toThrow(connectFailure);
    expect(channel._stateHandlerCount()).toBe(0);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect(ctrl.snapshotHealth('audio_fft')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
  });

  it('preserves hardware lifecycle cleanup when a health listener throws', async () => {
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctrl.subscribeHealth(() => { throw new Error('observer failed'); });

    const handle = await ctrl.hardwareScopeDriver.start();
    expect(channel._stateHandlerCount()).toBe(1);
    expect(() => channel._setState('connected')).not.toThrow();
    expect(() => channel._fire(makeScopeFrameBuffer())).not.toThrow();
    expect(ctrl.snapshotHealth('hardware')).toEqual({
      demanded: true, transport: 'connected', frameSeen: true,
    });
    await ctrl.hardwareScopeDriver.stop(handle);
    expect(channel._stateHandlerCount()).toBe(0);
    expect(channel.disconnect).toHaveBeenCalledTimes(1);

    const connectFailure = new Error('hardware connect failed');
    channel.connect.mockImplementationOnce(() => { throw connectFailure; });
    expect(() => ctrl.hardwareScopeDriver.start()).toThrow(connectFailure);
    expect(channel._stateHandlerCount()).toBe(0);
    expect(channel.disconnect).toHaveBeenCalledTimes(2);
    expect(ctrl.snapshotHealth('hardware')).toEqual({
      demanded: false, transport: 'disconnected', frameSeen: false,
    });
  });

  it('counts a real wire skip but neither flag changes nor uint16 wrap as gaps through the shared stream', async () => {
    const trace = new ScopeRenderTrace();
    const unsubscribe = ctrl.subscribeTraceReceipt(receipt => trace.observe(receipt));
    const handle = await ctrl.hardwareScopeDriver.start();
    try {
      channel._setState('connected');
      for (const { hex } of encodedScopeFrames) channel._fire(encodedScopeBuffer(hex));
      trace.stop();
      const result = JSON.parse(trace.export()!);
      expect(result.counts).toMatchObject({ accepted: 4, rejected: 0, wireGaps: 1 });
      expect(result.burst.filter((event: { stage: string }) => event.stage === 'wire_gap'))
        .toEqual([expect.objectContaining({ source: 'hardware', wireSequence: 3 })]);
    } finally {
      unsubscribe(); trace.dispose();
      await ctrl.hardwareScopeDriver.stop(handle);
    }
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
    expect(channel._stateHandlerCount()).toBe(0);
    expect(host.snapshot('audio-fft').activeHandle).toBeUndefined();
    const next = await ctrl.audioFftDriver.start();
    expect(next).not.toBe(firstHandle);
    await ctrl.audioFftDriver.stop(next);
  });

  it('teardown racing a repeated start prevents later stream publication', async () => {
    const host = new PresentationResourceHost<unknown>('session');
    const sink = vi.fn();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    ctrl.subscribeHealth(() => { throw new Error('observer failed'); });
    ctrl.stream('audio_fft').subscribe(sink);
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
    expect(channel._stateHandlerCount()).toBe(0);
    expect(sink).not.toHaveBeenCalled();
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
    const lookup = vi.fn(() => defaultChannel as never);
    const defaultController = new ScopeController(lookup);
    expect(lookup).not.toHaveBeenCalled();

    const handle = await defaultController.audioFftDriver.start();
    expect(lookup).toHaveBeenCalledWith('audio-scope');
    expect(defaultChannel.connect).toHaveBeenCalledWith('/api/v1/audio-scope');
    await defaultController.audioFftDriver.stop(handle);
  });

  it.each(['audioFftDriver', 'hardwareScopeDriver'] as const)(
    'keeps %s startup compatible with pre-session-transition channel doubles',
    async (driverName) => {
      const legacy = makeMockChannel();
      Reflect.deleteProperty(legacy, 'onSessionTransition');
      const controller = new ScopeController(() => legacy as never);
      const driver = controller[driverName];

      const handle = await driver.start();
      expect(legacy.connect).toHaveBeenCalledTimes(1);
      await driver.stop(handle);
      expect(legacy.disconnect).toHaveBeenCalledTimes(1);
    },
  );
});
