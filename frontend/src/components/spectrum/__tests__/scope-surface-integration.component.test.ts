/**
 * MOR-4271 (R2, GREEN-B RED): the four scope canvases — SpectrumCanvas,
 * WaterfallCanvas, AudioSpectrumCanvas and AmberAfScope — migrate onto
 * CanvasSurface + ScopeRenderScheduler. One surface per canvas owns sizing
 * (ResizeObserver ∧ devicePixelRatio ∧ stage scale) and visibility
 * (IntersectionObserver ∧ !document.hidden); one scheduler per canvas paints
 * latest-wins while the surface is active and NEVER requests an animation
 * frame while it is not. SpectrumPanel feeds scopeSubscription() from the
 * spectrum canvas surface facts (width = backingW, visible = facts.visible),
 * so the typed subscription demand follows the real surface.
 *
 * Harness: a real ScopeController over a real WsChannel feeds the REAL
 * ScopeStream (producer v2 golden bytes included), SpectrumPanel mounts
 * with the same runtime seam as SpectrumPanel.component.test.ts, and every
 * browser primitive the components rely on is owned by the test:
 * ResizeObserver (explicit scope-host/canvas sizes, 0x0 first), IntersectionObserver
 * (explicit isIntersecting), a counting rAF queue (explicit flush), a
 * document.hidden getter + visibilitychange, the matchMedia re-arm pattern,
 * and a per-canvas 2D context spy that records draw operations.
 *
 * Acceptance map: (a) sized-before-paint, (b) one draw per animation frame,
 * (c) hidden stops scheduling + maxFps 0 + cache repaint, (d) one backing
 * re-application per DPR change, plus the golden-wire paint sanity.
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { WsChannel } from '$lib/transport/ws-client';
import { controllerClient } from '$lib/transport/controller-client';
import { MockWebSocket, instances as wsInstances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { SpectrumRenderer } from '$lib/renderers/spectrum-renderer';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { ScopeSubscription } from '$lib/runtime/scope-view';
import type { SpectrumAuthority, SpectrumFilterRule } from '$lib/runtime/adapters/scope-adapter';
import type { FilterWidthDisplay, ScopeControlsViewModel } from '../../../semantic/radio-view-model';
import { scopeWire, loadScopeWireGolden } from '../../../lib/runtime/__tests__/scope-wire-fixture';

// ---------------------------------------------------------------------------
// Hoisted harness state (must exist before vi.mock factories run)
// ---------------------------------------------------------------------------

type TestScopeFrame = import('$lib/runtime/scope-frame').ScopeFrame;
type ScopeBridge = {
  stream(source: 'hardware' | 'audio_fft'): ReturnType<ScopeController['stream']>;
  registerPresentationDriver: ScopeController['registerPresentationDriver'];
  subscribeTraceReceipt: ScopeController['subscribeTraceReceipt'];
  snapshotHealth: ScopeController['snapshotHealth'];
  subscribeHealth: ScopeController['subscribeHealth'];
  readonly hardwareScopeConnected: boolean;
  readonly hardwareScopeFrameLive: boolean;
};

const runtimeHarness = vi.hoisted(() => {
  const state = {
    currentState: Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'MAIN' }) as unknown,
    currentCaps: Object.freeze({ source: 'test-capabilities' }) as unknown,
    currentSession: { state: 'connected' as const, epoch: 1 },
    mockScopeConnected: true,
    mockScopeFrameLive: true,
    tuningStep: 1_000,
    dxUnsubscribe: vi.fn(),
    driverBridge: {
      startHardware: (() => Object.freeze({ resource: 'hardware-scope' as const, sessionEpoch: 'test', id: 0 })) as () => unknown,
      stopHardware: (_lease: unknown) => {},
      startAudio: (() => Object.freeze({ resource: 'audio-fft' as const })) as () => unknown,
      stopAudio: (_lease: unknown) => {},
    },
  };
  const runtime = {
    get state() { return state.currentState; },
    get caps() { return state.currentCaps; },
    get controlSession() { return state.currentSession; },
    onTxAudioDied: () => () => {},
    defaultScopeStatus: { transport: 'connected' },
    scope: null as unknown as ScopeBridge,
    acquireHardwareScope: vi.fn(() => state.driverBridge.startHardware()),
    releaseHardwareScope: vi.fn((lease: unknown) => state.driverBridge.stopAudio(lease) as void),
    subscribeDx: vi.fn(() => state.dxUnsubscribe),
    send: vi.fn(),
  };
  return { state, runtime };
});
const mockRuntime = runtimeHarness.runtime;

const hostHarness = vi.hoisted(() => ({ host: null as unknown as PresentationResourceHost<unknown> }));

const appTxHostHarness = vi.hoisted(() => ({
  controller: null as ManagedAppTxController | null,
}));

const authorityHarness = vi.hoisted(() => ({
  state: { current: null as SpectrumAuthority | null },
  toSpectrumAuthority: vi.fn((_state: unknown, _caps: unknown) => authorityHarness.state.current),
  snapSpectrumFilterWidth: vi.fn((
    raw: number,
    rule: SpectrumFilterRule | null,
  ) => {
    if (!rule) return null;
    if (rule.kind === 'table') {
      return rule.values.reduce((best: number, value: number) =>
        Math.abs(raw - value) < Math.abs(raw - best) ? value : best);
    }
    const bounded = Math.max(rule.minHz, Math.min(rule.maxHz, raw));
    const step = rule.kind === 'step' ? rule.stepHz : rule.segments[0].stepHz;
    return rule.minHz + Math.round((bounded - rule.minHz) / step) * step;
  }),
}));

const handlerHarness = vi.hoisted(() => {
  const vfo = Object.freeze({ onFreqChange: vi.fn() });
  const filter = Object.freeze({ onFilterWidthCommit: vi.fn() });
  return {
    vfo,
    filter,
    getVfoHandlers: vi.fn(() => vfo),
    getFilterHandlers: vi.fn(() => filter),
  };
});

const audioPaint = vi.hoisted(() => vi.fn());
const commandHarness = vi.hoisted(() => ({ dispatch: vi.fn() }));

// ---------------------------------------------------------------------------
// Module mocks (components import through these)
// ---------------------------------------------------------------------------

vi.mock('$lib/runtime/frontend-runtime', () => ({
  get presentationResources() { return hostHarness.host; },
  runtime: runtimeHarness.runtime,
}));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => appTxHostHarness.controller,
}));

vi.mock('$lib/runtime/commands/radio-intents', async (original) => ({
  ...await original<typeof import('$lib/runtime/commands/radio-intents')>(),
  dispatchRadioIntent: commandHarness.dispatch,
}));

vi.mock('$lib/runtime/adapters/scope-adapter', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/adapters/scope-adapter')>(),
  toSpectrumAuthority: authorityHarness.toSpectrumAuthority,
  snapSpectrumFilterWidth: authorityHarness.snapSpectrumFilterWidth,
}));

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  return {
    ...actual,
    getVfoHandlers: handlerHarness.getVfoHandlers,
    getFilterHandlers: handlerHarness.getFilterHandlers,
    getFilterWidthCommandLifecycle: () => filterWidthLifecycleState.get('value')!,
    getPendingFrequencyHz: (receiver: 0 | 1) => pendingFrequencyState.get(String(receiver)) ?? null,
    getTuningBurstFrequencyHz: (receiver: 0 | 1) =>
      actual.getTuningBurstFrequencyHz(receiver) ?? tuningBurstState.get(String(receiver)) ?? null,
  };
});

vi.mock('$lib/stores/capabilities.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/capabilities.svelte')>(),
  hasCapability: vi.fn((name: string) => name === 'scope'),
  hasDualReceiver: vi.fn(() => false),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  tuneBy: vi.fn(() => 0),
  getTuningStep: vi.fn(() => runtimeHarness.state.tuningStep),
  adjustTuningStep: vi.fn(),
  isAutoStep: vi.fn(() => true),
  formatStep: vi.fn(() => '100 Hz'),
}));

vi.mock('$lib/stores/radio.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/radio.svelte')>(),
  patchActiveReceiver: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/props/panel-props')>(),
  resolveFilterModeConfig: vi.fn((caps: { filterConfig?: Record<string, unknown> }, mode: string) =>
    caps.filterConfig?.[mode] ?? null),
}));

vi.mock('../passband-geometry', () => ({
  canResizeFromRightEdge: vi.fn((mode: string) => mode.toUpperCase() !== 'LSB'),
  getFilterWidthFromRightEdgePx: vi.fn(() => 2_700),
  getPassbandGeometry: vi.fn(() => null),
}));

vi.mock('../../../components-v2/panels/audio-scope/audio-spectrum-renderer', async (original) => {
  const actual = await original<typeof import('../../../components-v2/panels/audio-scope/audio-spectrum-renderer')>();
  return {
    ...actual,
    renderAudioSpectrum: (...args: Parameters<typeof actual.renderAudioSpectrum>) => {
      audioPaint(...args);
      return actual.renderAudioSpectrum(...args);
    },
  };
});

// ---------------------------------------------------------------------------
// Components under test
// ---------------------------------------------------------------------------

import SpectrumPanel from '../SpectrumPanel.svelte';
import AudioSpectrumPanel from '../../../components-v2/panels/audio-scope/AudioSpectrumPanel.svelte';
import AmberAfScope from '../../../components-v2/panels/lcd/AmberAfScope.svelte';

// ---------------------------------------------------------------------------
// Test-owned browser primitives
// ---------------------------------------------------------------------------

interface ResizeRecord { element: Element; cb: ResizeObserverCallback; disconnected: boolean }
interface IntersectRecord { element: Element; cb: IntersectionObserverCallback; disconnected: boolean }

let resizeObservers: ResizeRecord[] = [];
let intersectionObservers: IntersectRecord[] = [];
const mediaListeners = new Map<string, Set<EventListener>>();

let rafCallbacks = new Map<number, FrameRequestCallback>();
let rafRequestCount = 0;
let nextRafId = 0;
let hidden = false;
let dpr = 1;
let originalHiddenDescriptor: PropertyDescriptor | undefined;
let originalDprDescriptor: PropertyDescriptor | undefined;
const rafStacksWhileHidden: string[] = [];

function flushRaf(): void {
  const pending = [...rafCallbacks.values()];
  rafCallbacks.clear();
  for (const callback of pending) callback(performance.now());
}

/** Drain re-arming loops (the old amber perpetual loop) up to a cap. */
function settleRaf(): void {
  for (let i = 0; i < 100 && rafCallbacks.size > 0; i++) flushRaf();
}

function fireResize(element: Element, width: number, height: number): void {
  for (const observer of resizeObservers) {
    if (observer.disconnected || observer.element !== element) continue;
    observer.cb([{ target: element, contentRect: { width, height } } as ResizeObserverEntry],
      {} as ResizeObserver);
  }
}

function fireIntersection(element: Element, isIntersecting: boolean): void {
  for (const observer of intersectionObservers) {
    if (observer.disconnected || observer.element !== element) continue;
    observer.cb([{ target: element, isIntersecting } as IntersectionObserverEntry],
      {} as IntersectionObserver);
  }
}

function intersectAll(isIntersecting: boolean): void {
  const elements = new Set(intersectionObservers.filter((o) => !o.disconnected).map((o) => o.element));
  for (const element of elements) fireIntersection(element, isIntersecting);
}

function fireMedia(query: string): void {
  for (const listener of mediaListeners.get(query) ?? []) {
    (listener as () => void)();
  }
}

// Per-canvas 2D context spy: records the draw operations of every canvas.
interface CanvasOps {
  clearRect: number;
  fillRect: number;
  putImageData: number;
  drawImage: number;
  strokes: number;
  setTransform: number;
  ctx: CanvasRenderingContext2D;
}
const canvasOps = new Map<HTMLCanvasElement, CanvasOps>();
const zeroOps: CanvasOps = {
  clearRect: 0, fillRect: 0, putImageData: 0, drawImage: 0, strokes: 0, setTransform: 0,
  ctx: undefined as unknown as CanvasRenderingContext2D,
};

function opsFor(canvas: HTMLCanvasElement): CanvasOps {
  // An unsized surface never applies a backing store and never paints, so
  // the canvas may have no recorded context yet — zero ops is the truth.
  return canvasOps.get(canvas) ?? zeroOps;
}

const noop = () => {};
function makeRecordingCtx(canvas: HTMLCanvasElement): CanvasOps {
  const ops: CanvasOps = {
    clearRect: 0, fillRect: 0, putImageData: 0, drawImage: 0, strokes: 0, setTransform: 0,
    ctx: undefined as unknown as CanvasRenderingContext2D,
  };
  const base: Record<string | symbol, unknown> = {
    canvas,
    setTransform: () => { ops.setTransform++; },
    clearRect: () => { ops.clearRect++; },
    fillRect: () => { ops.fillRect++; },
    putImageData: () => { ops.putImageData++; },
    drawImage: () => { ops.drawImage++; },
    beginPath: noop,
    closePath: noop,
    moveTo: noop,
    lineTo: noop,
    stroke: () => { ops.strokes++; },
    fill: noop,
    save: noop,
    restore: noop,
    clip: noop,
    scale: noop,
    rotate: noop,
    translate: noop,
    transform: noop,
    arc: noop,
    arcTo: noop,
    rect: noop,
    quadraticCurveTo: noop,
    measureText: (text: string) => ({ width: String(text).length * 8 }),
    fillText: noop,
    createImageData: (w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
    getImageData: (_x: number, _y: number, w: number, h: number) => ({ data: new Uint8ClampedArray(w * h * 4), width: w, height: h }),
    createLinearGradient: () => ({ addColorStop: noop }),
    createRadialGradient: () => ({ addColorStop: noop }),
    createPattern: () => null,
  };
  ops.ctx = new Proxy(base, {
    get: (target, key) => (key in target ? target[key] : noop),
    set: (target, key, value) => { target[key] = value; return true; },
  }) as unknown as CanvasRenderingContext2D;
  return ops;
}

// Backing-store application counting: one entry per canvas.width write.
const widthSets = new Map<HTMLCanvasElement, number>();
function wrapCanvasWidth(canvas: HTMLCanvasElement): void {
  if (widthSets.has(canvas)) return;
  let value = canvas.width;
  widthSets.set(canvas, 0);
  Object.defineProperty(canvas, 'width', {
    configurable: true,
    enumerable: true,
    get: () => value,
    set: (next: number) => {
      widthSets.set(canvas, (widthSets.get(canvas) ?? 0) + 1);
      value = next;
    },
  });
}

// ---------------------------------------------------------------------------
// Real ScopeController over real WsChannels backed by MockWebSocket
// (the transport seam — producer-derived, no hand-rolled channel fake)
// ---------------------------------------------------------------------------

const SCOPE_URL = '/api/v1/scope';

let harnessChannels: Record<string, WsChannel> = {};
let controller: ScopeController;
let txHarness: ManagedAppTxHarness;

function wireController(): void {
  controller = new ScopeController((name: string) => {
    harnessChannels[name] ??= new WsChannel(false);
    return harnessChannels[name]!;
  });
  controller.stream('hardware').setSource('radio', loadScopeWireGolden('radio.bin').expected.source_id);
  controller.stream('audio_fft').setSource('audio_fft', loadScopeWireGolden('audio_fft.bin').expected.source_id);
  mockRuntime.scope = {
    stream: (source) => controller.stream(source),
    registerPresentationDriver: (host, config) => controller.registerPresentationDriver(host, config),
    subscribeTraceReceipt: (observer) => controller.subscribeTraceReceipt(observer),
    snapshotHealth: (source) => controller.snapshotHealth(source),
    subscribeHealth: (listener) => controller.subscribeHealth(listener),
    get hardwareScopeConnected() {
      return runtimeHarness.state.mockScopeConnected ?? controller.hardwareScopeConnected;
    },
    get hardwareScopeFrameLive() {
      return runtimeHarness.state.mockScopeFrameLive ?? controller.hardwareScopeFrameLive;
    },
  };
  runtimeHarness.state.driverBridge.startHardware = () => controller.hardwareScopeDriver.start();
  runtimeHarness.state.driverBridge.stopHardware = (lease: unknown) => {
    void controller.hardwareScopeDriver.stop(lease);
  };
  runtimeHarness.state.driverBridge.startAudio = () => controller.audioFftDriver.start();
  runtimeHarness.state.driverBridge.stopAudio = (lease: unknown) => {
    void controller.audioFftDriver.stop(lease);
  };
  mockRuntime.acquireHardwareScope.mockImplementation(() => runtimeHarness.state.driverBridge.startHardware());
  mockRuntime.releaseHardwareScope.mockImplementation((lease: unknown) => {
    runtimeHarness.state.driverBridge.stopHardware(lease);
  });
}

// ---------------------------------------------------------------------------
// Frame delivery
// ---------------------------------------------------------------------------

type EmitOverrides = Partial<Omit<TestScopeFrame, 'pixels'>> & { pixels?: Uint8Array | null };

/** Both source kinds use the controller's one real scope channel. */
function scopeSocket(): MockWebSocket {
  const socket = wsInstances.find((candidate) => candidate.url.includes(SCOPE_URL));
  if (!socket) throw new Error('no scope socket opened — did the driver connect?');
  return socket;
}

/** Deliver producer-derived v2 hardware bytes through the real channel. */
function emitFrame(overrides: EmitOverrides = {}): void {
  scopeSocket().simulateMessage(scopeWire({
    source: 'radio', sourceId: loadScopeWireGolden('radio.bin').expected.source_id, receiver: 0,
    viewStartHz: 14_000_000, viewEndHz: 14_100_000,
    pixels: new Uint8Array(475).fill(64), ...overrides,
  }));
  flushSync();
}

/** Deliver producer-derived v2 audio-FFT bytes through the same channel. */
function emitAudioFrame(overrides: EmitOverrides = {}): void {
  scopeSocket().simulateMessage(scopeWire({
    source: 'audio_fft', sourceId: loadScopeWireGolden('audio_fft.bin').expected.source_id, receiver: null,
    viewStartHz: 0, viewEndHz: 48_000,
    pixels: new Uint8Array(475).fill(64), ...overrides,
  }));
  flushSync();
}

function deliverGoldenBytes(): void {
  const radio = loadScopeWireGolden('radio.bin');
  const audio = loadScopeWireGolden('audio_fft.bin');
  // Node's fs buffer has a different realm; WebSocket dispatch recognizes
  // the test realm's ArrayBuffer, with the producer bytes copied unchanged.
  scopeSocket().simulateMessage(new Uint8Array(new Uint8Array(radio.buffer)).buffer);
  flushSync();
  scopeSocket().simulateMessage(new Uint8Array(new Uint8Array(audio.buffer)).buffer);
  flushSync();
}

/** Both demanded sources report health from the same connected channel. */
async function openChannels(): Promise<void> {
  scopeSocket().simulateOpen();
  await vi.waitFor(() => {
    expect(harnessChannels.scope!.state).toBe('connected');
    expect(controller.snapshotHealth('hardware').transport).toBe('connected');
    expect(controller.snapshotHealth('audio_fft').transport).toBe('connected');
  });
  expect(wsInstances).toHaveLength(1);
}

// ---------------------------------------------------------------------------
// Authority + filter lifecycle fixtures (same contract as the panel tests)
// ---------------------------------------------------------------------------

const defaultRule: SpectrumFilterRule = Object.freeze({
  kind: 'step', minHz: 100, maxHz: 5_000, stepHz: 100,
});

function testScopeControls(modeValue = 2): ScopeControlsViewModel {
  const field = <T,>(value: T) => Object.freeze({
    reading: Object.freeze({ status: 'known' as const, value }),
    availability: Object.freeze({ structural: true, operational: true }),
  });
  return Object.freeze({
    mode: field(modeValue),
    edge: field(1),
    span: field(3),
    speed: field(0),
    hold: field(false),
    refDb: field(0),
    refRange: null,
    dual: field(false),
    receiver: field(0),
    duringTx: field(false),
    centerType: field(0),
    vbwNarrow: field(false),
    rbw: field(0),
  });
}

function authority(overrides: Partial<Omit<SpectrumAuthority, 'digest'>> = {}): SpectrumAuthority {
  const core = {
    providerGeneration: 17,
    receiver: 0 as const,
    frequencyHz: 14_050_250,
    mode: 'USB',
    filter: 'FIL1',
    filterWidthHz: 2_400,
    filterWidthDisplay: { state: 'known', hz: 2_400, stale: false } as FilterWidthDisplay,
    filterShape: 1,
    ifShiftHz: 0,
    pbtInnerHz: 0,
    pbtOuterHz: 0,
    dataMode: 0,
    rule: defaultRule,
    scopeControls: testScopeControls(),
    ...overrides,
  };
  return Object.freeze({ ...core, digest: JSON.stringify(core) });
}

const filterWidthLifecycleState = new SvelteMap<string, unknown>([['value', Object.freeze({
  confirmed: 2_400, target: null, phase: 'idle', busy: false, outcome: null, presentation: null,
})]]);
const pendingFrequencyState = new SvelteMap<string, number | null>([['0', null], ['1', null]]);
const tuningBurstState = new SvelteMap<string, number | null>([['0', null], ['1', null]]);

// ---------------------------------------------------------------------------
// Mount helpers
// ---------------------------------------------------------------------------

const SPECTRUM_SIZE = { w: 400, h: 160 };
const WATERFALL_SIZE = { w: 400, h: 120 };
const AUDIO_SIZE = { w: 360, h: 140 };
const AMBER_SIZE = { w: 320, h: 160 };

let components: ReturnType<typeof mount>[] = [];
let panelTarget: HTMLDivElement;
let audioTarget: HTMLDivElement;
let amberTarget: HTMLDivElement;
let stopAmberStream: (() => void) | null = null;
let amberPush: ((pixels: Uint8Array) => void) | null = null;
let spectrumRenderSpy: Mock;
let waterfallPushSpy: Mock;

function mountPanel(): HTMLDivElement {
  panelTarget = document.createElement('div');
  document.body.appendChild(panelTarget);
  components.push(mount(SpectrumPanel, { target: panelTarget, props: {} }));
  flushSync();
  return panelTarget;
}

function mountAudioPanel(): HTMLDivElement {
  audioTarget = document.createElement('div');
  document.body.appendChild(audioTarget);
  components.push(mount(AudioSpectrumPanel, { target: audioTarget, props: {} }));
  flushSync();
  return audioTarget;
}

function mountAmber(): HTMLDivElement {
  amberTarget = document.createElement('div');
  document.body.appendChild(amberTarget);
  components.push(mount(AmberAfScope, {
    target: amberTarget,
    props: {
      data: null,
      mode: 'fill',
      filterWidth: 2_400,
      filterWidthMax: 3_600,
      onRegisterPush: (fn: (pixels: Uint8Array) => void) => { amberPush = fn; },
    },
  }));
  flushSync();
  // Forward audio-FFT stream frames into the amber canvas exactly the way the
  // LCD skin wires it: one stream sink pushes into the registered callback.
  stopAmberStream = controller.stream('audio_fft').subscribe((frame: TestScopeFrame) => {
    if (frame.pixels) amberPush?.(frame.pixels);
  });
  return amberTarget;
}

function canvases(): { spectrum: HTMLCanvasElement; waterfall: HTMLCanvasElement; audio: HTMLCanvasElement; amber: HTMLCanvasElement } {
  const spectrum = panelTarget.querySelector<HTMLCanvasElement>('.spectrum-area canvas')!;
  const waterfall = panelTarget.querySelector<HTMLCanvasElement>('.waterfall-content canvas')!;
  const audioCanvas = audioTarget.querySelector<HTMLCanvasElement>('canvas')!;
  const amber = amberTarget.querySelector<HTMLCanvasElement>('canvas')!;
  for (const canvas of [spectrum, waterfall, audioCanvas, amber]) {
    if (!canvas) throw new Error('a mounted canvas is missing');
    wrapCanvasWidth(canvas);
  }
  return { spectrum, waterfall, audio: audioCanvas, amber };
}

function canvasesSafe(): Partial<Record<'spectrum' | 'waterfall' | 'audio' | 'amber', HTMLCanvasElement>> {
  return {
    spectrum: panelTarget?.querySelector<HTMLCanvasElement>('.spectrum-area canvas') ?? undefined,
    waterfall: panelTarget?.querySelector<HTMLCanvasElement>('.waterfall-content canvas') ?? undefined,
    audio: audioTarget?.querySelector<HTMLCanvasElement>('canvas') ?? undefined,
    amber: amberTarget?.querySelector<HTMLCanvasElement>('canvas') ?? undefined,
  };
}

/** Mount everything and hold the audio/amber canvases at 0x0. Frames are
 *  delivered by the caller AFTER openChannels() — the controller only
 *  accepts them on a connected transport. */
function mountAllUnsized(): void {
  mountPanel();
  mountAudioPanel();
  mountAmber();
  intersectAll(true);
  fireResize(panelTarget.querySelector('.spectrum-area')!, 0, 0);
  const early = canvasesSafe();
  if (early.audio) fireResize(early.audio, 0, 0);
  if (early.amber) fireResize(early.amber, 0, 0);
}

/** The persistent spectrum host and first-frame waterfall remain at 0x0. */
function holdPanelCanvasesAtZero(): void {
  intersectAll(true);
  const found = canvases();
  fireResize(panelTarget.querySelector('.spectrum-area')!, 0, 0);
  fireResize(found.waterfall, 0, 0);
}

function sizeAllReal(): void {
  const found = canvases();
  fireResize(panelTarget.querySelector('.spectrum-area')!, SPECTRUM_SIZE.w, SPECTRUM_SIZE.h);
  fireResize(found.waterfall, WATERFALL_SIZE.w, WATERFALL_SIZE.h);
  fireResize(found.audio, AUDIO_SIZE.w, AUDIO_SIZE.h);
  fireResize(found.amber, AMBER_SIZE.w, AMBER_SIZE.h);
  flushSync();
}

interface PaintCounts { spectrum: number; waterfall: number; audio: number; amber: number }
function paintCounts(): PaintCounts {
  const found = canvases();
  return {
    spectrum: spectrumRenderSpy.mock.calls.length,
    waterfall: waterfallPushSpy.mock.calls.length,
    audio: audioPaint.mock.calls.length,
    amber: opsFor(found.amber).clearRect,
  };
}

/** The panel serializes its scopeSubscription() result verbatim — parse it
 *  back into the producer's type, not a hand-shaped subset. */
function readSubscription(): ScopeSubscription {
  return JSON.parse(panelTarget.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!) as ScopeSubscription;
}

/** Inspect the actual v2 demand serialized by the real controller/channel. */
function readSentSubscription(sourceId: string): ScopeSubscription {
  const subscriptions = scopeSocket().sent.map(message => JSON.parse(message))
    .filter((message: ScopeSubscription & { type: string }) => message.type === 'subscribe' && message.sourceId === sourceId);
  expect(subscriptions.length).toBeGreaterThan(0);
  return subscriptions.at(-1)! as ScopeSubscription;
}

// ---------------------------------------------------------------------------

beforeEach(() => {
  vi.stubGlobal('ResizeObserver', class {
    private readonly cb: ResizeObserverCallback;
    constructor(cb: ResizeObserverCallback) { this.cb = cb; }
    observe(element: Element) { resizeObservers.push({ element, cb: this.cb, disconnected: false }); }
    unobserve() {}
    disconnect() {
      const record = resizeObservers.find((observer) => observer.cb === this.cb);
      if (record) record.disconnected = true;
    }
  });
  vi.stubGlobal('IntersectionObserver', class {
    private readonly cb: IntersectionObserverCallback;
    constructor(cb: IntersectionObserverCallback) { this.cb = cb; }
    observe(element: Element) { intersectionObservers.push({ element, cb: this.cb, disconnected: false }); }
    unobserve() {}
    disconnect() {
      const record = intersectionObservers.find((observer) => observer.cb === this.cb);
      if (record) record.disconnected = true;
    }
  });
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafRequestCount++;
    rafCallbacks.set(++nextRafId, callback);
    return nextRafId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
  // Real WsChannel objects open real (mock) WebSockets through the global
  // constructor; scope channels are plain local sockets (no controller
  // session handshake).
  wsInstances.length = 0;
  vi.stubGlobal('WebSocket', MockWebSocket);
  vi.spyOn(controllerClient, 'sessionMode', 'get').mockReturnValue(false);
  vi.spyOn(controllerClient, 'protocols').mockReturnValue([]);
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: false,
    media: query,
    addEventListener: (_type: string, listener: EventListener) => {
      const set = mediaListeners.get(query) ?? new Set<EventListener>();
      set.add(listener);
      mediaListeners.set(query, set);
    },
    removeEventListener: (_type: string, listener: EventListener) => {
      mediaListeners.get(query)?.delete(listener);
    },
  }));

  originalHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  originalDprDescriptor = Object.getOwnPropertyDescriptor(window, 'devicePixelRatio');
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, get: () => dpr });

  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (
    this: HTMLCanvasElement,
    type: string,
  ) {
    if (type !== '2d') return null;
    let ops = canvasOps.get(this);
    if (!ops) {
      ops = makeRecordingCtx(this);
      canvasOps.set(this, ops);
    }
    return ops.ctx;
  });

  Element.prototype.scrollIntoView = vi.fn();
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();

  resizeObservers = [];
  intersectionObservers = [];
  mediaListeners.clear();
  rafCallbacks = new Map();
  rafRequestCount = 0;
  nextRafId = 0;
  hidden = false;
  dpr = 1;
  canvasOps.clear();
  widthSets.clear();
  harnessChannels = {};
  components = [];
  stopAmberStream = null;
  amberPush = null;

  spectrumRenderSpy = vi.spyOn(SpectrumRenderer.prototype, 'render');
  waterfallPushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
  audioPaint.mockClear();

  wireController();
  hostHarness.host = new PresentationResourceHost('mor4271');
  hostHarness.host.configure('audio-fft', { available: true, selected: true, driver: controller.audioFftDriver });
  controller.registerPresentationDriver(hostHarness.host);

  txHarness = new ManagedAppTxHarness();
  appTxHostHarness.controller = txHarness.controller;

  runtimeHarness.state.currentState = Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'MAIN' });
  runtimeHarness.state.currentCaps = Object.freeze({ source: 'test-capabilities' });
  runtimeHarness.state.currentSession = { state: 'connected', epoch: 1 };
  runtimeHarness.state.mockScopeConnected = true;
  runtimeHarness.state.mockScopeFrameLive = true;
  authorityHarness.state.current = authority();
});

afterEach(async () => {
  for (const component of components.splice(0).reverse()) await unmount(component);
  stopAmberStream?.();
  stopAmberStream = null;
  await hostHarness.host.teardown();
  for (const name of Object.keys(harnessChannels)) harnessChannels[name]!.disconnect();
  document.body.innerHTML = '';
  appTxHostHarness.controller = null;
  if (originalHiddenDescriptor) Object.defineProperty(document, 'hidden', originalHiddenDescriptor);
  if (originalDprDescriptor) Object.defineProperty(window, 'devicePixelRatio', originalDprDescriptor);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

// ---------------------------------------------------------------------------
// (a) mount at 0x0 paints nothing; the first real size paints each canvas once
// ---------------------------------------------------------------------------

describe('scope canvases on CanvasSurface + ScopeRenderScheduler (MOR-4271)', () => {
  it('(a) mount at 0x0 then sized paints once without a window resize', async () => {
    mountAllUnsized();
    await openChannels();
    emitFrame();
    holdPanelCanvasesAtZero();
    emitAudioFrame();

    // Deliveries while every canvas reports 0x0: nothing may paint.
    emitFrame({ seq: 2 });
    emitFrame({ seq: 3 });
    emitAudioFrame({ seq: 2 });
    flushRaf();
    expect(paintCounts()).toEqual({ spectrum: 0, waterfall: 0, audio: 0, amber: 0 });

    // The first real size (a ResizeObserver delivery, not a window event)
    // wakes each scheduler exactly once and each canvas paints exactly once
    // from the frames it already accepted.
    sizeAllReal();
    const before = paintCounts();
    flushRaf();
    const after = paintCounts();
    expect(after.spectrum - before.spectrum).toBe(1);
    expect(after.waterfall - before.waterfall).toBe(1);
    expect(after.audio - before.audio).toBe(1);
    expect(after.amber - before.amber).toBe(1);
  });

  // -------------------------------------------------------------------------
  // (b) sixty frames inside one animation frame: one draw, one rAF each
  // -------------------------------------------------------------------------

  it('(b) 60 frames in one animation frame produce exactly one draw per canvas', async () => {
    mountAllUnsized();
    await openChannels();
    emitFrame();
    holdPanelCanvasesAtZero();
    emitAudioFrame();
    sizeAllReal();
    emitFrame({ seq: 10 });
    emitAudioFrame({ seq: 10 });
    // Drain re-arming loops (the old amber perpetual loop re-arms on every
    // tick, so "settled" here only means: one flush consumes everything
    // currently queued). Assertions below are deltas around the burst.
    settleRaf();

    const requestsBefore = rafRequestCount;
    const paintsBefore = paintCounts();
    for (let sequence = 11; sequence <= 70; sequence++) {
      emitFrame({ seq: sequence });
      emitAudioFrame({ seq: sequence });
    }
    // Latest-wins: each canvas requested exactly one animation frame for the
    // whole burst — no per-frame rAF, and the idle amber scope requested its
    // frame from the burst wake, not from a perpetual loop.
    expect(rafRequestCount - requestsBefore).toBe(4);
    flushRaf();
    const paints = paintCounts();
    expect(paints.spectrum - paintsBefore.spectrum).toBe(1);
    expect(paints.waterfall - paintsBefore.waterfall).toBe(1);
    expect(paints.audio - paintsBefore.audio).toBe(1);
    expect(paints.amber - paintsBefore.amber).toBe(1);
  });

  // -------------------------------------------------------------------------
  // (c) hidden: no scheduling, maxFps 0, cache repaint on restore
  // -------------------------------------------------------------------------

  it('(c) hidden stops all scheduling, the subscription reports maxFps 0, and visibility restores from cache', async () => {
    mountAllUnsized();
    await openChannels();
    emitFrame();
    holdPanelCanvasesAtZero();
    emitAudioFrame();
    sizeAllReal();
    emitFrame({ seq: 20 });
    emitAudioFrame({ seq: 20 });
    settleRaf();

    // While visible the demand carries the spectrum backing width and the
    // panel's configured frame rate.
    const visibleSubscription = readSubscription();
    expect(visibleSubscription.width).toBe(canvases().spectrum.width);
    expect(visibleSubscription.maxFps).toBe(20);
    expect(readSentSubscription(visibleSubscription.sourceId)).toMatchObject(visibleSubscription);

    hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    settleRaf(); // drain in-flight ticks; the schedulers must not re-arm

    const requestsWhileHiddenBaseline = rafRequestCount;
    const paintsBeforeHidden = paintCounts();
    for (let sequence = 21; sequence <= 40; sequence++) {
      emitFrame({ seq: sequence });
      emitAudioFrame({ seq: sequence });
    }
    flushRaf();
    // Hidden: not one animation frame was requested and nothing painted —
    // on ANY canvas.
    if (rafRequestCount - requestsWhileHiddenBaseline !== 0) {
      console.log('RAF-WHILE-HIDDEN STACKS:\n' + rafStacksWhileHidden.join('\n---\n'));
    }
    expect(rafRequestCount - requestsWhileHiddenBaseline).toBe(0);
    expect(paintCounts()).toEqual(paintsBeforeHidden);
    // The typed demand reports zero frame rate while hidden...
    expect(readSubscription().maxFps).toBe(0);
    expect(readSubscription().width).toBe(canvases().spectrum.width);
    expect(readSentSubscription(visibleSubscription.sourceId).maxFps).toBe(0);
    const audioDemand = scopeSocket().sent.map(message => JSON.parse(message))
      .filter((message: { sourceId?: string }) => message.sourceId === controller.stream('audio_fft').sourceId).at(-1);
    expect(audioDemand?.type === 'unsubscribe' || (audioDemand?.type === 'subscribe' && audioDemand.maxFps === 0)).toBe(true);

    hidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    // ...and the visibility wake repaints exactly once per canvas FROM THE
    // CACHED LATEST FRAME — no new frame is delivered here.
    const paintsBeforeRestore = paintCounts();
    flushRaf();
    const paintsAfterRestore = paintCounts();
    expect(paintsAfterRestore.spectrum - paintsBeforeRestore.spectrum).toBe(1);
    expect(paintsAfterRestore.waterfall - paintsBeforeRestore.waterfall).toBe(1);
    expect(paintsAfterRestore.audio - paintsBeforeRestore.audio).toBe(1);
    expect(paintsAfterRestore.amber - paintsBeforeRestore.amber).toBe(1);
    const restoredSubscription = readSubscription();
    expect(restoredSubscription.maxFps).toBe(20);
    expect(restoredSubscription.width).toBe(canvases().spectrum.width);
    expect(readSentSubscription(restoredSubscription.sourceId)).toMatchObject(restoredSubscription);
  });

  // -------------------------------------------------------------------------
  // (d) one backing-store re-application per canvas on a DPR change
  // -------------------------------------------------------------------------

  it('(d) a devicePixelRatio change re-applies each backing store exactly once', async () => {
    mountAllUnsized();
    await openChannels();
    emitFrame();
    holdPanelCanvasesAtZero();
    emitAudioFrame();
    const found = canvases(); // wraps canvas.width counting before sizing
    sizeAllReal();
    emitFrame({ seq: 30 });
    emitAudioFrame({ seq: 30 });
    settleRaf();
    for (const canvas of [found.spectrum, found.waterfall, found.audio, found.amber]) {
      expect(widthSets.get(canvas)).toBeGreaterThan(0);
    }

    const before = new Map(widthSets);
    dpr = 2;
    fireMedia('(resolution: 1dppx)');
    flushSync();
    settleRaf();
    for (const canvas of [found.spectrum, found.waterfall, found.audio, found.amber]) {
      expect(widthSets.get(canvas)! - before.get(canvas)!).toBe(1);
    }
    expect(found.spectrum.width).toBe(SPECTRUM_SIZE.w * 2);
    expect(found.waterfall.width).toBe(WATERFALL_SIZE.w * 2);
    expect(found.audio.width).toBe(AUDIO_SIZE.w * 2);
    expect(found.amber.width).toBe(AMBER_SIZE.w * 2);
  });

  // -------------------------------------------------------------------------
  // Golden wire sanity: producer bytes paint unchanged through the surfaces
  // -------------------------------------------------------------------------

  it('integration sanity: golden wire frames paint with the fixture pixels unchanged', async () => {
    mountAllUnsized();
    await openChannels();
    deliverGoldenBytes();
    holdPanelCanvasesAtZero();
    sizeAllReal();
    deliverGoldenBytes();
    settleRaf();

    const spectrumPixels = spectrumRenderSpy.mock.calls.at(-1)![1] as Uint8Array;
    expect([...spectrumPixels]).toEqual(loadScopeWireGolden('radio.bin').expected.pixels);
    const waterfallPixels = waterfallPushSpy.mock.calls.at(-1)![0] as Uint8Array;
    expect([...waterfallPixels]).toEqual(loadScopeWireGolden('radio.bin').expected.pixels);
    expect(audioPaint).toHaveBeenCalled();
    const audioArgs = audioPaint.mock.calls.at(-1)!;
    expect([...(audioArgs[3] as { pixels: Uint8Array }).pixels]).toEqual(loadScopeWireGolden('audio_fft.bin').expected.pixels);
    // The amber scope consumed the same audio bytes (paint recorded).
    expect(paintCounts().amber).toBeGreaterThan(0);
  });
});
