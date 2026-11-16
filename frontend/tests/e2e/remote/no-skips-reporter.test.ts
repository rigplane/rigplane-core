import { describe, expect, it } from 'vitest';
import {
  noSkipProblems,
  type PlannedTest,
  type RecordedResult,
} from './no-skips-reporter';

/**
 * Unit tests for the no-skips rule of the remote E2E harness
 * (playwright.remote.config.ts): a run is failing unless every test the
 * config planned actually ran and passed. A skipped or `fixme` test, and a
 * test missing from the results (filtered away, e.g. by a project
 * mismatch), must both count as failures — the E2E matrix forbids
 * softened rows (adviser brief §6).
 */

const planned = (title: string): PlannedTest => ({ id: `${title}-id`, title });
const result = (
  title: string,
  status: RecordedResult['status'],
): RecordedResult => ({ testId: `${title}-id`, status });

describe('noSkipProblems', () => {
  it('accepts a run where every planned test passed', () => {
    const problems = noSkipProblems(
      [planned('L1'), planned('L2')],
      [result('L1', 'passed'), result('L2', 'passed')],
    );
    expect(problems).toEqual([]);
  });

  it('fails the run when a test was skipped', () => {
    const problems = noSkipProblems(
      [planned('L1'), planned('L3')],
      [result('L1', 'passed'), result('L3', 'skipped')],
    );
    expect(problems).toEqual(['L3: skipped (skip/fixme rows are forbidden)']);
  });

  it('fails the run when a planned test has no result (filtered out)', () => {
    const problems = noSkipProblems(
      [planned('L1'), planned('P0')],
      [result('L1', 'passed')],
    );
    expect(problems).toEqual([
      'P0: no result (filtered out or never ran)',
    ]);
  });

  it('fails the run when a test did not finish', () => {
    const problems = noSkipProblems(
      [planned('L2')],
      [result('L2', 'interrupted')],
    );
    expect(problems).toEqual(['L2: interrupted (did not complete)']);
  });

  it('reports every problem, not only the first', () => {
    const problems = noSkipProblems(
      [planned('L1'), planned('L2'), planned('L3')],
      [result('L1', 'skipped'), result('L3', 'timedOut')],
    );
    expect(problems).toEqual([
      'L1: skipped (skip/fixme rows are forbidden)',
      'L2: no result (filtered out or never ran)',
      'L3: timedOut (did not complete)',
    ]);
  });
});
