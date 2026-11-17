import { describe, expect, it } from 'vitest';
import { scopeFixtures as fixtures } from './scope-wire-fixture';
import { ingressScopeFrame, parseCurrentScopeFrame, scopeSlotKey } from '../adapters/scope-adapter';
import { projectScopeCoverage, shouldHatchCoverageGap } from '../scope-frame';
import type { ScopeGeometry } from '../scope-frame';
import type { ScopeGranted } from '../scope-view';

const context = { source: 'radio' as const, sourceId: 'scope-1', epoch: 7 };
const wire = (bytes: number[]) => Uint8Array.from(bytes).buffer;

const geometry: ScopeGeometry = {
  source: 'radio', sourceId: 'scope-1', receiver: 0, mode: 0, width: 4,
  view: { startHz: 20, endHz: 80 }, coverage: { startHz: 30, endHz: 70 },
  outOfRange: false, hasPixels: true, freshness: 'fresh',
};
describe('coverage projection', () => {
  it('does not hatch a view wholly inside coverage', () => {
    expect(projectScopeCoverage(geometry, { startHz: 35, endHz: 65 })).toEqual({
      covered: [{ start: 0, end: 1, sampleStart: 0.25, sampleEnd: 0.75 }], hatched: [],
    });
  });
  it('hatches requested edges and maps covered columns against granted view without stretching', () => {
    expect(projectScopeCoverage(geometry, { startHz: 0, endHz: 100 })).toEqual({
      covered: [{ start: 0.3, end: 0.7, sampleStart: 1 / 6, sampleEnd: 5 / 6 }],
      hatched: [
        { start: 0, end: 0.3, reason: 'view wider than coverage' },
        { start: 0.7, end: 1, reason: 'view wider than coverage' },
      ],
    });
  });
  it('clips coverage to granted view even when coverage metadata is wider', () => {
    const projection = projectScopeCoverage({ ...geometry, coverage: { startHz: 0, endHz: 100 } }, { startHz: 0, endHz: 100 });
    expect(projection.covered).toEqual([{ start: 0.2, end: 0.8, sampleStart: 0, sampleEnd: 1 }]);
  });
  it.each([
    [{ outOfRange: true, hasPixels: false }, 'outOfRange'],
    [{ hasPixels: false }, 'no pixels'],
  ] as const)('hatches explicit missing-data state %s', (facts, reason) => {
    expect(projectScopeCoverage({ ...geometry, ...facts }, { startHz: 0, endHz: 100 }))
      .toEqual({ covered: [], hatched: [{ start: 0, end: 1, reason }] });
  });
  it('fully hatches disjoint coverage', () => {
    expect(projectScopeCoverage(geometry, { startHz: 100, endHz: 200 }).covered).toEqual([]);
    expect(projectScopeCoverage(geometry, { startHz: 100, endHz: 200 }).hatched)
      .toEqual([{ start: 0, end: 1, reason: 'view wider than coverage' }]);
  });
});

describe('shouldHatchCoverageGap (MOR-4451)', () => {
  // Fakes from the producer types: the grant carries the truthful native
  // coverage, the requested view is the follow window being drawn.
  const nativeOf = (startHz: number, endHz: number): ScopeGranted['coverage'] => ({ startHz, endHz });
  it('leaves an offset native coverage of equal width as plain background', () => {
    expect(shouldHatchCoverageGap(nativeOf(30, 70), { startHz: 35, endHz: 75 })).toBe(false);
  });
  it('leaves a wider native coverage as plain background', () => {
    expect(shouldHatchCoverageGap(nativeOf(0, 100), { startHz: 35, endHz: 75 })).toBe(false);
  });
  it('hatches a truly narrower native coverage', () => {
    expect(shouldHatchCoverageGap(nativeOf(40, 60), { startHz: 30, endHz: 70 })).toBe(true);
  });
  it('hatches when the native coverage is unknown', () => {
    expect(shouldHatchCoverageGap(null, { startHz: 30, endHz: 70 })).toBe(true);
  });
});

describe('transient-lag projection (MOR-4451)', () => {
  const nativeOf = (startHz: number, endHz: number): ScopeGranted['coverage'] => ({ startHz, endHz });
  it('leaves the uncovered leading edge unhatched when the native coverage is as wide as the view', () => {
    const projection = projectScopeCoverage(geometry, { startHz: 35, endHz: 75 }, nativeOf(35, 75));
    expect(projection.hatched).toEqual([]);
    expect(projection.covered).toEqual([{
      start: 0, end: 0.875, sampleStart: 0.25, sampleEnd: 5 / 6,
    }]);
  });
  it('leaves fully disjoint frames unhatched when the native coverage is as wide as the view', () => {
    const projection = projectScopeCoverage(geometry, { startHz: 100, endHz: 200 }, nativeOf(100, 200));
    expect(projection).toEqual({ covered: [], hatched: [] });
  });
  it('keeps hatching when the native coverage is truly narrower than the view', () => {
    const projection = projectScopeCoverage(geometry, { startHz: 0, endHz: 100 }, nativeOf(40, 60));
    expect(projection.hatched).toEqual([
      { start: 0, end: 0.3, reason: 'view wider than coverage' },
      { start: 0.7, end: 1, reason: 'view wider than coverage' },
    ]);
  });
});

it('keeps coverage for producer TX-frozen bytes that retain pixels with the out-of-range flag', () => {
  const buffer = wire(fixtures.radio); const header = new DataView(buffer);
  header.setUint8(2, header.getUint8(2) | 1);
  const result = ingressScopeFrame(buffer, context);
  expect(result.ok).toBe(true); if (!result.ok) throw new Error(result.reason);
  expect(result.frame.outOfRange).toBe(true); expect(result.geometry.outOfRange).toBe(true);
  expect(result.geometry.hasPixels).toBe(true);
  expect(result.frame.pixels).toEqual(new Uint8Array(fixtures.radio.slice(56 + "scope-1".length)));
  expect(result.frame.pixels!.buffer).toBe(buffer);
  expect(projectScopeCoverage(result.geometry, result.geometry.view)).toEqual({
    covered: [{ start: 0, end: 1, sampleStart: 0, sampleEnd: 1 }], hatched: [],
  });
});

it('fully hatches producer header-only out-of-range bytes without pixels', () => {
  const result = ingressScopeFrame(wire(fixtures.outOfRange), context);
  expect(result.ok).toBe(true); if (!result.ok) throw new Error(result.reason);
  expect(result.geometry).toMatchObject({ outOfRange: true, hasPixels: false });
  expect(projectScopeCoverage(result.geometry, result.geometry.view)).toEqual({
    covered: [], hatched: [{ start: 0, end: 1, reason: 'outOfRange' }],
  });
});
