import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// ─── Integration reconnect/de-key matrix — WS-loss recovery ordering and
// backend-driven de-key/re-key through the REAL stack (MOR-1089 U5) ─────────
//
// Sibling of `integration-lifecycle-matrix.isolated.test.ts` (U4): same wiring, same
// seam choices, same rationale for why `vi.resetModules()` runs per-`it()`
// and why this file lives in the isolated pool. See that file's header for
// the full explanation of the real-WsChannel + real-browser-dependencies +
// real-TxController wiring and the inlined `applyAuthority` glue below (a
// deliberately small, direct reproduction of `app-host.ts`'s
// `provideAppTxControllerHost`, which can't run here because it needs a live
// Svelte component lifecycle for `getContext`/`setContext`).
//
// U4 covers the steady-state lifecycle (held/latched/pending-release/no-ON-
// replay). This file covers what happens when the WS session itself misbehaves
// mid-lease:
//   1. WS loss while ACTIVE and KEYED — the model's own automatic release
//      (controlLive drops → `!ready(eligibility)` → `release()`) queues an
//      OFF that hasn't reached any socket yet. The focus here is proving
//      that queued OFF is the very first TX-relevant frame the recovered
//      socket ever sees — ahead of any other recovered/queued traffic.
//   2. A backend-observed de-key (authoritative ptt:false while the
//      controller believes it owns the key) — the model's
//      `fault: 'backend-dekeyed'` branch, not a self-initiated release.
//   3. An external re-key observed *after* a backend de-key — proving the
//      lease is never reconstructed from an external PTT observation alone.
//   4. WS loss while a *self-issued* release is already in flight but
//      unconfirmed — proving the pending release obligation is neither
//      duplicated nor silently discarded by the reconnect's epoch bump, and
//      that it can still converge afterwards.
//
// Case 4's boundary half was hand-verified against the real code before being
// written down: once the OFF has already reached the (since-dropped) socket,
// the reconnect must neither invent a duplicate OFF nor drop the still-owed
// one. Its convergence half is MOR-1205: the bound delivery barrier and the
// armed off-confirmation timeout are both epoch-stamped at the old epoch, so
// neither could fire against the new one and the controller used to sit in
// "releasing" forever. The stale timeout still cannot fire (asserted below);
// discharge now comes from a fresh authoritative ptt:false observed on the
// new epoch — evidence, not a timer, and never another command.

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
 * Svelte context (which needs a live component to call into). Identical to
 * U4's `setup()`, plus a `wsClient` handle so tests can drive extra wire
 * traffic (e.g. queueing a benign command while offline) directly. */
async function setup(): Promise<{
  factory: Factory; controller: Controller; socket: MockWebSocket;
  getSession: () => ControlSessionTransition; wsClient: WsClientModule;
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
  return { factory, controller, socket, getSession: () => session, wsClient };
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

/** Re-reads `controller.snapshot().guard` fresh on every call — see U4's
 * identical helper for why this matters for duplicate-release coalescing. */
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
    expect(controller.snapshot()).toMatchObject({ phase: 'active', mayOwnKey: true, txRisk: 'confirmed-on' });
    expect(countFrames('ptt_on', socket0)).toBe(1);

    // Real reconnect logic: close the fake socket. Losing the control session
    // drops eligibility.controlLive, which the model turns into an automatic
    // release; the real WsChannel can't send it yet (no open socket) so it
    // queues it as `pendingPttRelease` — exactly like U4's no-ON-replay case.
    socket0.simulateClose();
    expect(controller.snapshot().phase).toBe('releasing');
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
    expect(controller.snapshot()).toMatchObject({
      phase: 'idle', fault: null, guard: null, mayOwnKey: false, pendingOff: null,
    });
    expect(h.restore).toHaveBeenCalledTimes(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
  });

  it('backend de-key: an authoritative ptt:false observation while keyed is treated as external de-key, not a self-release', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-backend-dekey', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot()).toMatchObject({ phase: 'active', mayOwnKey: true, txRisk: 'confirmed-on' });
    expect(countFrames('ptt_on', socket)).toBe(1);

    // The backend independently reports ptt:false — no `release` event was
    // ever dispatched by this controller. A fresh, authoritative fieldStatus
    // (later monotonic than the confirmed-on observation) is what the model
    // requires to distinguish a real external de-key from a stale readback.
    confirmAuthority(controller, factory, getSession(), false, 5);

    expect(controller.snapshot()).toMatchObject({
      phase: 'failed', fault: 'backend-dekeyed',
      guard: null, leaseId: null, mayOwnKey: false, pendingOff: null,
      modRestorePending: false, txRisk: 'none', radioTx: 'off',
    });
    // No compensating ON — and no OFF either: the backend already de-keyed on
    // its own, there is nothing left for this controller to command.
    expect(countFrames('ptt_on', socket)).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.stop).toHaveBeenCalledTimes(1);
    expect(h.restore).toHaveBeenCalledTimes(1);
  });

  it('external re-key after backend de-key: a fresh ptt:true observation is only ever observed, never adopted as an owned key', async () => {
    const { factory, controller, socket, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-external-rekey', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    confirmAuthority(controller, factory, getSession(), false, 5); // backend de-key, per the case above
    expect(controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'backend-dekeyed', mayOwnKey: false, guard: null });

    // Someone/something else keys the radio — observed via the same
    // authoritative channel, with no `start`/`release` from this controller.
    confirmAuthority(controller, factory, getSession(), true, 6);

    expect(controller.snapshot()).toMatchObject({
      // No lease reconstruction: phase/fault/guard/leaseId are untouched by
      // the external observation — only the observed radioTx flag moves.
      phase: 'failed', fault: 'backend-dekeyed', guard: null, leaseId: null,
      mayOwnKey: false, pendingOff: null, radioTx: 'on',
    });
    // No ON frame — this TX was never commanded by us.
    expect(countFrames('ptt_on', socket)).toBe(1); // unchanged from the original lease
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.stop).toHaveBeenCalledTimes(1); // unchanged — no new stop/restore cycle
    expect(h.restore).toHaveBeenCalledTimes(1);
  });

  it('WS loss while a release is pending: the unconfirmed OFF obligation survives the reconnect intact, never duplicated or discarded', async () => {
    const { factory, controller, socket: socket0, getSession } = await setup();

    dispatchStart(controller, factory, getSession(), 'lease-pending-release', 'momentary', 2);
    await flush();
    confirmAuthority(controller, factory, getSession(), true, 3);
    expect(controller.snapshot().phase).toBe('active');

    // Release issued by the user while the socket is still live: the OFF
    // reaches the wire immediately (bound via a synchronous "sent" delivery)
    // but never gets a response — "queued/unconfirmed" from the app's view.
    releaseNow(controller, factory);
    const pendingOffBeforeClose = controller.snapshot().pendingOff;
    expect(controller.snapshot().phase).toBe('releasing');
    expect(pendingOffBeforeClose).not.toBeNull();
    expect(countFrames('ptt_off', socket0)).toBe(1);
    expect(h.stop).toHaveBeenCalledTimes(1);

    // WS loss strikes mid-release, before any confirmation ever arrives.
    socket0.simulateClose();
    expect(controller.snapshot().phase).toBe('releasing'); // no state corruption from the loss itself
    expect(controller.snapshot().pendingOff).toEqual(pendingOffBeforeClose); // obligation untouched by the close

    vi.advanceTimersByTime(1_500); // past calcBackoff's 800-1200ms jitter window
    expect(instances.length).toBe(2);
    const socket1 = instances[1];
    socket1.simulateOpen();

    // The already-sent OFF is never replayed or duplicated on the recovered
    // socket — the reconnect's epoch bump must not re-drive a fresh release.
    expect(sentFrames(socket1)).toEqual([]);
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);

    // The release obligation is not silently discarded by the reconnect: the
    // controller keeps the SAME pending-release record (same commandId,
    // leaseId, generation, originalEpoch) and stays in the conservative
    // "still releasing" phase — it never falsely reports idle/safe without an
    // actual confirmation, and it never forgets it still owes this release.
    expect(controller.snapshot()).toMatchObject({
      phase: 'releasing', mayOwnKey: true,
      pendingOff: expect.objectContaining({
        commandId: pendingOffBeforeClose!.commandId,
        leaseId: pendingOffBeforeClose!.leaseId,
        generation: pendingOffBeforeClose!.generation,
        originalEpoch: pendingOffBeforeClose!.originalEpoch,
      }),
    });
    expect(h.restore).not.toHaveBeenCalled(); // never falsely restores MOD without a real confirmation

    // ── The surviving obligation must also be able to DISCHARGE (MOR-1205) ──
    // The off-confirmation timeout armed before the drop is stamped at the old
    // epoch, so it can never fire against the new one: advancing well past its
    // 5s deadline must change nothing — no `release-not-confirmed`, no OFF.
    vi.advanceTimersByTime(6_000);
    expect(controller.snapshot()).toMatchObject({ phase: 'releasing', fault: null });
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1);

    // A fresh authoritative ptt:false on the NEW epoch is the source of truth,
    // and the controller converges on it: the obligation is discharged without
    // ever putting another OFF on the wire.
    confirmAuthority(controller, factory, getSession(), false, 4);
    expect(controller.snapshot()).toMatchObject({
      phase: 'idle', fault: null, guard: null, leaseId: null, mayOwnKey: false,
      pendingOff: null, txRisk: 'none', modRestorePending: false, radioTx: 'off',
    });
    expect(countFrames('ptt_off', socket0, socket1)).toBe(1); // still exactly one OFF, ever
    expect(countFrames('ptt_on', socket0, socket1)).toBe(1);
    expect(h.restore).toHaveBeenCalledTimes(1); // MOD restored once, on real evidence
  });
});
