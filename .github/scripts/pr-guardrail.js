'use strict';

/** Verbatim rule text; CLAUDE.md §Guardrails must match this string exactly. */
const PR_TEST_PATH_RULE_TEXT =
  'Test paths (excluded from the line limit): any path with a directory segment named tests, test, __tests__, testkit, fixtures or test_fixtures/test-fixtures (e.g. tests/fixtures/...), plus spec/test files by name: *_test.*, test_*.py, *.test.*, *.spec.*, *_spec.*, conftest.py.';

/**
 * Single matcher for PR test paths (directory segments and filename patterns).
 * @type {RegExp}
 */
const PR_TEST_PATH_RE =
  /(?:^|\/)(?:tests|test|__tests__|testkit|fixtures|test_fixtures|test-fixtures)(?:\/|$)|(?:^|\/)(?:[^/]*_test\.[^/]+|test_[^/]+\.py|[^/]*\.test\.[^/]+|[^/]*\.spec\.[^/]+|[^/]*_spec\.[^/]+|conftest\.py)$/u;

/**
 * @param {string} posixPath repository-relative path using `/` separators
 * @returns {boolean}
 */
function isPrTestPath(posixPath) {
  const normalized = posixPath.replace(/\\/g, '/');
  return PR_TEST_PATH_RE.test(normalized);
}

/**
 * @param {ReadonlyArray<{path: string, additions?: number, deletions?: number}>} changedFiles
 * @returns {{fileCount: number, productChangedLines: number, totalChangedLines: number}}
 */
function summarizePrGuardrailMetrics(changedFiles) {
  let productChangedLines = 0;
  let totalChangedLines = 0;
  const paths = new Set();

  for (const entry of changedFiles) {
    const additions = Number(entry.additions) || 0;
    const deletions = Number(entry.deletions) || 0;
    const delta = additions + deletions;
    totalChangedLines += delta;
    paths.add(entry.path);
    if (!isPrTestPath(entry.path)) {
      productChangedLines += delta;
    }
  }

  return {
    fileCount: paths.size,
    productChangedLines,
    totalChangedLines,
  };
}

const GUARDRAIL = Object.freeze({
  HARD_CEILING_FILES: 10,
  HARD_CEILING_PRODUCT_LINES: 900,
  SOFT_THRESHOLD_FILES: 6,
  SOFT_THRESHOLD_PRODUCT_LINES: 600,
});

module.exports = {
  GUARDRAIL,
  PR_TEST_PATH_RULE_TEXT,
  PR_TEST_PATH_RE,
  isPrTestPath,
  summarizePrGuardrailMetrics,
};