/**
 * MOR-1067 — pure per-receiver slicing, driven directly by the REAL MOR-1062
 * topology fixtures (no mocking). Each test's doc line names the mutation it
 * exists to kill.
 */
import { describe, it, expect } from 'vitest';
import { forReceiver, isActiveStrip, receiversOf } from '../dual-receiver-strips';
import { topologyFixtures } from '../../../semantic/fixtures/topologies';
import type { RadioViewModel } from '../../../semantic/radio-view-model';

const dualAbShared = topologyFixtures['2/ab_shared'];
const dualMainSub = topologyFixtures['2/main_sub'];
const singleReceiver = topologyFixtures['1/single'];

describe('receiversOf', () => {
  it('finds both receivers in an unslotted dual (ab_shared) fixture', () => {
    expect(receiversOf(dualAbShared)).toEqual(['MAIN', 'SUB']);
  });

  it('finds both receivers in a slotted dual (main_sub) fixture, de-duplicated', () => {
    expect(receiversOf(dualMainSub)).toEqual(['MAIN', 'SUB']);
  });

  // Kills: hardcoding ['MAIN', 'SUB'] instead of reading view.vfos — a
  // single-receiver topology would then falsely report a SUB strip.
  it('never fabricates a second receiver for a single-receiver fixture', () => {
    expect(receiversOf(singleReceiver)).toEqual(['MAIN']);
  });

  it('reports no receivers for an empty vfos array', () => {
    expect(receiversOf({ ...dualMainSub, vfos: [] })).toEqual([]);
  });
});

describe('forReceiver', () => {
  it('keeps only the requested receiver\'s VFOs, in original order', () => {
    const sub = forReceiver(dualMainSub, 'SUB');
    expect(sub.vfos.map((v) => v.label)).toEqual(['S-A', 'S-B']);
    expect(sub.vfos.every((v) => v.receiver === 'SUB')).toBe(true);
  });

  it('an unslotted dual fixture yields exactly one VFO per strip', () => {
    expect(forReceiver(dualAbShared, 'MAIN').vfos).toHaveLength(1);
    expect(forReceiver(dualAbShared, 'SUB').vfos).toHaveLength(1);
  });

  // Kills: the slice also touching shared/global facts (activeReceiver,
  // split, dualWatch, txTarget, txPermit, scope, disabledReasons) instead of
  // passing them through verbatim — those are radio-wide, not per-receiver.
  it('leaves every field other than vfos byte-identical to the source', () => {
    const sliced = forReceiver(dualMainSub, 'MAIN');
    const { vfos: _vfos, ...sourceRest } = dualMainSub;
    const { vfos: _slicedVfos, ...slicedRest } = sliced;
    expect(slicedRest).toEqual(sourceRest);
  });
});

describe('isActiveStrip', () => {
  it('is true only for the positively-observed active receiver', () => {
    expect(isActiveStrip(dualAbShared, 'SUB')).toBe(true);
    expect(isActiveStrip(dualAbShared, 'MAIN')).toBe(false);
  });

  // The explicit mutation this ticket names: a shell/strip that defaults to
  // "strip one is active" when activeReceiver was never observed.
  it('marks NEITHER strip active when activeReceiver is unknown — no fabricated default', () => {
    const unknownActive: RadioViewModel = { ...dualMainSub, activeReceiver: { status: 'unknown' } };
    expect(isActiveStrip(unknownActive, 'MAIN')).toBe(false);
    expect(isActiveStrip(unknownActive, 'SUB')).toBe(false);
  });
});
