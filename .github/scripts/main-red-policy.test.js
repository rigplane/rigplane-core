'use strict';
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const policy = require('./main-red-policy.js');
const shared = policy;
const fixture = name => fs.readFileSync(path.join(__dirname, 'fixtures/main-red', name), 'utf8');
const history = () => JSON.parse(fixture('history.json'));
const repo = {owner: 'example', repo: 'repo'};
const episodeMarker = '<!-- main-red:episode:example/repo/7:green-11 -->';

// Model actions/github-script@v8's Octokit normalization before yielding pages:
// total_count list envelopes become arrays; objects with a url (compare) stay objects.
async function* paginatedResponses(responses) {
  for (const response of responses) {
    if (response.data && !Array.isArray(response.data) && 'total_count' in response.data && !('url' in response.data)) {
      const {incomplete_results, repository_selection, total_count} = response.data;
      delete response.data.incomplete_results;
      delete response.data.repository_selection;
      delete response.data.total_count;
      response.data = response.data[Object.keys(response.data)[0]];
      if (incomplete_results !== undefined) response.data.incomplete_results = incomplete_results;
      if (repository_selection !== undefined) response.data.repository_selection = repository_selection;
      response.data.total_count = total_count;
    }
    yield response;
  }
}

function harness({logsFail = false, labelExists = true, failClose = false, dryRun = false, replay = false, issueReadFail = false, noFailedJobs = false, macos = false, corePolicy = true, compareFails = false, ownerFails = false, workflowPageSize = 100} = {}) {
  const implementation = corePolicy ? shared : policy;
  let current = {...history()[0], path: corePolicy ? '.github/workflows/full.yml' : macos ? '.github/workflows/onboarding-workflow-tests.yml' : '.github/workflows/pytest.yml', workflow_id: 7, run_attempt: 1,
    html_url: 'https://github.com/example/repo/actions/runs/13', head_repository: {full_name: 'example/repo'}};
  let runs = history();
  const jobOverrides = new Map();
  const mutations = [];
  const reads = [];
  const stored = [];
  const messages = [];
  const pullOverrides = new Map();
  let commits = null;
  let commitHistory = null;
  const pull = (number, sha) => ({number, merged_at: '2026-11-26T03:00:00Z', merge_commit_sha: sha,
    base: {ref: 'main'}, user: {login: 'builder'}});
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
  }, repos: {
    listPullRequestsAssociatedWithCommit: 'pulls', compareCommitsWithBasehead: 'compare', listCommits: 'commits',
  }, pulls: {get: async params => {
    if (ownerFails) throw Object.assign(new Error('pull detail unavailable'), {status: 503});
    reads.push({method: 'pull', ...params});
    return {data: {...pull(params.pull_number, ''), merged_by: {login: `merger-${params.pull_number}`}}};
  }}}};
  github.paginate = async (method, params) => {
    if (method === 'pulls') {
      reads.push({method, ...params});
      return pullOverrides.get(params.commit_sha) || [pull(42, params.commit_sha)];
    }
    if (method === 'jobs') return jobOverrides.get(params.run_id) || (noFailedJobs ? [] : [{id: 99, name: macos ? 'browser-webkit-macos' : 'test (3.12)', html_url: 'https://github.com/example/repo/actions/runs/13/job/99',
      conclusion: macos && params.run_id === 11 ? 'success' : 'failure', steps: [{name: 'Run tests', conclusion: 'failure'}]}]);
    return (await method(params)).data;
  };
  github.paginate.iterator = async function* (method, params) {
    if (method === 'compare') {
      reads.push({method, ...params});
      if (compareFails) throw Object.assign(new Error('compare unavailable'), {status: 503});
      // Compare responses are objects with a commits array, unlike listCommits.
      yield* paginatedResponses([{data: {url: 'https://api.github.com/repos/example/repo/compare/range', total_commits: 1,
        commits: commits || [{sha: params.basehead.split('...')[1]}]}}]);
      return;
    }
    if (method === 'commits') {
      reads.push({method, ...params});
      yield {data: commitHistory || [{sha: params.sha}, {sha: history()[2].head_sha}]};
      return;
    }
    assert.equal(params.workflow_id, current.workflow_id);
    const responses = [];
    for (let start = 0; start < runs.length; start += workflowPageSize) {
      responses.push({data: {total_count: runs.length, workflow_runs: runs.slice(start, start + workflowPageSize)}});
    }
    yield* paginatedResponses(responses);
  };
  return {
    mutations, stored, reads, messages,
    setJobs: (id, jobs) => jobOverrides.set(id, jobs),
    setCurrent: changes => {current = {...current, ...changes};},
    setHistory: values => {runs = values;},
    setPulls: (sha, values) => pullOverrides.set(sha, values),
    setCommits: values => {commits = values;},
    setCommitHistory: values => {commitHistory = values;},
    run: () => implementation.run({github, context: {repo, payload: replay ? {} : {workflow_run: current}},
      core: {info: message => messages.push(message)},
      env: {MAIN_RED_RUN_ID: String(current.id), MAIN_RED_DRY_RUN: String(dryRun)}}),
  };
}

test('Octokit-shaped paginator normalizes workflow-run pages but preserves compare objects', async () => {
  const h = harness({workflowPageSize: 1});
  await h.run();
  assert.equal(h.stored.length, 1);
  assert.match(h.stored[0].body, /green-11/u);
  assert.match(h.stored[0].body, /First red SHA: b{40}/u);
  assert.match(h.stored[0].body, /First red merge: #42/u);
  assert.doesNotMatch(h.stored[0].body, /Attribution lookup unavailable/u);
  assert.ok(h.reads.some(read => read.method === 'compare' && read.basehead === `${'a'.repeat(40)}...${'b'.repeat(40)}`));
});

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

test('neutral fixture rows between green and red never become attribution or recovery', () => {
  const runs = JSON.parse(fixture('neutral-history.json'));
  const episode = policy.findEpisode(runs[0], runs);
  assert.equal(episode.firstRed.id, 24);
  assert.equal(episode.green.id, 21);
  for (const conclusion of ['cancelled', 'action_required']) {
    assert.equal(policy.findEpisode({...runs[0], conclusion}, runs), null);
  }
  assert.equal(policy.findEpisode(history()[3], history()), null);
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
  assert.equal(policy.introducingMerge(sha, [{}, null]), null);
});

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

for (const sameSha of [true, false]) {
  test(`first-red green rerun closes an incident reported by a later red (${sameSha ? 'same' : 'later'} SHA)`, async () => {
    const h = harness();
    const first = history()[1];
    const later = {...history()[0], head_sha: sameSha ? first.head_sha : history()[0].head_sha};
    h.setCurrent(later);
    h.setHistory([later, ...history().slice(1)]);
    await h.run();
    assert.match(h.stored[0].body, /<!-- main-red:first-red-run-12 -->/u);
    assert.doesNotMatch(h.stored[0].body, /<!-- main-red:run-12-attempt-/u);
    const rerun = {...first, conclusion: 'success', run_attempt: 2,
      html_url: 'https://github.com/example/repo/actions/runs/12'};
    h.setCurrent(rerun);
    h.setHistory([later, rerun, ...history().slice(2)]);
    await h.run();
    await h.run();
    assert.equal(h.stored.length, 1);
    assert.equal(h.stored[0].state, 'closed');
    assert.equal(h.stored[0].comments.length, 1);
    assert.match(h.stored[0].comments[0].body, /run-12-attempt-2/u);
  });
}

test('first observed manual red keeps its recovery marker when attribution selects a later red push', async () => {
  const h = harness();
  const first = {...history()[1], event: 'workflow_dispatch'};
  h.setHistory([history()[0], first, ...history().slice(2)]);
  await h.run();
  assert.match(h.stored[0].body, /<!-- main-red:first-red-run-12 -->/u);
  assert.match(h.stored[0].body, /First red SHA: c{40}/u);
  const rerun = {...first, conclusion: 'success', run_attempt: 2};
  h.setCurrent(rerun);
  h.setHistory([history()[0], rerun, ...history().slice(2)]);
  await h.run();
  assert.equal(h.stored[0].state, 'closed');
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

for (const corePolicy of [true]) {
  const layer = corePolicy ? 'Core shared policy' : 'Pro adapter';
  for (const conclusion of ['cancelled', 'action_required']) {
    test(`${layer}: ${conclusion}-only history opens nothing and cannot recover an incident`, async () => {
      const h = harness({corePolicy});
      h.setCurrent({conclusion});
      h.setHistory(history().map(run => ({...run, conclusion})));
      await h.run();
      assert.equal(h.mutations.length, 0);
      h.setHistory(history());
      h.setCurrent({conclusion: 'failure'});
      await h.run();
      h.setCurrent({id: 14, run_number: 14, conclusion});
      await h.run();
      assert.equal(h.stored[0].state, 'open');
      assert.equal(h.stored[0].comments.length, 0);
    });

    test(`${layer}: ${conclusion} between green and red preserves the first red attribution`, async () => {
      const h = harness({corePolicy});
      const runs = JSON.parse(fixture('neutral-history.json'));
      h.setHistory(runs);
      h.setCurrent(runs[0]);
      await h.run();
      assert.match(h.stored[0].body, /First red SHA: b{40}/u);
      assert.match(h.stored[0].body, /green-21/u);
      assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
    });
  }

  for (const event of ['schedule', 'workflow_dispatch']) {
    test(`${layer}: a ${event} red uses the first red push even after an earlier manual red`, async () => {
      const h = harness({corePolicy});
      const runs = history();
      // A non-push failure can precede the first observed red push in this episode.
      const earlier = {...runs[1], id: 111, run_number: 12, event: 'workflow_dispatch', head_sha: 'd'.repeat(40)};
      const push = {...runs[1], run_number: 13};
      h.setHistory([earlier, push, runs[2]]);
      h.setCurrent({id: 14, run_number: 14, event, head_sha: 'e'.repeat(40)});
      await h.run();
      const body = h.stored[0].body;
      assert.match(body, /First red SHA: b{40}/u);
      assert.match(body, /First red push:.*b{40}/u);
      assert.doesNotMatch(body, /First red SHA: [de]{40}/u);
      assert.ok(h.reads.some(read => read.method === 'pulls' && read.commit_sha === push.head_sha));
    });

    test(`${layer}: a ${event} red without a red push reports unknown attribution`, async () => {
      const h = harness({corePolicy});
      h.setHistory(history().map(run => ({...run, event})));
      h.setCurrent({event});
      await h.run();
      assert.match(h.stored[0].body, /Observed red SHA: b{40}/u);
      assert.match(h.stored[0].body, /No red push run found/u);
      assert.match(h.stored[0].body, /Merges in observed red range: PRs #42/u);
      assert.match(h.stored[0].body, /introducing merge unknown/iu);
      assert.doesNotMatch(h.stored[0].body, /First red merge:/u);
    });

    test(`${layer}: a ${event} red attributes the observed range to its PRs and last merger without push runs`, async () => {
      const h = harness({corePolicy});
      const runs = history().map(run => ({...run, event}));
      const earlierSha = 'd'.repeat(40);
      const redSha = runs[1].head_sha;
      h.setHistory(runs);
      h.setCurrent({event});
      h.setCommits([{sha: earlierSha}, {sha: redSha}]);
      h.setPulls(earlierSha, [{number: 41, merged_at: '2026-11-26T02:30:00Z', merge_commit_sha: earlierSha,
        base: {ref: 'main'}, user: {login: 'earlier-builder'}}]);
      h.setPulls(redSha, [{number: 42, merged_at: '2026-11-26T03:00:00Z', merge_commit_sha: redSha,
        base: {ref: 'main'}, user: {login: 'builder'}}]);
      await h.run();
      const body = h.stored[0].body;
      assert.match(body, /SHA range: a{40}\.\.\.b{40}/u);
      assert.match(body, /Merges in observed red range: PRs #41, #42/u);
      assert.match(body, new RegExp(`No red push run found.*first observed ${event} red`, 'u'));
      assert.match(body, /Owner: @merger-42 \(last merger\)/u);
      assert.match(body, /AssertionError: expected ready/u);
      assert.doesNotMatch(body, /First red (?:push|merge):/u);
      assert.doesNotMatch(body, /Introducing merge batch:/u);
    });
  }

  test(`${layer}: a scheduled red on the last green SHA does not list a baseline PR as a range merge`, async () => {
    const h = harness({corePolicy});
    const green = history()[2];
    h.setHistory([green]);
    h.setCurrent({event: 'schedule', head_sha: green.head_sha});
    await h.run();
    assert.match(h.stored[0].body, /Merges in observed red range: no matching PRs found/u);
    assert.doesNotMatch(h.stored[0].body, /Merges in observed red range: PRs/u);
    assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
    assert.equal(h.reads.some(read => read.method === 'compare'), false);
  });

  for (const scenario of ['direct push', 'revert']) {
    test(`${layer}: ${scenario} creates and updates an unknown-merge issue with the range and last merger`, async () => {
      const h = harness({corePolicy});
      const sha = history()[1].head_sha;
      h.setPulls(sha, scenario === 'revert' ? [{number: 42, merged_at: '2026-11-26T01:00:00Z',
        merge_commit_sha: history()[2].head_sha, base: {ref: 'main'}, user: {login: 'builder'}}] : []);
      h.setCommits([{sha, commit: {message: scenario === 'revert' ? 'Revert "a change"' : 'Direct change'}}]);
      await h.run();
      assert.equal(h.stored.length, 1);
      assert.match(h.stored[0].body, /introducing merge unknown/iu);
      assert.match(h.stored[0].body, /SHA range: a{40}\.\.\.b{40}/u);
      assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
      assert.match(h.stored[0].body, /AssertionError: expected ready/u);
      h.setCurrent({id: 14, run_number: 14});
      await h.run();
      assert.equal(h.stored[0].comments.length, 1);
      assert.match(h.stored[0].comments[0].body, /introducing merge unknown/iu);
    });
  }

  test(`${layer}: merge-queue batches list every PR in the range and use the last merger`, async () => {
    const h = harness({corePolicy});
    const first = 'd'.repeat(40);
    const last = history()[1].head_sha;
    h.setCommits([{sha: first}, {sha: last}]);
    h.setPulls(first, [{number: 41, merged_at: '2026-11-26T02:30:00Z', merge_commit_sha: first,
      base: {ref: 'main'}, user: {login: 'earlier-builder'}}]);
    h.setPulls(last, [{number: 42, merged_at: '2026-11-26T03:00:00Z', merge_commit_sha: last,
      base: {ref: 'main'}, user: {login: 'builder'}}]);
    await h.run();
    assert.match(h.stored[0].body, /batch: PRs #41, #42/u);
    assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
    assert.match(h.stored[0].body, /SHA range: a{40}\.\.\.b{40}/u);
    assert.doesNotMatch(h.stored[0].body, /First red merge: #42/u);
  });

  test(`${layer}: multiple PRs associated with one batch SHA do not suppress the issue`, async () => {
    const h = harness({corePolicy});
    const sha = history()[1].head_sha;
    h.setPulls(sha, [41, 42, 43].map(number => ({number, merged_at: `2026-11-26T03:00:0${number - 40}Z`,
      merge_commit_sha: sha, base: {ref: 'main'}, user: {login: 'builder'}})));
    await h.run();
    assert.match(h.stored[0].body, /batch: PRs #41, #42, #43/u);
    assert.match(h.stored[0].body, /Owner: @merger-43 \(last merger\)/u);
  });

  test(`${layer}: a batch with distinct merge SHAs keeps all in-range associated PRs`, async () => {
    const h = harness({corePolicy});
    h.setPulls(history()[1].head_sha, [41, 42].map(number => ({number,
      merged_at: `2026-11-26T02:${number + 4}:00Z`, merge_commit_sha: 'f'.repeat(40),
      base: {ref: 'main'}, user: {login: 'builder'}})));
    await h.run();
    assert.match(h.stored[0].body, /batch: PRs #41, #42/u);
    assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
  });

  test(`${layer}: unavailable PR author data does not suppress a known merge incident`, async () => {
    const h = harness({corePolicy});
    const sha = history()[1].head_sha;
    h.setPulls(sha, [{number: 42, merged_at: '2026-11-26T03:00:00Z', merge_commit_sha: sha,
      base: {ref: 'main'}, user: null}]);
    await h.run();
    assert.match(h.stored[0].body, /First red merge: #42/u);
    assert.match(h.stored[0].body, /GitHub author: unknown/u);
    assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
  });

  test(`${layer}: missing history still opens an unknown-range incident for a direct push`, async () => {
    const h = harness({corePolicy});
    h.setHistory([]);
    h.setPulls(history()[0].head_sha, []);
    h.setCommitHistory([{sha: history()[0].head_sha}, {sha: history()[2].head_sha}]);
    await h.run();
    assert.match(h.stored[0].body, /introducing merge unknown/iu);
    assert.match(h.stored[0].body, /SHA range: unknown baseline\.\.\.c{40}/u);
    assert.match(h.stored[0].body, /Owner: @merger-42 \(last merger\)/u);
  });

  for (const failure of ['compareFails', 'ownerFails']) {
    test(`${layer}: ${failure} cannot suppress the incident or failure evidence`, async () => {
      const h = harness({corePolicy, [failure]: true});
      await h.run();
      assert.equal(h.stored.length, 1);
      assert.match(h.stored[0].body, /Attribution lookup unavailable \(HTTP 503\)/u);
      assert.match(h.stored[0].body, /SHA range: a{40}\.\.\.b{40}/u);
      assert.match(h.stored[0].body, /AssertionError: expected ready/u);
    });
  }
}
