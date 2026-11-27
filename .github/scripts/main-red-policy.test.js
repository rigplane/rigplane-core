'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const policy = require('./main-red-policy.js');
const fixture = name => fs.readFileSync(path.join(__dirname, 'fixtures/main-red', name), 'utf8');
const history = () => JSON.parse(fixture('history.json'));

test('extracts pytest failures, collection errors and Playwright test ids without duplicates', () => {
  assert.deepEqual(policy.extractFailures(fixture('failure.txt')).ids, [
    'tests/test_contract.py::test_main', 'tests/test_setup.py::test_load',
    '[webkit] › native-specs/connect.spec.mjs:12:3 › restores connection',
    'tests/test_contract.py::test_label[hello world]', 'src/lib/contract.test.ts > restores state',
  ]);
  assert.match(policy.extractFailures(fixture('failure.txt')).assertions.join('\n'), /AssertionError: expected ready/u);
});

test('one episode starts at the first red after a green, independent of API ordering', () => {
  const episode = policy.findEpisode(history()[0], history().reverse());
  assert.equal(episode.firstRed.id, 12);
  assert.equal(episode.green.id, 11);
});

test('green reports the preceding episode without closing it', () => {
  const green = {...history()[0], id: 14, run_number: 14, conclusion: 'success'};
  assert.equal(policy.findEpisode(green, [...history(), green]).firstRed.id, 12);
  assert.equal(policy.findEpisode(history()[2], history()), null);
});

test('cancelled runs do not become a recovery and PR runs do not create incidents', () => {
  const runs = history();
  runs.push({...runs[0], id: 14, run_number: 14, conclusion: 'cancelled'});
  assert.equal(policy.findEpisode(runs[0], runs).firstRed.id, 12);
  assert.equal(policy.findEpisode(runs[3], runs), null);
});

test('missing green is explicit instead of inventing an introducing baseline', () => {
  assert.equal(policy.findEpisode(history()[1], [history()[1]]).green, null);
});

test('introducing merge must match first red SHA and main, not a later associated PR', () => {
  const sha = history()[1].head_sha;
  const pr = {number: 42, merged_at: '2026-11-26', merge_commit_sha: sha, base: {ref: 'main'}, user: {login: 'builder'}};
  assert.deepEqual(policy.introducingMerge(sha, [{...pr, merge_commit_sha: 'f'.repeat(40)}, pr]), pr);
  assert.throws(() => policy.introducingMerge(sha, [{...pr, base: {ref: 'release/1.0.x'}}]), /introducing merge/u);
});

test('a retried event is a no-op; still red and recovery append comments only', async () => {
  const existing = {id: 'issue-1', description: 'incident', comments: []};
  const calls = [];
  const linear = {
    find: async () => existing,
    create: async () => assert.fail('must dedupe'),
    comment: async (id, body) => { calls.push({id, body}); existing.comments.push({body}); },
  };
  const input = {key: 'episode-12', eventKey: 'run-13-attempt-1', title: 'Main red', body: 'Still red'};
  await policy.publish(linear, input);
  await policy.publish(linear, input);
  await policy.publish(linear, {...input, eventKey: 'run-14-attempt-1', body: 'Recovered; ticket remains open'});
  assert.equal(calls.length, 2);
  assert.match(calls[1].body, /remains open/u);
});

test('dry run plans a new ticket without calling any mutation', async () => {
  const linear = {find: async () => null, create: async () => assert.fail('dry run'), comment: async () => assert.fail('dry run')};
  const result = await policy.publish(linear, {key: 'episode-12', eventKey: 'run-12-attempt-1', title: 'Main red', body: 'Failure'}, true);
  assert.equal(result.action, 'create');
});

test('Linear mutation retries use stable IDs and confirm an existing write after an ambiguous response', async () => {
  const calls = [];
  const fetch = async (_url, options) => {
    const request = JSON.parse(options.body);
    calls.push(request);
    if (request.query.includes('issueCreate')) return {ok: true, json: async () => ({errors: [{message: 'already exists'}]})};
    if (request.query.includes('commentCreate')) return {ok: true, json: async () => ({data: {commentCreate: {success: true}}})};
    return {ok: true, json: async () => ({data: {issue: {id: request.variables.id, description: 'earlier event'}}})};
  };
  const client = policy.linearClient(fetch, 'test-token', 'team', 'channel', {builder: 'user'});
  await client.create({key: 'episode', eventKey: 'event', owner: 'builder', title: 'Main red', body: 'failure'});
  assert.equal(calls[0].variables.input.id, policy.identifier('episode'));
  assert.equal(calls[2].variables.input.id, policy.identifier('event'));
});

test('Linear errors propagate without printing response contents', async () => {
  const client = policy.linearClient(async () => ({ok: false, status: 401}), 'test-token', 'team', 'channel', {});
  await assert.rejects(client.find('episode'), /^Error: Linear HTTP 401$/u);
});

test('workflow watches completed main runs on self-hosted runners with event-specific concurrency', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/main-red-policy.yml'), 'utf8');
  assert.match(workflow, /workflow_run:/u);
  assert.match(workflow, /types: \[completed\]/u);
  assert.match(workflow, /branches: \[main\]/u);
  assert.match(workflow, /runs-on: \[self-hosted, Linux, ARM64, light\]/u);
  assert.match(workflow, /cancel-in-progress: false/u);
  assert.match(workflow, /group: main-red-policy-.*workflow_run.run_attempt/u);
  assert.doesNotMatch(workflow, /ref:.*workflow_run/u);
});

function harness({macos = false, logsFail = false} = {}) {
  let current = {...history()[0], path: macos ? '.github/workflows/onboarding-workflow-tests.yml' : '.github/workflows/full.yml', workflow_id: 7, run_attempt: 1, html_url: 'https://github.com/example/repo/actions/runs/13', head_repository: {full_name: 'example/repo'}};
  const mutations = [];
  const stored = [];
  const github = {rest: {actions: {listJobsForWorkflowRunAttempt: 'jobs', listWorkflowRuns: 'runs', downloadJobLogsForWorkflowRun: async () => {
    if (logsFail) throw Object.assign(new Error('logs expired'), {status: 410});
    return {data: new TextEncoder().encode(fixture('failure.txt')).buffer};
  }}, repos: {listPullRequestsAssociatedWithCommit: 'pulls'}}};
  github.paginate = async (method, params) => method === 'pulls' ? [{number: 42, merged_at: '2026-11-26', merge_commit_sha: history()[1].head_sha, base: {ref: 'main'}, user: {login: 'builder'}}] : [{id: 99, name: macos ? 'browser-webkit-macos' : 'test (3.12)', html_url: 'https://github.com/example/repo/actions/runs/13/job/99', conclusion: params.run_id === 11 ? 'success' : 'failure', steps: [{name: 'Run tests', conclusion: 'failure'}]}];
  github.paginate.iterator = async function* () { yield {data: {workflow_runs: history()}}; };
  const fetch = async (_url, options) => {
    const {query, variables} = JSON.parse(options.body);
    let data;
    if (query.includes('issueCreate')) {
      mutations.push(variables.input);
      stored.push({id: variables.input.id, description: variables.input.description, comments: []});
      data = {issueCreate: {success: true, issue: {id: stored[0].id}}};
    } else if (query.includes('commentCreate')) {
      mutations.push(variables.input);
      stored[0].comments.push({body: variables.input.body});
      data = {commentCreate: {success: true}};
    } else if (query.includes('comments(')) {
      data = {issue: {comments: {nodes: stored[0].comments, pageInfo: {hasNextPage: false}}}};
    } else {
      data = {issues: {nodes: stored, pageInfo: {hasNextPage: false}}};
    }
    return {ok: true, json: async () => ({data})};
  };
  return {
    mutations,
    setCurrent: changes => {current = {...current, ...changes};},
    run: () => policy.run({github, context: {repo: {owner: 'example', repo: 'repo'}, payload: {workflow_run: current}}, core: {info: () => {}}, fetch, env: {LINEAR_API_KEY: 'test-token', MAIN_RED_LINEAR_TEAM_ID: 'team', MAIN_RED_OWNER_MAP: '{"builder":"user"}'}}),
  };
}

test('reporter creates an assigned incident with merge, failures, URL and four-hour target; replay is inert', async () => {
  const h = harness();
  await h.run();
  await h.run();
  assert.equal(h.mutations.length, 1);
  assert.equal(h.mutations[0].assigneeId, 'user');
  assert.match(h.mutations[0].description, /#42 \(bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\)/u);
  assert.match(h.mutations[0].description, /tests\/test_contract.py::test_main/u);
  assert.match(h.mutations[0].description, /job\/99/u);
  assert.match(h.mutations[0].description, /deadline 2026-11-26T07:00:00.000Z/u);
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await h.run();
  assert.equal(h.mutations.length, 2);
  assert.match(h.mutations[1].body, /ticket remains open/u);
});

test('macOS WebKit job failure is observed even when the aggregate run is green', async () => {
  const h = harness({macos: true});
  h.setCurrent({conclusion: 'success'});
  await h.run();
  assert.match(h.mutations[0].description, /browser-webkit-macos/u);
});

test('missing log access still files evidence with the failed job and an explicit unknown cause', async () => {
  const h = harness({logsFail: true});
  await h.run();
  assert.match(h.mutations[0].description, /Logs unavailable \(HTTP 410\)/u);
  assert.match(h.mutations[0].description, /job\/99/u);
});
