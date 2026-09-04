'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const ROOT = path.resolve(__dirname, '..', '..');
const TARGET_ROOT = process.env.GATE_TARGET_ROOT ? path.resolve(process.env.GATE_TARGET_ROOT) : ROOT;
const routePolicy = require(path.join(ROOT, '.github/scripts/base-gate-policy-v1.js'));
const observer = require(path.join(ROOT, '.github/scripts/quick-v2-metadata-policy-v1.js'));
const readTarget = (relative) => fs.readFileSync(path.join(TARGET_ROOT, relative), 'utf8');

function topologyErrors({quick, review, migration}) {
  const errors = [];
  if (!quick.includes('\n  pull_request_target:\n') || !quick.includes('\n  workflow_run:\n') || quick.includes('\n  pull_request:\n')) {
    errors.push('quick observer must use hosted lifecycle and workflow-run events');
  }
  for (const event of ['opened', 'reopened', 'synchronize', 'ready_for_review', 'converted_to_draft', 'edited']) {
    if (!quick.includes(event)) errors.push(`quick observer is missing lifecycle event ${event}`);
  }
  for (const required of [
    'workflows: ["Tests (quick)"]', 'types: [completed]', 'permissions: {}', 'actions: read',
    'checks: read', 'contents: read', 'pull-requests: read', 'statuses: write',
    'Read-only legacy quick metadata observer', 'Exact-head metadata observation publisher',
    '.github/scripts/quick-v2-metadata-policy-v1.js', 'rebindForPublication',
  ]) {
    if (!quick.includes(required)) errors.push(`quick observer is missing ${required}`);
  }
  const statusWrites = quick.match(/statuses: write/gu) ?? [];
  const pins = quick.match(/actions\/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd/gu) ?? [];
  if (statusWrites.length !== 1 || pins.length !== 2) errors.push('publisher permission or action pin count changed');
  for (const forbidden of [
    'self-hosted', 'actions/checkout', 'actions/upload-artifact', 'actions/download-artifact',
    '/logs', '/artifacts', '/caches', 'secrets.', 'github.workflow_sha',
  ]) {
    if (quick.includes(forbidden)) errors.push(`quick observer contains forbidden surface: ${forbidden}`);
  }
  if (!review.includes('\n  pull_request_target:\n') || review.includes('\n  pull_request:\n') || !review.includes("context: 'Agent Review Gate v2 observe'")) {
    errors.push('review observation contract changed unexpectedly');
  }
  for (const required of [
    'must never be configured as required checks', 'metadata-only', 'zero self-hosted',
    'route_job_concordance', 'terminal_binding_coverage',
    'status POST are not atomic',
    'distinct from GitHub Actions app ID `15368`', '"strict": true',
  ]) {
    if (!migration.includes(required)) errors.push(`migration contract is missing: ${required}`);
  }
  return errors;
}

function sources() { return {quick: readTarget('.github/workflows/quick-v2.yml'), review: readTarget('.github/workflows/agent-review-gate-v2.yml'), migration: readTarget('docs/internals/base-controlled-required-gates.md')}; }
function canonicalPull(number, head, base) { const repo = {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}; return {number, state: 'open', base: {ref: 'main', sha: base, repo}, head: {sha: head, repo}}; }
function runPull(number, head, base) {
  const repo = {id: observer.REPOSITORY.id, name: observer.REPOSITORY.repo, url: `https://api.github.com/repos/${observer.REPOSITORY.fullName}`};
  return {id: 9000, run_attempt: 1, head_sha: head, pull_requests: [{number, head: {sha: head, repo}, base: {ref: 'main', sha: base, repo}}]};
}

test('trusted route policy rejects incomplete and injected path metadata', () => {
  const docs = routePolicy.classifyPullFiles([{filename: 'docs/guide.md'}, {filename: 'frontend/README.MD'}, {filename: 'mkdocs.yml'}], 3);
  assert.deepEqual(docs, {core: false, frontend: false, ci: false, docs: true});
  for (const filename of ['../README.md', 'docs/../src/radio.py']) {
    assert.throws(() => routePolicy.classifyPullFiles([{filename}], 1), undefined, filename);
  }
  for (const filename of ['frontend/README.md\n', 'frontend/README.rst\r\n']) {
    const result = routePolicy.classifyPullFiles([{filename}], 1);
    assert.equal(result.docs, false, filename);
    assert.equal(result.core || result.frontend || result.ci, true, filename);
  }
  assert.throws(() => routePolicy.classifyPullFiles([{filename: 'docs/a.md'}], 2));
  assert.throws(() => routePolicy.classifyPullFiles([{filename: 'docs/a.md'}], 3000));
});

test('quick v2 topology is hosted, metadata-only, and separately published', () => {
  assert.deepEqual(topologyErrors(sources()), []);
  assert.equal(observer.LEGACY_WORKFLOW.id, 270532868);
  assert.equal(observer.LEGACY_WORKFLOW.path, '.github/workflows/quick.yml');
  assert.equal(observer.OBSERVATION_CONTEXT, 'quick-v2-observe');
});

test('topology contract catches permission, execution, event, and pin regressions', () => {
  const original = sources();
  const mutations = [
    ['runs-on: ubuntu-latest', 'runs-on: [self-hosted, linux, build]'],
    ['permissions: {}', 'permissions:\n  statuses: write'],
    ['actions: read', 'actions: read\n      statuses: write'],
    ['with:\n          script:', 'uses: actions/upload-artifact@v4\n        with:\n          script:'],
    ['ed597411d8f924073f98dfc5c65a23a2325f34cd', 'v8'], ['workflow_run:', 'pull_request:'],
  ];
  for (const [from, to] of mutations) assert.notDeepEqual(topologyErrors({...original, quick: original.quick.replace(from, to)}), []);
});

test('edited lifecycle events are no-ops unless base metadata changed', () => {
  assert.equal(observer.relevantLifecycle({action: 'opened'}), true);
  assert.equal(observer.relevantLifecycle({action: 'converted_to_draft'}), true);
  assert.equal(observer.relevantLifecycle({action: 'edited', changes: {base: {ref: {from: 'release'}}}}), true);
  assert.equal(observer.relevantLifecycle({action: 'edited', changes: {title: {from: 'old'}}}), false);
  assert.equal(observer.relevantLifecycle({action: 'edited', changes: {body: {from: 'old'}}}), false);
});

test('terminal topology refuses skipped success and injected or incomplete jobs', () => {
  const run = {conclusion: 'success', run_attempt: 2};
  const routes = {core: true, frontend: false, ci: false, docs: false};
  const valid = [
    {name: 'classify', status: 'completed', conclusion: 'success', run_attempt: 2, runner_name: 'GitHub Actions 1', labels: ['ubuntu-latest']},
    {name: 'quick', status: 'completed', conclusion: 'success', run_attempt: 2, runner_name: 'mm-build-core-1', labels: ['self-hosted', 'linux', 'build']},
  ];
  assert.deepEqual(observer.evaluateJobTopology(run, valid, routes, false), {ok: true, code: 'terminal_match', routeConcordance: true});
  const skipped = structuredClone(valid); skipped[1].conclusion = 'skipped'; skipped[1].runner_name = null;
  assert.equal(observer.evaluateJobTopology(run, skipped, routes, false).code, 'skipped_substantive');
  assert.equal(observer.evaluateJobTopology(run, [...valid, {...valid[1], name: 'quick-shadow'}], routes, false).code, 'route_mismatch');
  assert.equal(observer.evaluateJobTopology({...run, conclusion: 'cancelled'}, valid, routes, false).code, 'terminal_failure');
  assert.equal(observer.evaluateJobTopology(run, valid, {...routes, docs: true}, false).code, 'route_mismatch');
});

test('exact pull binding rejects stale base/head and identifies forks', () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40); const pull = canonicalPull(41, head, base);
  assert.deepEqual(observer.bindPull(pull, base, head), {headSha: head, baseSha: base, sameRepository: true});
  assert.throws(() => observer.bindPull(pull, 'c'.repeat(40), head), /live main/u);
  assert.throws(() => observer.bindPull(pull, base, 'd'.repeat(40)), /no longer current/u);
  pull.head.repo = {id: 99, full_name: 'outside/fork'};
  assert.equal(observer.bindPull(pull, base, head).sameRepository, false);
});

test('legacy run tuple rejects stale base and same-head different-PR reuse', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40);
  const binding = observer.bindRunPullRequest(runPull(41, head, base), base);
  assert.deepEqual(binding, {pullNumber: 41, headSha: head, baseSha: base});
  assert.throws(() => observer.bindRunPullRequest(runPull(41, head, base), 'c'.repeat(40)), /recorded base is not live main/u);
  for (const pullRequests of [[], [runPull(41, head, base).pull_requests[0], runPull(42, head, base).pull_requests[0]]]) {
    const run = runPull(41, head, base); run.pull_requests = pullRequests;
    assert.throws(() => observer.bindRunPullRequest(run, base), /exactly one pull request association/u);
  }
  const github = {paginate: async () => [canonicalPull(42, head, base)]};
  await assert.rejects(observer.assertUniqueCurrentPullMatchesRun(github, observer.REPOSITORY.owner, observer.REPOSITORY.repo, binding), /does not match the run association/u);
});

test('final publication snapshot rejects changed attempt and job topology', () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40);
  const run = {...runPull(41, head, base), status: 'completed', conclusion: 'success'};
  const result = {runId: run.id, runAttempt: run.run_attempt, runConclusion: run.conclusion, baseSha: base, headSha: head};
  const binding = {pullNumber: 41, headSha: head, baseSha: base};
  assert.deepEqual(observer.assertRunPublicationSnapshot(run, result, binding), binding);
  assert.throws(() => observer.assertRunPublicationSnapshot({...run, run_attempt: 2}, result, binding), /attempt or association changed/u);
  const jobs = [{id: 1, name: 'quick', status: 'completed', conclusion: 'success', run_attempt: 1, runner_id: 2, runner_name: 'runner', labels: ['build', 'linux', 'self-hosted']}];
  const topology = observer.jobTopologySnapshot(jobs);
  assert.doesNotThrow(() => observer.assertJobTopologyUnchanged(jobs, topology, head));
  assert.throws(() => observer.assertJobTopologyUnchanged([{...jobs[0], conclusion: 'failure'}], topology, head), /job topology changed/u);
});

test('final publication rebind turns head or base movement into failure', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40);
  const result = {kind: 'lifecycle', code: 'admitted', state: 'pending', publish: true, targetSha: head, pullNumber: 41, baseSha: base, headSha: head, runId: null, runAttempt: null, jobTopology: null, metrics: {false_success_publications: 0}};
  const githubFor = (pull, liveMain) => ({request: async (route) => {
    if (route.includes('/branches/')) return {data: {commit: {sha: liveMain}}};
    if (route.includes('/pulls/')) return {data: pull};
    throw new Error(`unexpected route ${route}`);
  }});
  const movedHead = await observer.rebindForPublication({github: githubFor(canonicalPull(41, 'd'.repeat(40), base), base), result});
  assert.deepEqual([movedHead.code, movedHead.state, movedHead.targetSha], ['stale_head', 'failure', head]);
  const movedBase = await observer.rebindForPublication({github: githubFor(canonicalPull(41, head, 'c'.repeat(40)), 'c'.repeat(40)), result});
  assert.deepEqual([movedBase.code, movedBase.state, movedBase.targetSha], ['stale_base', 'failure', head]);
});

function treeGithub(finalEntry, options = {}) {
  const root = '1'.repeat(40); const githubTree = '2'.repeat(40); const workflowsTree = '3'.repeat(40);
  const responses = {[root]: [{path: '.github', mode: '040000', type: 'tree', sha: githubTree}], [githubTree]: [{path: 'workflows', mode: '040000', type: 'tree', sha: workflowsTree}], [workflowsTree]: [finalEntry]};
  return {request: async (route, args) => {
    if (route.includes('/git/commits/')) return {data: {tree: {sha: root}}};
    if (route.includes('/git/trees/')) {
      const entries = responses[args.tree_sha] ?? [];
      return {data: {truncated: options.truncated === args.tree_sha, tree: options.duplicate === args.tree_sha ? [...entries, ...entries] : entries}};
    }
    throw new Error(`unexpected route ${route}`);
  }};
}

test('tree walk accepts only complete 040000 parents and a 100644 blob', async () => {
  const blob = '4'.repeat(40); const commit = 'a'.repeat(40);
  const entry = {path: 'quick.yml', mode: '100644', type: 'blob', sha: blob};
  assert.deepEqual(await observer.getTreeEntry(treeGithub(entry), 'rigplane', 'rigplane-core', commit, '.github/workflows/quick.yml'), {mode: '100644', type: 'blob', sha: blob});
  for (const [mode, type] of [['100755', 'blob'], ['120000', 'blob'], ['160000', 'commit']]) {
    await assert.rejects(observer.getTreeEntry(treeGithub({...entry, mode, type}), 'rigplane', 'rigplane-core', commit, '.github/workflows/quick.yml'), /regular non-executable/u);
  }
  await assert.rejects(observer.getTreeEntry(treeGithub(entry, {duplicate: '3'.repeat(40)}), 'rigplane', 'rigplane-core', commit, '.github/workflows/quick.yml'), /ambiguous/u);
  await assert.rejects(observer.getTreeEntry(treeGithub(entry, {truncated: '2'.repeat(40)}), 'rigplane', 'rigplane-core', commit, '.github/workflows/quick.yml'), /incomplete/u);
  await assert.rejects(observer.getTreeEntry(treeGithub(entry), 'rigplane', 'rigplane-core', commit, '.github/../quick.yml'), /path is invalid/u);
});
