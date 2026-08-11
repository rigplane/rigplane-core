/**
 * MOR-1086 — presentation switching preserves TX authority identity.
 *
 * Evidence-only (no production change). The MOR-1060 suite proves that a
 * switch does not RE-PROVIDE the TX host (`provideAppTxControllerHost` is
 * called once) against a MOCKED host. That leaves the operator-facing
 * question unanswered: with a key actually DOWN, does replacing the
 * presentation de-key the radio, lose the lease, or hand the incoming
 * subtree a different controller?
 *
 * This file answers it through the real stack — real `App.svelte`, real
 * `provideAppTxControllerHost`/`TxController`, real `WsChannel` singleton
 * driven only at the socket boundary by the shared `MockWebSocket` — so
 * "no de-key" is a wire fact (`ptt_off` frame count), not a mock call count.
 *
 * The harness is deliberately the one already proven by
 * `tx-controller/__tests__/integration-page-lifecycle.isolated.test.ts` (MOR-1089 U6);
 * the only additions are a controllable lazy loader (so the presentation can
 * actually be switched, including mid-flight) and `TxControllerProbe` served
 * per skin id, so the controller the INCOMING subtree pulls out of context
 * can be compared by object identity with the one the outgoing subtree held.
 *
 * Pinned here:
 *   1. a switch under a confirmed-on key keeps phase/risk/guard and sends no
 *      `ptt_off` and no second `ptt_on` — the swap neither de-keys nor replays;
 *   2. the incoming subtree receives the IDENTICAL `AppTxController` object;
 *   3. rapid A → B → A under a key is the same, with one live subtree;
 *   4. a stale resolution landing after a switch-during-TX cannot mount and
 *      cannot touch the TX source;
 *   5. teardown under an owned key still releases exactly once.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import type { SkinId } from '../skins/registry';

type Pending = { id: SkinId; resolve: (component: unknown) => void };

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
  registerBarrier: vi.fn(),
  bootstrap: vi.fn(),
  bootstrapCleanup: vi.fn(),
  initBattery: vi.fn(),
  batteryCleanup: vi.fn(),
  notifyRuntime: () => {},
  runtimeState: { stateRevision: 1 } as Record<string, unknown> | null,
  runtimeCaps: { tx: true } as Record<string, unknown> | null,
  pending: [] as Pending[],
  loadSkin: vi.fn(),
  provideCalls: 0,
}));

// ── Same facts seam as the U6 integration harness ──
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.radio,
  setRadioState: () => {},
  patchActiveReceiver: () => {},
  patchRadioState: () => {},
  resetRadioState: () => {},
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => h.caps,
  hasAnyScope: () => false,
  capabilitiesMatchGeneration: () => true,
  clearCapabilities: () => {},
  setCapabilities: () => true,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    startTx: h.start,
    stopLocalAudio: h.stop,
    restoreModAfterConfirmedOff: h.restore,
  }),
}));
vi.mock('../lib/local-extensions/LocalExtensionsHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../AppGlobalHost.svelte', async () => {
  const stub = await import('../components-v2/layout/__tests__/SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('$lib/stores/layout.svelte', () => ({ getLayoutMode: () => 'standard' }));
// The presentation under test is the loader result. `resolveSkinId` is driven
// off the viewport width (App's own `isMobile`/resize reactivity), and
// `loadSkin` hands back a deferred promise per call so a test can decide when
// — and whether — each resolution lands.
vi.mock('../skins/registry', () => ({
  resolveSkinId: () => widthToSkin(),
  loadSkin: h.loadSkin,
  presentationResourcePlan: () => [],
}));
vi.mock('../lib/utils/battery', () => ({ initBatteryMonitor: h.initBattery }));
vi.mock('../lib/media/media-session', () => ({
  initMediaSession: vi.fn(),
  destroyMediaSession: vi.fn(),
}));
vi.mock('../lib/runtime/frontend-runtime', async () => {
  const { createSubscriber } = await import('svelte/reactivity');
  let update = () => {};
  const subscribe = createSubscriber((notify) => {
    update = notify;
    return () => {};
  });
  h.notifyRuntime = () => update();
  return {
    runtime: {
      get state() { subscribe(); return h.runtimeState; },
      get caps() { subscribe(); return h.runtimeCaps; },
      bootstrap: h.bootstrap,
    },
    // Resource-demand continuity has its own dedicated proof
    // (presentation-switch-resources.component.test.ts, against the REAL
    // host). Inert here: this file is about TX authority.
    presentationResources: {
      snapshot: () => ({ demand: 0 }),
      acquire: () => ({}),
      release: () => true,
    },
  };
});
vi.mock('$lib/runtime/system-controller', () => ({
  systemController: { registerPreDisconnectBarrier: h.registerBarrier },
}));
vi.mock('$lib/i18n', () => ({ t: (key: string) => key }));

import App from '../App.svelte';
import * as wsClient from '$lib/transport/ws-client';
import * as connection from '$lib/stores/connection.svelte';
import {
  capturedController,
  resetCapturedController,
} from '../lib/runtime/tx-controller/__tests__/support/TxControllerProbe.svelte';
import TxControllerProbe from '../lib/runtime/tx-controller/__tests__/support/TxControllerProbe.svelte';
import type { AppTxController } from '../lib/runtime/tx-controller/app-host';

/** Viewport width → skin id. Reuses App's own resize reactivity as the knob. */
const WIDTH_FOR: Record<string, number> = {
  'desktop-v2': 1200,
  'lcd-cockpit': 1000,
  'lcd-scope': 900,
  'sdr-test': 1400,
  'mobile': 390,
};
function widthToSkin(): SkinId {
  const width = window.innerWidth;
  const found = Object.entries(WIDTH_FOR).find(([, value]) => value === width);
  return (found?.[0] ?? 'desktop-v2') as SkinId;
}

/**
 * A distinct component identity per skin id, each delegating to the real
 * `TxControllerProbe` — so every switch is a genuine destroy/recreate of the
 * presentation subtree, and every incoming subtree calls the real
 * `getAppTxController()` from inside the real mounted App tree.
 */
type ClientComponent = (anchor: unknown, props: Record<string, unknown>) => void;
const probeFor = new Map<string, ClientComponent>();
function probeComponent(id: string): ClientComponent {
  let probe = probeFor.get(id);
  if (!probe) {
    probe = (anchor, props) => (TxControllerProbe as unknown as ClientComponent)(anchor, props);
    probeFor.set(id, probe);
  }
  return probe;
}

const field = (at: number) => ({
  observed: true,
  freshness: 'fresh' as const,
  availability: 'available' as const,
  lastObservedMonotonic: at,
  source: { source: 'poll_response' },
});
const txTarget = { receiver: 'SUB' as const, slot: 'B' as const, frequencyHz: 150 };

function resetFacts(): void {
  h.radio = {
    revision: 1, ptt: false, active: 'SUB', txTarget: { status: 'known', ...txTarget },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { ptt: field(1), txTarget: field(1), data1ModInput: field(1) },
  };
  h.caps = {
    tx: true, audioTx: true, capabilities: ['tx', 'mod_input_routing'],
    vfoScheme: 'main_sub', audioTxRequiredModInputSource: 5, txBands: [{ start: 100, end: 200 }],
  } as Capabilities;
}

/** Drain past App's post-commit `await tick()`. */
async function settle(): Promise<void> {
  for (let i = 0; i < 4; i++) await Promise.resolve();
  await tick();
  flushSync();
}

async function observePtt(value: boolean, at: number): Promise<void> {
  h.radio = { ...h.radio, ptt: value, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  h.notifyRuntime();
  await settle();
}

function sentFrames(socket: MockWebSocket): Array<{ name: string }> {
  return socket.sent.map((raw) => JSON.parse(raw));
}
function countFrames(name: string, ...sockets: MockWebSocket[]): number {
  return sockets.flatMap(sentFrames).filter((f) => f.name === name).length;
}

/** Resolve the oldest still-pending load for `id`. */
function completeLoad(id: SkinId): void {
  const index = h.pending.findIndex((entry) => entry.id === id);
  if (index < 0) throw new Error(`no pending load for ${id}`);
  h.pending.splice(index, 1)[0].resolve(probeComponent(id));
}

/**
 * Request a skin; does NOT resolve the loader.
 *
 * The width is the knob, and `notifyRuntime()` invalidates the tracked
 * `runtime.caps` read inside App's `skinId` `$derived` so it recomputes and
 * re-reads the width. (App's own `isMobile` only flips across the 640px
 * threshold, so a desktop→LCD→desktop run needs an explicit invalidation —
 * exactly what a backend state push does in production.)
 */
function requestSkin(id: SkinId): void {
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: WIDTH_FOR[id] });
  window.dispatchEvent(new Event('resize'));
  h.notifyRuntime();
  flushSync();
}

/** Request a skin and let its load land. */
async function switchTo(id: SkinId): Promise<void> {
  requestSkin(id);
  await settle();
  completeLoad(id);
  await settle();
}

const probeCount = () => document.querySelectorAll('.tx-controller-probe').length;

let mountedComponent: object | null = null;

async function mountConnectedApp(): Promise<{
  component: object; controller: AppTxController; socket: MockWebSocket;
}> {
  resetFacts();
  resetCapturedController();
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(App, { target });
  mountedComponent = component;
  flushSync();
  await settle(); // onMount's runtime.bootstrap() → txAuthorityReady = true
  completeLoad('desktop-v2');
  await vi.waitFor(async () => {
    await settle();
    if (!capturedController()) throw new Error('presentation not committed yet');
  });

  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();

  await observePtt(false, 2); // first fresh (non-baseline) PTT reading

  const controller = capturedController();
  if (!controller) throw new Error('TxControllerProbe never captured a controller');
  return { component, controller, socket };
}

/** Bring the real controller to a confirmed-on, owned key. */
async function keyDown(controller: AppTxController, leaseId: string): Promise<void> {
  controller.start('probe', leaseId, 'momentary');
  await settle();
  await observePtt(true, 3);
  expect(controller.snapshot()).toMatchObject({
    phase: 'active', txRisk: 'confirmed-on', mayOwnKey: true,
  });
}

describe('MOR-1086 — TX authority identity across a presentation switch', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    mountedComponent = null;
    document.body.innerHTML = '';
    h.pending.length = 0;
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1200 });
    Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });
    Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'visible' });
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    h.loadSkin.mockReset().mockImplementation(
      (id: SkinId) => new Promise((resolve) => { h.pending.push({ id, resolve }); }),
    );
    h.bootstrap.mockReset().mockResolvedValue(h.bootstrapCleanup);
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
    h.restore.mockClear();
    h.registerBarrier.mockReset().mockImplementation(() => () => {});
    h.initBattery.mockReset().mockResolvedValue(h.batteryCleanup);
    h.batteryCleanup.mockReset();
    h.runtimeState = { stateRevision: 1 };
    h.runtimeCaps = { tx: true };
  });

  afterEach(() => {
    if (mountedComponent) {
      try { unmount(mountedComponent); } catch { /* already unmounted */ }
      mountedComponent = null;
    }
    wsClient.disconnectAll();
    globalThis.WebSocket = originalWebSocket;
  });

  // MUTATION KILLED: driving the switch through anything that disposes and
  // re-provides the TX host per presentation (or that routes the swap through
  // an App-script re-run). The key would drop — `ptt_off` on the wire and
  // `stopLocalAudio` called — purely because the operator changed skin.
  it('never de-keys and hands the incoming subtree the identical controller', async () => {
    const { controller, socket } = await mountConnectedApp();
    await keyDown(controller, 'lease-swap');
    const guardBefore = controller.snapshot().guard;
    expect(guardBefore).not.toBeNull();
    expect(countFrames('ptt_on', socket)).toBe(1);

    // The old subtree's capture is cleared, so a non-null capture afterwards
    // can only have come from the INCOMING presentation.
    resetCapturedController();
    await switchTo('lcd-cockpit');

    expect(probeCount()).toBe(1);
    // Object identity — not "a controller", THE controller.
    expect(capturedController()).toBe(controller);
    // The key is still down, still owned, still confirmed, same lease.
    expect(controller.snapshot()).toMatchObject({
      phase: 'active', txRisk: 'confirmed-on', mayOwnKey: true, fault: null,
    });
    expect(controller.snapshot().guard).toEqual(guardBefore);
    // Wire truth: the swap caused neither a de-key nor a re-key/replay.
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(h.stop).not.toHaveBeenCalled();
    // …and it opened no second control socket: still the one session.
    expect(instances).toHaveLength(1);

    // And the surviving authority still releases normally afterwards.
    controller.release('probe', controller.snapshot().guard!);
    await settle();
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  // MUTATION KILLED: the same host-per-presentation mutation under the
  // A → B → A return leg, and any commit path that mounts more than the
  // newest request (two live probes would mean two subtrees owning TX UI).
  it('survives rapid A → B → A under a confirmed key with one live subtree', async () => {
    const { controller, socket } = await mountConnectedApp();
    await keyDown(controller, 'lease-rapid');
    const guardBefore = controller.snapshot().guard;
    const probeBefore = document.querySelector('.tx-controller-probe');

    // ── Leg 1: superseded in flight ──
    // Both hops are requested before either resolves, so the return leg
    // supersedes the outbound one while it is still loading.
    requestSkin('lcd-scope');
    await settle();
    requestSkin('desktop-v2');
    await settle();
    resetCapturedController();

    completeLoad('lcd-scope');   // stale — must not mount
    await settle();
    expect(capturedController()).toBeNull();

    completeLoad('desktop-v2');  // current — and it is the skin already on
    await settle();              // screen, so the subtree is never replaced

    expect(probeCount()).toBe(1);
    // `loadSkin('desktop-v2')` yields the same module default it yielded the
    // first time, so committing the return leg leaves the SAME component —
    // and therefore the same live subtree — in place. Nothing remounted,
    // which is why nothing re-captured above.
    expect(document.querySelector('.tx-controller-probe')).toBe(probeBefore);

    // ── Leg 2: two committed hops, each a real destroy/recreate ──
    for (const id of ['lcd-cockpit', 'desktop-v2'] as const) {
      resetCapturedController();
      await switchTo(id);
      expect(probeCount()).toBe(1);
      // Every incoming subtree pulls the IDENTICAL controller out of context.
      expect(capturedController()).toBe(controller);
    }
    expect(document.querySelector('.tx-controller-probe')).not.toBe(probeBefore);

    expect(controller.snapshot()).toMatchObject({
      phase: 'active', txRisk: 'confirmed-on', mayOwnKey: true, fault: null,
    });
    expect(controller.snapshot().guard).toEqual(guardBefore);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(h.stop).not.toHaveBeenCalled();
    expect(instances).toHaveLength(1);
  });

  // MUTATION KILLED: dropping the generation gate on the commit path while a
  // key is down. The superseded resolution would mount a second presentation
  // subtree on top of a live TX session — a stale surface owning the key's UI.
  it('makes a stale resolution that lands after a switch-during-TX inert', async () => {
    const { controller, socket } = await mountConnectedApp();
    await keyDown(controller, 'lease-stale');
    const guardBefore = controller.snapshot().guard;
    const snapshotBefore = controller.snapshot();

    requestSkin('mobile');       // request 2 — left in flight
    await settle();
    requestSkin('sdr-test');     // request 3 — supersedes it
    await settle();
    resetCapturedController();

    completeLoad('mobile');      // the stale resolution lands
    await settle();

    // It mounted nothing and touched no TX state at all.
    expect(capturedController()).toBeNull();
    expect(controller.snapshot()).toEqual(snapshotBefore);
    expect(controller.snapshot().guard).toEqual(guardBefore);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.stop).not.toHaveBeenCalled();

    completeLoad('sdr-test');    // the current one commits normally
    await settle();
    expect(probeCount()).toBe(1);
    expect(capturedController()).toBe(controller);
    expect(controller.snapshot()).toMatchObject({
      phase: 'active', txRisk: 'confirmed-on', mayOwnKey: true,
    });
    expect(countFrames('ptt_off', socket)).toBe(0);
  });

  // MUTATION KILLED: releasing per presentation replacement instead of once
  // at App teardown — the switches above would each add a `ptt_off`.
  it('releases exactly once at teardown after a run of switches under a key', async () => {
    const { component, controller, socket } = await mountConnectedApp();
    await keyDown(controller, 'lease-teardown');

    for (const id of ['lcd-cockpit', 'mobile', 'desktop-v2'] as const) {
      await switchTo(id);
    }
    expect(countFrames('ptt_off', socket)).toBe(0);

    unmount(component);
    mountedComponent = null;

    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // A late lifecycle event after teardown adds no second release.
    window.dispatchEvent(new Event('pagehide'));
    expect(countFrames('ptt_off', socket)).toBe(1);
  });
});
