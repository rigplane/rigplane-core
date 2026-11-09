import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { recordCanvasLayers } from '$lib/renderers/__tests__/canvas-layers-fixture';
/**
 * SpectrumPanel component-level render tests.
 * Mounts the actual Svelte component in jsdom and verifies DOM structure,
 * child component slots, and event wiring.
 * MOR-4241 product-seam coverage: semantic-scope-projection.component.test.ts,
 * paired with Python's test_scope_health_watchdog_mor3690.py::
 * test_scope_ctr_control_and_scope_wire_replay using shared wire documents.
 */
import { describe, it, expect, vi, beforeEach, afterEach, type Mock } from 'vitest';
import { createRawSnippet, mount, unmount, flushSync } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import * as svelteClient from 'svelte/internal/client';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { scopeWire, audioScopeGoldenNames, loadAudioScopeGolden } from '../../../lib/runtime/__tests__/scope-wire-fixture';
import { EMPTY_SCOPE_PASSBAND_DISPLAY, projectScopePassbandDisplay,
  type ScopePassbandDisplay, type ScopePassbandDisplayInput } from '$lib/runtime/adapters/scope-passband-display';
import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
import { ScopeController, type ScopeSpanControl } from '$lib/runtime/scope-controller.svelte';
import { validateCapabilities } from '$lib/types/capabilities';
import spanCapabilities from '../../../../../tests/fixtures/scope-span-capabilities.json';
import { WsChannel } from '$lib/transport/ws-client';
import { controllerClient } from '$lib/transport/controller-client';
import PanadapterDisplay from '../../../skins/segmentline/PanadapterDisplay.svelte';
import { projectPeerSplitDisplay } from '../../../semantic/radio-display-model';
import AudioSpectrumPanel from '../../../components-v2/panels/audio-scope/AudioSpectrumPanel.svelte';
import VfoSurface from '../../../semantic/VfoSurface.svelte';
import {
  type ScopeFrame, type SpectrumAuthority, type SpectrumFilterRule } from '$lib/runtime/adapters/scope-adapter';
import type { FilterWidthDisplay, ScopeControlsViewModel } from '../../../semantic/radio-view-model';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ScopeRenderTrace, type TraceReceipt } from '$lib/runtime/scope-render-trace';
import { radio } from '$lib/stores/radio.svelte';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';

const panelEffects = vi.hoisted(() => ({ next: 0, runs: new Map<number, number>(), origins: new Map<number, string>() }));
const realUserEffect = svelteClient.user_effect;
vi.spyOn(svelteClient, 'user_effect').mockImplementation((fn: () => void) => {
  const caller = new Error().stack?.split('\n').find((line) => line.includes('.svelte:')) ?? '';
  if (!caller.includes('SpectrumPanel.svelte')) return realUserEffect(fn);
  const id = ++panelEffects.next;
  panelEffects.runs.set(id, 0);
  panelEffects.origins.set(id, caller);
  return realUserEffect(() => {
    panelEffects.runs.set(id, (panelEffects.runs.get(id) ?? 0) + 1);
    return fn();
  });
});

// ---------------------------------------------------------------------------
// Global mocks (must be before component import)
// ---------------------------------------------------------------------------

// Canvas 2D context — noop stub so <canvas> calls don't throw in jsdom
const noop = () => {};
const noopCanvas: Record<string, unknown> = {
  fillRect: noop,
  fillText: vi.fn(),
  quadraticCurveTo: noop,
  clearRect: noop,
  getImageData: (_sx: number, _sy: number, w: number, h: number) => ({
    data: new Uint8ClampedArray(w * h * 4),
    width: w,
    height: h,
  }),
  createImageData: (w: number, h: number) => ({
    data: new Uint8ClampedArray(w * h * 4),
    width: w,
    height: h,
  }),
  putImageData: noop,
  drawImage: noop,
  createLinearGradient: () => ({ addColorStop: noop }),
  createRadialGradient: () => ({ addColorStop: noop }),
  createPattern: () => null,
  beginPath: noop,
  closePath: noop,
  moveTo: noop,
  lineTo: noop,
  arc: noop,
  arcTo: noop,
  rect: noop,
  fill: noop,
  stroke: noop,
  clip: noop,
  save: noop,
  restore: noop,
  scale: noop,
  rotate: noop,
  translate: noop,
  transform: noop,
  setTransform: noop,
  measureText: () => ({ width: 0 }),
  canvas: { width: 800, height: 400 },
  fillStyle: '',
  strokeStyle: '',
  lineWidth: 1,
  font: '',
  textAlign: '',
  textBaseline: '',
  globalAlpha: 1,
  globalCompositeOperation: 'source-over',
};

const panelCanvasContext = function (this: HTMLCanvasElement, type: string) {
  if (type === 'webgl2') return webglFixture(this.width, this.height).gl;
  if (type === '2d') return noopCanvas;
  return null;
};
HTMLCanvasElement.prototype.getContext = vi.fn(panelCanvasContext) as any;

// ResizeObserver — MOR-4271: the scope canvases size through a
// CanvasSurface, so the observer must report a real box (asynchronously to
// the observe() call would also do). The 200x100 box matches the pointer
// geometry fixture used by the waterfall tap tests.
// Individual describes override this with their own controllable stub where
// they drive sizes explicitly.
class MockResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}
  observe(target: Element) {
    this.callback([{ target, contentRect: { width: 200, height: 100 } } as ResizeObserverEntry],
      this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver = MockResizeObserver as any;

// requestAnimationFrame / cancelAnimationFrame
globalThis.requestAnimationFrame = vi.fn((cb: FrameRequestCallback) => {
  return setTimeout(() => cb(performance.now()), 0) as unknown as number;
});
globalThis.cancelAnimationFrame = vi.fn((id: number) => clearTimeout(id));

// jsdom does not implement `Element.scrollIntoView` at all; MOR-1456's
// `focus_target` dispatch calls it unconditionally after `.focus()`.
Element.prototype.scrollIntoView = vi.fn();
HTMLElement.prototype.setPointerCapture = vi.fn();
HTMLElement.prototype.releasePointerCapture = vi.fn();

// localStorage stub (jsdom may not expose it as a proper Storage)
const storageMap = new Map<string, string>();
Object.defineProperty(globalThis, 'localStorage', {
  value: {
    getItem: vi.fn((key: string) => storageMap.get(key) ?? null),
    setItem: vi.fn((key: string, val: string) => storageMap.set(key, val)),
    removeItem: vi.fn((key: string) => storageMap.delete(key)),
    clear: vi.fn(() => storageMap.clear()),
    get length() { return storageMap.size; },
    key: vi.fn(() => null),
  },
  writable: true,
  configurable: true,
});

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

type TestScopeFrame = ScopeFrame;
// MOR-4270: the harness mounts the REAL ScopeController against fake
// channels, so every frame assertion runs the real ingress, stream and
// sink path (zero-copy included). The bridge is assigned after imports.
type ScopeBridge = {
  spanControl: ScopeSpanControl | null;
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
    capturedHardwareFrame: null as ((frame: TestScopeFrame) => void) | null,
    capturedDxMessage: null as ((message: unknown) => void) | null,
    currentState: Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'MAIN' }) as unknown,
    currentCaps: Object.freeze({ source: 'test-capabilities' }) as unknown,
    currentSession: { state: 'connected' as const, epoch: 1 },
    mockScopeConnected: true,
    mockScopeFrameLive: true,
    tuningStep: 1_000,
    nextLeaseId: 0,
    hardwareUnsubscribe: vi.fn(),
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
    releaseHardwareScope: vi.fn((lease: unknown) => state.driverBridge.stopHardware(lease)),
    subscribeDx: vi.fn((handler: (message: unknown) => void) => {
      state.capturedDxMessage = handler;
      return state.dxUnsubscribe;
    }),
    send: vi.fn(),
  };
  return { state, runtime };
});

const runtimeFacts = new SvelteMap<string, unknown>([
  ['caps', runtimeHarness.state.currentCaps], ['state', runtimeHarness.state.currentState],
]);
Object.defineProperty(runtimeHarness.state, 'currentState', {
  get: () => runtimeFacts.get('state'), set: value => runtimeFacts.set('state', value),
});
Object.defineProperty(runtimeHarness.state, 'currentCaps', {
  get: () => runtimeFacts.get('caps'),
  set: value => {
    runtimeFacts.set('caps', value);
    const caps = value as { scopeSources?: readonly string[]; scopeSource?: string; scopeDefaultSourceId?: 'radio' | 'iq_fft' | 'audio_fft' | null };
    scopeControllerHarness.configureSources(caps.scopeSources ??
      [caps.scopeSource === 'sdr' ? 'iq_fft' : caps.scopeSource === 'audio_fft' ? 'audio_fft' : 'radio'], caps.scopeDefaultSourceId);
  },
});

const mockRuntime = runtimeHarness.runtime;

// ── MOR-4270: real ScopeController over fake channels (the transport seam) ──
type FakeScopeChannel = {
  connect: Mock<() => void>;
  disconnect: Mock<() => void>;
  state: string;
  sessionEpoch: number;
  sendRaw(message: unknown): boolean;
  onMessage(handler: (message: unknown) => void): () => void;
  onBinary(handler: (buffer: ArrayBuffer) => void): () => void;
  onStateChange(handler: (state: string) => void): () => void;
  onSessionTransition(handler: (transition: { state: string; epoch: number }) => void): () => void;
  fire(bytes: Uint8Array<ArrayBuffer>): void;
  setState(next: string): void;
};
function makeFakeScopeChannel(): FakeScopeChannel {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(transition: { state: string; epoch: number }) => void>();
  let epoch = 0;
  const channel: FakeScopeChannel = {
    get state() { return (channel as { transport?: string }).transport ?? 'disconnected'; },
    set state(next: string) { (channel as { transport?: string }).transport = next; },
    sessionEpoch: 0,
    connect: vi.fn(() => {
      if ((channel as { transport?: string }).transport === 'connected') return;
      (channel as { transport?: string }).transport = 'connected';
      channel.sessionEpoch += 1;
      const transition = { state: 'connected', epoch: channel.sessionEpoch };
      for (const update of states) update(transition.state);
      for (const update of sessions) update(transition);
    }),
    disconnect: vi.fn(() => {
      (channel as { transport?: string }).transport = 'disconnected';
      for (const update of states) update('disconnected');
      for (const update of sessions) update({ state: 'disconnected', epoch: channel.sessionEpoch });
    }),
    sendRaw: vi.fn(() => true), onMessage: () => () => {},
    onBinary(handler) { binary.add(handler); return () => { binary.delete(handler); }; },
    onStateChange(handler) { states.add(handler); return () => { states.delete(handler); }; },
    onSessionTransition(handler) { sessions.add(handler); return () => { sessions.delete(handler); }; },
    fire(bytes: Uint8Array<ArrayBuffer>) { for (const handler of binary) handler(bytes.buffer); },
    setState(next: string) {
      (channel as { transport?: string }).transport = next;
      const transition = { state: next, epoch: channel.sessionEpoch };
      for (const update of states) update(next);
      for (const update of sessions) update(transition);
    },
  } as FakeScopeChannel;
  return channel;
}
const harnessChannels: Record<string, FakeScopeChannel> = {};
let scopeControllerHarness = new ScopeController(
  (name: string) => {
    harnessChannels[name] ??= makeFakeScopeChannel();
    return harnessChannels[name] as unknown as WsChannel;
  },
);
runtimeHarness.runtime.scope = {
  get spanControl() { return scopeControllerHarness.spanControl; },
  set spanControl(control) { scopeControllerHarness.spanControl = control; },
  stream: (source) => scopeControllerHarness.stream(source),
  registerPresentationDriver: (host, config) => scopeControllerHarness.registerPresentationDriver(host, config),
  subscribeTraceReceipt: (observer) => scopeControllerHarness.subscribeTraceReceipt(observer),
  snapshotHealth: (source) => scopeControllerHarness.snapshotHealth(source),
  subscribeHealth: (listener) => scopeControllerHarness.subscribeHealth(listener),
  get hardwareScopeConnected() {
    return runtimeHarness.state.mockScopeConnected ?? scopeControllerHarness.hardwareScopeConnected;
  },
  get hardwareScopeFrameLive() {
    return runtimeHarness.state.mockScopeFrameLive ?? scopeControllerHarness.hardwareScopeFrameLive;
  },
};
runtimeHarness.state.driverBridge.startHardware = () => scopeControllerHarness.hardwareScopeDriver.start();
runtimeHarness.state.driverBridge.stopHardware = (lease: unknown) => {
  void scopeControllerHarness.hardwareScopeDriver.stop(lease);
};
runtimeHarness.state.driverBridge.startAudio = () => scopeControllerHarness.audioFftDriver.start();
runtimeHarness.state.driverBridge.stopAudio = (lease: unknown) => {
  void scopeControllerHarness.audioFftDriver.stop(lease);
};
// Observes trace-receipt subscriptions through the real controller.
const traceReceiptSubscriptions: Array<(receipt: TraceReceipt) => void> = [];
let traceReceiptCreations = 0;
const capturedStreamSinks: Array<Parameters<ReturnType<ScopeController['stream']>['subscribe']>[0]> = [];
const sharedLeaseAcquisitions = vi.fn(() => runtimeHarness.state.driverBridge.startHardware());
const streamSubscriptions = { hardware: vi.fn(), audio_fft: vi.fn() };
const streamUnsubscriptions = { hardware: vi.fn(), audio_fft: vi.fn() };
function instrumentScopeHarness() {
for (const source of ['hardware', 'audio_fft'] as const) {
  const stream = scopeControllerHarness.stream(source);
  const original = stream.subscribe.bind(stream);
  stream.subscribe = (...args) => {
    streamSubscriptions[source]();
    if (source === 'hardware') capturedStreamSinks.push(args[0]);
    const stop = original(...args);
    return () => { streamUnsubscriptions[source](); stop(); };
  };
}
{
  const realSubscribe = scopeControllerHarness.subscribeTraceReceipt.bind(scopeControllerHarness);
  (scopeControllerHarness as unknown as { subscribeTraceReceipt: typeof realSubscribe })
    .subscribeTraceReceipt = (observer) => {
    traceReceiptCreations += 1;
    traceReceiptSubscriptions.push(observer);
    const stop = realSubscribe(observer);
    return () => {
      const index = traceReceiptSubscriptions.indexOf(observer);
      if (index >= 0) traceReceiptSubscriptions.splice(index, 1);
      stop();
    };
  };
}

}
instrumentScopeHarness();

const scopeCommandHarness = vi.hoisted(() => ({ dispatch: vi.fn() }));
vi.mock('$lib/runtime/commands/radio-intents', async (original) => ({
  ...await original<typeof import('$lib/runtime/commands/radio-intents')>(),
  dispatchRadioIntent: scopeCommandHarness.dispatch,
}));

// SvelteMap-backed so that a write to `state.mockScopeConnected` during a
// mounted test re-runs the panel's `$derived` chain; the plain-property
// writes the rest of this file makes keep working through the setter.
const hardwareScopeTransport = new SvelteMap<string, boolean>([['connected', true]]);
Object.defineProperty(runtimeHarness.state, 'mockScopeConnected', {
  configurable: true,
  get: () => hardwareScopeTransport.get('connected')!,
  set: (value: boolean) => hardwareScopeTransport.set('connected', value),
});
const hardwareScopeFrameLive = new SvelteMap<string, boolean>([['live', true]]);
Object.defineProperty(runtimeHarness.state, 'mockScopeFrameLive', {
  configurable: true,
  get: () => hardwareScopeFrameLive.get('live')!,
  set: (value: boolean) => hardwareScopeFrameLive.set('live', value),
});

const authorityHarness = vi.hoisted(() => {
  const state = { current: null as import('$lib/runtime/adapters/scope-adapter').SpectrumAuthority | null,
    useProductionSelector: false };
  return {
    state,
    toSpectrumAuthority: vi.fn((_state: unknown, _caps: unknown) => state.current),
    snapSpectrumFilterWidth: vi.fn((
      raw: number,
      rule: import('$lib/runtime/adapters/scope-adapter').SpectrumFilterRule | null,
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
  };
});
const authorityRefreshState = new SvelteMap<string, number>([['value', 0]]);

function refreshSpectrumAuthority(): void {
  authorityRefreshState.set('value', (authorityRefreshState.get('value') ?? 0) + 1);
}

// The real runtime.state is store-backed and reactive; make the mock's
// reads participate in refreshSpectrumAuthority() pokes so panel deriveds
// re-evaluate raw identity-field changes the same way (MOR-2464).
Object.defineProperty(mockRuntime, 'state', {
  configurable: true,
  get() {
    authorityRefreshState.get('value');
    return runtimeHarness.state.currentState;
  },
});

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

type TestFilterWidthLifecycle = Readonly<{
  confirmed: number | null;
  target: number | null;
  phase: 'unavailable' | 'idle' | 'pending' | 'acknowledged' | 'confirmed';
  busy: boolean;
  outcome: { phase: 'confirmed' | 'failed' | 'timed-out' | 'cancelled'; error?: string } | null;
  presentation: {
    lifecycleId: string;
    transitionId: string;
    receiver: 0 | 1;
    sessionEpoch: number;
    target: number;
    status: 'pending' | 'acknowledged' | 'confirmed' | 'failed' | 'timed-out' | 'cancelled';
  } | null;
}>;

const idleFilterWidthLifecycle: TestFilterWidthLifecycle = Object.freeze({
  confirmed: 2_400,
  target: null,
  phase: 'idle',
  busy: false,
  outcome: null,
  presentation: null,
});
const filterWidthLifecycleState = new SvelteMap<string, TestFilterWidthLifecycle>([
  ['value', idleFilterWidthLifecycle],
]);

function setFilterWidthLifecycle(
  overrides: Partial<TestFilterWidthLifecycle>,
): void {
  filterWidthLifecycleState.set('value', Object.freeze({
    ...idleFilterWidthLifecycle,
    ...overrides,
  }));
}

function pendingFilterWidthLifecycle(target: number, receiver: 0 | 1 = 0) {
  return {
    confirmed: 2_400,
    target,
    phase: 'acknowledged' as const,
    busy: true,
    outcome: null,
    presentation: {
      lifecycleId: `width-${target}`,
      transitionId: `width-${target}-acknowledged`,
      receiver,
      sessionEpoch: 17,
      target,
      status: 'acknowledged' as const,
    },
  };
}

// MOR-2464 follow-up — the pending set_freq target the panel's optimistic
// panorama reads. SvelteMap-backed so a write re-runs the panel's `$derived`
// chain, mirroring the accessor's own reactive source. Keys '0'/'1' are the
// wire receivers `getPendingFrequencyHz` accepts.
const pendingFrequencyState = new SvelteMap<string, number | null>([
  ['0', null],
  ['1', null],
]);

function setPendingFrequency(receiver: 0 | 1, hz: number | null): void {
  pendingFrequencyState.set(String(receiver), hz);
}

// MOR-2464 follow-up — the display-only tuning-burst target the panel
// prefers ahead of pending while a local gesture is live. Same
// SvelteMap-backed harness shape as `pendingFrequencyState`.
const tuningBurstState = new SvelteMap<string, number | null>([
  ['0', null],
  ['1', null],
]);

function setTuningBurstFrequency(receiver: 0 | 1, hz: number | null): void {
  tuningBurstState.set(String(receiver), hz);
}

const passbandHarness = vi.hoisted(() => ({
  rawWidth: 2_700 as number | null,
  getFilterWidthFromRightEdgePx: vi.fn<typeof import('../passband-geometry')['getFilterWidthFromRightEdgePx']>(() => 2_700),
}));

const spectrumRendererHarness = vi.hoisted(() => ({
  lastOptions: null as any,
  constructed: 0,
  resolveColorRolesCalls: 0,
  render: vi.fn((_ctx: unknown, _data: Uint8Array, _width: number, _height: number, options: any) => {
    spectrumRendererHarness.lastOptions = options;
  }),
  setAvgEnabled: vi.fn(),
  setPeakHoldEnabled: vi.fn(),
}));

const audioPaint = vi.hoisted(() => vi.fn());
vi.mock('../../../components-v2/panels/audio-scope/audio-spectrum-renderer', async (original) => {
  const actual = await original<typeof import('../../../components-v2/panels/audio-scope/audio-spectrum-renderer')>();
  return { ...actual, renderAudioSpectrum: (...args: Parameters<typeof actual.renderAudioSpectrum>) => {
    audioPaint(...args);
    return actual.renderAudioSpectrum(...args);
  } };
});

const appTxHostHarness = vi.hoisted(() => ({
  controller: null as ManagedAppTxController | null,
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: {
    configure: vi.fn(),
    acquire: vi.fn(() => runtimeHarness.state.driverBridge.startAudio()),
    release: vi.fn((lease: unknown) => runtimeHarness.state.driverBridge.stopAudio(lease)),
  },
  runtime: runtimeHarness.runtime,
}));

vi.mock('$lib/runtime/adapters/scope-adapter', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/scope-adapter')>();
  return {
    ...actual,
    toSpectrumAuthority: (...args: Parameters<typeof actual.toSpectrumAuthority>) => {
      authorityRefreshState.get('value');
      return authorityHarness.state.useProductionSelector
        ? actual.toSpectrumAuthority(...args)
        : authorityHarness.toSpectrumAuthority(...args);
    },
    snapSpectrumFilterWidth: authorityHarness.snapSpectrumFilterWidth,
  };
});

vi.mock('$lib/runtime/adapters/panel-adapters', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/adapters/panel-adapters')>();
  return {
    ...actual,
    getVfoHandlers: handlerHarness.getVfoHandlers,
    getFilterHandlers: handlerHarness.getFilterHandlers,
    getFilterWidthCommandLifecycle: () => filterWidthLifecycleState.get('value')!,
    getPendingFrequencyHz: (receiver: 0 | 1) =>
      pendingFrequencyState.get(String(receiver)) ?? null,
    // Delegates to the REAL accumulator publication first so reactive
    // seam tests can drive a real accumulator; the SvelteMap stands in
    // only when no real burst is live.
    getTuningBurstFrequencyHz: (receiver: 0 | 1) =>
      actual.getTuningBurstFrequencyHz(receiver)
      ?? tuningBurstState.get(String(receiver))
      ?? null,
  };
});

vi.mock('$lib/renderers/spectrum-renderer', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/renderers/spectrum-renderer')>();
  class SpectrumRenderer {
    constructor() { spectrumRendererHarness.constructed++; }
    setAvgEnabled = spectrumRendererHarness.setAvgEnabled;
    setPeakHoldEnabled = spectrumRendererHarness.setPeakHoldEnabled;
    render = spectrumRendererHarness.render;
  }
  return {
    ...actual,
    resolveSpectrumColorRoles: (...args: Parameters<typeof actual.resolveSpectrumColorRoles>) => {
      spectrumRendererHarness.resolveColorRolesCalls++;
      return actual.resolveSpectrumColorRoles(...args);
    },
    SpectrumRenderer,
  };
});

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => appTxHostHarness.controller,
}));

vi.mock('$lib/stores/radio.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/radio.svelte')>(),
  patchActiveReceiver: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  snapToStep: vi.fn((hz: number) => hz),
  tuneBy: vi.fn(() => 0),
  getTuningStep: vi.fn(() => runtimeHarness.state.tuningStep),
  adjustTuningStep: vi.fn(),
  isAutoStep: vi.fn(() => true),
  formatStep: vi.fn(() => '100 Hz'),
}));

vi.mock('$lib/stores/capabilities.svelte', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/stores/capabilities.svelte')>(),
  hasCapability: vi.fn((name: string) => name === 'scope'),
  hasDualReceiver: vi.fn(() => false),
}));

vi.mock('../passband-geometry', () => ({
  canResizeFromRightEdge: vi.fn((mode: string) => mode.toUpperCase() !== 'LSB'),
  getFilterWidthFromRightEdgePx: passbandHarness.getFilterWidthFromRightEdgePx,
  getPassbandGeometry: vi.fn((
    _mode: string,
    passbandHz: number,
    _shiftHz: number,
    spanHz: number,
    widthPx: number,
    tunePx?: number,
  ) => {
    if (passbandHz <= 0 || spanHz <= 0 || widthPx <= 0) return null;
    const projectedWidth = passbandHz / 100;
    const center = tunePx ?? widthPx / 2;
    return {
      leftPx: center - projectedWidth / 2,
      rightPx: center + projectedWidth / 2,
      widthPx: projectedWidth,
    };
  }),
}));

vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/props/panel-props')>(),
  resolveFilterModeConfig: vi.fn((caps: { filterConfig?: Record<string, unknown> }, mode: string) =>
    caps.filterConfig?.[mode] ?? null),
}));

// ---------------------------------------------------------------------------
// Import component after mocks
// ---------------------------------------------------------------------------

import { WaterfallRenderer, type WaterfallOptions } from '$lib/renderers/waterfall-renderer';
import SpectrumPanel from '../SpectrumPanel.svelte';
import spectrumPanelSource from '../SpectrumPanel.svelte?raw';
import {
  createTuningAccumulator,
  resetSharedTuningAccumulatorForTests,
} from '$lib/runtime/commands/tuning-accumulator';
import {
  defaultSpectrumColorRoles,
  spectrumColorRolesToOptions,
} from '$lib/renderers/spectrum-renderer';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
const authorityPluginSource = readFileSync(
  resolve(process.cwd(), 'scripts/radio-authority-eslint-plugin.mjs'),
  'utf8',
);
const authorityContractSource = readFileSync(
  resolve(process.cwd(), '../docs/internals/ui-radio-control-contract.toml'),
  'utf8',
);
import {
  deriveScopeIndicatorState,
  indicatorTone,
} from '../../../components-v2/layout/StatusBar.svelte';
import statusBarSource from '../../../components-v2/layout/StatusBar.svelte?raw';
import { IC7300_CAPABILITIES, IC7300_STATE } from '../../../lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import bandPlanGoldens from './fixtures/band-plan-responses.json';
import { resetBandPlanRegionForTests } from '$lib/stores/band-plan-region.svelte';

// MOR-2497 (post-#3519): the captured IC-7300 capabilities predate
// `pbtStepHz`, so AS CAPTURED they are the legacy payload — not what these
// production-selector tests mean to drive. The JSON stays a byte-faithful
// capture (re-deriving it needs the live radio, per its provenance header),
// so the payload the server serializes TODAY is composed here instead: the
// capture plus the per-mode twin-PBT steps `rigs/ic7300.toml` declares and
// `_serialize_filter_config` publishes — 50 Hz for SSB/CW/RTTY, 200 for AM,
// none for FM.
const IC7300_STEPPED_CAPABILITIES = {
  ...IC7300_CAPABILITIES,
  filterConfig: Object.fromEntries(
    Object.entries(IC7300_CAPABILITIES.filterConfig ?? {}).map(([mode, config]) => [
      mode,
      mode === 'FM' ? config : { ...config, pbtStepHz: mode === 'AM' ? 200 : 50 },
    ]),
  ),
} as Capabilities;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

let components: ReturnType<typeof mount>[] = [];
let txHarness: ManagedAppTxHarness;
const harnessLeases: unknown[] = [];

// MOR-4256 review: the authority fixtures satisfy the producer's real
// `SpectrumAuthority` contract (scope-adapter) — including the full
// `ScopeControlsViewModel` — instead of a re-guessed structural copy.
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

function freshPbtOnlyIc7300State() {
  const captured = structuredClone(IC7300_STATE);
  const { ifShift: _rawIfShift, ...mainWithoutRawIfShift } = captured.main!;
  const fieldStatus = { ...captured.fieldStatus };
  delete fieldStatus['main.ifShift'];
  const fresh = (path: string) => ({
    observed: true, freshness: 'fresh', availability: 'available', storePath: path,
  });
  return {
    ...captured,
    main: {
      ...mainWithoutRawIfShift,
      mode: 'AM',
      filterWidth: 10_000,
      pbtInner: 128,
      pbtOuter: 128,
    },
    fieldStatus: {
      ...fieldStatus,
      'main.mode': fresh('main.mode'),
      'main.filterWidth': fresh('main.filterWidth'),
      'main.pbtInner': fresh('main.pbtInner'),
      'main.pbtOuter': fresh('main.pbtOuter'),
    },
  };
}

/** Frame overrides encoded onto the real wire layout and fired through the fake channel. */
type EmitOverrides = {
  receiver?: number;
  mode?: number;
  startFreq?: number;
  endFreq?: number;
  outOfRange?: boolean;
  pixels?: Uint8Array | null;
  sequence?: number;
};
function encodeScopeWire(overrides: EmitOverrides): Uint8Array<ArrayBuffer> {
  const supplied = overrides.pixels === undefined ? new Uint8Array(475).fill(64) : overrides.pixels;
  const pixels = supplied?.length ? supplied : null;
  const iq = (runtimeHarness.state.currentCaps as { scopeSource?: string })?.scopeSource === 'sdr';
  return new Uint8Array(scopeWire({ source: iq ? 'iq_fft' : 'radio', receiver: iq ? null : overrides.receiver ?? 0,
    sourceId: iq ? 'iq_fft' : 'radio', viewStartHz: overrides.startFreq ?? 14_000_000,
    viewEndHz: overrides.endFreq ?? 14_100_000, seq: overrides.sequence ?? 1,
    outOfRange: overrides.outOfRange ?? false, pixels }));
}
function emitFrame(overrides: EmitOverrides = {}): EmitOverrides {
  const frame: EmitOverrides = {
    receiver: 0, mode: 0, startFreq: 14_000_000, endFreq: 14_100_000,
    outOfRange: false, ...overrides,
  };
  const pixels = (overrides.pixels ?? new Uint8Array(475).fill(64)) as Uint8Array;
  harnessChannels.scope ??= makeFakeScopeChannel();
  scopeControllerHarness.stream('hardware').setMode(frame.mode ?? 0);
  harnessChannels.scope.fire(encodeScopeWire(frame));
  flushSync();
  return { ...frame, pixels };
}

// MOR-4206: the waterfall paints from a requestAnimationFrame callback (the
// rAF stub above defers it to a macrotask), so a delivered frame's row exists
// only after one timer turn. Await this after a delivery the test expects to
// have painted; without it only the LAST delivered frame would be pending.
function flushWaterfallPaint(): Promise<void> {
  return new Promise((resolve) => { setTimeout(resolve, 0); });
}

function rect(element: Element, left = 0, width = 200): void {
  Object.defineProperty(element, 'getBoundingClientRect', {
    configurable: true,
    value: vi.fn(() => ({
      x: left, y: 0, left, top: 0, right: left + width, bottom: 100,
      width, height: 100, toJSON: () => ({}),
    })),
  });
}

function pointer(
  element: EventTarget,
  type: 'pointerdown' | 'pointermove' | 'pointerup' | 'pointercancel' | 'lostpointercapture',
  pointerId: number,
  clientX: number,
  init: Partial<PointerEventInit> = {},
): void {
  element.dispatchEvent(new PointerEvent(type, {
    bubbles: true,
    cancelable: true,
    button: 0,
    pointerId,
    clientX,
    clientY: 20,
    ...init,
  }));
  flushSync();
}

function verticalRect(element: Element, top: number, height: number): void {
  Object.defineProperty(element, 'getBoundingClientRect', {
    configurable: true,
    value: vi.fn(() => ({
      x: 0, y: top, left: 0, top, right: 800, bottom: top + height,
      width: 800, height, toJSON: () => ({}),
    })),
  });
}

function prepareGeometry(target: HTMLElement, width = 200): {
  spectrum: HTMLElement;
  waterfall: HTMLElement;
} {
  const spectrum = target.querySelector<HTMLElement>('.spectrum-area')!;
  const waterfall = target.querySelector<HTMLElement>('.waterfall-content')!;
  rect(spectrum, 0, width);
  rect(waterfall, 0, width);
  const canvas = waterfall.querySelector('canvas');
  if (canvas) rect(canvas, 0, width);
  return { spectrum, waterfall };
}

function mountPanel(props: Record<string, unknown> = {}) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(SpectrumPanel, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  HTMLCanvasElement.prototype.getContext = vi.fn(panelCanvasContext) as any;
  components = [];
  traceReceiptCreations = 0;
  capturedStreamSinks.length = 0;
  panelEffects.runs.clear(); panelEffects.origins.clear();
  for (const name of Object.keys(harnessChannels)) {
    const channel = harnessChannels[name]!;
    channel.disconnect();
  }
  for (const name of Object.keys(harnessChannels)) delete harnessChannels[name];
  scopeControllerHarness = new ScopeController(name => {
    harnessChannels[name] ??= makeFakeScopeChannel();
    return harnessChannels[name] as unknown as WsChannel;
  });
  traceReceiptSubscriptions.length = 0;
  instrumentScopeHarness();
  storageMap.clear();
  txHarness = new ManagedAppTxHarness();
  appTxHostHarness.controller = txHarness.controller;
  runtimeHarness.state.capturedHardwareFrame = null;
  runtimeHarness.state.capturedDxMessage = null;
  runtimeHarness.state.mockScopeConnected = true;
  runtimeHarness.state.mockScopeFrameLive = true;
  runtimeHarness.state.tuningStep = 1_000;
  runtimeHarness.state.nextLeaseId = 0;
  runtimeHarness.state.hardwareUnsubscribe = vi.fn();
  runtimeHarness.state.dxUnsubscribe = vi.fn();
  runtimeHarness.state.currentState = Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'MAIN' });
  runtimeHarness.state.currentCaps = Object.freeze({ source: 'test-capabilities' });
  runtimeHarness.state.currentSession = { state: 'connected', epoch: 1 };
  authorityHarness.state.current = authority();
  authorityHarness.state.useProductionSelector = false;
  setFilterWidthLifecycle({});
  pendingFrequencyState.set('0', null);
  pendingFrequencyState.set('1', null);
  tuningBurstState.set('0', null);
  tuningBurstState.set('1', null);
  resetSharedTuningAccumulatorForTests();
  passbandHarness.rawWidth = 2_700;
  passbandHarness.getFilterWidthFromRightEdgePx.mockImplementation(() => passbandHarness.rawWidth);
  spectrumRendererHarness.lastOptions = null;
  vi.clearAllMocks();
});

afterEach(async () => {
  await Promise.all(components.map((c) => unmount(c)));
  for (const lease of harnessLeases.splice(0)) mockRuntime.releaseHardwareScope(lease);
  expect(txHarness.trace()).toEqual([]);
  expect(txHarness.listenerCount()).toBe(0);
  appTxHostHarness.controller = null;
  document.body.innerHTML = '';
  radio.current = null; clearCapabilities();
  vi.unstubAllEnvs();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe('SpectrumPanel component', () => {
  describe.each(['IC-705', 'IC-7300'])('%s pending span requests', (model) => {
    it.each([-1, 1] as const)('accumulates down then %s before either grant', async (second) => {
      runtimeHarness.state.currentCaps = validateCapabilities({ ...IC7300_CAPABILITIES, ...spanCapabilities, model });
      const target = mountPanel();
      emitFrame(); await flushWaterfallPaint();
      const control = mockRuntime.scope.spanControl!;
      const view = () => JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!).view;
      expect(control.spanHz).toBe(100_000);
      control.step(-1); flushSync();
      expect(view()).toEqual({ follow: 'vfo', spanHz: 50_000 });
      control.step(second); flushSync();
      const expected = second === -1 ? 25_000 : 100_000;
      expect(view()).toEqual({ follow: 'vfo', spanHz: expected });
      expect(control.spanHz).toBe(100_000);
      expect(target.querySelector('[title="Scope span"] .toolbar-value')?.textContent).toBe('±50k');
      emitFrame({ startFreq: 14_050_000 - expected / 2, endFreq: 14_050_000 + expected / 2, sequence: 2 });
      expect(control.spanHz).toBe(expected);
    });

    it('steps from the pending absolute zoom view before its grant', async () => {
      runtimeHarness.state.currentCaps = validateCapabilities({ ...IC7300_CAPABILITIES, ...spanCapabilities, model });
      const target = mountPanel(); emitFrame(); await flushWaterfallPaint();
      target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click(); flushSync();
      mockRuntime.scope.spanControl!.step(-1); flushSync();
      const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
      expect(request.view).toEqual({ follow: 'vfo', spanHz: 25_000 });
      expect(mockRuntime.scope.spanControl!.spanHz).toBe(100_000);
    });
  });

  it('keeps trace controls absent in an ordinary build', () => {
    const target = mountPanel();
    expect(target.querySelector('[aria-label="Scope render trace"]')).toBeNull();
    expect(traceReceiptSubscriptions).toHaveLength(0);
    expect(traceReceiptCreations).toBe(0);
    emitFrame();
    expect(target.querySelector('[aria-label="Scope render trace"]')).toBeNull();
    expect(traceReceiptSubscriptions).toHaveLength(0);
    expect(traceReceiptCreations).toBe(0);
  });

  it('starts and stops a candidate trace only on explicit clicks without opening a source', () => {
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', '1');
    const target = mountPanel();
    emitFrame();
    const controls = target.querySelector('[aria-label="Scope render trace"]');
    expect(controls).not.toBeNull();
    const start = [...controls!.querySelectorAll('button')].find((button) => button.textContent === 'Start trace')!;
    expect(start.disabled).toBe(false);
    start.click();
    flushSync();
    expect(traceReceiptSubscriptions).toHaveLength(1);
    expect(controls?.textContent).toContain('recording');
    expect(runtimeHarness.runtime.acquireHardwareScope).toHaveBeenCalledTimes(1);
    const stop = [...controls!.querySelectorAll('button')].find((button) => button.textContent === 'Stop trace')!;
    stop.click();
    flushSync();
    expect(controls?.textContent).toContain('stopped');
    expect(controls?.textContent).toContain('Download trace');
  });

  it('keeps concurrent panel sessions separate and ignores receipts from another source', () => {
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', '1');
    const observe = vi.spyOn(ScopeRenderTrace.prototype, 'observe');
    const first = mountPanel();
    const second = mountPanel();
    emitFrame();
    for (const panel of [first, second]) {
      (panel.querySelector('[aria-label="Scope render trace"] button') as HTMLButtonElement).click();
    }
    flushSync();
    const subscriptions = traceReceiptSubscriptions.splice(-2);
    expect(subscriptions).toHaveLength(2);
    const frame = Object.freeze({ source: 'radio' as const, sourceId: 'scope', epoch: 1,
      receiver: 0, viewStartHz: 1, viewEndHz: 2, coverageStartHz: 1, coverageEndHz: 2,
      width: 4, pixels: new Uint8Array(4), outOfRange: false, seq: 1, sentAtMs: null });
    const receipt = { kind: 'accepted' as const, frame, token: { source: 'hardware' as const,
      transportEpoch: 1, wireSequence: 1, acceptedSequence: 1 }, callbackAt: performance.now(), acceptedAt: performance.now() };
    subscriptions[0](receipt);
    subscriptions[1](receipt);
    expect(observe).toHaveBeenCalledTimes(2);
    expect(observe.mock.contexts[0]).not.toBe(observe.mock.contexts[1]);
    subscriptions[0]({ ...receipt, token: { ...receipt.token, source: 'audio_fft' } });
    expect(observe).toHaveBeenCalledTimes(2);
  });

  it.each(['source', 'receiver'] as const)('stops a trace when selected %s changes without another frame', (change) => {
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', '1');
    const target = mountPanel();
    emitFrame();
    const start = target.querySelector<HTMLButtonElement>('[aria-label="Scope render trace"] button')!;
    start.click();
    flushSync();
    expect(target.querySelector('[aria-label="Scope render trace"]')?.textContent).toContain('recording');
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    if (change === 'source') runtimeHarness.state.currentCaps = Object.freeze({ scopeSource: 'audio_fft' });
    else runtimeHarness.state.currentState = Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'SUB' });
    refreshSpectrumAuthority();
    flushSync();
    expect(target.querySelector('[aria-label="Scope render trace"]')?.textContent).toContain('stopped (source_changed)');
    expect(push).not.toHaveBeenCalled();
    // The stopped trace released its receipt subscription and nothing
    // resubscribed without another frame.
    expect(traceReceiptSubscriptions).toHaveLength(0);
    expect(traceReceiptCreations).toBe(1);
  });
  it('runs no panel effect for 100 pixel-only frames and at most once per changed geometry', () => {
    mountPanel(); emitFrame();
    const baseline = new Map(panelEffects.runs);
    expect(baseline.size).toBeGreaterThan(0);
    for (let sequence = 2; sequence <= 101; sequence++) emitFrame({ sequence });
    expect(panelEffects.runs).toEqual(baseline);
    emitFrame({ sequence: 102, startFreq: 13_990_000, endFreq: 14_110_000 });
    const changed = [...panelEffects.runs].map(([id, runs]) => runs - (baseline.get(id) ?? 0));
    const sites = [...panelEffects.runs].map(([id, runs]) => ({ runs: runs - (baseline.get(id) ?? 0), site: panelEffects.origins.get(id) })).filter(({ runs }) => runs > 0);
    console.info('MOR-4270 geometry effect sites', sites);
    expect(changed.reduce((sum, runs) => sum + runs, 0), JSON.stringify(sites)).toBeLessThanOrEqual(1);
    expect(changed.some((runs) => runs === 1)).toBe(true);
    console.info('MOR-4270 SpectrumPanel effects', { registered: baseline.size, pixelOnly: 0, geometryMaximum: Math.max(...changed), geometryTotal: changed.reduce((sum, runs) => sum + runs, 0) });
  });

  it('retires averaging and peak history when only the scope payload width changes', async () => {
    const { SpectrumRenderer: RealRenderer } = await vi.importActual<typeof import('$lib/renderers/spectrum-renderer')>('$lib/renderers/spectrum-renderer');
    const actual = new RealRenderer();
    const expected = new RealRenderer();
    const context = () => ({ ...noopCanvas, setLineDash: noop, lineTo: vi.fn(), moveTo: vi.fn() });
    const painted = context(); const fresh = context();
    const previousObserver = globalThis.ResizeObserver;
    globalThis.ResizeObserver = class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) { this.callback([{ target, contentRect: { width: 200, height: 100 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      unobserve() {}
      disconnect() {}
    } as typeof ResizeObserver;
    const original = spectrumRendererHarness.render.getMockImplementation()!;
    spectrumRendererHarness.render.mockImplementation((_ctx, pixels, width, height, options) => {
      spectrumRendererHarness.lastOptions = options;
      recordCanvasLayers(painted as unknown as CanvasRenderingContext2D);
    actual.render(painted as unknown as CanvasRenderingContext2D, pixels, width, height, options);
    });
    try {
      mountPanel();
      emitFrame({ pixels: new Uint8Array([80, 80]), sequence: 1 });
      await flushWaterfallPaint();
      expect(painted.lineTo.mock.calls.some(([x, y]) => x > 5 && x < 190 && y === 0)).toBe(true);
      painted.lineTo.mockClear(); painted.moveTo.mockClear();
      emitFrame({ pixels: new Uint8Array([0, 0, 0, 0]), sequence: 2 });
      await flushWaterfallPaint();
      const last = spectrumRendererHarness.render.mock.calls.at(-1)!;
      recordCanvasLayers(fresh as unknown as CanvasRenderingContext2D);
    expected.render(fresh as unknown as CanvasRenderingContext2D, last[1], last[2], last[3], last[4]);
      expect(painted.lineTo.mock.calls).toEqual(fresh.lineTo.mock.calls);
      expect(painted.moveTo.mock.calls).toEqual(fresh.moveTo.mock.calls);
    } finally {
      spectrumRendererHarness.render.mockImplementation(original);
      globalThis.ResizeObserver = previousObserver;
    }
  });

  it('delivers the central audio FFT payload to both renderer sinks without a copy', async () => {
    runtimeHarness.state.currentCaps = { scopeSource: 'audio_fft' };
    mountPanel();
    const bytes = new Uint8Array(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, viewStartHz: 0, viewEndHz: 48_000, pixels: new Uint8Array([1, 2, 40, 160]) }));
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    harnessChannels.scope.fire(bytes); flushSync(); await flushWaterfallPaint();
    const pixels = spectrumRendererHarness.render.mock.calls.at(-1)![1];
    expect(pixels.buffer).toBe(bytes.buffer);
    expect(pixels.byteOffset).toBe(56 + 'audio_fft'.length);
    expect([...pixels]).toEqual([1, 2, 40, 160]);
    expect(push.mock.calls.at(-1)![0]).toBe(pixels);
    expect(spectrumRendererHarness.lastOptions).toMatchObject({ sampleOffset: 2, sampleAmplitudeMax: 160 });
  });

  it('retains default audio follow intent after granted RF geometry and a QSY', () => {
    runtimeHarness.state.currentCaps = { scopeSource: 'audio_fft' };
    const target = mountPanel();
    const subscription = () => JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(subscription().view).toEqual({ follow: 'vfo', spanHz: 48000 });
    harnessChannels.scope.fire(new Uint8Array(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null,
      viewStartHz: 14_026_000, viewEndHz: 14_074_000, pixels: new Uint8Array([0, 80, 160, 0]) })));
    flushSync();
    expect(subscription().view).toEqual({ follow: 'vfo', spanHz: 48000 });
    const state = runtimeHarness.state.currentState as ServerState;
    runtimeHarness.state.currentState = { ...state, main: { ...state.main, freqHz: 14_200_000 } };
    flushSync();
    expect(subscription().view).toEqual({ follow: 'vfo', spanHz: 48000 });
  });

  it.each(['radio', 'iq_fft', 'audio_fft'] as const)('preserves the configured %s default in the mounted panorama among all sources', (scopeDefaultSourceId) => {
    runtimeHarness.state.currentCaps = { scopeSource: null, scopeDefaultSourceId,
      scopeSources: ['radio', 'iq_fft', 'audio_fft'], scope: true, audio: true, audioFftAvailable: true };
    const target = mountPanel();
    const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(request.sourceId).toBe(scopeDefaultSourceId);
    expect(request.receiver).toBe(scopeDefaultSourceId === 'radio' ? 0 : null);
    expect(target.querySelector('.spectrum-panel')!.classList.contains('audio-fft')).toBe(scopeDefaultSourceId === 'audio_fft');
  });

  it.each(audioScopeGoldenNames)('keeps real %s pixels and observed AF coordinates through the actual panel', async (name) => {
    const { buffer, expected } = loadAudioScopeGolden(name);
    runtimeHarness.state.currentCaps = { scopeSource: null, scopeDefaultSourceId: 'audio_fft',
      scopeSources: ['radio', 'iq_fft', 'audio_fft'], audio: true, audioFftAvailable: true };
    const observed = { observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1 };
    runtimeHarness.state.currentState = { providerGeneration: 17, main: { freqHz: expected.audioPassband.tunedHz,
      mode: expected.input.mode }, cwPitch: expected.input.cwPitchHz,
      fieldStatus: Object.fromEntries(['main.freqHz', 'main.mode', 'cwPitch'].map(path => [path, { ...observed, storePath: path }])) };
    const target = mountPanel();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    harnessChannels.scope.fire(new Uint8Array(buffer)); flushSync(); await flushWaterfallPaint();
    const span = expected.frame.view_end_hz - expected.frame.view_start_hz;
    const carrier = expected.audioPassband.tunedHz + expected.audioPassband.carrierOffsetHz;
    const sign = expected.audioPassband.sideband === 'lower' ? -1 : 1;
    const afSpan = sign === 1 ? expected.frame.view_end_hz - carrier : carrier - expected.frame.view_start_hz;
    const pixels = spectrumRendererHarness.render.mock.calls.at(-1)![1];
    expect(pixels.buffer).toBe(buffer); expect(push.mock.lastCall?.[0]).toBe(pixels);
    expect(spectrumRendererHarness.lastOptions).toMatchObject({ spanHz: afSpan, sampleOffset: 0,
      sampleWindow: { start: (carrier - expected.frame.view_start_hz) / span,
        end: (carrier - expected.frame.view_start_hz) / span + sign / span * afSpan } });
    const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(request.view).toEqual({ follow: 'vfo', spanHz: 48000 });
  });

  it.each([1, 3])('keeps IQ View following QSY independently of native radio scope mode %s', async (mode) => {
    runtimeHarness.state.currentCaps = { scopeSource: null, scopeDefaultSourceId: 'iq_fft',
      scopeSources: ['radio', 'iq_fft', 'audio_fft'] };
    authorityHarness.state.current = authority({ scopeControls: testScopeControls(mode) });
    const target = mountPanel();
    harnessChannels.scope.fire(new Uint8Array(scopeWire({ source: 'iq_fft', sourceId: 'iq_fft', receiver: null })));
    flushSync();
    await flushWaterfallPaint();
    expect(spectrumRendererHarness.lastOptions?.scopeMode).toBe(0);
    authorityHarness.state.current = authority({ frequencyHz: 14_150_000, scopeControls: testScopeControls(mode) });
    refreshSpectrumAuthority(); flushSync();
    const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(request.receiver).toBeNull();
    expect(request.view).toEqual({ follow: 'vfo', spanHz: 100_000 });
  });

  it.each([0, 1] as const)('requests the numeric receiver %s from authority', (receiver) => {
    authorityHarness.state.current = authority({ receiver });
    const target = mountPanel();
    emitFrame({ receiver });
    const subscription = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(subscription.receiver).toBe(receiver);
  });

  it('zooms the effective View repeatedly and updates displayed bounds without new pixels', async () => {
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    const target = mountPanel();
    emitFrame();
    await flushWaterfallPaint();
    const zoom = target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!;
    const subscription = () => JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    zoom.click(); flushSync(); await flushWaterfallPaint();
    expect(subscription().view.endHz - subscription().view.startHz).toBe(50_000);
    expect(spectrumRendererHarness.lastOptions.spanHz).toBe(50_000);
    zoom.click(); flushSync(); await flushWaterfallPaint();
    expect(subscription().view.endHz - subscription().view.startHz).toBe(25_000);
    expect(spectrumRendererHarness.lastOptions.spanHz).toBe(25_000);
  });

  it('places the carrier proportionally when a band preset is centered away from the VFO', async () => {
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    const target = mountPanel(); emitFrame(); await flushWaterfallPaint();
    const select = target.querySelector<HTMLSelectElement>('[data-testid="scope-preset-select"]')!;
    select.value = '0'; select.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync(); await flushWaterfallPaint();
    const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(request.view.endHz - request.view.startHz).toBeGreaterThan(100_000);
    expect(spectrumRendererHarness.lastOptions.scopeMode).toBe(1);
    const expectedLeft = (14_050_250 - request.view.startHz) / (request.view.endHz - request.view.startHz) * 100;
    expect(parseFloat(target.querySelector<HTMLElement>('.waterfall-content .tune-line')!.style.left)).toBeCloseTo(expectedLeft);
  });

  it('maps a click through the zoomed View and retains it after a non-drag release', async () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    const target = mountPanel(); emitFrame(); await flushWaterfallPaint();
    target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click();
    flushSync(); await flushWaterfallPaint();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 90, 150); pointer(spectrum, 'pointerup', 90, 150);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_063_000, 0);
    const request = JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(request.view).toEqual({ startHz: 14_025_000, endHz: 14_075_000 });
  });

  it('applies repeated zoom and absolute requested bounds to a FIX scope too', async () => {
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    const target = mountPanel(); emitFrame({ mode: 1 });
    await flushWaterfallPaint();
    const read = () => JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!);
    expect(read().view).toEqual({ follow: 'vfo', spanHz: 100_000 });
    target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click();
    flushSync(); await flushWaterfallPaint();
    expect(read().view).toEqual({ startHz: 14_025_000, endHz: 14_075_000 });
    expect(spectrumRendererHarness.lastOptions).toMatchObject({ centerHz: 14_050_000, spanHz: 50_000, sampleSpanHz: 100_000 });
  });

  it('hatches both uncovered requested margins with a typed reason', async () => {
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
    const target = mountPanel();
    emitFrame();
    target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-out"]')!.click();
    flushSync(); await flushWaterfallPaint();
    const hatches = target.querySelectorAll<HTMLElement>('.scope-hatch');
    expect(hatches).toHaveLength(2);
    expect([...hatches].map((hatch) => [hatch.style.left, hatch.style.width])).toEqual([['0%', '25%'], ['75%', '25%']]);
    expect([...hatches].every((hatch) => hatch.title === 'View wider than the delivered scope coverage')).toBe(true);
    expect(spectrumRendererHarness.lastOptions.spanHz).toBe(200_000);
  });

  it('mounts without errors', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-panel')).not.toBeNull();
    expect(appTxHostHarness.controller).toBe(txHarness.controller);
    expect(txHarness.trace()).toEqual([]);
  });

  it('renders the toolbar section', () => {
    const target = mountPanel();
    // SpectrumToolbar renders inside the panel
    const panel = target.querySelector('.spectrum-panel');
    expect(panel).not.toBeNull();
    expect(panel!.children.length).toBeGreaterThan(0);
  });

  it('renders spectrum-with-scales area containing db-scale and spectrum-area', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-with-scales')).not.toBeNull();
    expect(target.querySelector('.db-scale')).not.toBeNull();
    expect(target.querySelector('.spectrum-area')).not.toBeNull();
  });

  it('renders waterfall area with waterfall-content', () => {
    const target = mountPanel();
    expect(target.querySelector('.waterfall-area')).not.toBeNull();
    expect(target.querySelector('.waterfall-content')).not.toBeNull();
  });

  it('renders dB scale ticks', () => {
    const target = mountPanel();
    const ticks = target.querySelectorAll('.db-scale .tick');
    expect(ticks.length).toBe(4);
    const labels = Array.from(ticks).map((t) => t.textContent?.trim());
    expect(labels).toEqual(['0', '-20', '-40', '-60']);
  });

  it('acquires one runtime hardware-scope lease and lifetime-neutral subscriptions on mount', () => {
    mountPanel();
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledWith('SpectrumPanel');
    expect(harnessChannels.scope?.connect).toHaveBeenCalledTimes(1);
    expect(mockRuntime.subscribeDx).toHaveBeenCalledTimes(1);
    expect(streamSubscriptions.hardware).toHaveBeenCalledTimes(1);
    expect(streamSubscriptions.audio_fft).not.toHaveBeenCalled();
  });

  it('releases the exact lease on OFF and acquires a fresh lease on ON', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;
    const firstLease = mockRuntime.acquireHardwareScope.mock.results[0].value;

    expect(toggle).not.toBeNull();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(toggle.textContent).toContain('VIEW ON');

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(toggle.textContent).toContain('VIEW OFF');
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(firstLease);

    toggle.click();
    flushSync();
    expect(toggle.getAttribute('aria-pressed')).toBe('true');
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(2);
    const currentLease = mockRuntime.acquireHardwareScope.mock.results[1].value;
    expect(currentLease).not.toBe(firstLease);

    const component = components.pop()!;
    unmount(component);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(2);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(currentLease);
    expect(runtimeHarness.state.dxUnsubscribe).toHaveBeenCalledTimes(1);
    expect(streamUnsubscriptions.hardware).toHaveBeenCalledTimes(1);
  });

  it('keeps OFF intent inert across runtime health, teardown, and remount', () => {
    const target = mountPanel();
    const toggle = target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!;
    const releasedLease = mockRuntime.acquireHardwareScope.mock.results[0].value;

    toggle.click();
    runtimeHarness.state.mockScopeConnected = false;
    runtimeHarness.state.mockScopeConnected = true;
    flushSync();

    expect(toggle.getAttribute('aria-pressed')).toBe('false');
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);

    unmount(components.pop()!);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenLastCalledWith(releasedLease);

    mountPanel();
    const remountLease = mockRuntime.acquireHardwareScope.mock.results[1].value;
    expect(remountLease).not.toBe(releasedLease);
    unmount(components.pop()!);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenNthCalledWith(2, remountLease);
  });

  it('renders demand ON without claiming an inactive channel is live', () => {
    runtimeHarness.state.mockScopeConnected = false;
    const target = mountPanel();

    expect(
      target.querySelector<HTMLButtonElement>('.scope-demand-toggle')?.getAttribute('aria-pressed'),
    ).toBe('true');
    expect(target.querySelector('.scope-disconnected-overlay')).not.toBeNull();
    expect(target.querySelector('.scope-demand-off-overlay')).toBeNull();
  });

  it('ignores shared runtime frames while VIEW is OFF', () => {
    const target = mountPanel();
    emitFrame();
    target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click();
    emitFrame({ mode: 1, endFreq: 14_350_000 });
    expect(target.querySelector('.freq-axis')).toBeNull();
  });

  it('does not render freq-axis when no span data', () => {
    const target = mountPanel();
    // Without scope frames, spanHz = 0, so freq-axis is not rendered
    expect(target.querySelector('.freq-axis')).toBeNull();
  });

  it('shows the static CENTER reference from frequency evidence before any frame data', () => {
    const target = mountPanel();
    // No frame has arrived (span 0), but the radio's observed frequency is
    // evidence enough for the stationary CENTER reference in BOTH areas
    // (MOR-2464); the passband shade stays absent — its geometry is not
    // available without the tuple.
    expect(target.querySelectorAll('.spectrum-area .tune-line, .waterfall-content .tune-line')).toHaveLength(2);
    expect(target.querySelector('.passband-overlay')).toBeNull();
  });

  it('unmounts cleanly without errors', () => {
    const target = mountPanel();
    const component = components.pop()!;
    expect(() => unmount(component)).not.toThrow();
    // Verify disconnect was called via the cleanup returned from onMount
    target.remove();
  });

  it('wheel snaps an off-grid Observation and emits one typed VFO intent', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_074_250 });
    const target = mountPanel();
    const panel = target.querySelector('.spectrum-panel')!;
    const wheelEvent = new WheelEvent('wheel', {
      deltaY: -100,
      bubbles: true,
      cancelable: true,
    });
    panel.dispatchEvent(wheelEvent);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    // MOR-1425 review B1: scroll-to-tune is a fixed one-step relative
    // gesture — it opts into the accumulate path explicitly.
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_075_000, 0, 'step');
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });

  it('renders runtime hardware frames and DX spots', () => {
    const target = mountPanel();
    emitFrame({ mode: 1, endFreq: 14_350_000 });
    expect(target.querySelector('.freq-axis')).not.toBeNull();

    runtimeHarness.state.capturedDxMessage?.({
      type: 'dx_spot',
      spot: {
        spotter: 'N0CALL',
        freq: 14_175_000,
        call: 'K1ABC',
        comment: 'test',
        time_utc: '1200',
        timestamp: 1,
      },
    });
    flushSync();
    expect(target.querySelector('.dx-badge')?.textContent).toContain('K1ABC');
  });

  it('keeps all scope transport and health authority behind the runtime facade', () => {
    for (const legacyAuthority of [
      'getChannel',
      'onMessage',
      'sendCommand',
      'setScopeConnected',
      'markScopeFrame',
    ]) {
      expect(spectrumPanelSource).not.toContain(legacyAuthority);
    }
    expect(spectrumPanelSource).toContain('runtime.acquireHardwareScope');
    expect(spectrumPanelSource).toContain("runtime.scope.stream(sourceIsAudio ? 'audio_fft' : 'hardware')");
    expect(spectrumPanelSource).toContain('runtime.subscribeDx');
    expect(spectrumPanelSource).not.toContain('runtime.send');
    expect(spectrumPanelSource).not.toContain('patchActiveReceiver');
    expect(spectrumPanelSource).not.toContain('patchReceiver');
    expect(spectrumPanelSource).not.toContain("stores/radio.svelte");
    expect(spectrumPanelSource).not.toContain('getCapabilities');
    expect(spectrumPanelSource).not.toContain('resolveFilterModeConfig');
    expect(spectrumPanelSource).not.toContain('tuneBy');
    expect(spectrumPanelSource).not.toContain('getDragInterval');
  });
});

describe('SpectrumPanel TX-known overlay (MOR-2859)', () => {
  it('shows the transmitting message instead of the disconnected overlay when TX is known', () => {
    txHarness.emitServerSnapshot({ observedPtt: 'on' });
    runtimeHarness.state.mockScopeConnected = false;
    const target = mountPanel();

    expect(target.querySelector('[data-testid="scope-tx-overlay"]')?.textContent)
      .toBe('Transmitting — the radio sends no panorama during transmit');
    expect(target.querySelector('.scope-disconnected-overlay')).toBeNull();
  });

  it('keeps the disconnected overlay on RX with no frame', () => {
    runtimeHarness.state.mockScopeConnected = false;
    const target = mountPanel();

    expect(target.querySelector('.scope-disconnected-overlay')).not.toBeNull();
    expect(target.querySelector('[data-testid="scope-tx-overlay"]')).toBeNull();
  });

  it('keeps the disconnected overlay when TX is only uncertain', () => {
    txHarness.emitServerSnapshot({ intent: 'ptt', observedPtt: 'off' });
    runtimeHarness.state.mockScopeConnected = false;
    const target = mountPanel();

    expect(target.querySelector('.scope-disconnected-overlay')).not.toBeNull();
    expect(target.querySelector('[data-testid="scope-tx-overlay"]')).toBeNull();
  });

  it('shows no overlay when TX is known and frames are live', () => {
    txHarness.emitServerSnapshot({ observedPtt: 'on' });
    const target = mountPanel();
    emitFrame();

    expect(target.querySelector('[data-testid="scope-tx-overlay"]')).toBeNull();
    expect(target.querySelector('.scope-disconnected-overlay')).toBeNull();
  });
});

describe('SpectrumPanel spectrum/waterfall separator (MOR-2461)', () => {
  function splitElements(target: HTMLElement) {
    const region = target.querySelector<HTMLElement>('.spectrum-split-region')!;
    const axis = target.querySelector<HTMLElement>('.freq-axis');
    const separator = target.querySelector<HTMLElement>('[role="separator"]')!;
    verticalRect(region, 0, 400);
    if (axis) verticalRect(axis, 120, 20);
    verticalRect(separator, 140, 8);
    return { region, axis, separator };
  }

  it('renders a focusable horizontal separator and falls back safely for invalid storage', () => {
    storageMap.set('rigplane-spectrum-split-ratio', 'not-a-ratio');
    const target = mountPanel();
    const { region, separator } = splitElements(target);

    expect(separator.getAttribute('aria-orientation')).toBe('horizontal');
    expect(separator.tabIndex).toBe(0);
    expect(separator.getAttribute('aria-valuenow')).toBe('30');
    expect(separator.getAttribute('aria-valuemin')).toBe('20');
    expect(separator.getAttribute('aria-valuemax')).toBe('80');
    expect(region.style.gridTemplateRows).toContain('0.3fr');
    expect(region.style.gridTemplateRows).toContain('0.7fr');
    expect(region.style.gridTemplateRows).toContain('20px 8px');
  });

  it('restores a normalized persisted ratio without remounting either canvas', () => {
    storageMap.set('rigplane-spectrum-split-ratio', '0.62');
    const target = mountPanel();
    const { region, separator } = splitElements(target);
    const spectrumCanvas = target.querySelector('.spectrum-area canvas');
    const waterfallCanvas = target.querySelector('.waterfall-content canvas');

    expect(separator.getAttribute('aria-valuenow')).toBe('62');
    expect(region.style.gridTemplateRows).toContain('0.62fr');
    window.dispatchEvent(new Event('resize'));
    target.querySelector<HTMLButtonElement>('[title="Toggle fullscreen"]')!.click();
    flushSync();

    expect(target.querySelector('.spectrum-panel')?.classList.contains('fullscreen')).toBe(true);
    expect(separator.getAttribute('aria-valuenow')).toBe('62');
    expect(region.style.gridTemplateRows).toContain('0.62fr');
    expect(target.querySelector('.spectrum-area canvas')).toBe(spectrumCanvas);
    expect(target.querySelector('.waterfall-content canvas')).toBe(waterfallCanvas);
  });

  it('captures pointer drag, clamps to practical minimum heights, and persists the ratio', () => {
    const target = mountPanel();
    const { separator } = splitElements(target);
    const spectrumCanvas = target.querySelector('.spectrum-area canvas');
    const waterfallCanvas = target.querySelector('.waterfall-content canvas');

    pointer(separator, 'pointerdown', 71, 10, { clientY: 250 });
    expect(HTMLElement.prototype.setPointerCapture).toHaveBeenCalledWith(71);
    expect(Number(separator.getAttribute('aria-valuenow'))).toBe(61);
    expect(Number(storageMap.get('rigplane-spectrum-split-ratio'))).toBeCloseTo(226 / 372);

    pointer(separator, 'pointermove', 71, 10, { clientY: -100 });
    expect(separator.getAttribute('aria-valuenow')).toBe('20');
    pointer(separator, 'pointermove', 71, 10, { clientY: 1000 });
    expect(Number(separator.getAttribute('aria-valuenow'))).toBe(78);

    pointer(separator, 'pointerup', 71, 10, { clientY: 300 });
    expect(HTMLElement.prototype.releasePointerCapture).toHaveBeenCalledWith(71);
    const completed = separator.getAttribute('aria-valuenow');
    pointer(separator, 'pointermove', 71, 10, { clientY: 100 });
    expect(separator.getAttribute('aria-valuenow')).toBe(completed);
    expect(target.querySelector('.spectrum-area canvas')).toBe(spectrumCanvas);
    expect(target.querySelector('.waterfall-content canvas')).toBe(waterfallCanvas);
  });

  it('cleans up matching cancel and lost capture without emitting a radio command', () => {
    const target = mountPanel();
    const { separator } = splitElements(target);

    pointer(separator, 'pointerdown', 72, 10, { clientY: 200 });
    pointer(separator, 'pointercancel', 72, 10, { clientY: 200 });
    const afterCancel = separator.getAttribute('aria-valuenow');
    pointer(separator, 'pointermove', 72, 10, { clientY: 300 });
    expect(separator.getAttribute('aria-valuenow')).toBe(afterCancel);

    pointer(separator, 'pointerdown', 73, 10, { clientY: 220 });
    pointer(separator, 'lostpointercapture', 73, 10, { clientY: 220 });
    const afterLost = separator.getAttribute('aria-valuenow');
    pointer(separator, 'pointermove', 73, 10, { clientY: 300 });
    expect(separator.getAttribute('aria-valuenow')).toBe(afterLost);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });

  it('supports ArrowUp, ArrowDown, Home, and End while isolating wheel intent', () => {
    storageMap.set('rigplane-spectrum-split-ratio', '0.5');
    const target = mountPanel();
    const { separator } = splitElements(target);
    const key = (value: string) => {
      separator.dispatchEvent(new KeyboardEvent('keydown', { key: value, bubbles: true }));
      flushSync();
    };

    key('ArrowUp');
    expect(separator.getAttribute('aria-valuenow')).toBe('45');
    key('ArrowDown');
    expect(separator.getAttribute('aria-valuenow')).toBe('50');
    key('Home');
    expect(separator.getAttribute('aria-valuenow')).toBe('20');
    key('End');
    expect(separator.getAttribute('aria-valuenow')).toBe('78');

    separator.dispatchEvent(new WheelEvent('wheel', {
      deltaY: -100, bubbles: true, cancelable: true,
    }));
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    expect(mockRuntime.send).not.toHaveBeenCalled();
    expect(Number(storageMap.get('rigplane-spectrum-split-ratio')))
      .toBeCloseTo(1 - 80 / 372);
  });

  // MOR-2512 — Ctrl/Alt/Meta+Arrow belongs to the global keyboard map
  // (Ctrl+ArrowUp/Down adjust_af_level); the separator must neither step
  // nor swallow the event so the window-level global handler sees it.
  it('leaves Ctrl+ArrowUp to the global keyboard map', () => {
    storageMap.set('rigplane-spectrum-split-ratio', '0.5');
    const target = mountPanel();
    const { separator } = splitElements(target);
    separator.focus();
    const event = new KeyboardEvent('keydown', {
      key: 'ArrowUp', ctrlKey: true, bubbles: true, cancelable: true,
    });
    separator.dispatchEvent(event);
    flushSync();

    expect(separator.getAttribute('aria-valuenow')).toBe('50');
    expect(event.defaultPrevented).toBe(false);
  });
});

describe('SpectrumPanel Observation authority and final-gesture intents', () => {
  it('binds the existing handler singletons once and uses the merged selector with state and caps', () => {
    mountPanel();
    // EiBiBrowser binds the same shared singleton independently; the Panel's
    // own source call is asserted exactly below.
    expect(handlerHarness.getVfoHandlers).toHaveBeenCalledTimes(2);
    expect(handlerHarness.getFilterHandlers).toHaveBeenCalledOnce();
    expect(authorityHarness.toSpectrumAuthority).toHaveBeenCalledWith(
      mockRuntime.state,
      mockRuntime.caps,
    );
    expect(spectrumPanelSource.match(/getVfoHandlers\(\)/g)).toHaveLength(1);
    expect(spectrumPanelSource.match(/getFilterHandlers\(\)/g)).toHaveLength(1);
    expect(spectrumPanelSource).toContain('toSpectrumAuthority(runtime.state, runtime.caps)');
    expect(spectrumPanelSource).toContain('let scopeMode = $derived(frameScopeMode)');
    expect(spectrumPanelSource).not.toContain('function toSpectrumAuthority');
    expect(spectrumPanelSource).not.toContain('function snapSpectrumFilterWidth');
  });

  it('routes an actual WaterfallCanvas tap exactly once without a parent duplicate', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 4, 100);
    pointer(canvas, 'pointerup', 4, 100);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });

  it('routes a DX tune exactly once through the typed VFO singleton', () => {
    const target = mountPanel();
    emitFrame();
    runtimeHarness.state.capturedDxMessage?.({
      type: 'dx_spot',
      spot: {
        spotter: 'N0CALL', freq: 14_075_410, call: 'K1ABC', comment: 'test',
        time_utc: '1200', timestamp: 1,
      },
    });
    flushSync();
    target.querySelector<HTMLButtonElement>('.dx-badge')!.click();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_075_000, 0);
  });

  it('rejects tap and wheel when current Observation frequency is missing', () => {
    authorityHarness.state.current = authority({ frequencyHz: null });
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 5, 150);
    pointer(canvas, 'pointerup', 5, 150);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', {
      deltaY: -100, bubbles: true, cancelable: true,
    }));
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });

  it('submits a paced drag target and final snapped VFO intent on stable pointer-up', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 7, 100);
    pointer(spectrum, 'pointermove', 7, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
    pointer(spectrum, 'pointerup', 7, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  it('tunes a below-threshold spectrum release to the clicked frequency and emits zero for a no-change drag', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 8, 100);
    pointer(spectrum, 'pointermove', 8, 104);
    pointer(spectrum, 'pointerup', 8, 104);
    // MOR-2464 click-to-tune: below the drag threshold the release is a
    // click — it tunes to the clicked position (104/200 of the 100 kHz
    // span → 14_052_000 snapped), not a pan.
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_052_000, 0);

    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
    rect(spectrum, 0, 100_000);
    pointer(spectrum, 'pointerdown', 9, 100);
    pointer(spectrum, 'pointermove', 9, 106);
    pointer(spectrum, 'pointerup', 9, 106);
    // An above-threshold drag whose snapped target equals the observed
    // frequency stays silent — and must not fall through to a click tune.
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
  });

  it('matching drag cancel preserves the accepted move and never finalizes', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 10, 100);
    pointer(spectrum, 'pointermove', 10, 120);
    pointer(spectrum, 'pointercancel', 10, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
  });

  it('wrong-pointer up emits zero and preserves the real drag completion', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 11, 100);
    pointer(spectrum, 'pointermove', 11, 120);
    pointer(spectrum, 'pointerup', 12, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
    pointer(spectrum, 'pointerup', 11, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
  });

  it('keeps resize moves local and commits one snapped width with captured identity', () => {
    const captured = authority();
    authorityHarness.state.current = captured;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    expect(zone).not.toBeNull();
    pointer(zone, 'pointerdown', 21, 100);
    pointer(waterfall, 'pointermove', 21, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    pointer(waterfall, 'pointerup', 21, 130);
    expect(authorityHarness.snapSpectrumFilterWidth).toHaveBeenCalledWith(2_700, captured.rule);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_700, 0, 17);
  });

  it('projects each resize move immediately and keeps only the busy lifecycle target after release (MOR-1649)', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    const overlay = () => waterfall.querySelector<HTMLElement>('.passband-overlay')!;

    expect(overlay().style.width).toBe('24%');
    pointer(zone, 'pointerdown', 42, 100);
    passbandHarness.rawWidth = 2_500;
    pointer(waterfall, 'pointermove', 42, 120);
    expect(overlay().style.width).toBe('25%');
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    passbandHarness.rawWidth = 2_700;
    pointer(waterfall, 'pointermove', 42, 130);
    expect(overlay().style.width).toBe('27%');
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700));
    flushSync();
    pointer(waterfall, 'pointerup', 42, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority({ filterWidthHz: 2_700 });
    refreshSpectrumAuthority();
    setFilterWidthLifecycle({ confirmed: 2_700, phase: 'confirmed', presentation: {
      lifecycleId: 'width-2700', transitionId: 'width-2700-confirmed', receiver: 0,
      sessionEpoch: 17, target: 2_700, status: 'confirmed',
    } });
    flushSync();
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority();
    refreshSpectrumAuthority();
    for (const outcome of ['failed', 'timed-out', 'cancelled'] as const) {
      setFilterWidthLifecycle({
        target: null,
        phase: 'idle',
        busy: false,
        outcome: { phase: outcome },
        presentation: {
          lifecycleId: `width-2700-${outcome}`,
          transitionId: `width-2700-${outcome}`,
          receiver: 0,
          sessionEpoch: 17,
          target: 2_700,
          status: outcome,
        },
      });
      flushSync();
      expect(overlay().style.width).toBe('24%');
    }
  });

  it('does not project a busy width lifecycle for another receiver', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700, 1));
    flushSync();

    expect(waterfall.querySelector<HTMLElement>('.passband-overlay')?.style.width).toBe('24%');
  });

  it('abandons an active resize projection after receiver authority drifts', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    const overlay = () => waterfall.querySelector<HTMLElement>('.passband-overlay')!;

    pointer(zone, 'pointerdown', 43, 100);
    passbandHarness.rawWidth = 2_700;
    pointer(waterfall, 'pointermove', 43, 130);
    expect(overlay().style.width).toBe('27%');

    authorityHarness.state.current = authority({ receiver: 1 });
    refreshSpectrumAuthority();
    flushSync();
    expect(overlay()).toBeNull();

    pointer(waterfall, 'pointerup', 43, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('renders and resizes a fresh PBT-only IC-7300 passband via the production selector (MOR-1649)', () => {
    runtimeHarness.state.currentState = freshPbtOnlyIc7300State();
    runtimeHarness.state.currentCaps = IC7300_STEPPED_CAPABILITIES;
    authorityHarness.state.useProductionSelector = true;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone');

    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(zone).not.toBeNull();
    pointer(zone!, 'pointerdown', 41, 100);
    pointer(waterfall, 'pointermove', 41, 130);
    pointer(waterfall, 'pointerup', 41, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_800, 0, 1);
  });

  it('normalizes fixed-frame resize X around the captured carrier rather than sample center', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_025_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 22, 100);
    pointer(waterfall, 'pointermove', 22, 100);
    pointer(waterfall, 'pointerup', 22, 100);
    expect(passbandHarness.getFilterWidthFromRightEdgePx).toHaveBeenCalledWith(
      'USB', 0, 100_000, 200, 150, 5_000,
    );
  });

  it('matching resize cancel emits zero and a wrong pointer preserves the real completion', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 23, 100);
    pointer(waterfall, 'pointermove', 23, 130);
    pointer(waterfall, 'pointercancel', 23, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    pointer(zone, 'pointerdown', 24, 100);
    pointer(waterfall, 'pointermove', 24, 130);
    pointer(waterfall, 'pointerup', 25, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    pointer(waterfall, 'pointerup', 24, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
  });

  it.each([
    ['filter shape', { filterShape: 2 }],
    ['scope controls', { scopeControls: testScopeControls(3) }],
  ])('keeps resize completion through unrelated %s churn', (_label, overrides) => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 26, 100);
    pointer(waterfall, 'pointermove', 26, 130);
    authorityHarness.state.current = authority(overrides);
    pointer(waterfall, 'pointerup', 26, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledOnce();
  });

  it('rejects resize completion after passband identity or frame-geometry drift', () => {
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 27, 100);
    pointer(waterfall, 'pointermove', 27, 130);
    authorityHarness.state.current = authority({ frequencyHz: 14_050_100 });
    pointer(waterfall, 'pointerup', 27, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();

    authorityHarness.state.current = authority();
    pointer(zone, 'pointerdown', 27, 100);
    pointer(waterfall, 'pointermove', 27, 130);
    emitFrame({ endFreq: 14_101_000 });
    pointer(waterfall, 'pointerup', 27, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('rejects a resize candidate equal to the captured observed width', () => {
    passbandHarness.rawWidth = 2_400;
    const target = mountPanel();
    emitFrame();
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector<HTMLButtonElement>('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 28, 100);
    pointer(waterfall, 'pointermove', 28, 130);
    pointer(waterfall, 'pointerup', 28, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it.each([
    ['provider generation', { providerGeneration: 18 }],
    ['physical receiver', { receiver: 1 as const }],
    ['frequency', { frequencyHz: 14_050_300 }],
  ])('rejects final drag after %s drift', (_label, overrides) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 30, 100);
    pointer(spectrum, 'pointermove', 30, 120);
    authorityHarness.state.current = authority(overrides);
    pointer(spectrum, 'pointerup', 30, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
  });

  // MOR-1497: plain drag-to-pan only moves frequency, so drift in passband
  // fields it never reads must NOT abort an otherwise-stable pan. Before the
  // fix these all shared one full-digest recheck with passband-resize, which
  // made irrelevant field churn spuriously cancel a completed drag.
  it.each([
    ['mode', { mode: 'LSB' }],
    ['filter', { filter: 'FIL2' }],
    ['width', { filterWidthHz: 2_500 }],
    ['IF shift', { ifShiftHz: 50 }],
    ['PBT inner', { pbtInnerHz: 50 }],
    ['PBT outer', { pbtOuterHz: 50 }],
    ['filter shape', { filterShape: 2 }],
    ['DATA', { dataMode: 1 }],
    ['rule', { rule: Object.freeze({ kind: 'step' as const, minHz: 200, maxHz: 5_000, stepHz: 100 }) }],
    ['scope controls', { scopeControls: testScopeControls(3) }],
  ])('completes final drag despite %s drift, which plain panning does not depend on (MOR-1497)', (_label, overrides) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 33, 100);
    pointer(spectrum, 'pointermove', 33, 120);
    authorityHarness.state.current = authority(overrides);
    pointer(spectrum, 'pointerup', 33, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  // MOR-1497 regression: drag-to-pan was gated on completeGestureAuthority(false),
  // which required mode/filterWidthHz/ifShiftHz to all be non-null. On Icom
  // radios ifShiftHz is structurally unobservable (PBT-only, no IF-shift
  // command) so the gate returned null forever and every drag silently
  // no-op'd behind a grab cursor that promised it would work.
  it('completes a plain drag when filter width and IF shift are unobserved (MOR-1497)', () => {
    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null });
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 40, 100);
    pointer(spectrum, 'pointermove', 40, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
    pointer(spectrum, 'pointerup', 40, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  // MOR-1497: the grab cursor is decorative CSS unless it reflects whether a
  // drag is actually possible — withhold it exactly when the drag-start gate
  // (frequency authority AND usable sample geometry) would refuse the gesture.
  it('withholds the draggable cursor class until both frequency authority and scope geometry exist (MOR-1497)', () => {
    authorityHarness.state.current = authority({ frequencyHz: null });
    const withoutFreq = mountPanel();
    expect(withoutFreq.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(false);
    expect(withoutFreq.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(false);

    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null });
    const withFreqOnly = mountPanel();
    // Before the first scope frame the sample geometry is unusable
    // (endFreq <= startFreq) and handleDragStart would bail — the cursor
    // must not promise a drag that cannot happen.
    expect(withFreqOnly.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(false);
    expect(withFreqOnly.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(false);

    emitFrame();
    expect(withFreqOnly.querySelector('.spectrum-area')?.classList.contains('draggable')).toBe(true);
    expect(withFreqOnly.querySelector('.waterfall-content')?.classList.contains('draggable')).toBe(true);
  });

  it.each([
    ['frame mode', { mode: 1 }],
    ['start edge', { startFreq: 13_999_000 }],
    ['end edge', { endFreq: 14_101_000 }],
  ])('rejects final drag after %s drift', (_label, frameOverride) => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 31, 100);
    pointer(spectrum, 'pointermove', 31, 120);
    emitFrame(frameOverride);
    pointer(spectrum, 'pointerup', 31, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
  });

  it('rejects final drag after measured element-width drift', () => {
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 32, 100);
    pointer(spectrum, 'pointermove', 32, 120);
    rect(spectrum, 0, 240);
    pointer(spectrum, 'pointerup', 32, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_040_000, 0, 'drag');
  });

  it('hides confirmed carrier/passband for missing Observation while retaining sample plane pixels', async () => {
    authorityHarness.state.current = null;
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.freq-axis')).not.toBeNull();
    // A mode-less v2 frame does not prove CENTER when control facts are missing.
    expect(target.querySelectorAll('.spectrum-area .tune-line, .waterfall-content .tune-line')).toHaveLength(0);
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject({ spanHz: 100_000, sampleSpanHz: 100_000, showTuneIndicator: false, tuneHz: 0, passbandHz: 0 });
    expect(spectrumRendererHarness.render.mock.calls.at(-1)?.[1]).toHaveLength(475);
  });

  it('hides confirmed carrier/passband when Observation is outside fixed sample edges', async () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_200_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    expect(target.querySelector('.tune-line')).toBeNull();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject({ spanHz: 100_000, sampleSpanHz: 100_000, showTuneIndicator: false, tuneHz: 0, passbandHz: 0 });
  });

  it('keeps frame mode and pixel geometry authoritative over canonical scope-control metadata', async () => {
    authorityHarness.state.current = authority({
      frequencyHz: 14_025_000,
      scopeControls: testScopeControls(3),
    });
    const target = mountPanel();
    emitFrame({ mode: 0 });
    expect(target.querySelector<HTMLElement>('.tune-line')?.style.left).toBe('50%');
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions.scopeMode).toBe(0);
    expect(spectrumRendererHarness.render.mock.calls.at(-1)?.[1]).toHaveLength(475);
  });

  it.each([
    ['mode', { mode: null }],
    ['width', { filterWidthHz: null }],
    ['IF shift', { ifShiftHz: null }],
  ])('never defaults missing %s into a confirmed passband', (_label, overrides) => {
    authorityHarness.state.current = authority(overrides);
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });

  it('allows a confirmed overlay but no resize permission when the rule is missing', () => {
    authorityHarness.state.current = authority({ rule: null });
    const target = mountPanel();
    emitFrame();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });

  it('rejects fixed resize capture when confirmed frequency is outside the sample plane', () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_200_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('shrinks the scope-metadata owners to the single ingress adapter (MOR-4270)', () => {
    const owners = authorityPluginSource.match(
      /const SCOPE_METADATA_OWNERS = new Set\(\[([\s\S]*?)\]\);/,
    )!;
    expect([...owners[1].matchAll(/'([^']+)'/g)].map((match) => match[1])).toEqual([
      'src/lib/runtime/adapters/scope-adapter.ts',
    ]);
    expect(owners[1]).not.toContain('SpectrumPanel.svelte');
    expect(owners[1]).not.toContain('AmberScope');
    expect(owners[1]).not.toContain('AmberCockpit');
    expect(owners[1]).not.toContain('AudioSpectrumPanel');
    expect(owners[1]).not.toContain('scope-stream.ts');
    expect(owners[1]).not.toContain('scope-frame.ts');
    expect(authorityContractSource).toContain('id = "spectrum_payload"');
    const guard = authorityContractSource.split('id = "scope_metadata_owner"')[1].split('[[guards]]')[0];
    expect([...guard.matchAll(/path = "([^"]+)", count = (\d+)/g)].map((match) => [match[1], Number(match[2])])).toEqual([
      ['src/lib/runtime/adapters/scope-adapter.ts', 1],
    ]);
    const payload = authorityContractSource.split('id = "spectrum_payload"')[1].split('[[families]]')[0];
    expect(payload.match(/surfaces = \[(.*?)\]/)?.[1]).toBe('"frontend/src/components/spectrum/SpectrumPanel.svelte"');
  });
});

// MOR-3677 — the passband-unknown chip: when the radio's width answer is
// honest-but-Hz-less (its own mode default, NARROW, or an observed "no Hz
// for this mode"), the spectrum says WHY with a text chip instead of
// silently drawing no passband geometry at all. The chip is NOT gated on
// `tuneVisible` and never draws an overlay, a shaded region, or a
// full-width fill — there is no geometry to draw.
describe('SpectrumPanel passband-unknown chip (MOR-3677)', () => {
  function mountWithDisplay(display: FilterWidthDisplay | null): HTMLElement {
    authorityHarness.state.current = authority({
      filterWidthDisplay: display,
      filterWidthHz: display !== null && display.state === 'known' ? display.hz : null,
    });
    const target = mountPanel();
    emitFrame();
    return target;
  }

  it.each([
    ['radio-default', { state: 'radio-default' } as FilterWidthDisplay, 'Radio default'],
    ['narrow', { state: 'narrow' } as FilterWidthDisplay, 'Narrow (radio)'],
  ])('renders the %s chip with its value text and no overlay', (_label, display, text) => {
    const target = mountWithDisplay(display);
    const chip = target.querySelector<HTMLElement>('[data-testid="passband-unknown"]');
    expect(chip).not.toBeNull();
    expect(chip!.dataset.state).toBe(display.state);
    expect(chip!.textContent).toBe(text);
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });

  it('renders the not-reported chip and never a fabricated overlay', () => {
    const target = mountWithDisplay({ state: 'unknown', reason: 'not-reported' });
    const chip = target.querySelector<HTMLElement>('[data-testid="passband-unknown"]');
    expect(chip).not.toBeNull();
    expect(chip!.dataset.state).toBe('not-reported');
    expect(chip!.textContent).toBe('The radio does not report a filter width for this mode.');
    expect(target.querySelector('.passband-overlay')).toBeNull();
  });

  it.each([
    ['known', { state: 'known', hz: 2_400, stale: false } as FilterWidthDisplay],
    ['known stale', { state: 'known', hz: 2_400, stale: true } as FilterWidthDisplay],
    ['not-observed', { state: 'unknown', reason: 'not-observed' } as FilterWidthDisplay],
    ['unsupported', { state: 'unsupported' } as FilterWidthDisplay],
    ['absent', null],
  ])('draws NO chip for the %s display', (_label, display) => {
    const target = mountWithDisplay(display);
    expect(target.querySelector('[data-testid="passband-unknown"]')).toBeNull();
  });
});


// MOR-1369 (v3-rework S6b-1) — `hideScopeControls` is a pure pass-through to
// the real (unmocked) SpectrumToolbar; no logic of its own lives here. One
// direct DOM check per direction is enough to prove the wire is connected —
// SpectrumToolbar.component.test.ts owns the exhaustive fact-backed/
// view-option split.
// MOR-2851 — the portrait phone hides the whole toolbar above the panorama;
// every other layout keeps it. Pure suppression: the panorama itself and the
// panel's own behaviour are unchanged.
describe('SpectrumPanel hideToolbar (MOR-2851)', () => {
  it('renders the toolbar by default', () => {
    const target = mountPanel();
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
  });

  it('renders no toolbar with hideToolbar=true, keeping the panorama', () => {
    const target = mountPanel({ hideToolbar: true });
    expect(target.querySelector('.spectrum-toolbar')).toBeNull();
    expect(target.querySelector('.spectrum-split-region')).not.toBeNull();
  });
});

describe('SpectrumPanel hideScopeControls pass-through (MOR-1369, S6b-1)', () => {
  it('omitting the prop keeps the toolbar fact-backed half reachable (default false)', () => {
    const target = mountPanel();
    const holdBtn = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'))
      .find((b) => b.textContent?.trim() === 'HOLD');
    expect(holdBtn).toBeDefined();
  });

  it('forwards hideScopeControls=true to SpectrumToolbar, hiding the fact-backed half', () => {
    const target = mountPanel({ hideScopeControls: true });
    const buttons = Array.from(target.querySelectorAll<HTMLButtonElement>('.toolbar-btn'));
    const labels = buttons.map((b) => b.textContent?.trim());
    expect(labels).not.toContain('HOLD');
    // the view-options half is unaffected by the forwarded prop
    expect(labels).toContain('AVG');
    expect(labels).toContain('PEAK');
  });
});

// MOR-1456 — the "Go to Waterfall" (`g w`) keyboard shortcut. Neither the
// spectrum `<canvas>` nor the waterfall `<canvas>` (`WaterfallCanvas.svelte`)
// is natively focusable, and `ScopeDisplaySurface.svelte` (the semantic
// scope-display readout) is a bare readout with ZERO focusable elements by
// construction (MOR-1069) — the panel root's own `data-waterfall`/
// `tabindex="-1"` is the one stable anchor `focus_target` can resolve to.
// Lives here, not alongside the other `focus_target` shortcuts in
// `panel-commands/__tests__/focus-target-resolution.component.test.ts`,
// because only THIS file already carries the canvas/ResizeObserver/rAf
// mocking a real `SpectrumPanel` mount needs.
describe('"waterfall" focus_target shortcut resolves to the real SpectrumPanel root (MOR-1456)', () => {
  it('"g w" focuses the spectrum panel root', async () => {
    const { makeKeyboardHandlers } = await import('$lib/runtime/commands/panel-commands');
    const target = mountPanel();
    const root = target.querySelector('[data-waterfall]');
    expect(root).not.toBeNull();

    makeKeyboardHandlers().dispatch({ action: 'focus_target', params: { target: 'waterfall' } });

    expect(document.activeElement).toBe(root);
  });
});

type ScopeStatusProbe = Parameters<typeof deriveScopeIndicatorState>[0];

function scopeStatus(
  overrides: Partial<ScopeStatusProbe> = {},
): ScopeStatusProbe {
  return {
    source: 'hardware',
    available: true,
    resourceSelected: true,
    demand: 1,
    lifecycle: 'streaming',
    transport: 'connected',
    frameSeen: true,
    ...overrides,
  };
}

describe('StatusBar default scope status consumption', () => {
  it('uses only the canonical runtime default and removes the legacy reader', () => {
    expect(statusBarSource).toContain('runtime.defaultScopeStatus');
    expect(statusBarSource).not.toContain('isScopeConnected');
    expect(statusBarSource).not.toContain('hardwareScopeConnected');
    expect(statusBarSource).not.toContain('audioScopeFrame');
  });

  it.each([
    ['power-off override', scopeStatus(), true, 'disconnected'],
    ['power-off precedes inactive facts', scopeStatus({ source: null, demand: 0 }), true, 'disconnected'],
    ['no default source', scopeStatus({ source: null }), false, 'inactive'],
    ['unavailable default', scopeStatus({ available: false }), false, 'inactive'],
    ['unselected resource', scopeStatus({ resourceSelected: false }), false, 'inactive'],
    ['zero demand', scopeStatus({ demand: 0 }), false, 'inactive'],
    ['starting host', scopeStatus({ lifecycle: 'starting', transport: 'disconnected', frameSeen: false }), false, 'starting'],
    ['connecting transport', scopeStatus({ transport: 'connecting', frameSeen: false }), false, 'connecting'],
    ['reconnecting transport', scopeStatus({ transport: 'reconnecting', frameSeen: false }), false, 'reconnecting'],
    ['waiting for current frame', scopeStatus({ frameSeen: false }), false, 'waiting'],
    ['failed host under demand', scopeStatus({ lifecycle: 'failed' }), false, 'failed'],
    ['disconnected transport under demand', scopeStatus({ transport: 'disconnected', frameSeen: false }), false, 'disconnected'],
    ['healthy hardware default', scopeStatus(), false, 'connected'],
    ['healthy audio default uses identical facts', scopeStatus({ source: 'audio_fft' }), false, 'connected'],
    ['non-streaming lifecycle is not green', scopeStatus({ lifecycle: 'inactive' }), false, 'inactive'],
  ] as const)('%s maps to %s', (_label, status, poweredOff, expected) => {
    expect(deriveScopeIndicatorState(status, poweredOff)).toBe(expected);
  });

  it.each([
    ['inactive', 'neutral'],
    ['starting', 'yellow'],
    ['connecting', 'yellow'],
    ['waiting', 'yellow'],
    ['reconnecting', 'yellow'],
    ['failed', 'red'],
    ['disconnected', 'red'],
    ['connected', 'green'],
  ] as const)('%s uses the %s tone', (state, expected) => {
    expect(indicatorTone(state)).toBe(expected);
  });
});


describe('opaque semantic scope snippet forwarding (MOR-2358)', () => {
  const scopeControls = createRawSnippet(() => ({ render: () => '<div data-testid="hosted-scope-probe">Semantic scope probe</div>' }));
  it('renders the opaque snippet once inside the hardware toolbar', () => {
    const target = mountPanel({ hideScopeControls: true, scopeControls });
    expect(target.querySelectorAll('[data-testid="hosted-scope-probe"]')).toHaveLength(1);
    expect(target.querySelector('[data-testid="hosted-scope-probe"]')?.closest('.spectrum-toolbar')).not.toBeNull();
    expect(target.querySelector('.settings-group')).toBeNull();
  });
  it('keeps audio FFT source label and Viewer without rendering the hardware snippet', () => {
    runtimeHarness.state.currentCaps = { scopeSource: 'audio_fft' };
    const target = mountPanel({ hideScopeControls: true, scopeControls });
    expect(target.querySelector('[data-testid="hosted-scope-probe"]')).toBeNull();
    expect(target.querySelector('.spectrum-toolbar')).toBeNull();
    expect(target.querySelector('.audio-source-label')?.textContent).toContain('Audio FFT · AF');
    // Audio FFT keeps no carrier reference at all (MOR-2464 ruling).
    expect(target.querySelector('.tune-line')).toBeNull();
    const viewer = target.querySelector<HTMLButtonElement>('[aria-label="Scope viewer"]')!;
    expect(viewer.textContent).toContain('Viewer ON'); viewer.click(); flushSync(); expect(viewer.textContent).toContain('Viewer OFF');
    expect(streamSubscriptions.hardware).not.toHaveBeenCalled();
  });
});

describe('SDR scope source routing (MOR-3158)', () => {
  // The public `sdr` leaf is the generated `SdrStatusPublic` contract
  // (MOR-3201); it rides `currentState` like every other raw field.
  function sdrLeaf(overrides: Record<string, unknown> = {}) {
    return Object.freeze({
      state: 'streaming', device: 'rtlsdr=0', sampleRateHz: 2_400_000,
      spanHz: 2_000_000, txFrozen: false, overflowCount: 0, lastError: null,
      ...overrides,
    });
  }
  function selectSdrSource(sdr: unknown = sdrLeaf()): void {
    runtimeHarness.state.currentCaps = Object.freeze({ scopeSource: 'sdr' });
    runtimeHarness.state.currentState = Object.freeze({
      source: 'test-state', providerGeneration: 17, active: 'MAIN', sdr,
    });
    refreshSpectrumAuthority();
  }
  function replaceSdrLeaf(sdr: unknown): void {
    runtimeHarness.state.currentState = Object.freeze({
      ...(runtimeHarness.state.currentState as Record<string, unknown>), sdr,
    });
    refreshSpectrumAuthority();
  }

  it('routes the SDR source over the hardware path and mounts only the badge', () => {
    selectSdrSource();
    const target = mountPanel();
    // Hardware transport routing: the IQ-derived ScopeFrames arrive on the
    // hardware channel with its leases (MOR-3158 contract assumption).
    expect(harnessChannels.scope?.connect).toHaveBeenCalledOnce();
    expect(streamSubscriptions.hardware).toHaveBeenCalledTimes(1);
    expect(streamSubscriptions.audio_fft).not.toHaveBeenCalled();
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledOnce();
    expect(target.querySelector('.audio-source-label')).toBeNull();
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge).not.toBeNull();
    expect(badge.closest('.spectrum-toolbar')).not.toBeNull();
    expect(badge.dataset.source).toBe('sdr');
    expect(badge.dataset.sdrState).toBe('streaming');
    expect(badge.dataset.tone).toBe('green');
    // A single advertised source renders no selector.
    expect(target.querySelector('[data-testid="scope-source-selector"]')).toBeNull();
    // RF geometry like the hardware path: an axis appears with the frame.
    expect(target.querySelector('.freq-axis')).toBeNull();
    emitFrame();
    expect(target.querySelector('.freq-axis')).not.toBeNull();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
  });

  it('renders an uncoloured badge and no TX hold while the sdr leaf is absent', () => {
    runtimeHarness.state.currentCaps = Object.freeze({ scopeSource: 'sdr' });
    runtimeHarness.state.currentState = Object.freeze({
      source: 'test-state', providerGeneration: 17, active: 'MAIN',
    });
    const target = mountPanel();
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.dataset.tone).toBe('neutral');
    expect(badge.dataset.sdrState).toBeUndefined();
    expect(badge.title).toBe('Scope source: SDR');
    expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
  });

  it('keeps the layout status indicator and adds the selector beside the badge when another source is advertised', () => {
    const statusProbe = createRawSnippet(() => ({ render: () => '<span data-testid="status-probe"></span>' }));
    runtimeHarness.state.currentCaps = Object.freeze({
      scopeSource: 'sdr', scope: true, audioFftAvailable: true,
    });
    runtimeHarness.state.currentState = Object.freeze({
      source: 'test-state', providerGeneration: 17, active: 'MAIN', sdr: sdrLeaf(),
    });
    const target = mountPanel({ scopeStatusIndicator: statusProbe });
    expect(target.querySelector('[data-testid="status-probe"]')?.closest('.spectrum-toolbar')).not.toBeNull();
    const selector = target.querySelector<HTMLElement>('[data-testid="scope-source-selector"]')!;
    expect(selector).not.toBeNull();
    const keys = [...selector.querySelectorAll<HTMLElement>('.scope-source-key')];
    expect(keys.map((key) => key.dataset.source)).toEqual(['hardware', 'audio_fft', 'sdr']);
    expect(keys.map((key) => key.textContent)).toEqual(['RIG', 'AUDIO', 'SDR']);
    expect(keys.at(-1)!.dataset.active).toBe('true');
    expect(selector.contains(target.querySelector('[data-testid="scope-source-badge"]'))).toBe(false);
  });

  it('colours the badge by the SDR state and carries lastError in its tooltip', () => {
    selectSdrSource(sdrLeaf({ state: 'error', lastError: 'SoapySDR: device busy' }));
    const target = mountPanel();
    const badge = target.querySelector<HTMLElement>('[data-testid="scope-source-badge"]')!;
    expect(badge.dataset.tone).toBe('red');
    expect(badge.dataset.sdrState).toBe('error');
    expect(badge.title).toBe('Scope source: SDR error — SoapySDR: device busy');
    replaceSdrLeaf(sdrLeaf({ state: 'reconnecting', lastError: null }));
    flushSync();
    expect(badge.dataset.tone).toBe('yellow');
    expect(badge.dataset.sdrState).toBe('reconnecting');
  });

  it('tunes a below-threshold SDR-frame click to the clicked RF frequency through the existing VFO intent', () => {
    selectSdrSource();
    const target = mountPanel();
    emitFrame();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 30, 100);
    pointer(spectrum, 'pointerup', 30, 104);
    // 104/200 of the 100 kHz SDR frame span → 14_052_000 snapped to the
    // 1 kHz step — the same set_frequency seam the hardware path uses.
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_052_000, 0);
  });

  it('holds the waterfall under the TX marker while sdr.txFrozen and resumes on release', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    try {
      selectSdrSource();
      const target = mountPanel();
      emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(1);
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
      replaceSdrLeaf(sdrLeaf({ txFrozen: true }));
      flushSync();
      const marker = target.querySelector<HTMLElement>('[data-testid="sdr-tx-frozen"]')!;
      expect(marker).not.toBeNull();
      expect(marker.closest('.waterfall-history')).not.toBeNull();
      clock += 1; emitFrame({ outOfRange: true });
      clock += 1; emitFrame({ outOfRange: true });
      await flushWaterfallPaint();
      // Frozen: no new row scrolls — the last pre-TX panorama stays.
      expect(push).toHaveBeenCalledTimes(1);
      expect(target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
      expect(target.querySelector<HTMLElement>('.waterfall-history')!.style.visibility).toBe('visible');
      expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
      replaceSdrLeaf(sdrLeaf({ txFrozen: false }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
      // Unfrozen: with no spacing-based suppression left at the
      // registration (MOR-4206), painting resumes immediately.
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(2);
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(3);
    } finally {
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });

  it('paints the first live frame after a >3 s delivery silence during a TX freeze', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    try {
      selectSdrSource();
      const target = mountPanel();
      emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(1);
      // TX freeze with a silent source: deliveries stop during the freeze,
      // and with no skip-window suppression left at the registration
      // (MOR-4206) the 4 s silence costs no live rows afterwards.
      replaceSdrLeaf(sdrLeaf({ txFrozen: true }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).not.toBeNull();
      clock += 4_000;
      replaceSdrLeaf(sdrLeaf({ txFrozen: false }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
      // The first live frame after the freeze paints immediately — no
      // multi-row suppression after a normal silence — and painting
      // continues.
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(2);
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(3);
    } finally {
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });

  it('paints burst-spaced live frames after a long flowing TX freeze (MOR-4206)', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    try {
      selectSdrSource();
      const target = mountPanel();
      emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(1);
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(2);
      replaceSdrLeaf(sdrLeaf({ txFrozen: true }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).not.toBeNull();
      // Flowing freeze: the SDR keeps delivering through the freeze at its
      // production cadence (fps=20 → 50 ms) for 82 frames = 4.1 s. Every
      // frozen delivery is withheld from the waterfall (MOR-3158).
      for (let i = 0; i < 82; i += 1) {
        clock += 50; emitFrame();
      }
      await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(2);
      replaceSdrLeaf(sdrLeaf({ txFrozen: false }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).toBeNull();
      // Live deliveries right after unfreeze can batch tightly. With
      // backlog handling consolidated into the rAF pump there is no
      // spacing-based suppression left at the registration, so every
      // burst-spaced live frame paints in its own animation frame.
      for (let i = 0; i < 5; i += 1) {
        clock += 1; emitFrame(); await flushWaterfallPaint();
      }
      expect(push).toHaveBeenCalledTimes(7);
      clock += 83; emitFrame(); await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(8);
    } finally {
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });

  it('keeps retained binary OOR pixels live under TX freeze but clears an empty OOR before recovery', async () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
    selectSdrSource();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    const target = mountPanel();
    emitFrame({ pixels: new Uint8Array([10, 80]), sequence: 1 });
    await flushWaterfallPaint();
    expect(push).toHaveBeenCalledTimes(1);
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    replaceSdrLeaf(sdrLeaf({ txFrozen: true })); flushSync();
    emitFrame({ pixels: new Uint8Array([10, 80]), sequence: 2, outOfRange: true });
    await flushWaterfallPaint();
    expect([...spectrumRendererHarness.render.mock.calls.at(-1)![1]]).toEqual([10, 80]);
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    expect(target.querySelector('.scope-hatch[data-reason="outOfRange"]')).toBeNull();
    expect(target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
    expect(target.querySelector('.scope-disconnected-overlay')).toBeNull();
    expect(push).toHaveBeenCalledTimes(1);
    const renders = spectrumRendererHarness.render.mock.calls.length;
    emitFrame({ pixels: new Uint8Array(0), sequence: 3, outOfRange: true });
    await flushWaterfallPaint();
    expect(target.querySelector('[data-testid="scope-out-of-range"]')).not.toBeNull();
    expect(target.querySelector('.spectrum-area canvas')).toBeNull();
    expect(spectrumRendererHarness.render).toHaveBeenCalledTimes(renders);
    expect(push).toHaveBeenCalledTimes(1);
    replaceSdrLeaf(sdrLeaf({ txFrozen: false })); flushSync();
    emitFrame({ pixels: new Uint8Array([1, 2, 3]), sequence: 4 });
    await flushWaterfallPaint();
    expect([...spectrumRendererHarness.render.mock.calls.at(-1)![1]]).toEqual([1, 2, 3]);
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    expect(target.querySelector('.scope-hatch[data-reason="outOfRange"]')).toBeNull();
    expect(target.querySelector('.scope-disconnected-overlay')).toBeNull();
    expect(push).toHaveBeenCalledTimes(2);
  });

  it('holds managed waterfall pushes under the TX marker too', async () => {
    function sdrProjection(sequence: number, outOfRange = false) {
      emitFrame({
        startFreq: 14_000_000, endFreq: 14_100_000, sequence,
        outOfRange, pixels: new Uint8Array([0, 128, 255]),
      });
      return Object.freeze({ state: 'current' as const, tuple: Object.freeze({
        frequencyHz: 14_050_250, mode: 'USB', widthHz: 2_400, shiftHz: 0,
        frameMode: 0, startHz: 14_000_000, endHz: 14_100_000 }) });
    }
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    try {
      selectSdrSource();
      harnessLeases.push(mockRuntime.acquireHardwareScope());
      const props = new SvelteMap<string, unknown>([['projection',
        Object.freeze({ state: 'unknown', reason: 'not-observed' })]]);
      const target = mountPanel({
        get scopeRegion() {
          return Object.freeze({ passband: props.get('projection'),
            demanded: true, setDemand: () => {} });
        },
        scopeDemanded: true,
      });
      props.set('projection', sdrProjection(1));
      await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(1);
      // The first delivered frame paints (fresh gate) and the next
      // live-spaced one latches steady painting.
      for (let sequence = 2; sequence <= 6; sequence += 1) {
        clock += 83;
        props.set('projection', sdrProjection(sequence)); flushSync();
        await flushWaterfallPaint();
      }
      expect(push).toHaveBeenCalledTimes(6);
      replaceSdrLeaf(sdrLeaf({ txFrozen: true }));
      flushSync();
      expect(target.querySelector('[data-testid="sdr-tx-frozen"]')).not.toBeNull();
      clock += 83;
      props.set('projection', sdrProjection(7, true)); flushSync();
      await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(6);
      expect(target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
      expect(target.querySelector<HTMLElement>('.waterfall-history')!.style.visibility).toBe('visible');
      expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
      replaceSdrLeaf(sdrLeaf({ txFrozen: false }));
      flushSync();
      clock += 83;
      props.set('projection', sdrProjection(8)); flushSync();
      await flushWaterfallPaint();
      expect(push).toHaveBeenCalledTimes(7);
    } finally {
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });
});

describe('managed scope projection (MOR-2367)', () => {
  // MOR-4270: the managed region carries the passband display; frames and
  // geometry come from the shared stream (emitFrame drives the fake
  // channel) and the 'projection gap' is frame silence (health frameLive
  // forced false), exactly the observation the old null projection modelled.
  function passband(state: 'current' | 'stale' | 'unknown' | 'unsupported' = 'current', frameMode = 0) {
    return state === 'current' || state === 'stale'
      ? Object.freeze({ state, tuple: Object.freeze({ frequencyHz: 14_050_250, mode: 'USB',
        widthHz: 2_400, shiftHz: 0, frameMode, startHz: 14_000_000, endHz: 14_100_000 }) })
      : Object.freeze({ state, reason: 'not-observed' });
  }
  function projection(state: 'current' | 'stale' | 'unknown' | 'unsupported' = 'current', frameMode = 0) {
    return passband(state, frameMode);
  }
  // MOR-2464: the static CENTER reference must appear in BOTH the spectrum
  // area and over the waterfall — asserted together, never via one generic
  // `.tune-line` query.
  function expectCenterReference(target: HTMLElement, present: boolean): void {
    const refs = target.querySelectorAll('.spectrum-area .tune-line, .waterfall-content .tune-line');
    if (present) {
      expect(refs).toHaveLength(2);
      refs.forEach((el) => expect((el as HTMLElement).style.left).toBe('50%'));
    } else {
      expect(refs).toHaveLength(0);
    }
  }
  function managed(initial: ScopePassbandDisplay | null | undefined) {
    // The wiring's managed lease, stood in for by the harness: frames must
    // flow through the shared stream exactly as they do under SRS.
    harnessLeases.push(sharedLeaseAcquisitions());
    const props = new SvelteMap<string, unknown>([['projection', initial], ['demanded', true]]);
    const onScopeDemandChange = vi.fn((enabled: boolean) => props.set('demanded', enabled));
    const target = mountPanel({
      get scopeRegion() {
        return props.get('projection') === null ? Object.freeze({
          passband: Object.freeze({ state: 'unknown', reason: 'not-observed' }),
          demanded: props.get('demanded') as boolean,
          setDemand: onScopeDemandChange,
        }) : Object.freeze({
          passband: props.get('projection'),
          demanded: props.get('demanded') as boolean,
          setDemand: onScopeDemandChange,
        });
      },
      get scopeDemanded() { return props.get('demanded') as boolean; }, onScopeDemandChange,
    });
    emitFrame();
    return { target, props, onScopeDemandChange };
  }
  it('does not paint a managed passband when the requested preset excludes its carrier', async () => {
    const media = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: noop, removeEventListener: noop });
    const presets = await import('$lib/runtime/scope-presets');
    const spy = vi.spyOn(presets, 'scopePresets').mockReturnValue([
      { kind: 'cw', label: 'CW segment', view: { startHz: 14_000_000, endHz: 14_040_000 } },
    ]);
    try {
      const current = passband();
      if (!('tuple' in current)) throw new Error('Expected a current passband');
      const { target } = managed(Object.freeze({ ...current, tuple: Object.freeze({ ...current.tuple!, frequencyHz: 14_050_000 }) }));
      const select = target.querySelector<HTMLSelectElement>('[data-testid="scope-preset-select"]')!;
      select.value = '0'; select.dispatchEvent(new Event('change', { bubbles: true }));
      flushSync(); await flushWaterfallPaint();
      const { renderSpectrum } = await vi.importActual<typeof import('$lib/renderers/spectrum-renderer')>('$lib/renderers/spectrum-renderer');
      const fills: string[] = [];
      const ctx = { ...noopCanvas, fillStyle: '', setLineDash: noop, fillRect() { fills.push(this.fillStyle); } };
      const options = spectrumRendererHarness.lastOptions;
      recordCanvasLayers(ctx as unknown as CanvasRenderingContext2D);
      renderSpectrum(ctx as unknown as CanvasRenderingContext2D, new Uint8Array(100).fill(80), 400, 100, options);
      expect(options).toMatchObject({ sampleSpanHz: 100_000, spanHz: 40_000, tuneHz: 0, showTuneIndicator: false });
      expect(fills).not.toContain(options.passbandFillColor);
      expect(target.querySelector('.passband-overlay')).toBeNull();
    } finally { spy.mockRestore(); window.matchMedia = media; }
  });

  /** The managed data-plane gap: transport drops and frames stop flowing. */
  function silenceFrames(): void {
    runtimeHarness.state.mockScopeFrameLive = false;
    harnessChannels.scope?.setState('disconnected');
    flushSync();
  }
  function resumeFrames(): void {
    runtimeHarness.state.mockScopeFrameLive = true;
    harnessChannels.scope?.setState('connected');
    flushSync();
    emitFrame();
  }
  function resumeFramesWith(pixels: Uint8Array): void {
    runtimeHarness.state.mockScopeFrameLive = true;
    harnessChannels.scope?.setState('connected');
    flushSync();
    emitFrame({ pixels });
  }
  function cleared(target: HTMLElement) {
    // The static CENTER tune-line is deliberately NOT in this list: it is
    // reference-only and survives projection/demand gaps per call-site
    // expectations (MOR-2464 focus-gap fix). The data plane clears.
    for (const selector of ['.spectrum-area canvas', '.freq-axis', '.passband-overlay', '.passband-resize-zone', '.draggable']) {
      expect(target.querySelector(selector), selector).toBeNull();
    }
  }

  it.each(['managed', 'raw'] as const)('shows captured OOR edges and CTR action on the %s path, then recovers', (path) => {
    radio.current = structuredClone(IC7300_STATE);
    expect(setCapabilities(structuredClone(IC7300_CAPABILITIES))).toBe(true);
    // Recorded IC-705 out-of-range bytes (tests/fixtures/ic705/scope_out_of_range.json):
    // FIX mode, receiver MAIN, 144.000–144.500 MHz, flags bit 0 set, no pixels.
    const mounted = path === 'managed' ? managed(passband()) : { target: mountPanel(), props: null };
    if (path === 'raw') { emitFrame(); flushSync(); }
    emitFrame({ mode: 1, startFreq: 144_000_000, endFreq: 144_500_000,
      outOfRange: true, pixels: new Uint8Array(0) });
    flushSync();
    const overlay = mounted.target.querySelector('[data-testid="scope-out-of-range"]');
    expect(overlay).not.toBeNull();
    expect(overlay!.textContent).toContain('Frequency outside the fixed scope edges');
    expect(overlay!.textContent).toContain('144.000–144.500 MHz');
    expect(mounted.target.textContent).not.toContain('Scope disconnected');
    overlay!.querySelector<HTMLButtonElement>('button')!.click(); flushSync();
    expect(scopeCommandHarness.dispatch).toHaveBeenCalledExactlyOnceWith({
      name: 'set_scope_mode', params: { mode: 0 },
    });
    if (mounted.props) { resumeFrames(); }
    else emitFrame();
    expect(mounted.target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
  });
  it.each([null, 'current'] as const)('never acquires a raw hardware lease for managed %s', (kind) => {
    const { target } = managed(kind === null ? null : projection());
    expect(sharedLeaseAcquisitions).toHaveBeenCalledTimes(1);
    expect(streamSubscriptions.hardware).toHaveBeenCalledTimes(1);
    expect(harnessChannels.scope.connect).toHaveBeenCalledTimes(1);
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    if (kind === null) {
      silenceFrames();
      cleared(target);
      // MOR-2464: with frequency evidence the static CENTER reference
      // stays visible in BOTH areas through the frame-silence gap.
      expectCenterReference(target, true);
    } else expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });
  it('preserves waterfall history across a transient frame silence', () => {
    const destroy = vi.spyOn(WaterfallRenderer.prototype, 'destroy');
    const { target } = managed(projection());
    const before = destroy.mock.calls.length;
    silenceFrames();
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    resumeFrames();
    expect(destroy.mock.calls.length).toBe(before);
    expect(target.querySelector('.waterfall-content canvas')).not.toBeNull();
    destroy.mockRestore();
  });
  it('keeps a recording through frame silence but ends it on selection change without a frame', () => {
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', '1');
    const { target } = managed(projection());
    target.querySelector<HTMLButtonElement>('[aria-label="Scope render trace"] button')!.click();
    flushSync();
    silenceFrames();
    expect(target.querySelector('[aria-label="Scope render trace"]')?.textContent).toContain('recording');
    runtimeHarness.state.currentCaps = Object.freeze({ scopeSource: 'audio_fft' });
    refreshSpectrumAuthority();
    flushSync();
    expect(target.querySelector('[aria-label="Scope render trace"]')?.textContent).toContain('stopped (source_changed)');
  });
  it('discards waterfall history when provider generation changes', () => {
    const generations = new SvelteMap([['current', 17]]);
    const original = Object.getOwnPropertyDescriptor(runtimeHarness.state, 'currentState')!;
    Object.defineProperty(runtimeHarness.state, 'currentState', {
      configurable: true, get: () => ({ providerGeneration: generations.get('current') }),
    });
    try {
      const { target } = managed(projection());
      const first = target.querySelector('.waterfall-content canvas');
      silenceFrames();
      generations.set('current', 18); flushSync();
      resumeFrames();
      expect(target.querySelector('.waterfall-content canvas')).not.toBe(first);
    } finally {
      Object.defineProperty(runtimeHarness.state, 'currentState', original);
    }
  });
  it('keeps unmanaged mounts on their own hardware lease', () => {
    mountPanel(); mountPanel({ scopeRegion: undefined });
    expect(mockRuntime.acquireHardwareScope).toHaveBeenCalledTimes(2);
    expect(harnessChannels.scope?.connect).toHaveBeenCalledTimes(1);
    expect(streamSubscriptions.hardware).toHaveBeenCalledTimes(2);
  });
  it('retires the old sink and lease when unmanaged ownership transfers to a managed gap', async () => {
    const region = new SvelteMap<string, unknown>([['value', undefined]]);
    const target = mountPanel({ get scopeRegion() { return region.get('value'); } });
    emitFrame({ pixels: new Uint8Array([0, 128, 255]) });
    await flushWaterfallPaint();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    const oldSink = capturedStreamSinks[0];
    const stream = scopeControllerHarness.stream('hardware');
    const oldFrame = stream.latest(stream.selectedSlot!)!;
    const oldSlot = stream.selectedSlot!;
    const ownLease = mockRuntime.acquireHardwareScope.mock.results[0].value;
    harnessLeases.push(sharedLeaseAcquisitions());
    stream.reset();
    region.set('value', { passband: { state: 'unknown', reason: 'not-observed' }, demanded: true, setDemand: () => {} });
    flushSync();
    expect(streamUnsubscriptions.hardware).toHaveBeenCalledTimes(1);
    expect(mockRuntime.releaseHardwareScope).toHaveBeenCalledExactlyOnceWith(ownLease);
    cleared(target);
    const renderCount = spectrumRendererHarness.render.mock.calls.length;
    oldSink(oldFrame, oldSlot); flushSync(); await flushWaterfallPaint();
    cleared(target);
    expect(spectrumRendererHarness.render).toHaveBeenCalledTimes(renderCount);
    // MOR-4271 review: the stream-effect cleanup CANCELS the panorama
    // scheduler (dispose would kill it permanently) — after this
    // unmanaged→managed transition the next CENTER retarget must still
    // receive animation ticks and reach its target. Re-arm the managed
    // region, deliver a frame whose center moved, then retarget the managed
    // tuple's center: the visual center glides to the new confirmed center.
    region.set('value', {
      passband: Object.freeze({ state: 'current', tuple: Object.freeze({
        frequencyHz: 14_050_000, mode: 'USB', widthHz: 2_400, shiftHz: 0, frameMode: 0,
        startHz: 14_000_000, endHz: 14_100_000 }) }),
      demanded: true, setDemand: () => {},
    });
    emitFrame({ startFreq: 14_010_000, endFreq: 14_110_000 });
    flushSync();
    region.set('value', {
      passband: Object.freeze({ state: 'current', tuple: Object.freeze({
        frequencyHz: 14_090_000, mode: 'USB', widthHz: 2_400, shiftHz: 0, frameMode: 0,
        startHz: 14_010_000, endHz: 14_110_000 }) }),
      demanded: true, setDemand: () => {},
    });
    flushSync();
    const shifts = () => spectrumRendererHarness.render.mock.calls.map((call) => call[4]?.panoramaShiftHz ?? 0);
    await vi.waitFor(() => expect(shifts().at(-1)).toBe(14_090_000 - 14_060_000));
  });
  it.each(['null', 'off'] as const)('clears pixels, coordinates and active captures on %s while shared health stays true', async (boundary) => {
    const { target, props, onScopeDemandChange } = managed(projection());
    const shared = harnessLeases.at(-1)!;
    expect(sharedLeaseAcquisitions).toHaveBeenCalledTimes(1);
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    emitFrame({ pixels: new Uint8Array([0, 128, 255]) });
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect([...spectrumRendererHarness.render.mock.calls.at(-1)![1]]).toEqual([0, 128, 255]);
    const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 80, 100); pointer(waterfall, 'pointermove', 80, 130);
    const oldCanvas = target.querySelector('canvas');
    if (boundary === 'null') silenceFrames();
    else target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click();
    flushSync(); cleared(target);
    // MOR-2464: a projection gap keeps the static CENTER reference in both
    // areas; the viewer-OFF boundary keeps hiding it.
    if (boundary === 'off') expectCenterReference(target, false);
    else expectCenterReference(target, true);
    expect(oldCanvas?.isConnected).toBe(false);
    expect(runtimeHarness.state.mockScopeConnected).toBe(true);
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    expect(mockRuntime.releaseHardwareScope).not.toHaveBeenCalled();
    pointer(window, 'pointerup', 80, 130);
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 81, 100); pointer(spectrum, 'pointermove', 81, 130); pointer(spectrum, 'pointerup', 81, 130);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: 1 }));
    if (boundary === 'null') { silenceFrames(); } else { flushSync(); }
    cleared(target);
    if (boundary === 'off') expectCenterReference(target, false);
    else expectCenterReference(target, true);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    if (boundary === 'off') {
      expect(onScopeDemandChange).toHaveBeenCalledWith(false);
      silenceFrames();
      target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click(); flushSync();
      expect(onScopeDemandChange).toHaveBeenLastCalledWith(true); cleared(target);
      // Demand back ON while frames are still silent: reference returns.
      expectCenterReference(target, true);
    }
    props.set('projection', projection()); resumeFrames();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    mockRuntime.releaseHardwareScope(shared);
  });
  it.each(['unknown', 'unsupported'] as const)('keeps the managed sample plane without raw RF fallback for %s passband', (state) => {
    const { target } = managed(projection(state));
    expect(target.querySelector('canvas')).not.toBeNull();
    expect(target.querySelector('.freq-axis')).not.toBeNull();
    // MOR-2464: the static CENTER carrier reference is not passband
    // geometry — in BOTH areas it shows from frequency evidence while the
    // tuple lags; the filter shade itself stays absent (its geometry is
    // intentionally unavailable until the tuple recovers).
    expectCenterReference(target, true);
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
  });
  it('keeps the static CENTER reference through a focus-like projection gap and restores passband on recovery', () => {
    const { target, props } = managed(projection());
    expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
    expect(target.querySelector('.spectrum-area .tune-line')).toBeNull();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    // Focus pause: frames go silent; only the static reference survives in
    // both areas — no passband geometry is invented from it.
    silenceFrames();
    expectCenterReference(target, true);
    expect(target.querySelector('.passband-overlay')).toBeNull();
    props.set('projection', projection()); resumeFrames();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
  });
  it('does not infer CENTER proof from a v2 span while the current scope mode is unknown', () => {
    const controls = testScopeControls(0);
    const unknownMode: ScopeControlsViewModel = {
      ...controls, mode: { ...controls.mode, reading: { status: 'unknown' } },
    };
    runtimeHarness.state.currentState = Object.freeze({ providerGeneration: 18, active: 'MAIN' });
    authorityHarness.state.current = authority({ providerGeneration: 18, scopeControls: unknownMode });
    refreshSpectrumAuthority();
    const target = mountPanel();
    expectCenterReference(target, false);
    harnessChannels.scope.fire(new Uint8Array(scopeWire({ sourceId: 'radio',
      viewStartHz: 14_000_000, viewEndHz: 14_100_000 })));
    flushSync();
    silenceFrames();
    expectCenterReference(target, false);
    authorityHarness.state.current = authority({ providerGeneration: 18, scopeControls: controls });
    refreshSpectrumAuthority(); flushSync();
    expectCenterReference(target, true);
  });
  it('pins no false CENTER marker when a FIX projection drops to null', () => {
    authorityHarness.state.current = authority({
      scopeControls: testScopeControls(1),
    });
    refreshSpectrumAuthority();
    const { target, props } = managed(projection('current', 1));
    // FIX with live frame+tuple: proportional waterfall line, canvas
    // marker, no spectrum-area DOM stand-in.
    expect(target.querySelector<HTMLElement>('.waterfall-content .tune-line')).not.toBeNull();
    expect(target.querySelector('.spectrum-area .tune-line')).toBeNull();
    // The post-clear frameScopeMode default (0) is NOT proof of CENTER —
    // the authority's observed FIX scope-control mode must keep the
    // reference hidden in both areas.
    silenceFrames();
    expectCenterReference(target, false);
  });
  it('keeps the static CENTER reference through a strict authority freshness gap in the same session', () => {
    const { target, props } = managed(projection());
    expect(target.querySelector('.spectrum-area .tune-line')).toBeNull();
    expect(target.querySelector('.waterfall-content .tune-line')).not.toBeNull();
    // Focus gap: frames go silent AND the strict authority nulls
    // while provider/session/receiver identity is unchanged. The static
    // ruler must not require a strictly fresh frequency to stay drawn.
    silenceFrames();
    authorityHarness.state.current = null; refreshSpectrumAuthority(); flushSync();
    expectCenterReference(target, true);
    // Refocus: frames and authority return; the normal path resumes.
    authorityHarness.state.current = authority(); refreshSpectrumAuthority(); flushSync();
    props.set('projection', projection()); resumeFrames();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
  });
  it('clears the remembered mode on a provider identity change', () => {
    const { target, props } = managed(projection());
    silenceFrames();
    authorityHarness.state.current = null;
    runtimeHarness.state.currentState = Object.freeze({ source: 'test-state', providerGeneration: 18, active: 'MAIN' });
    refreshSpectrumAuthority(); flushSync();
    expectCenterReference(target, false);
    // The memory is invalidated by the MISMATCH, not consumed by the gap:
    // identity back — the reference returns.
    runtimeHarness.state.currentState = Object.freeze({ source: 'test-state', providerGeneration: 17, active: 'MAIN' });
    refreshSpectrumAuthority(); flushSync();
    expectCenterReference(target, true);
  });
  it('keeps a proven FIX mode hidden through the same freshness gap', () => {
    authorityHarness.state.current = authority({
      scopeControls: testScopeControls(1),
    });
    refreshSpectrumAuthority();
    const { target, props } = managed(projection('current', 1));
    expect(target.querySelector<HTMLElement>('.waterfall-content .tune-line')).not.toBeNull();
    silenceFrames();
    authorityHarness.state.current = null; refreshSpectrumAuthority(); flushSync();
    expectCenterReference(target, false);
  });
  it('retains whole stale geometry with no cue while strict fresh frequency still pans', () => {
    const { target, props } = managed(projection()); const original = target.querySelector('.passband-overlay')!.getAttribute('style');
    authorityHarness.state.current = authority({ filterWidthHz: null, ifShiftHz: null }); refreshSpectrumAuthority();
    props.set('projection', projection('stale')); flushSync();
    expect(target.querySelector('.passband-overlay')!.getAttribute('style')).toBe(original);
    // MOR-2425/R41: the geometry is retained with NO cue at all.
    expect(target.querySelector('.passband-freshness')).toBeNull();
    expect(target.textContent).not.toContain('◷');
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 82, 100); pointer(spectrum, 'pointermove', 82, 120); pointer(spectrum, 'pointerup', 82, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
    authorityHarness.state.current = authority({ frequencyHz: null }); refreshSpectrumAuthority(); flushSync();
    pointer(spectrum, 'pointerdown', 83, 100); pointer(spectrum, 'pointermove', 83, 120); pointer(spectrum, 'pointerup', 83, 120);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    props.set('projection', projection()); flushSync();
    expect(target.querySelector('.passband-freshness')).toBeNull();
  });
  it('does not grant stale resize even if a separate strict snapshot is current', () => {
    const { target, props } = managed(projection()); const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 84, 100); pointer(waterfall, 'pointermove', 84, 130);
    props.set('projection', projection('stale')); flushSync();
    pointer(window, 'pointerup', 84, 130);
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });
  it('anchors translated stale CENTER geometry at 50% and pans the panorama under it (MOR-2464)', async () => {
    const { target, props } = managed(projection()); const { waterfall } = prepareGeometry(target);
    pointer(waterfall.querySelector('.passband-resize-zone')!, 'pointerdown', 89, 100);
    pointer(waterfall, 'pointermove', 89, 130);
    const initial = projection('stale');
    if (initial.state !== 'stale') throw new Error('stale fixture required');
    const translated = { ...initial, translated: true as const,
      tuple: { ...initial.tuple, frequencyHz: 14_051_000 } };
    props.set('projection', translated); flushSync();
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.left).toBe('38%');
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.width).toBe('24%');
    await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions).toMatchObject({
      tuneHz: 14_051_000, scopeMode: 0, passbandHz: 2400,
    }));
    await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions.panoramaShiftHz).toBe(1_000));
    pointer(window, 'pointerup', 89, 130);
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    emitFrame({ startFreq: 14_001_000, endFreq: 14_101_000, sequence: 2, pixels: new Uint8Array([0, 128, 255]) });
    props.set('projection', { ...translated,
      tuple: { ...translated.tuple, startHz: 14_001_000, endHz: 14_101_000 } });
    flushSync();
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    // The recentered frame must not reset the animation: the offset settles
    // back to zero on its own, never shifting stationary content twice.
    await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions.panoramaShiftHz).toBe(0));
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
  });
  it('keeps projector-produced overlays through a stale frequency gap and staggered recovery while tuning', () => {
    authorityHarness.state.useProductionSelector = true;
    // MOR-4213: the test edits `draft`; every present() delivers it as a NEW
    // snapshot (a JSON round trip, as a `full` envelope arrives), so no snapshot
    // the projector or the view model already saw is ever edited in place.
    const draft = structuredClone(IC7300_STATE); const caps = structuredClone(IC7300_STEPPED_CAPABILITIES);
    const rx = draft.main!;
    Object.assign(rx, { freqHz: 14_074_000, mode: 'USB', filter: 1, activeSlot: 'A',
      filterWidth: 2400, pbtInner: 128, pbtOuter: 128, dataMode: 0,
      vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 1, dataMode: 0 } });
    const paths = ['main', ...Object.keys(rx).map((leaf) => `main.${leaf}`),
      ...Object.keys(rx.vfoA!).map((leaf) => `main.vfoA.${leaf}`)];
    draft.fieldStatus = Object.fromEntries(paths.map((path) => [path, { storePath: path,
      observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 10 }]));
    function deliver(): ServerState {
      const snapshot = JSON.parse(JSON.stringify(draft)) as ServerState;
      runtimeHarness.state.currentState = snapshot;
      return snapshot;
    }
    let state = deliver(); runtimeHarness.state.currentCaps = caps;
    const input: ScopePassbandDisplayInput = { state, caps, selection: { receiver: 'MAIN', slot: 'A' },
      session: { state: 'connected', epoch: 1 }, frame: null };
    let result = EMPTY_SCOPE_PASSBAND_DISPLAY; let sequence = 0;
    const { target, props } = managed(null);
    const hardwareStream = scopeControllerHarness.stream('hardware');
    let streamGeometry: import('$lib/runtime/scope-frame').ScopeGeometry | null = null;
    hardwareStream.geometry.subscribe((geometry) => { streamGeometry = geometry; });
    function present(_now: number, center: number): void {
      emitFrame({ startFreq: center - 50_000, endFreq: center + 50_000,
        pixels: new Uint8Array([10, 100, 200]), sequence: ++sequence });
      state = deliver(); input.state = state;
      input.frame = { source: 'hardware', receiver: 0, providerGeneration: 1,
        demanded: true, transport: 'connected', transportEpoch: hardwareStream.sessionEpoch, geometry: streamGeometry, receipt: hardwareStream.receipt };
      result = projectScopePassbandDisplay(result, input);
      props.set('projection', result.display); flushSync();
    }
    present(0, 14_074_000); expect(result.display.state).toBe('current');
    rx.vfoA!.freqHz = 14_075_000; draft.fieldStatus['main.vfoA.freqHz'].lastObservedMonotonic = 11;
    present(100, 14_074_000);
    expect(result.display.state).toBe('stale');
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    rx.freqHz = 14_075_000; draft.fieldStatus['main.freqHz'].lastObservedMonotonic = 11.1;
    for (let now = 200; now <= 5000; now += 100) {
      present(now, 14_075_000);
      expect(result.display.state).toBe('stale');
      expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
      expect(target.querySelectorAll('.passband-overlay')).toHaveLength(1);
      expect(target.querySelector('.passband-resize-zone')).toBeNull();
    }
    for (const [index, leaf] of ['filterWidth', 'pbtInner', 'pbtOuter'].entries()) {
      draft.fieldStatus[`main.${leaf}`].lastObservedMonotonic = 15.1 + index / 10;
      present(5100 + index * 100, 14_075_000);
      expect(result.display.state).toBe(index === 2 ? 'current' : 'stale');
      expect(target.querySelector('.passband-overlay')).not.toBeNull();
    }

    for (const path of ['main.freqHz', 'main.vfoA.freqHz', 'main.mode', 'main.vfoA.mode']) {
      Object.assign(draft.fieldStatus[path], { freshness: 'stale', availability: 'stale' });
    }
    for (let step = 0; step < 10; step += 1) {
      const frequency = step % 2 === 0 ? 14_076_000 : 14_075_000;
      rx.freqHz = frequency; rx.vfoA!.freqHz = frequency;
      draft.fieldStatus['main.freqHz'].lastObservedMonotonic = 20 + step;
      draft.fieldStatus['main.vfoA.freqHz'].lastObservedMonotonic = 20 + step;
      if (step === 3) draft.fieldStatus['main.filterWidth'].lastObservedMonotonic = 24;
      if (step === 7) draft.fieldStatus['main.pbtInner'].lastObservedMonotonic = 28;
      present(6000 + step * 250, frequency);
      expect(result.display).toMatchObject({ state: 'stale', translated: true,
        tuple: { frequencyHz: 14_075_000 } });
      expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
      expect(target.querySelectorAll('.passband-overlay')).toHaveLength(1);
      expect(target.querySelector('.passband-resize-zone')).toBeNull();
    }

    for (const path of ['main.freqHz', 'main.vfoA.freqHz', 'main.mode', 'main.vfoA.mode']) {
      Object.assign(draft.fieldStatus[path], { freshness: 'fresh', availability: 'available',
        lastObservedMonotonic: 40 });
    }
    for (let step = 0; step < 8; step += 1) {
      const frequency = step % 2 === 0 ? 14_076_000 : 14_075_000;
      rx.freqHz = frequency; rx.vfoA!.freqHz = frequency;
      draft.fieldStatus['main.freqHz'].lastObservedMonotonic = 41 + step;
      draft.fieldStatus['main.vfoA.freqHz'].lastObservedMonotonic = 41 + step;
      if (step === 7) draft.fieldStatus['main.pbtOuter'].lastObservedMonotonic = 48;
      present(9000 + step * 250, frequency);
      expect(result.display.state).toBe(step === 7 ? 'current' : 'stale');
      expect(target.querySelectorAll('.tune-line')).toHaveLength(1);
      expect(target.querySelectorAll('.passband-overlay')).toHaveLength(1);
      if (step < 7) expect(target.querySelector('.passband-resize-zone')).toBeNull();
    }
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });
  it('recovers selected-slot geometry through canonical alias hydration without a second width reading', () => {
    authorityHarness.state.useProductionSelector = true;
    // MOR-4213: the test edits `draft`; every present() delivers it as a NEW
    // snapshot (a JSON round trip, as a `full` envelope arrives), so no snapshot
    // the projector or the view model already saw is ever edited in place.
    const draft = structuredClone(IC7300_STATE); const caps = structuredClone(IC7300_STEPPED_CAPABILITIES);
    const rx = draft.main!;
    Object.assign(rx, { freqHz: 14_074_000, mode: 'USB', filter: 1, activeSlot: 'A',
      filterWidth: 2400, pbtInner: 128, pbtOuter: 128, dataMode: 0,
      vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 1, dataMode: 0 },
      vfoB: { freqHz: 14_090_000, mode: 'LSB', filterNum: 2, dataMode: 1 } });
    const paths = ['main', ...Object.keys(rx).map((leaf) => `main.${leaf}`),
      ...['vfoA', 'vfoB'].flatMap(slot => Object.keys(rx.vfoA!).map(leaf => `main.${slot}.${leaf}`))];
    draft.fieldStatus = Object.fromEntries(paths.map(path => [path, { storePath: path,
      observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 10 }]));
    function deliver(): ServerState {
      const snapshot = JSON.parse(JSON.stringify(draft)) as ServerState;
      runtimeHarness.state.currentState = snapshot;
      return snapshot;
    }
    let state = deliver(); runtimeHarness.state.currentCaps = caps;
    const input: ScopePassbandDisplayInput = { state, caps, selection: null,
      session: { state: 'connected', epoch: 1 }, frame: null };
    const { target, props } = managed(null); let result = EMPTY_SCOPE_PASSBAND_DISPLAY; let sequence = 0;
    const hardwareStream = scopeControllerHarness.stream('hardware');
    let streamGeometry: import('$lib/runtime/scope-frame').ScopeGeometry | null = null;
    hardwareStream.geometry.subscribe((geometry) => { streamGeometry = geometry; });
    function present(center: number): void {
      state = deliver(); input.state = state;
      const active = toRadioViewModel(state, caps)!.vfos.filter(vfo => vfo.isActive);
      const vfo = active.length === 1 ? active[0] : null;
      input.selection = vfo?.slot.kind === 'slotted' ? { receiver: vfo.receiver, slot: vfo.slot.id } : null;
      emitFrame({ startFreq: center - 50_000, endFreq: center + 50_000,
        pixels: new Uint8Array([10, 100, 200]), sequence: ++sequence });
      input.frame = { source: 'hardware', receiver: 0, providerGeneration: 1,
        demanded: true, transport: 'connected', transportEpoch: hardwareStream.sessionEpoch, geometry: streamGeometry, receipt: hardwareStream.receipt };
      result = projectScopePassbandDisplay(result, input);
      props.set('projection', result.display); flushSync();
    }
    present(14_074_000); expect(result.display.state).toBe('current');
    rx.activeSlot = 'B'; draft.fieldStatus['main.activeSlot'].lastObservedMonotonic = 20;
    for (const leaf of Object.keys(rx.vfoB!)) draft.fieldStatus[`main.vfoB.${leaf}`].observed = false;
    rx.filterWidth = 1800; draft.fieldStatus['main.filterWidth'].lastObservedMonotonic = 20.1;
    present(14_074_000);
    expect(input.selection).toEqual({ receiver: 'MAIN', slot: 'B' });
    expect(result.display.state).toBe('unknown');
    expect(target.querySelector('.passband-overlay')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    Object.assign(rx, { freqHz: 14_090_000, mode: 'LSB', filter: 2, dataMode: 1 });
    for (const path of [...Object.keys(rx.vfoB!).map(leaf => `main.vfoB.${leaf}`),
      'main.freqHz', 'main.mode', 'main.filter', 'main.dataMode']) {
      Object.assign(draft.fieldStatus[path], { observed: true, lastObservedMonotonic: 20.2 });
    }
    present(14_090_000); expect(result.display.state).toBe('unknown');
    for (const [index, leaf] of ['pbtInner', 'pbtOuter'].entries()) {
      draft.fieldStatus[`main.${leaf}`].lastObservedMonotonic = 20.3 + index / 10;
      present(14_090_000);
      if (index === 0) expect(target.querySelector('.passband-overlay')).toBeNull();
    }
    expect(result.display).toMatchObject({ state: 'current', tuple: { frequencyHz: 14_090_000, mode: 'LSB', widthHz: 1800 } });
    expect(target.querySelectorAll('.passband-overlay')).toHaveLength(1);
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    Object.assign(draft.fieldStatus['main.activeSlot'], {
      lastObservedMonotonic: 30, source: { source: 'command_response' },
    });
    present(14_090_000);
    expect(result.display.state).toBe('unknown');
    expect(target.querySelector('.passband-overlay')).toBeNull();
    for (const [index, leaf] of ['filterWidth', 'pbtInner', 'pbtOuter'].entries()) {
      draft.fieldStatus[`main.${leaf}`].lastObservedMonotonic = 30.1 + index / 10;
      present(14_090_000);
    }
    expect(result.display).toMatchObject({ state: 'current', tuple: { frequencyHz: 14_090_000, widthHz: 1800 } });
    expect(target.querySelectorAll('.passband-overlay')).toHaveLength(1);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });
  it('pushes normalized managed frames through existing waterfall registrations on mount and recovery', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    const pixels = new Uint8Array([0, 128, 255]);
    const sequenced = (sequence: number) => emitFrame({ pixels, sequence });
    try {
      const { target, props } = managed(projection());
      emitFrame({ pixels, sequence: 1 });
      await flushWaterfallPaint();
      expect([...push.mock.calls.at(-1)![0]]).toEqual([0, 128, 255]);
      expect(push).toHaveBeenCalledTimes(1);
      const firstCanvas = target.querySelector('.waterfall-content canvas');
      // Ordinary live-spaced deliveries with distinct sequences establish
      // a paint history before the recovery frame below.
      for (let sequence = 2; sequence <= 6; sequence += 1) {
        clock += 83;
        sequenced(sequence);
        await flushWaterfallPaint();
      }
      expect(push).toHaveBeenCalledTimes(6);
      const afterLoop = 6;
      silenceFrames();
      expect(target.querySelector<HTMLElement>('.waterfall-history')?.style.visibility).toBe('hidden');
      emitFrame({ pixels }); await flushWaterfallPaint(); expect(push).toHaveBeenCalledTimes(afterLoop);
      clock += 83;
      const beforeResume = push.mock.calls.length;
      resumeFramesWith(pixels);
      await flushWaterfallPaint();
      expect(target.querySelector('.waterfall-content canvas')).toBe(firstCanvas);
      expect(target.querySelector<HTMLElement>('.waterfall-history')?.style.visibility).toBe('visible');
      expect([...push.mock.calls.at(-1)![0]]).toEqual([0, 128, 255]);
      expect(push).toHaveBeenCalledTimes(beforeResume + 1);
    } finally {
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });
  it('preserves raw waterfall push delivery in legacy mode', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    try {
      mountPanel(); const frame = emitFrame();
      await flushWaterfallPaint();
      expect(push).toHaveBeenCalledWith(frame.pixels);
    } finally { push.mockRestore(); }
  });
  it('consolidates backlog handling into the rAF pump: a burst before one animation frame paints only the latest delivery (MOR-4206)', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let clock = 0;
    const nowSpy = vi.spyOn(performance, 'now').mockImplementation(() => clock);
    const generations = new SvelteMap([['current', 17]]);
    const original = Object.getOwnPropertyDescriptor(runtimeHarness.state, 'currentState')!;
    Object.defineProperty(runtimeHarness.state, 'currentState', {
      configurable: true, get: () => ({ source: 'test-state', providerGeneration: generations.get('current'), active: 'MAIN' }),
    });
    try {
      mountPanel();
      let id = 0;
      // One delivery per id, NO paint flush in between: every delivery that
      // reaches the registration must replace the pump's pending frame, so
      // the single animation frame that follows paints only the final one.
      const deliver = (): void => {
        clock += 1;
        emitFrame({ pixels: new Uint8Array(475).fill(++id) });
      };
      const painted = () => push.mock.calls.map(([pixels]) => pixels[0]);
      // Fresh registration: the burst is not suppressed at the
      // registration — the pump coalesces it and paints the LATEST frame,
      // not the first (the MOR-3471 spacing gate painted the first).
      for (let index = 0; index < 6; index += 1) deliver();
      await flushWaterfallPaint();
      expect(painted()).toEqual([6]);
      // A live-spaced delivery gets its own animation frame and paints.
      clock += 83; deliver(); await flushWaterfallPaint();
      expect(painted()).toEqual([6, 7]);
      // A post-gap (>3 s) burst: same consolidation — one rAF paints only
      // the final delivered frame; nothing is suppressed at the boundary.
      clock += 2 * 60 * 60 * 1000;
      for (let index = 0; index < 8; index += 1) deliver();
      await flushWaterfallPaint();
      expect(painted()).toEqual([6, 7, 15]);
      // A burst straddling a provider-switch remount hits the remounted
      // canvas's fresh registration, which is likewise unfiltered: one
      // animation frame paints only its latest delivery.
      clock += 2 * 60 * 60 * 1000;
      deliver(); await flushWaterfallPaint();
      generations.set('current', 18); flushSync();
      for (let index = 0; index < 4; index += 1) deliver();
      await flushWaterfallPaint();
      expect(painted()).toEqual([6, 7, 15, 16, 20]);
      // Live-spaced streaming after the burst paints every frame.
      for (let index = 0; index < 3; index += 1) {
        clock += 83; deliver(); await flushWaterfallPaint();
      }
      expect(painted()).toEqual([6, 7, 15, 16, 20, 21, 22, 23]);
    } finally {
      Object.defineProperty(runtimeHarness.state, 'currentState', original);
      nowSpy.mockRestore();
      push.mockRestore();
    }
  });
  it.each(['tap', 'pan'] as const)('cancels an in-flight %s and DX coordinates when managed frames go silent', (gesture) => {
    const { target } = managed(projection()); const { spectrum, waterfall } = prepareGeometry(target);
    const surface = gesture === 'tap' ? waterfall.querySelector('canvas')! : spectrum;
    const spot = { spotter: 'N0CALL', freq: 14_075_410, call: 'K1ABC', comment: 'test', time_utc: '1200', timestamp: 1 };
    runtimeHarness.state.capturedDxMessage?.({ type: 'dx_spot', spot }); flushSync();
    expect(target.querySelector('.dx-badge')).not.toBeNull();
    pointer(surface, 'pointerdown', 86, 100);
    if (gesture === 'pan') pointer(surface, 'pointermove', 86, 120);
    silenceFrames();
    pointer(surface, 'pointerup', 86, 120);
    runtimeHarness.state.capturedDxMessage?.({ type: 'dx_spot', spot }); flushSync();
    expect(target.querySelector('.dx-badge')).toBeNull(); cleared(target);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(gesture === 'pan' ? 1 : 0);
    if (gesture === 'pan') expect(handlerHarness.vfo.onFreqChange.mock.calls[0][2]).toBe('drag');
  });
  it('routes current managed waterfall tap and wheel through canonical handlers', () => {
    const { target } = managed(projection()); const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 87, 100); pointer(canvas, 'pointerup', 87, 100);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
    target.querySelector('.spectrum-panel')!.dispatchEvent(new WheelEvent('wheel', { bubbles: true, deltaY: 1 }));
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenLastCalledWith(14_049_000, 0, 'step');
  });
  it('obeys an external demanded=false even if the supplied projection is still live', () => {
    const { target, props } = managed(projection());
    props.set('demanded', false); flushSync(); cleared(target);
    // Viewer OFF keeps hiding the reference (MOR-2464 ruling).
    expect(target.querySelector('.tune-line')).toBeNull();
    props.set('projection', projection()); flushSync(); cleared(target);
    expect(target.querySelector('.tune-line')).toBeNull();
    expect(mockRuntime.acquireHardwareScope).not.toHaveBeenCalled();
    props.set('demanded', true); flushSync();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
  });
  it.each(['failed', 'cancelled'] as const)('keeps pending resize temporary and returns to the qualified tuple after %s', (phase) => {
    const input = projection(); const { target } = managed(input); const { waterfall } = prepareGeometry(target);
    const zone = waterfall.querySelector('.passband-resize-zone')!;
    pointer(zone, 'pointerdown', 85, 100); pointer(waterfall, 'pointermove', 85, 130); pointer(waterfall, 'pointerup', 85, 130);
    expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledWith(2_700, 0, 17);
    setFilterWidthLifecycle(pendingFilterWidthLifecycle(2_700)); flushSync();
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.width).toBe('27%');
    setFilterWidthLifecycle({ outcome: { phase } }); flushSync();
    expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.width).toBe('24%');
    expect(input).toEqual(projection());
  });
});

describe('SpectrumPanel colour roles', () => {
  const distinct = {
    trace: '#010101',
    traceFill: { top: '#020202', bottom: '#030303' },
    grid: '#040404',
    axisText: '#050505',
    tuneLine: '#060606',
    passbandFill: '#070707',
    passbandEdge: '#080808',
  };

  it('sends every role to the renderer options', async () => {
    mountPanel({ colorRoles: distinct });
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject({
      lineColor: '#010101', fillColor: '#020202', fillColorBottom: '#030303',
      gridColor: '#040404', textColor: '#050505', tuneLineColor: '#060606',
      passbandFillColor: '#070707', passbandEdgeColor: '#080808',
    });
  });

  it('publishes the DOM-overlay roles as custom properties the overlay styles read', () => {
    const target = mountPanel({ colorRoles: distinct });
    emitFrame();
    const panel = target.querySelector<HTMLElement>('.spectrum-panel')!;
    expect(panel.style.getPropertyValue('--scope-tune-line')).toBe('#060606');
    expect(panel.style.getPropertyValue('--scope-passband-fill')).toBe('#070707');
    expect(panel.style.getPropertyValue('--scope-passband-edge')).toBe('#080808');
    expect(target.querySelector('.tune-line')).not.toBeNull();
    expect(target.querySelector('.passband-overlay')).not.toBeNull();
    expect(spectrumPanelSource).toMatch(
      /\.tune-line\s*\{[^}]*background:\s*var\(--scope-tune-line,\s*rgba\(239, 68, 68, 0\.75\)\)/,
    );
    expect(spectrumPanelSource).toMatch(
      /\.passband-overlay\s*\{[^}]*background:\s*var\(--scope-passband-fill,\s*rgba\(59, 130, 246, 0\.15\)\)/,
    );
    expect(spectrumPanelSource).toMatch(
      /\.passband-overlay\s*\{[^}]*border-left:\s*1px dashed var\(--scope-passband-edge,\s*rgba\(59, 130, 246, 0\.4\)\)/,
    );
    expect(spectrumPanelSource).toMatch(
      /\.passband-overlay\s*\{[^}]*border-right:\s*1px dashed var\(--scope-passband-edge,\s*rgba\(59, 130, 246, 0\.4\)\)/,
    );
  });

  it('leaves an omitted prop on the renderer defaults', async () => {
    mountPanel();
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject(
      spectrumColorRolesToOptions(defaultSpectrumColorRoles),
    );
  });

  it('keeps both paths on the default when a role is passed as undefined', async () => {
    const target = mountPanel({
      colorRoles: { tuneLine: undefined, traceFill: { top: undefined, bottom: undefined } },
    });
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject({
      tuneLineColor: defaultSpectrumColorRoles.tuneLine,
      fillColor: defaultSpectrumColorRoles.traceFill.top,
      fillColorBottom: defaultSpectrumColorRoles.traceFill.bottom,
    });
    const panel = target.querySelector<HTMLElement>('.spectrum-panel')!;
    expect(panel.style.getPropertyValue('--scope-tune-line')).toBe(defaultSpectrumColorRoles.tuneLine);
  });

  it('takes the unlisted roles from the defaults when only one is overridden', async () => {
    mountPanel({ colorRoles: { tuneLine: '#0a0a0a' } });
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions).toMatchObject({
      ...spectrumColorRolesToOptions(defaultSpectrumColorRoles),
      tuneLineColor: '#0a0a0a',
    });
  });

  function publishedRoles(target: HTMLElement) {
    const panel = target.querySelector<HTMLElement>('.spectrum-panel')!;
    return JSON.parse(panel.dataset.scopeColorRoles!);
  }

  it('publishes on the root the same seven resolved roles it sends to the renderer', async () => {
    const target = mountPanel({ colorRoles: distinct });
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    const published = publishedRoles(target);
    expect(published).toEqual(distinct);
    expect(spectrumRendererHarness.lastOptions).toMatchObject(spectrumColorRolesToOptions(published));
  });

  it('publishes the defaults on the root when no roles are passed', () => {
    expect(publishedRoles(mountPanel())).toEqual(defaultSpectrumColorRoles);
  });

  it('publishes the default on the root for an explicit undefined override', () => {
    const target = mountPanel({
      colorRoles: { tuneLine: undefined, traceFill: { top: undefined, bottom: undefined } },
    });
    expect(publishedRoles(target)).toEqual(defaultSpectrumColorRoles);
  });

  it('resolves the roles once per mount for both the root attribute and the renderer options', async () => {
    spectrumRendererHarness.resolveColorRolesCalls = 0;
    const target = mountPanel({ colorRoles: distinct });
    emitFrame();
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(publishedRoles(target)).toEqual(distinct);
    expect(spectrumRendererHarness.resolveColorRolesCalls).toBe(1);
  });
});

describe('hardware scope drop clears the trace (R53)', () => {
  it('stops painting on drop and repaints only from a post-reconnect frame', async () => {
    const target = mountPanel();
    const spectrum = target.querySelector('.spectrum-area')!;
    emitFrame({ pixels: new Uint8Array(475).fill(64) });
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.render.mock.calls.at(-1)![1][0]).toBe(64);
    const renderersBeforeDrop = spectrumRendererHarness.constructed;

    runtimeHarness.state.mockScopeConnected = false;
    flushSync();

    // No paint surface while the scope is down: the pre-drop frame cannot be
    // repainted, and the peak-hold/average state held by the SpectrumRenderer
    // instance is discarded with it.
    expect(spectrum.querySelector('canvas')).toBeNull();
    spectrumRendererHarness.render.mockClear();
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(spectrumRendererHarness.render).not.toHaveBeenCalled();

    runtimeHarness.state.mockScopeConnected = true;
    flushSync();
    expect(spectrum.querySelector('canvas')).not.toBeNull();
    expect(spectrumRendererHarness.constructed).toBe(renderersBeforeDrop + 1);

    // Reconnecting alone paints nothing — the panel kept no pixels to repaint.
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(spectrumRendererHarness.render).not.toHaveBeenCalled();

    emitFrame({ pixels: new Uint8Array(475).fill(100) });
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.render.mock.calls.at(-1)![1][0]).toBe(100);
  });
});

describe('silent raw scope refuses click-to-tune', () => {
  it('clears the stale span on frame silence and tunes again after a fresh frame', () => {
    const target = mountPanel();
    emitFrame({ mode: 1, startFreq: 14_000_000, endFreq: 14_100_000 });
    prepareGeometry(target);

    // One in-span DX spot so the post-silence badge assertion can fail:
    // DxOverlay renders only spots inside the frame span.
    runtimeHarness.state.capturedDxMessage?.({
      type: 'dx_spot',
      spot: {
        spotter: 'N0CALL', freq: 14_075_410, call: 'K1ABC', comment: 'test',
        time_utc: '1200', timestamp: 1,
      },
    });
    flushSync();
    expect(target.querySelector('.dx-badge')).not.toBeNull();

    // Frames stop while the transport stays connected.
    runtimeHarness.state.mockScopeFrameLive = false;
    flushSync();

    authorityHarness.state.current = authority({ frequencyHz: 7_050_000 });
    refreshSpectrumAuthority();
    flushSync();

    const { spectrum, waterfall } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 8, 100);
    pointer(spectrum, 'pointermove', 8, 104);
    pointer(spectrum, 'pointerup', 8, 104);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 4, 150);
    pointer(canvas, 'pointerup', 4, 150);
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(mockRuntime.send).not.toHaveBeenCalled();
    expect(target.querySelector('.dx-badge')).toBeNull();

    runtimeHarness.state.mockScopeFrameLive = true;
    flushSync();
    emitFrame({ mode: 1, startFreq: 7_000_000, endFreq: 7_100_000 });
    // A second waterfall tap at the same point would fall inside the
    // recognizer's double-tap window, so the recovery leg clicks the
    // spectrum release path instead — the same x the neighbouring
    // below-threshold release test uses (104/200 of the 100 kHz span).
    const { spectrum: revivedSpectrum } = prepareGeometry(target);
    pointer(revivedSpectrum, 'pointerdown', 9, 100);
    pointer(revivedSpectrum, 'pointermove', 9, 104);
    pointer(revivedSpectrum, 'pointerup', 9, 104);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(7_052_000, 0);
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });
});

// MOR-4256 — drag-to-tune live preview. The snapped drag candidate is a
// reactive, display-only preview published at most once per animation
// frame: in CENTER scope it leads the panorama target (spectrum, waterfall,
// band plan and DX overlay retarget under the pointer, redrawing the cached
// last frame — no scope frame arrives); in FIX scope the same preview state
// moves the tune marker and the passband instead. The release still emits
// exactly one snapped VFO intent.
describe('drag-to-tune live preview (MOR-4256)', () => {
  // Deterministic panorama: under prefers-reduced-motion the viewport
  // retarget snaps instead of gliding, so the preview shift is exact on the
  // next painted frame.
  function stubReducedMotion(): () => void {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({
      matches: true, addEventListener: () => {}, removeEventListener: () => {},
      addListener: () => {}, removeListener: () => {},
    }) as unknown as typeof window.matchMedia;
    return () => { window.matchMedia = original; };
  }

  // The drag preview commits on a requestAnimationFrame callback (the rAF
  // stub at the top of this file defers it to a macrotask); the canvases
  // then repaint from the new options on their own rAF. Await both turns
  // and re-sync the deriveds in between.
  async function flushPreviewPaint(): Promise<void> {
    await new Promise((resolve) => setTimeout(resolve, 0));
    flushSync();
    await new Promise((resolve) => setTimeout(resolve, 0));
  }

  it('retargets the panorama viewport at the snapped candidate on each preview frame without a new scope frame', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { spectrum } = prepareGeometry(target);
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(0));
      const settledCalls = spectrumRendererHarness.render.mock.calls.length;
      const paintedFrame = spectrumRendererHarness.render.mock.calls.at(-1)![1];

      pointer(spectrum, 'pointerdown', 31, 100);
      pointer(spectrum, 'pointermove', 31, 120);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-10_000));
      // viewportStartFreq = centerHz - spanHz / 2 tracks the candidate.
      expect(spectrumRendererHarness.render).toHaveBeenCalledTimes(settledCalls + 1);
      expect(JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!).view).toEqual({ follow: 'vfo', spanHz: 100_000 });
      expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_040_000);
      expect(spectrumRendererHarness.lastOptions.centerHz
        - spectrumRendererHarness.lastOptions.spanHz / 2).toBe(13_990_000);

      pointer(spectrum, 'pointermove', 31, 140);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-20_000));
      expect(spectrumRendererHarness.render).toHaveBeenCalledTimes(settledCalls + 2);
      expect(JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!).view).toEqual({ follow: 'vfo', spanHz: 100_000 });

      // Every repaint since the press drew the SAME cached frame pixels —
      // the preview never waits for a new scope frame.
      const previewCalls = spectrumRendererHarness.render.mock.calls.slice(settledCalls);
      expect(previewCalls.length).toBeGreaterThan(0);
      for (const call of previewCalls) expect(call[1]).toBe(paintedFrame);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      expect(mockRuntime.send).not.toHaveBeenCalled();
      pointer(spectrum, 'pointerup', 31, 140);
    } finally {
      restore();
    }
  });

  it('submits paced absolute targets during moves and an exact snapped target on pointer-up', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 32, 100);
      pointer(spectrum, 'pointermove', 32, 120);
      await flushPreviewPaint();
      pointer(spectrum, 'pointermove', 32, 130);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-15_000));
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      expect(mockRuntime.send).not.toHaveBeenCalled();
      pointer(spectrum, 'pointerup', 32, 130);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(3);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_035_000, 0);
    } finally {
      restore();
    }
  });

  it.each([14_034_000, 14_060_000])('keeps actual VFO digits on every drag target through coalescing and stale readbacks until fresh %i', async (settledHz) => {
    const restore = stubReducedMotion();
    const adapters = await import('$lib/runtime/adapters/panel-adapters');
    const actualAdapters = await vi.importActual<typeof adapters>('$lib/runtime/adapters/panel-adapters');
    const capabilities = await import('$lib/stores/capabilities.svelte');
    const actualCapabilities = await vi.importActual<typeof capabilities>('$lib/stores/capabilities.svelte');
    const radio = await import('$lib/stores/radio.svelte');
    const commands = await import('$lib/stores/commands.svelte');
    const transport = await import('$lib/transport/ws-client');
    const send = vi.spyOn(transport, 'sendCommand').mockReturnValue(true);
    // The file-wide MOR-4241 dispatch spy stands in for the CTR intent; this
    // test needs the production dispatch so the release reaches the transport.
    const intents = await vi.importActual<typeof import('$lib/runtime/commands/radio-intents')>(
      '$lib/runtime/commands/radio-intents');
    scopeCommandHarness.dispatch.mockImplementation(intents.dispatchRadioIntent);
    const pending = vi.spyOn(adapters, 'getPendingFrequencyHz')
      .mockImplementation(actualAdapters.getPendingFrequencyHz);
    const vfo = vi.spyOn(adapters, 'getVfoHandlers').mockImplementation(actualAdapters.getVfoHandlers);
    const getCapabilities = vi.spyOn(capabilities, 'getCapabilities')
      .mockImplementation(actualCapabilities.getCapabilities);
    vi.useFakeTimers({ toFake: ['Date', 'performance', 'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval'] });
    const advancePreview = async () => {
      await vi.advanceTimersByTimeAsync(1);
      flushSync();
      await vi.advanceTimersByTimeAsync(1);
      flushSync();
    };
    try {
      const state: ServerState = structuredClone(IC7300_STATE);
      state.main!.freqHz = 14_050_000;
      state.main!.activeSlot = 'A';
      state.main!.vfoA = { freqHz: 14_050_000, mode: 'USB', filterNum: 1, dataMode: 0 };
      for (const path of ['main.activeSlot', ...Object.keys(state.main!.vfoA).map((leaf) => `main.vfoA.${leaf}`)]) {
        state.fieldStatus![path] = { ...state.fieldStatus!['main.freqHz'], storePath: path, observed: true, freshness: 'fresh', availability: 'available' };
      }
      expect(capabilities.setCapabilities(IC7300_STEPPED_CAPABILITIES)).toBe(true);
      expect(radio.setRadioState(state)).toBe(true);
      runtimeHarness.state.currentState = radio.getRadioState();
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000,
        providerGeneration: state.providerGeneration, scopeControls: testScopeControls(state.scopeControls!.mode!) });
      const target = mountPanel();
      const readout = document.createElement('div'); document.body.append(readout);
      components.push(mount(VfoSurface, { target: readout, props: {
        get viewModel() { return toRadioViewModel(radio.getRadioState(), IC7300_STEPPED_CAPABILITIES)!; },
        onTuneFrequency: noop,
        get pendingFrequencyHz() {
          const hz = actualAdapters.getTuningBurstFrequencyHz(0) ?? actualAdapters.getPendingFrequencyHz(0);
          return hz === null ? {} : { MAIN: hz };
        },
      } }));
      const digits = () => readout.querySelector('[data-vfo-freq]')!.textContent!.replace(/\D/g, '');
      emitFrame({ mode: state.scopeControls!.mode! });
      const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 42, 100);
      for (const x of [120, 133]) {
        pointer(spectrum, 'pointermove', 42, x);
        await advancePreview();
        expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz)
          .toBe(x === 120 ? -10_000 : -16_000);
        expect(digits()).toContain(String(x === 120 ? 14_040_000 : 14_034_000));
        expect(actualAdapters.getTuningBurstFrequencyHz(0)).toBe(x === 120 ? 14_040_000 : 14_034_000);
        expect(send).toHaveBeenCalledTimes(1);
      }
      const first = commands.getCommandLifecycles()[0];
      commands.acknowledgeCommand(first.id, first.originalEpoch, first.originalEpoch);
      const ownEcho = structuredClone(state);
      ownEcho.observationSeq = (state.observationSeq ?? 0) + 1;
      ownEcho.main!.freqHz = 14_040_000; ownEcho.main!.vfoA!.freqHz = 14_040_000;
      ownEcho.fieldStatus!['main.freqHz'].lastObservedMonotonic!++;
      expect(radio.setRadioState(ownEcho)).toBe(true);
      runtimeHarness.state.currentState = radio.getRadioState();
      authorityHarness.state.current = authority({ frequencyHz: 14_040_000, providerGeneration: state.providerGeneration, scopeControls: testScopeControls(state.scopeControls!.mode!) });
      refreshSpectrumAuthority();
      emitFrame({ mode: state.scopeControls!.mode!, startFreq: 13_990_000, endFreq: 14_090_000 });
      await advancePreview();
      expect(digits()).toContain('14034000');
      pointer(spectrum, 'pointermove', 42, 133); await advancePreview();
      expect(digits()).toContain('14034000');
      expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_034_000);
      pointer(spectrum, 'pointerup', 42, 133);
      expect(send).toHaveBeenLastCalledWith('set_freq',
        { freq: 14_034_000, receiver: 0 }, expect.any(String));
      expect(commands.getCommandLifecycles()).toHaveLength(2);
      const final = commands.getCommandLifecycles().at(-1)!;
      commands.acknowledgeCommand(final.id, final.originalEpoch, final.originalEpoch);
      expect(actualAdapters.getPendingFrequencyHz(0)).toBe(14_034_000);
      await vi.advanceTimersByTimeAsync(201);
      flushSync();
      expect(actualAdapters.getTuningBurstFrequencyHz(0)).toBeNull();
      await advancePreview();
      expect(actualAdapters.getPendingFrequencyHz(0)).toBe(14_034_000);
      expect(spectrumRendererHarness.lastOptions.panoramaShiftHz).toBe(-6_000);
      expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_034_000);
      expect(send).toHaveBeenCalledTimes(2);
      const stale = structuredClone(ownEcho);
      stale.observationSeq!++;
      stale.main!.freqHz = 14_040_000;
      stale.main!.vfoA!.freqHz = 14_040_000;
      stale.fieldStatus!['main.freqHz'] = { ...stale.fieldStatus!['main.freqHz'], lastObservedMonotonic: (ownEcho.fieldStatus!['main.freqHz'].lastObservedMonotonic ?? 0) + 1 };
      expect(radio.setRadioState(stale)).toBe(true);
      runtimeHarness.state.currentState = radio.getRadioState();
      authorityHarness.state.current = authority({ frequencyHz: 14_040_000, providerGeneration: state.providerGeneration, scopeControls: testScopeControls(state.scopeControls!.mode!) });
      refreshSpectrumAuthority(); await advancePreview();
      expect(digits()).toContain('14034000');
      expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_034_000);
      const confirmed = structuredClone(stale);
      confirmed.observationSeq!++;
      confirmed.main!.freqHz = settledHz; confirmed.main!.vfoA!.freqHz = settledHz;
      confirmed.fieldStatus!['main.freqHz'].lastObservedMonotonic!++;
      expect(radio.setRadioState(confirmed)).toBe(true);
      runtimeHarness.state.currentState = radio.getRadioState();
      authorityHarness.state.current = authority({ frequencyHz: settledHz, providerGeneration: state.providerGeneration, scopeControls: testScopeControls(state.scopeControls!.mode!) });
      refreshSpectrumAuthority(); await advancePreview();
      expect(actualAdapters.getPendingFrequencyHz(0)).toBeNull();
      expect(digits()).toContain(String(settledHz));
      expect(radio.setRadioState(stale)).toBe(false);
      await advancePreview(); expect(digits()).toContain(String(settledHz));
    } finally {
      send.mockRestore();
      scopeCommandHarness.dispatch.mockReset();
      pending.mockRestore();
      vfo.mockRestore();
      getCapabilities.mockRestore();
      commands.resetCommandLifecycle();
      radio.resetRadioState();
      capabilities.clearCapabilities();
      restore();
      vi.useRealTimers();
    }
  });

  it.each(['pointercancel', 'lostpointercapture'] as const)(
    'clears the preview on %s and returns the viewport to the confirmed frequency',
    async (ending) => {
      const restore = stubReducedMotion();
      try {
        authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
        const target = mountPanel();
        emitFrame();
        const { spectrum } = prepareGeometry(target);
        pointer(spectrum, 'pointerdown', 33, 100);
        pointer(spectrum, 'pointermove', 33, 120);
        await flushPreviewPaint();
        await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-10_000));
        pointer(spectrum, ending, 33, 120);
        await flushPreviewPaint();
        await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(0));
        expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
        expect(mockRuntime.send).not.toHaveBeenCalled();
      } finally {
        restore();
      }
    },
  );

  it('retargets the waterfall viewport through updateOptions when the drag runs on the waterfall surface', async () => {
    const restore = stubReducedMotion();
    try {
      const updateSpy = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { waterfall } = prepareGeometry(target);
      pointer(waterfall, 'pointerdown', 34, 100);
      pointer(waterfall, 'pointermove', 34, 120);
      await flushPreviewPaint();
      await vi.waitFor(() => {
        const options = updateSpy.mock.calls.at(-1)?.[0];
        expect(options?.centerHz).toBe(14_040_000);
        expect(options?.panoramaShiftHz).toBe(-10_000);
      });
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      pointer(waterfall, 'pointerup', 34, 120);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
    } finally {
      restore();
    }
  });

  // The waterfall canvas's tap recognizer captures the pointer on
  // pointerdown; when the panel drag crosses its threshold the panel
  // re-captures the pointer on `.waterfall-content` and the canvas's
  // lostpointercapture bubbles up. That transfer must NOT cancel the drag.
  it('survives the waterfall canvas capture transfer and still previews and commits once', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { waterfall } = prepareGeometry(target);
      const canvas = waterfall.querySelector('canvas')!;
      pointer(canvas, 'pointerdown', 37, 100);
      pointer(canvas, 'pointermove', 37, 120);
      // The recognizer's capture on the canvas is released when the panel
      // takes the pointer over; the bubbling event is not a loss.
      pointer(canvas, 'lostpointercapture', 37, 120);
      pointer(canvas, 'pointermove', 37, 130);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-15_000));
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      pointer(canvas, 'pointerup', 37, 130);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(3);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_035_000, 0);
    } finally {
      restore();
    }
  });

  it('retires the preview the moment the captured context changes and rejects the release', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 38, 100);
      pointer(spectrum, 'pointermove', 38, 120);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-10_000));
      // A retune from another client lands mid-drag: the preview anchored
      // to the captured 14.050 MHz must not retarget the new observation.
      authorityHarness.state.current = authority({ frequencyHz: 7_050_000 });
      refreshSpectrumAuthority();
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.centerHz).toBe(7_050_000));
      // The release-side staleness guard already refuses the commit.
      pointer(spectrum, 'pointerup', 38, 120);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      expect(mockRuntime.send).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it('retires the preview when the drag surface geometry changes mid-drag', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 40, 100);
      pointer(spectrum, 'pointermove', 40, 120);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-10_000));
      // A window resize re-lays the surface out mid-drag: the captured
      // Hz-per-pixel mapping is stale, so the preview retires on the next
      // frame instead of projecting with it (the release is rejected by
      // the same geometry check).
      rect(spectrum, 0, 400);
      pointer(spectrum, 'pointermove', 40, 130);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(0));
      pointer(spectrum, 'pointerup', 40, 130);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      expect(mockRuntime.send).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it('shows the FIX-mode preview when the drag pulls the candidate into the window from outside it', async () => {
    const restore = stubReducedMotion();
    try {
      // Confirmed frequency sits OUTSIDE the fixed 14.000-14.100 window:
      // no marker at rest.
      authorityHarness.state.current = authority({ frequencyHz: 14_200_000 });
      const target = mountPanel();
      emitFrame({ mode: 1 });
      const { spectrum } = prepareGeometry(target);
      expect(target.querySelector('.waterfall-content .tune-line')).toBeNull();

      pointer(spectrum, 'pointerdown', 39, 100);
      // dx=300 px at 500 Hz/px pulls the candidate to 14.050 MHz — inside.
      pointer(spectrum, 'pointermove', 39, 400);
      await flushPreviewPaint();
      expect(target.querySelector<HTMLElement>('.waterfall-content .tune-line')!.style.left).toBe('50%');
      expect(target.querySelector<HTMLElement>('.waterfall-content .passband-overlay')!.style.left).toBe('38%');
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.tuneHz).toBe(14_050_000));
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);
      pointer(spectrum, 'pointerup', 39, 400);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
      // Released: the marker hides again until the radio confirms a
      // frequency inside the window.
      expect(target.querySelector('.waterfall-content .tune-line')).toBeNull();
    } finally {
      restore();
    }
  });

  it('moves the FIX-mode tune marker and passband with the preview and leaves the panorama parked', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame({ mode: 1 });
      const { spectrum } = prepareGeometry(target);
      const tuneLine = () => target.querySelector<HTMLElement>('.waterfall-content .tune-line')!;
      const overlay = () => target.querySelector<HTMLElement>('.waterfall-content .passband-overlay')!;
      expect(tuneLine().style.left).toBe('50%');
      expect(overlay().style.left).toBe('38%');

      pointer(spectrum, 'pointerdown', 35, 100);
      pointer(spectrum, 'pointermove', 35, 120);
      await flushPreviewPaint();
      // FIX has no panorama glide: the preview moves the proportional
      // marker (canvas tuneHz + DOM tune-line) and the passband overlay.
      expect(tuneLine().style.left).toBe('40%');
      expect(overlay().style.left).toBe('28%');
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.tuneHz).toBe(14_040_000));
      expect(spectrumRendererHarness.lastOptions.panoramaShiftHz).toBe(0);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(1);
      expect(handlerHarness.vfo.onFreqChange.mock.calls.every((call) => call[2] === 'drag')).toBe(true);

      pointer(spectrum, 'pointerup', 35, 120);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
      // The preview dies with the gesture: the marker returns to the
      // confirmed frequency until the radio reports the new one.
      expect(tuneLine().style.left).toBe('50%');
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.tuneHz).toBe(14_050_000));
    } finally {
      restore();
    }
  });

  it('schedules at most one preview update per animation frame and applies the latest candidate', async () => {
    const restore = stubReducedMotion();
    try {
      authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
      const target = mountPanel();
      emitFrame();
      const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 36, 100);
      // Settle any paint scheduled by the frame delivery first, so the
      // counted window starts from a clean rAF slate.
      await flushPreviewPaint();
      vi.mocked(requestAnimationFrame).mockClear();
      // A 120 Hz pointermove stream inside ONE animation frame: the View
      // takes every candidate synchronously while the canvas keeps AT MOST
      // one pending redraw request per surface.
      pointer(spectrum, 'pointermove', 36, 110);
      expect(vi.mocked(requestAnimationFrame)).toHaveBeenCalledTimes(2);
      pointer(spectrum, 'pointermove', 36, 120);
      pointer(spectrum, 'pointermove', 36, 130);
      expect(vi.mocked(requestAnimationFrame)).toHaveBeenCalledTimes(2);
      await flushPreviewPaint();
      await vi.waitFor(() => expect(spectrumRendererHarness.lastOptions?.panoramaShiftHz).toBe(-15_000));
      pointer(spectrum, 'pointerup', 36, 130);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(4);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_035_000, 0);
    } finally {
      restore();
    }
  });
});

// MOR-2464 — the center panorama. In CENTER scope the carrier+passband
// indicator is anchored at 50% while the visual viewport glides toward the
// commanded frequency; a late recentered frame never restarts or double-shifts.
describe('center panorama motion (MOR-2464)', () => {
  const PANORAMA_TUPLE = Object.freeze({ frequencyHz: 14_050_000, mode: 'USB',
    widthHz: 2_400, shiftHz: 0, frameMode: 0, startHz: 14_000_000, endHz: 14_100_000 });
  // Frame centered on `centerHz` (±halfSpan); `translatedDeltaHz` builds the
  // translated-stale variant whose frequency leads the frame.
  // MOR-4270: the frame window rides real wire frames (emitFrame) and the
  // region carries the passband tuple; `translatedDeltaHz` keeps building
  // the translated-stale variant.
  function panoramaProjection(centerHz = 14_050_000, halfSpan = 50_000, sequence = 1,
    translatedDeltaHz?: number) {
    const start = centerHz - halfSpan;
    const end = centerHz + halfSpan;
    const tuple = Object.freeze({ ...PANORAMA_TUPLE,
      frequencyHz: 14_050_000 + (translatedDeltaHz ?? centerHz - 14_050_000),
      startHz: translatedDeltaHz === undefined ? start : PANORAMA_TUPLE.startHz,
      endHz: translatedDeltaHz === undefined ? end : PANORAMA_TUPLE.endHz });
    emitFrame({
      startFreq: translatedDeltaHz === undefined ? start : PANORAMA_TUPLE.startHz,
      endFreq: translatedDeltaHz === undefined ? end : PANORAMA_TUPLE.endHz,
      sequence,
    });
    return Object.freeze(translatedDeltaHz === undefined
      ? { state: 'current' as const, tuple }
      : { state: 'stale' as const, translated: true as const, tuple });
  }
  function mountPanorama(initial: ReturnType<typeof panoramaProjection>) {
    const props = new SvelteMap<string, unknown>([['projection', initial], ['demanded', true]]);
    harnessLeases.push(mockRuntime.acquireHardwareScope());
    const target = mountPanel({
      get scopeRegion() {
        return Object.freeze({ passband: props.get('projection'),
          demanded: props.get('demanded') as boolean, setDemand: () => {} });
      },
      get scopeDemanded() { return props.get('demanded') as boolean; },
    });
    emitFrame({ startFreq: 14_000_000, endFreq: 14_100_000 });
    return { target, props };
  }
  const recordedShifts = (): number[] =>
    spectrumRendererHarness.render.mock.calls.map((call) => call[4]?.panoramaShiftHz ?? 0);
  // An intermediate shift exists only inside the real 0-100 ms PANORAMA_SETTLE_MS
  // band, and the rAF-as-setTimeout frame that renders it can miss that band on
  // a loaded machine (quick run 36308962513 lost it; the same flake hit
  // `does not bounce ...` in full run 40803166710). Drive the panorama clock from a
  // stubbed performance.now stepped per tick instead, so the asserted state no
  // longer depends on real elapsed time or on how many frames ran. The caller
  // owns `vi.unstubAllGlobals()`.
  const stubPanoramaClock = (): ((ms: number) => Promise<void>) => {
    let panoramaNow = 0;
    vi.stubGlobal('performance', { now: () => panoramaNow });
    return async (ms) => {
      panoramaNow += ms;
      await new Promise((resolve) => setTimeout(resolve, 0));
    };
  };
  const bindingOrders = (
    updateSpy: { mock: { calls: unknown[][]; invocationCallOrder: number[] } },
    center: number, shift: number,
  ) => {
    const orders: number[] = [];
    updateSpy.mock.calls.forEach((call, index) => {
      const opts = call[0] as Partial<WaterfallOptions> | undefined;
      if (opts?.centerHz === center && opts?.panoramaShiftHz === shift) {
        orders.push(updateSpy.mock.invocationCallOrder[index]);
      }
    });
    return orders;
  };
  it('glides the panorama across a translated→recentered transition through real intermediate frames', async () => {
    let panoramaNow = 0;
    vi.stubGlobal('performance', { now: () => panoramaNow });
    try {
      const { target, props } = mountPanorama(panoramaProjection());
      props.set('projection', panoramaProjection(14_050_000, 50_000, 1, 1_000)); flushSync();
      panoramaNow = 40;
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      expect(target.querySelector<HTMLElement>('.passband-overlay')!.style.left).toBe('38%');
      await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
      panoramaNow = 140;
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
      props.set('projection', panoramaProjection(14_051_000, 50_000, 2)); flushSync();
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(0));
      expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it('retargets a rapid reversal without overshoot and keeps the carrier anchored', async () => {
    const { target, props } = mountPanorama(panoramaProjection());
    props.set('projection', panoramaProjection(14_050_000, 50_000, 1, 1_000)); flushSync();
    props.set('projection', panoramaProjection(14_050_000, 50_000, 1, -1_000)); flushSync();
    await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(-1_000));
    const shifts = recordedShifts();
    expect(Math.max(...shifts.map(Math.abs))).toBeLessThanOrEqual(1_000);
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
    expect(mockRuntime.send).not.toHaveBeenCalled();
  });

  it('snaps the panorama under prefers-reduced-motion with no animated frames', async () => {
    const originalMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: () => {},
      removeEventListener: () => {}, addListener: () => {}, removeListener: () => {},
    }) as unknown as typeof window.matchMedia;
    try {
      const { target, props } = mountPanorama(panoramaProjection());
      spectrumRendererHarness.render.mockClear();
      props.set('projection', panoramaProjection(14_050_000, 50_000, 1, 1_000)); flushSync();
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
      expect(recordedShifts().every((v) => v === 1_000)).toBe(true);
      expect(recordedShifts().length).toBeGreaterThan(0);
    } finally {
      window.matchMedia = originalMatchMedia;
    }
  });

  it('maps a settled animated viewport click to its carrier frequency', async () => {
    const { target, props } = mountPanorama(panoramaProjection());
    const { waterfall } = prepareGeometry(target);
    props.set('projection', panoramaProjection(14_050_000, 50_000, 1, 1_000)); flushSync();
    await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 95, 100);
    pointer(canvas, 'pointerup', 95, 100);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_051_000, 0);
  });

  // MOR-2464 owner regression: click-to-tune on the panorama. The upper
  // (spectrum) area has no tap recognizer of its own — unlike WaterfallCanvas
  // — so a press/release below the drag threshold must resolve as a click
  // through the SAME release-driven path (pointerup, never the native click
  // event) and tune to the clicked position through the visual viewport.
  it('tunes a panorama spectrum-area click to the clicked frequency with the native click inert', () => {
    const { target } = mountPanorama(panoramaProjection());
    const { spectrum } = prepareGeometry(target);
    const canvas = spectrum.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 21, 100);
    pointer(canvas, 'pointerup', 21, 100);
    // The browser follows pointerup with a native click — it must stay
    // inert so one physical click emits exactly one frequency command.
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: 100 }));
    flushSync();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_050_000, 0);
  });

  it('keeps the waterfall tap at exactly one command when the browser follows with a native click', () => {
    const { target } = mountPanorama(panoramaProjection());
    const { waterfall } = prepareGeometry(target);
    const canvas = waterfall.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 22, 100);
    pointer(canvas, 'pointerup', 22, 100);
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: 100 }));
    flushSync();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledOnce();
  });

  it('emits only the pan for an above-threshold spectrum drag followed by the native click', () => {
    const { target } = mountPanorama(panoramaProjection());
    const { spectrum } = prepareGeometry(target);
    const canvas = spectrum.querySelector('canvas')!;
    pointer(canvas, 'pointerdown', 23, 100);
    pointer(canvas, 'pointermove', 23, 120);
    pointer(canvas, 'pointerup', 23, 120);
    canvas.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, clientX: 120 }));
    flushSync();
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(2);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledWith(14_040_000, 0);
  });

  it('resets the panorama while retaining waterfall history when the span changes mid-animation', async () => {
    const clearSpy = vi.spyOn(WaterfallRenderer.prototype, 'clear');
    try {
      const { target, props } = mountPanorama(panoramaProjection());
      props.set('projection', panoramaProjection(14_050_000, 50_000, 1, 1_000)); flushSync();
      await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0)).toBe(true));
      props.set('projection', panoramaProjection(14_100_000, 100_000, 2)); flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(0));
      expect(clearSpy).not.toHaveBeenCalled();
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    } finally {
      clearSpy.mockRestore();
    }
  });

  it('initializes the effective viewport before the first row is written (first mount)', async () => {
    const updateSpy = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
    const pushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    try {
      const { target, props } = mountPanorama(panoramaProjection());
      props.set('projection', panoramaProjection(14_051_000, 50_000, 2)); flushSync();
      // MOR-4206: the row is written from the waterfall's rAF paint now.
      await vi.waitFor(() => expect(pushSpy).toHaveBeenCalled());
      const binding = bindingOrders(updateSpy, 14_050_000, 0);
      expect(binding.length).toBeGreaterThan(0);
      expect(Math.min(...binding)).toBeLessThan(pushSpy.mock.invocationCallOrder[0]!);
      // No update ever binds a stale zero center to a real span.
      expect(updateSpy.mock.calls.filter((call) => call[0]?.centerHz === 0 && (call[0]?.spanHz ?? 0) > 0)).toHaveLength(0);
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(0));
    } finally {
      updateSpy.mockRestore();
      pushSpy.mockRestore();
    }
  });

  it('binds recentered frame geometry to pixels before the row is written (real component path)', async () => {
    const updateSpy = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
    const pushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let panoramaNow = 0;
    vi.stubGlobal('performance', { now: () => panoramaNow });
    try {
      const { target, props } = mountPanorama(panoramaProjection());
      // Ordinary live-spaced deliveries establish the paint history the
      // asserted recentered row below follows.
      for (let sequence = 2; sequence <= 5; sequence += 1) {
        panoramaNow += 83;
        props.set('projection', panoramaProjection(14_050_000, 50_000, sequence)); flushSync();
      }
      panoramaNow += 83;
      props.set('projection', panoramaProjection(14_050_000, 50_000, 6, 1_000)); flushSync();
      panoramaNow += 140;
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
      updateSpy.mockClear(); pushSpy.mockClear();
      panoramaNow += 83;
      props.set('projection', panoramaProjection(14_051_000, 50_000, 7)); flushSync();
      // MOR-4206: the row is written from the waterfall's rAF paint now.
      await vi.waitFor(() => expect(pushSpy).toHaveBeenCalled());
      const binding = bindingOrders(updateSpy, 14_051_000, 0);
      expect(binding.length).toBeGreaterThan(0);
      expect(Math.min(...binding)).toBeLessThan(pushSpy.mock.invocationCallOrder.at(-1)!);
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    } finally {
      vi.unstubAllGlobals();
      updateSpy.mockRestore();
      pushSpy.mockRestore();
    }
  });

  it('keeps the FIX marker proportional with no panorama shift', async () => {
    authorityHarness.state.current = authority({ frequencyHz: 14_025_000 });
    const target = mountPanel();
    emitFrame({ mode: 1 });
    expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('25%');
    await vi.waitFor(() => expect(spectrumRendererHarness.render).toHaveBeenCalled());
    expect(spectrumRendererHarness.lastOptions.panoramaShiftHz).toBe(0);
    expect(spectrumRendererHarness.lastOptions.scopeMode).toBe(1);
  });

  // MOR-2464 follow-up — the optimistic leg: while a `set_freq` intent is in
  // flight for the scoped receiver (through ack, until the radio's own
  // observation confirms it), the panorama chases the pending target instead
  // of waiting for the confirmed tuple to move. Display-only.
  describe('optimistic pending target', () => {
    it('starts gliding toward a pending set_freq target before the confirmed frequency moves', async () => {
      const advancePanorama = stubPanoramaClock();
      try {
        const initial = panoramaProjection();
        const { target, props } = mountPanorama(initial);
        setPendingFrequency(0, 14_051_000);
        flushSync();
        expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
        // 40 ms into the 100 ms settle band — the deterministic intermediate.
        await advancePanorama(40);
        await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
        await advancePanorama(100);
        await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
        expect(props.get('projection')).toBe(initial);
        expect(handlerHarness.vfo.onFreqChange).not.toHaveBeenCalled();
        expect(mockRuntime.send).not.toHaveBeenCalled();
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it('retargets a rapid pending reversal while the observed frequency stays stale', async () => {
      // MOR-2714: see `stubPanoramaClock`.
      const advancePanorama = stubPanoramaClock();
      try {
        const { target } = mountPanorama(panoramaProjection());
        setPendingFrequency(0, 14_051_000);
        flushSync();
        // 40 ms into the 100 ms settle band — the deterministic intermediate.
        await advancePanorama(40);
        await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
        setPendingFrequency(0, 14_049_000);
        flushSync();
        // Past the band: the reversal settles on the new target.
        await advancePanorama(100);
        await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(-1_000));
        expect(Math.max(...recordedShifts().map(Math.abs))).toBeLessThanOrEqual(1_000);
        expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it('does not bounce toward the stale confirmed value while the pending target survives an ack', async () => {
      const advancePanorama = stubPanoramaClock();
      try {
        const { target, props } = mountPanorama(panoramaProjection());
        setPendingFrequency(0, 14_051_000);
        flushSync();
        // 40 ms into the 100 ms settle band — the deterministic intermediate.
        await advancePanorama(40);
        await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
        // A late frame still observes the pre-tune frequency while the command
        // is only acknowledged — the accessor keeps returning the pending
        // target, so the viewport must keep gliding, never fall back.
        props.set('projection', panoramaProjection(14_050_000, 50_000, 2));
        flushSync();
        await advancePanorama(100);
        await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
        const shifts = recordedShifts();
        const firstMoving = shifts.findIndex((v) => v > 0);
        expect(shifts.slice(firstMoving).every((v) => v > 0)).toBe(true);
        expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it('falls back to the observed frequency when the pending target clears', async () => {
      const { target } = mountPanorama(panoramaProjection());
      setPendingFrequency(0, 14_051_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
      setPendingFrequency(0, null);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(0));
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });

    it('ignores a pending target scoped to the other receiver', async () => {
      const { target } = mountPanorama(panoramaProjection());
      setPendingFrequency(1, 14_051_000);
      flushSync();
      await new Promise((resolve) => setTimeout(resolve, 40));
      expect(recordedShifts().every((v) => v === 0)).toBe(true);
      // Authority switches to SUB: a MAIN-scoped pending must not move it.
      setPendingFrequency(1, null);
      setPendingFrequency(0, 14_052_000);
      authorityHarness.state.current = authority({ receiver: 1 });
      refreshSpectrumAuthority();
      flushSync();
      emitFrame({ receiver: 1 });
      await new Promise((resolve) => setTimeout(resolve, 40));
      expect(recordedShifts().filter((v) => v !== 0)).toHaveLength(0);
      // Sanity: the SUB-scoped pending does drive the SUB-authority viewport.
      setPendingFrequency(0, null);
      setPendingFrequency(1, 14_053_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(3_000));
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });
  });

  // MOR-2464 follow-up — the display-only tuning-burst latch: while local
  // gesture intent is live (repeated arrows/wheel/clicks), the viewport
  // chases the accumulator's per-gesture target — including paced-unsent
  // steps — and intermediate confirmed observations plus the pending
  // accessor's post-ack drop cannot pull it back mid-gesture. After the
  // burst ends the pending lifecycle remains the fallback, then confirmed
  // truth.
  describe('tuning burst target', () => {
    it('shields the glide when a mid-gesture observation drops the pending latch on an intermediate value', async () => {
      const { target, props } = mountPanorama(panoramaProjection());
      setTuningBurstFrequency(0, 14_051_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
      // The radio's own poll observes freqHz past the last ack boundary, so
      // `getPendingFrequencyHz` drops to null while the confirmed tuple
      // still carries an intermediate value — the exact handoff gap. The
      // burst latch must keep the viewport on the gesture target.
      setPendingFrequency(0, null);
      props.set('projection', panoramaProjection(14_050_000, 50_000, 2));
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
      const shifts = recordedShifts();
      const firstMoving = shifts.findIndex((v) => v > 0);
      expect(shifts.slice(firstMoving).every((v) => v > 0)).toBe(true);
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });

    it('prefers the paced-unsent burst target over the older emitted pending value', async () => {
      // MOR-2743: see `stubPanoramaClock` (quick run 36340105991 lost the
      // intermediate frame once).
      const advancePanorama = stubPanoramaClock();
      try {
        const { target } = mountPanorama(panoramaProjection());
        setPendingFrequency(0, 14_051_000);
        setTuningBurstFrequency(0, 14_052_000);
        flushSync();
        // 40 ms into the 100 ms settle band — the deterministic intermediate.
        await advancePanorama(40);
        await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 2_000)).toBe(true));
        // Past the band: the viewport settles on the paced-unsent target.
        await advancePanorama(100);
        await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(2_000));
        expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
      } finally {
        vi.unstubAllGlobals();
      }
    });

    it('retargets a rapid burst reversal while observations stay stale', async () => {
      const { target } = mountPanorama(panoramaProjection());
      setTuningBurstFrequency(0, 14_051_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().some((v) => v > 0 && v < 1_000)).toBe(true));
      setTuningBurstFrequency(0, 14_049_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(-1_000));
      expect(Math.max(...recordedShifts().map(Math.abs))).toBeLessThanOrEqual(1_000);
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });

    it('reconciles through pending after the burst ends, then to confirmed', async () => {
      const { target } = mountPanorama(panoramaProjection());
      setTuningBurstFrequency(0, 14_051_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(1_000));
      // Burst hold elapsed: pending (the last emitted command) keeps the
      // viewport parked at the same target — no bounce.
      setPendingFrequency(0, 14_051_000);
      setTuningBurstFrequency(0, null);
      flushSync();
      await new Promise((resolve) => setTimeout(resolve, 60));
      expect(recordedShifts().at(-1)).toBe(1_000);
      // Pending resolves: confirmed observed truth takes over.
      setPendingFrequency(0, null);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(0));
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });

    it('drops the displayed burst reactively when a paced flush emits an already-failed lifecycle', async () => {
      const emitted: { status: string }[] = [];
      const acc = createTuningAccumulator({
        emit: () => {
          const lifecycle = emitted.length === 0 ? { status: 'pending' } : { status: 'failed' };
          emitted.push(lifecycle);
          return lifecycle;
        },
        paceMs: 500, quietWindowMs: 4_000, visualHoldMs: 5_000,
      });
      mountPanorama(panoramaProjection());
      acc.step(0, 14_050_000, 14_051_000, 1);
      acc.step(0, 14_050_000, 14_051_000, 1);
      // Two rapid steps: the component chases the paced-unsent +2_000
      // target through the real publication map (glide completes well
      // inside the 500ms pace window).
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(2_000), { timeout: 2_000 });
      // The paced flush emits a lifecycle that is ALREADY terminal. The
      // map must change so the panel's $derived re-reads it and falls back
      // to the confirmed frequency — a shallow in-place mutation of the
      // old record would leave the stale target painted.
      await vi.waitFor(() => {
        expect(emitted).toHaveLength(2);
        expect(recordedShifts().at(-1)).toBe(0);
      });
      acc.cancel();
    });

    it('ignores a burst target scoped to the other receiver', async () => {
      const { target } = mountPanorama(panoramaProjection());
      setTuningBurstFrequency(1, 14_051_000);
      flushSync();
      await new Promise((resolve) => setTimeout(resolve, 40));
      expect(recordedShifts().every((v) => v === 0)).toBe(true);
      // Authority switches to SUB with no SUB-scoped burst: a MAIN-scoped
      // burst must not move it...
      setTuningBurstFrequency(1, null);
      setTuningBurstFrequency(0, 14_052_000);
      authorityHarness.state.current = authority({ receiver: 1 });
      refreshSpectrumAuthority();
      flushSync();
      emitFrame({ receiver: 1 });
      await new Promise((resolve) => setTimeout(resolve, 40));
      expect(recordedShifts().filter((v) => v !== 0)).toHaveLength(0);
      // ...while a SUB-scoped burst does drive the SUB-authority viewport.
      setTuningBurstFrequency(0, null);
      setTuningBurstFrequency(1, 14_053_000);
      flushSync();
      await vi.waitFor(() => expect(recordedShifts().at(-1)).toBe(3_000));
      expect(target.querySelector<HTMLElement>('.tune-line')!.style.left).toBe('50%');
    });
  });
});

describe('SpectrumPanel scope handle hover-intent CSS (MOR-2562)', () => {
  const style = spectrumPanelSource.slice(spectrumPanelSource.indexOf('<style>'));

  it('draws the passband handle only on hover intent and drag', () => {
    // At rest the handle's ::before is not visible: opacity 0 over the full
    // zone height — the hit zone and cursor live on the element itself.
    expect(style).toMatch(
      /\.passband-resize-zone::before\s*\{[^}]*top:\s*0;[^}]*bottom:\s*0;[^}]*width:\s*1px;[^}]*opacity:\s*0;/,
    );
    // Hover lights the 1 px translucent line only after the ~200 ms intent
    // delay; the delay sits on the hover state so leaving is immediate.
    expect(style).toMatch(
      /\.passband-resize-zone:hover::before\s*\{[^}]*opacity:\s*0\.45;[^}]*transition-delay:\s*200ms;/,
    );
    // The drag state is the 2 px line at 80 % with no delay.
    expect(style).toMatch(
      /\.passband-resize-zone\.active::before\s*\{[^}]*width:\s*2px;[^}]*opacity:\s*0\.8;[^}]*transition-delay:\s*0s;/,
    );
    // Cascade precedence: the value checks above stay green even if .active
    // is moved above :hover, so pin the order itself — while hovering and
    // dragging at once, the .active rule must win over :hover.
    expect(style).toContain('.passband-resize-zone:hover::before');
    expect(style).toContain('.passband-resize-zone.active::before');
    expect(style.indexOf('.passband-resize-zone.active::before')).toBeGreaterThan(
      style.indexOf('.passband-resize-zone:hover::before'),
    );
    // Leaving must stay immediate: the rest rule's transition carries no
    // delay — only the :hover rule above may set a transition-delay.
    const handleRest = style.match(/\.passband-resize-zone::before\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(handleRest).toMatch(/transition:\s*opacity 0\.15s ease;/);
    expect(handleRest).not.toContain('transition-delay');
    // The old always-on handle decorations are gone: no dark outline, no
    // brightness filter, no partial-height grip. The absence checks run on
    // the passband zone's own rule blocks only — an unrelated brightness
    // filter elsewhere in the panel's style block is allowed.
    const handleRules = style.match(/\.passband-resize-zone[^{]*\{[^}]*\}/g) ?? [];
    expect(handleRules.length).toBeGreaterThan(0);
    for (const rule of handleRules) {
      expect(rule).not.toMatch(/brightness\(/);
    }
    expect(style).not.toMatch(/\.passband-resize-zone[^{]*::before\s*\{[^}]*box-shadow/);
    expect(style).not.toMatch(/\.passband-resize-zone::before\s*\{[^}]*top:\s*18%/);
  });

  it('fades the split separator through a hover-intent accent line, not the solid bar', () => {
    // The faint 2 px structural rest stripe stays on the element itself.
    expect(style).toMatch(
      /\.spectrum-split-separator\s*\{[^}]*var\(--panel-border\) 3px,\s*var\(--panel-border\) 5px,/,
    );
    // The overlay line is invisible at rest and pinned to the rest stripe's
    // row on whole pixels — no transform, so no half-pixel blur.
    expect(style).toMatch(
      /\.spectrum-split-separator::after\s*\{[^}]*top:\s*4px;[^}]*height:\s*1px;[^}]*background:\s*var\(--accent, var\(--panel-border\)\);[^}]*opacity:\s*0;/,
    );
    expect(style).toMatch(
      /\.spectrum-split-separator:hover::after\s*\{[^}]*opacity:\s*0\.45;[^}]*transition-delay:\s*200ms;/,
    );
    expect(style).toMatch(
      /\.spectrum-split-separator\.active::after\s*\{[^}]*top:\s*3px;[^}]*height:\s*2px;[^}]*opacity:\s*0\.8;[^}]*transition-delay:\s*0s;/,
    );
    // Cascade precedence: the .active rule must come after :hover so the
    // drag line wins while both states apply.
    expect(style).toContain('.spectrum-split-separator:hover::after');
    expect(style).toContain('.spectrum-split-separator.active::after');
    expect(style.indexOf('.spectrum-split-separator.active::after')).toBeGreaterThan(
      style.indexOf('.spectrum-split-separator:hover::after'),
    );
    // Leaving must stay immediate: the rest rule's transition carries no
    // delay — only the :hover rule above may set a transition-delay.
    const dividerRest = style.match(/\.spectrum-split-separator::after\s*\{([^}]*)\}/)?.[1] ?? '';
    expect(dividerRest).toMatch(/transition:\s*opacity 0\.15s ease;/);
    expect(dividerRest).not.toContain('transition-delay');
    // The old solid 4 px accent bar (shared :hover/.active background) is gone.
    expect(style).not.toMatch(
      /\.spectrum-split-separator:hover,\s*\.spectrum-split-separator\.active\s*\{/,
    );
  });
});

// MOR-3631 — the production region-selection wiring. A region click in the
// real SpectrumToolbar must reach the real BandPlanOverlay through
// the shared band-plan-region state and SpectrumPanel's
// `region={bandPlanRegion}`, and the overlay must then show the new region's
// segments at an unchanged viewport. The fetch backend answers with
// `fixtures/band-plan-responses.json`, the bodies the real server handlers
// return (recorded and kept current by the Python real-handler replay
// `tests/test_band_plan_response_goldens.py`). Deleting either binding leaves
// the overlay on the previous region's segments and fails this test.
describe('SpectrumPanel band-plan region wiring (MOR-3631)', () => {
  interface GoldenSegment { start: number; end: number; label: string; notes?: string | null }
  type GoldenBody = { segments: GoldenSegment[] };
  const goldens = bandPlanGoldens as unknown as {
    layers: unknown;
    config: Record<string, unknown>;
    configPost: Record<string, unknown>;
    segments: Record<string, GoldenBody>;
  };
  const VIEW = { startFreq: 145_800_000, endFreq: 146_200_000 };
  const WINDOW = '145600000-146400000'; // viewport +/- 50 %, what the overlay requests
  const ok = (body: unknown) => ({ ok: true, json: async () => body }) as Response;
  const titlesFor = (region: string) =>
    goldens.segments[`${region}|${WINDOW}`].segments
      .filter((seg) => seg.end > VIEW.startFreq && seg.start < VIEW.endFreq)
      .map((seg) => seg.notes ?? seg.label);
  const overlayTitles = (target: HTMLElement) =>
    Array.from(target.querySelectorAll('.bandplan-overlay .band-segment'))
      .map((el) => el.getAttribute('title'));

  afterEach(() => {
    vi.unstubAllGlobals();
    resetBandPlanRegionForTests();
  });

  it('a toolbar region click refetches and re-renders the overlay for the new region', async () => {
    let serverRegion = 'US';
    const segmentUrls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === '/api/v1/band-plan/layers') return ok(goldens.layers);
      if (url === '/api/v1/band-plan/config') {
        if (init?.method === 'POST') {
          serverRegion = JSON.parse(String(init.body)).region;
          return ok(goldens.configPost[serverRegion]);
        }
        return ok(goldens.config[serverRegion]);
      }
      if (url.startsWith('/api/v1/band-plan/segments?')) {
        segmentUrls.push(url);
        const key = /start=(\d+)&end=(\d+)/.exec(url)!;
        const body = goldens.segments[`${serverRegion}|${key[1]}-${key[2]}`];
        if (!body) throw new Error(`no golden for ${serverRegion} ${url}`);
        return ok(body);
      }
      return { ok: false, status: 404 } as Response;
    }));

    const target = mountPanel();
    emitFrame({ mode: 1, ...VIEW });

    // US baseline: the overlay shows the golden US segments for the viewport.
    await vi.waitFor(() => {
      expect(overlayTitles(target)).toEqual(titlesFor('US'));
    }, { timeout: 3000 });
    expect(titlesFor('US')).not.toEqual(titlesFor('IARU-R1'));
    const fetchesBefore = segmentUrls.length;
    const urlBefore = segmentUrls.at(-1);

    // Real toolbar: open the layer menu and pick the region.
    await vi.waitFor(() => {
      expect(target.querySelector('.layer-toggle-btn')).not.toBeNull();
    }, { timeout: 3000 });
    target.querySelector<HTMLButtonElement>('.layer-toggle-btn')!.click();
    flushSync();
    const r1 = Array.from(target.querySelectorAll<HTMLButtonElement>('.region-btn'))
      .find((btn) => btn.textContent?.trim() === 'IARU-R1');
    expect(r1).toBeDefined();
    r1!.click();

    await vi.waitFor(() => {
      expect(overlayTitles(target)).toEqual(titlesFor('IARU-R1'));
    }, { timeout: 3000 });
    // One refetch for the switch (same viewport), and no loop afterwards.
    expect(segmentUrls.length).toBe(fetchesBefore + 1);
    expect(segmentUrls.at(-1)).toBe(urlBefore);
    await new Promise((resolve) => setTimeout(resolve, 400));
    expect(segmentUrls.length).toBe(fetchesBefore + 1);
  });
  it('the panel and its toolbar read the band-plan config with one request', async () => {
    const configReads: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === '/api/v1/band-plan/layers') return ok(goldens.layers);
      if (url === '/api/v1/band-plan/config') {
        configReads.push(url);
        return ok(goldens.config['IARU-R3']);
      }
      return ok({ segments: [], plansLoaded: false });
    }));

    const target = mountPanel();
    emitFrame({ mode: 1, ...VIEW });
    await vi.waitFor(() => {
      expect(target.querySelector('.layer-toggle-btn')).not.toBeNull();
    }, { timeout: 3000 });
    await new Promise((resolve) => setTimeout(resolve, 200));

    expect(configReads).toHaveLength(1);
  });

  // The production portrait layout (MobileRadioLayout) mounts the panel with
  // `hideToolbar`, so the toolbar never runs. The effective region must still
  // come from the server's band-plan config (the same state the toolbar
  // shows), or a failed segment request falls back to the local ARRL/US copy
  // while an IARU-R3 install is configured.
  describe('with the toolbar hidden (portrait layout)', () => {
    const SIXTY_M = { startFreq: 5_380_000, endFreq: 5_390_000 };

    async function mountHidden(config: () => Response | Promise<Response>) {
      const segmentUrls: string[] = [];
      vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === '/api/v1/band-plan/layers') return ok(goldens.layers);
        if (url === '/api/v1/band-plan/config') return config();
        if (url.startsWith('/api/v1/band-plan/segments?')) {
          segmentUrls.push(url);
          return { ok: false, status: 500 } as Response;
        }
        return { ok: false, status: 404 } as Response;
      }));
      const target = mountPanel({ hideToolbar: true });
      emitFrame({ mode: 1, ...SIXTY_M });
      expect(target.querySelector('.layer-toggle-btn')).toBeNull();
      await vi.waitFor(() => {
        expect(segmentUrls.length).toBeGreaterThan(0);
      }, { timeout: 3000 });
      // Let the failed request settle and the overlay re-render.
      await new Promise((resolve) => setTimeout(resolve, 300));
      flushSync();
      return target;
    }
    const labels = (target: HTMLElement) =>
      Array.from(target.querySelectorAll('.bandplan-overlay .band-segment'))
        .map((el) => el.getAttribute('aria-label'));

    it('control: a US config with a failed segment request still shows the local US copy', async () => {
      const target = await mountHidden(() => ok(goldens.config['US']));
      expect(labels(target)).toContain('PHONE');
    });

    it('an IARU-R3 config with a failed segment request shows nothing', async () => {
      const target = await mountHidden(() => ok(goldens.config['IARU-R3']));
      expect(labels(target)).toEqual([]);
    });

    it('an unreachable config never authorises the US copy', async () => {
      const target = await mountHidden(() => ({ ok: false, status: 503 }) as Response);
      expect(labels(target)).toEqual([]);
    });
  });
});

describe('golden wire through actual WebSocket transport (MOR-4270)', () => {
  it('paints panel, LCD pixels and audio overlays from producer bytes with unchanged buffers', async () => {
    const previousController = scopeControllerHarness;
    const channels = new Map<string, WsChannel>();
    const sockets: FakeSocket[] = [];
    class FakeSocket {
      static readonly OPEN = 1;
      static readonly CONNECTING = 0;
      static readonly CLOSED = 3;
      readyState = 0;
      protocol = '';
      binaryType = '';
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) { sockets.push(this); }
      send() {}
      open() { this.readyState = 1; this.onopen?.(new Event('open')); }
      receive(buffer: ArrayBuffer) { this.onmessage?.(new MessageEvent('message', { data: buffer })); }
      close() { this.readyState = 3; this.onclose?.(new CloseEvent('close', { code: 1000, wasClean: true })); }
    }
    vi.stubGlobal('WebSocket', FakeSocket);
    vi.stubGlobal('ResizeObserver', class {
      constructor(private readonly callback: ResizeObserverCallback) {}
      observe(target: Element) { this.callback([{ target, contentRect: { width: 800, height: 200 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
      unobserve() {}
      disconnect() {}
    });
    const sessionMode = vi.spyOn(controllerClient, 'sessionMode', 'get').mockReturnValue(false);
    const protocols = vi.spyOn(controllerClient, 'protocols').mockReturnValue([]);
    scopeControllerHarness = new ScopeController((name) => {
      let channel = channels.get(name);
      if (!channel) { channel = new WsChannel(false); channels.set(name, channel); }
      return channel;
    });
    try {
      const goldenState = structuredClone(IC7300_STATE);
      goldenState.main!.filterWidth = 2400;
      goldenState.main!.activeSlot = 'A';
      goldenState.main!.vfoA = { freqHz: 14_050_000, mode: 'USB', filterNum: 1, dataMode: 0 };
      const statuses = goldenState.fieldStatus;
      if (!statuses) throw new Error('Captured state omits fieldStatus');
      for (const path of ['main.activeSlot', ...Object.keys(goldenState.main!.vfoA).map((leaf) => `main.vfoA.${leaf}`)]) {
        statuses[path] = { ...statuses['main.freqHz'], storePath: path, observed: true, freshness: 'fresh', availability: 'available' };
      }
      runtimeHarness.state.currentState = goldenState;
      runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
      const hardwareStream = scopeControllerHarness.stream('hardware');
      const realSubscribe = hardwareStream.subscribe.bind(hardwareStream);
      const sinkBuffers: Array<ArrayBufferLike | null> = [];
      vi.spyOn(hardwareStream, 'subscribe').mockImplementation((sink) => realSubscribe((frame, key) => {
        sinkBuffers.push(frame.pixels?.buffer ?? null); sink(frame, key);
      }));
      const target = mountPanel();
      const lcdTarget = document.createElement('div'); document.body.append(lcdTarget);
      components.push(mount(PanadapterDisplay, { target: lcdTarget, props: {
        model: projectPeerSplitDisplay(toRadioViewModel(goldenState, runtimeHarness.state.currentCaps as typeof IC7300_CAPABILITIES)!),
        scopeStream: scopeControllerHarness.stream('hardware'),
        audioScopeStream: scopeControllerHarness.stream('audio_fft'),
      } }));
      const audioTarget = document.createElement('div'); document.body.append(audioTarget);
      components.push(mount(AudioSpectrumPanel, { target: audioTarget }));
      flushSync();
      expect(sockets).toHaveLength(1);
      const hardwareSocket = sockets[0];
      hardwareSocket.open(); flushSync();
      const radioWire = scopeWire({ sourceId: 'radio', pixels: new Uint8Array([0, 64, 128, 255]) });
      const audioWire = scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, viewStartHz: 7_100_000, viewEndHz: 7_103_000, pixels: new Uint8Array([1, 127, 254]) });
      hardwareSocket.receive(radioWire); hardwareSocket.receive(audioWire); flushSync();
      await flushWaterfallPaint(); flushSync(); await flushWaterfallPaint();
      expect(spectrumRendererHarness.render).toHaveBeenCalled();
      const panelPixels = spectrumRendererHarness.render.mock.calls.at(-1)![1];
      expect(panelPixels.buffer).toBe(radioWire);
      expect(panelPixels.byteOffset).toBe(56 + 'radio'.length);
      expect(sinkBuffers).toHaveLength(2);
      expect(sinkBuffers.every((buffer) => buffer === radioWire)).toBe(true);
      expect([...panelPixels]).toEqual([0, 64, 128, 255]);
      expect(target.querySelector('.passband-overlay')).not.toBeNull();
      expect(lcdTarget.querySelector('[data-testid="panadapter-display"]')).not.toBeNull();
      expect(lcdTarget.querySelector('[data-rf-mode="live"]')).not.toBeNull();
      expect([...lcdTarget.querySelectorAll('[data-rf-sample]')].map((node) => Number(node.getAttribute('data-rf-sample')))).toEqual([0, 64, 128, 255]);
      expect(lcdTarget.querySelectorAll('[data-axis-frequency]')).toHaveLength(5);
      expect(audioPaint).toHaveBeenCalled();
      const audioState = audioPaint.mock.calls.at(-1)![3];
      expect(audioState.pixels.buffer).toBe(audioWire);
      expect([...audioState.pixels]).toEqual([1, 127, 254]);
      expect(audioState.bandwidth).toBe(3_000);
      expect(audioState.filterWidth).toBe(2_400);
      expect(noopCanvas.fillText).toHaveBeenCalledWith('0', 400, 197);
      runtimeHarness.state.currentCaps = { ...runtimeHarness.state.currentCaps as typeof IC7300_CAPABILITIES, scopeSource: 'audio_fft' };
      const centralAudio = mountPanel();
      expect(centralAudio.querySelector('.audio-source-label')).not.toBeNull();
      hardwareSocket.receive(audioWire); flushSync(); await flushWaterfallPaint();
      const centralPixels = spectrumRendererHarness.render.mock.calls.at(-1)![1];
      expect(centralPixels.buffer).toBe(audioWire);
      expect(centralPixels.byteOffset).toBe(56 + 'audio_fft'.length);
      expect([...centralPixels]).toEqual([1, 127, 254]);
      expect(spectrumRendererHarness.lastOptions).toMatchObject({ sampleOffset: 1, sampleAmplitudeMax: 160 });

    } finally {
      for (const component of components.splice(0)) unmount(component);
      for (const channel of channels.values()) channel.disconnect();

      scopeControllerHarness = previousController;
      sessionMode.mockRestore(); protocols.mockRestore();
      vi.unstubAllGlobals();
    }
  });
});

describe('review corrections for requested View and sample retirement', () => {
  let previousMatchMedia: typeof window.matchMedia;
  beforeEach(() => {
    previousMatchMedia = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({ matches: true, addEventListener: noop, removeEventListener: noop });
    runtimeHarness.state.currentCaps = structuredClone(IC7300_CAPABILITIES);
    authorityHarness.state.current = authority({ frequencyHz: 14_050_000 });
  });
  afterEach(() => { window.matchMedia = previousMatchMedia; });

  async function selectView(view: { startHz: number; endHz: number }, frequencyHz = 14_050_000) {
    const presets = await import('$lib/runtime/scope-presets');
    const spy = vi.spyOn(presets, 'scopePresets').mockReturnValue([{ kind: 'cw', label: 'Requested segment', view }]);
    authorityHarness.state.current = authority({ frequencyHz });
    const target = mountPanel(); emitFrame({ pixels: new Uint8Array(100).fill(80) }); await flushWaterfallPaint();
    const select = target.querySelector<HTMLSelectElement>('[data-testid="scope-preset-select"]')!;
    select.value = '0'; select.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync(); await flushWaterfallPaint();
    return { target, restore: () => spy.mockRestore() };
  }
  const requested = (target: HTMLElement) => JSON.parse(target.querySelector('.spectrum-panel')!.getAttribute('data-scope-subscription')!).view;

  it('excludes the native carrier from a requested segment without painting a clamped marker', async () => {
    const { target, restore } = await selectView({ startHz: 14_000_000, endHz: 14_040_000 });
    try {
      expect(spectrumRendererHarness.lastOptions).toMatchObject({ spanHz: 40_000, tuneHz: 0, showTuneIndicator: false });
      expect(target.querySelector('.waterfall-content .tune-line')).toBeNull();
      expect(target.querySelector('.passband-overlay')).toBeNull();
    } finally { restore(); }
  });

  it('includes an uncovered carrier while mapping only actual source columns in the wider View', async () => {
    const { target, restore } = await selectView({ startHz: 14_000_000, endHz: 14_300_000 }, 14_200_000);
    try {
      const { renderSpectrum } = await vi.importActual<typeof import('$lib/renderers/spectrum-renderer')>('$lib/renderers/spectrum-renderer');
      const ctx = { ...noopCanvas, setLineDash: noop, lineTo: vi.fn(), moveTo: vi.fn() };
      const options = spectrumRendererHarness.lastOptions;
      recordCanvasLayers(ctx as unknown as CanvasRenderingContext2D);
      renderSpectrum(ctx as unknown as CanvasRenderingContext2D, new Uint8Array(100).fill(80), 300, 100, options);
      const covered = ctx.lineTo.mock.calls.filter(([x, y]) => x < 300 && y === 0).map(([x]) => x);
      expect(Math.max(...covered)).toBe(99);
      expect(options).toMatchObject({ spanHz: 300_000, sampleSpanHz: 100_000, tuneHz: 14_200_000, showTuneIndicator: true });
      expect(parseFloat(target.querySelector<HTMLElement>('.waterfall-content .tune-line')!.style.left)).toBeCloseTo(200 / 3);
    } finally { restore(); }
  });

  it('pairs both zoom legends with the displayed span and click coordinates', async () => {
    const target = mountPanel(); emitFrame(); await flushWaterfallPaint();
    target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click(); flushSync(); await flushWaterfallPaint();
    expect([...target.querySelectorAll('.freq-axis .tick')].map(node => node.textContent)).toEqual(['-25k', '-13k', '0', '+13k', '+25k']);
    expect(target.querySelector('.span-left')!.textContent).toBe('-25 kHz');
    expect(target.querySelector('.span-right')!.textContent).toBe('+25 kHz');
    const { spectrum } = prepareGeometry(target); pointer(spectrum, 'pointerdown', 201, 150); pointer(spectrum, 'pointerup', 201, 150);
    expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_063_000, 0);
  });

  it('shows absolute preset endpoints in both legends and uses them for click-to-tune', async () => {
    const { target, restore } = await selectView({ startHz: 14_000_000, endHz: 14_200_000 });
    try {
      expect(target.querySelector('.freq-axis')!.textContent).toContain('14.000');
      expect(target.querySelector('.freq-axis')!.textContent).toContain('14.200');
      expect(target.querySelector('.span-left')!.textContent).toContain('14.000');
      expect(target.querySelector('.span-right')!.textContent).toContain('14.200');
      const { spectrum } = prepareGeometry(target); pointer(spectrum, 'pointerdown', 202, 25); pointer(spectrum, 'pointerup', 202, 25);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledExactlyOnceWith(14_025_000, 0);
    } finally { restore(); }
  });

  it.each(['zoom', 'preset'] as const)('resizes the real filter width in displayed %s coordinates', async (kind) => {
    const geometry = await import('../passband-geometry');
    const actual = await vi.importActual<typeof import('../passband-geometry')>('../passband-geometry');
    const previousWidth = passbandHarness.getFilterWidthFromRightEdgePx.getMockImplementation()!;
    const previousGeometry = vi.mocked(geometry.getPassbandGeometry).getMockImplementation()!;
    passbandHarness.getFilterWidthFromRightEdgePx.mockImplementation(actual.getFilterWidthFromRightEdgePx);
    vi.mocked(geometry.getPassbandGeometry).mockImplementation(actual.getPassbandGeometry);
    let restore = noop;
    try {
      let target: HTMLElement;
      if (kind === 'preset') { const selected = await selectView({ startHz: 14_000_000, endHz: 14_200_000 }); target = selected.target; restore = selected.restore; }
      else { target = mountPanel(); emitFrame(); target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click(); flushSync(); await flushWaterfallPaint(); }
      const { waterfall } = prepareGeometry(target);
      const zone = waterfall.querySelector('.passband-resize-zone')!;
      const edge = kind === 'zoom' ? 110 : 52.5;
      pointer(zone, 'pointerdown', 203, edge); pointer(waterfall, 'pointermove', 203, edge); pointer(waterfall, 'pointerup', 203, edge);
      expect(handlerHarness.filter.onFilterWidthCommit).toHaveBeenCalledExactlyOnceWith(2_500, 0, 17);
    } finally {
      restore(); passbandHarness.getFilterWidthFromRightEdgePx.mockImplementation(previousWidth);
      vi.mocked(geometry.getPassbandGeometry).mockImplementation(previousGeometry);
    }
  });

  it('retires resize capture when the requested View changes before release', async () => {
    const target = mountPanel(); emitFrame(); const { waterfall } = prepareGeometry(target);
    pointer(waterfall.querySelector('.passband-resize-zone')!, 'pointerdown', 204, 110);
    pointer(waterfall, 'pointermove', 204, 120);
    target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click(); flushSync();
    pointer(waterfall, 'pointerup', 204, 120);
    expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
  });

  it('leaves ordinary clicks free to follow pending, confirmed and recentered frames', async () => {
    const target = mountPanel(); emitFrame(); await flushWaterfallPaint(); const { spectrum } = prepareGeometry(target);
    pointer(spectrum, 'pointerdown', 205, 150); pointer(spectrum, 'pointerup', 205, 150);
    setPendingFrequency(0, 14_075_000); flushSync(); await flushWaterfallPaint();
    expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_075_000);
    authorityHarness.state.current = authority({ frequencyHz: 14_075_000 }); refreshSpectrumAuthority(); setPendingFrequency(0, null);
    emitFrame({ startFreq: 14_025_000, endFreq: 14_125_000 }); await flushWaterfallPaint();
    expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_075_000);
    authorityHarness.state.current = authority({ frequencyHz: 14_085_000 }); refreshSpectrumAuthority();
    emitFrame({ startFreq: 14_035_000, endFreq: 14_135_000 }); await flushWaterfallPaint();
    expect(spectrumRendererHarness.lastOptions.centerHz).toBe(14_085_000);
  });

  it.each([
    ['zoom', 'pointerup'], ['zoom', 'pointercancel'], ['preset', 'pointerup'], ['preset', 'pointercancel'],
  ] as const)('preserves persistent %s View through drag %s', async (kind, ending) => {
    let restore = noop;
    try {
      let target: HTMLElement;
      if (kind === 'preset') { const selected = await selectView({ startHz: 14_000_000, endHz: 14_200_000 }); target = selected.target; restore = selected.restore; }
      else { target = mountPanel(); emitFrame(); target.querySelector<HTMLButtonElement>('[data-testid="scope-zoom-in"]')!.click(); flushSync(); await flushWaterfallPaint(); }
      const initial = requested(target); const { spectrum } = prepareGeometry(target);
      pointer(spectrum, 'pointerdown', 206, 100); pointer(spectrum, 'pointermove', 206, 120); pointer(spectrum, ending, 206, 120);
      expect(requested(target)).toEqual(initial);
      expect(handlerHarness.vfo.onFreqChange).toHaveBeenCalledTimes(ending === 'pointerup' ? 2 : 1);
    } finally { restore(); }
  });

  it('retires a live spectrum on empty non-OOR frames and recovers without dropping waterfall history', async () => {
    const target = mountPanel(); emitFrame({ pixels: new Uint8Array([80, 80]) }); await flushWaterfallPaint();
    const waterfall = target.querySelector('.waterfall-content canvas');
    spectrumRendererHarness.render.mockClear();
    emitFrame({ pixels: new Uint8Array(0), outOfRange: false }); await flushWaterfallPaint();
    expect(target.querySelector('.spectrum-area canvas')).toBeNull();
    const hatch = target.querySelector<HTMLElement>('.spectrum-area .scope-hatch[data-reason="no pixels"]');
    expect(hatch).not.toBeNull();
    expect(hatch!.style.left).toBe('0%');
    expect(hatch!.style.width).toBe('100%');
    expect(spectrumRendererHarness.render).not.toHaveBeenCalled();
    expect(target.querySelector('.waterfall-content canvas')).toBe(waterfall);
    emitFrame({ pixels: new Uint8Array([1, 2, 3]) }); await flushWaterfallPaint();
    expect([...spectrumRendererHarness.render.mock.calls.at(-1)![1]]).toEqual([1, 2, 3]);
    expect(target.querySelector('.spectrum-area .scope-hatch[data-reason="no pixels"]')).toBeNull();
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    expect(target.querySelector('.waterfall-content canvas')).toBe(waterfall);
  });

  it('retires resize capture if a same-edge preset changes the displayed carrier position', async () => {
    const presets = await import('$lib/runtime/scope-presets');
    const spy = vi.spyOn(presets, 'scopePresets').mockReturnValue([{ kind: 'cw', label: 'Same edges', view: { startHz: 14_010_000, endHz: 14_110_000 } }]);
    try {
      const target = mountPanel(); emitFrame(); setPendingFrequency(0, 14_060_000); flushSync(); await flushWaterfallPaint();
      const before = spectrumRendererHarness.lastOptions.centerHz; const { waterfall } = prepareGeometry(target);
      pointer(waterfall.querySelector('.passband-resize-zone')!, 'pointerdown', 207, 110);
      pointer(waterfall, 'pointermove', 207, 120);
      const select = target.querySelector<HTMLSelectElement>('[data-testid="scope-preset-select"]')!;
      select.value = '0'; select.dispatchEvent(new Event('change', { bubbles: true })); flushSync();
      expect(spectrumRendererHarness.lastOptions.centerHz).toBe(before);
      expect(requested(target)).toEqual({ startHz: 14_010_000, endHz: 14_110_000 });
      pointer(waterfall, 'pointerup', 207, 120);
      expect(handlerHarness.filter.onFilterWidthCommit).not.toHaveBeenCalled();
    } finally { spy.mockRestore(); }
  });

  it('uses the existing ordinary toolbar control classes for View controls', () => {
    const target = mountPanel(); emitFrame();
    expect(target.querySelector('[data-testid="scope-preset-select"]')!.classList.contains('toolbar-select')).toBe(true);
    expect(target.querySelector('[data-testid="scope-zoom-in"]')!.classList.contains('toolbar-btn')).toBe(true);
  });
});
