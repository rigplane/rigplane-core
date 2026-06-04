import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReceiverState, ServerState } from '../../types/state';

// ─── End-to-end fidelity test: REAL ws-client → REAL radio.svelte store ──────
//
// Unlike the unit suite in ``ws-client.test.ts`` (which mocks
// ``../../stores/radio.svelte`` and re-implements the acceptance gate by
// hand), this file drives the genuine module graph end-to-end: the real
// control-channel ``_ctrl.onMessage`` handler feeds the real
// ``setRadioState`` gate in ``radio.svelte.ts``. No store stand-in. The
// assertions therefore only pass when the production
// ``liveMetadataAdvanced`` / ``metadataAdvanced`` gate runs for real
// (MOR-442 fidelity gap).
//
// ``connection.svelte`` is also left REAL — its setters are plain ``$state``
// assignments, harmless under jsdom.
//
// The ``fast`` vitest project runs with ``isolate: false`` and ws-client
// holds module-level singletons (``_ctrl``, ``_fullState``,
// ``_hasReceivedFullState``) while radio.svelte holds module-level
// revision trackers (``lastRevision`` etc.). We therefore reset the module
// graph per test (``vi.resetModules()`` in ``afterEach``) and dynamically
// import both modules inside each test for a clean slate — the proven
// pattern from the singleton block of ``ws-client.test.ts``.

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

// ─── Minimal WebSocket mock (shapes copied from ws-client.test.ts) ───────────

class MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  readyState = MockWebSocket.CONNECTING;
  binaryType = 'blob';
  url: string;
  sent: string[] = [];

  onopen: (() => void) | null = null;
  onmessage: ((e: MessageEvent) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    instances.push(this);
  }

  send(data: string) {
    this.sent.push(data);
  }

  close() {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  }

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: string | ArrayBuffer) {
    this.onmessage?.({ data } as MessageEvent);
  }
}

const instances: MockWebSocket[] = [];

// ─── Envelope/state fixtures (shapes copied from ws-client.test.ts) ──────────

function makeReceiver(overrides: Partial<ReceiverState> = {}): ReceiverState {
  return {
    freqHz: 14074000,
    mode: 'USB',
    filter: 1,
    dataMode: 0,
    sMeter: 0,
    att: 0,
    preamp: 0,
    nb: false,
    nr: false,
    afLevel: 128,
    rfGain: 128,
    squelch: 0,
    ...overrides,
  };
}

function makeState(
  overrides: Partial<ServerStateWithObservation> & {
    main?: Partial<ReceiverState>;
    sub?: Partial<ReceiverState>;
    connection?: Partial<ServerState['connection']>;
  } = {},
): ServerStateWithObservation {
  const { main, sub, connection, ...topLevel } = overrides;
  const revision = topLevel.stateRevision ?? topLevel.revision ?? 1;
  return {
    revision,
    stateRevision: revision,
    freshnessRevision: topLevel.freshnessRevision ?? 1,
    healthRevision: topLevel.healthRevision ?? 1,
    observationSeq: topLevel.observationSeq ?? revision,
    publicStateSeq: topLevel.publicStateSeq,
    updatedAt: '2026-06-03T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: makeReceiver(main),
    sub: makeReceiver({ freqHz: 7074000, ...sub }),
    connection: {
      rigConnected: true,
      radioReady: true,
      controlConnected: true,
      ...connection,
    },
    ...topLevel,
  };
}

function fullEnvelope(state: ServerStateWithObservation): Record<string, unknown> {
  return {
    type: 'full',
    data: state,
    revision: state.revision,
    stateRevision: state.stateRevision,
    freshnessRevision: state.freshnessRevision,
    healthRevision: state.healthRevision,
    observationSeq: state.observationSeq,
    publicStateSeq: state.publicStateSeq,
    transportSeq: state.transportSeq,
  };
}

function deltaEnvelope(
  state: ServerStateWithObservation,
  changed: Record<string, unknown>,
  removed: string[] = [],
): Record<string, unknown> {
  return {
    type: 'delta',
    changed,
    removed,
    revision: state.revision,
    stateRevision: state.stateRevision,
    freshnessRevision: state.freshnessRevision,
    healthRevision: state.healthRevision,
    observationSeq: state.observationSeq,
    publicStateSeq: state.publicStateSeq,
    transportSeq: state.transportSeq,
  };
}

function sendStateUpdate(socket: MockWebSocket, data: Record<string, unknown>): void {
  socket.simulateMessage(JSON.stringify({ type: 'state_update', data }));
}

// Fresh module graph per test so module-level singletons in both ws-client
// and radio.svelte start clean (``fast`` project runs ``isolate: false``).
async function loadModules() {
  const wsClient = await import('../ws-client');
  const store = await import('../../stores/radio.svelte');
  return { wsClient, store };
}

describe('ws-client → real radio store gate (integration)', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.resetModules();
  });

  it('applies a full envelope through the real store', async () => {
    const { wsClient, store } = await loadModules();

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({ revision: 5, stateRevision: 5, ptt: false })),
    );

    // Real store accepted the initial full snapshot (isInitial branch).
    const s = store.getRadioState() as ServerStateWithObservation | null;
    expect(s?.stateRevision).toBe(5);
    expect(s?.ptt).toBe(false);
    expect(store.getLastRevision()).toBe(5);
  });

  it('applies a delta with a higher stateRevision through the real store', async () => {
    const { wsClient, store } = await loadModules();

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({ revision: 5, stateRevision: 5, ptt: false })),
    );
    sendStateUpdate(
      instances[0],
      deltaEnvelope(makeState({ revision: 6, stateRevision: 6 }), { ptt: true }),
    );

    // Real store accepted via the semanticAdvanced branch (6 > 5).
    const s = store.getRadioState() as ServerStateWithObservation | null;
    expect(s?.revision).toBe(6);
    expect(s?.stateRevision).toBe(6);
    expect(s?.ptt).toBe(true);
    expect(store.getLastRevision()).toBe(6);
  });

  it('accepts a same-revision delta when observationSeq + fieldStatus advance', async () => {
    const { wsClient, store } = await loadModules();

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({
        revision: 5,
        stateRevision: 5,
        observationSeq: 1,
        fieldStatus: {
          'main.freqHz': {
            storePath: 'receiver.main.active.freq_mode.freq_hz',
            observed: true,
            freshness: 'fresh',
            availability: 'available',
            lastObservedMonotonic: 1,
            source: { provider: 'first' },
          },
        },
      })),
    );

    const nextFieldStatus = {
      'main.freqHz': {
        storePath: 'receiver.main.active.freq_mode.freq_hz',
        observed: true,
        freshness: 'fresh',
        availability: 'available',
        lastObservedMonotonic: 2,
        source: { provider: 'second' },
      },
    } as const;

    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        // Same stateRevision (5), newer observationSeq (2).
        makeState({ revision: 5, stateRevision: 5, observationSeq: 2 }),
        { fieldStatus: nextFieldStatus },
      ),
    );

    // This is the core MOR-442 regression class: equal semantic revision but
    // advancing observation metadata. Acceptance flows through the real
    // store's ``observationAdvanced`` → ``metadataAdvanced`` branch, NOT a mock.
    const s = store.getRadioState() as ServerStateWithObservation | null;
    expect(s?.stateRevision).toBe(5);
    expect(s?.observationSeq).toBe(2);
    expect(s?.fieldStatus?.['main.freqHz']).toEqual(nextFieldStatus['main.freqHz']);
  });

  it('rejects a stale delta even when observationSeq advances', async () => {
    const { wsClient, store } = await loadModules();

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(
      instances[0],
      fullEnvelope(makeState({ revision: 6, stateRevision: 6, observationSeq: 6, ptt: true })),
    );

    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        // Lower semantic revision (5 < 6) — stale despite higher observationSeq.
        makeState({ revision: 5, stateRevision: 5, observationSeq: 7, ptt: false }),
        { ptt: false },
      ),
    );

    // ws-client's ``isRevisionAcceptable`` rejects at the accumulator
    // (neither semanticAdvanced nor metadataAdvanced nor isReset), so
    // ``setRadioState`` is never invoked and the real store cannot regress.
    const s = store.getRadioState() as ServerStateWithObservation | null;
    expect(s?.stateRevision).toBe(6);
    expect(s?.observationSeq).toBe(6);
    expect(s?.ptt).toBe(true);
    expect(store.getLastRevision()).toBe(6);
  });
});
