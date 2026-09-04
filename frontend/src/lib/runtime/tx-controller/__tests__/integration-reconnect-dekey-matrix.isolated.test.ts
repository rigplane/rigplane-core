import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// Managed reconnect/de-key delivery matrix (MOR-1089 U5).
// It joins the real WsChannel, managed browser dependencies, and
// ManagedTxController while controlling only server-state/media inputs and
// the socket boundary. The cases pin OFF-first recovery ordering, backend
// de-key/re-key observations, and convergence of a pending OFF across a
// session change. Server observations are supplied explicitly; they do not
// reconstruct browser-local TX ownership.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => null, managedTransmitIsStale: () => true,
  managedTransmitRemainingMs: () => null, refreshManagedTransmit: vi.fn(async () => {}),
  invalidateManagedTransmit: vi.fn(), submitManagedTransmit: vi.fn(async () => 'accepted'),
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

/** Boot the real control channel and managed controller without Svelte context. */
async function setup(): Promise<{
  factory: Factory; controller: Controller; socket: MockWebSocket;
  getSession: () => ControlSessionTransition; wsClient: WsClientModule;
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
  return { factory, controller, socket, getSession: () => session, wsClient };
}

function dispatchStart(
  controller: Controller, factory: Factory, session: ControlSessionTransition,
  leaseId: string, intent: 'momentary' | 'latched', at: number,
) {
  h.radio = { ...h.radio, ptt: false, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  void factory; void session; void leaseId; void intent; void at;
  controller.pttOn();
}

function confirmAuthority(
  controller: Controller, factory: Factory, session: ControlSessionTransition,
  pttValue: boolean, at: number,
) {
  h.radio = { ...h.radio, ptt: pttValue, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  void controller; void factory; void session; void pttValue; void at;
}

/** Deliver the current managed `pttOff()` request. */
function releaseNow(controller: Controller, factory: Factory) {
  void factory; void controller.pttOff();
}

function sentFrames(socket: MockWebSocket): Array<{ name: string }> {
  return socket.sent.map((raw) => JSON.parse(raw));
}
function countFrames(name: string, ...sockets: MockWebSocket[]): number {
  return sockets.flatMap(sentFrames).filter((f) => f.name === name).length;
}

describe('tx-controller integration reconnect/de-key matrix — real WsChannel + real browser dependencies + real TxController', () => {
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

  it('WS loss during active TX: the automatic release queues OFF, which is the first TX-relevant frame on the recovered socket, ahead of other recovered traffic', async () => {
    const { factory, controller, socket: socket0, getSession, wsClient } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-reconnect-off-first', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(countFrames('ptt_on', socket0)).toBe(1);

    // Real reconnect logic: close the fake socket. Losing the control session
    // drops eligibility.controlLive, which the model turns into an automatic
    // release; the real WsChannel can't send it yet (no open socket) so it
    // queues it as `pendingPttRelease` — exactly like U4's no-ON-replay case.
    socket0.simulateClose();
    expect(countFrames('ptt_off', socket0)).toBe(0);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // Simulate other recovered traffic queued while offline — a benign,
    // non-PTT command riding the same `sendQueue` the OFF release rides in on.
    // ws-client's `onopen` drains `pendingPttRelease` before `sendQueue`, so
    // this is what makes the ordering assertion below non-vacuous.
    wsClient.sendCommand('set_freq', { freq: 14_200_000 });

    vi.advanceTimersByTime(1_500); // past calcBackoff's 800-1200ms jitter window
    expect(instances.length).toBe(2);
    const socket1 = instances[1];
    socket1.simulateOpen();

    const framesOnSocket1 = sentFrames(socket1);
    expect(framesOnSocket1.map((f) => f.name)).toEqual(['ptt_off', 'set_freq']); // OFF first, explicitly
    expect(framesOnSocket1.some((f) => f.name === 'ptt_on')).toBe(false); // never a replayed ON
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);

    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
  });

  it('WS loss while a release is pending: the unconfirmed OFF obligation survives the reconnect intact, never duplicated or discarded', async () => {
    const { factory, controller, socket: socket0, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-pending-release', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    // Release issued by the user while the socket is still live: the OFF
    // reaches the wire immediately (bound via a synchronous "sent" delivery)
    // but never gets a response — "queued/unconfirmed" from the app's view.
    releaseNow(controller, factory);
    expect(countFrames('ptt_off', socket0)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // WS loss strikes mid-release, before any confirmation ever arrives.
    socket0.simulateClose();

    vi.advanceTimersByTime(1_500); // past calcBackoff's 800-1200ms jitter window
    expect(instances.length).toBe(2);
    const socket1 = instances[1];
    socket1.simulateOpen();

    // The already-sent OFF is never replayed or duplicated on the recovered
    // socket — the reconnect's epoch bump must not re-drive a fresh release.
    expect(sentFrames(socket1)).toEqual([]);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);

    // No client confirmation timer or retry is armed after reconnect.
    vi.advanceTimersByTime(6_000);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);

    // A fresh authoritative ptt:false on the NEW epoch is the source of truth,
    // and the controller converges on it: the obligation is discharged without
    // ever putting another OFF on the wire.
    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1); // still exactly one OFF, ever
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
  });
});
