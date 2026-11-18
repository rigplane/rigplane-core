import { get } from 'svelte/store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScopeController } from '../scope-controller.svelte';
import { scopeWire } from './scope-wire-fixture';
import type { WsChannel } from '$lib/transport/ws-client';
import { scopeSubscriptionSurface } from '../scope-view';
import type { CanvasSurfaceFacts } from '../../canvas/canvas-surface.svelte';

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
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('scope subscription retirement', () => {
  it('mounts and repeatedly unmounts an undemanded phone surface without channel writes', async () => {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    const handle = await owner.audioFftDriver.start(); change('connected');
    const node = document.createElement('div');
    const facts: CanvasSurfaceFacts = { cssW: 390, cssH: 50, backingW: 390, backingH: 50,
      pixelScale: 1, visible: true };
    const surface = scopeSubscriptionSurface(node, { stream: owner.stream('hardware'),
      receiver: 0, view: request('radio', 0).view, facts });
    expect(owner.stream('hardware').subscriptions).toHaveLength(1);
    expect(port.sendRaw).not.toHaveBeenCalled();
    surface.destroy(); surface.destroy();
    expect(port.sendRaw).not.toHaveBeenCalled();
    await owner.audioFftDriver.stop(handle);
  });

  it.each(['disconnected', 'refused'] as const)('does not retire a %s subscription that was never sent', async state => {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    owner.stream('hardware').setSubscription(request('radio', 0));
    if (state === 'refused') port.sendRaw.mockReturnValue(false);
    const handle = await owner.hardwareScopeDriver.start();
    if (state === 'refused') change('connected');
    port.sendRaw.mockClear();
    await owner.hardwareScopeDriver.stop(handle);
    await owner.hardwareScopeDriver.stop(handle);
    expect(port.sendRaw).not.toHaveBeenCalled();
  });

  it.each(['surface-first', 'driver-first'] as const)('sends exactly one unsubscribe in %s teardown order', async order => {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    const keepOpen = await owner.audioFftDriver.start(); change('connected');
    const stream = owner.stream('hardware'), token = Symbol('surface');
    stream.updateSurfaceSubscription(token, request('radio', 0));
    const handle = await owner.hardwareScopeDriver.start();
    expect(port.sendRaw).toHaveBeenCalledExactlyOnceWith({ type: 'subscribe', ...request('radio', 0) });
    if (order === 'driver-first') await owner.hardwareScopeDriver.stop(handle);
    stream.removeSurfaceSubscription(token); stream.removeSurfaceSubscription(token);
    await owner.hardwareScopeDriver.stop(handle); await owner.hardwareScopeDriver.stop(handle);
    expect(port.sendRaw.mock.calls.map(call => call[0])).toEqual([
      { type: 'subscribe', ...request('radio', 0) },
      { type: 'unsubscribe', sourceId: 'radio', receiver: 0 },
    ]);
    await owner.audioFftDriver.stop(keepOpen);
  });
});

describe('one multiplexed subscribed scope channel', () => {
  it('never re-sends a pinned (absolute) view because the confirmed frequency moved', async () => {
    const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
    const token = Symbol('surface'), stream = owner.stream('hardware');
    const demand = { ...request('radio', 0), view: { startHz: 14_225_000, endHz: 14_275_000 } };
    stream.updateSurfaceSubscription(token, demand, true, 'initial');
    const handle = await owner.hardwareScopeDriver.start(); change('connected');
    // Frames that do and do not cover the whole pinned view: a retune re-sends in neither case.
    port.receive(scopeWire({ sourceId: 'radio', receiver: 0, viewStartHz: 14_225_000, viewEndHz: 14_275_000 }));
    port.receive(scopeWire({ sourceId: 'radio', receiver: 0, viewStartHz: 14_225_000, viewEndHz: 14_275_000,
      coverageStartHz: 14_225_000, coverageEndHz: 14_250_000 }));
    port.sendRaw.mockClear();
    for (const revision of ['geometry', 'confirmed', 'frame']) stream.updateSurfaceSubscription(token, demand, false, revision);
    expect(port.sendRaw).not.toHaveBeenCalled();
    stream.removeSurfaceSubscription(token); await owner.hardwareScopeDriver.stop(handle);
  });
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

  it('re-sends a tagged subscription on reconnect as requestSeq 0, which is not a request (MOR-4532)', async () => {
    const { port, change } = channel(); const owner = new ScopeController(() => port as unknown as WsChannel);
    const hardware = owner.stream('hardware');
    hardware.setSubscription({ ...request('radio', 0), view: { follow: 'vfo', spanHz: 200_000 }, requestSeq: 3 });
    const handle = await owner.hardwareScopeDriver.start();
    change('connected');
    expect(owner.connectionEpoch).toBe(1);
    change('disconnected');
    expect(owner.connectionEpoch).toBe(0);
    port.sendRaw.mockClear();
    change('connected');
    expect(owner.connectionEpoch).toBe(2);
    const resent = port.sendRaw.mock.calls.map(call => call[0]).filter(message => message.type === 'subscribe');
    expect(resent).toEqual([{ type: 'subscribe', ...request('radio', 0), view: { follow: 'vfo', spanHz: 200_000 }, requestSeq: 0 }]);
    await owner.hardwareScopeDriver.stop(handle);
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

describe('MOR-4534: a presentation swap subscribes before it unsubscribes', () => {
  const facts: CanvasSurfaceFacts = { cssW: 1400, cssH: 200, backingW: 2800, backingH: 400, pixelScale: 2, visible: true };
  const view = { follow: 'vfo' as const, spanHz: 20_000 };
  async function mounted() {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    const handle = await owner.hardwareScopeDriver.start(); change('connected');
    const outgoing = scopeSubscriptionSurface(document.createElement('div'),
      { stream: owner.stream('hardware'), receiver: 0, view, facts, requestSeq: 3 });
    port.sendRaw.mockClear();
    const sent = () => port.sendRaw.mock.calls.map(call => call[0] as Record<string, unknown>);
    return { port, owner, handle, outgoing, sent };
  }
  /** The incoming panel mounts before its canvas is measured. */
  const incoming = (owner: ScopeController) => scopeSubscriptionSurface(document.createElement('div'),
    { stream: owner.stream('hardware'), receiver: 0, view, facts: null, requestSeq: 0 });

  it('keeps the outgoing request on the wire until the incoming panel sends its own', async () => {
    const { owner, handle, outgoing, sent } = await mounted();
    const release = owner.holdSurfaces();
    outgoing.destroy();
    const next = incoming(owner);
    release();
    expect(sent()).toEqual([]);
    next.update({ stream: owner.stream('hardware'), receiver: 0, view, facts, requestSeq: 0 });
    expect(sent()).toEqual([{ type: 'subscribe', sourceId: 'radio', receiver: 0, view, width: 2800, maxFps: 20, requestSeq: 0 }]);
    next.destroy();
    expect(sent().at(-1)).toEqual({ type: 'unsubscribe', sourceId: 'radio', receiver: 0 });
    await owner.hardwareScopeDriver.stop(handle);
  });

  it.each(['alone', 'beside an unmeasured panel'] as const)('must-not: the last measured panel leaving outside a swap unsubscribes at once (%s)', async company => {
    const { owner, handle, outgoing, sent } = await mounted();
    const other = company === 'alone' ? null : incoming(owner);
    outgoing.destroy();
    expect(sent()).toEqual([{ type: 'unsubscribe', sourceId: 'radio', receiver: 0 }]);
    other?.destroy();
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('sends the incoming request at once when the incoming panel is measured within the swap', async () => {
    const { owner, handle, outgoing, sent } = await mounted();
    const release = owner.holdSurfaces();
    outgoing.destroy();
    const next = incoming(owner);
    next.update({ stream: owner.stream('hardware'), receiver: 0, view, facts, requestSeq: 0 });
    expect(sent()).toEqual([{ type: 'subscribe', sourceId: 'radio', receiver: 0, view, width: 2800, maxFps: 20, requestSeq: 0 }]);
    release();
    expect(sent()).toHaveLength(1);
    next.destroy();
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('unsubscribes when the swap ends without an incoming panel', async () => {
    const { owner, handle, outgoing, sent } = await mounted();
    const release = owner.holdSurfaces();
    outgoing.destroy();
    expect(sent()).toEqual([]);
    release();
    expect(sent()).toEqual([{ type: 'unsubscribe', sourceId: 'radio', receiver: 0 }]);
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('unsubscribes when the incoming panel leaves before it sends a request', async () => {
    const { owner, handle, outgoing, sent } = await mounted();
    const release = owner.holdSurfaces();
    outgoing.destroy();
    const next = incoming(owner);
    release();
    expect(sent()).toEqual([]);
    next.destroy();
    expect(sent()).toEqual([{ type: 'unsubscribe', sourceId: 'radio', receiver: 0 }]);
    await owner.hardwareScopeDriver.stop(handle);
  });
});

describe('MOR-4534: a mounting surface continues the follow request on its key', () => {
  const facts: CanvasSurfaceFacts = { cssW: 400, cssH: 100, backingW: 800, backingH: 200, pixelScale: 2, visible: true };
  async function connected() {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    const handle = await owner.hardwareScopeDriver.start(); change('connected');
    const sent = () => port.sendRaw.mock.calls.map(call => call[0] as Record<string, unknown>);
    return { port, change, owner, handle, sent };
  }

  it('a passive radio follow surface sends requestSeq 0 with the newest request width', async () => {
    const { port, owner, handle, sent } = await connected();
    const stream = owner.stream('hardware');
    const chosen = scopeSubscriptionSurface(document.createElement('div'),
      { stream, receiver: 0, view: { follow: 'vfo', spanHz: 200_000 }, facts, requestSeq: 3 });
    expect(stream.followContext(0)).toEqual({ spanHz: 200_000, requestSeq: 3 });
    port.sendRaw.mockClear();
    const release = owner.holdSurfaces();
    chosen.destroy();
    const passive = scopeSubscriptionSurface(document.createElement('div'),
      { stream, receiver: 0, view: { follow: 'vfo', spanHz: 100_000 }, facts });
    release();
    expect(sent()).toEqual([{ type: 'subscribe', sourceId: 'radio', receiver: 0,
      view: { follow: 'vfo', spanHz: 200_000 }, width: 800, maxFps: 20, requestSeq: 0 }]);
    passive.destroy();
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('with no follow request on the key a passive surface keeps its own width, still at requestSeq 0', async () => {
    const { owner, handle, sent } = await connected();
    const passive = scopeSubscriptionSurface(document.createElement('div'),
      { stream: owner.stream('hardware'), receiver: 0, view: { follow: 'vfo', spanHz: 100_000 }, facts });
    expect(sent()).toEqual([{ type: 'subscribe', sourceId: 'radio', receiver: 0,
      view: { follow: 'vfo', spanHz: 100_000 }, width: 800, maxFps: 20, requestSeq: 0 }]);
    passive.destroy();
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('a passive request never replaces the user request it continues, and a new session keeps only the width', async () => {
    const { change, owner, handle } = await connected();
    const stream = owner.stream('hardware');
    const chosen = scopeSubscriptionSurface(document.createElement('div'),
      { stream, receiver: 0, view: { follow: 'vfo', spanHz: 500_000 }, facts, requestSeq: 4 });
    const passive = scopeSubscriptionSurface(document.createElement('div'),
      { stream, receiver: 0, view: { follow: 'vfo', spanHz: 100_000 }, facts });
    expect(stream.followContext(0)).toEqual({ spanHz: 500_000, requestSeq: 4 });
    change('disconnected'); change('connected');
    expect(stream.followContext(0)).toEqual({ spanHz: 500_000, requestSeq: 0 });
    passive.destroy(); chosen.destroy();
    await owner.hardwareScopeDriver.stop(handle);
  });

  it('audio surfaces stay untagged', async () => {
    const { port, change } = channel();
    const owner = new ScopeController(() => port as unknown as WsChannel);
    const handle = await owner.audioFftDriver.start(); change('connected');
    const surface = scopeSubscriptionSurface(document.createElement('div'),
      { stream: owner.stream('audio_fft'), receiver: null, view: { follow: 'vfo', spanHz: 48_000 }, facts });
    expect(port.sendRaw).toHaveBeenCalled();
    expect(port.sendRaw.mock.calls.every(call => !('requestSeq' in (call[0] as object)))).toBe(true);
    surface.destroy();
    await owner.audioFftDriver.stop(handle);
  });
});
