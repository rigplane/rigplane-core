import { describe, expect, it } from 'vitest';
import {
  checkAdvanceAndSettle,
  classifyHeldKeyCatch,
  requirePopulatedSeries,
  requireTransientSeamObserved,
} from '../../../../tests/e2e/held-key/held-key-analysis';

const span = (fromHz: number, toHz: number, count: number) =>
  Array.from({ length: count }, (_, i) => ({
    t: i * 33,
    v: fromHz + ((toHz - fromHz) * i) / (count - 1),
  }));

describe('held-key analysis', () => {
  it('rejects empty and all-missing series instead of passing', () => {
    expect(() => requirePopulatedSeries('vfo', { samples: 0, missing: 0 }, 10))
      .toThrow(/valid samples/);
    expect(() => requirePopulatedSeries('vfo', { samples: 0, missing: 120 }, 10))
      .toThrow(/valid samples/);
    expect(() => requirePopulatedSeries('vfo', { samples: 9, missing: 200 }, 10))
      .toThrow(/valid samples/);
    expect(() => requirePopulatedSeries('vfo', { samples: 10, missing: 200 }, 10))
      .not.toThrow();
  });
  it('rejects a frozen rendered series that never advances', () => {
    const flat = Array.from({ length: 60 }, (_, i) => ({ t: i * 33, v: 14_250_000 }));
    expect(() => checkAdvanceAndSettle({
      name: 'centre', values: flat, confirmedHz: 14_254_000,
      toleranceHz: 500, minimumAdvanceHz: 1_000,
    })).toThrow(/advanced/);
  });
  it('rejects all-missing values before checking advance or settle', () => {
    const missing = Array.from({ length: 60 }, (_, i) => ({ t: i * 33, v: null }));
    expect(() => checkAdvanceAndSettle({
      name: 'marker', values: missing, confirmedHz: 14_254_000,
      toleranceHz: 500, minimumAdvanceHz: 1_000,
    })).toThrow(/no valid samples/);
  });
  it('accepts a series that advances and settles at the confirmed frequency', () => {
    const result = checkAdvanceAndSettle({
      name: 'centre', values: span(14_250_000, 14_254_000, 120),
      confirmedHz: 14_254_000, toleranceHz: 500, minimumAdvanceHz: 1_000,
    });
    expect(result.advanceHz).toBe(4_000);
    expect(result.settleHz).toBe(0);
  });
  it('rejects a series that advances but never settles at the confirmed frequency', () => {
    expect(() => checkAdvanceAndSettle({
      name: 'marker', values: span(14_250_000, 14_252_000, 120),
      confirmedHz: 14_254_000, toleranceHz: 500, minimumAdvanceHz: 1_000,
    })).toThrow(/settle/);
  });
  it('fails a lagged row that never observed a partial frame', () => {
    expect(() => requireTransientSeamObserved({
      responseDelayS: 0.1, partialCount: 0, hatchedCount: 0,
    })).toThrow(/transient not exercised/);
  });
  it('fails a lagged row whose transient margin hatched', () => {
    expect(() => requireTransientSeamObserved({
      responseDelayS: 0.1, partialCount: 7, hatchedCount: 3,
    })).toThrow(/hatched/);
  });
  it('passes a lagged row with observed partials and no hatch', () => {
    expect(requireTransientSeamObserved({
      responseDelayS: 0.1, partialCount: 7, hatchedCount: 0,
    })).toBeUndefined();
  });
  it('passes a delay-free row without requiring partials', () => {
    expect(requireTransientSeamObserved({
      responseDelayS: 0, partialCount: 0, hatchedCount: 0,
    })).toBeUndefined();
  });
  it('classifies a server-start failure as a skip carrying the reason', () => {
    const error = new Error('journey server on port 21500 exited with code 1 before it answered');
    const verdict = classifyHeldKeyCatch({
      setupDone: false, serverReady: false, framesObserved: 0, error, row: 'zoomed, d0',
    });
    expect(verdict.disposition).toBe('skip');
    if (verdict.disposition !== 'skip') throw new Error('unreachable');
    expect(verdict.reason).toContain('server');
    expect(verdict.reason).toContain('exited with code 1');
  });
  it('classifies zero frames after start as a skip carrying the reason', () => {
    const error = new Error('expect.poll Timeout 30000ms exceeded');
    const verdict = classifyHeldKeyCatch({
      setupDone: false, serverReady: true, framesObserved: 0, error, row: 'zoomed, d0',
    });
    expect(verdict.disposition).toBe('skip');
    if (verdict.disposition !== 'skip') throw new Error('unreachable');
    expect(verdict.reason).toContain('zero');
    expect(verdict.reason).toContain('Timeout');
  });
  it('fails with the original error when setup breaks after frames arrive', () => {
    const error = new Error('boom');
    expect(classifyHeldKeyCatch({
      setupDone: false, serverReady: true, framesObserved: 12, error, row: 'zoomed, d0',
    })).toEqual({ disposition: 'fail', error });
  });
  it('fails with the original error after setup completed', () => {
    const error = new Error('boom');
    expect(classifyHeldKeyCatch({
      setupDone: true, serverReady: true, framesObserved: 300, error, row: 'zoomed, d0',
    })).toEqual({ disposition: 'fail', error });
  });
});
