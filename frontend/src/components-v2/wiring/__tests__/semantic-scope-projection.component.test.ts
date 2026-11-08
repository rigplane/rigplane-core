import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
/** MOR-4241 wire documents are replayed by Python's
 * test_scope_health_watchdog_mor3690.py::test_scope_ctr_control_and_scope_wire_replay
 * through the real control and scope WebSocket handlers (only hardware faked).
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import scopeOutOfRangeFixture from '../../../../../tests/fixtures/ic705/scope_out_of_range.json';
import { createRawSnippet, flushSync, mount, unmount, type Snippet } from 'svelte';
import * as svelteClient from 'svelte/internal/client';
import { SvelteMap } from 'svelte/reactivity';
import { createClassComponent } from 'svelte/legacy';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { ScopeStream } from '$lib/runtime/scope-stream';
import type { ManagedScopeRegion } from '$lib/runtime/adapters/scope-display-projection';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import { SpectrumRenderer } from '$lib/renderers/spectrum-renderer';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import * as passband from '$lib/runtime/adapters/scope-passband-display';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import * as controlTransport from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import { setRadioReady, setRadioHealth } from '$lib/stores/connection.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';
import type { WsCommand, WsIncoming } from '$lib/types/protocol';
import { IC7300_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';

const h = vi.hoisted(() => ({
  scope: null as unknown as ScopeController, resources: null as unknown as PresentationResourceHost<unknown>,
  tx: null as unknown as ManagedAppTxController, session: { state: 'connected', epoch: 1 },
  listeners: new Set<(next: { state: string; epoch: number }) => void>(),
  authoritySubscribers: new Set<(next: {
    state: ServerState | null; caps: Capabilities | null; session: { state: string; epoch: number };
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: true, rxEnabled: false, volume: 0 },
  frequency: vi.fn(), width: vi.fn(), raw: vi.fn(), acquire: vi.fn(),
  channelRequests: [] as string[],
}));
const surfaceEffects = vi.hoisted(() => ({ next: 0, runs: new Map<number, number>() }));
const realUserEffect = svelteClient.user_effect;
function observeSurfaceEffects() { vi.spyOn(svelteClient, 'user_effect').mockImplementation((fn: () => void) => {
  const caller = new Error().stack?.split('\n').find(line => line.includes('.svelte:')) ?? '';
  if (!caller.includes('SemanticRadioSurfaces.svelte')) return realUserEffect(fn);
  const id = ++surfaceEffects.next;
  surfaceEffects.runs.set(id, 0);
  return realUserEffect(() => {
    surfaceEffects.runs.set(id, surfaceEffects.runs.get(id)! + 1);
    return fn();
  });
}); }
vi.mock('$lib/runtime', async () => {
  const { radio } = await import('$lib/stores/radio.svelte');
  const { getCapabilities } = await import('$lib/stores/capabilities.svelte');
  return { get presentationResources() { return h.resources; }, runtime: {
    get state() { return radio.current; }, get caps() { return getCapabilities(); },
    get scope() { return h.scope; }, get controlSession() { return h.session; },
    subscribeControlSession: (fn: (next: typeof h.session) => void) => { h.listeners.add(fn); return () => h.listeners.delete(fn); },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: radio.current, caps: getCapabilities(), session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    onTxAudioDied: () => () => {}, get audio() { return h.audio; }, connectionAudio: false,
    connectionStatus: 'connected', radioPowerOn: true, connection: { status: 'connected' },
    defaultScopeStatus: { source: 'hardware', available: true, resourceSelected: true, demand: 1,
      lifecycle: 'streaming', transport: 'connected', frameSeen: true },
    subscribeDx: () => () => {},
    acquireHardwareScope: (...args: unknown[]) => { h.acquire(...args); return h.resources.acquire('hardware-scope', 'panel'); },
    releaseHardwareScope: (lease: Parameters<typeof h.resources.release>[0]) => h.resources.release(lease),
  } };
});
vi.mock('$lib/runtime/frontend-runtime', async () => await import('$lib/runtime'));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({ getManagedAppTxController: () => h.tx }));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({ deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }), getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }) }));
const pendingWidth = new SvelteMap<string, number>();
vi.mock('$lib/runtime/adapters/panel-adapters', async (original) => ({
  ...await original<typeof import('$lib/runtime/adapters/panel-adapters')>(),
  getVfoHandlers: () => ({ onFreqChange: h.frequency }), getFilterHandlers: () => ({ onFilterWidthCommit: h.width }),
  getFilterWidthCommandLifecycle: () => ({ busy: pendingWidth.has('target'), target: pendingWidth.get('target'), presentation: { receiver: 0 } }),
}));

import { radio, resetRadioState } from '$lib/stores/radio.svelte';
import { editState } from '$lib/transport/__tests__/support/state-edit';
import { clearCapabilities, setCapabilities, getCapabilities } from '$lib/stores/capabilities.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import RadioLayout from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

function publishAuthority(): void {
  for (const subscriber of h.authoritySubscribers) subscriber({
    state: radio.current, caps: getCapabilities(), session: h.session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}

function channel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>(), states = new Set<(state: string) => void>();
  const sessions = new Set<(next: { state: string; epoch: number }) => void>();
  let state = 'disconnected', epoch = 0;
  function transition(next: string) {
    if (next === 'connected' && state !== 'connected') epoch++;
    state = next; for (const fn of states) fn(state);
    for (const fn of sessions) fn({ state, epoch });
  }
  return { get state() { return state; }, get sessionEpoch() { return epoch; },
    connect: vi.fn(() => transition('connecting')), disconnect: vi.fn(() => transition('disconnected')),
    sendRaw: vi.fn(() => true), onMessage: () => () => {},
    onBinary: (fn: (buffer: ArrayBuffer) => void) => { binary.add(fn); return () => binary.delete(fn); },
    onStateChange: (fn: (value: string) => void) => { states.add(fn); return () => states.delete(fn); },
    onSessionTransition: (fn: (next: { state: string; epoch: number }) => void) => { sessions.add(fn); return () => sessions.delete(fn); },
    connected() { transition('connected'); },
    frame(receiver = 0, pixels = [0, 128, 255], captured?: ArrayBuffer) {
      const buffer = captured ?? scopeWire({ sourceId: 'radio', receiver, pixels: new Uint8Array(pixels), viewStartHz: 14_000_000, viewEndHz: 14_100_000 });
      for (const fn of binary) fn(buffer); flushSync();
    }, count: () => binary.size,
  };
}
function fixture(scheme: 'single' | 'ab' = 'single') {
  const rx = { freqHz: 14_050_000, mode: 'USB', filter: 1, dataMode: 0, filterWidth: 2400, ifShift: 0,
    activeSlot: 'A', vfoA: { freqHz: 14_050_000, mode: 'USB', filterNum: 1 },
    vfoB: { freqHz: 14_060_000, mode: 'USB', filterNum: 1 } };
  const paths = ['active', 'split', 'dualWatch', ...Object.keys(rx).map(k => `main.${k}`),
    ...['vfoA', 'vfoB'].flatMap(k => Object.keys(rx.vfoA).map(leaf => `main.${k}.${leaf}`))];
  radio.current = { providerGeneration: 1, stateContractVersion: 1, active: 'MAIN', main: rx,
    split: false, dualWatch: false, ptt: false, txTarget: { status: 'unknown' },
    fieldStatus: Object.fromEntries(paths.map(path => [path, { storePath: path, observed: true,
      freshness: 'fresh', availability: 'available', lastObservedMonotonic: 10 }])) } as ServerState;
  expect(setCapabilities({ providerGeneration: 1, stateContractVersion: 1, model: 'fixture', scope: true,
    scopeSource: 'hardware', audio: false, tx: false, capabilities: ['scope', 'filter_width', 'if_shift', 'data_mode'],
    receivers: 1, vfoScheme: scheme, freqRanges: [], modes: ['USB'], filters: ['FIL1'],
    filterConfig: { USB: { defaults: [2400], minHz: 100, maxHz: 3600, stepHz: 100 } },
    txBands: [], audioConfig: { sampleRate: 48000, channels: 1, codecs: [] }, webrtc: { available: false, enabled: false },
  } as unknown as Capabilities)).toBe(true);
  publishAuthority();
}
/**
 * MOR-4213: a change arrives as a NEW snapshot built by the client's delta
 * applier, as in production; the store is never edited in place.
 */
function deliver(change: (state: ServerState) => void): void {
  radio.current = editState(radio.current!, change);
}
function renew(marker: number, stale = false) {
  const next = JSON.parse(JSON.stringify(radio.current!)) as ServerState;
  for (const status of Object.values(next.fieldStatus!)) status.lastObservedMonotonic = marker;
  for (const path of ['main.filterWidth', 'main.ifShift']) Object.assign(next.fieldStatus![path],
    { freshness: stale ? 'stale' : 'fresh', availability: stale ? 'stale' : 'available' });
  radio.current = next; publishAuthority(); flushSync();
}
// MOR-3094 / GitHub #3950 — an IC-9700-class radio declares `vfoReadback:
// 'none'`, so its missing active-receiver identity is a declared structural
// absence, not a transient loss: raw hardware scope/waterfall may display,
// while every receiver-dependent projection and command stays unavailable.
function noReadback() {
  setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'main_sub',
    capabilities: [...getCapabilities()!.capabilities, 'dual_rx'], vfoReadback: 'none' });
  deliver((s) => { s.active = null; });
  publishAuthority();
}
let wire: ReturnType<typeof channel>, target: HTMLDivElement;
let component: ReturnType<typeof mount> | null, legacyComponent: ReturnType<typeof createClassComponent> | null;
let tx: ManagedAppTxHarness;
let region: (() => ManagedScopeRegion | undefined) | undefined;
const snippet = createRawSnippet<[Snippet | undefined, ManagedScopeRegion | undefined]>((toolbar, managed) => {
  region = managed;
  return { render: () => '<div data-projection-probe></div>', setup: (element) => { element.setAttribute('data-toolbar', String(typeof toolbar())); } };
});
async function mountHost(layout = true, skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2') {
  target = document.createElement('div'); document.body.append(target);
  component = layout ? mount(RadioLayout, { target, props: { skinId } })
    : mount(SemanticRadioSurfaces, { target, props: { regions: true, displayFrameSource: 'hardware', regionContent: snippet, scopeControlsInRegionContent: true } });
  flushSync(); await Promise.resolve(); flushSync();
}
async function render(layout = true, skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2') {
  await mountHost(layout, skinId); wire.connected(); wire.frame();
}
function toggle() {
  // MOR-2545 PR2: on a hosted toolbar (desktop-v2 / sdr-test with a hardware
  // scope) VIEW lives in the More payload, not the closed row — open the
  // panel first. Unhosted and non-layout mounts keep VIEW in the row.
  const inRow = target.querySelector<HTMLButtonElement>('.scope-demand-toggle');
  if (inRow) { inRow.click(); flushSync(); return; }
  target.querySelector<HTMLButtonElement>('[data-testid="scope-more"]')!.click(); flushSync();
  target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click(); flushSync();
}
const overlay = () => target.querySelector<HTMLElement>('.passband-overlay');
function clear() {
  for (const selector of ['.spectrum-area canvas', '.freq-axis', '.passband-overlay', '.passband-resize-zone']) {
    expect(target.querySelector(selector), selector).toBeNull();
  }
  // CENTER's static ruler may survive a data gap; RF-dependent geometry must not.
  for (const line of target.querySelectorAll<HTMLElement>('.tune-line')) {
    expect(line.style.left).toBe('50%');
  }
  const history = target.querySelector<HTMLElement>('.waterfall-history');
  if (history) expect(history.style.visibility).toBe('hidden');
}

// MOR-4206: the waterfall paints through the latest-frame pump on rAF, and
// the stubbed rAF never fires on its own — flush the paint queue at the
// points where a test expects a paint (same helper as the spectrum tests).
let rafCallbacks: Map<number, FrameRequestCallback>;
let rafId: number;
function flushPaint(): void {
  const pending = [...rafCallbacks.values()];
  rafCallbacks.clear();
  for (const callback of pending) callback(performance.now());
}

beforeEach(() => {
  observeSurfaceEffects();
  surfaceEffects.next = 0; surfaceEffects.runs.clear();
  vi.useFakeTimers();
  rafCallbacks = new Map(); rafId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { rafCallbacks.set(++rafId, callback); return rafId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
  vi.stubGlobal('ResizeObserver', class { constructor(private callback: ResizeObserverCallback) {} observe(target: Element) { this.callback([{ target, contentRect: { width: 800, height: 200 } } as ResizeObserverEntry], this as unknown as ResizeObserver); } unobserve() {} disconnect() {} });
  const noop = () => {}; const ctx = new Proxy({ measureText: () => ({ width: 1 }),
    createImageData: () => ({ data: new Uint8ClampedArray(4) }), getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    createLinearGradient: () => ({ addColorStop: noop }) }, { get: (obj, key) => key in obj ? obj[key as keyof typeof obj] : noop });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never);
  tx = new ManagedAppTxHarness(); h.tx = tx.controller; h.session = { state: 'connected', epoch: 1 }; h.listeners.clear();
  h.frequency.mockClear(); h.width.mockClear(); pendingWidth.clear(); h.acquire.mockClear(); wire = channel();
  h.channelRequests = [];
  h.scope = new ScopeController((name: string) => { h.channelRequests.push(name); return wire as never; },
    { now: () => Date.now(), setTimeout, clearTimeout, setInterval, clearInterval });
  h.resources = new PresentationResourceHost('test');
  h.resources.configure('hardware-scope', { available: true, selected: true, driver: h.scope.hardwareScopeDriver });
  fixture(); region = undefined; component = null; legacyComponent = null;
});
afterEach(async () => {
  if (component) await unmount(component); legacyComponent?.$destroy(); flushSync(); await h.resources.teardown();
  expect(h.listeners.size).toBe(0); expect(h.authoritySubscribers.size).toBe(0); expect(tx.trace()).toEqual([]);
  document.body.innerHTML = ''; resetRadioState(); clearCapabilities(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

describe('one managed scope owner through the real region and panel', () => {
  it('admits known receiver geometry while an unobserved active slot keeps passband authority closed', async () => {
    fixture('ab');
    deliver(state => { state.fieldStatus!['main.activeSlot'].observed = false; });
    publishAuthority(); await render(false);
    expect(region!()!.geometryAdmitted).toBe(true);
    expect(region!()!.passband.state).toBe('unknown');
    await unmount(component!); component = null;
    await render();
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    expect(overlay()).toBeNull();
  });
  it('runs no surface effects for 100 pixel-only deliveries and once at most per geometry change', async () => {
    await render(false);
    expect(surfaceEffects.runs.size).toBeGreaterThan(0);
    const initial = new Map(surfaceEffects.runs);
    const initialReceipt = h.scope.stream('hardware').receipt;
    for (let index = 0; index < 100; index += 1) wire.frame(0, [index, 128, 255]);
    expect(h.scope.stream('hardware').receipt).toBe(initialReceipt + 100);
    expect(surfaceEffects.runs).toEqual(initial);
    const pixelRuns = [...surfaceEffects.runs].reduce((total, [id, count]) => total + count - initial.get(id)!, 0);
    expect(pixelRuns).toBe(0);
    wire.frame(0, [0, 128, 255, 64]);
    const changes = [...surfaceEffects.runs].map(([id, count]) => count - initial.get(id)!);
    const geometryRuns = changes.reduce((total, count) => total + count, 0);
    console.info('SRS effect budget', { registered: initial.size, pixelRuns, geometryRuns });
    expect(geometryRuns).toBeGreaterThan(0);
    expect(geometryRuns).toBeLessThanOrEqual(1);
  });
  it('sends CTR through the real client and consumes the paired server recovery documents', async () => {
    const documents = scopeOutOfRangeFixture as unknown as {
      streamWireHex: string; inRangeWireHex: string; ctrCommand: WsCommand;
      startHz: number; endHz: number; inRangeStartHz: number; inRangeEndHz: number;
      ctrResponse: Extract<WsIncoming, { type: 'response' }>;
    };
    deliver(s => {
      s.main.freqHz = 146_520_000;
      s.scopeControls = { ...IC7300_STATE.scopeControls!, mode: 1 };
      s.fieldStatus!['scopeControls.mode'] = IC7300_STATE.fieldStatus!['scopeControls.mode'];
    });
    publishAuthority();
    instances.length = 0;
    vi.stubGlobal('WebSocket', MockWebSocket);
    setRadioReady(true); setRadioHealth(null);
    controlTransport.connect('ws://station.test/api/v1/ws');
    const socket = instances[0]; socket.simulateOpen();
    try {
      await render();
      wire.frame(0, [], scopeWire({ sourceId: 'radio', outOfRange: true, pixels: null, viewStartHz: documents.startHz, viewEndHz: documents.endHz }));
      const overlay = target.querySelector('[data-testid="scope-out-of-range"]')!;
      expect(overlay).not.toBeNull();
      overlay.querySelector<HTMLButtonElement>('button')!.click(); flushSync();
      const command = socket.sent.map(raw => JSON.parse(raw) as WsCommand)
        .find(message => message.type === 'cmd' && message.name === 'set_scope_mode');
      expect(command).toEqual({ ...documents.ctrCommand, id: expect.any(String) });
      socket.simulateMessage(JSON.stringify({ ...documents.ctrResponse, id: command!.id }));
      wire.frame(0, [], scopeWire({ sourceId: 'radio', pixels: Uint8Array.from(Buffer.from(documents.inRangeWireHex, 'hex')).slice(16), viewStartHz: documents.inRangeStartHz, viewEndHz: documents.inRangeEndHz }));
      expect(target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
      expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    } finally {
      controlTransport.disconnect(); resetCommandLifecycle();
      setRadioReady(false); setRadioHealth(null);
    }
  });

  it('keeps captured OOR content distinct from transport loss through the real host', async () => {
    await render();
    wire.frame(0, [], scopeWire({ sourceId: 'radio', outOfRange: true, pixels: null, viewStartHz: scopeOutOfRangeFixture.startHz, viewEndHz: scopeOutOfRangeFixture.endHz }));
    expect(h.scope.hardwareScopeConnected).toBe(true);
    expect(target.querySelector('[data-testid="scope-out-of-range"]')?.textContent)
      .toContain('144.000–144.500 MHz');
    expect(target.textContent).not.toContain('Scope disconnected');
    wire.frame();
    expect(target.querySelector('[data-testid="scope-out-of-range"]')).toBeNull();
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
  });

  it.each([50, 100, 200])('keeps the selected panorama drawn across interleaved receivers every %s ms', async interval => {
    setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'main_sub',
      capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    deliver((s) => { s.sub = structuredClone(radio.current!.main); });
    publishAuthority();
    const callbacks = new Map<number, FrameRequestCallback>();
    let nextRaf = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
      const id = ++nextRaf; callbacks.set(id, callback); return id;
    });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => callbacks.delete(id));
    vi.stubGlobal('ResizeObserver', class {
      constructor(private callback: ResizeObserverCallback) {}
      observe(target: Element) {
        this.callback([{ target, contentRect: { width: 240, height: 80 } } as ResizeObserverEntry], this as unknown as ResizeObserver);
      }
      unobserve() {} disconnect() {}
    });
    const render = vi.spyOn(SpectrumRenderer.prototype, 'render');
    const rows = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    const project = vi.spyOn(passband, 'projectScopePassbandDisplay');
    const context = document.createElement('canvas').getContext('2d')!;
    const line = vi.fn<(x: number, y: number) => void>();
    context.lineTo = line;
    function draw() {
      flushSync();
      const pending = [...callbacks.values()]; callbacks.clear();
      for (const callback of pending) callback(performance.now());
      flushSync();
    }
    await mountHost(); wire.connected(); wire.frame(0); draw();
    const canvas = target.querySelector('.spectrum-area canvas');
    expect(canvas).not.toBeNull();
    expect(render).toHaveBeenCalled();
    expect(new Set(line.mock.calls.map(([, y]) => y)).size).toBeGreaterThan(1);
    const selected = render.mock.lastCall![1];
    const initialFrequency = 14_050_000;
    expect(radio.current!.main.freqHz).toBe(initialFrequency);
    for (let tick = 1; tick <= 3; tick++) {
      vi.advanceTimersByTime(interval);
      deliver((s) => { s.main.freqHz = initialFrequency + tick * 100; });
      renew(10 + tick);
      const before = rows.mock.calls.length;
      wire.frame(1, [255, 0, 0]); draw();
      expect(target.querySelector('.spectrum-area canvas')).toBe(canvas);
      expect(target.querySelector<HTMLElement>('.waterfall-history')!.style.visibility).toBe('visible');
      expect(target.querySelector('.scope-disconnected-overlay')).toBeNull();
      expect(rows).toHaveBeenCalledTimes(before);
      expect(render.mock.lastCall![1]).toEqual(selected);
      expect(project.mock.lastCall![1].state?.main.freqHz).toBe(initialFrequency + tick * 100);
      wire.frame(0); draw();
      expect(rows).toHaveBeenCalledTimes(before + 1);
    }
  });

  it('appends only accepted receipts, including identical bins, and seeds recovery once', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); flushPaint(); expect(push).toHaveBeenCalledTimes(1);
    renew(10, true); expect(push).toHaveBeenCalledTimes(1);
    expect(target.querySelector('.passband-freshness')).toBeNull();
    renew(10); expect(push).toHaveBeenCalledTimes(1);
    deliver((s) => { s.ptt = true; }); publishAuthority(); flushSync(); expect(push).toHaveBeenCalledTimes(1);
    // MOR-3471: the gate collapses sub-40ms-spaced deliveries as a replay
    // burst; advance to the live fps=20 cadence (50 ms) so this consecutive
    // frame classifies as live, not burst drain.
    vi.advanceTimersByTime(50); flushSync();
    wire.frame(); flushPaint(); expect(push).toHaveBeenCalledTimes(2);
    const shared = h.resources.acquire('hardware-scope', 'independent');
    toggle(); clear(); wire.frame(); expect(push).toHaveBeenCalledTimes(2);
    toggle(); flushPaint(); expect(push).toHaveBeenCalledTimes(3);
    renew(11); expect(push).toHaveBeenCalledTimes(3);
    vi.advanceTimersByTime(500); flushSync(); clear();
    wire.frame(); flushPaint(); expect(push).toHaveBeenCalledTimes(4);
    h.resources.release(shared);
  });
  // MOR-2425/R41: the panel paints no per-field freshness — the `◷` chip and
  // its aria-label are gone, in both states.
  it('paints no passband freshness chip in either state', async () => {
    await render();
    for (const stale of [true, false, true]) {
      renew(10, stale);
      expect(target.querySelector('.passband-freshness')).toBeNull();
      expect(target.textContent).not.toContain('◷');
    }
  });
  it('recovers when an independent hardware lease predates managed authority', async () => {
    const shared = h.resources.acquire('hardware-scope', 'independent-before-host');
    await Promise.resolve();
    await render(); wire.frame();
    expect(h.scope.hardwareScopeConnected).toBe(true);
    expect(h.resources.snapshot('hardware-scope').demand).toBe(2);
    expect(overlay()).not.toBeNull();
    h.resources.release(shared);
  });

  it.each(['desktop-v2', 'sdr-test'] as const)('%s clears local OFF under an independent healthy lease and fences replay', async skin => {
    await render(true, skin); const shared = h.resources.acquire('hardware-scope', 'independent');
    expect(overlay()).not.toBeNull(); expect(wire.count()).toBe(1); expect(h.acquire).not.toHaveBeenCalled();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(2);
    toggle(); clear(); expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
    expect(h.scope.hardwareScopeConnected).toBe(true); wire.frame(); clear();
    toggle(); expect(overlay()).toBeNull(); wire.frame(); expect(overlay()).toBeNull();
    renew(11); expect(overlay()).not.toBeNull();
    h.resources.release(shared);
  });
  it('propagates the real host frame expiry at exactly 500ms and requires renewed evidence', async () => {
    await render(); expect(overlay()).not.toBeNull();
    vi.advanceTimersByTime(499); flushSync(); expect(overlay()).not.toBeNull();
    vi.advanceTimersByTime(1); flushSync(); clear(); expect(h.scope.hardwareScopeConnected).toBe(true);
    wire.frame(); expect(overlay()).toBeNull(); renew(11); wire.frame(); expect(overlay()).not.toBeNull();
    const canvas = target.querySelector('.spectrum-area canvas');
    wire.frame(1); expect(target.querySelector('.spectrum-area canvas')).toBe(canvas);
  });
  it('retains whole stale geometry and sends frequency-only pan while resize remains denied', async () => {
    await render(); const edges = overlay()!.getAttribute('style'); renew(10, true); wire.frame();
    expect(overlay()!.getAttribute('style')).toBe(edges); expect(target.querySelector('.passband-freshness')).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    const surface = target.querySelector<HTMLElement>('.spectrum-area')!;
    surface.getBoundingClientRect = () => ({ left: 0, width: 200 } as DOMRect);
    for (const [type, x] of [['pointerdown', 100], ['pointermove', 120], ['pointerup', 120]] as const) {
      surface.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, clientX: x })); flushSync();
    }
    expect(h.frequency).toHaveBeenCalledTimes(2);
    expect(h.frequency).toHaveBeenNthCalledWith(1, 14_040_000, 0, 'drag');
    expect(h.frequency).toHaveBeenNthCalledWith(2, 14_040_000, 0);
    expect(h.width).not.toHaveBeenCalled();
    renew(11); wire.frame(); expect(overlay()!.getAttribute('style')).toBe(edges);
    expect(target.querySelector('.passband-freshness')).toBeNull();
  });
  it('keeps one geometry owner per consumer and resets display state on disposal', async () => {
    const watch = vi.spyOn(h.scope.stream('hardware').geometry, 'subscribe');
    await render(); expect(watch).toHaveBeenCalledTimes(2); expect(wire.count()).toBe(1); expect(h.listeners.size).toBe(1);
    await unmount(component!); component = null; flushSync(); await Promise.resolve();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
    expect(wire.count()).toBe(0); expect(h.scope.stream('hardware').selectedSlot).toBeNull();
    expect(h.scope.snapshotHealth('hardware')).toEqual({ demanded: false, transport: 'disconnected', frameSeen: false });
    expect(h.scope.stream('hardware').latest('radio:0')).toBeNull();
    renew(11, true); await render(); expect(overlay()).toBeNull();
    expect(watch).toHaveBeenCalledTimes(4); renew(12); wire.frame(); expect(overlay()).not.toBeNull();
  });
  it('preserves toolbar argument one and exposes a passive nullable region with demand callback', async () => {
    await render(false); expect(target.querySelector('[data-projection-probe]')?.getAttribute('data-toolbar')).toBe('function');
    expect(h.scope.stream('hardware').latest('radio:0')?.pixels).toEqual(new Uint8Array([0, 128, 255]));
    expect(region?.()?.passband.state).toBe('current');
    region!()!.setDemand(false); flushSync(); expect(region!()!.passband.state).toBe('unknown'); expect(region!()!.demanded).toBe(false);
    expect(h.frequency).not.toHaveBeenCalled(); expect(h.width).not.toHaveBeenCalled();
  });
  it('requires a post-boundary receipt even when geometry renews first', async () => {
    await render(); const shared = h.resources.acquire('hardware-scope', 'independent');
    toggle(); toggle(); renew(11); expect(overlay()).toBeNull();
    wire.frame(); expect(overlay()).not.toBeNull(); h.resources.release(shared);
  });
  it('requires a new MAIN receipt after retirement even when SUB traffic arrives before renewed MAIN readbacks', async () => {
    setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'main_sub',
      capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    const sub = structuredClone(radio.current!.main);
    deliver(state => { state.sub = sub; }); publishAuthority();
    await render(); expect(overlay()).not.toBeNull();
    deliver(state => { state.fieldStatus!['main.filterWidth'].observed = false; }); publishAuthority(); flushSync();
    expect(overlay()).toBeNull();
    wire.frame(1);
    deliver(state => { state.fieldStatus!['main.filterWidth'].observed = true; }); renew(11);
    expect(overlay()).toBeNull();
    wire.frame(0); expect(overlay()).not.toBeNull();
  });
  it.each(['single', 'ab'] as const)('uses the exact canonical %s VFO selection', async scheme => {
    fixture(scheme); const project = vi.spyOn(passband, 'projectScopePassbandDisplay'); await render(false);
    expect(project.mock.lastCall?.[1].selection).toEqual({ receiver: 'MAIN', slot: scheme === 'single' ? 'single' : 'A' });
    if (scheme === 'ab') {
      deliver((s) => { s.fieldStatus!['main.activeSlot'].observed = false; }); publishAuthority(); flushSync(); wire.frame();
      expect(project.mock.lastCall?.[1].selection).toBeNull();
      expect(region!()!.passband.state).toBe('unknown');
    }
  });
  it('preserves explicit relative-slot unknown instead of selecting MAIN/A', async () => {
    fixture('ab'); setCapabilities({ ...getCapabilities()!, vfoReadback: 'selected_unselected' });
    deliver((s) => { s.fieldStatus!['main.activeSlot'].observed = false; });
    const project = vi.spyOn(passband, 'projectScopePassbandDisplay'); await render(false);
    expect(project.mock.lastCall?.[1].selection).toBeNull(); expect(region!()!.passband.state).toBe('unknown');
  });
  it.each(['unknown receiver', 'generation mismatch'])('clears a live projection for %s', async boundary => {
    setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'ab_shared', capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    await render(); expect(overlay()).not.toBeNull();
    if (boundary === 'unknown receiver') deliver((s) => { s.fieldStatus!.active.observed = false; });
    else setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
    publishAuthority(); flushSync(); clear();
  });
  it('delivers coalesced disconnected/connected session boundaries before reactive effects can hide them', async () => {
    await render(); const shared = h.resources.acquire('hardware-scope', 'independent');
    for (const state of ['disconnected', 'connected']) {
      h.session = { state, epoch: 1 }; for (const fn of h.listeners) fn(h.session);
      publishAuthority();
    }
    flushSync(); expect(overlay()).toBeNull(); wire.frame(); expect(overlay()).toBeNull();
    renew(11); expect(overlay()).not.toBeNull(); h.resources.release(shared);
  });
  it('switches between configured managed and readonly consumers without simultaneous subscriptions or resetting floors', async () => {
    target = document.createElement('div'); document.body.append(target);
    let active = 0, maximum = 0;
    const geometry = h.scope.stream('hardware').geometry;
    const subscribe = geometry.subscribe;
    const evidence = vi.spyOn(geometry, 'subscribe').mockImplementation((handler) => {
      active++; maximum = Math.max(maximum, active); const stop = subscribe(handler);
      return () => { stop(); active--; };
    });
    const props = { regions: true, displayFrameSource: 'hardware' as const, regionContent: snippet };
    legacyComponent = createClassComponent({ component: SemanticRadioSurfaces, target, props });
    flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(region!()!.passband.state).toBe('current');
    legacyComponent.$set({ regionContent: undefined }); flushSync();
    expect(evidence).toHaveBeenCalledOnce(); expect(active).toBe(1);
    legacyComponent.$set({ regionContent: snippet }); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(evidence).toHaveBeenCalledOnce();
    expect(region!()!.passband.state).toBe('unknown'); renew(11); wire.frame();
    expect(region!()!.passband.state).toBe('current');
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1); expect(maximum).toBe(1); expect(active).toBe(1);
    const shared = h.resources.acquire('hardware-scope', 'independent');
    let frame: (() => ScopeStream | null) | undefined;
    const readonlyDisplay = createRawSnippet<[RadioViewModel, ScopeStream | null]>((_view, selected) => {
      frame = selected; return { render: () => '<output data-passive-frame></output>' };
    });
    const setDemand = region!()!.setDemand;
    legacyComponent.$set({ readonlyDisplay }); flushSync(); expect(frame?.()).toBe(h.scope.stream('hardware'));
    setDemand(false); flushSync(); expect(frame?.()).toBeNull();
    wire.frame(); expect(h.scope.hardwareScopeConnected).toBe(true); expect(frame?.()).toBeNull();
    h.resources.release(shared);
    legacyComponent.$destroy(); legacyComponent = null; flushSync(); expect(active).toBe(0);
  });
  it('keeps audio FFT layout on the shared scope channel with no hardware demand', async () => {
    setCapabilities({ ...getCapabilities()!, scope: false, capabilities: [], scopeSource: 'audio_fft', audioFftAvailable: true });
    const acquire = vi.spyOn(h.resources, 'acquire'); await render();
    expect(h.channelRequests).toEqual(['scope']);
    expect(acquire.mock.calls.filter(([resource]) => resource === 'hardware-scope')).toHaveLength(0);
        expect(acquire.mock.calls.filter(([resource, consumer]) => resource === 'audio-fft' && consumer === 'SpectrumPanel')).toHaveLength(1);
    expect(target.querySelector('.audio-source-label')?.textContent).toContain('Audio FFT');
  });

  it('never retains the strict pending width target in the real reducer tuple', async () => {
    const project = vi.spyOn(passband, 'projectScopePassbandDisplay'); await render();
    const original = overlay()!.getAttribute('style');
    const confirmed = project.mock.results.at(-1)!.value.display;
    expect(confirmed.state).toBe('current');
    pendingWidth.set('target', 3000); flushSync(); expect(overlay()!.getAttribute('style')).not.toBe(original);
    renew(10, true); wire.frame(); expect(overlay()!.getAttribute('style')).toBe(original);
    const retained = project.mock.results.at(-1)!.value.display;
    expect(retained.state).toBe('stale'); expect(retained.tuple).toBe(confirmed.tuple); expect(retained.tuple.widthHz).toBe(2400);
    pendingWidth.clear(); renew(11); wire.frame(); expect(overlay()!.getAttribute('style')).toBe(original);
    expect(h.width).not.toHaveBeenCalled();
  });

  it.each(['missing state', 'generation mismatch', 'unknown receiver'] as const)(
    'does not acquire managed hardware demand with %s', async boundary => {
      if (boundary === 'missing state') radio.current = null;
      else if (boundary === 'generation mismatch') setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
      else {
        setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'ab_shared',
          capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
        deliver(state => { state.fieldStatus!.active.observed = false; });
      }
      publishAuthority(); await mountHost();
      expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
      expect(wire.connect).not.toHaveBeenCalled();
      expect(h.scope.snapshotHealth('hardware')).toEqual({ demanded: false, transport: 'disconnected', frameSeen: false });
    },
  );

  it('recovers normal managed startup when state arrives after hardware capabilities', async () => {
    radio.current = null; await render(); clear();
    fixture(); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(overlay()).not.toBeNull();
  });

  it.each(['receiver', 'slot'] as const)('recovers startup after the canonical %s becomes known', async identity => {
    fixture('ab');
    if (identity === 'receiver') setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'ab_shared', capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    const path = identity === 'receiver' ? 'active' : 'main.activeSlot';
    deliver((s) => { s.fieldStatus![path].observed = false; }); await render(); expect(overlay()).toBeNull();
    deliver((s) => { s.fieldStatus![path].observed = true; }); publishAuthority(); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(overlay()).not.toBeNull(); expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });
  it('keeps demand stable while requalifying the transport on provider generation changes', async () => {
    const watch = vi.spyOn(h.scope.stream('hardware').geometry, 'subscribe'); await render(); expect(overlay()).not.toBeNull();
    const acquired = vi.spyOn(h.resources, 'acquire'), released = vi.spyOn(h.resources, 'release');
    deliver((s) => {
      s.providerGeneration = 2;
      for (const status of Object.values(s.fieldStatus!)) status.lastObservedMonotonic = 1;
    });
    setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
    publishAuthority(); flushSync(); await Promise.resolve(); await Promise.resolve(); wire.connected(); renew(1); wire.frame();
    expect(overlay()).toBeNull(); renew(2); wire.frame(); expect(overlay()).not.toBeNull();
    expect(acquired).not.toHaveBeenCalled(); expect(released).not.toHaveBeenCalled();
    expect(watch).toHaveBeenCalledTimes(2); expect(wire.connect).toHaveBeenCalledTimes(2);
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });

});

describe('MOR-3094 declared no-readback raw hardware scope', () => {
  it('paints raw canvas/waterfall with one socket and lease and no receiver-dependent command', async () => {
    noReadback();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); flushPaint();
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    expect(push).toHaveBeenCalledTimes(1);
    expect(wire.connect).toHaveBeenCalledTimes(1);
    expect(wire.count()).toBe(1);
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
    expect(overlay()).toBeNull();
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    // Gestures without receiver authority must emit no frequency or filter
    // command; the raw display uses one hardware-frame subscription.
    const surface = target.querySelector<HTMLElement>('.spectrum-area')!;
    surface.getBoundingClientRect = () => ({ left: 0, width: 200 } as DOMRect);
    for (const [type, x] of [['pointerdown', 100], ['pointermove', 120], ['pointerup', 120]] as const) {
      surface.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, clientX: x }));
      flushSync();
    }
    surface.dispatchEvent(new MouseEvent('dblclick', { bubbles: true })); flushSync();
    surface.dispatchEvent(new WheelEvent('wheel', { bubbles: true })); flushSync();
    expect(h.frequency).not.toHaveBeenCalled();
    expect(h.width).not.toHaveBeenCalled();
    expect(wire.count()).toBe(1);
    // The fallback ends the moment actual receiver authority arrives.
    deliver((s) => { s.active = 'MAIN'; });
    publishAuthority(); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(overlay()).not.toBeNull();
  });
  it.each([undefined, 'absolute', 'selected_unselected'] as const)(
    'keeps raw display closed when readback is %s (no explicit none declaration)', async readback => {
      // The fallback must not apply to every unknown receiver — only the
      // declared structural absence opens it. Base keeps this and the
      // 'unknown receiver' boundary above fail-closed.
      setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'main_sub',
        capabilities: [...getCapabilities()!.capabilities, 'dual_rx'],
        ...(readback === undefined ? {} : { vfoReadback: readback }) });
      deliver((s) => { s.active = null; });
      publishAuthority();
      const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
      await render();
      expect(target.querySelector('.spectrum-area canvas')).toBeNull();
      expect(push).not.toHaveBeenCalled();
      const surface = target.querySelector<HTMLElement>('.spectrum-area')!;
      if (surface) surface.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, pointerId: 1, button: 0, clientX: 100 }));
      flushSync();
      expect(h.frequency).not.toHaveBeenCalled();
      expect(h.width).not.toHaveBeenCalled();
      expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
      expect(wire.count()).toBe(0);
    });
  it('drops the lease and socket on unmount and never retains prior-session raw display', async () => {
    noReadback();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); flushPaint();
    expect(push).toHaveBeenCalledTimes(1);
    const painted = push.mock.calls.length;
    await unmount(component!); component = null; flushSync(); await Promise.resolve();
    expect(wire.disconnect).toHaveBeenCalled();
    expect(wire.count()).toBe(0);
    expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
    renew(11, true);
    // Remount without emitting a frame: a prior-session frame must not paint,
    // even when the paint queue flushes.
    await mountHost(); flushPaint();
    expect(push.mock.calls.length).toBe(painted);
    wire.connected(); wire.frame(); flushPaint();
    expect(push.mock.calls.length).toBe(painted + 1);
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });
  it('keeps one lease across provider generation change with declared no-readback', async () => {
    noReadback();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); flushPaint();
    expect(push).toHaveBeenCalledTimes(1);
    const acquired = vi.spyOn(h.resources, 'acquire'), released = vi.spyOn(h.resources, 'release');
    deliver((s) => {
      s.providerGeneration = 2;
      for (const status of Object.values(s.fieldStatus!)) status.lastObservedMonotonic = 1;
    });
    setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
    publishAuthority(); flushSync(); await Promise.resolve(); await Promise.resolve(); wire.connected(); renew(1); wire.frame(); flushPaint();
    expect(push).toHaveBeenCalledTimes(2);
    expect(acquired).not.toHaveBeenCalled(); expect(released).not.toHaveBeenCalled();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });
  it('ignores frames from the old epoch across a real disconnect and reconnect', async () => {
    noReadback();
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); flushPaint();
    expect(push).toHaveBeenCalledTimes(1);
    wire.disconnect(); flushSync();
    const painted = push.mock.calls.length;
    // A frame from the old epoch must not paint, even when the queue flushes.
    wire.frame(); flushPaint();
    expect(push.mock.calls.length).toBe(painted);
    wire.connected(); flushSync();
    // MOR-3471: the gate collapses sub-40ms-spaced deliveries as a replay
    // burst; advance to the live fps=20 cadence (50 ms) so the first frame
    // of the new epoch classifies as live.
    vi.advanceTimersByTime(50); flushSync();
    wire.frame(); flushPaint();
    expect(push.mock.calls.length).toBe(painted + 1);
  });
  it('clears the raw display and drops the lease when the hardware boundary ends', async () => {
    noReadback();
    await render();
    expect(target.querySelector('.spectrum-area canvas')).not.toBeNull();
    setCapabilities({ ...getCapabilities()!, scope: false, scopeSource: null,
      capabilities: getCapabilities()!.capabilities.filter(value => value !== 'scope') });
    flushSync(); await Promise.resolve();
    clear();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
  });
});
