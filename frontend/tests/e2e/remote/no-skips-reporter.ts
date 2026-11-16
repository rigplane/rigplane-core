import type {
  FullConfig,
  Reporter,
  Suite,
  TestCase,
  TestResult,
} from '@playwright/test';

/**
 * The no-skips gate of the remote E2E matrix (playwright.remote.config.ts):
 * a run is failing unless every test the config planned ran and passed.
 * A skipped or `fixme` row, and a planned test missing from the results
 * (filtered away, e.g. by a project mismatch that kept it out of one
 * browser), both count as failures — softened rows are forbidden (C5b
 * adviser brief §6).
 */

/** What the run planned: every test case onBegin saw. */
export interface PlannedTest {
  id: string;
  title: string;
}

/** What the run recorded for one test case. */
export interface RecordedResult {
  testId: string;
  status: 'passed' | 'failed' | 'timedOut' | 'interrupted' | 'skipped' | 'didNotRun';
}

const PASSED = 'passed';

/** The problems of a run: empty means every planned test ran and passed. */
export function noSkipProblems(
  planned: PlannedTest[],
  results: RecordedResult[],
): string[] {
  const problems: string[] = [];
  for (const test of planned) {
    const recorded = results.filter((result) => result.testId === test.id);
    if (recorded.length === 0) {
      problems.push(`${test.title}: no result (filtered out or never ran)`);
      continue;
    }
    const bad = recorded.find((result) => result.status !== PASSED);
    if (!bad) continue;
    if (bad.status === 'skipped') {
      problems.push(`${test.title}: skipped (skip/fixme rows are forbidden)`);
    } else {
      problems.push(`${test.title}: ${bad.status} (did not complete)`);
    }
  }
  return problems;
}

export default class NoSkipsReporter implements Reporter {
  private planned: PlannedTest[] = [];
  private results: RecordedResult[] = [];

  onBegin(_config: FullConfig, suite: Suite): void {
    this.planned = suite.allTests().map((test) => ({
      id: test.id,
      title: test.titlePath().slice(1).join(' › '),
    }));
  }

  onTestEnd(test: TestCase, result: TestResult): void {
    this.results.push({ testId: test.id, status: result.status });
  }

  onEnd(): { status: 'failed'; message: string } | undefined {
    const problems = noSkipProblems(this.planned, this.results);
    if (problems.length === 0) return undefined;
    const message = [
      'no-skips gate: the run is failing because planned tests did not run and pass:',
      ...problems,
    ].join('\n');
    console.error(message);
    return { status: 'failed', message };
  }
}
