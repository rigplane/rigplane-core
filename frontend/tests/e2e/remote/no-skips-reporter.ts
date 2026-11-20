import type { Reporter, TestCase, TestResult } from '@playwright/test/reporter';

/**
 * The no-skips gate of the remote E2E matrix (playwright.remote.config.ts):
 * a run is failing unless every required (project x row x repetition)
 * case ran and passed. A skipped or `fixme` row, and a required case
 * missing from the results (filtered away by `--project`, `--grep` or
 * `--repeat-each`), both count as failures — softened rows are forbidden
 * (C5b adviser brief §6).
 */

/** The matrix the remote config requires, independent of any CLI filter
 * (`--project`, `--grep`, `--repeat-each`): every row in every project,
 * `repeatEach` times. playwright.remote.config.ts is built from it. */
export const REQUIRED_MATRIX = {
  projects: ['chromium', 'webkit'],
  rows: ['L1', 'L2', 'L3', 'L4', 'L5', 'P0'],
  repeatEach: 2,
} as const;

const PASSED = 'passed';

/** What the run recorded for one executed test case. */
export interface RecordedCase {
  row: string;
  project: string;
  repeat: number;
  status: 'passed' | 'failed' | 'timedOut' | 'interrupted' | 'skipped';
}

/** The row id ("L1", "P0") a test title starts with, or null. */
export function rowOf(title: string): string | null {
  return /^([A-Z]\d+):/.exec(title)?.[1] ?? null;
}

/**
 * The problems of a run: empty means every required (project x row x
 * repetition) combination ran and passed. The requirement comes from
 * REQUIRED_MATRIX, never from the suite Playwright hands the reporter,
 * which is already filtered by `--project`, `--grep` and `--repeat-each`.
 */
export function matrixProblems(recorded: RecordedCase[]): string[] {
  const problems: string[] = [];
  for (const project of REQUIRED_MATRIX.projects) {
    for (const row of REQUIRED_MATRIX.rows) {
      for (let repeat = 0; repeat < REQUIRED_MATRIX.repeatEach; repeat += 1) {
        const label = `${project} › ${row} #${repeat + 1}`;
        const found = recorded.filter(
          (c) => c.project === project && c.row === row && c.repeat === repeat,
        );
        if (found.length === 0) {
          problems.push(`${label}: no result (filtered out or never ran)`);
        } else if (found.some((c) => c.status === 'skipped')) {
          problems.push(`${label}: skipped (skip/fixme rows are forbidden)`);
        } else {
          const bad = found.find((c) => c.status !== PASSED);
          if (bad) problems.push(`${label}: ${bad.status} (did not complete)`);
        }
      }
    }
  }
  return problems;
}

export default class NoSkipsReporter implements Reporter {
  private recorded: RecordedCase[] = [];

  onTestEnd(test: TestCase, result: TestResult): void {
    const row = rowOf(test.titlePath().at(-1) ?? '');
    if (row === null) return;
    this.recorded.push({
      row,
      project: test.parent.project()?.name ?? '',
      repeat: test.repeatEachIndex,
      status: result.status as RecordedCase['status'],
    });
  }

  async onEnd(): Promise<{ status: 'failed' } | undefined> {
    const problems = matrixProblems(this.recorded);
    if (problems.length === 0) return undefined;
    const message = [
      'no-skips gate: the run is failing because required matrix cases did not run and pass:',
      ...problems,
    ].join('\n');
    console.error(message);
    return { status: 'failed' };
  }
}
