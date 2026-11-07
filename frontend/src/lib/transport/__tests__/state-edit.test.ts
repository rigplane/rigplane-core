/**
 * MOR-4213: `editState` (test support) must build a new snapshot exactly the
 * way the client's delta applier does, or the tests that use it would pin an
 * identity pattern production never produces.
 */
import { describe, expect, it } from 'vitest';
import { applyStateDelta } from '../state-delta';
import { editCaps, editState } from './support/state-edit';

const base = () => ({
  revision: 1,
  main: { freqHz: 14_074_000, mode: 'USB', vfoA: { freqHz: 14_074_000 }, vfoB: { freqHz: 7_074_000 } },
  sub: { freqHz: 21_074_000 },
  fieldStatus: { 'main.freqHz': { observed: true, lastObservedMonotonic: 1 } },
  list: [1, 2],
});

describe('editState', () => {
  it('matches the client applier: new changed branches, untouched branches shared, input untouched', () => {
    const state = base();
    const before = structuredClone(state);
    const edited = editState(state, (s) => {
      s.main.vfoA.freqHz = 7_100_000;
      s.fieldStatus['main.freqHz'].lastObservedMonotonic = 2;
    });
    const applied = applyStateDelta(state, { set: [
      [['main', 'vfoA', 'freqHz'], 7_100_000],
      [['fieldStatus', 'main.freqHz', 'lastObservedMonotonic'], 2],
    ], unset: [] })!;
    expect(edited).toEqual(applied);
    expect(state).toEqual(before);
    expect(edited).not.toBe(state);
    expect(edited.main).not.toBe(state.main);
    expect(edited.main.vfoA).not.toBe(state.main.vfoA);
    expect(edited.main.vfoB).toBe(state.main.vfoB);
    expect(edited.sub).toBe(state.sub);
    expect(edited.list).toBe(state.list);
  });

  it('writes arrays whole, deletes keys, and stores no recording wrappers', () => {
    const state = base();
    const edited = editState(state, (s) => {
      s.list.push(3);
      delete (s.main as Partial<typeof s.main>).mode;
      s.main = { ...s.main, freqHz: 1 };
    });
    expect(edited.list).toEqual([1, 2, 3]);
    expect(state.list).toEqual([1, 2]);
    expect(edited.main).not.toHaveProperty('mode');
    expect(edited.main.freqHz).toBe(1);
    expect(structuredClone(edited)).toEqual(edited);
  });

  it('editCaps returns a new body and leaves the old one alone', () => {
    const caps = { capabilities: ['scope'], receivers: 1 };
    const next = editCaps(caps, (c) => { c.capabilities.push('dual_rx'); });
    expect(next).not.toBe(caps);
    expect(next.capabilities).toEqual(['scope', 'dual_rx']);
    expect(caps.capabilities).toEqual(['scope']);
  });
});
