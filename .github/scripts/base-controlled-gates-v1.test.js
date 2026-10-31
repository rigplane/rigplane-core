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
    'self-hosted, linux, build', 'actions/checkout', 'actions/upload-artifact', 'actions/download-artifact',
    '/logs', '/artifacts', '/caches', 'secrets.', 'github.workflow_sha',
  ]) {
    if (quick.includes(forbidden)) errors.push(`quick observer contains forbidden surface: ${forbidden}`);
  }
  if (!review.includes('\n  pull_request_target:\n') || review.includes('\n  pull_request:\n') || !review.includes("context: 'Agent Review Gate v2 observe'")) {
    errors.push('review observation contract changed unexpectedly');
  }
  for (const required of [
    'must never be configured as required checks', 'metadata-only',
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
  // Machine-readable data files under docs/ are not documentation: they must
  // route to a substantive quick run, never the docs-only path (MOR-2580).
  assert.deepEqual(routePolicy.classifyPullFiles([{filename: 'docs/internals/ui-radio-control-contract.toml'}], 1), {core: true, frontend: false, ci: false, docs: false});
  assert.deepEqual(routePolicy.classifyPullFiles([{filename: 'docs/guide.md'}, {filename: 'docs/validation/templates/x6200.json'}], 2), {core: true, frontend: false, ci: false, docs: false});
  assert.deepEqual(routePolicy.classifyPullFiles([{filename: '.claude/settings.json'}], 1), {core: false, frontend: false, ci: false, docs: true});
  // Test-read Markdown under docs/ is not documentation either: it must
  // route to a substantive quick run, never the docs-only path (MOR-2589).
  assert.deepEqual(routePolicy.classifyPullFiles([{filename: 'docs/PROJECT.md'}], 1), {core: true, frontend: false, ci: false, docs: false});
  assert.deepEqual(routePolicy.classifyPullFiles([{filename: 'docs/guide.md'}, {filename: 'docs/api/web.md'}], 2), {core: true, frontend: false, ci: false, docs: false});
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

test('quick v2 topology is light-runner, metadata-only, and separately published', () => {
  assert.deepEqual(topologyErrors(sources()), []);
  assert.equal(observer.LEGACY_WORKFLOW.id, 270532868);
  assert.equal(observer.LEGACY_WORKFLOW.path, '.github/workflows/quick.yml');
  assert.equal(observer.OBSERVATION_CONTEXT, 'quick-v2-observe');
});
test('workflow-run observation admits feature heads targeting main', () => assert.doesNotMatch(sources().quick, /\n  workflow_run:\n(?: {4}.*\n)* {4}branches:/u));

test('topology contract catches permission, execution, event, and pin regressions', () => {
  const original = sources();
  const mutations = [
    ['runs-on: [self-hosted, linux, light]', 'runs-on: [self-hosted, linux, build]'],
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
  const build = ['self-hosted', 'linux', 'build'];
  const light = ['self-hosted', 'linux', 'light'];
  const valid = [
    {name: 'classify', status: 'completed', conclusion: 'success', run_attempt: 2, runner_name: 'GitHub Actions 1', labels: ['ubuntu-latest']},
    {name: 'quick-frontend', status: 'completed', conclusion: 'skipped', run_attempt: 2, runner_name: null, labels: build},
    {name: 'quick-python', status: 'completed', conclusion: 'success', run_attempt: 2, runner_name: 'mm-build-core-1', labels: build},
    {name: 'quick', status: 'completed', conclusion: 'success', run_attempt: 2, runner_name: 'mm-light-core-1', labels: light},
  ];
  const ok = {ok: true, code: 'terminal_match', routeConcordance: true};
  assert.deepEqual(observer.evaluateJobTopology(run, valid, routes, false), ok);
  const both = structuredClone(valid); both[1].conclusion = 'success'; both[1].runner_name = 'mm-build-core-2';
  assert.deepEqual(observer.evaluateJobTopology(run, both, {...routes, frontend: true}, false), ok);
  const lightClassify = structuredClone(valid); lightClassify[0].labels = light; lightClassify[0].runner_name = 'mm-light-core-2';
  assert.deepEqual(observer.evaluateJobTopology(run, lightClassify, routes, false), ok);
  const skipped = structuredClone(valid); skipped[3].conclusion = 'skipped'; skipped[3].runner_name = null;
  assert.equal(observer.evaluateJobTopology(run, skipped, routes, false).code, 'skipped_substantive');
  // An unknown root selects base core but no workflow half; the legacy run is
  // still a match, as it was before the split.
  const noWork = structuredClone(valid); noWork[2].conclusion = 'skipped'; noWork[2].runner_name = null;
  assert.deepEqual(observer.evaluateJobTopology(run, noWork, routes, false), ok);
  assert.equal(observer.evaluateJobTopology(run, noWork, {...routes, ci: true}, false).code, 'skipped_substantive');
  assert.equal(observer.evaluateJobTopology(run, valid, {...routes, frontend: true}, false).code, 'skipped_substantive');
  const hostedWorker = structuredClone(valid); hostedWorker[2].labels = ['ubuntu-latest'];
  assert.equal(observer.evaluateJobTopology(run, hostedWorker, routes, false).code, 'route_mismatch');
  const buildAggregator = structuredClone(valid); buildAggregator[3].labels = build;
  assert.equal(observer.evaluateJobTopology(run, buildAggregator, routes, false).code, 'route_mismatch');
  assert.equal(observer.evaluateJobTopology(run, valid.filter((job) => job.name !== 'quick-frontend'), routes, false).code, 'route_mismatch');
  assert.equal(observer.evaluateJobTopology(run, [...valid, {...valid[2], name: 'quick-shadow'}], routes, false).code, 'route_mismatch');
  assert.equal(observer.evaluateJobTopology({...run, conclusion: 'cancelled'}, valid, routes, false).code, 'terminal_failure');
  const failedWorker = structuredClone(valid); failedWorker[2].conclusion = 'failure';
  assert.equal(observer.evaluateJobTopology({...run, conclusion: 'failure'}, failedWorker, routes, false).code, 'terminal_failure');
  assert.equal(observer.evaluateJobTopology(run, valid, {...routes, docs: true}, false).code, 'route_mismatch');
});

test('legacy attempt jobs consume the normalized pagination shape', async () => {
  const classify = {name: 'classify', status: 'completed', conclusion: 'success', run_attempt: 1, runner_name: 'GitHub Actions 1', labels: ['ubuntu-latest']};
  const quick = {name: 'quick', status: 'completed', conclusion: 'success', run_attempt: 1, runner_name: 'mm-build-core-1', labels: ['self-hosted', 'linux', 'build']};
  // Pinned actions/github-script paginate.iterator normalizes the list-jobs
  // response: the page data is the jobs array itself, not an object with a
  // `jobs` key (MOR-2434).
  const page = (jobs, total_count) => ({data: Object.assign([...jobs], {total_count})});
  const github = {paginate: {iterator: async function* () {
    yield page([classify], 2);
    yield page([quick], 2);
  }}};
  const jobs = await observer.listAttemptJobs(github, observer.REPOSITORY.owner, observer.REPOSITORY.repo, 9000, 1);
  assert.deepEqual(jobs, [classify, quick]);
  const incomplete = {paginate: {iterator: async function* () { yield page([classify], 2); }}};
  await assert.rejects(observer.listAttemptJobs(incomplete, observer.REPOSITORY.owner, observer.REPOSITORY.repo, 9000, 1), /topology is incomplete/u);
  const wrapped = {paginate: {iterator: async function* () { yield {data: {total_count: 2, jobs: [classify, quick]}}; }}};
  await assert.rejects(observer.listAttemptJobs(wrapped, observer.REPOSITORY.owner, observer.REPOSITORY.repo, 9000, 1), /job metadata is invalid/u);
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

test('final publication rebind turns head movement into failure and base movement into a pending publication', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40);
  const result = {kind: 'lifecycle', code: 'admitted', state: 'pending', publish: true, targetSha: head, pullNumber: 41, baseSha: base, headSha: head, runId: null, runAttempt: null, jobTopology: null, metrics: {false_success_publications: 0}};
  const githubFor = (pull, liveMain) => ({request: async (route) => {
    if (route.includes('/branches/')) return {data: {commit: {sha: liveMain}}};
    if (route.includes('/pulls/')) return {data: pull};
    throw new Error(`unexpected route ${route}`);
  }});
  // The earlier published observation rebinds cleanly while the base is current.
  const sameBase = await observer.rebindForPublication({github: githubFor(canonicalPull(41, head, base), base), result});
  assert.deepEqual([sameBase.code, sameBase.state, sameBase.publish, sameBase.targetSha], ['admitted', 'pending', true, head]);
  const movedHead = await observer.rebindForPublication({github: githubFor(canonicalPull(41, 'd'.repeat(40), base), base), result});
  assert.deepEqual([movedHead.code, movedHead.state, movedHead.targetSha], ['stale_head', 'failure', head]);
  // After the base moves, the same head republishes as pending (non-success)
  // instead of leaving the earlier observation in place, while the job stays green.
  const movedBase = await observer.rebindForPublication({github: githubFor(canonicalPull(41, head, 'c'.repeat(40)), 'c'.repeat(40)), result});
  assert.deepEqual([movedBase.code, movedBase.state, movedBase.publish, movedBase.targetSha], ['stale_base', 'pending', true, head]);
});

test('stale base republishes the lifecycle observation as pending for the bound head', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40); const live = 'c'.repeat(40);
  const pull = canonicalPull(41, head, base);
  const github = {request: async (route) => {
    if (route.includes('/pulls/')) return {data: pull};
    throw new Error(`unexpected route ${route}`);
  }};
  const context = {eventName: 'pull_request_target', repo: {owner: observer.REPOSITORY.owner, repo: observer.REPOSITORY.repo}, payload: {action: 'synchronize', repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, pull_request: {number: 41}}};
  // The same head and base bind cleanly while the base is current, so an
  // earlier observation could have published success for this head.
  assert.deepEqual(observer.bindPull(pull, base, head), {headSha: head, baseSha: base, sameRepository: true});
  // After the base moves, the bound head republishes as pending (non-success)
  // instead of leaving the earlier success in place, while the job stays green.
  const result = await observer.assess({github, context, liveBaseSha: live});
  assert.deepEqual([result.kind, result.code, result.state, result.publish, result.targetSha], ['lifecycle', 'stale_base', 'pending', true, head]);
});

test('stale base republishes the workflow-run observation as pending for the bound head', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40); const live = 'c'.repeat(40);
  const run = {...runPull(41, head, base), id: 9001, event: 'pull_request', status: 'completed', conclusion: 'success', workflow_id: observer.LEGACY_WORKFLOW.id, path: observer.LEGACY_WORKFLOW.path, name: observer.LEGACY_WORKFLOW.name, repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, head_repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, check_suite_id: 7};
  const github = {request: async (route) => {
    if (route.includes('/actions/runs/')) return {data: run};
    throw new Error(`unexpected route ${route}`);
  }};
  const context = {eventName: 'workflow_run', repo: {owner: observer.REPOSITORY.owner, repo: observer.REPOSITORY.repo}, payload: {workflow_run: {event: 'pull_request', id: 9001, run_attempt: 1}}};
  // The same run binds cleanly while the base is current, so an earlier
  // observation could have published success for this head.
  assert.deepEqual(observer.bindRunPullRequest(run, base), {pullNumber: 41, headSha: head, baseSha: base});
  // After the base moves, the workflow-run leg skips: it publishes nothing for
  // a head it did not evaluate, and both jobs stay green (MOR-3875).
  const result = await observer.assess({github, context, liveBaseSha: live});
  assert.deepEqual([result.kind, result.code, result.state, result.publish, result.targetSha], ['workflow_run', 'stale_base', 'success', false, head]);
});

test('stale workflow-run base skips publication end to end without posting a status', async () => {
  const base = 'b'.repeat(40); const head = 'a'.repeat(40); const live = 'c'.repeat(40);
  const run = {...runPull(41, head, base), id: 9001, event: 'pull_request', status: 'completed', conclusion: 'success', workflow_id: observer.LEGACY_WORKFLOW.id, path: observer.LEGACY_WORKFLOW.path, name: observer.LEGACY_WORKFLOW.name, repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, head_repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, check_suite_id: 7};
  const posted = [];
  const github = {request: async (route) => {
    if (route.startsWith('POST ')) { posted.push(route); return {data: {}}; }
    if (route.includes('/actions/runs/')) return {data: run};
    throw new Error(`unexpected route ${route}`);
  }};
  const context = {eventName: 'workflow_run', repo: {owner: observer.REPOSITORY.owner, repo: observer.REPOSITORY.repo}, payload: {workflow_run: {event: 'pull_request', id: 9001, run_attempt: 1}}};
  // Same sequence as the quick-v2.yml publish job: assess, rebind, then post
  // only when the assessment says to publish.
  let result = await observer.assess({github, context, liveBaseSha: live});
  assert.deepEqual([result.kind, result.code, result.state, result.publish], ['workflow_run', 'stale_base', 'success', false]);
  result = await observer.rebindForPublication({github, result});
  assert.deepEqual([result.code, result.state, result.publish], ['stale_base', 'success', false]);
  if (result.publish) {
    await github.request('POST /repos/{owner}/{repo}/statuses/{sha}', {});
  }
  assert.deepEqual(posted, []);
});

test('stale live pull base on the workflow-run path also skips without posting', async () => {
  const head = 'a'.repeat(40); const live = 'c'.repeat(40); const moved = 'd'.repeat(40);
  const run = {...runPull(41, head, live), id: 9001, event: 'pull_request', status: 'completed', conclusion: 'success', workflow_id: observer.LEGACY_WORKFLOW.id, path: observer.LEGACY_WORKFLOW.path, name: observer.LEGACY_WORKFLOW.name, repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, head_repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, check_suite_id: 7};
  const posted = [];
  const github = {
    paginate: async (route) => {
      if (route.includes('/commits/')) return [canonicalPull(41, head, live)];
      throw new Error(`unexpected page ${route}`);
    },
    request: async (route) => {
      if (route.startsWith('POST ')) { posted.push(route); return {data: {}}; }
      if (route.includes('/actions/runs/')) return {data: run};
      if (route.includes('/actions/workflows/')) return {data: {id: observer.LEGACY_WORKFLOW.id, path: observer.LEGACY_WORKFLOW.path, name: observer.LEGACY_WORKFLOW.name, state: 'active'}};
      if (route.includes('/check-suites/')) return {data: {head_sha: head, app: {id: 15368}}};
      if (route.includes('/pulls/')) return {data: canonicalPull(41, head, moved)};
      throw new Error(`unexpected route ${route}`);
    },
  };
  const context = {eventName: 'workflow_run', repo: {owner: observer.REPOSITORY.owner, repo: observer.REPOSITORY.repo}, payload: {workflow_run: {event: 'pull_request', id: 9001, run_attempt: 1}}};
  // The run recorded the live base, but the live pull has since moved to
  // another base: the head was not evaluated against anything current.
  let result = await observer.assess({github, context, liveBaseSha: live});
  assert.deepEqual([result.kind, result.code, result.state, result.publish, result.targetSha], ['workflow_run', 'stale_base', 'success', false, head]);
  result = await observer.rebindForPublication({github, result});
  assert.deepEqual([result.code, result.state, result.publish], ['stale_base', 'success', false]);
  if (result.publish) {
    await github.request('POST /repos/{owner}/{repo}/statuses/{sha}', {});
  }
  assert.deepEqual(posted, []);
});

test('quick v2 reports no hard-coded self-hosted minutes', async () => {
  const policySource = fs.readFileSync(path.join(ROOT, '.github/scripts/quick-v2-metadata-policy-v1.js'), 'utf8');
  assert.doesNotMatch(policySource, /v2_self_hosted_minutes/u);
  const base = 'b'.repeat(40); const head = 'a'.repeat(40); const live = 'c'.repeat(40);
  const pull = canonicalPull(41, head, base);
  const github = {request: async (route) => {
    if (route.includes('/pulls/')) return {data: pull};
    throw new Error(`unexpected route ${route}`);
  }};
  const context = {eventName: 'pull_request_target', repo: {owner: observer.REPOSITORY.owner, repo: observer.REPOSITORY.repo}, payload: {action: 'synchronize', repository: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName}, pull_request: {number: 41}}};
  const result = await observer.assess({github, context, liveBaseSha: live});
  assert.equal('v2_self_hosted_minutes' in (result.metrics ?? {}), false);
});

function workflowJobs(root) {
  const dir = path.join(root, '.github/workflows');
  const jobs = [];
  for (const file of fs.readdirSync(dir).filter((name) => name.endsWith('.yml')).sort()) {
    const text = fs.readFileSync(path.join(dir, file), 'utf8');
    const lines = text.split('\n');
    let inJobs = false; let current = null; let currentRunsOn = null;
    const flush = () => {
      if (inJobs && current !== null && currentRunsOn !== null) jobs.push({file, job: current, runsOn: currentRunsOn, text: jobText});
      current = null; currentRunsOn = null; jobText = '';
    };
    let jobText = '';
    for (const line of lines) {
      if (/^jobs:\s*$/.test(line)) { inJobs = true; continue; }
      if (!inJobs) continue;
      const jobMatch = /^  ([A-Za-z0-9_-]+):\s*$/.exec(line);
      if (jobMatch) { flush(); current = jobMatch[1]; continue; }
      if (current !== null) {
        jobText += `${line}\n`;
        const runsMatch = /^\s+runs-on:\s*(.*)\s*$/.exec(line);
        if (runsMatch) currentRunsOn = runsMatch[1];
      }
    }
    flush();
  }
  return jobs;
}

test('every workflow job runs on self-hosted runners', () => {
  const jobs = workflowJobs(TARGET_ROOT);
  assert.ok(jobs.length > 0);
  const hosted = jobs.filter(({runsOn}) => /ubuntu-|macos-|windows-/iu.test(runsOn) && !/self-hosted/iu.test(runsOn));
  assert.deepEqual(hosted.map(({file, job, runsOn}) => `${file}:${job}:${runsOn}`), []);
});

test('migrated light jobs carry their explicit tool setup', () => {
  const jobs = workflowJobs(TARGET_ROOT);
  const byName = new Map(jobs.map(({file, job, runsOn, text}) => [`.github/workflows/${file}:${job}`, {runsOn, text}]));
  const problems = [];
  const requireIn = (key, needle, why) => {
    const entry = byName.get(key);
    if (!entry) { problems.push(`${key} is missing`); return; }
    if (!entry.text.includes(needle)) problems.push(`${key} lacks ${why}: ${needle}`);
  };
  for (const [key, {runsOn, text}] of byName) {
    if (!/light/.test(runsOn)) continue;
    if (/(^|\s)python3(\s|$)/.test(text) && !text.includes('actions/setup-python@')) problems.push(`${key} runs python3 without actions/setup-python`);
    if (/\buv\s+(run|sync)\b/.test(text) && !text.includes('astral-sh/setup-uv@') && !text.includes('uv python install')) problems.push(`${key} runs uv without astral-sh/setup-uv`);
    const runLines = text.split('\n').filter((line) => /^\s+run:/.test(line));
    if (runLines.some((line) => /(\s|^)(node\s|npm\s|npx\s)/.test(line)) && !text.includes('actions/setup-node@')) problems.push(`${key} runs node/npm without actions/setup-node`);
    if (/(^|\s)gh(\s|$)/.test(text) && !text.includes('command -v gh')) problems.push(`${key} uses gh without a command -v gh guard`);
  }
  requireIn('.github/workflows/quick.yml:classify', 'actions/setup-python@v5', 'explicit Python setup');
  requireIn('.github/workflows/freeze-gate.yml:freeze-gate', 'actions/setup-node@v6', 'explicit Node setup');
  requireIn('.github/workflows/artifact-cleanup.yml:cleanup', 'actions/setup-python@v5', 'explicit Python setup');
  requireIn('.github/workflows/certification-status.yml:status', 'actions/setup-python@v5', 'explicit Python setup');
  requireIn('.github/workflows/docs.yml:indexnow', 'actions/setup-python@v5', 'explicit Python setup');
  requireIn('.github/workflows/dispatch-downstream.yml:bump-downstream', 'actions/setup-python@v5', 'explicit Python setup');
  requireIn('.github/workflows/dispatch-downstream.yml:bump-downstream', 'command -v gh', 'GitHub CLI guard');
  assert.deepEqual(problems, []);
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
