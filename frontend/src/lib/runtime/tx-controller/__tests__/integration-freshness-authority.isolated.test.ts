import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// Managed freshness delivery integration pin (MOR-1880).
// The real WsChannel, managed browser dependencies, and ManagedTxController
// are joined below; only server-state/media inputs and the socket boundary are
// controlled. The scenario repeats one server observation timestamp and
// verifies that the managed `pttOn()` delivery still reaches the wire.
//
// Real: the REAL `WsChannel` singleton in `$lib/transport/ws-client` (driven
// only at the socket boundary by the shared `MockWebSocket` fake), the REAL
// `createManagedBrowserDependencies()` factory and a REAL
// `ManagedTxController`. Mocked: only its projection/media seams
// seam `browser-dependencies.ts` reads from (`radio.svelte`,
// `capabilities.svelte`, `tx-adapter`) — the same seam every sibling file
// mocks.
//
// Like the sibling isolated files, `vi.resetModules()` runs every `it()`
// because `ws-client` holds module-level singletons that would otherwise leak
// session/epoch state across tests sharing a module cache.
//
// This remains a delivery integration scenario, not a browser-local source of
// radio truth: the test supplies the server-shaped observation explicitly.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  restore: vi.fn(),
  stale: true,
  document: null as any,
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => h.document, managedTransmitIsStale: () => h.stale,
  managedTransmitRemainingMs: () => null, refreshManagedTransmit: vi.fn(async () => { h.stale = false; }),
  invalidateManagedTransmit: () => { h.stale = true; }, submitManagedTransmit: vi.fn(async () => 'accepted'),
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

/** Boot the real control channel and managed controller without Svelte context. */
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
    if (transition.state === 'connected') void controller.refresh(); else controller.invalidate();
  });
  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  return { factory, controller, socket, getSession: () => session };
}

/** Push a server observation and refresh the managed snapshot. */
function observeAuthority(
  controller: Controller, factory: Factory, session: ControlSessionTransition, at: number,
) {
  h.radio = { ...h.radio, ptt: false, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  void factory; void session; void at;
  void controller.refresh();
  return controller.snapshot();
}

/** Deliver `pttOn()` without changing the supplied server observation. */
function dispatchStart(
  controller: Controller,
) {
  controller.pttOn();
  return controller.snapshot();
}

function sentFrames(socket: MockWebSocket): Array<{ name: string }> {
  return socket.sent.map((raw) => JSON.parse(raw));
}
function countFrames(name: string, socket: MockWebSocket): number {
  return sentFrames(socket).filter((f) => f.name === name).length;
}

describe('tx-controller freshness-authority integration pin — real projector + real reducer (MOR-1880)', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
    resetFacts();
    h.stale = false;
    h.document = { schemaVersion: 1, sampledAt: '2026-09-04T00:00:00Z', managedTransmit: {
      status: 'available', intent: { kind: 'rx' }, releaseRequired: false, lastError: null,
      lastActuation: null, abortErrors: [], tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
    }, txObservation: { observedPtt: 'off' } };
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
    h.restore.mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.resetModules();
  });

  it('keys through a repeated-timestamp reading — the delta-omits-fieldStatus.ptt shape — instead of refusing not-eligible', async () => {
    const { factory, controller, socket, getSession } = await setup();

    // One genuinely newer reading moves radioTx off 'unknown' to 'off'.
    observeAuthority(controller, factory, getSession(), 2);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', radioTx: 'off', fresh: true });

    // A SECOND reading repeating the exact same lastObservedMonotonic — the
    // shape a delta that omits fieldStatus.ptt produces (a bench measurement
    // on an IC-7300 over USB serial found fieldStatus.ptt present in 3 of 149
    // captured state_update messages, all three of type "full" — MOR-1880).
    observeAuthority(controller, factory, getSession(), 2);
    expect(controller.snapshot().phase).toBe('idle');

    // The operator presses the key control against that repeated-timestamp
    // reading.
    dispatchStart(controller);
    await flush();

    expect(countFrames('ptt_on', socket)).toBe(1);
  });
});
