// MOR-2607: an idle page must not refetch GET /api/v1/managed-transmit on
// every radio observation — only when a TX input changes.
import { describe, expect, it } from 'vitest';
import { managedTxAuthorityKey } from '../authority-refresh-key';
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

describe('managedTxAuthorityKey (MOR-2607)', () => {
  it('an idle stream of observation-only frames keeps one key: 0 refetches', () => {
    const keys = new Set<string>();
    let state = baseState;
    for (let frame = 0; frame < 10; frame += 1) {
      state = {
        ...state,
        stateRevision: state.stateRevision + 1,
        freshnessRevision: state.freshnessRevision + 1,
        observationSeq: state.observationSeq + 1,
        fieldStatus: { ...state.fieldStatus, 'main.sMeter': field(frame + 2) },
        main: { ...state.main, sMeter: frame },
      } as unknown as ServerState;
      keys.add(managedTxAuthorityKey(state, baseCaps));
    }
    expect(keys.size).toBe(1);
  });

  it('a ptt change moves the key: exactly 1 refetch', () => {
    const before = managedTxAuthorityKey(baseState, baseCaps);
    const after = managedTxAuthorityKey(
      { ...baseState, ptt: true, fieldStatus: { ...baseState.fieldStatus, ptt: field(2) } } as ServerState,
      baseCaps,
    );
    expect(after).not.toBe(before);
  });

  it('a providerGeneration change moves the key: exactly 1 refetch', () => {
    const before = managedTxAuthorityKey(baseState, baseCaps);
    const after = managedTxAuthorityKey(
      { ...baseState, providerGeneration: 4 } as ServerState,
      baseCaps,
    );
    expect(after).not.toBe(before);
  });
});
