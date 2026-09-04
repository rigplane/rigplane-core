/**
 * MOR-1086 — presentation switching preserves TX authority identity.
 *
 * Evidence-only (no production change). The MOR-1060 suite proves that a
 * switch does not re-provide the managed TX host (the provider is
 * called once) against a MOCKED host. That leaves the operator-facing
 * question unanswered: with a key actually DOWN, does replacing the
 * presentation de-key the radio, lose the lease, or hand the incoming
 * subtree a different controller?
 *
 * This file answers it through the real stack — real `App.svelte`, real
 * `provideManagedAppTxHost`/`ManagedTxController`, real `WsChannel` singleton
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
 *   1. a switch under a held PTT intent sends no
 *      `ptt_off` and no second `ptt_on` — the swap neither de-keys nor replays;
 *   2. the incoming subtree receives the IDENTICAL managed controller object;
 *   3. rapid A → B → A under a key is the same, with one live subtree;
 *   4. a stale resolution landing after a switch-during-TX cannot mount and
 *      cannot touch the TX source;
 *   5. teardown under an owned key still releases exactly once.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';
import type { SkinId } from '../skins/registry';

type ProbeComponent = (...args: Parameters<typeof TxControllerProbe>) => ReturnType<typeof TxControllerProbe>;
type Pending = { id: SkinId; resolve: (component: ProbeComponent) => void };

const h = vi.hoisted(() => ({
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  submit: vi.fn(async () => 'accepted' as const),
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

vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => null, managedTransmitIsStale: () => true,
  managedTransmitRemainingMs: () => null, refreshManagedTransmit: vi.fn(async () => {}),
  invalidateManagedTransmit: vi.fn(), setManagedTransmitTot: vi.fn(async () => {}), submitManagedTransmit: h.submit,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    onTxAudioDied: () => () => {},
    startManagedTx: h.start,
    stopLocalAudio: h.stop,
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
import type { ManagedAppTxController } from '../lib/runtime/tx-controller/managed-app-host';

/** Viewport width → skin id. Reuses App's own resize reactivity as the knob. */
const WIDTH_FOR: Record<SkinId, number> = {
  'desktop-v2': 1200,
  'dual-receiver-cockpit': 1500,
  'lcd-cockpit': 1000,
  'lcd-scope': 900,
  'sdr-test': 1400,
  'mobile': 390,
  // MOR-2155: `peer-split` has no picker/resolveSkinId path (MOR-2152), so
  // this file's TX-authority sweeps never request it directly — it is here
  // only to satisfy `Record<SkinId, number>` exhaustiveness. 1600 is unused
  // by every other entry in this table.
  'peer-split': 1600,
  'unified-instrument': 1280,
  'panadapter-first': 1281,
  // Production entrypoint is readonly and receives no command callback.
  'dual-sdr-face': 1700,
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
 * `getManagedAppTxController()` from inside the real mounted App tree.
 */
const probeFor = new Map<string, ProbeComponent>();
function probeComponent(id: string): ProbeComponent {
  const existing = probeFor.get(id);
  if (existing) return existing;
  const probe: ProbeComponent = (...args) => TxControllerProbe(...args);
  probeFor.set(id, probe);
  return probe;
}

/** Drain past App's post-commit `await tick()`. */
async function settle(): Promise<void> {
  for (let i = 0; i < 4; i++) await Promise.resolve();
  await tick();
  flushSync();
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
  component: object; controller: ManagedAppTxController; socket: MockWebSocket;
}> {
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

  const controller = capturedController();
  if (!controller) throw new Error('TxControllerProbe never captured a controller');
  return { component, controller, socket };
}

/** Start one momentary PTT flow without inventing browser RF truth. */
async function keyDown(controller: ManagedAppTxController): Promise<void> {
  controller.pttOn();
  await settle();
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
    h.submit.mockClear();
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
    await keyDown(controller);
    expect(countFrames('ptt_on', socket)).toBe(1);

    // The old subtree's capture is cleared, so a non-null capture afterwards
    // can only have come from the INCOMING presentation.
    resetCapturedController();
    await switchTo('lcd-cockpit');

    expect(probeCount()).toBe(1);
    // Object identity — not "a controller", THE controller.
    expect(capturedController()).toBe(controller);
    // Wire truth: the swap caused neither a de-key nor a re-key/replay.
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.stop).not.toHaveBeenCalled();
    // …and it opened no second control socket: still the one session.
    expect(instances).toHaveLength(1);

    // And the surviving authority still releases normally afterwards.
    await controller.pttOff();
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  // MUTATION KILLED: the same host-per-presentation mutation under the
  // A → B → A return leg, and any commit path that mounts more than the
  // newest request (two live probes would mean two subtrees owning TX UI).
  it('survives rapid A → B → A under a confirmed key with one live subtree', async () => {
    const { controller, socket } = await mountConnectedApp();
    await keyDown(controller);
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
    for (const id of ['lcd-cockpit', 'unified-instrument', 'panadapter-first', 'desktop-v2'] as const) {
      resetCapturedController();
      await switchTo(id);
      expect(probeCount()).toBe(1);
      // Every incoming subtree pulls the IDENTICAL controller out of context.
      expect(capturedController()).toBe(controller);
    }
    expect(document.querySelector('.tx-controller-probe')).not.toBe(probeBefore);

    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.stop).not.toHaveBeenCalled();
    expect(instances).toHaveLength(1);
  });

  // MUTATION KILLED: dropping the generation gate on the commit path while a
  // key is down. The superseded resolution would mount a second presentation
  // subtree on top of a live TX session — a stale surface owning the key's UI.
  it('makes a stale resolution that lands after a switch-during-TX inert', async () => {
    const { controller, socket } = await mountConnectedApp();
    await keyDown(controller);
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
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.submit).not.toHaveBeenCalled();
    expect(h.stop).not.toHaveBeenCalled();

    completeLoad('sdr-test');    // the current one commits normally
    await settle();
    expect(probeCount()).toBe(1);
    expect(capturedController()).toBe(controller);
    expect(countFrames('ptt_off', socket)).toBe(0);
  });

  // MUTATION KILLED: releasing per presentation replacement instead of once
  // at App teardown — the switches above would each add a `ptt_off`.
  it('releases exactly once at teardown after a run of switches under a key', async () => {
    const { component, controller, socket } = await mountConnectedApp();
    await keyDown(controller);

    for (const id of ['lcd-cockpit', 'mobile', 'desktop-v2'] as const) {
      await switchTo(id);
    }
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.submit).not.toHaveBeenCalled();

    unmount(component);
    mountedComponent = null;

    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // A late lifecycle event after teardown adds no second release.
    window.dispatchEvent(new Event('pagehide'));
    expect(countFrames('ptt_off', socket)).toBe(1);
  });
});
