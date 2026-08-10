import { describe, it, expect, beforeEach, vi } from 'vitest';
import type { ServerState } from '../../types/state';

type ServerStateWithObservation = ServerState & {
  observationSeq?: number;
  publicStateSeq?: number;
  fieldStatus?: Record<string, unknown>;
};

function makeState(overrides: Partial<ServerStateWithObservation> = {}): ServerStateWithObservation {
  const revision = overrides.stateRevision ?? overrides.revision ?? 1;
  const freshnessRevision = overrides.freshnessRevision ?? 1;
  const {
    txTarget = { status: 'unknown', reason: 'not-observed' },
    ...stateOverrides
  } = overrides;
  return {
    revision,
    stateRevision: revision,
    freshnessRevision,
    observationSeq: overrides.observationSeq ?? revision,
    updatedAt: '2026-03-07T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14074000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 50,
      att: 0,
      preamp: 1,
      nb: false,
      nr: false,
      afLevel: 100,
      rfGain: 255,
      squelch: 0,
      digisel: false,
      ipplus: false,
      sMeterSqlOpen: true,
      agc: 3,
      audioPeakFilter: 0,
      autoNotch: false,
      manualNotch: false,
      twinPeakFilter: false,
      filterShape: 0,
      agcTimeConstant: 13,
      apfTypeLevel: 0,
      nrLevel: 0,
      pbtInner: 0,
      pbtOuter: 0,
      nbLevel: 0,
      digiselShift: 0,
      afMute: false,
    },
    sub: {
      freqHz: 7100000,
      mode: 'LSB',
      filter: 2,
      dataMode: 0,
      sMeter: 20,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 80,
      rfGain: 255,
      squelch: 0,
      digisel: false,
      ipplus: false,
      sMeterSqlOpen: false,
      agc: 0,
      audioPeakFilter: 0,
      autoNotch: false,
      manualNotch: false,
      twinPeakFilter: false,
      filterShape: 0,
      agcTimeConstant: 13,
      apfTypeLevel: 0,
      nrLevel: 0,
      pbtInner: 0,
      pbtOuter: 0,
      nbLevel: 0,
      digiselShift: 0,
      afMute: false,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    wsClients: { scope: 0, control: 1, audio: 0 },
    powerLevel: 255,
    scanning: false,
    tuningStep: 0,
    overflow: false,
    txFreqMonitor: false,
    ritFreq: 0,
    ritOn: false,
    ritTx: false,
    compMeter: 0,
    vdMeter: 0,
    idMeter: 0,
    cwPitch: 0,
    micGain: 0,
    keySpeed: 0,
    notchFilter: 0,
    mainSubTracking: false,
    compressorOn: false,
    compressorLevel: 0,
    monitorOn: false,
    breakInDelay: 0,
    breakIn: 0,
    dialLock: false,
    driveGain: 0,
    monitorGain: 0,
    voxOn: false,
    voxGain: 0,
    antiVoxGain: 0,
    ssbTxBandwidth: 0,
    refAdjust: 0,
    dashRatio: 0,
    nbDepth: 0,
    nbWidth: 0,
    scopeControls: {
      receiver: 0,
      dual: false,
      mode: 0,
      span: 0,
      edge: 0,
      hold: false,
      refDb: 0,
      speed: 0,
      duringTx: false,
      centerType: 0,
      vbwNarrow: false,
      rbw: 0,
      fixedEdge: { rangeIndex: 0, edge: 0, startHz: 0, endHz: 0 },
    },
    txTarget,
    stateContractVersion: 1,
    providerGeneration: 0,
    ...stateOverrides,
  };
}

function singleReceiverWireState(
  overrides: Partial<ServerStateWithObservation> = {},
): ServerStateWithObservation {
  const { sub: _sub, ...wireState } = makeState(overrides);
  return wireState as ServerStateWithObservation;
}

async function useDualReceiverCapabilities(): Promise<void> {
  const capabilities = await import('../capabilities.svelte');
  capabilities.setCapabilities({
    ...capabilities.getCapabilities()!, receivers: 2, vfoScheme: 'main_sub',
  });
}

describe('radio store', () => {
  let store: typeof import('../radio.svelte');

  beforeEach(async () => {
    vi.resetModules();
    store = await import('../radio.svelte');
    const capabilities = await import('../capabilities.svelte');
    capabilities.setCapabilities({
      model: 'TEST', scope: false, audio: false, tx: false, capabilities: [],
      receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
      audioConfig: { sampleRate: 48_000, channels: 1, codecs: [] },
      webrtc: { available: false, enabled: false }, txBands: null,
      stateContractVersion: 1, providerGeneration: 0,
    });
  });

  it('starts with null state', () => {
    expect(store.getRadioState()).toBeNull();
  });

  it('sets state and reads it back', () => {
    const s = makeState({ revision: 1 });
    store.setRadioState(s);
    expect(store.getRadioState()).toStrictEqual(s);
  });

  it('accepts canonical single-receiver wire state with omitted or null sub through the legacy HTTP writer', () => {
    const omitted = singleReceiverWireState({ revision: 1 });
    expect(store.isValidServerState(omitted)).toBe(true);
    expect(store.setRadioState(omitted)).toBe(true);
    expect(store.getRadioState()?.main.freqHz).toBe(14_074_000);

    const nullSub = { ...singleReceiverWireState({ revision: 2 }), sub: null } as unknown as ServerStateWithObservation;
    expect(store.isValidServerState(nullSub)).toBe(true);
    expect(store.setRadioState(nullSub)).toBe(true);
    expect(store.getRadioState()?.main.freqHz).toBe(14_074_000);
  });

  it('still rejects metadata-only and malformed single-receiver wire bodies', () => {
    expect(store.isValidServerState({ stateContractVersion: 1, providerGeneration: 0 })).toBe(false);
    expect(store.isValidServerState({ ...singleReceiverWireState(), main: 'corrupt' })).toBe(false);
  });

  it('rejects a single-receiver active SUB before it can turn the structural alias into truth', async () => {
    const capabilities = await import('../capabilities.svelte');
    const connection = await import('../connection.svelte');
    capabilities.setCapabilities({
      ...capabilities.getCapabilities()!, receivers: 1, vfoScheme: 'ab',
    });
    const activeStatus = {
      active: { storePath: 'global.slow_state.active', observed: true, freshness: 'fresh', availability: 'available' },
    } as const;
    expect(store.setRadioState(singleReceiverWireState({ revision: 1, active: 'MAIN', fieldStatus: activeStatus }))).toBe(true);
    const accepted = store.getRadioState();
    const acceptedCapabilities = capabilities.getCapabilities();
    const acceptedReady = connection.getRadioReady();

    expect(store.setRadioState(singleReceiverWireState({ revision: 2, active: 'SUB', fieldStatus: activeStatus }))).toBe(false);
    expect(store.getRadioState()).toBe(accepted);
    expect(capabilities.getCapabilities()).toBe(acceptedCapabilities);
    expect(connection.getRadioReady()).toBe(acceptedReady);

    capabilities.setCapabilities({ ...acceptedCapabilities!, receivers: 2, vfoScheme: 'main_sub' });
    expect(store.setRadioState(makeState({ revision: 2, active: 'SUB', fieldStatus: activeStatus }))).toBe(true);
    expect(store.getRadioState()?.active).toBe('SUB');
  });

  it('keeps single-receiver selected and unselected A/B facts under physical MAIN', async () => {
    const capabilities = await import('../capabilities.svelte');
    capabilities.setCapabilities({
      ...capabilities.getCapabilities()!, receivers: 1, vfoScheme: 'ab',
    });
    const main = {
      ...makeState().main,
      activeSlot: 'A' as const,
      vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 1, dataMode: 0 },
      vfoB: { freqHz: 14_076_000, mode: 'USB', filterNum: 1, dataMode: 0 },
      unselectedVfo: { freqHz: 14_076_000, mode: 'USB', filterNum: 1, dataMode: 0 },
    };
    expect(store.setRadioState(singleReceiverWireState({ revision: 1, active: 'MAIN', main }))).toBe(true);
    expect(store.getRadioState()?.main.vfoA?.freqHz).toBe(14_074_000);
    expect(store.getRadioState()?.main.unselectedVfo?.freqHz).toBe(14_076_000);

    expect(store.setRadioState(singleReceiverWireState({ revision: 2, active: 'MAIN', main: { ...main, activeSlot: 'B' as const } }))).toBe(true);
    expect(store.getRadioState()?.active).toBe('MAIN');
    expect(store.getRadioState()?.main.activeSlot).toBe('B');
  });

  it('rejects a state whose epoch does not match capabilities before touching connection truth', async () => {
    const connection = await import('../connection.svelte');
    const capabilities = await import('../capabilities.svelte');
    expect(store.setRadioState(makeState({ providerGeneration: 0 }))).toBe(true);
    expect(store.setRadioState(makeState({ revision: 2, providerGeneration: 1, ptt: true }))).toBe(false);
    expect(store.getRadioState()?.providerGeneration).toBe(0);
    expect(connection.getRadioReady()).toBe(true);

    capabilities.setCapabilities({
      ...(capabilities.getCapabilities()!), providerGeneration: 1,
    });
    expect(store.setRadioState(makeState({ revision: 1, providerGeneration: 1, ptt: true }))).toBe(true);
    expect(store.getRadioState()?.providerGeneration).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
  });

  it('rejects a matching-epoch legacy HTTP rollback from revision 100 to 1', () => {
    expect(store.setRadioState(makeState({ revision: 100, ptt: false }))).toBe(true);
    const accepted = store.getRadioState();
    expect(store.setRadioState(makeState({ revision: 1, ptt: true }))).toBe(false);
    expect(store.getRadioState()).toBe(accepted);
    expect(store.getRadioState()?.revision).toBe(100);
    expect(store.getRadioState()?.ptt).toBe(false);
  });

  it('resets generation bookkeeping cleanly when accepting a new provider epoch', async () => {
    const capabilities = await import('../capabilities.svelte');
    store.setRadioState(makeState({ providerGeneration: 0, main: { ...makeState().main, afLevel: 10 } }));
    // Legacy overlay/lock path (pre-A09b) — optional-chained so this pin
    // survives both regimes: a no-op once the store exposes no patch API.
    (store as any).patchActiveReceiver?.({ afLevel: 42 }, true);

    capabilities.setCapabilities({
      ...(capabilities.getCapabilities()!), providerGeneration: 1,
    });
    const next = makeState({
      revision: 1,
      providerGeneration: 1,
      main: { ...makeState().main, afLevel: 12 },
    });
    store.setRadioState(next);
    expect(store.getRadioState()?.providerGeneration).toBe(1);
    expect(store.getRadioState()?.main.afLevel).toBe(12);
    expect(store.getRadioState()).toStrictEqual(next);
  });

  it('accepts initial revision 0 state when store is empty', () => {
    const s = makeState({ revision: 0 });
    store.setRadioState(s);
    expect(store.getRadioState()?.revision).toBe(0);
  });

  it('ignores stale states (lower revision)', () => {
    store.setRadioState(makeState({ revision: 5 }));
    const stale = makeState({ revision: 3 });
    store.setRadioState(stale);
    expect(store.getRadioState()?.revision).toBe(5);
  });

  it('accepts higher revision update', () => {
    store.setRadioState(makeState({ revision: 3 }));
    store.setRadioState(makeState({ revision: 7, ptt: true }));
    expect(store.getRadioState()?.revision).toBe(7);
    expect(store.getRadioState()?.ptt).toBe(true);
  });

  it('ignores equal revision (not strictly greater)', () => {
    store.setRadioState(makeState({ revision: 5, ptt: false }));
    store.setRadioState(makeState({ revision: 5, ptt: true }));
    expect(store.getRadioState()?.ptt).toBe(false);
  });

  it('accepts health-only updates when healthRevision advances', async () => {
    const connection = await import('../connection.svelte');
    store.setRadioState(makeState({ revision: 5, healthRevision: 1 }));
    store.setRadioState(makeState({
      revision: 5,
      healthRevision: 2,
      connection: { rigConnected: true, radioReady: false, controlConnected: true },
      radioHealth: {
        serverReachable: true,
        radioLink: 'connected',
        readiness: 'delayed',
        likelyCause: 'radio_not_responding',
        sinceMs: 1200,
        lastError: null,
      },
    }));

    expect(store.getRadioState()?.healthRevision).toBe(2);
    expect(store.getRadioState()?.radioHealth?.likelyCause).toBe('radio_not_responding');
    expect(connection.getRadioReady()).toBe(false);
    expect(connection.getRadioHealth()?.readiness).toBe('delayed');
  });

  it('accepts freshness-only updates when freshnessRevision advances', () => {
    store.setRadioState(makeState({ revision: 5, stateRevision: 5, freshnessRevision: 1 }));
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 2,
      main: {
        ...makeState().main,
        sMeter: 77,
      },
    }));

    expect(store.getRadioState()?.freshnessRevision).toBe(2);
    expect(store.getRadioState()?.main.sMeter).toBe(77);
  });

  it('accepts same-value fieldStatus metadata when observationSeq advances', () => {
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 1,
      healthRevision: 1,
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
    }));

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
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 1,
      healthRevision: 1,
      observationSeq: 2,
      fieldStatus: nextFieldStatus,
    }));

    expect(store.getRadioState()?.stateRevision).toBe(5);
    expect(store.getRadioState()?.freshnessRevision).toBe(1);
    expect(store.getRadioState()?.observationSeq).toBe(2);
    expect(store.getRadioState()?.fieldStatus?.['main.freqHz']).toEqual(
      nextFieldStatus['main.freqHz'],
    );
  });

  it('accepts wsClients metadata when publicStateSeq advances without semantic revisions', () => {
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 1,
      observationSeq: 1,
      publicStateSeq: 1,
      wsClients: { scope: 0, control: 1, audio: 0 },
    }));

    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 1,
      observationSeq: 1,
      publicStateSeq: 2,
      wsClients: { scope: 0, control: 2, audio: 0 },
    }));

    expect(store.getRadioState()?.stateRevision).toBe(5);
    expect(store.getRadioState()?.freshnessRevision).toBe(1);
    expect(store.getRadioState()?.observationSeq).toBe(1);
    expect(store.getRadioState()?.publicStateSeq).toBe(2);
    expect(store.getRadioState()?.wsClients?.control).toBe(2);
  });

  it('ignores equal-revision semantic changes even when publicStateSeq advances', () => {
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      publicStateSeq: 1,
      ptt: true,
      wsClients: { scope: 0, control: 1, audio: 0 },
    }));

    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      publicStateSeq: 2,
      ptt: false,
      wsClients: { scope: 0, control: 2, audio: 0 },
    }));

    expect(store.getRadioState()?.publicStateSeq).toBe(1);
    expect(store.getRadioState()?.wsClients?.control).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
  });

  it('ignores stale semantic state even when freshnessRevision advances', () => {
    store.setRadioState(makeState({ revision: 6, stateRevision: 6, freshnessRevision: 1, ptt: true }));
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      freshnessRevision: 2,
      ptt: false,
      main: {
        ...makeState().main,
        freqHz: 7100000,
      },
    }));

    expect(store.getRadioState()?.stateRevision).toBe(6);
    expect(store.getRadioState()?.freshnessRevision).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
    expect(store.getRadioState()?.main.freqHz).toBe(14074000);
  });

  it('ignores stale semantic state even when observationSeq advances', () => {
    store.setRadioState(makeState({
      revision: 6,
      stateRevision: 6,
      observationSeq: 6,
      ptt: true,
    }));
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      observationSeq: 7,
      ptt: false,
      main: {
        ...makeState().main,
        freqHz: 7100000,
      },
    }));

    expect(store.getRadioState()?.stateRevision).toBe(6);
    expect(store.getRadioState()?.observationSeq).toBe(6);
    expect(store.getRadioState()?.ptt).toBe(true);
    expect(store.getRadioState()?.main.freqHz).toBe(14074000);
  });

  it('ignores stale semantic state even when healthRevision advances', () => {
    store.setRadioState(makeState({ revision: 6, stateRevision: 6, healthRevision: 1, ptt: true }));
    store.setRadioState(makeState({
      revision: 5,
      stateRevision: 5,
      healthRevision: 2,
      ptt: false,
      connection: { rigConnected: true, radioReady: false, controlConnected: true },
    }));

    expect(store.getRadioState()?.stateRevision).toBe(6);
    expect(store.getRadioState()?.healthRevision).toBe(1);
    expect(store.getRadioState()?.ptt).toBe(true);
    expect(store.getRadioState()?.connection.radioReady).toBe(true);
  });

  it('getFrequency returns active receiver frequency (MAIN)', () => {
    store.setRadioState(makeState({ active: 'MAIN' }));
    expect(store.getFrequency()).toBe(14074000);
  });

  it('getFrequency returns sub receiver frequency when active is SUB', async () => {
    await useDualReceiverCapabilities();
    store.setRadioState(makeState({ active: 'SUB' }));
    expect(store.getFrequency()).toBe(7100000);
  });

  it('getMode returns active receiver mode', () => {
    store.setRadioState(makeState({ active: 'MAIN' }));
    expect(store.getMode()).toBe('USB');
  });

  it('getIsTransmitting reflects ptt state', () => {
    store.setRadioState(makeState({ ptt: true }));
    expect(store.getIsTransmitting()).toBe(true);
  });

  it('getLastRevision tracks the latest revision', () => {
    store.setRadioState(makeState({ revision: 10 }));
    expect(store.getLastRevision()).toBe(10);
  });

  it('getMainReceiver and getSubReceiver return correct receivers', () => {
    store.setRadioState(makeState());
    expect(store.getMainReceiver()?.freqHz).toBe(14074000);
    expect(store.getSubReceiver()?.freqHz).toBe(7100000);
  });

  it('notifies subscribeRadioState listeners exactly once per accepted state', () => {
    const seen: Array<ReturnType<typeof store.getRadioState>> = [];
    const unsubscribe = store.subscribeRadioState((state) => seen.push(state));
    // subscribeRadioState delivers the current value immediately on subscribe.
    expect(seen).toEqual([null]);

    const accepted = makeState({ revision: 1 });
    store.setRadioState(accepted);
    expect(seen).toEqual([null, accepted]);

    // A stale/rejected update must not notify.
    store.setRadioState(makeState({ revision: 0 }));
    expect(seen).toEqual([null, accepted]);

    unsubscribe();
    store.setRadioState(makeState({ revision: 2 }));
    expect(seen).toEqual([null, accepted]);
  });

  it('rejects a revision reset inside the same epoch and session', () => {
    store.setRadioState(makeState({ revision: 100 }));
    store.setRadioState(makeState({ revision: 1, ptt: true }));
    expect(store.getRadioState()?.revision).toBe(100);
    expect(store.getRadioState()?.ptt).toBe(false);
  });

  it('does not treat small revision drop as server restart (lastRevision <= 10)', () => {
    store.setRadioState(makeState({ revision: 5 }));
    store.setRadioState(makeState({ revision: 1, ptt: true }));
    // lastRevision=5 which is NOT > 10, so treated as stale
    expect(store.getRadioState()?.revision).toBe(5);
    expect(store.getRadioState()?.ptt).toBe(false);
  });

  // --- resetRadioState (migrated from radio.isolated.test.ts, MOR-1409 A09b:
  // that file's remaining coverage tested the deleted optimism machinery and
  // was retired; these reset-semantics pins are still meaningful) ---

  it('resetRadioState clears radio.current to null', () => {
    store.setRadioState(makeState({ revision: 1 }));
    expect(store.getRadioState()).not.toBeNull();

    store.resetRadioState();
    expect(store.getRadioState()).toBeNull();
  });

  it('resetRadioState resets lastRevision to -1', () => {
    store.setRadioState(makeState({ revision: 42 }));
    expect(store.getLastRevision()).toBe(42);

    store.resetRadioState();
    expect(store.getLastRevision()).toBe(-1);
  });

  it('allows new state to be set after resetRadioState', () => {
    store.setRadioState(makeState({ revision: 10 }));
    store.resetRadioState();

    // After reset, a state with revision=1 should be accepted.
    store.setRadioState(makeState({ revision: 1 }));
    expect(store.getRadioState()).not.toBeNull();
    expect(store.getLastRevision()).toBe(1);
  });

  it('getRadioState returns null after resetRadioState', () => {
    store.setRadioState(makeState({ revision: 5 }));
    store.resetRadioState();
    expect(store.getRadioState()).toBeNull();
  });

  // --- store surface and byte-exact acceptance (MOR-1409 A09b) ---
  //
  // A09b deletes the optimistic maps/TTLs/locks, `applyOptimistic()`, and the
  // three patch functions (`patchActiveReceiver`, `patchReceiver`,
  // `patchRadioState`). `setRadioState` becomes the store's sole mutator and
  // stores each accepted snapshot byte-exact — no overlay can survive it.
  // The legacy calls below are optional-chained through `(store as any)` so
  // these tests demonstrate the base-line fabrication/overlay bugs as causal
  // RED before the deletion, then degrade to harmless no-ops and prove the
  // new invariant (GREEN) once the exports are gone.

  it('exposes no patch/optimistic API — only setRadioState mutates the store', () => {
    expect((store as any).patchRadioState).toBeUndefined();
    expect((store as any).patchActiveReceiver).toBeUndefined();
    expect((store as any).patchReceiver).toBeUndefined();
    expect((store as any).applyOptimistic).toBeUndefined();
  });

  it('accepted snapshot is stored byte-exact — no fabricated top-level field survives it', () => {
    store.setRadioState(makeState({ revision: 1, split: false }));
    // Legacy fabrication path (pre-A09b): patched split optimistically without
    // any server observation ever confirming it.
    (store as any).patchRadioState?.({ split: true });

    const accepted = makeState({ revision: 2, split: false });
    store.setRadioState(accepted);
    expect(store.getRadioState()).toStrictEqual(accepted);
  });

  it('accepted snapshot wins immediately — no receiver-field overlay survives it', () => {
    store.setRadioState(makeState({ revision: 1, main: { ...makeState().main, afLevel: 100 } }));
    // Legacy overlay path (pre-A09b): an unconfirmed local patch that used to
    // hold the display value until TTL/confirm.
    (store as any).patchActiveReceiver?.({ afLevel: 99 });

    const accepted = makeState({ revision: 2, main: { ...makeState().main, afLevel: 10 } });
    store.setRadioState(accepted);
    expect(store.getRadioState()?.main.afLevel).toBe(10);
    expect(store.getRadioState()).toStrictEqual(accepted);
  });

  it('getActiveReceiver returns SUB when active is SUB', async () => {
    await useDualReceiverCapabilities();
    store.setRadioState(makeState({ active: 'SUB' }));
    expect(store.getActiveReceiver()?.freqHz).toBe(7100000);
  });

  it('getActiveReceiver returns null when state is null', () => {
    expect(store.getActiveReceiver()).toBeNull();
  });

  // --- StateStore-owned VFO truth (MOR-1403; the optimistic carve-out this
  // protected is gone entirely in A09b — no patch API exists to fabricate a
  // VFO field, so the invariant now reduces to "setRadioState is causal") ---

  it('VFO truth changes only when each causally-advancing StateStore observation arrives', () => {
    store.setRadioState(makeState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 1,
      freshnessRevision: 1,
      main: { ...makeState().main, freqHz: 14074000 },
    }));
    expect(store.getMainReceiver()?.freqHz).toBe(14074000);

    // A newer observation carrying the same value remains the displayed fact.
    store.setRadioState(makeState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 2,
      freshnessRevision: 2,
      main: { ...makeState().main, freqHz: 14074000 },
    }));
    expect(store.getMainReceiver()?.freqHz).toBe(14074000);

    // A confirmed differing readback advances the display immediately —
    // there is no local intent, lock, or TTL that could delay or override it.
    store.setRadioState(makeState({
      revision: 1,
      stateRevision: 1,
      observationSeq: 3,
      freshnessRevision: 3,
      main: { ...makeState().main, freqHz: 14100000 },
    }));
    expect(store.getMainReceiver()?.freqHz).toBe(14100000);
    expect(store.getFrequency()).toBe(14100000);
  });
});
