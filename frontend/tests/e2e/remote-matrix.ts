/**
 * The required matrix of the remote/browser E2E harness (MOR-4493), kept
 * OUTSIDE tests/e2e/remote/ on purpose: that directory is CommonJS-scoped
 * (tests/e2e/remote/package.json) so Playwright compiles the whole spec
 * graph — product modules included — through require(), whose native JSON
 * require needs no import attributes, while playwright.remote.config.ts
 * (the frontend's ESM scope) imports THIS module through Playwright's ESM
 * hooks. Both loaders compile it; each side sees one copy of the truth.
 */
export const REQUIRED_MATRIX = {
  projects: ['chromium', 'webkit'],
  rows: ['L1', 'L2', 'L3', 'P0', 'R-A1'],
  repeatEach: 2,
} as const;

/** The row id ("L1", "P0", "R-A1") a test title starts with, or null.
 * The REQUIRED_MATRIX rows are the authority: a title carrying any other
 * prefix is not a row of this suite (the matrix check fails a run whose
 * required rows are missing, so a mistyped row cannot pass). */
export function rowOf(title: string): string | null {
  for (const row of REQUIRED_MATRIX.rows) {
    if (title.startsWith(`${row}:`)) return row;
  }
  return null;
}
