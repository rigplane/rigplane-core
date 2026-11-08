import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '../scope-controller.svelte';
import { scopeWire } from './scope-wire-fixture';
import type { WsChannel } from '$lib/transport/ws-client';

function channel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(session: { state: string; epoch: number }) => void>();
  const messages = new Set<(message: unknown) => void>();
  let state = 'disconnected'; let epoch = 0;
  const port = {
    get state() { return state; }, get sessionEpoch() { return epoch; },
    connect: vi.fn((_url: string) => {}), disconnect: vi.fn(() => { state = 'disconnected'; }),
    sendRaw: vi.fn((_message: Record<string, unknown>) => true),
    onBinary: vi.fn((sink: (buffer: ArrayBuffer) => void) => { binary.add(sink); return () => binary.delete(sink); }),
    onStateChange(sink: (state: string) => void) { states.add(sink); return () => states.delete(sink); },
    onSessionTransition(sink: (session: { state: string; epoch: number }) => void) { sessions.add(sink); return () => sessions.delete(sink); },
    onMessage(sink: (message: unknown) => void) { messages.add(sink); return () => messages.delete(sink); },
    change(next: string) {
      state = next; if (state === 'connected') epoch++;
      states.forEach(sink => sink(state)); sessions.forEach(sink => sink({ state, epoch }));
    },
    receive(buffer: ArrayBuffer) { binary.forEach(sink => sink(buffer)); },
    message(message: unknown) { messages.forEach(sink => sink(message)); },
  };
  // Keep a getter distinct from the state-transition helper.
  return { port, change: port.change };
}
const request = (sourceId: string, receiver: number | null) => ({ sourceId, receiver,
  view: { follow: 'vfo' as const, spanHz: 100_000 }, width: 2800, maxFps: 20 });
afterEach(() => vi.restoreAllMocks());

describe('one multiplexed subscribed scope channel', () => {
  it('opens once, routes radio/audio zero-copy, pauses hidden demand, and releases one source at a time', async () => {
    const { port, change } = channel(); const factory = vi.fn(() => port as unknown as WsChannel);
    const owner = new ScopeController(factory);
    const hardware = owner.stream('hardware'); const audio = owner.stream('audio_fft');
    hardware.setSubscription(request('radio', 0)); audio.setSubscription(request('audio_fft', null));
    const radioSink = vi.fn(); const audioSink = vi.fn(); hardware.subscribe(radioSink); audio.subscribe(audioSink);
    const first = await owner.hardwareScopeDriver.start(); const second = await owner.audioFftDriver.start();
    expect(factory).toHaveBeenCalledExactlyOnceWith('scope');
    expect(port.connect).toHaveBeenCalledExactlyOnceWith('/api/v1/scope');
    change('connected');
    expect(port.sendRaw.mock.calls.map(call => call[0])).toEqual([
      { type: 'subscribe', ...request('radio', 0) }, { type: 'subscribe', ...request('audio_fft', null) },
    ]);
    const radio = scopeWire({ sourceId: 'radio' });
    const fft = scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null });
    port.receive(radio); port.receive(fft);
    expect(radioSink).toHaveBeenCalledTimes(1); expect(audioSink).toHaveBeenCalledTimes(1);
    expect(radioSink.mock.lastCall?.[0].pixels.buffer).toBe(radio);
    expect(audioSink.mock.lastCall?.[0].pixels.buffer).toBe(fft);
    audio.setSubscription({ ...request('audio_fft', null), maxFps: 0 });
    port.receive(fft); expect(audioSink).toHaveBeenCalledTimes(1);
    await owner.hardwareScopeDriver.stop(first);
    expect(port.sendRaw).toHaveBeenLastCalledWith({ type: 'unsubscribe', sourceId: 'radio', receiver: 0 });
    expect(port.disconnect).not.toHaveBeenCalled();
    await owner.audioFftDriver.stop(second);
    expect(port.disconnect).toHaveBeenCalledTimes(1);
  });
  it('resends each current subscription on reconnect, invalidates old callbacks and routes targeted errors', async () => {
    const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
    const hardware = owner.stream('hardware'); const audio = owner.stream('audio_fft');
    hardware.setSubscription(request('radio', 0)); audio.setSubscription(request('audio_fft', null));
    owner.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 1 }, ['audio_fft']);
    const first = await owner.hardwareScopeDriver.start(); const second = await owner.audioFftDriver.start();
    change('connected'); port.receive(scopeWire({ sourceId: 'radio' }));
    const retired = port.onBinary.mock.lastCall![0];
    owner.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 2 }, ['audio_fft']);
    retired(scopeWire({ sourceId: 'radio' })); expect(hardware.latest('radio:0')).toBeNull();
    change('connected');
    port.message({ type: 'error', sourceId: 'audio_fft', receiver: null, message: 'mapping unavailable' });
    expect(get(audio.error)).toBe('mapping unavailable'); expect(get(hardware.error)).toBeNull();
    change('disconnected'); change('connected');
    expect(port.sendRaw.mock.calls.filter(call => call[0].type === 'subscribe')).toHaveLength(6);
    await owner.hardwareScopeDriver.stop(first); await owner.audioFftDriver.stop(second);
  });
});

it('retires observed mode at provider/source boundaries and accepts the next qualified control fact', async () => {
  const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
  const hardware = owner.stream('hardware');
  owner.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 1 });
  hardware.setMode(2);
  const handle = await owner.hardwareScopeDriver.start(); change('connected');
  port.receive(scopeWire({ sourceId: 'radio' }));
  expect(get(hardware.geometry)?.mode).toBe(2);
  owner.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 2 });
  change('connected'); port.receive(scopeWire({ sourceId: 'radio' }));
  expect(get(hardware.geometry)?.mode).toBe(0);
  hardware.setMode(3); expect(get(hardware.geometry)?.mode).toBe(3);
  hardware.setSource('iq_fft', 'iq_fft');
  port.receive(scopeWire({ source: 'iq_fft', sourceId: 'iq_fft', receiver: null }));
  expect(get(hardware.geometry)?.mode).toBe(0);
  await owner.hardwareScopeDriver.stop(handle);
});

it('preserves an explicit IQ default even when radio is also discoverable', () => {
  const { port } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
  owner.configureSources(['radio', 'iq_fft', 'audio_fft'], 'iq_fft');
  expect(owner.stream('hardware').kind).toBe('iq_fft');
  expect(owner.stream('hardware').sourceId).toBe('iq_fft');
});

it('aggregates panorama and dedicated AF demand on one configured audio target', async () => {
  const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
  owner.configureSources(['radio', 'audio_fft', 'iq_fft'], 'audio_fft');
  const audio = owner.stream('audio_fft'), main = Symbol('panorama'), af = Symbol('dedicated AF');
  audio.updateSurfaceSubscription(main, { ...request('audio_fft', null), width: 2800 });
  audio.updateSurfaceSubscription(af, { ...request('audio_fft', null), width: 224,
    view: { follow: 'vfo', spanHz: 4800 } });
  const mainSink = vi.fn(), afSink = vi.fn(); audio.subscribe(mainSink); audio.subscribe(afSink);
  const handle = await owner.audioFftDriver.start(); change('connected');
  expect(port.connect).toHaveBeenCalledExactlyOnceWith('/api/v1/scope');
  expect(audio.subscriptions).toEqual([{ ...request('audio_fft', null), width: 2800 }]);
  port.message({ type: 'granted', sourceId: 'audio_fft', receiver: null, width: 2049, fps: 20,
    view: { startHz: 14_026_000, endHz: 14_074_000 }, coverage: { startHz: 14_026_000, endHz: 14_074_000 } });
  const buffer = scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, pixels: new Uint8Array(2049) });
  port.receive(buffer);
  expect(get(audio.granted)?.width).toBe(2049);
  expect(mainSink.mock.lastCall?.[0].pixels.buffer).toBe(buffer);
  expect(afSink.mock.lastCall?.[0].pixels.buffer).toBe(buffer);
  audio.removeSurfaceSubscription(main);
  expect(port.sendRaw).toHaveBeenLastCalledWith({ type: 'subscribe', ...request('audio_fft', null),
    width: 224, view: { follow: 'vfo', spanHz: 4800 } });
  expect(port.disconnect).not.toHaveBeenCalled();
  port.receive(buffer); expect(afSink).toHaveBeenCalledTimes(2);
  audio.removeSurfaceSubscription(af);
  expect(port.sendRaw).toHaveBeenLastCalledWith({ type: 'unsubscribe', sourceId: 'audio_fft', receiver: null });
  await owner.audioFftDriver.stop(handle);
});

it.each(['iq_fft', 'audio_fft'] as const)('does not apply native radio scope mode to %s geometry', async source => {
  const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
  const stream = owner.stream(source === 'audio_fft' ? 'audio_fft' : 'hardware');
  stream.setSource(source, source); stream.setMode(3);
  stream.setSubscription(request(source, null));
  const driver = source === 'audio_fft' ? owner.audioFftDriver : owner.hardwareScopeDriver;
  const handle = await driver.start(); change('connected');
  port.receive(scopeWire({ source, sourceId: source, receiver: null }));
  expect(get(stream.geometry)?.mode).toBe(0);
  await driver.stop(handle);
});
