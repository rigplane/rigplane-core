import { describe, expect, it } from 'vitest';
import {
  EMPTY_PBT_PRESENTATION, projectPbtPresentation, type PbtPresentationEvidence,
} from '../pbt-presentation-continuity';
import { topologyFixtures, withFilterPassband } from '../fixtures/topologies';
import type { FilterPassbandViewModel, RadioViewModel } from '../radio-view-model';

const view = (inner = 100, outer = 200): RadioViewModel => {
  const base = withFilterPassband(topologyFixtures['1/single']);
  return {
    ...base,
    filterPassband: {
      ...base.filterPassband!,
      pbtInner: { reading: { status: 'known', value: inner }, availability: { structural: true, operational: true } },
      pbtOuter: { reading: { status: 'known', value: outer }, availability: { structural: true, operational: true } },
    } as FilterPassbandViewModel,
  };
};

const evidence = (
  inner: PbtPresentationEvidence['fields']['pbtInner'] = { status: 'fresh', marker: { source: 'snapshot', value: 1 } },
  outer: PbtPresentationEvidence['fields']['pbtOuter'] = { status: 'fresh', marker: { source: 'snapshot', value: 1 } },
  boundary: PbtPresentationEvidence['boundary'] = { providerGeneration: 1, receiver: 'MAIN', controlSession: 'a', epoch: 1 },
): PbtPresentationEvidence => ({ boundary, fields: { pbtInner: inner, pbtOuter: outer } });

const reduce = (state = EMPTY_PBT_PRESENTATION, canonical = view(), input = evidence()) =>
  projectPbtPresentation(state, canonical, input);
const unavailable = { status: 'unavailable' } as const;
const stale = { status: 'stale' } as const;
const fresh = (source: 'snapshot' | 'field', value: number) =>
  ({ status: 'fresh', marker: { source, value } }) as const;

describe('PBT presentation continuity', () => {
  it('starts with independent fresh Inner and Outer retained values', () => {
    const result = reduce();
    expect(result.state.pbtInner?.value).toBe(100);
    expect(result.state.pbtOuter?.value).toBe(200);
  });

  it('does not fabricate a value when no prior reading exists', () => {
    const canonical = view();
    canonical.filterPassband!.pbtInner = {
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    };
    const result = reduce(EMPTY_PBT_PRESENTATION, canonical, evidence(unavailable, unavailable));
    expect(result.state.pbtInner).toBeNull();
    expect(result.view.filterPassband!.pbtInner.reading.status).toBe('unknown');
  });

  it('retains a field through unavailable or stale evidence as non-authoritative', () => {
    const first = reduce();
    const unavailableResult = reduce(first.state, view(999, 999), evidence(unavailable, stale));
    const fields = unavailableResult.view.filterPassband!;
    expect(fields.pbtInner).toMatchObject({ reading: { value: 100 }, availability: { operational: false } });
    expect(fields.pbtOuter).toMatchObject({ reading: { value: 200 }, availability: { operational: false } });
  });

  it('snapshots the accepted boundary against caller mutation', () => {
    const boundary = { providerGeneration: 1, receiver: 'MAIN', controlSession: 'a', epoch: 1 };
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence(fresh('field', 10), fresh('field', 10), boundary));
    boundary.controlSession = 'b';
    const result = reduce(first.state, view(999), evidence(unavailable, unavailable, boundary));
    expect(result.state.pbtInner).toBeNull();
    expect(result.view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: 999 });
  });

  it('snapshots accepted marker and value objects against caller mutation', () => {
    const marker = { source: 'field' as const, value: 10 };
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence({ status: 'fresh', marker }));
    marker.value = 0;
    const result = reduce(first.state, view(999), evidence(fresh('snapshot', 5)));
    expect(result.view.filterPassband!.pbtInner).toMatchObject({
      reading: { value: 100 }, availability: { operational: false },
    });
  });

  it('does not retain unsupported fields', () => {
    const first = reduce();
    const unsupported = view();
    unsupported.filterPassband!.pbtInner = {
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    };
    const result = reduce(first.state, unsupported, evidence(unavailable));
    expect(result.state.pbtInner).toBeNull();
  });

  it('keeps Inner and Outer independent', () => {
    const first = reduce();
    const result = reduce(first.state, view(999, 333), evidence(unavailable, fresh('snapshot', 2)));
    expect(result.view.filterPassband!.pbtInner).toMatchObject({ reading: { value: 100 }, availability: { operational: false } });
    expect(result.view.filterPassband!.pbtOuter).toMatchObject({ reading: { value: 333 }, availability: { operational: true } });
  });

  it.each([
    ['older', fresh('snapshot', 1), 100],
    ['equal', fresh('snapshot', 2), 100],
    ['newer', fresh('snapshot', 3), 999],
  ] as const)('handles %s same-source markers conservatively', (_name, next, expected) => {
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence(fresh('snapshot', 2)));
    const result = reduce(first.state, view(999), evidence(next));
    expect(result.view.filterPassband!.pbtInner.reading).toEqual({ status: 'known', value: expected });
  });

  it('does not regress a delayed snapshot after a field observation', () => {
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence(fresh('field', 20)));
    const result = reduce(first.state, view(999), evidence(fresh('snapshot', 19)));
    expect(result.view.filterPassband!.pbtInner).toMatchObject({ reading: { value: 100 }, availability: { operational: false } });
  });

  it('does not regress a delayed field after a snapshot observation', () => {
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence(fresh('snapshot', 20)));
    const result = reduce(first.state, view(999), evidence(fresh('field', 19)));
    expect(result.view.filterPassband!.pbtInner).toMatchObject({ reading: { value: 100 }, availability: { operational: false } });
  });

  it.each([
    ['snapshot to field', fresh('snapshot', 20), fresh('field', 21)],
    ['field to snapshot', fresh('field', 20), fresh('snapshot', 21)],
  ] as const)('accepts truly fresh %s evidence and cannot freeze', (_name, oldMarker, nextMarker) => {
    const first = reduce(EMPTY_PBT_PRESENTATION, view(100), evidence(oldMarker));
    const result = reduce(first.state, view(999), evidence(nextMarker));
    expect(result.view.filterPassband!.pbtInner).toMatchObject({ reading: { value: 999 }, availability: { operational: true } });
  });

  it.each([
    ['provider generation', { providerGeneration: 2, receiver: 'MAIN', controlSession: 'a', epoch: 1 }],
    ['receiver', { providerGeneration: 1, receiver: 'SUB', controlSession: 'a', epoch: 1 }],
    ['control session', { providerGeneration: 1, receiver: 'MAIN', controlSession: 'b', epoch: 1 }],
    ['session epoch', { providerGeneration: 1, receiver: 'MAIN', controlSession: 'a', epoch: 2 }],
  ] as const)('clears retention at a %s boundary', (_name, boundary) => {
    const first = reduce();
    const result = reduce(first.state, view(999), evidence(unavailable, unavailable, boundary));
    expect(result.state.pbtInner).toBeNull();
    expect(result.state.pbtOuter).toBeNull();
  });

  it('clears all retention when the boundary is absent', () => {
    const first = reduce();
    const result = reduce(first.state, view(), evidence(unavailable, unavailable, null));
    expect(result.state).toEqual(EMPTY_PBT_PRESENTATION);
  });
});


describe('fractional PBT display admission (MOR-1692)', () => {
  const observedView = (value = 100, state: 'current' | 'stale' = 'current') => {
    const result = view(value);
    result.filterPassband!.pbtInner = {
      reading: state === 'current' ? { status: 'known', value } : { status: 'unknown' },
      availability: { structural: true, operational: state === 'current' }, display: { state, value },
    };
    return result;
  };
  const markedStale = (value: number) => ({ status: 'stale', marker: { source: 'field', value } }) as const;

  it('retains fractional current → stale → newer current with independent outer', () => {
    const first = reduce(EMPTY_PBT_PRESENTATION, observedView(), evidence(fresh('field', 310658.42975425)));
    expect(first.state.pbtInner?.marker.value).toBe(310658.42975425);
    const next = reduce(first.state, observedView(100, 'stale'), evidence(markedStale(310658.42975425), fresh('field', 2)));
    expect(next.view.filterPassband!.pbtInner).toMatchObject({ display: { state: 'stale', value: 100 }, availability: { operational: false } });
    expect(next.view.filterPassband!.pbtOuter.availability.operational).toBe(true);
    const final = reduce(next.state, observedView(200), evidence(fresh('field', 310658.5)));
    expect(final.view.filterPassband!.pbtInner.display).toEqual({ state: 'current', value: 200 });
  });

  it('admits an already-stale first observation and keeps it inert', () => {
    const result = reduce(EMPTY_PBT_PRESENTATION, observedView(0, 'stale'), evidence(markedStale(0.25)));
    expect(result.state.pbtInner?.value).toBe(0);
    expect(result.view.filterPassband!.pbtInner).toMatchObject({ reading: { status: 'known', value: 0 }, display: { state: 'stale', value: 0 }, availability: { operational: false } });
  });

  it.each([NaN, Infinity, -0.1])('rejects invalid marker %s without display leakage', (marker) => {
    const result = reduce(EMPTY_PBT_PRESENTATION, observedView(), evidence(fresh('field', marker)));
    expect(result.state.pbtInner).toBeNull();
    expect(result.view.filterPassband!.pbtInner.display?.state).toBe('unknown');
  });

  it.each([unavailable, stale])('does not admit a raw display without marked evidence: $status', (incoming) => {
    const result = reduce(EMPTY_PBT_PRESENTATION, observedView(999, 'stale'), evidence(incoming));
    expect(result.view.filterPassband!.pbtInner.display?.state).toBe('unknown');
  });

  it('masks display when boundary is null or support disappears', () => {
    const current = observedView();
    expect(reduce(EMPTY_PBT_PRESENTATION, current, evidence(unavailable, unavailable, null)).view.filterPassband!.pbtInner.display?.state).toBe('unknown');
    current.filterPassband!.pbtInner.availability.structural = false;
    expect(reduce(EMPTY_PBT_PRESENTATION, current).view.filterPassband!.pbtInner.display).toEqual({ state: 'unsupported' });
  });

  it.each([9.25, 10.25])('does not replace retained display with conflicting marker %s', (marker) => {
    const first = reduce(EMPTY_PBT_PRESENTATION, observedView(), evidence(fresh('field', 10.25)));
    const result = reduce(first.state, observedView(999), evidence(fresh('field', marker)));
    expect(result.view.filterPassband!.pbtInner.display).toEqual({ state: 'stale', value: 100 });
    expect(result.view.filterPassband!.pbtInner.availability.operational).toBe(false);
  });

  it.each([
    { providerGeneration: 2, receiver: 'MAIN', controlSession: 'a', epoch: 1 },
    { providerGeneration: 1, receiver: 'SUB', controlSession: 'a', epoch: 1 },
    { providerGeneration: 1, receiver: 'MAIN', controlSession: 'b', epoch: 2 },
  ])('does not spread display across a changed boundary without new evidence: %j', (boundary) => {
    const first = reduce(EMPTY_PBT_PRESENTATION, observedView(), evidence(fresh('field', 10.25)));
    const result = reduce(first.state, observedView(999, 'stale'), evidence(unavailable, unavailable, boundary));
    expect(result.state.pbtInner).toBeNull();
    expect(result.view.filterPassband!.pbtInner.display?.state).toBe('unknown');
    const next = reduce(result.state, observedView(200, 'stale'), evidence(markedStale(0.25), unavailable, boundary));
    expect(next.view.filterPassband!.pbtInner.display).toEqual({ state: 'stale', value: 200 });
  });

  it('keeps current display current when an independent gate disables operation', () => {
    const current = observedView(); current.filterPassband!.pbtInner.availability.operational = false;
    const result = reduce(EMPTY_PBT_PRESENTATION, current, evidence(fresh('field', 0.5)));
    expect(result.view.filterPassband!.pbtInner.display).toEqual({ state: 'current', value: 100 });
    expect(result.view.filterPassband!.pbtInner.availability.operational).toBe(false);
  });
});
