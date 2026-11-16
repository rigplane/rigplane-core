'use strict';

const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const assert = require('node:assert/strict');

const {
  GUARDRAIL,
  PR_TEST_PATH_RULE_TEXT,
  isPrTestPath,
  summarizePrGuardrailMetrics,
} = require('./pr-guardrail.js');

const REPO_ROOT = path.join(__dirname, '..', '..');
const CLAUDE_PATH = path.join(REPO_ROOT, 'CLAUDE.md');

test('GUARDRAIL hard ceiling is 10 files and 900 product-code lines', () => {
  assert.equal(GUARDRAIL.HARD_CEILING_FILES, 10);
  assert.equal(GUARDRAIL.HARD_CEILING_PRODUCT_LINES, 900);
});

test('directory-segment test paths are excluded from product line counts', () => {
  const cases = [
  'tests/test_radio.py',
  'tests/integration/foo.py',
  'tests/fixtures/capture.json',
  'frontend/tests/e2e/a.spec.ts',
  'src/rigplane/testkit/harness.py',
  'frontend/src/__tests__/panel.test.ts',
  'fixtures/golden.png',
  'nested/test/unit.py',
  'pkg/test_fixtures/a.py',
  'pkg/test-fixtures/b.py',
  ];
  for (const candidate of cases) {
    assert.equal(isPrTestPath(candidate), true, candidate);
  }
});

test('filename-pattern test paths are excluded from product line counts', () => {
  const cases = [
  'src/rigplane/foo_test.py',
  'frontend/src/a.test.ts',
  'frontend/src/a.spec.ts',
  'frontend/src/a_spec.ts',
  'tests/conftest.py',
  'src/test_spam.py',
  'lib/widget_test.mjs',
  ];
  for (const candidate of cases) {
    assert.equal(isPrTestPath(candidate), true, candidate);
  }
});

test('product paths are not test paths', () => {
  const cases = [
  'src/rigplane/radio.py',
  'frontend/src/App.svelte',
  'docs/PROJECT.md',
  'src/rigplane/contest.py',
  'src/rigplane/testing/foo.py',
  'test-data/seed.toml',
  'frontend/src/lib/latest.ts',
  'CLAUDE.md',
  ];
  for (const candidate of cases) {
    assert.equal(isPrTestPath(candidate), false, candidate);
  }
});

test('summarizePrGuardrailMetrics counts every file but only non-test lines', () => {
  const metrics = summarizePrGuardrailMetrics([
    {path: 'src/radio.py', additions: 400, deletions: 100},
    {path: 'tests/test_radio.py', additions: 800, deletions: 200},
    {path: 'frontend/foo.test.ts', additions: 50, deletions: 0},
  ]);
  assert.equal(metrics.fileCount, 3);
  assert.equal(metrics.productChangedLines, 500);
  assert.equal(metrics.totalChangedLines, 1550);
});

test('CLAUDE.md §Guardrails documents the verbatim test-path rule and 900 product-code ceiling', () => {
  const claude = fs.readFileSync(CLAUDE_PATH, 'utf8');
  assert.ok(claude.includes(PR_TEST_PATH_RULE_TEXT));
  assert.match(claude, /10 files · 900 product-code changed lines/u);
  assert.match(claude, /600 product-code changed lines/u);
  assert.match(claude, /test paths are not excluded/u);
});