import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// ─── Integration-lifecycle matrix — held/latched/pending-release/no-ON-replay
// through the REAL stack (MOR-1089 U4) ───────────────────────────────────────
//
// Every other tx-controller test proves ONE layer against a hand-built fake of
// its neighbor (model.ts against fake dependencies in controller-contract, or
// browser-dependencies.ts against a fake ws-client in
// browser-dependencies(-fault-injection).test.ts). This file is the one place
// that wires all three for real: the REAL WsChannel singleton in
// `$lib/transport/ws-client` (driven only at the socket boundary by the
// shared `MockWebSocket` fake — U0), the REAL
// `createBrowserTxControllerDependencies()` factory, and a REAL
// `TxController`. Only the seams `browser-dependencies.ts` reads facts from
// are mocked (radio.svelte, capabilities.svelte, tx-adapter) — the same seam
// U2 (browser-dependencies-fault-injection.test.ts) mocks.
// `$lib/stores/connection.svelte` is left REAL: it is the real ws-client's
// own dependency for `isLiveRadioAvailable()`/connection flags, plain
// `$state` setters that are harmless under jsdom.
//
// The "session → controller" glue in `setup()` below (dispatch `epoch` then
// `authority` on every session transition) is a deliberately small, direct
// reproduction of `app-host.ts`'s `applyAuthority` —
// `provideAppTxControllerHost` itself can't run here because it calls
// Svelte's `getContext`/`setContext`, which require a live component
// lifecycle.
//
// Like `ws-client-store.integration.test.ts`, ws-client holds module-level
// singletons (`_ctrl`, `_fullState`, ...) that persist across `it()` blocks
// sharing a module cache, so every test resets the module graph
// (`vi.resetModules()`) and dynamically imports the stack fresh.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.radio,
  setRadioState: () => {},
  patchActiveReceiver: () => {},
  patchRadioState: () => {},
  resetRadioState: () => {},
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({ getCapabilities: () => h.caps }));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    startTx: h.start,
    stopLocalAudio: h.stop,
    restoreModAfterConfirmedOff: h.restore,
  }),
}));

type WsClientModule = typeof import('$lib/transport/ws-client');
type ConnectionModule = typeof import('$lib/stores/connection.svelte');
type BrowserDepsModule = typeof import('../browser-dependencies');
type ControllerModule = typeof import('../controller');
type Factory = ReturnType<BrowserDepsModule['createBrowserTxControllerDependencies']>;
type Controller = InstanceType<ControllerModule['TxController']>;

const field = (at: number) => ({
  observed: true, freshness: 'fresh' as const, availability: 'available' as const,
  lastObservedMonotonic: at, source: { source: 'poll_response' },
});
const target = { receiver: 'SUB' as const, slot: 'B' as const, frequencyHz: 150 };

function resetFacts(): void {
  h.radio = {
    revision: 1, ptt: false, active: 'SUB', txTarget: { status: 'known', ...target },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { ptt: field(1), txTarget: field(1), data1ModInput: field(1) },
  };
  h.caps = {
    tx: true, audioTx: true, capabilities: ['tx', 'mod_input_routing'],
    vfoScheme: 'main_sub', audioTxRequiredModInputSource: 5, txBands: [{ start: 100, end: 200 }],
  } as Capabilities;
}

const flush = async () => { await Promise.resolve(); await Promise.resolve(); };

async function loadStack() {
  const wsClient = (await import('$lib/transport/ws-client')) as WsClientModule;
  const connection = (await import('$lib/stores/connection.svelte')) as ConnectionModule;
  const browserDeps = (await import('../browser-dependencies')) as BrowserDepsModule;
  const controllerModule = (await import('../controller')) as ControllerModule;
  return { wsClient, connection, browserDeps, controllerModule };
}

/** Boot the real control channel, factory, and controller — wired the same
 * way `app-host.ts`'s `provideAppTxControllerHost` wires them, minus the
 * Svelte context (which needs a live component to call into). */
async function setup(): Promise<{
  factory: Factory; controller: Controller; socket: MockWebSocket;
  getSession: () => ControlSessionTransition;
}> {
  const { wsClient, connection, browserDeps, controllerModule } = await loadStack();
  const factory = browserDeps.createBrowserTxControllerDependencies();
  const baseline = factory.projectAuthority({ state: 'disconnected', epoch: 0 });
  const controller = new controllerModule.TxController(baseline.epoch, baseline.ptt.marker, factory.dependencies);
  let session: ControlSessionTransition = { state: 'disconnected', epoch: 0 };
  factory.subscribeSession((authority, transition) => {
    session = transition;
    const offCommandId = factory.dependencies.commandId('off');
    if (authority.epoch > controller.snapshot().authorityEpoch) {
      controller.dispatch({ type: 'epoch', epoch: authority.epoch, baseline: authority.ptt.marker, offCommandId });
    }
    controller.dispatch({
      type: 'authority', epoch: authority.epoch, ptt: authority.ptt,
      eligibility: authority.eligibility, offCommandId,
    });
  });
  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  return { factory, controller, socket, getSession: () => session };
}

function dispatchStart(
  controller: Controller, factory: Factory, session: ControlSessionTransition,
  leaseId: string, intent: 'momentary' | 'latched', at: number,
) {
  h.radio = { ...h.radio, ptt: false, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  const authority = factory.projectAuthority(session);
  controller.dispatch({
    type: 'start', sourceId: 'panel-a', leaseId, intent,
    eligibility: authority.eligibility, ptt: authority.ptt,
  });
  return authority;
}

function confirmAuthority(
  controller: Controller, factory: Factory, session: ControlSessionTransition,
  pttValue: boolean, at: number,
) {
  h.radio = { ...h.radio, ptt: pttValue, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  const authority = factory.projectAuthority(session);
  controller.dispatch({
    type: 'authority', epoch: authority.epoch, ptt: authority.ptt,
    eligibility: authority.eligibility, offCommandId: factory.dependencies.commandId('off'),
  });
  return authority;
}

/** Re-reads `controller.snapshot().guard` fresh on every call — the same
 * thing a real caller (e.g. a panel's release handler) does. This matters
 * for the duplicate-release case: two release dispatches sharing one STALE
 * guard object would already be blocked by the event-level `sameGuard` check
 * before ever reaching `release()`'s own re-entrancy guard. */
function releaseNow(controller: Controller, factory: Factory) {
  controller.dispatch({
    type: 'release', sourceId: 'panel-a', guard: controller.snapshot().guard!,
    commandId: factory.dependencies.commandId('off'),
  });
}

function sentFrames(socket: MockWebSocket): Array<{ name: string }> {
  return socket.sent.map((raw) => JSON.parse(raw));
}
function countFrames(name: string, ...sockets: MockWebSocket[]): number {
  return sockets.flatMap(sentFrames).filter((f) => f.name === name).length;
}

describe('tx-controller integration lifecycle matrix — real WsChannel + real browser dependencies + real TxController', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
    resetFacts();
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
    h.restore.mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.resetModules();
  });

  it('held: ON hits the wire exactly once, holds through an authoritative confirm, then a clean release/OFF/idle cycle', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-held', 'momentary', 2);
    await flush();
    expect(controller.snapshot().phase).toBe('key-confirm-pending');
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);

    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', txRisk: 'confirmed-on' });
    expect(countFrames('ptt_on', socket)).toBe(1); // holding never re-sends ON

    releaseNow(controller, factory);
    expect(controller.snapshot().phase).toBe('releasing');
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(controller.snapshot()).toMatchObject({
      phase: 'idle', fault: null, guard: null, mayOwnKey: false, pendingOff: null,
    });
    expect(h.restore).toHaveBeenCalledTimes(1);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('latched: ON hits the wire exactly once, holds through intent "latched", then a clean release/OFF/idle cycle', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-latched', 'latched', 2);
    await flush();
    expect(controller.snapshot()).toMatchObject({ phase: 'key-confirm-pending', intent: 'latched' });
    expect(countFrames('ptt_on', socket)).toBe(1);

    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', intent: 'latched' });
    expect(countFrames('ptt_on', socket)).toBe(1);

    releaseNow(controller, factory);
    expect(countFrames('ptt_off', socket)).toBe(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', fault: null });
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('pending release: releasing before audio confirms keeps ON off the wire even once the deferred audio resolves late', async () => {
    const { factory, controller, socket, getSession } = await setup();
    let resolveAudio!: (error: string | null) => void;
    h.start.mockImplementationOnce(() => new Promise((resolve) => { resolveAudio = resolve; }));

    dispatchStart(controller, factory, getSession(), 'lease-pending', 'momentary', 2);
    expect(controller.snapshot()).toMatchObject({ phase: 'audio-start-pending', mayOwnKey: false });

    releaseNow(controller, factory);
    expect(controller.snapshot()).toMatchObject({ phase: 'releasing', pendingOff: null });
    expect(h.stop).toHaveBeenCalledTimes(1);

    resolveAudio(null); // the deferred audio "succeeds" — but only AFTER release already ran
    await flush();

    expect(countFrames('ptt_on', socket)).toBe(0);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(controller.snapshot().phase).toBe('releasing'); // late audio-ready: stale-guard no-op

    confirmAuthority(controller, factory, getSession(), false, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', fault: null, modRestorePending: false });
    expect(h.restore).toHaveBeenCalledTimes(1); // the one MOD-restore obligation resolves exactly once
    expect(countFrames('ptt_on', socket)).toBe(0);
  });

  it('duplicate release coalescing: two release dispatches produce exactly one ptt_off frame', async () => {
    const { factory, controller, socket, getSession } = await setup();
    dispatchStart(controller, factory, getSession(), 'lease-dup', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot().phase).toBe('active');

    releaseNow(controller, factory);
    const pendingOffAfterFirst = controller.snapshot().pendingOff;
    expect(controller.snapshot().phase).toBe('releasing');

    releaseNow(controller, factory); // fresh guard snapshot each call, like a real double-click

    expect(controller.snapshot().pendingOff).toEqual(pendingOffAfterFirst); // second release coalesced
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(controller.snapshot().phase).toBe('idle');
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('no-ON-replay: reconnecting with an active lease never re-sends ptt_on, and the queued OFF drains first', async () => {
    const { factory, controller, socket: socket0, getSession } = await setup();
    dispatchStart(controller, factory, getSession(), 'lease-reconnect', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot().phase).toBe('active');
    expect(countFrames('ptt_on', socket0)).toBe(1);

    // Real reconnect logic: close the fake socket. Losing the control
    // session drops eligibility.controlLive, which the model turns into an
    // automatic release; the real WsChannel can't send it yet (no open
    // socket) so it queues it as `pendingPttRelease`.
    socket0.simulateClose();
    expect(controller.snapshot().phase).toBe('releasing');
    expect(countFrames('ptt_off', socket0)).toBe(0);

    vi.advanceTimersByTime(1_500); // past calcBackoff's 800-1200ms jitter window
    expect(instances.length).toBe(2);
    const socket1 = instances[1];
    socket1.simulateOpen();

    // The queued OFF drains on the very first reconnect flush; the original
    // ON is never replayed onto the new socket (or anywhere else).
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', fault: null });
    expect(h.restore).toHaveBeenCalledTimes(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
  });
});
