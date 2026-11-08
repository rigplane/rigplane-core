import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createRawSnippet, flushSync, type Snippet,
} from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { LcdSpectrumSource } from '../../../skins/segmentline/lcd-display-contract';
import type { ScopeStream } from '$lib/runtime/scope-stream';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import type { WsChannel } from '$lib/transport/ws-client';


const h = vi.hoisted(() => ({
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: { state: 'connected'; epoch: 1 };
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: true, rxEnabled: false, volume: 0 },
  events: [] as string[],
  scope: null as unknown as ScopeController, resources: null as unknown as PresentationResourceHost<unknown>,
  acquire: vi.fn((resource: string) => {
    h.events.push(`acquire:${resource}`);
    return h.resources.acquire(resource as 'hardware-scope' | 'audio-fft', 'test');
  }),
  release: vi.fn((lease: { resource: string }) => {
    h.events.push(`release:${lease.resource}`);
    return h.resources.release(lease as never);
  }),
  frameGetter: null as null | (() => unknown),
  viewGetter: null as null | (() => unknown),
  renderCount: 0,
  txController: null as unknown,
}));


vi.mock('$lib/runtime', async () => {
  const { radio } = await import('$lib/stores/radio.svelte');
  const { getCapabilities } = await import('$lib/stores/capabilities.svelte');
  return {
    presentationResources: {
      acquire: h.acquire,
      release: h.release,
    },
    runtime: {
      onTxAudioDied: () => () => {},
      get state() { return radio.current; },
      get caps() { return getCapabilities(); },
      subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
        h.authoritySubscribers.add(handler);
        handler({
          state: radio.current, caps: getCapabilities(), session: { state: 'connected', epoch: 1 },
          rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
        });
        return () => { h.authoritySubscribers.delete(handler); };
      },
      get audio() { return h.audio; },
      get connectionAudio() { return false; },
      get defaultScopeStatus() {
        return {
          source: null, available: false, resourceSelected: false, demand: 0,
          lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
        };
      },
      get radioPowerOn() { return null; },
      get scope() { return h.scope; },
    },
  };
});

vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (h.txController === null) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));

import { radio, resetRadioState } from '$lib/stores/radio.svelte';
import { clearCapabilities, getCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

function liveState(providerGeneration = 7, active: 'MAIN' | 'SUB' = 'MAIN'): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const vfo of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${vfo}.freqHz`, `${rx}.${vfo}.mode`, `${rx}.${vfo}.filterNum`);
    }
  }
  const receiver = (frequency: number) => ({
    ...slot(frequency), vfoA: slot(frequency), vfoB: slot(frequency + 50_000),
    activeSlot: 'A', filter: 1,
  });
  return {
    providerGeneration, active, split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: active, slot: 'A', frequencyHz: 14_250_000 },
    main: receiver(14_250_000), sub: receiver(14_300_000),
    fieldStatus: Object.fromEntries(paths.map((path) => [path, fresh])),
  } as unknown as ServerState;
}

function liveCaps(providerGeneration = 7): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration,
    model: 'fixture', scope: true, audio: true, tx: true,
    capabilities: ['audio', 'scope', 'tx', 'dual_rx'],
    receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: [],
    scopeSource: 'hardware', audioFftAvailable: true,
  } as Capabilities;
}

const readonlyDisplay = createRawSnippet<[RadioViewModel, ScopeStream | null]>(
  (view, stream) => {
    h.viewGetter = view;
    h.frameGetter = stream;
    h.renderCount += 1;
    return { render: () => '<output data-testid="lcd-display"></output>' };
  },
) as Snippet<[RadioViewModel, ScopeStream | null]>;


function publishAuthority(): void {
  const next = {
    state: radio.current, caps: getCapabilities(),
    session: { state: 'connected' as const, epoch: 1 as const },
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  };
  for (const subscriber of h.authoritySubscribers) subscriber(next);
}

let target: HTMLDivElement;
let component: ReturnType<typeof createClassComponent> | null = null;
let txHarness: ManagedAppTxHarness;

function render(
  source?: LcdSpectrumSource,
  strips: 'single' | 'dual' = 'single',
): ReturnType<typeof createClassComponent> {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = createClassComponent({
    component: SemanticRadioSurfaces,
    target,
    props: { strips, readonlyDisplay, displayFrameSource: source },
  });
  flushSync();
  return component;
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  radio.current = liveState();
  expect(setCapabilities(liveCaps())).toBe(true);
  h.events.length = 0;
channels = { scope: channel() };
h.scope = new ScopeController(name => channels[name as keyof typeof channels] as unknown as WsChannel);
h.resources = new PresentationResourceHost('test');
h.resources.configure('hardware-scope', { available: true, selected: true, driver: h.scope.hardwareScopeDriver });
h.resources.configure('audio-fft', { available: true, selected: true, driver: h.scope.audioFftDriver });

  h.acquire.mockClear();
  h.release.mockClear();
  h.frameGetter = null;
  h.viewGetter = null;
  h.renderCount = 0;
});

afterEach(async () => {
  component?.$destroy();
  component = null;
  flushSync();
  await h.resources.teardown();
  expect(h.authoritySubscribers.size).toBe(0);
  resetRadioState();
  clearCapabilities();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

function channel() {
  let state = 'disconnected', epoch = 0;
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(next: { state: string; epoch: number }) => void>();
  function transition(next: string) {
    if (next === 'connected' && state !== 'connected') epoch += 1;
    state = next; states.forEach(fn => fn(state)); sessions.forEach(fn => fn({ state, epoch }));
  }
  return {
    get state() { return state; }, get sessionEpoch() { return epoch; },
    connect: vi.fn(() => transition('connecting')), disconnect: vi.fn(() => transition('disconnected')),
    connected() { transition('connected'); },
    sendRaw: vi.fn(() => true), onMessage: () => () => {},
    onBinary(fn: (buffer: ArrayBuffer) => void) { binary.add(fn); return () => { binary.delete(fn); }; },
    onStateChange(fn: (state: string) => void) { states.add(fn); return () => { states.delete(fn); }; },
    onSessionTransition(fn: (next: { state: string; epoch: number }) => void) { sessions.add(fn); return () => { sessions.delete(fn); }; },
    count: () => binary.size,
    callbacks: () => [...binary],
    frame(receiver: number | null = 0, values = [0, 128, 255], callbacks = [...binary], source: 'radio' | 'audio_fft' = 'radio') {
      const buffer = scopeWire({ source, sourceId: source === 'radio' ? 'radio' : 'audio_fft', receiver, pixels: new Uint8Array(values), viewStartHz: 14_000_000, viewEndHz: 14_100_000 });
      callbacks.forEach(fn => fn(buffer)); flushSync(); return buffer;
    },
    audioFrame(receiver: number | null = null, values = [0, 128, 255], callbacks = [...binary]) { return this.frame(receiver, values, callbacks, 'audio_fft'); },
  };
}
let channels: Record<'scope', ReturnType<typeof channel>>;
async function settle() { await Promise.resolve(); flushSync(); await Promise.resolve(); }
const selected = () => h.frameGetter?.() as ScopeStream | null;
const geometry = (stream: ScopeStream) => {
  let value: unknown;
  const stop = stream.geometry.subscribe(next => { value = next; }); stop(); return value;
};

describe('MOR-2329 semantic LCD stream binding', () => {
  it('leaves an independently demanded live stream and its authority untouched for a scope-free mount', async () => {
    h.scope.setFrameAuthority({ source: 'hardware', receiver: 0, providerGeneration: 7 });
    const lease = h.resources.acquire('hardware-scope', 'independent');
    await settle(); channels.scope.connected(); const buffer = channels.scope.frame();
    const stream = h.scope.stream('hardware');
    const before = geometry(stream);
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    const streams = vi.spyOn(h.scope, 'stream');
    render(); await settle();
    expect(authority).not.toHaveBeenCalled(); expect(streams).not.toHaveBeenCalled();
    expect(geometry(stream)).toBe(before);
    expect(stream.latest('radio:0')?.pixels?.buffer).toBe(buffer);
    component!.$destroy(); component = null; flushSync(); await settle();
    expect(authority).not.toHaveBeenCalled(); expect(streams).not.toHaveBeenCalled();
    expect(geometry(stream)).toBe(before); expect(channels.scope.count()).toBe(1);
    expect(channels.scope.disconnect).not.toHaveBeenCalled();
    h.resources.release(lease);
  });
  it('qualifies an audio-only peer as a genuine consumer and fences prior-provider audio callbacks', async () => {
    render(); component!.$set({ displayAudioScope: true }); flushSync(); await settle();
    const audio = h.scope.stream('audio_fft');
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    expect(selected()).toBe(audio);
    expect(h.events).toEqual(['acquire:audio-fft']);
    expect(channels.scope.connect).toHaveBeenCalledTimes(1);
    channels.scope.connected(); channels.scope.audioFrame();
    expect(geometry(audio)).toMatchObject({ source: 'audio_fft', receiver: null });
    const previous = channels.scope.callbacks();
    radio.current = liveState(8); expect(setCapabilities(liveCaps(8))).toBe(true);
    publishAuthority(); flushSync(); await settle();
    expect(authority.mock.lastCall).toEqual([{ source: 'audio_fft', receiver: null, providerGeneration: 8 }, []]);
    channels.scope.audioFrame(null, [9, 9, 9], previous);
    expect(geometry(audio)).toBeNull();
    expect(channels.scope.connect).toHaveBeenCalledTimes(2);
    channels.scope.connected(); channels.scope.audioFrame();
    expect(geometry(audio)).toMatchObject({ source: 'audio_fft', receiver: null, freshness: 'fresh' });
    expect(h.events).toEqual(['acquire:audio-fft']);
  });
  it('releases only its own scope lease on unmount while an independent live consumer retains geometry', async () => {
    render('hardware'); await settle();
    const independent = h.resources.acquire('hardware-scope', 'independent');
    channels.scope.connected(); const buffer = channels.scope.frame();
    const stream = h.scope.stream('hardware'), before = geometry(stream);
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    component!.$destroy(); component = null; flushSync(); await settle();
    expect(authority).not.toHaveBeenCalled();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
    expect(geometry(stream)).toBe(before);
    expect(stream.latest('radio:0')?.pixels?.buffer).toBe(buffer);
    expect(channels.scope.count()).toBe(1); expect(channels.scope.disconnect).not.toHaveBeenCalled();
    h.resources.release(independent);
  });
  it('owns one selected-source lease, preserves stream/pixel identity, and switches sources without fallback', async () => {
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    render('audio-fft'); await settle();
    const audio = h.scope.stream('audio_fft');
    expect(selected()).toBe(audio);
    expect(authority.mock.lastCall).toEqual([{ source: 'audio_fft', receiver: null, providerGeneration: 7 }, []]);
    expect(h.events).toEqual(['acquire:audio-fft']); expect(geometry(audio)).toBeNull();
    channels.scope.connected(); const bytes = channels.scope.audioFrame();
    expect(audio.latest('audio_fft:*')?.pixels?.buffer).toBe(bytes);
    expect(audio.latest('audio_fft:*')?.pixels).toEqual(new Uint8Array([0, 128, 255]));
    audio.setFreshness('stale'); flushSync();
    expect(geometry(audio)).toMatchObject({ freshness: 'stale' });
    expect(selected()).toBe(audio); expect(h.events).toEqual(['acquire:audio-fft']);
    component!.$set({ displayFrameSource: 'hardware' }); flushSync(); await settle();
    const hardware = h.scope.stream('hardware');
    expect(selected()).toBe(hardware);
    expect(h.events).toEqual(['acquire:audio-fft', 'release:audio-fft', 'acquire:hardware-scope']);
    expect(authority.mock.lastCall).toEqual([{ source: 'hardware', receiver: 0, providerGeneration: 7 }, []]);
    expect(geometry(hardware)).toBeNull();
    channels.scope.connected(); const hardwareBytes = channels.scope.frame();
    expect(hardware.latest('radio:0')?.pixels?.buffer).toBe(hardwareBytes);
    expect(h.acquire.mock.calls.map(([resource]) => resource)).toEqual(['audio-fft', 'hardware-scope']);
    expect(channels.scope.count()).toBe(1);
  });
  it('updates canonical authority without reacquiring demand and fails closed on disagreement', async () => {
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    render('hardware'); await settle(); channels.scope.connected(); channels.scope.frame();
    const stream = selected()!;
    expect(h.events).toEqual(['acquire:hardware-scope']);
    radio.current = liveState(8, 'SUB'); publishAuthority(); flushSync();
    expect(authority.mock.lastCall).toEqual([null, []]); expect(geometry(stream)).toBeNull();
    expect(h.events).toEqual(['acquire:hardware-scope']);
    expect(setCapabilities(liveCaps(8))).toBe(true); publishAuthority(); flushSync(); await settle();
    expect(authority.mock.lastCall).toEqual([{ source: 'hardware', receiver: 1, providerGeneration: 8 }, []]);
    expect(h.events).toEqual(['acquire:hardware-scope']);
    channels.scope.connected(); channels.scope.frame(0);
    expect(geometry(stream)).toBeNull();
    channels.scope.frame(1); expect(geometry(stream)).toMatchObject({ receiver: 1, freshness: 'fresh' });
    channels.scope.audioFrame(1); expect(geometry(stream)).toMatchObject({ source: 'radio', receiver: 1 });
    stream.setFreshness('stale'); flushSync(); expect(geometry(stream)).toMatchObject({ freshness: 'stale' });
    stream.reset(); flushSync(); expect(geometry(stream)).toBeNull();
    expect(h.events).toEqual(['acquire:hardware-scope']);
  });
  it('acquires no source for readonly mounts and tears a defined source down exactly once', async () => {
    render(); expect(selected()).toBeNull(); expect(h.events).toEqual([]);
    component!.$destroy(); component = null;
    render(undefined, 'dual'); expect(selected()).toBeNull(); expect(h.events).toEqual([]);
    component!.$set({ displayFrameSource: 'audio-fft' }); flushSync(); await settle();
    expect(selected()).toBe(h.scope.stream('audio_fft')); expect(h.events).toEqual(['acquire:audio-fft']);
    channels.scope.connected(); expect(channels.scope.count()).toBe(1);
    component!.$destroy(); component = null; flushSync(); await settle();
    expect(h.events).toEqual(['acquire:audio-fft', 'release:audio-fft']);
    expect(channels.scope.count()).toBe(0); expect(channels.scope.disconnect).toHaveBeenCalledOnce();
  });
  it('owns simultaneous RF and AF demand only when an audio inset is declared', async () => {
    render('hardware'); component!.$set({ displayAudioScope: true }); flushSync(); await settle();
    expect(h.events).toEqual(['acquire:hardware-scope', 'acquire:audio-fft']);
    channels.scope.connected();
    channels.scope.frame(); channels.scope.audioFrame(null, [255, 128, 0]);
    expect(h.scope.stream('hardware').latest('radio:0')?.pixels).toEqual(new Uint8Array([0, 128, 255]));
    expect(h.scope.stream('audio_fft').latest('audio_fft:*')?.pixels).toEqual(new Uint8Array([255, 128, 0]));
    const oldRf = channels.scope.callbacks(), oldAudio = channels.scope.callbacks();
    radio.current = liveState(8); expect(setCapabilities(liveCaps(8))).toBe(true);
    publishAuthority(); flushSync(); await settle();
    expect(geometry(h.scope.stream('hardware'))).toBeNull();
    expect(geometry(h.scope.stream('audio_fft'))).toBeNull();
    channels.scope.frame(0, [9, 9, 9], oldRf); channels.scope.audioFrame(null, [9, 9, 9], oldAudio);
    expect(geometry(h.scope.stream('hardware'))).toBeNull();
    expect(geometry(h.scope.stream('audio_fft'))).toBeNull();
    expect(channels.scope.connect).toHaveBeenCalledTimes(2);
    expect(channels.scope.connect).toHaveBeenCalledTimes(2);
    channels.scope.connected();
    channels.scope.frame(); channels.scope.audioFrame(null, [255, 128, 0]);
    expect(geometry(h.scope.stream('hardware'))).toMatchObject({ source: 'radio', freshness: 'fresh' });
    expect(geometry(h.scope.stream('audio_fft'))).toMatchObject({ source: 'audio_fft', freshness: 'fresh' });
    component!.$set({ displayAudioScope: false }); flushSync(); await settle();
    expect(h.events).toEqual(['acquire:hardware-scope', 'acquire:audio-fft', 'release:audio-fft']);
    expect(channels.scope.count()).toBe(1);
  });
  it('contains no raw-frame bypass, pixel store, or replacement frame host', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/components-v2/wiring/SemanticRadioSurfaces.svelte'), 'utf8');
    expect(source).not.toMatch(/audioScopeFrame|scopeFrame\b|\$lib\/transport|getChannel|onBinary/);
    expect(source).not.toMatch(/FrameHost\b|lcdFrame|\.pixels\b|stream\.subscribe\(/);
    expect(source).toContain('runtime.scope.setFrameAuthority');
    expect(source).toContain('stream.geometry.subscribe');
  });
});
