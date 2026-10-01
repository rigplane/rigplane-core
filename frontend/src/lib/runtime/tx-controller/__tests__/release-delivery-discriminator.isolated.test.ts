import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ManagedTransmitDocument } from '$lib/types/managed-transmit';
import type { CommandDeliveryEvent, ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// Release-delivery discriminator (MOR-3096 software-only triage).
//
// Real pieces: ManagedTxController, managed browser dependencies
// (createManagedBrowserDependencies), the WsChannel control transport
// (including its offline `pendingPttRelease` retention/replay and epoch
// accounting), and the production gesture interpreter
// (createManagedTxGesture) with its real 300ms up-delay.
//
// Controlled inputs are exactly three: the WS socket boundary
// (MockWebSocket server frames), the canonical managed-transmit HTTP
// projection (`managed-transmit.svelte` store mock supplies explicitly
// chosen server observations, applied on the real refresh path), and local
// media (tx-adapter). No recording stub stands in for the delivery seam.
//
// This file discriminates the BROWSER delivery seam only: command IDs, wire
// order, delivery epochs, intents/outcomes, and the local/canonical release
// obligation as the browser actually projects it. The controlled canonical
// documents are supplied observations, NOT evidence of server-side RF
// authority or backend release obligations; the Python-side author checks
// that seam independently.

const h = vi.hoisted(() => ({
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
}));

const canonical = vi.hoisted(() => ({
  doc: null as ManagedTransmitDocument | null,
  stale: true,
  applied: 0,
  invalidations: 0,
}));

vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => canonical.doc,
  managedTransmitIsStale: () => canonical.stale,
  managedTransmitRemainingMs: () => null,
  managedTransmitAppliedRevision: () => canonical.applied,
  refreshManagedTransmit: vi.fn(async () => {
    if (canonical.doc !== null) { canonical.stale = false; canonical.applied += 1; }
  }),
  invalidateManagedTransmit: vi.fn(() => {
    canonical.invalidations += 1;
    canonical.stale = true;
  }),
  submitManagedTransmit: vi.fn(async () => 'accepted' as const),
  setManagedTransmitTot: vi.fn(async () => {}),
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getRadioState: () => null,
  setRadioState: () => {},
  patchActiveReceiver: () => {},
  patchRadioState: () => {},
  resetRadioState: () => {},
  isValidServerState: () => true,
  matchesCurrentCapabilityTopology: () => true,
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: () => null,
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
type GestureModule = typeof import('$lib/components-v2/wiring/managed-tx-gesture');
type Factory = ReturnType<BrowserDepsModule['createManagedBrowserDependencies']>;
type Controller = InstanceType<ControllerModule['ManagedTxController']>;
type Gesture = ReturnType<GestureModule['createManagedTxGesture']>;

type WireFrame = { type: string; name?: string; id?: string };

const rxIdleDocument = (): ManagedTransmitDocument => ({
  schemaVersion: 1,
  sampledAt: '2026-10-01T00:00:00Z',
  managedTransmit: {
    status: 'available',
    intent: { kind: 'rx' },
    releaseRequired: false,
    lastError: null,
    lastActuation: null,
    abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'off' },
});

const pttHeldDocument = (attemptId: string): ManagedTransmitDocument => ({
  schemaVersion: 1,
  sampledAt: '2026-10-01T00:00:01Z',
  managedTransmit: {
    status: 'available',
    intent: { kind: 'ptt', owner: 'operator' },
    releaseRequired: true,
    lastError: null,
    lastActuation: { operation: 'ptt_on', result: 'accepted', attemptId },
    abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: 'on' },
});

const framesOf = (socket: MockWebSocket): WireFrame[] =>
  socket.sent.map((raw) => JSON.parse(raw) as WireFrame);

const lastId = (socket: MockWebSocket): string => {
  const frame = framesOf(socket).at(-1);
  if (frame?.id === undefined) throw new Error('no command frame on socket');
  return frame.id;
};

const respond = (socket: MockWebSocket, id: string, ok: boolean, error?: string): void => {
  socket.simulateMessage(JSON.stringify(
    ok ? { type: 'response', id, ok: true } : { type: 'response', id, ok: false, error: error ?? 'rejected' },
  ));
};

const trace = (label: string, event: string, payload: Record<string, unknown>): void => {
  console.info(`MOR3096_DELIVERY ${label} ${event} ${JSON.stringify(payload)}`);
};

const flush = async () => { for (let i = 0; i < 6; i += 1) await Promise.resolve(); };

/** Boot the real control channel, browser dependencies, controller, and gesture. */
async function boot(): Promise<{
  wsClient: WsClientModule; factory: Factory; controller: Controller; gesture: Gesture;
  socket: MockWebSocket; deliveries: CommandDeliveryEvent[]; transitions: ControlSessionTransition[];
}> {
  const wsClient = (await import('$lib/transport/ws-client')) as WsClientModule;
  const connection = (await import('$lib/stores/connection.svelte')) as ConnectionModule;
  const browserDeps = (await import('../browser-dependencies')) as BrowserDepsModule;
  const controllerModule = (await import('../managed-controller')) as ControllerModule;
  const gestureModule = (await import('$lib/components-v2/wiring/managed-tx-gesture')) as GestureModule;
  const factory = browserDeps.createManagedBrowserDependencies();
  const controller = new controllerModule.ManagedTxController(factory.dependencies);
  const gesture = gestureModule.createManagedTxGesture(
    {
      latched: () => controller.snapshot().intent === 'latched',
      transmitAvailable: () => controller.snapshot().fresh,
    },
    {
      pttOn: () => controller.pttOn(),
      pttOff: () => { void controller.pttOff(); },
      transmitOn: () => controller.transmitOn(),
      forceOff: () => { void controller.forceOff(); },
    },
    {
      schedule: (callback: () => void, ms: number) => setTimeout(callback, ms),
      cancel: (handle: unknown) => clearTimeout(handle as ReturnType<typeof setTimeout>),
    },
  );
  const deliveries: CommandDeliveryEvent[] = [];
  wsClient.onCommandDelivery((event) => deliveries.push(event));
  const transitions: ControlSessionTransition[] = [];
  factory.subscribeSession((transition) => transitions.push(transition));
  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  return { wsClient, factory, controller, gesture, socket, deliveries, transitions };
}

const teardown = (parts: {
  wsClient: WsClientModule; factory: Factory; controller: Controller; gesture: Gesture;
}): void => {
  parts.gesture.destroy();
  parts.controller.dispose();
  parts.factory.dispose();
  parts.wsClient.disconnect();
};

describe('release-delivery discriminator — real controller + real browser dependencies + real controlled WS transport + real gesture', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    vi.useFakeTimers();
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    globalThis.WebSocket = MockWebSocket as unknown as typeof WebSocket;
    vi.resetModules();
    canonical.doc = rxIdleDocument();
    canonical.stale = false;
    canonical.applied = 0;
    canonical.invalidations = 0;
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.resetModules();
  });

  it('accepted ON then gesture up after 300ms: a fulfilled terminal response-error OFF is ignored as an outcome while the canonical release obligation stays visible', async () => {
    const parts = await boot();
    const { controller, gesture, socket, deliveries } = parts;
    try {
      gesture.down();
      await flush();
      const onId = lastId(socket);
      expect(framesOf(socket).map((f) => f.name)).toEqual(['ptt_on']);

      canonical.doc = pttHeldDocument(`srv-${onId}`);
      respond(socket, onId, true);
      await flush();
      expect(controller.snapshot()).toMatchObject({
        phase: 'active', intent: 'momentary', radioTx: 'on',
        txRisk: 'confirmed-on', fresh: true, releaseRequired: true,
      });

      gesture.up();
      vi.advanceTimersByTime(300);
      await flush();
      const offId = lastId(socket);
      expect(offId).not.toBe(onId);
      expect(framesOf(socket).map((f) => f.name)).toEqual(['ptt_on', 'ptt_off']);
      expect(h.stop).toHaveBeenCalledTimes(1);

      canonical.doc = pttHeldDocument(`srv-${offId}`);
      respond(socket, offId, false, 'release refused: tot guard');
      await flush();
      expect(canonical.invalidations).toBe(0);
      expect(controller.snapshot()).toMatchObject({
        phase: 'active', intent: 'momentary', radioTx: 'on',
        txRisk: 'confirmed-on', fresh: true, releaseRequired: true,
      });
      expect(deliveries).toContainEqual(
        expect.objectContaining({ commandId: offId, kind: 'response-error', originalEpoch: 1, eventEpoch: 1 }),
      );
      expect(framesOf(socket).map((f) => f.name)).toEqual(['ptt_on', 'ptt_off']);
      trace('rejected-off', 'settled', {
        onId, offId,
        wireOrder: framesOf(socket).map((f) => ({ name: f.name, id: f.id })),
        invalidations: canonical.invalidations,
        snapshot: controller.snapshot(),
      });
    } finally {
      teardown(parts);
    }
  });

  it('offline OFF then reconnect/new epoch: the OFF obligation survives offline intact and is replayed first on the recovered socket, ahead of other recovered traffic', async () => {
    const parts = await boot();
    const { controller, gesture, socket: socket0, deliveries, transitions, wsClient } = parts;
    try {
      gesture.down();
      await flush();
      const onId = lastId(socket0);
      canonical.doc = pttHeldDocument(`srv-${onId}`);
      respond(socket0, onId, true);
      await flush();
      expect(controller.snapshot()).toMatchObject({
        phase: 'active', releaseRequired: true, txRisk: 'confirmed-on', fresh: true,
      });

      socket0.simulateClose();
      gesture.up();
      vi.advanceTimersByTime(300);
      await flush();
      expect(framesOf(socket0).map((f) => f.name)).toEqual(['ptt_on']);
      expect(canonical.invalidations).toBe(0);
      expect(controller.snapshot()).toMatchObject({
        phase: 'active', releaseRequired: true, txRisk: 'confirmed-on', fresh: true,
      });
      expect(h.stop).toHaveBeenCalledTimes(1);

      wsClient.sendCommand('set_freq', { freq: 14_200_000 });
      vi.advanceTimersByTime(1_500);
      expect(instances.length).toBe(2);
      const socket1 = instances[1];
      socket1.simulateOpen();

      const replay = framesOf(socket1);
      expect(replay.map((f) => f.name)).toEqual(['ptt_off', 'set_freq']);
      expect(replay.some((f) => f.name === 'ptt_on')).toBe(false);
      const offId = replay[0]!.id!;
      const allFrames = [...framesOf(socket0), ...framesOf(socket1)];
      expect(allFrames.filter((f) => f.name === 'ptt_on')).toHaveLength(1);
      expect(allFrames.filter((f) => f.name === 'ptt_off')).toHaveLength(1);
      expect(deliveries).toContainEqual(
        expect.objectContaining({ commandId: offId, kind: 'transport-sent', originalEpoch: 1, eventEpoch: 2 }),
      );
      expect(transitions.at(-1)).toEqual({ state: 'connected', epoch: 2 });

      canonical.doc = rxIdleDocument();
      socket1.simulateMessage(JSON.stringify({ type: 'event', name: 'managed_transmit_changed' }));
      await flush();
      expect(controller.snapshot()).toMatchObject({
        phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fresh: true, releaseRequired: false,
      });
      expect([...framesOf(socket0), ...framesOf(socket1)].filter((f) => f.name === 'ptt_off')).toHaveLength(1);
      trace('offline-off-replay', 'released', {
        onId, offId,
        wireOrderSocket0: framesOf(socket0).map((f) => ({ name: f.name, id: f.id })),
        wireOrderSocket1: framesOf(socket1).map((f) => ({ name: f.name, id: f.id })),
        epoch: transitions.at(-1)?.epoch,
        invalidations: canonical.invalidations,
        snapshot: controller.snapshot(),
      });
    } finally {
      teardown(parts);
    }
  });

  it('lost terminal ON response after the server-side fake accepts in flight: the gesture discharge sends exactly one OFF on the new epoch even while the canonical projection owes nothing', async () => {
    const parts = await boot();
    const { controller, gesture, socket: socket0, deliveries, transitions } = parts;
    try {
      gesture.down();
      await flush();
      const onId = lastId(socket0);
      socket0.simulateMessage(JSON.stringify({ type: 'ack', id: onId }));
      socket0.simulateClose();
      expect(deliveries.filter((e) => e.commandId === onId).map((e) => e.kind))
        .toEqual(['transport-sent', 'ack']);
      expect(controller.snapshot()).toMatchObject({
        phase: 'idle', releaseRequired: false, txRisk: 'none', fresh: true,
      });

      vi.advanceTimersByTime(1_500);
      expect(instances.length).toBe(2);
      const socket1 = instances[1];
      socket1.simulateOpen();
      expect(framesOf(socket1)).toEqual([]);
      expect(transitions.at(-1)).toEqual({ state: 'connected', epoch: 2 });

      gesture.up();
      vi.advanceTimersByTime(300);
      await flush();
      const offFrames = framesOf(socket1);
      expect(offFrames.map((f) => f.name)).toEqual(['ptt_off']);
      const offId = offFrames[0]!.id!;
      expect(offId).not.toBe(onId);
      expect([...framesOf(socket0), ...framesOf(socket1)].filter((f) => f.name === 'ptt_on'))
        .toHaveLength(1);
      expect(deliveries).toContainEqual(
        expect.objectContaining({ commandId: offId, kind: 'transport-sent', originalEpoch: 2, eventEpoch: 2 }),
      );

      respond(socket1, offId, true);
      await flush();
      expect(canonical.invalidations).toBe(0);
      expect(controller.snapshot()).toMatchObject({
        phase: 'idle', intent: null, radioTx: 'off', txRisk: 'none', fresh: true, releaseRequired: false,
      });
      expect(h.stop).toHaveBeenCalledTimes(1);
      trace('lost-on-terminal', 'discharged', {
        onId, offId,
        wireOrderSocket0: framesOf(socket0).map((f) => ({ name: f.name, id: f.id })),
        wireOrderSocket1: framesOf(socket1).map((f) => ({ name: f.name, id: f.id })),
        onDeliveries: deliveries.filter((e) => e.commandId === onId).map((e) => e.kind),
        epoch: transitions.at(-1)?.epoch,
        snapshot: controller.snapshot(),
      });
    } finally {
      teardown(parts);
    }
  });
});
