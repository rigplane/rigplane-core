'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..', '..');
const TARGET_ROOT = process.env.GATE_TARGET_ROOT
  ? path.resolve(process.env.GATE_TARGET_ROOT)
  : ROOT;

const WORKFLOWS = Object.freeze({
  gate: '.github/workflows/agent-review-gate.yml',
  gateV2: '.github/workflows/agent-review-gate-v2.yml',
  quickV2: '.github/workflows/quick-v2.yml',
  docsOnlyQuick: '.github/workflows/docs-only-quick.yml',
});

// Kept byte-identical to agent-review-gate.test.js (eval + concurrency pinning).
const OPEN_PR_PREDICATE =
  "(github.event.pull_request.state || github.event.issue.state || 'open') == 'open'";
const QUICK_V2_OPEN_PR_PREDICATE =
  "(github.event.pull_request.state || 'open') == 'open'";
const DOCS_ONLY_CONCURRENCY_GROUP =
  'group: docs-only-quick-${{ github.event.pull_request.number }}';

function readWorkflow(relativePath) {
  return fs.readFileSync(path.join(TARGET_ROOT, relativePath), 'utf8');
}

function jobBlock(workflowText, jobId) {
  const marker = `\n  ${jobId}:\n`;
  const start = workflowText.indexOf(marker);
  assert.notEqual(start, -1, `missing job ${jobId}`);
  const rest = workflowText.slice(start + marker.length);
  const nextJob = rest.search(/\n  [a-z][a-z0-9-]*:\n/u);
  return nextJob === -1 ? rest : rest.slice(0, nextJob);
}

function scalarAfter(block, key) {
  const match = new RegExp(`^ {4}${key}: (.+)$`, 'mu').exec(block);
  return match ? match[1].trim() : null;
}

function assertJobIfStartsWith(block, jobId, prefix) {
  const value = scalarAfter(block, 'if');
  assert.notEqual(value, null, `${jobId} missing job if`);
  assert.ok(
    value.startsWith(prefix),
    `${jobId} if must start with open-state guard; got ${value}`,
  );
}

function concurrencySection(workflowText) {
  const match = /^concurrency:\n((?: {2}.+\n)+)/mu.exec(workflowText);
  assert.notEqual(match, null, 'missing workflow concurrency');
  return match[1];
}

test('MOR-4524 agent review gate keeps trigger legs and guards closed PRs on job and group', () => {
  const workflow = readWorkflow(WORKFLOWS.gate);
  assert.match(
    workflow,
    /types: \[opened, reopened, synchronize, ready_for_review, labeled, unlabeled\]/u,
    'pull_request labeled/unlabeled',
  );
  assert.match(workflow, /pull_request_review:\n {4}types: \[submitted, dismissed\]/u);
  assert.match(workflow, /issue_comment:\n {4}types: \[created, edited, deleted\]/u);

  const agentReview = jobBlock(workflow, 'agent-review');
  assertJobIfStartsWith(agentReview, 'agent-review', `${OPEN_PR_PREDICATE} &&`);

  const groupLine = concurrencySection(workflow)
    .split('\n')
    .find((line) => line.startsWith('  group: '));
  assert.ok(groupLine, 'gate concurrency group');
  assert.ok(
    groupLine.includes(OPEN_PR_PREDICATE),
    'gate concurrency group must carry the open-state predicate',
  );
  assert.match(concurrencySection(workflow), /cancel-in-progress: true/u);
});

test('MOR-4524 agent review gate v2 guards closed PRs on edited and issue_comment legs', () => {
  const workflow = readWorkflow(WORKFLOWS.gateV2);
  assert.match(
    workflow,
    /pull_request_target:\n {4}branches: \[main\]\n {4}types: \[opened, reopened, synchronize, ready_for_review, converted_to_draft, edited\]/u,
  );
  assert.match(workflow, /issue_comment:\n {4}types: \[created, edited, deleted\]/u);

  const publish = jobBlock(workflow, 'publish');
  assertJobIfStartsWith(publish, 'publish', `${OPEN_PR_PREDICATE} &&`);

  const groupLine = concurrencySection(workflow)
    .split('\n')
    .find((line) => line.startsWith('  group: '));
  assert.ok(groupLine?.includes(OPEN_PR_PREDICATE), 'v2 concurrency open-state predicate');
  assert.match(concurrencySection(workflow), /cancel-in-progress: false/u);
});

test('MOR-4524 quick v2 observer skips closed PRs and publisher skips skipped observe', () => {
  const workflow = readWorkflow(WORKFLOWS.quickV2);
  assert.match(
    workflow,
    /pull_request_target:\n {4}branches: \[main\]\n {4}types: \[opened, reopened, synchronize, ready_for_review, converted_to_draft, edited\]/u,
    'edited lifecycle trigger',
  );

  const observe = jobBlock(workflow, 'observe');
  assert.equal(
    scalarAfter(observe, 'if'),
    QUICK_V2_OPEN_PR_PREDICATE,
    'observe open-state guard',
  );

  const publish = jobBlock(workflow, 'publish');
  assert.equal(scalarAfter(publish, 'needs'), 'observe', 'publish needs observe');
  assert.equal(
    scalarAfter(publish, 'if'),
    "always() && needs.observe.result != 'skipped'",
    'publish must not run when observe is skipped',
  );
});

test('MOR-4524 docs-only quick cancels superseded per-PR status runs', () => {
  const workflow = readWorkflow(WORKFLOWS.docsOnlyQuick);
  const concurrency = concurrencySection(workflow);
  assert.ok(
    concurrency.includes(DOCS_ONLY_CONCURRENCY_GROUP),
    'docs-only concurrency group key',
  );
  assert.match(concurrency, /cancel-in-progress: true/u);
});