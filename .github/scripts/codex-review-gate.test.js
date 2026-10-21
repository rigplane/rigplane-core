'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');

const {
  buildCodexVerdict,
  codexReviewedHead,
  readReviewThreads,
  unresolvedCodexFindings,
} = require('./codex-review-gate.js');

const HEAD = 'dae01ac3d5ee33985356568c688636c3fc8d8824';
const OTHER = '0123456789abcdef0123456789abcdef01234567';
const CODEX = {login: 'chatgpt-codex-connector[bot]', type: 'Bot'};
const HUMAN = {login: 'morozsm', type: 'User'};

const summary = (sha, status = 'completed') => ({
  user: CODEX,
  body:
    '<!-- codex-pull-request-review-summary -->\n' +
    `<!-- codex-review-state:v1 {"code_review":{"commit_sha":${JSON.stringify(sha)},` +
    `"has_any_findings":true,"has_unresolved_findings":true,"status":"${status}"},` +
    '"security_review":{"commit_sha":null,"has_any_findings":null,' +
    '"has_unresolved_findings":null,"status":null}} -->\n\n## Codex Review Summary',
});
const receipt = (short) => ({
  user: CODEX,
  body:
    "<!-- codex-review-receipt:v1 -->\n\nCodex Review: Didn't find any major issues. Bravo.\n\n" +
    `**Reviewed commit:** \`${short}\`\n`,
});
const thread = (body, {resolved = false, author = {login: 'chatgpt-codex-connector', __typename: 'Bot'}} = {}) => ({
  isResolved: resolved,
  path: 'src/a.py',
  line: 7,
  comments: {nodes: [{url: 'https://x/1', body, author}]},
});
const P0 = '**<sub><sub>![P0 Badge](https://img.shields.io/badge/P0-red?style=flat)</sub></sub>  Bug**';
const P1 = '**<sub><sub>![P1 Badge](https://img.shields.io/badge/P1-orange?style=flat)</sub></sub>  Bug**';
const P2 = '**<sub><sub>![P2 Badge](https://img.shields.io/badge/P2-yellow?style=flat)</sub></sub>  Bug**';
const P3 = '**<sub><sub>![P3 Badge](https://img.shields.io/badge/P3-green?style=flat)</sub></sub>  Nit**';

test('review object at the exact head counts, any other sha does not', () => {
  const review = (sha) => ({user: CODEX, commit_id: sha, state: 'COMMENTED'});
  assert.deepEqual(codexReviewedHead({reviews: [review(HEAD)], comments: [], headSha: HEAD}), {reviewed: true, via: 'review'});
  assert.equal(codexReviewedHead({reviews: [review(OTHER)], comments: [], headSha: HEAD}).reviewed, false);
});

test('a pending, dismissed or human review does not count', () => {
  const pending = {user: CODEX, commit_id: HEAD, state: 'PENDING'};
  const dismissed = {user: CODEX, commit_id: HEAD, state: 'DISMISSED'};
  const human = {user: HUMAN, commit_id: HEAD, state: 'COMMENTED'};
  assert.equal(codexReviewedHead({reviews: [pending, dismissed, human], comments: [], headSha: HEAD}).reviewed, false);
});

test('summary comment counts only when completed and for the exact head', () => {
  assert.equal(codexReviewedHead({reviews: [], comments: [summary(HEAD)], headSha: HEAD}).via, 'summary');
  assert.equal(codexReviewedHead({reviews: [], comments: [summary(OTHER)], headSha: HEAD}).reviewed, false);
  assert.equal(codexReviewedHead({reviews: [], comments: [summary(HEAD, 'in_progress')], headSha: HEAD}).reviewed, false);
});

test('clean receipt matches the abbreviated head, a short or wrong prefix does not', () => {
  assert.equal(codexReviewedHead({reviews: [], comments: [receipt('dae01ac3d5')], headSha: HEAD}).via, 'receipt');
  assert.equal(codexReviewedHead({reviews: [], comments: [receipt('dae01ac')], headSha: HEAD}).reviewed, false);
  assert.equal(codexReviewedHead({reviews: [], comments: [receipt('0123456789')], headSha: HEAD}).reviewed, false);
});

test('a human quoting Codex markers is not Codex', () => {
  const fake = {...summary(HEAD), user: HUMAN};
  const fakeReceipt = {...receipt('dae01ac3d5'), user: {login: 'chatgpt-codex-connector', type: 'User'}};
  assert.equal(codexReviewedHead({reviews: [], comments: [fake, fakeReceipt], headSha: HEAD}).reviewed, false);
});

test('a malformed state block is ignored', () => {
  const broken = {user: CODEX, body: '<!-- codex-review-state:v1 {"code_review": -->'};
  assert.equal(codexReviewedHead({reviews: [], comments: [broken], headSha: HEAD}).reviewed, false);
});

test('a non-sha head never matches', () => {
  assert.equal(codexReviewedHead({reviews: [], comments: [receipt('dae01ac3d5')], headSha: 'dae01ac3d5'}).reviewed, false);
});

test('only unresolved Codex P1/P2 threads are findings', () => {
  const found = unresolvedCodexFindings([
    thread(P0),
    thread(P1),
    thread(P2),
    thread(P3),
    thread(P1, {resolved: true}),
    thread(P1, {author: {login: 'morozsm', __typename: 'User'}}),
    {isResolved: false, comments: {nodes: []}},
  ]);
  assert.deepEqual(found.map((f) => f.priority), ['P0', 'P1', 'P2']);
});

const none = {pass: false, blocked: false, description: ''};
const reviewed = {reviewed: true, via: 'review'};
const absent = {reviewed: false, via: null};

test('Codex reviewed + no open threads -> success', () => {
  const v = buildCodexVerdict({codex: reviewed, unresolved: [], backup: none, headSha: HEAD});
  assert.equal(v.state, 'success');
  assert.match(v.description, /^Codex reviewed dae01ac/u);
});

test('Codex reviewed but a P1/P2 thread is open -> failure even with a backup PASS', () => {
  const open = [{priority: 'P2', path: 'a.py', line: 3}];
  const v = buildCodexVerdict({codex: reviewed, unresolved: open, backup: {...none, pass: true}, headSha: HEAD});
  assert.equal(v.state, 'failure');
  assert.match(v.description, /1 unresolved Codex P0-P2 thread\(s\): P2 a\.py:3/u);
});

test('no Codex review and no backup -> failure that names the wait', () => {
  const v = buildCodexVerdict({codex: absent, unresolved: [], backup: none, headSha: HEAD});
  assert.equal(v.state, 'failure');
  assert.match(v.description, /Waiting for Codex review of dae01ac; backup review after 20 min/u);
});

test('backup PASS turns the gate green only when Codex did not review', () => {
  const v = buildCodexVerdict({codex: absent, unresolved: [], backup: {...none, pass: true}, headSha: HEAD});
  assert.equal(v.state, 'success');
  assert.match(v.description, /^Backup cross-family PASS/u);
});

test('a backup BLOCKED directive beats a Codex review', () => {
  const blocked = {pass: false, blocked: true, description: 'BLOCKED is active.'};
  const v = buildCodexVerdict({codex: reviewed, unresolved: [], backup: blocked, headSha: HEAD});
  assert.deepEqual(v, {state: 'failure', description: 'BLOCKED is active.'});
});

test('a GPT-built PR does not take Codex as its independent reviewer', () => {
  const gpt = {codex: reviewed, unresolved: [], backup: none, headSha: HEAD, gptBuilt: true};
  const v = buildCodexVerdict(gpt);
  assert.equal(v.state, 'failure');
  assert.match(v.description, /^Label built-by-gpt: Codex is not independent/u);
  assert.equal(buildCodexVerdict({...gpt, backup: {...none, pass: true}}).state, 'success');
});

test('every description fits the 140-character status limit', () => {
  const open = Array.from({length: 9}, () => ({priority: 'P1', path: 'x'.repeat(80), line: 100}));
  const v = buildCodexVerdict({codex: reviewed, unresolved: open, backup: none, headSha: HEAD});
  assert.ok(v.description.length <= 140);
});

test('review threads are read page by page', async () => {
  const pages = [
    {pageInfo: {hasNextPage: true, endCursor: 'c1'}, nodes: [{id: 1}]},
    {pageInfo: {hasNextPage: false, endCursor: null}, nodes: [{id: 2}]},
  ];
  const cursors = [];
  const github = {
    graphql: async (_query, vars) => {
      cursors.push(vars.after);
      return {repository: {pullRequest: {reviewThreads: pages.shift()}}};
    },
  };
  const threads = await readReviewThreads({github, owner: 'o', repo: 'r', number: 1});
  assert.deepEqual(threads, [{id: 1}, {id: 2}]);
  assert.deepEqual(cursors, [null, 'c1']);
});
