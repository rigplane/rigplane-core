import { describe, expect, it } from 'vitest';
import fixtures from './fixtures/scope-wire.json';
import { parseCurrentScopeFrame, projectScopeCoverage } from '../scope-frame';
import type { ScopeGeometry } from '../scope-frame';

const context = { source: 'radio' as const, sourceId: 'scope-1', epoch: 7 };
const wire = (bytes: number[]) => Uint8Array.from(bytes).buffer;

describe('current scope wire → ScopeFrame', () => {
  it.each(['radio', 'audio'] as const)('decodes real %s encoder bytes without copying', (name) => {
    const buffer = wire(fixtures[name]);
    const source = name === 'radio' ? 'radio' : 'audio_fft';
    const result = parseCurrentScopeFrame(buffer, { ...context, source });
    expect(result.ok).toBe(true);
    if (!result.ok) throw new Error(result.reason);
    expect(result.frame).toMatchObject({
      source, sourceId: 'scope-1', epoch: 7, sentAtMs: null,
      receiver: name === 'radio' ? 0 : 1, seq: name === 'radio' ? 65535 : 17,
      viewStartHz: name === 'radio' ? 14_000_000 : 7_100_000,
      coverageEndHz: name === 'radio' ? 14_100_000 : 7_103_000,
      width: name === 'radio' ? 4 : 3, outOfRange: false,
    });
    expect(result.frame.pixels?.buffer).toBe(buffer);
    expect(result.frame.pixels?.byteOffset).toBe(16);
    expect(result.frame.pixels?.byteLength).toBe(result.frame.width);
  });
  it('preserves outOfRange and represents absent pixels explicitly', () => {
    const result = parseCurrentScopeFrame(wire(fixtures.outOfRange), context);
    expect(result.ok && result.frame).toMatchObject({ receiver: 1, outOfRange: true, pixels: null, width: 0 });
  });
  it('returns typed malformed results for short, wrong-type and truncated messages', () => {
    expect(parseCurrentScopeFrame(new ArrayBuffer(0), context)).toEqual({ ok: false, reason: 'short header' });
    const wrong = [...fixtures.radio]; wrong[0] = 0x10;
    expect(parseCurrentScopeFrame(wire(wrong), context)).toEqual({ ok: false, reason: 'message type' });
    expect(parseCurrentScopeFrame(wire(fixtures.radio.slice(0, -1)), context)).toEqual({ ok: false, reason: 'pixel length' });
    const backwards = wire(fixtures.radio);
    new DataView(backwards).setUint32(7, 13_000_000, true);
    expect(parseCurrentScopeFrame(backwards, context)).toEqual({ ok: false, reason: 'frequency range' });
  });
});

const geometry: ScopeGeometry = {
  source: 'radio', sourceId: 'scope-1', receiver: 0,
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
    [{ outOfRange: true }, 'outOfRange'],
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
