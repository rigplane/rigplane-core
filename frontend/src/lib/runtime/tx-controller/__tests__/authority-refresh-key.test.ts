// MOR-2607: an idle page must not refetch GET /api/v1/managed-transmit on
// every radio observation — only when a TX input changes.
import { describe, expect, it } from 'vitest';
import { managedTxAuthorityKey, ManagedTxAuthorityRefreshGate } from '../authority-refresh-key';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const field = (at: number) => ({
  observed: true,
  freshness: 'fresh' as const,
  availability: 'available' as const,
  lastObservedMonotonic: at,
  source: { source: 'poll_response' },
});

const baseState = {
  stateRevision: 1,
  freshnessRevision: 1,
  observationSeq: 1,
  revision: 1,
  updatedAt: '2026-09-25T00:00:00Z',
  providerGeneration: 3,
  active: 'MAIN',
  ptt: false,
  split: false,
  dualWatch: false,
  tunerStatus: 0,
  connection: { rigConnected: true, radioReady: true, controlConnected: true },
  txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
  main: { dataMode: 0 },
  sub: { dataMode: 0 },
  fieldStatus: { ptt: field(1), txTarget: field(1) },
} as unknown as ServerState;

const baseCaps = {
  tx: true,
  audioTx: true,
  capabilities: ['tx'],
  vfoScheme: 'main_sub',
  audioTxRequiredModInputSource: 5,
  txBands: [{ start: 100, end: 200 }],
} as unknown as Capabilities;

/** One idle observation frame: a NEW state object, counters advanced, the
 * ptt poll timestamp advanced — but nothing TX-relevant changed. */
const idleFrame = (state: ServerState, frame: number): ServerState => ({
  ...state,
  stateRevision: state.stateRevision + 1,
  freshnessRevision: state.freshnessRevision + 1,
  observationSeq: state.observationSeq + 1,
  fieldStatus: {
    ...state.fieldStatus,
    ptt: field(frame + 2),
    'main.sMeter': field(frame + 2),
  },
  main: { ...state.main, sMeter: frame },
}) as unknown as ServerState;

describe('managedTxAuthorityKey + refresh gate (MOR-2607)', () => {
  it('an idle stream of observation-only frames causes 0 refreshes', () => {
    const gate = new ManagedTxAuthorityRefreshGate();
    let state = baseState;
    // The mounted page already refreshed once for the initial key; only
    // idle frames must add nothing.
    gate.shouldRefresh(managedTxAuthorityKey(state, baseCaps));
    let refreshes = 0;
    for (let frame = 0; frame < 10; frame += 1) {
      state = idleFrame(state, frame);
      if (gate.shouldRefresh(managedTxAuthorityKey(state, baseCaps))) refreshes += 1;
    }
    expect(refreshes).toBe(0);
  });

  it('a ptt value change alone causes no refresh: observedPtt arrives via the server event', () => {
    const gate = new ManagedTxAuthorityRefreshGate();
    let refreshes = 0;
    const observe = (state: ServerState) => {
      if (gate.shouldRefresh(managedTxAuthorityKey(state, baseCaps))) refreshes += 1;
    };
    observe(baseState);
    observe(idleFrame(baseState, 0));
    expect(refreshes).toBe(1);
    observe({ ...baseState, ptt: true, fieldStatus: { ...baseState.fieldStatus, ptt: field(9) } } as unknown as ServerState);
    observe(idleFrame({ ...baseState, ptt: true } as unknown as ServerState, 1));
    expect(refreshes).toBe(1);
  });

  it('a providerGeneration change causes exactly 1 refresh', () => {
    const gate = new ManagedTxAuthorityRefreshGate();
    let refreshes = 0;
    const observe = (state: ServerState) => {
      if (gate.shouldRefresh(managedTxAuthorityKey(state, baseCaps))) refreshes += 1;
    };
    observe(baseState);
    observe(idleFrame(baseState, 0));
    expect(refreshes).toBe(1);
    observe({ ...baseState, providerGeneration: 4 } as unknown as ServerState);
    expect(refreshes).toBe(2);
  });

  it('repeating the same key never refreshes again', () => {
    const gate = new ManagedTxAuthorityRefreshGate();
    const key = managedTxAuthorityKey(baseState, baseCaps);
    expect(gate.shouldRefresh(key)).toBe(true);
    expect(gate.shouldRefresh(key)).toBe(false);
    expect(gate.shouldRefresh(key)).toBe(false);
  });
});
