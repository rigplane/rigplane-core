import { describe, expect, it } from 'vitest';
import { applyStateDelta, stateDeltaOps } from '../state-delta';

function stationState(): Record<string, unknown> {
  const fieldStatus: Record<string, unknown> = {};
  for (let index = 0; index < 110; index += 1) {
    fieldStatus[`main.field${index}`] = {
      storePath: `receiver.0.levels.field_${index}`,
      observed: true,
      freshness: 'fresh',
      availability: 'available',
      lastObservedMonotonic: 1,
      quality: ['direct'],
    };
  }
  return {
    main: { freqHz: 14_074_000, mode: 'USB' },
    connection: { rigConnected: true, radioReady: true, liveness: { lastFrameAgeMs: 4 } },
    fieldStatus,
  };
}

type Tree = Record<string, Record<string, unknown>>;

describe('applyStateDelta (MOR-4206)', () => {
  it('updates only the leaf a nested delta names and keeps every other subtree', () => {
    const state = stationState();
    const ops = stateDeltaOps({
      type: 'delta',
      deltaVersion: 2,
      set: [[['fieldStatus', 'main.field7', 'lastObservedMonotonic'], 2]],
    });
    expect(ops).not.toBeNull();

    const next = applyStateDelta(state, ops!) as unknown as Tree;
    const before = state as unknown as Tree;

    expect((next.fieldStatus['main.field7'] as Record<string, unknown>).lastObservedMonotonic).toBe(2);
    expect((before.fieldStatus['main.field7'] as Record<string, unknown>).lastObservedMonotonic).toBe(1);
    expect(next.main).toBe(before.main);
    expect(next.connection).toBe(before.connection);
    expect(next.fieldStatus['main.field6']).toBe(before.fieldStatus['main.field6']);
    expect(next.fieldStatus['main.field7']).not.toBe(before.fieldStatus['main.field7']);
    expect(next.fieldStatus).not.toBe(before.fieldStatus);
  });

  it('copies a shared parent once for several leaves and removes unset paths', () => {
    const state = stationState();
    const next = applyStateDelta(state, stateDeltaOps({
      type: 'delta',
      deltaVersion: 2,
      set: [
        [['connection', 'liveness', 'lastFrameAgeMs'], 9],
        [['connection', 'radioReady'], false],
      ],
      unset: [['main', 'mode']],
    })!) as unknown as Tree;

    expect(next.connection).toEqual({ rigConnected: true, radioReady: false, liveness: { lastFrameAgeMs: 9 } });
    expect(next.main).toEqual({ freqHz: 14_074_000 });
    expect('mode' in (state.main as Record<string, unknown>)).toBe(true);
    expect(next.fieldStatus).toBe(state.fieldStatus);
  });

  it('reads a top-level delta as one-key paths with whole values', () => {
    const state = stationState();
    const ops = stateDeltaOps({ type: 'delta', changed: { main: { freqHz: 7_074_000 } }, removed: ['connection'] });

    const next = applyStateDelta(state, ops!)!;

    expect(next.main).toEqual({ freqHz: 7_074_000 });
    expect('connection' in next).toBe(false);
    expect(next.fieldStatus).toBe(state.fieldStatus);
  });

  it('rejects malformed envelopes and paths through a non-object', () => {
    expect(stateDeltaOps({ type: 'delta', deltaVersion: 3, set: [] })).toBeNull();
    expect(stateDeltaOps({ type: 'delta', deltaVersion: 2, set: [[[], 1]] })).toBeNull();
    expect(stateDeltaOps({ type: 'delta', deltaVersion: 2, set: [[['a', 1], 1]] })).toBeNull();
    expect(stateDeltaOps({ type: 'delta', deltaVersion: 2, set: [], unset: ['a'] })).toBeNull();
    expect(stateDeltaOps({ type: 'delta', changed: [] })).toBeNull();
    expect(stateDeltaOps({ type: 'delta', changed: {}, removed: [1] })).toBeNull();

    const ops = stateDeltaOps({ type: 'delta', deltaVersion: 2, set: [[['main', 'freqHz', 'x'], 1]] });
    expect(applyStateDelta(stationState(), ops!)).toBeNull();
  });
});
