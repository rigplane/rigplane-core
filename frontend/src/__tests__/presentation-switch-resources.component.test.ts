/**
 * MOR-1086 — presentation switching preserves control, RX-audio and
 * scope/audio-FFT resource identity.
 *
 * Evidence-only (no production change). The MOR-1060 suite proves the App
 * swap-bridge ORDERING (acquire before commit, release after) against a
 * FAKE `presentationResources`; the MOR-1058/1122 suites prove the demand
 * model against the real host but never switch a presentation. Nothing joined
 * the two, so "a swap does not reconnect anything" was still an argument
 * rather than a measurement.
 *
 * This file joins them: the REAL `runtime` / `presentationResources` /
 * `ResourceDemand` / `ScopeController` drive a REAL mounted `App.svelte`,
 * and only the bottom boundary is faked — the control transport, the HTTP
 * client, the audio manager and the scope WS channels — so every claim below
 * is an open/close/connect counter, not a mock call on the seam under test.
 *
 * The presentation subtree is `PresentationResourceStub`, which acquires a
 * skin's real resource plan on mount and releases it on destroy, exactly as
 * the production viewer panels do.
 *
 * Pinned here:
 *   1. a desktop → LCD → SDR → mobile → desktop sweep opens the control
 *      socket once, fetches capabilities once, subscribes once, and never
 *      restarts RX audio — with the same AudioManager instance throughout;
 *   2. between two presentations that BOTH demand a resource, the channel is
 *      never disconnected and the driver handle is the identical object;
 *   3. switching to a presentation that genuinely has no viewer for a
 *      resource releases it exactly once (correct — MOR-971 "no viewer means
 *      no open"), and returning opens exactly one new session; the resource
 *      both presentations DO demand never bounces on those same hops;
 *   4. a rapid A → B → A whose middle leg never commits touches nothing;
 *   5. final App teardown stops each live resource exactly once.
 */
import { readFileSync } from 'node:fs';
import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, tick, unmount } from 'svelte';
import type { AppResource } from '../lib/runtime/resource-demand';
import type { SkinId } from '../skins/registry';

type Pending = { id: SkinId; resolve: (component: unknown) => void };
type FakeChannel = ReturnType<typeof makeChannel>;

const h = vi.hoisted(() => ({
  pending: [] as Pending[],
  loadSkin: vi.fn(),
  initBattery: vi.fn(),
  batteryCleanup: vi.fn(),
  provide: vi.fn(),
  txHost: undefined as { refreshAuthority: () => void; dispose: () => void } | undefined,
  notifyCaps: () => {},
}));

// ── Bottom boundary only: transport, HTTP, audio, scope channels ──
vi.mock('$lib/transport/http-client', () => ({
  fetchCapabilities: vi.fn(),
  startPolling: vi.fn(),
  setPollingMultiplier: vi.fn(),
}));
vi.mock('$lib/transport/ws-client', () => ({
  connect: vi.fn(),
  sendRaw: vi.fn(),
  sendCommand: vi.fn(),
  disconnect: vi.fn(),
  disconnectAll: vi.fn(),
  reconnectAll: vi.fn(),
  isConnected: vi.fn(() => false),
  onMessage: vi.fn(() => () => {}),
  addMessageHandler: vi.fn(() => () => {}),
  getChannel: vi.fn(),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    rxEnabled: false,
    startRx: vi.fn(),
    stopRx: vi.fn(),
    startTx: vi.fn(),
    stopTx: vi.fn(),
    setRxVolume: vi.fn(),
    destroy: vi.fn(),
  },
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null },
  getRadioState: vi.fn(() => null),
  setRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  resetRadioState: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => { update = notify; return () => {}; });
  h.notifyCaps = () => update();
  return {
    getCapabilities: vi.fn(() => { subscribe(); return null; }),
    setCapabilities: vi.fn(),
    hasSpectrum: vi.fn(() => false),
    hasAnyScope: vi.fn(() => false),
  };
});
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => 'disconnected'),
  isConnected: vi.fn(() => false),
  getHttpConnected: vi.fn(() => false),
  getWsConnected: vi.fn(() => false),
  isAudioConnected: vi.fn(() => false),
  isStale: vi.fn(() => false),
  isReconnecting: vi.fn(() => false),
  getRadioStatus: vi.fn(() => ''),
  getRadioPowerOn: vi.fn(() => null),
  setHttpConnected: vi.fn(),
  setWsConnected: vi.fn(),
  setRadioStatus: vi.fn(),
  setReconnecting: vi.fn(),
  setRadioPowerOn: vi.fn(),
  setRigConnected: vi.fn(),
  setRadioReady: vi.fn(),
  setControlConnected: vi.fn(),
  markStateUpdated: vi.fn(),
  markScopeFrame: vi.fn(),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({})),
  setVolume: vi.fn(),
  setMuted: vi.fn(),
  toggleMute: vi.fn(),
}));
vi.mock('../lib/runtime/adapters/mod-input-auto.svelte', () => ({
  clearLegacyPendingModInputRestore: vi.fn(),
}));

// ── App-level substitutions (everything else stays real) ──
vi.mock('$lib/stores/layout.svelte', () => ({
  getLayoutMode: () => 'standard',
  normalizeLayoutMode: (value: unknown) => value ?? 'auto',
}));
vi.mock('../skins/registry', () => ({
  resolveSkinId: () => widthToSkin(),
  loadSkin: h.loadSkin,
  presentationResourcePlan: (id: SkinId) => SKIN_PLAN[id] ?? [],
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  // TX controller identity across a switch has its own real-stack proof
  // (presentation-switch-tx.component.test.ts). Inert here.
  provideAppTxControllerHost: h.provide,
}));
vi.mock('../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));
vi.mock('../AppGlobalHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});

import App from '../App.svelte';
import PresentationResourceStub from './PresentationResourceStub.svelte';
import { fetchCapabilities, startPolling } from '$lib/transport/http-client';
import { connect, getChannel, sendRaw } from '$lib/transport/ws-client';
import { audioManager } from '$lib/audio/audio-manager';
import { presentationResources, runtime } from '../lib/runtime/frontend-runtime';

/**
 * The real per-skin resource plan. Mirrored rather than imported because
 * `skins/registry` is mocked here (its `loadSkin` must be controllable);
 * `assertPlanMirrorsProduction()` below fails if production ever diverges,
 * and `skins/__tests__/registry.test.ts` owns the plan's own pin.
 */
const SKIN_PLAN: Record<SkinId, readonly AppResource[]> = {
  'desktop-v2': ['hardware-scope', 'audio-fft'],
  'lcd-cockpit': ['audio-fft'],
  'lcd-scope': ['audio-fft'],
  'mobile': ['hardware-scope'],
  'sdr-test': ['hardware-scope', 'audio-fft'],
};

const WIDTH_FOR: Record<SkinId, number> = {
  'desktop-v2': 1200,
  'lcd-cockpit': 1000,
  'lcd-scope': 900,
  'sdr-test': 1400,
  'mobile': 390,
};
function widthToSkin(): SkinId {
  const width = window.innerWidth;
  const found = (Object.keys(WIDTH_FOR) as SkinId[]).find((id) => WIDTH_FOR[id] === width);
  return found ?? 'desktop-v2';
}

const caps = {
  modes: ['USB', 'LSB'],
  receivers: 1,
  vfoScheme: 'ab',
  scope: true,
  audio: true,
  audioFftAvailable: true,
  capabilities: ['audio', 'scope'],
  scopeSource: 'hardware',
} as never;

/** A scope WS channel whose connect/disconnect are the open/close counters. */
function makeChannel(name: string) {
  const binary = new Set<(data: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  let state = 'disconnected';
  return {
    name,
    connect: vi.fn(() => { state = 'connected'; for (const s of states) s('connected'); }),
    disconnect: vi.fn(() => { state = 'disconnected'; for (const s of states) s('disconnected'); }),
    get state() { return state; },
    onBinary: vi.fn((handler: (data: ArrayBuffer) => void) => {
      binary.add(handler);
      return () => binary.delete(handler);
    }),
    onStateChange: vi.fn((handler: (state: string) => void) => {
      states.add(handler);
      return () => states.delete(handler);
    }),
    frame() {
      const data = new ArrayBuffer(20), view = new DataView(data);
      view.setUint8(0, 1); view.setUint32(3, 14_100_000, true);
      view.setUint32(7, 14_200_000, true); view.setUint16(14, 4, true);
      for (const handler of binary) handler(data);
    },
  };
}

let channels: Record<string, FakeChannel>;

/** connect/disconnect counts for a named scope channel. */
const opens = (name: string) => channels[name].connect.mock.calls.length;
const closes = (name: string) => channels[name].disconnect.mock.calls.length;

/**
 * The App-session host and the runtime latch `ended` on teardown and expose
 * no reset (correct for production: a torn-down session must stay closed).
 * Each test needs a fresh session, so the private sentinels are cleared here
 * — the same cast-and-reset idiom `frontend-runtime.test.ts::freshRuntime()`
 * uses. The registered DRIVERS are deliberately left in place: they are
 * installed by the runtime constructor, which only runs on module import.
 */
function resetAppSession(): void {
  const rt = runtime as unknown as Record<string, unknown>;
  rt._bootstrapCleanup = null;
  rt._bootstrapInFlight = null;
  rt._rxAudioLease = null;
  rt._ended = false;
  (rt._dxSubscribers as Map<unknown, unknown>).clear();
  rt._dxControlUnsubscribe = null;
  const host = presentationResources as unknown as {
    demand: Record<string, unknown>;
    bindings: unknown[];
    listeners: Set<unknown>;
    inFlight: Set<unknown>;
    final?: Promise<void>;
  };
  host.final = undefined;
  host.bindings.length = 0;
  host.listeners.clear();
  host.inFlight.clear();
  const demand = host.demand as unknown as {
    states: Map<unknown, unknown>; leases: Set<unknown>;
    operations: unknown[]; ended: boolean;
  };
  demand.ended = false;
  demand.states.clear();
  demand.leases.clear();
  demand.operations.length = 0;
}

/** Drain past App's post-commit `await tick()` and the host's async drivers. */
async function settle(): Promise<void> {
  for (let i = 0; i < 6; i++) await Promise.resolve();
  await tick();
  flushSync();
  await Promise.resolve();
  flushSync();
}

function completeLoad(id: SkinId): void {
  const index = h.pending.findIndex((entry) => entry.id === id);
  if (index < 0) throw new Error(`no pending load for ${id}`);
  h.pending.splice(index, 1)[0].resolve(presentationFor(id));
}

/**
 * A distinct component identity per skin, each delegating to the resource
 * stub with that skin's real plan — so a switch is a genuine destroy/recreate
 * of a subtree that really holds leases.
 */
type ClientComponent = (anchor: unknown, props: Record<string, unknown>) => void;
const componentFor = new Map<SkinId, ClientComponent>();
function presentationFor(id: SkinId): ClientComponent {
  let component = componentFor.get(id);
  if (!component) {
    component = (anchor, props) => (PresentationResourceStub as unknown as ClientComponent)(
      anchor, { ...props, skinId: id, resources: SKIN_PLAN[id] },
    );
    componentFor.set(id, component);
  }
  return component;
}

function requestSkin(id: SkinId): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: WIDTH_FOR[id] });
  window.dispatchEvent(new Event('resize'));
  h.notifyCaps();   // invalidate the tracked caps read inside App's `skinId`
  flushSync();
}

async function switchTo(id: SkinId): Promise<void> {
  requestSkin(id);
  await settle();
  completeLoad(id);
  await settle();
}

const mountedSkin = () =>
  document.querySelector('.presentation-stub')?.getAttribute('data-skin') ?? null;
const mountedCount = () => document.querySelectorAll('.presentation-stub').length;

let mountedComponent: object | null = null;

/** Mount App, let bootstrap run, commit the desktop presentation. */
async function mountApp(): Promise<void> {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mountedComponent = mount(App, { target });
  flushSync();
  await settle();
  completeLoad('desktop-v2');
  await vi.waitFor(async () => {
    await settle();
    if (mountedSkin() !== 'desktop-v2') throw new Error('presentation not committed yet');
  });
}

describe('MOR-1086 — resource identity across a presentation switch', () => {
  beforeEach(async () => {
    vi.clearAllMocks();
    h.pending.length = 0;
    document.body.innerHTML = '';
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    channels = { scope: makeChannel('scope'), 'audio-scope': makeChannel('audio-scope') };
    vi.mocked(getChannel).mockImplementation((name: string) => channels[name] as never);
    vi.mocked(fetchCapabilities).mockResolvedValue(caps);
    vi.mocked(startPolling).mockReturnValue(vi.fn());
    h.loadSkin.mockImplementation(
      (id: SkinId) => new Promise((resolve) => { h.pending.push({ id, resolve }); }),
    );
    h.initBattery.mockResolvedValue(h.batteryCleanup);
    h.provide.mockImplementation(() => {
      h.txHost = { refreshAuthority: vi.fn(), dispose: vi.fn() };
      return { ...h.txHost, release: vi.fn() };
    });
    resetAppSession();
  });

  afterEach(async () => {
    if (mountedComponent) {
      try { unmount(mountedComponent); } catch { /* already unmounted */ }
      mountedComponent = null;
    }
    await settle();
  });

  it('mirrors the production per-skin resource plan', () => {
    // If production ever diverges from the table this file switches on, the
    // whole matrix below is measuring the wrong thing.
    const source = readFileSync('src/skins/registry.ts', 'utf8');
    const table = source.slice(source.indexOf('const SKIN_RESOURCE_PLAN'));
    for (const [id, plan] of Object.entries(SKIN_PLAN)) {
      const line = table.match(new RegExp(`'${id}':\\s*\\[([^\\]]*)\\]`));
      expect(line, `no plan entry for ${id}`).not.toBeNull();
      const declared = [...line![1].matchAll(/'([^']+)'/g)].map((m) => m[1]).sort();
      expect(declared).toEqual([...plan].sort());
    }
  });

  // MUTATION KILLED: routing a presentation switch through anything that
  // re-runs bootstrap (re-mounting App per skin, tearing the runtime down on
  // swap). Each hop would re-open the control socket, re-fetch capabilities,
  // re-send the events subscription and restart RX audio.
  it('sweeps every presentation family without touching the control socket or RX audio', async () => {
    await mountApp();
    runtime.setRxLive(true);          // the operator is listening
    await settle();

    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    const audioHandle = presentationResources.snapshot('rx-audio').activeHandle;
    expect(audioHandle).toBe(audioManager);

    // The App-global overlay host is a sibling of the presentation, so it
    // must be the same DOM node at the end of the whole sweep.
    const globalHost = document.querySelector('.spectrum-panel-stub');
    expect(globalHost).not.toBeNull();

    // Every presentation family: desktop → LCD (both variants) → SDR →
    // mobile → desktop.
    for (const id of ['lcd-cockpit', 'lcd-scope', 'sdr-test', 'mobile', 'desktop-v2'] as const) {
      await switchTo(id);
      expect(mountedSkin()).toBe(id);
      expect(mountedCount()).toBe(1);
    }
    expect(document.querySelector('.spectrum-panel-stub')).toBe(globalHost);

    // Control plane: opened once, described once, subscribed once.
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(connect).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);
    expect(startPolling).toHaveBeenCalledTimes(1);
    // RX audio: never restarted, never stopped, same AudioManager session.
    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(audioManager.stopRx).not.toHaveBeenCalled();
    expect(presentationResources.snapshot('rx-audio').activeHandle).toBe(audioHandle);
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 1, health: 'streaming',
    });
  });

  // MUTATION KILLED: releasing the App swap bridge before the commit (or
  // acquiring it after). The outgoing subtree's release would take demand to
  // zero between the two presentations and the driver would close and re-open
  // the channel — a reconnect caused solely by presentation replacement.
  it('never closes a live channel both presentations demand', async () => {
    await mountApp();
    await settle();
    expect(opens('scope')).toBe(1);
    expect(opens('audio-scope')).toBe(1);
    const scopeHandle = presentationResources.snapshot('hardware-scope').activeHandle;
    const fftHandle = presentationResources.snapshot('audio-fft').activeHandle;
    expect(scopeHandle).toBeDefined();
    expect(fftHandle).toBeDefined();

    // desktop-v2 and sdr-test plan the SAME two resources.
    await switchTo('sdr-test');
    expect(mountedSkin()).toBe('sdr-test');

    expect(closes('scope')).toBe(0);
    expect(closes('audio-scope')).toBe(0);
    expect(opens('scope')).toBe(1);
    expect(opens('audio-scope')).toBe(1);
    // The exact same driver handles — not merely "a stream is running".
    expect(presentationResources.snapshot('hardware-scope').activeHandle).toBe(scopeHandle);
    expect(presentationResources.snapshot('audio-fft').activeHandle).toBe(fftHandle);
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      demand: 1, health: 'streaming',
    });
  });

  // PINS the honest boundary of "zero reconnect caused solely by
  // presentation replacement": a resource the incoming presentation has no
  // viewer for is not preserved — it is released exactly once, because
  // MOR-971/MOR-1057 require that no viewer means no open. What must NOT
  // happen on those same hops is a bounce of the resource both presentations
  // DO demand.
  it('releases a resource the incoming presentation has no viewer for, exactly once', async () => {
    await mountApp();
    await settle();
    const fftHandle = presentationResources.snapshot('audio-fft').activeHandle;

    // LCD has an audio-FFT surface but no hardware scope.
    await switchTo('lcd-cockpit');

    expect(closes('scope')).toBe(1);
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      demand: 0, health: 'inactive',
    });
    // ...while the audio FFT, demanded by both, never bounced.
    expect(closes('audio-scope')).toBe(0);
    expect(opens('audio-scope')).toBe(1);
    expect(presentationResources.snapshot('audio-fft').activeHandle).toBe(fftHandle);

    // Returning opens exactly one new hardware-scope session — one open per
    // genuine viewer change, never a close/open pair per swap.
    await switchTo('desktop-v2');
    expect(opens('scope')).toBe(2);
    expect(closes('scope')).toBe(1);
    expect(closes('audio-scope')).toBe(0);
    expect(presentationResources.snapshot('audio-fft').activeHandle).toBe(fftHandle);
  });

  // MUTATION KILLED: dropping the loader-generation gate. The superseded
  // resolution would commit, destroying and recreating the live subtree and
  // driving a close/open pair on a stream nobody asked to interrupt.
  it('leaves every resource untouched when the middle leg of A → B → A never commits', async () => {
    await mountApp();
    runtime.setRxLive(true);
    await settle();
    const scopeHandle = presentationResources.snapshot('hardware-scope').activeHandle;
    const fftHandle = presentationResources.snapshot('audio-fft').activeHandle;

    requestSkin('lcd-cockpit');   // request 2 — in flight
    await settle();
    requestSkin('desktop-v2');    // request 3 — supersedes it
    await settle();

    completeLoad('lcd-cockpit');  // stale
    await settle();
    completeLoad('desktop-v2');   // current, and already on screen
    await settle();

    expect(mountedSkin()).toBe('desktop-v2');
    expect(mountedCount()).toBe(1);
    expect(opens('scope')).toBe(1);
    expect(closes('scope')).toBe(0);
    expect(opens('audio-scope')).toBe(1);
    expect(closes('audio-scope')).toBe(0);
    expect(presentationResources.snapshot('hardware-scope').activeHandle).toBe(scopeHandle);
    expect(presentationResources.snapshot('audio-fft').activeHandle).toBe(fftHandle);
    expect(audioManager.startRx).toHaveBeenCalledTimes(1);
    expect(audioManager.stopRx).not.toHaveBeenCalled();
    // The control plane never noticed the churn either.
    expect(connect).toHaveBeenCalledTimes(1);
    expect(fetchCapabilities).toHaveBeenCalledTimes(1);
    expect(sendRaw).toHaveBeenCalledTimes(1);
  });

  // MUTATION KILLED: releasing per presentation replacement rather than once
  // at App-session teardown — the sweep above would already have closed the
  // channels, and the teardown counts here would be greater than one.
  it('stops each live resource exactly once at final App teardown', async () => {
    await mountApp();
    runtime.setRxLive(true);
    await settle();
    for (const id of ['sdr-test', 'desktop-v2'] as const) await switchTo(id);
    expect(closes('scope')).toBe(0);
    expect(closes('audio-scope')).toBe(0);

    unmount(mountedComponent!);
    mountedComponent = null;
    await settle();

    expect(closes('scope')).toBe(1);
    expect(closes('audio-scope')).toBe(1);
    expect(audioManager.stopRx).toHaveBeenCalledTimes(1);
    expect(presentationResources.snapshot('hardware-scope')).toMatchObject({
      demand: 0, health: 'inactive', activeHandle: undefined,
    });
    expect(presentationResources.snapshot('rx-audio')).toMatchObject({
      demand: 0, health: 'inactive', activeHandle: undefined,
    });
  });
});
