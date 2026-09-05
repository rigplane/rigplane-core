import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createRawSnippet, flushSync, mount, unmount, type Snippet } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import { createClassComponent } from 'svelte/legacy';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { RadioViewModel } from '../../../semantic/radio-view-model';
import type { LcdSpectrumFrame } from '../../../skins/segmentline/lcd-display-contract';
import type { ManagedScopeRegion } from '$lib/runtime/adapters/scope-display-projection';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import * as passband from '$lib/runtime/adapters/scope-passband-display';
import { ScopeFrameHost } from '$lib/runtime/scope-frame-host';
import { PresentationResourceHost } from '$lib/runtime/resource-host';

const h = vi.hoisted(() => ({
  scope: null as unknown as ScopeController, resources: null as unknown as PresentationResourceHost<unknown>,
  tx: null as unknown as ManagedAppTxController, session: { state: 'connected', epoch: 1 },
  listeners: new Set<(next: { state: string; epoch: number }) => void>(),
  frequency: vi.fn(), width: vi.fn(), raw: vi.fn(), acquire: vi.fn(),
}));
vi.mock('$lib/runtime', async () => {
  const { radio } = await import('$lib/stores/radio.svelte');
  const { getCapabilities } = await import('$lib/stores/capabilities.svelte');
  return { get presentationResources() { return h.resources; }, runtime: {
    get state() { return radio.current; }, get caps() { return getCapabilities(); },
    get scope() { return h.scope; }, get controlSession() { return h.session; },
    subscribeControlSession: (fn: (next: typeof h.session) => void) => { h.listeners.add(fn); return () => h.listeners.delete(fn); },
    onTxAudioDied: () => () => {}, audio: { muted: true, rxEnabled: false, volume: 0 }, connectionAudio: false,
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
import { clearCapabilities, setCapabilities, getCapabilities } from '$lib/stores/capabilities.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import RadioLayout from '../../layout/RadioLayout.svelte';
import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

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
    onBinary: (fn: (buffer: ArrayBuffer) => void) => { binary.add(fn); return () => binary.delete(fn); },
    onStateChange: (fn: (value: string) => void) => { states.add(fn); return () => states.delete(fn); },
    onSessionTransition: (fn: (next: { state: string; epoch: number }) => void) => { sessions.add(fn); return () => sessions.delete(fn); },
    connected() { transition('connected'); },
    frame(receiver = 0) {
      const buffer = new ArrayBuffer(19), view = new DataView(buffer);
      view.setUint8(0, 1); view.setUint8(1, receiver); view.setUint32(3, 14_000_000, true);
      view.setUint32(7, 14_100_000, true); view.setUint16(14, 3, true); new Uint8Array(buffer, 16).set([0, 128, 255]);
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
}
function renew(marker: number, stale = false) {
  const next = JSON.parse(JSON.stringify(radio.current!)) as ServerState;
  for (const status of Object.values(next.fieldStatus!)) status.lastObservedMonotonic = marker;
  for (const path of ['main.filterWidth', 'main.ifShift']) Object.assign(next.fieldStatus![path],
    { freshness: stale ? 'stale' : 'fresh', availability: stale ? 'stale' : 'available' });
  radio.current = next; flushSync();
}
let wire: ReturnType<typeof channel>, target: HTMLDivElement;
let component: ReturnType<typeof mount> | null, legacyComponent: ReturnType<typeof createClassComponent> | null;
let tx: ManagedAppTxHarness;
let region: (() => ManagedScopeRegion | undefined) | undefined;
const snippet = createRawSnippet<[Snippet | undefined, ManagedScopeRegion | undefined]>((toolbar, managed) => {
  region = managed;
  return { render: () => '<div data-projection-probe></div>', setup: (element) => { element.setAttribute('data-toolbar', String(typeof toolbar())); } };
});
async function render(layout = true, skinId: 'desktop-v2' | 'sdr-test' = 'desktop-v2') {
  target = document.createElement('div'); document.body.append(target);
  component = layout ? mount(RadioLayout, { target, props: { skinId } })
    : mount(SemanticRadioSurfaces, { target, props: { regions: true, displayFrameSource: 'hardware', regionContent: snippet, scopeControlsInRegionContent: true } });
  flushSync(); await Promise.resolve(); flushSync(); wire.connected(); wire.frame();
}
function toggle() { target.querySelector<HTMLButtonElement>('.scope-demand-toggle')!.click(); flushSync(); }
const overlay = () => target.querySelector<HTMLElement>('.passband-overlay');
function clear() { for (const selector of ['canvas', '.freq-axis', '.passband-overlay', '.tune-line', '.passband-resize-zone']) expect(target.querySelector(selector), selector).toBeNull(); }

beforeEach(() => {
  vi.useFakeTimers(); vi.stubGlobal('requestAnimationFrame', () => 1); vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
  const noop = () => {}; const ctx = new Proxy({ measureText: () => ({ width: 1 }),
    createImageData: () => ({ data: new Uint8ClampedArray(4) }), getImageData: () => ({ data: new Uint8ClampedArray(4) }),
    createLinearGradient: () => ({ addColorStop: noop }) }, { get: (obj, key) => key in obj ? obj[key as keyof typeof obj] : noop });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as never);
  tx = new ManagedAppTxHarness(); h.tx = tx.controller; h.session = { state: 'connected', epoch: 1 }; h.listeners.clear();
  h.frequency.mockClear(); h.width.mockClear(); pendingWidth.clear(); h.acquire.mockClear(); wire = channel();
  h.scope = new ScopeController(() => wire as never, { now: () => Date.now(), setTimeout, clearTimeout });
  h.resources = new PresentationResourceHost('test');
  h.resources.configure('hardware-scope', { available: true, selected: true, driver: h.scope.hardwareScopeDriver });
  h.raw = vi.spyOn(h.scope, 'subscribeHardware'); fixture(); region = undefined; component = null; legacyComponent = null;
});
afterEach(async () => {
  if (component) await unmount(component); legacyComponent?.$destroy(); flushSync(); await h.resources.teardown();
  expect(h.listeners.size).toBe(0); expect(tx.trace()).toEqual([]);
  document.body.innerHTML = ''; resetRadioState(); clearCapabilities(); vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.useRealTimers();
});

describe('one managed scope owner through the real region and panel', () => {
  it('appends only accepted receipts, including identical bins, and seeds recovery once', async () => {
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await render(); expect(push).toHaveBeenCalledTimes(1);
    renew(10, true); expect(push).toHaveBeenCalledTimes(1);
    expect(target.querySelector('.passband-freshness')!.textContent).toContain('◷');
    renew(10); expect(push).toHaveBeenCalledTimes(1);
    radio.current!.ptt = true; flushSync(); expect(push).toHaveBeenCalledTimes(1);
    wire.frame(); expect(push).toHaveBeenCalledTimes(2);
    const shared = h.resources.acquire('hardware-scope', 'independent');
    toggle(); clear(); wire.frame(); expect(push).toHaveBeenCalledTimes(2);
    toggle(); expect(push).toHaveBeenCalledTimes(3);
    renew(11); expect(push).toHaveBeenCalledTimes(3);
    vi.advanceTimersByTime(500); flushSync(); clear();
    wire.frame(); expect(push).toHaveBeenCalledTimes(4);
    h.resources.release(shared);
  });
  it('keeps freshness text readable without effective live-region semantics', async () => {
    await render();
    for (const stale of [true, false, true]) {
      renew(10, stale);
      const cue = target.querySelector('.passband-freshness')!;
      const implicitLive = ({ status: 'polite', log: 'polite', alert: 'assertive' } as Record<string, string>)[cue.getAttribute('role') ?? ''];
      expect(cue.getAttribute('aria-live') ?? implicitLive ?? 'off').toBe('off');
      expect(cue.textContent?.includes('◷')).toBe(stale);
      if (stale) expect(cue.getAttribute('aria-label')).toBeTruthy();
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
    expect(overlay()).not.toBeNull(); expect(h.raw).not.toHaveBeenCalled(); expect(h.acquire).not.toHaveBeenCalled();
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
    wire.frame(1); clear();
  });
  it('retains whole stale geometry and sends frequency-only pan while resize remains denied', async () => {
    await render(); const edges = overlay()!.getAttribute('style'); renew(10, true); wire.frame();
    expect(overlay()!.getAttribute('style')).toBe(edges); expect(target.querySelector('.passband-freshness')!.textContent).toContain('◷');
    expect(target.querySelector('.passband-resize-zone')).toBeNull();
    const surface = target.querySelector<HTMLElement>('.spectrum-area')!;
    surface.getBoundingClientRect = () => ({ left: 0, width: 200 } as DOMRect);
    for (const [type, x] of [['pointerdown', 100], ['pointermove', 120], ['pointerup', 120]] as const) {
      surface.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, clientX: x })); flushSync();
    }
    expect(h.frequency).toHaveBeenCalledOnce(); expect(h.width).not.toHaveBeenCalled();
    renew(11); wire.frame(); expect(overlay()!.getAttribute('style')).toBe(edges);
    expect(target.querySelector('.passband-freshness')!.textContent?.trim()).toBe('');
  });
  it('keeps one host/evidence subscription and resets display state on controller lifetime disposal', async () => {
    const watch = vi.spyOn(h.scope, 'subscribeFrameEvidence'), dispose = vi.spyOn(ScopeFrameHost.prototype, 'dispose');
    const authority = vi.spyOn(h.scope, 'setFrameAuthority');
    await render(); expect(watch).toHaveBeenCalledOnce(); expect(wire.count()).toBe(1); expect(h.listeners.size).toBe(1);
    await unmount(component!); component = null; flushSync(); await Promise.resolve();
    expect(dispose).toHaveBeenCalledOnce(); expect(h.resources.snapshot('hardware-scope').demand).toBe(0);
    expect(wire.count()).toBe(0); expect(authority.mock.calls.filter(([value]) => value === null)).toHaveLength(1);
    renew(11, true); await render(); expect(overlay()).toBeNull();
    expect(watch).toHaveBeenCalledTimes(2); renew(12); wire.frame(); expect(overlay()).not.toBeNull();
  });
  it('preserves toolbar argument one and exposes a passive nullable region with demand callback', async () => {
    await render(false); expect(target.querySelector('[data-projection-probe]')?.getAttribute('data-toolbar')).toBe('function');
    expect(region?.()?.projection?.frame.normalizedBins).toEqual([0, 128 / 255, 1]);
    expect(region?.()?.projection?.passband.state).toBe('current');
    region!()!.setDemand(false); flushSync(); expect(region!()!.projection).toBeNull(); expect(region!()!.demanded).toBe(false);
    expect(h.frequency).not.toHaveBeenCalled(); expect(h.width).not.toHaveBeenCalled();
  });
  it('requires a post-boundary receipt even when geometry renews first', async () => {
    await render(); const shared = h.resources.acquire('hardware-scope', 'independent');
    toggle(); toggle(); renew(11); expect(overlay()).toBeNull();
    wire.frame(); expect(overlay()).not.toBeNull(); h.resources.release(shared);
  });
  it.each(['single', 'ab'] as const)('uses the exact canonical %s VFO selection', async scheme => {
    fixture(scheme); const project = vi.spyOn(passband, 'projectScopePassbandDisplay'); await render(false);
    expect(project.mock.lastCall?.[1].selection).toEqual({ receiver: 'MAIN', slot: scheme === 'single' ? 'single' : 'A' });
    if (scheme === 'ab') {
      radio.current!.fieldStatus!['main.activeSlot'].observed = false; flushSync(); wire.frame();
      expect(project.mock.lastCall?.[1].selection).toBeNull();
      expect(region!()!.projection?.passband.state).toBe('unknown');
    }
  });
  it('preserves explicit relative-slot unknown instead of selecting MAIN/A', async () => {
    fixture('ab'); setCapabilities({ ...getCapabilities()!, vfoReadback: 'selected_unselected' });
    radio.current!.fieldStatus!['main.activeSlot'].observed = false;
    const project = vi.spyOn(passband, 'projectScopePassbandDisplay'); await render(false);
    expect(project.mock.lastCall?.[1].selection).toBeNull(); expect(region!()!.projection?.passband.state).toBe('unknown');
  });
  it.each(['unknown receiver', 'generation mismatch'])('clears a live projection for %s', async boundary => {
    setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'ab_shared', capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    await render(); expect(overlay()).not.toBeNull();
    if (boundary === 'unknown receiver') radio.current!.fieldStatus!.active.observed = false;
    else setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
    flushSync(); clear();
  });
  it('delivers coalesced disconnected/connected session boundaries before reactive effects can hide them', async () => {
    await render(); const shared = h.resources.acquire('hardware-scope', 'independent');
    for (const state of ['disconnected', 'connected']) {
      h.session = { state, epoch: 1 }; for (const fn of h.listeners) fn(h.session);
    }
    flushSync(); expect(overlay()).toBeNull(); wire.frame(); expect(overlay()).toBeNull();
    renew(11); expect(overlay()).not.toBeNull(); h.resources.release(shared);
  });
  it('switches between configured managed and readonly consumers without simultaneous subscriptions or resetting floors', async () => {
    target = document.createElement('div'); document.body.append(target);
    let active = 0, maximum = 0;
    const managedSubscribe = ScopeFrameHost.prototype.subscribePresentation, readonlySubscribe = ScopeFrameHost.prototype.subscribe;
    const managedWatch = vi.spyOn(ScopeFrameHost.prototype, 'subscribePresentation').mockImplementation(function (this: ScopeFrameHost, handler) {
      active++; maximum = Math.max(maximum, active); const stop = managedSubscribe.call(this, handler);
      return () => { stop(); active--; };
    });
    const readonlyWatch = vi.spyOn(ScopeFrameHost.prototype, 'subscribe').mockImplementation(function (this: ScopeFrameHost, handler) {
      active++; maximum = Math.max(maximum, active); const stop = readonlySubscribe.call(this, handler);
      return () => { stop(); active--; };
    });
    const evidence = vi.spyOn(h.scope, 'subscribeFrameEvidence');
    const props = { regions: true, displayFrameSource: 'hardware' as const, regionContent: snippet };
    legacyComponent = createClassComponent({ component: SemanticRadioSurfaces, target, props });
    flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(region!()!.projection?.passband.state).toBe('current');
    legacyComponent.$set({ regionContent: undefined }); flushSync();
    expect(managedWatch).toHaveBeenCalledOnce(); expect(readonlyWatch).toHaveBeenCalledOnce();
    legacyComponent.$set({ regionContent: snippet }); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(managedWatch).toHaveBeenCalledTimes(2); expect(evidence).toHaveBeenCalledOnce();
    expect(region!()!.projection?.passband.state).toBe('unknown'); renew(11); wire.frame();
    expect(region!()!.projection?.passband.state).toBe('current');
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1); expect(maximum).toBe(1); expect(active).toBe(1);
    const shared = h.resources.acquire('hardware-scope', 'independent');
    let frame: (() => LcdSpectrumFrame | undefined) | undefined;
    const readonlyDisplay = createRawSnippet<[RadioViewModel, LcdSpectrumFrame?]>((_view, selected) => {
      frame = selected; return { render: () => '<output data-passive-frame></output>' };
    });
    const setDemand = region!()!.setDemand;
    legacyComponent.$set({ readonlyDisplay }); flushSync(); expect(frame?.()).toBeDefined();
    setDemand(false); flushSync(); expect(frame?.()).toBeUndefined();
    wire.frame(); expect(h.scope.hardwareScopeConnected).toBe(true); expect(frame?.()).toBeUndefined();
    h.resources.release(shared);
    legacyComponent.$destroy(); legacyComponent = null; flushSync(); expect(active).toBe(0);
  });
  it('keeps audio FFT layout on its legacy acquisition path with no hardware host', async () => {
    setCapabilities({ ...getCapabilities()!, scope: false, capabilities: [], scopeSource: 'audio_fft', audioFftAvailable: true });
    const watch = vi.spyOn(h.scope, 'subscribeFrameEvidence'), acquire = vi.spyOn(h.resources, 'acquire'); await render();
    expect(watch).not.toHaveBeenCalled(); expect(h.raw).not.toHaveBeenCalled();
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

  it('recovers normal managed startup when state arrives after hardware capabilities', async () => {
    radio.current = null; await render(); clear();
    fixture(); flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(overlay()).not.toBeNull();
  });

  it.each(['receiver', 'slot'] as const)('recovers startup after the canonical %s becomes known', async identity => {
    fixture('ab');
    if (identity === 'receiver') setCapabilities({ ...getCapabilities()!, receivers: 2, vfoScheme: 'ab_shared', capabilities: [...getCapabilities()!.capabilities, 'dual_rx'] });
    const path = identity === 'receiver' ? 'active' : 'main.activeSlot';
    radio.current!.fieldStatus![path].observed = false; await render(); expect(overlay()).toBeNull();
    radio.current!.fieldStatus![path].observed = true; flushSync(); await Promise.resolve(); wire.connected(); wire.frame();
    expect(overlay()).not.toBeNull(); expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });
  it('reacquires only its own resource binding when provider generation changes', async () => {
    const watch = vi.spyOn(h.scope, 'subscribeFrameEvidence'); await render(); expect(overlay()).not.toBeNull();
    const acquired = vi.spyOn(h.resources, 'acquire'), released = vi.spyOn(h.resources, 'release');
    radio.current!.providerGeneration = 2;
    for (const status of Object.values(radio.current!.fieldStatus!)) status.lastObservedMonotonic = 1;
    setCapabilities({ ...getCapabilities()!, providerGeneration: 2 });
    flushSync(); await Promise.resolve(); await Promise.resolve(); wire.connected(); renew(1); wire.frame();
    expect(overlay()).toBeNull(); renew(2); wire.frame(); expect(overlay()).not.toBeNull();
    expect(acquired).toHaveBeenCalledOnce(); expect(released).toHaveBeenCalledOnce(); expect(watch).toHaveBeenCalledOnce();
    expect(h.resources.snapshot('hardware-scope').demand).toBe(1);
  });

});
