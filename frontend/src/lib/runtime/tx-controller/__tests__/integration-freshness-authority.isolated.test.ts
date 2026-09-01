import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// ─── Freshness-contract integration pin (MOR-1880) — the REAL
// `createAppAuthorityProjector` joined to the REAL `TxController` through the
// REAL stack ─────────────────────────────────────────────────────────────────
//
// Every other app-authority test (`app-authority.test.ts`) drives the
// projector alone. The sibling integration files
// (`integration-lifecycle-matrix.isolated.test.ts`,
// `integration-reconnect-dekey-matrix.isolated.test.ts`) already join the
// real projector to the real reducer the same way this file does. Neither
// ever dispatches `start` against a REPEATED,
// non-advancing timestamp — the exact shape MOR-1880 fixes and closed PR
// #2921 broke (it set `pttObservationSeq: null` unconditionally, and 8163
// other tests in this suite stayed green while keying was impossible end to
// end). This file is the one that exercises that shape: `setup()` below is a
// deliberately small, direct reproduction of `app-host.ts`'s
// `applyAuthority` — `provideAppTxControllerHost` itself can't run here
// because it needs a live Svelte component for `getContext`/`setContext`,
// the same reason the sibling integration files give.
//
// Real: the REAL `WsChannel` singleton in `$lib/transport/ws-client` (driven
// only at the socket boundary by the shared `MockWebSocket` fake), the REAL
// `createBrowserTxControllerDependencies()` factory (so the REAL
// `createAppAuthorityProjector`), and a REAL `TxController`
// (`controller.ts`/`model.ts`, untouched by MOR-1880). Mocked: only the facts
// seam `browser-dependencies.ts` reads from (`radio.svelte`,
// `capabilities.svelte`, `tx-adapter`) — the same seam every sibling file
// mocks.
//
// Like the sibling isolated files, `vi.resetModules()` runs every `it()`
// because `ws-client` holds module-level singletons that would otherwise leak
// session/epoch state across tests sharing a module cache.
//
// The scenario: a normal page-load session sequence (`connecting`/epoch 0 —
// emitted by `wsClient.connect()` itself — then `connected`/epoch 1 once the
// socket opens), one genuinely newer PTT observation to move `radioTx` off
// `'unknown'`, then a SECOND observation repeating the same
// `lastObservedMonotonic` — the shape a delta that omits `fieldStatus.ptt`
// produces in production (MOR-1880's actual bug). The key control is then
// pressed (`start`) against that repeated-timestamp reading. Before MOR-1880,
// `app-authority.ts` reported that reading `fresh: false` (decayed), so
// `authoritative()` failed and `start` refused with `fault: 'not-eligible'` /
// `'ptt-not-authoritative'` — the operator's bench symptom. After MOR-1880 the
// reading is `fresh: true`, and `model.ts`'s existing `currentConfirmedOff`
// leg (untouched by this fix) recognizes the repeated reading as "unchanged
// because nothing changed" and lets `start` proceed.

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
vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => h.caps,
  capabilitiesMatchGeneration: () => true,
  clearCapabilities: () => {},
  setCapabilities: () => true,
}));
vi.mock('$lib/runtime/adapters/tx-adapter', () => ({
  getTxAudioControl: () => ({
  onTxAudioDied: () => () => {},
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
 * way `app-host.ts`'s `provideAppTxControllerHost` wires them (`applyAuthority`
 * below mirrors its `epoch`-then-`authority` dispatch exactly), minus the
 * Svelte context. `wsClient.connect()` fires the real `'connecting'`/epoch 0
 * session transition synchronously (already observed by `subscribeSession`
 * below); `socket.simulateOpen()` fires the real `'connected'`/epoch 1 one. */
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

/** Push a new `fieldStatus.ptt` reading and replay it through the real
 * projector as a real `authority` event — the same shape `refreshAuthority()`
 * produces in production on every backend push. */
function observeAuthority(
  controller: Controller, factory: Factory, session: ControlSessionTransition, at: number,
) {
  h.radio = { ...h.radio, ptt: false, fieldStatus: { ...h.radio.fieldStatus, ptt: field(at) } };
  const authority = factory.projectAuthority(session);
  controller.dispatch({
    type: 'authority', epoch: authority.epoch, ptt: authority.ptt,
    eligibility: authority.eligibility, offCommandId: factory.dependencies.commandId('off'),
  });
  return authority;
}

/** Re-project the CURRENT `fieldStatus.ptt` (no new reading pushed) and
 * dispatch `start` against it — the operator pressing the key control against
 * whatever the projector reports right now. */
function dispatchStart(
  controller: Controller, factory: Factory, session: ControlSessionTransition, leaseId: string,
) {
  const authority = factory.projectAuthority(session);
  controller.dispatch({
    type: 'start', sourceId: 'panel-a', leaseId, intent: 'momentary',
    eligibility: authority.eligibility, ptt: authority.ptt,
  });
  return authority;
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
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', radioTx: 'off' });

    // A SECOND reading repeating the exact same lastObservedMonotonic — the
    // shape a delta that omits fieldStatus.ptt produces (a bench measurement
    // on an IC-7300 over USB serial found fieldStatus.ptt present in 3 of 149
    // captured state_update messages, all three of type "full" — MOR-1880).
    observeAuthority(controller, factory, getSession(), 2);
    expect(controller.snapshot().phase).toBe('idle');

    // The operator presses the key control against that repeated-timestamp
    // reading.
    dispatchStart(controller, factory, getSession(), 'lease-freshness-pin');
    await flush();

    expect(controller.snapshot()).toMatchObject({ phase: 'key-confirm-pending', fault: null });
    expect(countFrames('ptt_on', socket)).toBe(1);
  });
});
