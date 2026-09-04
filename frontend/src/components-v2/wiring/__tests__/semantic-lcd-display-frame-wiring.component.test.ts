import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createRawSnippet, flushSync, type Snippet,
} from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type {
  LcdSpectrumFrame, LcdSpectrumFrameResolution, LcdSpectrumSource,
} from '../../../skins/segmentline/lcd-display-contract';

type Authority = Readonly<{
  source: LcdSpectrumSource;
  receiver: 'MAIN' | 'SUB' | null;
  providerGeneration: number | null;
}>;

const h = vi.hoisted(() => ({
  events: [] as string[],
  hostInstances: [] as Array<{
    authorities: Array<Authority | null>;
    emit: (resolution: LcdSpectrumFrameResolution) => void;
    dispose: () => void;
  }>,
  acquire: vi.fn((resource: string) => {
    h.events.push(`acquire:${resource}`);
    return Object.freeze({ resource, id: h.events.length });
  }),
  release: vi.fn((lease: { resource: string }) => {
    h.events.push(`release:${lease.resource}`);
    return true;
  }),
  subscribeCount: 0,
  unsubscribeCount: 0,
  disposeCount: 0,
  frameGetter: null as null | (() => unknown),
  viewGetter: null as null | (() => unknown),
  renderCount: 0,
  rawAudioFrame: Object.freeze({ pixels: new Uint8Array([255, 0]), startFreq: 1, endFreq: 2 }),
  rawHardwareFrame: Object.freeze({ pixels: new Uint8Array([0, 255]), startFreq: 3, endFreq: 4 }),
  txController: null as unknown,
}));

vi.mock('$lib/runtime/scope-frame-host', () => ({
  ScopeFrameHost: class FakeScopeFrameHost {
    readonly authorities: Array<Authority | null> = [];
    private resolution: LcdSpectrumFrameResolution = { state: 'ghost', reason: 'missing' };
    private listener: ((resolution: LcdSpectrumFrameResolution) => void) | null = null;
    private disposed = false;

    constructor(_scope: unknown) {
      h.hostInstances.push(this);
    }

    updateAuthority(authority: Authority | null): void {
      this.authorities.push(authority);
      this.resolution = { state: 'ghost', reason: authority ? 'missing' : 'receiver-unknown' };
      this.listener?.(this.resolution);
    }

    snapshot(): LcdSpectrumFrameResolution {
      return this.resolution;
    }

    subscribe(listener: (resolution: LcdSpectrumFrameResolution) => void): () => void {
      this.listener = listener;
      h.subscribeCount += 1;
      let active = true;
      return () => {
        if (!active) return;
        active = false;
        this.listener = null;
        h.unsubscribeCount += 1;
      };
    }

    emit(resolution: LcdSpectrumFrameResolution): void {
      this.resolution = resolution;
      this.listener?.(resolution);
    }

    dispose(): void {
      if (this.disposed) return;
      this.disposed = true;
      h.disposeCount += 1;
      this.listener = null;
    }
  },
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
      get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
      get connectionAudio() { return false; },
      get defaultScopeStatus() {
        return {
          source: null, available: false, resourceSelected: false, demand: 0,
          lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
        };
      },
      get radioPowerOn() { return null; },
      get scope() {
        return {
          hardwareScopeConnected: false,
          audioScopeFrame: h.rawAudioFrame,
          scopeFrame: h.rawHardwareFrame,
        };
      },
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
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
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

const readonlyDisplay = createRawSnippet<[RadioViewModel, LcdSpectrumFrame?]>(
  (view, frame) => {
    h.viewGetter = view;
    h.frameGetter = frame ?? (() => undefined);
    h.renderCount += 1;
    return { render: () => '<output data-testid="lcd-display"></output>' };
  },
) as Snippet<[RadioViewModel, LcdSpectrumFrame?]>;

const audioFrame: LcdSpectrumFrame = Object.freeze({
  source: 'audio-fft', receiver: 'MAIN', freshness: 'fresh',
  startHz: 14_000_000, endHz: 14_100_000, normalizedBins: Object.freeze([0, 0.5, 1]),
});
const hardwareFrame: LcdSpectrumFrame = Object.freeze({
  source: 'hardware', receiver: 'SUB', freshness: 'fresh',
  startHz: 7_000_000, endHz: 7_100_000, normalizedBins: Object.freeze([1, 0.5, 0]),
});

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
  h.hostInstances.length = 0;
  h.acquire.mockClear();
  h.release.mockClear();
  h.subscribeCount = 0;
  h.unsubscribeCount = 0;
  h.disposeCount = 0;
  h.frameGetter = null;
  h.viewGetter = null;
  h.renderCount = 0;
});

afterEach(() => {
  component?.$destroy();
  component = null;
  flushSync();
  resetRadioState();
  clearCapabilities();
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

describe('MOR-2329 semantic LCD display-frame binding', () => {
  it('owns one selected-source lease, preserves live identity, and switches sources without fallback', () => {
    render('audio-fft');

    expect(h.hostInstances).toHaveLength(1);
    const host = h.hostInstances[0];
    expect(host.authorities.at(-1)).toEqual({
      source: 'audio-fft', receiver: 'MAIN', providerGeneration: 7,
    });
    expect(h.events).toEqual(['acquire:audio-fft']);
    expect(h.frameGetter?.()).toBeUndefined();

    host.emit({ state: 'live', frame: audioFrame });
    flushSync();
    expect(h.frameGetter?.()).toBe(audioFrame);

    host.emit({ state: 'ghost', reason: 'stale' });
    flushSync();
    expect(h.frameGetter?.()).toBeUndefined();
    expect(h.events).toEqual(['acquire:audio-fft']);

    component?.$set({ displayFrameSource: 'hardware' });
    flushSync();
    expect(h.events).toEqual([
      'acquire:audio-fft', 'release:audio-fft', 'acquire:hardware-scope',
    ]);
    expect(h.hostInstances).toHaveLength(1);
    expect(host.authorities.at(-1)).toEqual({
      source: 'hardware', receiver: 'MAIN', providerGeneration: 7,
    });
    expect(h.frameGetter?.()).toBeUndefined();

    host.emit({ state: 'live', frame: hardwareFrame });
    flushSync();
    expect(h.frameGetter?.()).toBe(hardwareFrame);
    expect(h.acquire.mock.calls.map(([resource]) => resource)).toEqual([
      'audio-fft', 'hardware-scope',
    ]);
  });

  it('updates canonical authority without reacquiring demand and fails closed on disagreement', () => {
    render('hardware');
    const host = h.hostInstances[0];
    expect(h.events).toEqual(['acquire:hardware-scope']);

    radio.current = liveState(8, 'SUB');
    flushSync();
    expect(host.authorities.at(-1)).toBeNull();
    expect(h.events).toEqual(['acquire:hardware-scope']);

    expect(setCapabilities(liveCaps(8))).toBe(true);
    flushSync();
    expect(host.authorities.at(-1)).toEqual({
      source: 'hardware', receiver: 'SUB', providerGeneration: 8,
    });
    expect(h.events).toEqual(['acquire:hardware-scope']);

    for (const reason of ['receiver-mismatch', 'source-mismatch', 'stale', 'missing'] as const) {
      host.emit({ state: 'ghost', reason });
      flushSync();
      expect(h.frameGetter?.()).toBeUndefined();
      expect(h.events).toEqual(['acquire:hardware-scope']);
    }
  });

  it('acquires no source for the shared readonly path and tears a defined source down exactly once', () => {
    render();
    expect(h.hostInstances).toHaveLength(0);
    expect(h.events).toEqual([]);
    expect(h.frameGetter?.()).toBeUndefined();

    component?.$destroy();
    component = null;
    const dualComponent = render(undefined, 'dual');
    expect(h.hostInstances).toHaveLength(0);
    expect(h.events).toEqual([]);

    dualComponent.$set({ displayFrameSource: 'audio-fft' });
    flushSync();
    expect(h.hostInstances).toHaveLength(1);
    expect(h.events).toEqual(['acquire:audio-fft']);

    dualComponent.$destroy();
    component = null;
    flushSync();
    expect(h.events).toEqual(['acquire:audio-fft', 'release:audio-fft']);
    expect(h.subscribeCount).toBe(1);
    expect(h.unsubscribeCount).toBe(1);
    expect(h.disposeCount).toBe(1);
  });

  it('contains no raw-frame bypass or transport/channel authority', () => {
    const source = readFileSync(resolve(
      process.cwd(), 'src/components-v2/wiring/SemanticRadioSurfaces.svelte',
    ), 'utf8');
    expect(source).not.toMatch(/audioScopeFrame|scopeFrame\b|\$lib\/transport|getChannel|onBinary/);
    expect(source.match(/new ScopeFrameHost\(/g)).toHaveLength(1);
    expect(source).toContain("resolution.state === 'live' ? resolution.frame : undefined");
  });
});
