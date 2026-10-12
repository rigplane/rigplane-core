/**
 * Pins of the shared state-advance predicate (`live-metadata.ts`'s
 * `isStateAdvance`), the one rule both the store's `setRadioState` and the
 * client's envelope filter use to decide whether a pushed state moves the
 * held state forward.
 */
import { describe, expect, it } from 'vitest';
import { isStateAdvance, type StateAdvanceBaseline } from '../live-metadata';

const BASE_STATE = {
  revision: 5,
  stateRevision: 5,
  freshnessRevision: 1,
  observationSeq: 2,
  healthRevision: 0,
  publicStateSeq: 10,
  transportSeq: 10,
  connection: { rigConnected: true },
  main: { freqHz: 14000000 },
};

function baselineFrom(state: object): StateAdvanceBaseline {
  return {
    stateRevision: 5,
    freshnessRevision: 1,
    observationSeq: 2,
    healthRevision: 0,
    deliverySeq: 10,
    state,
  };
}

describe('isStateAdvance', () => {
  it('accepts any next state on a null baseline', () => {
    expect(isStateAdvance(null, { ...BASE_STATE })).toBe(true);
  });

  it('accepts an advanced semantic revision', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(isStateAdvance(baseline, { ...BASE_STATE, revision: 6, stateRevision: 6 })).toBe(true);
  });

  it('accepts an advanced freshness revision at equal semantic revision', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(isStateAdvance(baseline, { ...BASE_STATE, freshnessRevision: 2 })).toBe(true);
  });

  it('accepts an advanced observation sequence at equal semantic revision', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(isStateAdvance(baseline, { ...BASE_STATE, observationSeq: 3 })).toBe(true);
  });

  it('accepts an advanced health revision at equal semantic revision', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(isStateAdvance(baseline, { ...BASE_STATE, healthRevision: 1 })).toBe(true);
  });

  it('accepts a delivery advance that changes only live-metadata values', () => {
    const baseline = baselineFrom(BASE_STATE);
    const next = {
      ...BASE_STATE,
      transportSeq: 11,
      connection: { rigConnected: false },
    };
    expect(isStateAdvance(baseline, next)).toBe(true);
  });

  it('rejects a delivery advance that changes a non-metadata value', () => {
    const baseline = baselineFrom(BASE_STATE);
    const next = {
      ...BASE_STATE,
      transportSeq: 11,
      main: { freqHz: 7100000 },
    };
    expect(isStateAdvance(baseline, next)).toBe(false);
  });

  it('rejects a delivery advance that removes a non-metadata key', () => {
    const baseline = baselineFrom(BASE_STATE);
    const { main: _removed, ...next } = { ...BASE_STATE, transportSeq: 11 };
    expect(isStateAdvance(baseline, next)).toBe(false);
  });

  it('rejects a lower semantic revision even when the observation sequence advances', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(
      isStateAdvance(baseline, { ...BASE_STATE, revision: 4, stateRevision: 4, observationSeq: 3 }),
    ).toBe(false);
  });

  it('accepts a delivery advance carried by publicStateSeq alone', () => {
    const baseline = baselineFrom(BASE_STATE);
    const next = {
      ...BASE_STATE,
      publicStateSeq: 11,
      connection: { rigConnected: false },
    };
    expect(isStateAdvance(baseline, next)).toBe(true);
  });

  it('rejects an identical state', () => {
    const baseline = baselineFrom(BASE_STATE);
    expect(isStateAdvance(baseline, { ...BASE_STATE })).toBe(false);
  });
});
