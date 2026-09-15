import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import type { ReceiverState, ServerState } from '../../types/state';
import type { Capabilities } from '../../types/capabilities';
import { MockWebSocket, instances } from './support/fake-ws-backend';

const fetchCapabilities = vi.hoisted(() => vi.fn());
vi.mock('../http-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('../http-client')>(), fetchCapabilities }));

// ─── End-to-end fidelity test: REAL ws-client → REAL radio.svelte store ──────
//
// Unlike the unit suite in ``ws-client.isolated.test.ts`` (which mocks
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
// pattern from the singleton block of ``ws-client.isolated.test.ts``.

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

function makeCapabilities(
  providerGeneration = 0,
  overrides: Partial<Capabilities> = {},
): Capabilities {
  return {
    model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false }, txBands: null,
    stateContractVersion: 1, providerGeneration,
    ...overrides,
  };
}

// ─── Envelope/state fixtures (shapes copied from ws-client.isolated.test.ts) ──────────

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
  const { main, sub, connection, txTarget, ...topLevel } = overrides;
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
    stateContractVersion: 1,
    providerGeneration: 0,
    ...topLevel,
    txTarget: txTarget ?? { status: 'unknown', reason: 'not-observed' },
  };
}

function singleReceiverWireState(
  overrides: Partial<ServerStateWithObservation> = {},
): ServerStateWithObservation {
  const { sub: _sub, ...wireState } = makeState(overrides);
  return wireState as ServerStateWithObservation;
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
    stateContractVersion: state.stateContractVersion,
    providerGeneration: state.providerGeneration,
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
    stateContractVersion: state.stateContractVersion,
    providerGeneration: state.providerGeneration,
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
  const capabilities = await import('../../stores/capabilities.svelte');
  return { wsClient, store, capabilities };
}

describe('ws-client → real radio store gate (integration)', () => {
  let originalWebSocket: typeof WebSocket;

  beforeEach(() => {
    instances.length = 0;
    originalWebSocket = globalThis.WebSocket;
    // @ts-expect-error install the mock as the global WebSocket constructor
    globalThis.WebSocket = MockWebSocket;
    // Reset the module graph BEFORE each test, not only after. Under the
    // ``fast`` project (``isolate: false``) a sibling test file that imported
    // ``radio.svelte`` first leaves the real store singleton at a non-zero
    // revision in the shared module cache; without a pre-test reset the first
    // ``loadModules()`` here would pick up that stale singleton and the
    // revision-gate assertions drift (e.g. ``expected 6 to be 5``).
    vi.resetModules();
    fetchCapabilities.mockResolvedValue(makeCapabilities());
  });

  afterEach(() => {
    globalThis.WebSocket = originalWebSocket;
    vi.resetModules();
  });

  it('applies a full envelope through the real store', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());

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

  it('accepts canonical single-receiver full bodies with omitted or null sub', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], fullEnvelope(singleReceiverWireState({ revision: 1 })));
    expect(store.getRadioState()?.main.freqHz).toBe(14_074_000);

    const nullSub = { ...singleReceiverWireState({ revision: 2 }), sub: null } as unknown as ServerStateWithObservation;
    sendStateUpdate(instances[0], fullEnvelope(nullSub));
    expect(store.getRadioState()?.revision).toBe(2);
    expect(store.getRadioState()?.main.freqHz).toBe(14_074_000);
  });

  it('rejects a single-receiver active SUB full before it mutates browser truth', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    const connection = await import('../../stores/connection.svelte');
    const singleReceiverCaps = makeCapabilities(0, { receivers: 1, vfoScheme: 'ab' });
    fetchCapabilities.mockResolvedValue(singleReceiverCaps);
    capabilities.setCapabilities(singleReceiverCaps);
    const markStateUpdated = vi.spyOn(connection, 'markStateUpdated');
    const activeStatus = {
      active: { storePath: 'global.slow_state.active', observed: true, freshness: 'fresh', availability: 'available' },
    } as const;
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(singleReceiverWireState({ revision: 1, active: 'MAIN', fieldStatus: activeStatus })));
    const accepted = store.getRadioState();
    const acceptedCapabilities = capabilities.getCapabilities();
    const acceptedReady = connection.getRadioReady();
    markStateUpdated.mockClear();

    sendStateUpdate(instances[0], fullEnvelope(singleReceiverWireState({ revision: 2, active: 'SUB', fieldStatus: activeStatus })));
    expect(store.getRadioState()).toBe(accepted);
    expect(capabilities.getCapabilities()).toBe(acceptedCapabilities);
    expect(connection.getRadioReady()).toBe(acceptedReady);
    expect(markStateUpdated).not.toHaveBeenCalled();

    const dualReceiverCaps = makeCapabilities(0, { receivers: 2, vfoScheme: 'main_sub' });
    capabilities.setCapabilities(dualReceiverCaps);
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 2, active: 'SUB', fieldStatus: activeStatus })));
    expect(store.getRadioState()?.active).toBe('SUB');
  });

  it('applies a delta with a higher stateRevision through the real store', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());

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
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());

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

  it('keeps a retained relative tuple visible and applies a live leaf delta', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    const status = (at: number) => ({
      storePath: 'receiver.0.active.freq_mode.freq_hz',
      observed: true,
      freshness: 'fresh' as const,
      availability: 'available' as const,
      lastObservedMonotonic: at,
      source: { provider: 'icom_civ' },
    });
    const fieldStatus = {
      'main.freqHz': status(902_507),
      'main.mode': status(902_507),
      'main.unselectedVfo.freqHz': status(902_507),
      'main.unselectedVfo.mode': status(902_507),
    };
    const initial = makeState({
      revision: 5,
      stateRevision: 5,
      observationSeq: 8,
      main: makeReceiver({
        freqHz: 14_284_000,
        mode: 'USB',
        unselectedVfo: {
          freqHz: 14_075_000, mode: 'USB', filterNum: 1, dataMode: 0,
        },
      }),
      fieldStatus,
    });

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(initial));
    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({
          revision: 5, stateRevision: 5, freshnessRevision: 2,
          observationSeq: 8,
        }),
        { fieldStatus },
      ),
    );

    let current = store.getRadioState() as ServerStateWithObservation;
    expect(current.main.freqHz).toBe(14_284_000);
    expect(current.main.unselectedVfo?.freqHz).toBe(14_075_000);

    const liveMain = { ...current.main, freqHz: 14_285_000 };
    sendStateUpdate(
      instances[0],
      deltaEnvelope(
        makeState({ revision: 6, stateRevision: 6, observationSeq: 9 }),
        { main: liveMain, fieldStatus: { ...fieldStatus, 'main.freqHz': status(902_512) } },
      ),
    );

    current = store.getRadioState() as ServerStateWithObservation;
    expect(current.main.freqHz).toBe(14_285_000);
    expect(current.main.unselectedVfo?.freqHz).toBe(14_075_000);
  });

  it('rejects a stale delta even when observationSeq advances', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());

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

  it('rejects a same-generation 100-to-1 delta without changing radio or capability truth', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 100, ptt: false })));
    const accepted = store.getRadioState();
    const acceptedCapabilities = capabilities.getCapabilities();

    sendStateUpdate(
      instances[0],
      deltaEnvelope(makeState({ revision: 1, ptt: true }), { ptt: true }),
    );

    expect(store.getRadioState()).toBe(accepted);
    expect(store.getRadioState()?.revision).toBe(100);
    expect(store.getRadioState()?.ptt).toBe(false);
    expect(capabilities.getCapabilities()).toBe(acceptedCapabilities);
  });

  it('rejects incomplete full data and malformed changed records before they alter truth', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], {
      type: 'full', stateContractVersion: 1, providerGeneration: 0,
      revision: 1, data: { stateContractVersion: 1, providerGeneration: 0 },
    });
    expect(store.getRadioState()).toBeNull();

    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1, ptt: false })));
    const accepted = store.getRadioState();
    const acceptedCapabilities = capabilities.getCapabilities();
    sendStateUpdate(
      instances[0],
      deltaEnvelope(makeState({ revision: 2, ptt: true }), { main: 'corrupt' }),
    );

    expect(store.getRadioState()).toBe(accepted);
    expect(store.getRadioState()?.main.freqHz).toBe(14_074_000);
    expect(store.getRadioState()?.ptt).toBe(false);
    expect(capabilities.getCapabilities()).toBe(acceptedCapabilities);
  });

  it('rejects unversioned frames and a delta before a valid full without changing truth', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();

    sendStateUpdate(instances[0], makeState({ revision: 9 }) as unknown as Record<string, unknown>);
    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 9 }), { ptt: true }));

    expect(store.getRadioState()).toBeNull();
    expect(store.getLastRevision()).toBe(-1);
  });

  it('rejects wrong contract and unsafe generation before changing browser truth', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    const wrongContract = fullEnvelope(makeState({ revision: 4 }));
    wrongContract.stateContractVersion = 2;
    const unsafeGeneration = fullEnvelope(makeState({ revision: 5 }));
    unsafeGeneration.providerGeneration = Number.MAX_SAFE_INTEGER + 1;

    sendStateUpdate(instances[0], wrongContract);
    sendStateUpdate(instances[0], unsafeGeneration);

    expect(store.getRadioState()).toBeNull();
    expect(capabilities.getCapabilities()?.providerGeneration).toBe(0);
  });

  it('does not let a malformed delta erase or replace its established epoch', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 4 })));
    sendStateUpdate(
      instances[0],
      deltaEnvelope(makeState({ revision: 5 }), { ptt: true, providerGeneration: 1 }),
    );

    expect(store.getRadioState()?.providerGeneration).toBe(0);
    expect(store.getRadioState()?.ptt).toBe(false);
  });

  it('clears generation N on a higher delta and accepts only a matching later full', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities(0));
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 7, providerGeneration: 0 })));
    expect(store.getRadioState()?.revision).toBe(7);

    sendStateUpdate(instances[0], deltaEnvelope(makeState({ revision: 8, providerGeneration: 1 }), { ptt: true }));
    expect(store.getRadioState()).toBeNull();
    expect(capabilities.getCapabilities()).toBeNull();

    capabilities.setCapabilities(makeCapabilities(1));
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1, providerGeneration: 1, ptt: true })));
    expect(store.getRadioState()?.providerGeneration).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
  });

  it('does not install a delayed capability response for an older provider generation', async () => {
    let resolveOld: ((value: ReturnType<typeof makeCapabilities>) => void) | undefined;
    let resolveNew: ((value: ReturnType<typeof makeCapabilities>) => void) | undefined;
    fetchCapabilities
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOld = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNew = resolve; }));
    const { wsClient, store, capabilities } = await loadModules();
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 2, providerGeneration: 2 })));

    resolveOld!(makeCapabilities(1));
    await Promise.resolve();
    await Promise.resolve();
    expect(capabilities.getCapabilities()).toBeNull();

    resolveNew!(makeCapabilities(2));
    await Promise.resolve();
    await Promise.resolve();
    expect(capabilities.getCapabilities()?.providerGeneration).toBe(2);
    expect(store.getRadioState()?.providerGeneration).toBe(2);
  });

  it('rejects an old epoch in-session but accepts a lower epoch after disconnect', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities(2));
    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 4, providerGeneration: 2 })));
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 99, providerGeneration: 1, ptt: true })));
    expect(store.getRadioState()?.providerGeneration).toBe(2);
    expect(store.getRadioState()?.ptt).toBe(false);

    wsClient.disconnect();
    capabilities.setCapabilities(makeCapabilities(1));
    wsClient.connect('ws://test/api/v1/ws');
    instances[1].simulateOpen();
    sendStateUpdate(instances[1], fullEnvelope(makeState({ revision: 1, providerGeneration: 1, ptt: true })));
    expect(store.getRadioState()?.providerGeneration).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
  });

  // ─── MOR-1419: server-link chip honesty ────────────────────────────────
  //
  // The StatusBar server-link chip (and the `connectionStatus`/`isConnected`
  // it shares with the control-link indicator + connect/disconnect toggle)
  // used to read an orphaned `httpConnected` store field. Its only real
  // producer was deleted in the A10 HTTP-polling retirement (#2362); the
  // sole remaining producer was `setHttpConnected(true)` inside the
  // `state_update` handler, gated on `applyDeltaEnvelope` returning a
  // committed state. On cold start, `commitCurrentState()` fails until
  // capabilities resolve (`capabilitiesMatchGeneration` false), so that
  // gate silently drops the very first `state_update` — and a quiet radio
  // that never sends a second one left the chip red forever despite a
  // fully healthy WS link. These tests drive the real `ws-client` against
  // the real `connection.svelte` store (this file's whole point) to prove
  // the derived signal no longer depends on that race.
  it('reports connected once the WS session is up, even while capabilities are still pending (cold-start race)', async () => {
    let resolveCaps: ((value: ReturnType<typeof makeCapabilities>) => void) | undefined;
    fetchCapabilities.mockImplementationOnce(() => new Promise((resolve) => { resolveCaps = resolve; }));
    const { wsClient, store } = await loadModules();
    const connection = await import('../../stores/connection.svelte');

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1 })));

    // Capabilities are still unresolved — the full envelope could not be
    // committed yet (the exact cold-start race).
    expect(store.getRadioState()).toBeNull();
    // But the honest live source (the WS transport itself) is already up,
    // so the server-link chip must not be stuck red.
    expect(connection.getWsConnected()).toBe(true);
    expect(connection.getConnectionStatus()).toBe('connected');
    expect(connection.isConnected()).toBe(true);

    // Capabilities resolving later still lets the state commit retroactively.
    resolveCaps!(makeCapabilities());
    await Promise.resolve();
    await Promise.resolve();
    expect(store.getRadioState()?.revision).toBe(1);
    expect(connection.getConnectionStatus()).toBe('connected');
  });

  it('reports disconnected immediately on a real WS drop, even after a prior committed state', async () => {
    const { wsClient, store, capabilities } = await loadModules();
    capabilities.setCapabilities(makeCapabilities());
    const connection = await import('../../stores/connection.svelte');

    wsClient.connect('ws://test/api/v1/ws');
    instances[0].simulateOpen();
    sendStateUpdate(instances[0], fullEnvelope(makeState({ revision: 1 })));
    expect(store.getRadioState()?.revision).toBe(1);
    expect(connection.getConnectionStatus()).toBe('connected');

    instances[0].simulateClose();

    expect(connection.getWsConnected()).toBe(false);
    expect(connection.getConnectionStatus()).toBe('disconnected');
    expect(connection.isConnected()).toBe(false);
  });

  describe('capability fetch retry', () => {
    let modules: Awaited<ReturnType<typeof loadModules>>;
    beforeEach(async () => {
      vi.useFakeTimers();
      vi.spyOn(Math, 'random').mockReturnValue(0.5);
      fetchCapabilities.mockReset();
      modules = await loadModules();
      modules.wsClient.connect('ws://test/api/v1/ws');
      instances[0].simulateOpen();
    });
    afterEach(() => {
      modules.wsClient.disconnect();
      vi.clearAllTimers();
      vi.useRealTimers();
      vi.restoreAllMocks();
    });

    it('publishes the latest buffered state after a failed GET retries in the same session', async () => {
      fetchCapabilities.mockRejectedValueOnce(new Error('HTTP 503'))
        .mockResolvedValue(makeCapabilities(1));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      await vi.advanceTimersByTimeAsync(0);
      expect(modules.store.getRadioState()).toBeNull();
      const session = modules.wsClient.getControlSession();
      for (let revision = 2; revision <= 20; revision++) {
        sendStateUpdate(instances[0], deltaEnvelope(makeState({ providerGeneration: 1, revision }),
          { main: makeReceiver({ freqHz: 14_075_000 + revision }) }));
      }
      await vi.advanceTimersByTimeAsync(1000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(2);
      expect(modules.store.getRadioState()).toMatchObject({ providerGeneration: 1, revision: 20,
        main: { freqHz: 14_075_020 } });
      expect(modules.wsClient.getControlSession()).toEqual(session);
      expect(instances).toHaveLength(1);
    });

    it('bounds repeated failures with backoff even while full frames keep arriving', async () => {
      fetchCapabilities.mockRejectedValue(new Error('HTTP 503'));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      await vi.advanceTimersByTimeAsync(0);
      let revision = 1;
      let expectedRequests = 1;
      for (const delay of [1000, 2000, 4000, 8000, 16000, 30000, 30000]) {
        for (let index = 0; index < 20; index++) {
          sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1, revision: ++revision })));
        }
        await vi.advanceTimersByTimeAsync(delay - 1);
        expect(fetchCapabilities).toHaveBeenCalledTimes(expectedRequests);
        await vi.advanceTimersByTimeAsync(1);
        expect(fetchCapabilities).toHaveBeenCalledTimes(++expectedRequests);
      }
      expect(modules.store.getRadioState()).toBeNull();
      expect(instances).toHaveLength(1);
    });

    it('deduplicates an in-flight fetch and stops retrying after matching success', async () => {
      let resolve!: (caps: Capabilities) => void;
      fetchCapabilities.mockImplementation(() => new Promise<Capabilities>(done => { resolve = done; }));
      for (let revision = 1; revision <= 20; revision++) {
        sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1, revision })));
      }
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(1);
      resolve(makeCapabilities(1));
      await vi.advanceTimersByTimeAsync(0);
      expect(modules.store.getRadioState()?.revision).toBe(20);
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1, revision: 21 })));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(1);
      expect(modules.store.getRadioState()?.revision).toBe(21);
    });

    it('retries a mismatched capabilities response without publishing it', async () => {
      fetchCapabilities.mockResolvedValueOnce(makeCapabilities(2)).mockResolvedValue(makeCapabilities(1));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      await vi.advanceTimersByTimeAsync(0);
      expect(modules.capabilities.getCapabilities()).toBeNull();
      expect(modules.store.getRadioState()).toBeNull();
      await vi.advanceTimersByTimeAsync(1000);
      expect(modules.store.getRadioState()?.providerGeneration).toBe(1);
    });

    it('discards a late response from a replaced provider generation', async () => {
      let resolveOld!: (caps: Capabilities) => void;
      fetchCapabilities.mockImplementationOnce(() => new Promise<Capabilities>(done => { resolveOld = done; }))
        .mockResolvedValue(makeCapabilities(2));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 2, revision: 2 })));
      await vi.advanceTimersByTimeAsync(0);
      resolveOld(makeCapabilities(1));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(2);
      expect(modules.capabilities.getCapabilities()?.providerGeneration).toBe(2);
      expect(modules.store.getRadioState()).toMatchObject({ providerGeneration: 2, revision: 2 });
    });

    it('cancels old generation backoff and starts the new generation immediately', async () => {
      fetchCapabilities.mockRejectedValueOnce(new Error('HTTP 503')).mockResolvedValue(makeCapabilities(2));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      await vi.advanceTimersByTimeAsync(0);
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 2 })));
      await vi.advanceTimersByTimeAsync(0);
      expect(modules.store.getRadioState()?.providerGeneration).toBe(2);
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(2);
    });

    it('discards an old same-generation session response after reconnect', async () => {
      let resolveOld!: (caps: Capabilities) => void;
      fetchCapabilities.mockImplementationOnce(() => new Promise<Capabilities>(done => { resolveOld = done; }))
        .mockResolvedValue(makeCapabilities(1, { model: 'NEW' }));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      instances[0].simulateClose();
      await vi.advanceTimersByTimeAsync(1000);
      instances[1].simulateOpen();
      sendStateUpdate(instances[1], fullEnvelope(makeState({ providerGeneration: 1, revision: 2 })));
      await vi.advanceTimersByTimeAsync(0);
      resolveOld(makeCapabilities(1, { model: 'OLD' }));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(modules.capabilities.getCapabilities()?.model).toBe('NEW');
      expect(modules.store.getRadioState()?.revision).toBe(2);
      expect(fetchCapabilities).toHaveBeenCalledTimes(2);
    });

    it.each(['explicit stop', 'socket close'] as const)('cancels retry backoff on %s', async (ending) => {
      fetchCapabilities.mockRejectedValue(new Error('HTTP 503'));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      await vi.advanceTimersByTimeAsync(0);
      if (ending === 'explicit stop') modules.wsClient.disconnect();
      else instances[0].simulateClose();
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(1);
      expect(modules.store.getRadioState()).toBeNull();
      expect(modules.capabilities.getCapabilities()).toBeNull();
    });

    it.each(['resolve', 'reject'] as const)('ignores a late fetch %s after stop', async (settlement) => {
      let resolve!: (caps: Capabilities) => void;
      let reject!: (reason: Error) => void;
      fetchCapabilities.mockImplementation(() => new Promise<Capabilities>((done, fail) => { resolve = done; reject = fail; }));
      sendStateUpdate(instances[0], fullEnvelope(makeState({ providerGeneration: 1 })));
      modules.wsClient.disconnect();
      if (settlement === 'resolve') resolve(makeCapabilities(1));
      else reject(new Error('late HTTP 503'));
      await vi.advanceTimersByTimeAsync(60_000);
      expect(fetchCapabilities).toHaveBeenCalledTimes(1);
      expect(modules.store.getRadioState()).toBeNull();
      expect(modules.capabilities.getCapabilities()).toBeNull();
    });
  });

});
