'use strict';

const assert = require('node:assert/strict');
const test = require('node:test');

const {parseQueueRef, evaluateAgentReviewGate, run} = require('./merge-queue-gates');

const SHA = 'a'.repeat(40);
const SHORT = `gh-readonly-queue/main/pr-42-${SHA}`;

test('parses the short and the full queue ref', () => {
  assert.deepEqual(parseQueueRef(SHORT), {base: 'main', number: 42, queuedHeadSha: SHA});
  assert.equal(parseQueueRef(`refs/heads/${SHORT}`).number, 42);
  assert.equal(parseQueueRef(`gh-readonly-queue/release/1.x/pr-7-${SHA}`).base, 'release/1.x');
});

test('rejects refs that do not name exactly one PR', () => {
  for (const ref of [undefined, '', 'main', `gh-readonly-queue/main/pr-x-${SHA}`, 'gh-readonly-queue/main/pr-1-abc']) {
    assert.equal(parseQueueRef(ref), null, String(ref));
  }
});

const queued = parseQueueRef(SHORT);
const pull = {number: 42, head: {sha: SHA}};

test('agent review gate needs a success status on the queued head', () => {
  assert.equal(evaluateAgentReviewGate({queued, pull, statuses: [{context: 'Agent Review Gate', state: 'success'}]}).ok, true);
  assert.equal(evaluateAgentReviewGate({queued, pull, statuses: [{context: 'Agent Review Gate', state: 'failure'}]}).ok, false);
  assert.equal(evaluateAgentReviewGate({queued, pull, statuses: []}).ok, false);
  assert.equal(evaluateAgentReviewGate({queued, pull: {number: 42, head: {sha: 'b'.repeat(40)}}, statuses: [{context: 'Agent Review Gate', state: 'success'}]}).ok, false);
});

function fakeApi({pullData, statuses, throwOn}) {
  const calls = {published: [], failed: []};
  return {
    calls,
    github: {rest: {
      pulls: {get: async () => { if (throwOn === 'pull') throw new Error('boom'); return {data: pullData}; }},
      repos: {
        getCombinedStatusForRef: async () => ({data: {statuses}}),
        createCommitStatus: async (status) => { calls.published.push(status); },
      },
    }},
    core: {setFailed: (m) => calls.failed.push(m), info: () => {}},
    context: {repo: {owner: 'o', repo: 'r'}, serverUrl: 'https://github.com', runId: 1},
  };
}

test('run publishes success for a passing head', async () => {
  const api = fakeApi({pullData: pull, statuses: [{context: 'Agent Review Gate', state: 'success'}]});
  await run({...api, gate: 'agent-review', ref: SHORT, sha: 'c'.repeat(40)});
  assert.deepEqual(api.calls.published.map((s) => s.state), ['success']);
  assert.equal(api.calls.failed.length, 0);
});

test('run fails closed on a bad ref and on API errors, publishing a failure status', async () => {
  const bad = fakeApi({pullData: pull, statuses: []});
  await run({...bad, gate: 'agent-review', ref: 'main', sha: 'c'.repeat(40)});
  assert.deepEqual(bad.calls.published.map((s) => s.state), ['failure']);
  assert.equal(bad.calls.failed.length, 1);
  const broken = fakeApi({pullData: pull, statuses: [], throwOn: 'pull'});
  await run({...broken, gate: 'agent-review', ref: SHORT, sha: 'c'.repeat(40)});
  assert.deepEqual(broken.calls.published.map((s) => s.state), ['failure']);
});

test('freeze gate reuses the freeze contract and publishes no status', async () => {
  const ok = fakeApi({pullData: {title: 'ci: x', body: 'Change class: ci', labels: [], draft: false}, statuses: []});
  await run({...ok, gate: 'freeze', ref: SHORT, sha: 'c'.repeat(40)});
  assert.equal(ok.calls.failed.length, 0);
  assert.equal(ok.calls.published.length, 0);
  const feat = fakeApi({pullData: {title: 'feat: x', body: 'Change class: fix', labels: [], draft: false}, statuses: []});
  await run({...feat, gate: 'freeze', ref: SHORT, sha: 'c'.repeat(40)});
  assert.equal(feat.calls.failed.length, 1);
});
