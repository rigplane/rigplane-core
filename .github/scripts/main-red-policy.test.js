'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const policy = require('./main-red-policy.js');
const fixture = name => fs.readFileSync(path.join(__dirname, 'fixtures/main-red', name), 'utf8');
const history = () => JSON.parse(fixture('history.json'));
const repo = {owner: 'example', repo: 'repo'};
const episodeMarker = '<!-- main-red:episode:example/repo/7:green-11 -->';

function harness({logsFail = false, labelExists = true, failClose = false, dryRun = false, replay = false, issueReadFail = false, noFailedJobs = false, associatedPulls} = {}) {
  let current = {...history()[0], path: '.github/workflows/full.yml', workflow_id: 7, run_attempt: 1,
    html_url: 'https://github.com/example/repo/actions/runs/13', head_repository: {full_name: 'example/repo'}};
  let runs = history();
  const mutations = [];
  const reads = [];
  const stored = [];
  const messages = [];
  let closeFails = failClose;
  const issues = {
    listForRepo: async params => {
      if (issueReadFail) throw Object.assign(new Error('issue lookup unavailable'), {status: 503});
      reads.push({method: 'listForRepo', ...params});
      assert.equal(params.labels, 'main-red');
      assert.equal(params.state, 'all');
      return {data: stored.filter(issue => issue.labels.some(label => label.name === params.labels))};
    },
    listComments: async params => {
      reads.push({method: 'listComments', ...params});
      return {data: stored.find(issue => issue.number === params.issue_number).comments};
    },
    getLabel: async () => {
      if (!labelExists) throw Object.assign(new Error('missing label'), {status: 404});
      return {data: {name: 'main-red'}};
    },
    createLabel: async params => {
      mutations.push({method: 'createLabel', ...params});
      labelExists = true;
      return {data: {name: params.name}};
    },
    create: async params => {
      mutations.push({method: 'create', ...params});
      const issue = {number: stored.length + 1, state: 'open', body: params.body,
        labels: params.labels.map(name => ({name})), comments: []};
      stored.push(issue);
      return {data: issue};
    },
    createComment: async params => {
      mutations.push({method: 'createComment', ...params});
      const comment = {body: params.body};
      stored.find(issue => issue.number === params.issue_number).comments.push(comment);
      return {data: comment};
    },
    update: async params => {
      if (closeFails) {closeFails = false; throw new Error('close failed');}
      mutations.push({method: 'update', ...params});
      const issue = stored.find(item => item.number === params.issue_number);
      issue.state = params.state;
      return {data: issue};
    },
  };
  const github = {rest: {issues, actions: {
    getWorkflowRun: async params => {assert.equal(params.run_id, current.id); return {data: current};},
    listJobsForWorkflowRunAttempt: 'jobs', listWorkflowRuns: 'runs',
    downloadJobLogsForWorkflowRun: async () => {
      if (logsFail) throw Object.assign(new Error('logs expired'), {status: 410});
      return {data: new TextEncoder().encode(fixture('failure.txt')).buffer};
    },
  }, repos: {listPullRequestsAssociatedWithCommit: 'pulls'}, pulls: {
    list: 'recentPulls',
    get: async params => ({data: {number: params.pull_number, merged_by: {login: `merger-${params.pull_number}`}}}),
  }}};
  github.paginate = async (method, params) => {
    if (method === 'pulls') return associatedPulls ?? [{number: 42, merged_at: '2026-11-26', merge_commit_sha: params.commit_sha,
      base: {ref: 'main'}, user: {login: 'builder'}}];
    if (method === 'recentPulls') return [
      {number: 90, merged_at: '2026-11-26T05:00:00Z'},
      {number: 39, merged_at: '2026-11-26T01:00:00Z'},
      {number: 40, merged_at: '2026-11-26T02:30:00Z'},
    ];
    if (method === 'jobs') return noFailedJobs ? [] : [{id: 99, name: 'test (3.12)', html_url: 'https://github.com/example/repo/actions/runs/13/job/99',
      conclusion: 'failure', steps: [{name: 'Run tests', conclusion: 'failure'}]}];
    return (await method(params)).data;
  };
  github.paginate.iterator = async function* (_method, params) {
    assert.equal(params.workflow_id, current.workflow_id);
    yield {data: {workflow_runs: runs}};
  };
  return {
    mutations, stored, reads, messages,
    setCurrent: changes => {current = {...current, ...changes};},
    setHistory: values => {runs = values;},
    run: () => policy.run({github, context: {repo, payload: replay ? {} : {workflow_run: current}},
      core: {info: message => messages.push(message)},
      env: {MAIN_RED_RUN_ID: String(current.id), MAIN_RED_DRY_RUN: String(dryRun)}}),
  };
}

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

test('green identifies the preceding episode; an earlier green alone has no incident', () => {
  const green = {...history()[0], id: 14, run_number: 14, conclusion: 'success'};
  assert.equal(policy.findEpisode(green, [...history(), green]).firstRed.id, 12);
  assert.equal(policy.findEpisode(history()[2], history()), null);
});

test('cancelled and action-required history is neutral between green and red and within a red episode', () => {
  const runs = JSON.parse(fixture('neutral-history.json'));
  const episode = policy.findEpisode(runs[0], runs);
  assert.equal(episode.firstRed.id, 23);
  assert.equal(episode.green.id, 11);
  assert.equal(policy.findEpisode(history()[3], history()), null);
  const neutralHistory = runs.filter(run => run.conclusion !== 'failure');
  assert.equal(policy.findEpisode({...runs[0], conclusion: 'success'}, neutralHistory), null);
});

test('neutral fixture rows do not change issue attribution or its last-green episode key', async () => {
  const runs = JSON.parse(fixture('neutral-history.json'));
  const h = harness();
  h.setCurrent(runs[0]);
  h.setHistory(runs);
  await h.run();
  assert.ok(h.stored[0].body.includes(episodeMarker));
  assert.match(h.stored[0].body, /First red SHA: c{40}/u);
});

test('neutral-only completions neither open nor close an incident, including replay', async () => {
  for (const conclusion of ['cancelled', 'action_required']) {
    for (const replay of [false, true]) {
      const h = harness({replay});
      h.setCurrent({conclusion});
      assert.equal(policy.findEpisode({...history()[0], conclusion}, history()), null);
      await h.run();
      assert.equal(h.mutations.length, 0);
      assert.equal(h.reads.length, 0);
    }
    const h = harness();
    await h.run();
    h.setCurrent({id: 14, run_number: 14, conclusion});
    await h.run();
    assert.deepEqual(h.mutations.map(item => item.method), ['create']);
    assert.equal(h.stored[0].state, 'open');
  }
});

test('missing green is explicit instead of inventing an introducing baseline', () => {
  assert.equal(policy.findEpisode(history()[1], [history()[1]]).green, null);
});

test('introducing merge must match first red SHA and main, not a later associated PR', () => {
  const sha = history()[1].head_sha;
  const pr = {number: 42, merged_at: '2026-11-26', merge_commit_sha: sha, base: {ref: 'main'}, user: {login: 'builder'}};
  assert.deepEqual(policy.introducingMerge(sha, [{...pr, merge_commit_sha: 'f'.repeat(40)}, pr]), pr);
  assert.equal(policy.introducingMerge(sha, [{...pr, base: {ref: 'release/1.0.x'}}]), null);
  assert.equal(policy.introducingMerge(sha, []), null);
  assert.equal(policy.introducingMerge(sha, [pr, {...pr, number: 43}]), null);
  assert.equal(policy.introducingMerge(sha, [null, {}]), null);
});

for (const event of ['schedule', 'workflow_dispatch']) {
  test(`${event} red completion attributes the first red push, skipping an earlier non-push failure`, async () => {
    const h = harness();
    h.setHistory([...history().map(run => ({...run, run_number: run.run_number * 10})),
      {...history()[1], id: 111, run_number: 115, event,
      head_sha: 'd'.repeat(40)}]);
    h.setCurrent({event, run_number: 130, head_sha: 'e'.repeat(40)});
    await h.run();
    assert.match(h.stored[0].body, /First red SHA: b{40}/u);
    assert.match(h.stored[0].body, /First red merge: #42 \(b{40}\)/u);
    assert.doesNotMatch(h.stored[0].body, /First red SHA: [de]{40}/u);
  });
}

test('a scheduled-only failure reports unknown attribution instead of inventing a push merge', async () => {
  const h = harness();
  h.setCurrent({event: 'schedule'});
  h.setHistory([history()[2]]);
  await h.run();
  assert.match(h.stored[0].body, /introducing merge unknown/u);
  assert.match(h.stored[0].body, /No red push run in available workflow history/u);
});

for (const scenario of ['direct push', 'batch', 'revert']) {
  test(`${scenario} attribution still creates and updates an issue with its SHA range and last merger`, async () => {
    const pr = {number: 41, merged_at: '2026-11-26T02:45:00Z',
      merge_commit_sha: 'f'.repeat(40), base: {ref: 'main'}, user: {login: 'builder'}};
    const associatedPulls = scenario === 'direct push' ? [] : scenario === 'batch'
      ? [pr, {...pr, number: 42, merged_at: '2026-11-26T02:50:00Z'}] : [pr];
    const h = harness({associatedPulls});
    await h.run();
    h.setCurrent({id: 14, run_number: 14});
    await h.run();
    assert.deepEqual(h.mutations.map(item => item.method), ['create', 'createComment']);
    for (const report of [h.stored[0].body, h.stored[0].comments[0].body]) {
      assert.match(report, /SHA range: a{40}\.\.\.b{40}/u);
      assert.match(report, scenario === 'batch' ? /batch: PRs #41, #42/u : /introducing merge unknown/u);
      assert.match(report, scenario === 'batch' ? /Owner: @merger-42/u : /Owner: @merger-40/u);
    }
  });
}

test('creates one labelled GitHub issue with first-red attribution, failure evidence and four-hour target', async () => {
  const h = harness();
  await h.run();
  await h.run();
  assert.equal(h.mutations.length, 1);
  const issue = h.mutations[0];
  assert.equal(issue.method, 'create');
  assert.deepEqual(issue.labels, ['main-red']);
  assert.equal(issue.owner, repo.owner);
  assert.equal(issue.repo, repo.repo);
  assert.ok(issue.body.includes(episodeMarker));
  assert.match(issue.body, /#42 \(bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\)/u);
  assert.match(issue.body, /GitHub (?:lane\/)?author: @?builder/u);
  assert.match(issue.body, /Owner: @merger-42/u);
  assert.match(issue.body, /tests\/test_contract.py::test_main/u);
  assert.match(issue.body, /AssertionError: expected ready/u);
  assert.match(issue.body, /https:\/\/github.com\/example\/repo\/actions\/runs\/13/u);
  assert.match(issue.body, /job\/99/u);
  assert.match(issue.body, /green in under 4 h; deadline 2026-11-26T07:00:00.000Z/u);
});

test('still red comments once on the open episode; next green comments and closes it once', async () => {
  const h = harness();
  await h.run();
  h.setCurrent({id: 14, run_number: 14, html_url: 'https://github.com/example/repo/actions/runs/14'});
  await h.run();
  await h.run();
  assert.deepEqual(h.mutations.map(item => item.method), ['create', 'createComment']);
  assert.match(h.stored[0].comments[0].body, /run-14-attempt-1/u);
  h.setHistory([...history(), {...history()[0], id: 14, run_number: 14}]);
  h.setCurrent({id: 15, run_number: 15, conclusion: 'success', html_url: 'https://github.com/example/repo/actions/runs/15'});
  await h.run();
  await h.run();
  assert.deepEqual(h.mutations.map(item => item.method), ['create', 'createComment', 'createComment', 'update']);
  assert.match(h.stored[0].comments[1].body, /Green/u);
  assert.match(h.stored[0].comments[1].body, /actions\/runs\/15/u);
  assert.equal(h.stored[0].state, 'closed');
  assert.equal(h.mutations.at(-1).state_reason, 'completed');
});

test('a green rerun of the first red run finds the episode and closes it', async () => {
  const h = harness();
  h.setCurrent({...history()[1], html_url: 'https://github.com/example/repo/actions/runs/12'});
  h.setHistory(history().slice(1));
  await h.run();
  h.setCurrent({conclusion: 'success', run_attempt: 2});
  await h.run();
  await h.run();
  assert.equal(h.stored.length, 1);
  assert.equal(h.stored[0].state, 'closed');
  assert.match(h.stored[0].comments[0].body, /run-12-attempt-2/u);
});

test('recovery replay retries closure after a comment succeeded but closing failed', async () => {
  const h = harness({failClose: true});
  await h.run();
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await assert.rejects(h.run(), /close failed/u);
  await h.run();
  assert.equal(h.stored[0].comments.length, 1);
  assert.equal(h.stored[0].state, 'closed');
});

test('closed episode markers dedupe old red replays without reopening or commenting', async () => {
  const h = harness();
  await h.run();
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await h.run();
  const count = h.mutations.length;
  h.setCurrent({id: 13, run_number: 13, conclusion: 'failure', run_attempt: 2});
  await h.run();
  assert.equal(h.mutations.length, count);
  assert.equal(h.stored[0].state, 'closed');
});

test('a new red after a green gets a distinct episode issue', async () => {
  const h = harness();
  await h.run();
  const green = {...history()[0], id: 14, run_number: 14, conclusion: 'success'};
  h.setCurrent(green);
  await h.run();
  h.setHistory([green, ...history()]);
  h.setCurrent({id: 15, run_number: 15, conclusion: 'failure', head_sha: 'e'.repeat(40)});
  await h.run();
  assert.equal(h.stored.length, 2);
  assert.equal(h.stored[0].state, 'closed');
  assert.equal(h.stored[1].state, 'open');
  assert.match(h.stored[1].body, /episode:example\/repo\/7:green-14/u);
  assert.match(h.stored[1].body, /#42 \(eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\)/u);
});

test('an episode lookup ignores a pull request carrying the same label and body marker', async () => {
  const h = harness();
  h.stored.push({number: 100, pull_request: {}, state: 'open', body: episodeMarker,
    labels: [{name: 'main-red'}], comments: []});
  await h.run();
  assert.equal(h.mutations[0].method, 'create');
});

test('dry-run dispatch replay plans an issue without any mutation or custom credentials', async () => {
  const h = harness({dryRun: true, replay: true, labelExists: false});
  await h.run();
  assert.equal(h.mutations.length, 0);
  assert.ok(h.messages.some(message => message.includes('create')));
});

test('dry-run recovery leaves an existing issue and its comments untouched', async () => {
  const h = harness({dryRun: true});
  h.stored.push({number: 1, body: episodeMarker, state: 'open', labels: [{name: 'main-red'}], comments: []});
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await h.run();
  assert.equal(h.mutations.length, 0);
  assert.equal(h.stored[0].state, 'open');
  assert.equal(h.stored[0].comments.length, 0);
});

test('creates the main-red label when missing before creating the issue', async () => {
  const h = harness({labelExists: false});
  await h.run();
  assert.deepEqual(h.mutations.map(item => item.method), ['createLabel', 'create']);
  assert.equal(h.mutations[0].name, 'main-red');
});

test('green without a matching incident performs no mutations', async () => {
  const h = harness();
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await h.run();
  assert.equal(h.mutations.length, 0);
});

test('a delayed red event after a newer green cannot open a stale incident', async () => {
  const h = harness();
  h.setHistory([{...history()[0], id: 14, run_number: 14, conclusion: 'success'}, ...history()]);
  await h.run();
  assert.equal(h.mutations.length, 0);
});

test('a delayed red event reconciles an existing incident with the newer green', async () => {
  const h = harness();
  await h.run();
  h.setHistory([{...history()[0], id: 14, run_number: 14, conclusion: 'success',
    html_url: 'https://github.com/example/repo/actions/runs/14'}, ...history()]);
  await h.run();
  assert.equal(h.stored[0].state, 'closed');
  assert.match(h.stored[0].comments[0].body, /run-14-attempt-1/u);
});

test('a green completion records its own recovery even when a later green is already in history', async () => {
  const h = harness();
  await h.run();
  h.setHistory([{...history()[0], id: 16, run_number: 16, conclusion: 'success'}, ...history()]);
  h.setCurrent({id: 14, run_number: 14, conclusion: 'success'});
  await h.run();
  assert.match(h.stored[0].comments[0].body, /run-14-attempt-1/u);
});

test('missing log access keeps the failed job and an explicit unknown cause', async () => {
  const h = harness({logsFail: true});
  await h.run();
  assert.match(h.stored[0].body, /Logs unavailable \(HTTP 410\)/u);
  assert.match(h.stored[0].body, /job\/99/u);
});

test('a red run without failed-job evidence states that the test cause is unknown', async () => {
  const h = harness({noFailedJobs: true});
  await h.run();
  assert.match(h.stored[0].body, /Failing tests: no failed-job evidence available; inspect the run/u);
});

test('issue lookup errors propagate instead of creating an un-deduplicated incident', async () => {
  const h = harness({issueReadFail: true});
  await assert.rejects(h.run(), /issue lookup unavailable/u);
  assert.equal(h.mutations.length, 0);
});

test('reporter ignores PR runs, foreign repositories, unfinished runs and unobserved workflows', async () => {
  for (const changes of [{event: 'pull_request'}, {head_branch: 'feature'},
    {head_repository: {full_name: 'fork/repo'}}, {status: 'in_progress'},
    {path: '.github/workflows/onboarding-workflow-tests.yml'}]) {
    const h = harness();
    h.setCurrent(changes);
    await h.run();
    assert.equal(h.mutations.length, 0);
    assert.equal(h.reads.length, 0);
  }
});

test('workflow keeps main completion/replay triggers, read-only defaults and job-scoped issues permission', () => {
  const workflow = fs.readFileSync(path.join(__dirname, '../workflows/main-red-policy.yml'), 'utf8');
  assert.match(workflow, /workflows: \['Tests \(full matrix\)', 'Tests \(quick\)'\]/u);
  assert.match(workflow, /types: \[completed\]/u);
  assert.match(workflow, /branches: \[main\]/u);
  assert.match(workflow, /workflow_dispatch:/u);
  assert.match(workflow, /default: true/u);
  assert.match(workflow, /runs-on: \[self-hosted, Linux, ARM64, light\]/u);
  assert.match(workflow, /cancel-in-progress: false/u);
  assert.match(workflow, /group: main-red-policy-\$\{\{ github.repository \}\}/u);
  const defaults = workflow.split('\npermissions:\n')[1].split('\nconcurrency:')[0];
  assert.doesNotMatch(defaults, /write/u);
  assert.match(workflow, /\n  report:\n    permissions:\n(?:      [^\n]+\n)*      issues: write\n/u);
  assert.equal((workflow.match(/: write/gu) || []).length, 1);
  assert.match(workflow, /ref: main/u);
  assert.match(workflow, /persist-credentials: false/u);
  assert.doesNotMatch(workflow, /LINEAR|OWNER_MAP|secrets\[|secrets\.|vars\./u);
  assert.doesNotMatch(workflow, /ref:.*workflow_run/u);
});
