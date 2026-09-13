import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import { MockWebSocket, instances } from '$lib/transport/__tests__/support/fake-ws-backend';

// MOR-2463: server-side RX/TOT must stop the browser-local mic through the
// canonical HTTP snapshot — never by issuing extra TX commands. The real
// `WsChannel` singleton (driven only at the socket boundary by the shared
// `MockWebSocket` fake), the real `createManagedBrowserDependencies()` and a
// real `ManagedTxController` are joined. The managed-transmit store is
// mocked, faithfully: a refresh applies the current `h.document` and bumps
// `h.appliedRevision` only when it is not superseded or failed. Media runs
// through the mocked tx-adapter. `vi.resetModules()` runs every `it()`
// because `ws-client` holds module-level singletons.

const h = vi.hoisted(() => ({
  radio: null as any,
  caps: null as any,
  start: vi.fn(async (): Promise<string | null> => null),
  stop: vi.fn(),
  stale: true,
  document: null as any,
  appliedRevision: 0,
  supersedeRefresh: 0,
  deferRefresh: false,
  refreshQueue: [] as Array<() => void>,
  refreshError: null as Error | null,
  submit: vi.fn(async (_operation: string) => 'accepted' as const),
}));
vi.mock('$lib/stores/managed-transmit.svelte', () => ({
  managedTransmitSnapshot: () => h.document,
  managedTransmitIsStale: () => h.stale,
  managedTransmitRemainingMs: () => null,
  managedTransmitAppliedRevision: () => h.appliedRevision,
  refreshManagedTransmit: () => {
    if (h.refreshError !== null) return Promise.reject(h.refreshError);
    if (h.supersedeRefresh > 0) {
      h.supersedeRefresh -= 1;
      return Promise.resolve();
    }
    const apply = () => {
      h.stale = false;
      h.appliedRevision += 1;
    };
    if (!h.deferRefresh) {
      apply();
      return Promise.resolve();
    }
    return new Promise<void>((resolve) => {
      h.refreshQueue.push(() => {
        apply();
        resolve();
      });
    });
  },
  invalidateManagedTransmit: () => {
    h.stale = true;
  },
  submitManagedTransmit: h.submit,
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
  lastObservedMonotonic: at, source: { source: 'poll_response' as const },
});

function resetFacts(): void {
  h.radio = {
    revision: 1, ptt: false, active: 'MAIN', txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 150 },
    fieldStatus: { ptt: field(1), txTarget: field(1) },
  };
  h.caps = {
    tx: true, audioTx: true, capabilities: ['tx'],
    vfoScheme: 'main_sub', txBands: [{ start: 100, end: 200 }],
  } as Capabilities;
}

const rxDocument = (overrides: { observedPtt?: 'off' | 'on' | 'unknown'; releaseRequired?: boolean } = {}) => ({
  schemaVersion: 1 as const,
  sampledAt: '2026-09-13T00:00:00.000Z',
  managedTransmit: {
    status: 'available' as const,
    intent: { kind: 'rx' as const },
    releaseRequired: overrides.releaseRequired ?? false,
    lastError: null,
    lastActuation: null,
    abortErrors: [],
    tot: { configuredSeconds: 180, active: false, remainingMs: null, expiresAt: null },
  },
  txObservation: { observedPtt: overrides.observedPtt ?? 'off' },
});

const activeDocument = (intent: 'ptt' | 'transmit') => ({
  schemaVersion: 1 as const,
  sampledAt: '2026-09-13T00:00:00.000Z',
  managedTransmit: {
    status: 'available' as const,
    intent: intent === 'ptt' ? { kind: 'ptt' as const, owner: 'session-1' } : { kind: 'transmit' as const },
    releaseRequired: true,
    lastError: null,
    lastActuation: {
      operation: intent === 'ptt' ? ('ptt_on' as const) : ('transmit_on' as const),
      result: 'accepted' as const,
      attemptId: '1',
    },
    abortErrors: [],
    tot: { configuredSeconds: 180, active: true, remainingMs: 150_000, expiresAt: null },
  },
  txObservation: { observedPtt: 'on' },
});

const settle = async (turns = 12) => {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
};

async function loadStack() {
  const wsClient = (await import('$lib/transport/ws-client')) as WsClientModule;
  const connection = (await import('$lib/stores/connection.svelte')) as ConnectionModule;
  const browserDeps = (await import('../browser-dependencies')) as BrowserDepsModule;
  const controllerModule = (await import('../managed-controller')) as ControllerModule;
  return { wsClient, connection, browserDeps, controllerModule };
}

const answered = new Set<string>();

async function setup(): Promise<{
  wsClient: WsClientModule;
  factory: Factory;
  controller: Controller;
  socket: MockWebSocket;
  sessions: (transition: ControlSessionTransition) => void;
}> {
  const { wsClient, connection, browserDeps, controllerModule } = await loadStack();
  const factory = browserDeps.createManagedBrowserDependencies();
  const controller = new controllerModule.ManagedTxController(factory.dependencies);
  const sessions: Array<(transition: ControlSessionTransition) => void> = [];
  let sessionBoundary = 0;
  factory.subscribeSession((transition) => {
    for (const handler of sessions) handler(transition);
    const boundary = ++sessionBoundary;
    if (transition.state === 'connected') {
      void controller.refresh();
    } else {
      controller.invalidate();
      void controller.releaseSession().finally(() => {
        if (sessionBoundary === boundary) controller.abandonSession();
      });
    }
  });
  connection.setRadioReady(true);
  wsClient.connect('ws://test/api/v1/ws');
  const socket = instances[0];
  socket.simulateOpen();
  await settle();
  return { wsClient, factory, controller, socket, sessions: (t) => sessions.forEach((fn) => fn(t)) };
}

const sentFrames = (socket: MockWebSocket) => socket.sent.map((raw) => JSON.parse(raw));
const countFrames = (name: string, socket: MockWebSocket) =>
  sentFrames(socket).filter((frame) => frame.name === name).length;

/** Answer the oldest unanswered `name` command frame with a terminal response. */
const replyOk = (name: string, socket: MockWebSocket) => {
  const frame = sentFrames(socket).find(
    (item) => item.name === name && typeof item.id === 'string' && !answered.has(item.id),
  );
  if (!frame) throw new Error(`no unanswered ${name} frame to answer`);
  answered.add(frame.id);
  socket.simulateMessage(JSON.stringify({ type: 'response', id: frame.id, ok: true }));
};

const signalInvalidation = (socket: MockWebSocket) => {
  socket.simulateMessage(JSON.stringify({ type: 'event', name: 'managed_transmit_changed', data: {} }));
};

describe('server RX/TOT local audio cleanup — real controller + real browser dependencies (MOR-2463)', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    answered.clear();
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    vi.resetModules();
    resetFacts();
    h.stale = true;
    h.document = rxDocument();
    h.appliedRevision = 0;
    h.supersedeRefresh = 0;
    h.deferRefresh = false;
    h.refreshQueue = [];
    h.refreshError = null;
    h.start.mockReset().mockResolvedValue(null);
    h.stop.mockClear();
    h.submit.mockClear();
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.resetModules();
  });

  it('cleans up a held PTT mic on post-TOT RX and sends no redundant release', async () => {
    const { wsClient, factory, controller, socket } = await setup();

    h.document = activeDocument('ptt');
    controller.pttOn();
    await settle();
    expect(countFrames('ptt_on', socket)).toBe(1);
    replyOk('ptt_on', socket);
    await settle();
    expect(h.start.mock.calls.length).toBe(1);
    expect(h.stop).not.toHaveBeenCalled();

    signalInvalidation(socket);
    await settle();
    expect(h.stop).not.toHaveBeenCalled();

    h.document = rxDocument();
    signalInvalidation(socket);
    await settle();
    expect(h.stop.mock.calls.length).toBe(1);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle', intent: null, fresh: true });

    const stops = h.stop.mock.calls.length;
    await controller.pttOff();
    await settle();
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.stop.mock.calls.length).toBe(stops);
    expect(h.submit).not.toHaveBeenCalled();

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });

  it('cannot clean up from a refresh begun before ON completion, only a fresh post-accept snapshot', async () => {
    const { wsClient, factory, controller, socket } = await setup();

    h.deferRefresh = true;
    void controller.refresh();
    h.document = activeDocument('ptt');
    controller.pttOn();
    await settle();
    replyOk('ptt_on', socket);
    await settle();
    expect(h.refreshQueue.length).toBe(3);

    h.document = rxDocument();
    h.refreshQueue.shift()?.();
    await settle();
    expect(h.stop).not.toHaveBeenCalled();

    h.refreshQueue.shift()?.();
    await settle();
    expect(h.stop).not.toHaveBeenCalled();

    h.refreshQueue.shift()?.();
    await settle();
    expect(h.stop.mock.calls.length).toBe(1);
    expect(controller.snapshot()).toMatchObject({ phase: 'idle' });

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });

  it('ignores a superseded post-ON refresh and cleans up on the next applied snapshot', async () => {
    const { wsClient, factory, controller, socket } = await setup();
    const appliedAtStart = h.appliedRevision;

    h.supersedeRefresh = 2;
    controller.pttOn();
    await settle();
    replyOk('ptt_on', socket);
    await settle();
    expect(h.appliedRevision).toBe(appliedAtStart);
    expect(h.stop).not.toHaveBeenCalled();

    signalInvalidation(socket);
    await settle();
    expect(h.appliedRevision).toBe(appliedAtStart + 1);
    expect(h.stop.mock.calls.length).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.submit).not.toHaveBeenCalled();

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });

  it('cleans up latched transmit through the HTTP projection without replaying commands', async () => {
    const { wsClient, factory, controller, socket } = await setup();

    h.document = activeDocument('transmit');
    expect(controller.snapshot().fresh).toBe(true);
    controller.transmitOn();
    await settle();
    expect(h.start.mock.calls.length).toBe(1);
    expect(countFrames('ptt_off', socket)).toBe(0);
    expect(h.submit).toHaveBeenCalledExactlyOnceWith('transmit_on');
    expect(h.stop).not.toHaveBeenCalled();

    h.document = rxDocument({ observedPtt: 'unknown', releaseRequired: true });
    signalInvalidation(socket);
    await settle();
    expect(h.stop.mock.calls.length).toBe(1);
    expect(h.submit.mock.calls).toEqual([['transmit_on']]);
    expect(sentFrames(socket).filter((frame) => typeof frame.name === 'string' && frame.name.startsWith('ptt'))).toEqual([]);
    expect(controller.snapshot()).toMatchObject({ phase: 'releasing', intent: null, fresh: true, radioTx: 'unknown' });

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });

  it('keeps the mic running when the snapshot refresh fails or the authority is unavailable', async () => {
    const { wsClient, factory, controller, socket } = await setup();

    h.document = activeDocument('ptt');
    controller.pttOn();
    await settle();
    replyOk('ptt_on', socket);
    await settle();

    h.refreshError = new Error('snapshot endpoint failed');
    signalInvalidation(socket);
    await settle();
    expect(h.stop).not.toHaveBeenCalled();
    expect(controller.snapshot().fresh).toBe(false);
    h.refreshError = null;
    h.document = {
      schemaVersion: 1,
      sampledAt: '2026-09-13T00:00:00.000Z',
      managedTransmit: { status: 'unavailable', reason: 'authority_not_composed' },
      txObservation: { observedPtt: 'unknown' },
    };
    signalInvalidation(socket);
    await settle();
    expect(h.stop).not.toHaveBeenCalled();
    expect(controller.snapshot().fresh).toBe(false);

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });

  it('keeps MOR-2343 disconnect release intact and ignores late signals after abandon', async () => {
    const { wsClient, factory, controller, socket, sessions } = await setup();

    h.document = activeDocument('ptt');
    controller.pttOn();
    await settle();
    replyOk('ptt_on', socket);
    await settle();
    expect(h.start.mock.calls.length).toBe(1);

    socket.simulateClose();
    await settle();
    expect(h.stop.mock.calls.length).toBe(1);
    expect(controller.snapshot().fresh).toBe(false);
    const stops = h.stop.mock.calls.length;
    const starts = h.start.mock.calls.length;

    h.document = rxDocument();
    h.stale = false;
    sessions({ state: 'connected', epoch: 2 });
    await settle();
    expect(h.stop.mock.calls.length).toBe(stops);
    expect(h.start.mock.calls.length).toBe(starts);
    expect(sentFrames(socket).filter((frame) => frame.name === 'ptt_off')).toEqual([]);
    expect(h.submit).not.toHaveBeenCalled();

    controller.dispose();
    factory.dispose();
    wsClient.disconnect();
  });
});
