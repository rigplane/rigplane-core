import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// Managed delivery lifecycle matrix (MOR-1089 U4): held, latched,
// pending-release, duplicate-release, and reconnect/no-ON-replay behavior.
// It joins the real WsChannel, managed browser dependencies, and
// ManagedTxController; only server-state/media inputs and the socket boundary
// are controlled. Every case resets the module graph because WsChannel keeps
// module-level session state.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
  submit: vi.fn(async () => 'accepted' as const),
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => ({ schemaVersion: 1, sampledAt: '2026-09-04T00:00:00Z', managedTransmit: { status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null, lastActuation: null, abortErrors: [], tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null } }, txObservation: { observedPtt: 'off' } }),
  managedTransmitIsStale: () => false, managedTransmitRemainingMs: () => null,
  refreshManagedTransmit: vi.fn(async () => {}), invalidateManagedTransmit: vi.fn(), submitManagedTransmit: h.submit,
  setManagedTransmitTot: vi.fn(async () => {}),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => h.radio,
  setRadioState: () => {},
  patchActiveReceiver: () => {},
  patchRadioState: () => {},
  resetRadioState: () => {},
}));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => h.caps,
  capabilitiesMatchGeneration: () => true,
  clearCapabilities: () => {},
  setCapabilities: () => true,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
    onTxAudioDied: () => () => {},
    startManagedTx: h.start,
    stopLocalAudio: h.stop,
  }),
}));

type WsClientModule = typeof import('$lib/transport/ws-client');
type ConnectionModule = typeof import('$lib/stores/connection.svelte');
type BrowserDepsModule = typeof import('../browser-dependencies');
type ControllerModule = typeof import('../managed-controller');
type Factory = ReturnType<BrowserDepsModule['createManagedBrowserDependencies']>;
type Controller = InstanceType<ControllerModule['ManagedTxController']>;

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
  const controllerModule = (await import('../managed-controller')) as ControllerModule;
  return { wsClient, connection, browserDeps, controllerModule };
}

/** Boot the real control channel, factory, and controller — wired the same
 * way `managed-app-host.ts`'s `provideManagedAppTxHost` wires them, minus the
 * Svelte context (which needs a live component to call into). */
async function setup(): Promise<{
  factory: Factory; controller: Controller; socket: MockWebSocket;
  getSession: () => ControlSessionTransition;
}> {
  const { wsClient, connection, browserDeps, controllerModule } = await loadStack();
  const factory = browserDeps.createManagedBrowserDependencies();
  const controller = new controllerModule.ManagedTxController(factory.dependencies);
  let session: ControlSessionTransition = { state: 'disconnected', epoch: 0 };
  factory.subscribeSession((transition) => {
    session = transition;
    if (transition.state !== 'connected') void controller.releaseSession().finally(() => controller.abandonSession());
  });
  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  return { factory, controller, socket, getSession: () => session };
}

function dispatchStart(
  controller: Controller, intent: 'momentary' | 'latched', at: number,
) {
  h.radio = { ...h.radio, ptt: false, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  void at;
  if (intent === 'momentary') controller.pttOn(); else controller.transmitOn();
}

function confirmAuthority(
  controller: Controller, factory: Factory, session: ControlSessionTransition,
  pttValue: boolean, at: number,
) {
  h.radio = { ...h.radio, ptt: pttValue, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  void controller; void factory; void session; void pttValue; void at;
}

/** Re-reads `controller.snapshot().guard` fresh on every call — the same
 * thing a real caller (e.g. a panel's release handler) does. This matters
 * for the duplicate-release case: two release dispatches sharing one STALE
 * guard object would already be blocked by the event-level `sameGuard` check
 * before ever reaching `release()`'s own re-entrancy guard. */
function releaseNow(controller: Controller, factory: Factory, latched = false) {
  void factory;
  if (latched) void controller.forceOff(); else void controller.pttOff();
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
    h.submit.mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.resetModules();
  });

  it('held: ON hits the wire exactly once, holds through an authoritative confirm, then a clean release/OFF/idle cycle', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, 'momentary', 2);
    await flush();
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);

    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(countFrames('ptt_on', socket)).toBe(1); // holding never re-sends ON

    releaseNow(controller, factory);
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('latched: ON hits the wire exactly once, holds through intent "latched", then a clean release/OFF/idle cycle', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, 'latched', 2);
    await flush();
    expect(countFrames('ptt_on', socket)).toBe(0);
    expect(h.submit).toHaveBeenCalledWith('transmit_on');

    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(countFrames('ptt_on', socket)).toBe(0);

    releaseNow(controller, factory, true);
    expect(h.submit).toHaveBeenCalledWith('force_off');

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_on', socket)).toBe(0);
    expect(countFrames('ptt_off', socket)).toBe(0);
  });

  it('pending release: releasing before audio confirms keeps ON off the wire even once the deferred audio resolves late', async () => {
    const { factory, controller, socket, getSession } = await setup();
    let resolveAudio!: (error: string | null) => void;
    h.start.mockImplementationOnce(() => new Promise((resolve) => { resolveAudio = resolve; }));

    dispatchStart(controller, 'momentary', 2);
    releaseNow(controller, factory);
    expect(h.stop).toHaveBeenCalledTimes(1);

    resolveAudio(null); // the deferred audio "succeeds" — but only AFTER release already ran
    await flush();

    expect(countFrames('ptt_on', socket)).toBe(0);
    expect(countFrames('ptt_off', socket)).toBe(0);
    confirmAuthority(controller, factory, getSession(), false, 3);
    expect(countFrames('ptt_on', socket)).toBe(0);
  });

  it('duplicate release coalescing: two release dispatches produce exactly one ptt_off frame', async () => {
    const { factory, controller, socket, getSession } = await setup();
    dispatchStart(controller, 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    releaseNow(controller, factory);
    releaseNow(controller, factory); // fresh guard snapshot each call, like a real double-click
    expect(countFrames('ptt_off', socket)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(1);
  });

  it('no-ON-replay: reconnecting with an active lease never re-sends ptt_on, and the queued OFF drains first', async () => {
    const { factory, controller, socket: socket0, getSession } = await setup();
    dispatchStart(controller, 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(countFrames('ptt_on', socket0)).toBe(1);

    // Real reconnect logic: close the fake socket. Losing the control
    // session drops eligibility.controlLive, which the model turns into an
    // automatic release; the real WsChannel can't send it yet (no open
    // socket) so it queues it as `pendingPttRelease`.
    socket0.simulateClose();
    expect(countFrames('ptt_off', socket0)).toBe(0);

    vi.advanceTimersByTime(1_500); // past calcBackoff's 800-1200ms jitter window
    expect(instances.length).toBe(2);
    const socket1 = instances[1];
    socket1.simulateOpen();

    // Session disconnect owns de-key server-side; no browser ON/OFF is replayed.
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
  });
});
