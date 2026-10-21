const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  buildCommitStatus,
  evaluateReviewGate,
  parseReviewDirectives,
} = require('./agent-review-gate');
const codexGate = require('./codex-review-gate');

const HEAD = '1234567890abcdef1234567890abcdef12345678';
const STALE = 'abcdef1234567890abcdef1234567890abcdef12';
const COMMITTED_AT = new Date('2026-08-08T04:50:00Z');
const AGENTS_PATH = path.join(__dirname, '..', '..', 'AGENTS.md');
const WORKFLOW_PATH = path.join(__dirname, '..', 'workflows', 'agent-review-gate.yml');
const WORKFLOW_V2_PATH = path.join(__dirname, '..', 'workflows', 'agent-review-gate-v2.yml');

// The one predicate "this event can change the verdict", kept byte-identical
// between each workflow's job-level `if:` and its workflow-level concurrency
// group (pinned by 'job condition and concurrency group share one
// verdict-relevance predicate' below).
const ISSUE_COMMENT_PREDICATE =
  "github.event.issue.pull_request && (contains(github.event.comment.body, 'Agent Review:') || contains(github.event.changes.body.from, 'Agent Review:'))";
// Codex's verdict arrives as a PR review, as comments by its bot (the summary
// comment is edited in place on every review) and, for thread resolution which
// fires no event, as a manual dispatch.
const CODEX_COMMENT_PREDICATE =
  "github.event.issue.pull_request && github.event.comment.user.login == 'chatgpt-codex-connector[bot]'";
const GATE_PREDICATE = `github.event_name == 'pull_request' || github.event_name == 'pull_request_review' || github.event_name == 'workflow_dispatch' || (${ISSUE_COMMENT_PREDICATE}) || (${CODEX_COMMENT_PREDICATE})`;
const V2_PREDICATE =
  `github.event_name == 'pull_request_target' && (github.event.action != 'edited' || github.event.changes.base) || (${ISSUE_COMMENT_PREDICATE})`;

function directive(result, sha = HEAD) {
  return `Agent Review: ${result} ${sha}`;
}

function comment(body, overrides = {}) {
  return {
    author_association: 'MEMBER',
    body,
    created_at: '2026-08-08T04:51:00Z',
    updated_at: '2026-08-08T04:51:00Z',
    minimized: false,
    ...overrides,
  };
}

function evaluate(comments) {
  return evaluateReviewGate({
    comments,
    headSha: HEAD,
    committedAt: COMMITTED_AT,
  });
}

function assertFailure(body, message = body) {
  assert.equal(evaluate([comment(body)]).state, 'failure', message);
}

function workflowScript(stepName, workflowPath = WORKFLOW_PATH) {
  const workflow = fs.readFileSync(workflowPath, 'utf8');
  const marker = `      - name: ${stepName}\n`;
  const stepStart = workflow.indexOf(marker);
  assert.notEqual(stepStart, -1, `missing workflow step ${stepName}`);
  const scriptMarker = '          script: |\n';
  const scriptStart = workflow.indexOf(scriptMarker, stepStart);
  assert.notEqual(scriptStart, -1, `missing script for ${stepName}`);
  const bodyStart = scriptStart + scriptMarker.length;
  const lines = [];
  for (const line of workflow.slice(bodyStart).split('\n')) {
    if (line.length > 0 && !line.startsWith('            ')) {
      break;
    }
    lines.push(line.startsWith('            ') ? line.slice(12) : '');
  }
  return lines.join('\n');
}

async function runWorkflowScript(
  script,
  {github, context, core, workspace, requireFn = require},
) {
  const AsyncFunction = Object.getPrototypeOf(async function () {}).constructor;
  const processMock = {env: {GITHUB_WORKSPACE: workspace}};
  return new AsyncFunction('github', 'context', 'core', 'require', 'process', script)(
    github,
    context,
    core,
    requireFn,
    processMock,
  );
}

function coreMock() {
  const outputs = {};
  return {
    errors: [],
    failures: [],
    notices: [],
    outputs,
    error(value) {
      this.errors.push(value);
    },
    notice(value) {
      this.notices.push(value);
    },
    setFailed(value) {
      this.failures.push(value);
    },
    setOutput(name, value) {
      outputs[name] = String(value);
    },
  };
}

function updaterScript({
  number = '2321',
  head = HEAD,
  committedAt = '2026-08-08T04:50:00Z',
  draft = 'false',
} = {}) {
  return workflowScript('Update required Agent Review Gate status')
    .replace("Number('${{ steps.pr.outputs.number }}')", `Number('${number}')`)
    .replace("'${{ steps.pr.outputs.head_sha }}'", `'${head}'`)
    .replace("'${{ steps.pr.outputs.committed_at }}'", `'${committedAt}'`)
    .replace("'${{ steps.pr.outputs.draft }}'", `'${draft}'`);
}

function workflowContext(runId = 1, action = undefined) {
  return {
    repo: {owner: 'rigplane', repo: 'rigplane-core'},
    runId,
    serverUrl: 'https://github.test',
    payload: {action},
  };
}

const CODEX_BOT = {login: 'chatgpt-codex-connector[bot]', type: 'Bot'};

// A complete fake of the GitHub client surface the updater step touches.
// `paginate` routes by the REST function it is handed, as the real one does.
function gateGithub({comments = [], reviews = [], threads = [], overrides = {}} = {}) {
  const statuses = [];
  const created = [];
  const graphqlCalls = [];
  const restCalls = [];
  const listComments = () => {};
  const listReviews = () => {};
  const github = {
    paginate: async (fn) => {
      if (fn === listComments) {
        return comments;
      }
      if (fn === listReviews) {
        return reviews;
      }
      throw new Error('unexpected paginate target');
    },
    graphql: async (query, variables) => {
      graphqlCalls.push(variables);
      return {
        repository: {
          pullRequest: {
            reviewThreads: {pageInfo: {hasNextPage: false, endCursor: null}, nodes: threads},
          },
        },
      };
    },
    rest: {
      issues: {
        listComments,
        createComment: async (args) => {
          restCalls.push('issues.createComment');
          created.push(args);
        },
      },
      pulls: {listReviews},
      repos: {
        createCommitStatus: async (status) => {
          restCalls.push('repos.createCommitStatus');
          statuses.push(status);
        },
      },
    },
    ...overrides,
  };
  return {github, statuses, created, graphqlCalls, restCalls};
}

// Routes the codex module to the real one so the stubbed-parser scenarios still
// exercise the real Codex layer.
function realCodexRequire(parserModule) {
  return (modulePath) =>
    String(modulePath).endsWith('codex-review-gate.js') ? codexGate : parserModule;
}

async function runGate({
  comments = [],
  reviews = [],
  threads = [],
  action = undefined,
  draft = 'false',
  overrides = {},
  runId = 20,
} = {}) {
  const fake = gateGithub({comments, reviews, threads, overrides});
  const core = coreMock();
  await runWorkflowScript(updaterScript({draft}), {
    github: fake.github,
    context: workflowContext(runId, action),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  return {...fake, core};
}

function codexReview(overrides = {}) {
  return {user: CODEX_BOT, commit_id: HEAD, state: 'COMMENTED', ...overrides};
}

function codexThread(priority = 'P1', {resolved = false, login = 'chatgpt-codex-connector'} = {}) {
  return {
    isResolved: resolved,
    path: 'src/rigplane/example.py',
    line: 12,
    originalLine: 12,
    comments: {
      nodes: [
        {
          url: 'https://github.test/rigplane/rigplane-core/pull/1#discussion_r1',
          body: `**![${priority} Badge](https://img.shields.io/badge/${priority}-red)** Finding`,
          author: {login, __typename: 'Bot'},
        },
      ],
    },
  };
}

test('binds an exact current-head PASS on the first physical nonblank line', () => {
  const bodies = [
    directive('PASS'),
    `\n${directive('PASS')}`,
    ` \t\n\t\n${directive('PASS')}\nExplanation`,
    `\uFEFF${directive('PASS')}\nExplanation`,
    ` \t\r\n\t\r\n${directive('PASS')}\r\nExplanation`,
  ];

  for (const body of bodies) {
    const result = evaluate([comment(body)]);
    assert.equal(result.state, 'success', JSON.stringify(body));
    assert.equal(result.passCount, 1);
  }
});

test('normalization rejects ambiguous BOM, bare CR, and Unicode-leading lines', () => {
  const prefixes = [
    '\uFEFF\uFEFF',
    `\n\uFEFF`,
    '\u00a0\n',
    '\u2003\n',
    '\u200b\n',
    '\u200d\n',
    '\u202e\n',
    '\u0301\n',
  ];
  for (const prefix of prefixes) {
    assertFailure(`${prefix}${directive('PASS')}`, JSON.stringify(prefix));
  }

  assertFailure(`${directive('PASS')}\rDetails`, 'bare CR after directive');
  assertFailure(`\r${directive('PASS')}`, 'bare CR before directive');
  assertFailure(`\uFEFF\uFEFF${directive('PASS')}`, 'exactly one BOM only');
});

test('directive grammar is exact and ASCII-only', () => {
  const bodies = [
    ` ${directive('PASS')}`,
    `\t${directive('PASS')}`,
    `${directive('PASS')} `,
    `${directive('PASS')}\t`,
    `agent Review: PASS ${HEAD}`,
    `Agent review: PASS ${HEAD}`,
    `Agent Review: pass ${HEAD}`,
    `Agent Review: PАSS ${HEAD}`,
    directive('PASS', HEAD.toUpperCase()),
    directive('PASS', HEAD.slice(0, 39)),
    directive('PASS', `${HEAD}0`),
    directive('PASS', STALE),
    `Agent Review: PASS x${HEAD}`,
    `Agent Review: PASS ${HEAD}.`,
    `${directive('PASS')} extra`,
    `> ${directive('PASS')}`,
    `- ${directive('PASS')}`,
    `\`${directive('PASS')}\``,
  ];

  for (const body of bodies) {
    assertFailure(body);
  }
});

test('only the first physical ASCII-nonblank line can contribute a directive', () => {
  const historicalAndMarkdownBodies = [
    `No Agent Review: PASS is valid for this head.\n${directive('PASS')}`,
    `<!--\n${directive('PASS')}\n-->`,
    `>> <!--\n> -->\n${directive('PASS')}`,
    `> quoted paragraph\n<!--\n\n${directive('PASS')}\n-->`,
    `> \`\`\` info\`bad\n${directive('PASS')}`,
    `~~~\n${directive('PASS')}\n~~~`,
    `\`opening code span\n${directive('PASS')}\nclosing\``,
    `# Heading\n${directive('PASS')}`,
    `ordinary prose\n${directive('PASS')}`,
    `\u00a0\n${directive('PASS')}`,
  ];

  for (const body of historicalAndMarkdownBodies) {
    assertFailure(body);
  }
});

test('later explanation is inert even when it contains exact-looking directives', () => {
  const pass = evaluate([
    comment(`${directive('PASS')}\n${directive('BLOCKED')}\n<!-- arbitrary Markdown -->`),
  ]);
  assert.equal(pass.state, 'success');
  assert.equal(pass.passCount, 1);
  assert.equal(pass.blockedCount, 0);

  assertFailure(`${directive('PASS', STALE)}\n${directive('PASS')}`);
  assertFailure(`prose first\n${directive('BLOCKED')}\nDetails 123`);
  assertFailure(`${directive('PASS')} and ${directive('BLOCKED')}`);
});

test('valid current-head BLOCKED requires details and globally overrides PASS', () => {
  const blockers = [
    `${directive('BLOCKED')}\nConcrete English details`,
    `${directive('BLOCKED')}\nПричина блокировки`,
    `${directive('BLOCKED')}\nIssue 2320`,
  ];

  for (const blocker of blockers) {
    const result = evaluate([comment(directive('PASS')), comment(blocker)]);
    assert.equal(result.state, 'failure');
    assert.equal(result.passCount, 1);
    assert.equal(result.blockedCount, 1);
    assert.equal(result.malformedBlockedCount, 0);
  }
});

test('missing BLOCKED details deny instead of disappearing', () => {
  const malformedBodies = [
    directive('BLOCKED'),
    `${directive('BLOCKED')}\n`,
    `${directive('BLOCKED')}\n \t\n--- !!! \u200d`,
  ];

  for (const body of malformedBodies) {
    const result = evaluate([comment(directive('PASS')), comment(body)]);
    assert.equal(result.state, 'failure', JSON.stringify(body));
    assert.equal(result.blockedCount, 0);
    assert.equal(result.malformedBlockedCount, 1);
  }

  const stale = evaluate([
    comment(directive('PASS')),
    comment(directive('BLOCKED', STALE)),
  ]);
  assert.equal(stale.state, 'success');
  assert.equal(stale.malformedBlockedCount, 0);
});

test('BLOCKED details cannot approve and PASS explanation cannot block', () => {
  const blocked = evaluate([
    comment(`${directive('BLOCKED')}\n${directive('PASS')} is quoted evidence`),
  ]);
  assert.equal(blocked.state, 'failure');
  assert.equal(blocked.blockedCount, 1);
  assert.equal(blocked.passCount, 0);

  const passed = evaluate([
    comment(`${directive('PASS')}\n${directive('BLOCKED')} is explanatory text`),
  ]);
  assert.equal(passed.state, 'success');
  assert.equal(passed.blockedCount, 0);
});

test('trust, minimization, and PASS freshness are enforced', () => {
  for (const association of ['OWNER', 'MEMBER', 'COLLABORATOR']) {
    assert.equal(
      evaluate([comment(directive('PASS'), {author_association: association})]).state,
      'success',
    );
  }
  for (const association of ['NONE', 'CONTRIBUTOR', 'FIRST_TIMER']) {
    assert.equal(
      evaluate([comment(directive('PASS'), {author_association: association})]).state,
      'failure',
    );
  }

  assert.equal(evaluate([comment(directive('PASS'), {minimized: true})]).state, 'failure');

  // The REST payload (issues.listComments) reports a minimized comment as an
  // OBJECT -- {"reason": "outdated"} -- never as the boolean `true`. A strict
  // `=== true` check therefore never matches a real hidden comment, so hiding
  // a superseded directive had no effect on the gate. Both shapes must skip.
  assert.equal(
    evaluate([comment(directive('PASS'), {minimized: {reason: 'outdated'}})]).state,
    'failure',
  );
  // `reason` is itself nullable in the schema, so {reason: null} is a legal
  // hidden-comment payload even though no public mutation produces one today.
  // This row and the {minimized: true} row above each independently catch a
  // narrowing to `comment.minimized?.reason`; removing either one still leaves
  // the narrowing caught, removing both lets it through silently.
  assert.equal(
    evaluate([comment(directive('PASS'), {minimized: {reason: null}})]).state,
    'failure',
  );
  assert.equal(
    evaluate([
      comment(directive('BLOCKED') + '\n\nsuperseded', {
        minimized: {reason: 'outdated'},
      }),
      comment(directive('PASS')),
    ]).state,
    'success',
  );
  assert.equal(
    evaluate([comment(directive('PASS'), {updated_at: '2026-08-08T04:49:59Z'})]).state,
    'failure',
  );
  assert.equal(
    evaluate([comment(directive('PASS'), {updated_at: '2026-08-08T04:50:00Z'})]).state,
    'success',
  );
  assert.equal(
    evaluate([comment(directive('PASS'), {updated_at: 'not-a-date'})]).state,
    'failure',
  );
});

test('live edits, deletion, blocker order, and large comment sets recompute', () => {
  const pass = comment(directive('PASS'));
  const blocked = comment(`${directive('BLOCKED')}\nConcrete blocker`);
  assert.equal(evaluate([pass]).state, 'success');
  assert.equal(evaluate([{...pass, body: 'edited to prose'}]).state, 'failure');
  assert.equal(evaluate([{...pass, body: blocked.body}]).state, 'failure');
  assert.equal(evaluate([]).state, 'failure');

  for (const comments of [[pass, blocked], [blocked, pass], [pass, pass, blocked]]) {
    assert.equal(evaluate(comments).state, 'failure');
  }

  const comments = Array.from({length: 150}, (_, index) =>
    comment(`ordinary comment ${index}`),
  );
  comments.push(blocked);
  assert.equal(evaluate(comments).state, 'failure');
  assert.equal(evaluate(comments.slice(0, 150).concat(pass)).state, 'success');
});

test('parser exposes current-head intent and malformed blocker explicitly', () => {
  assert.deepEqual(parseReviewDirectives(`\uFEFF\n${directive('PASS')}\nLater`, HEAD), {
    pass: true,
    blocked: false,
    malformedBlocked: false,
    currentHead: true,
  });
  assert.deepEqual(parseReviewDirectives(directive('BLOCKED'), HEAD), {
    pass: false,
    blocked: false,
    malformedBlocked: true,
    currentHead: true,
  });
  assert.deepEqual(parseReviewDirectives(directive('PASS'), 'not-a-head'), {
    pass: false,
    blocked: false,
    malformedBlocked: false,
    currentHead: false,
  });
  assert.deepEqual(parseReviewDirectives(null, HEAD), {
    pass: false,
    blocked: false,
    malformedBlocked: false,
    currentHead: false,
  });
});

test('status targets only the resolved head and malformed blockers fail closed', () => {
  const common = {
    owner: 'rigplane',
    repo: 'rigplane-core',
    headSha: HEAD,
    targetUrl: 'https://example.test/run/1',
  };
  const malformed = buildCommitStatus({
    ...common,
    result: {
      state: 'failure',
      passCount: 1,
      blockedCount: 0,
      malformedBlockedCount: 1,
    },
  });
  assert.equal(malformed.sha, HEAD);
  assert.equal(malformed.state, 'failure');
  assert.match(malformed.description, /malformed/i);

  const invalid = buildCommitStatus({
    ...common,
    result: {state: 'success', passCount: 1, blockedCount: 0},
  });
  assert.equal(invalid.state, 'failure');

  for (const result of [
    {
      state: 'success',
      passCount: 0,
      blockedCount: 0,
      malformedBlockedCount: 0,
    },
    {
      state: 'success',
      passCount: 1,
      blockedCount: 1,
      malformedBlockedCount: 0,
    },
    {
      state: 'failure',
      passCount: -1,
      blockedCount: 0,
      malformedBlockedCount: 0,
    },
  ]) {
    assert.equal(buildCommitStatus({...common, result}).state, 'failure');
  }
});

test('workflow preserves triggers, workflow revision checkout, pagination, and exact target', () => {
  const workflow = fs.readFileSync(WORKFLOW_PATH, 'utf8');
  assert.match(workflow, /types: \[opened, reopened, synchronize, ready_for_review\]/);
  assert.match(workflow, /types: \[created, edited, deleted\]/);
  // Codex's verdict arrives as a review; thread resolution fires no event, so a
  // manual dispatch re-evaluates (GitHub Actions has no review-thread trigger).
  assert.match(workflow, /pull_request_review:\n {4}types: \[submitted, dismissed\]/);
  assert.match(workflow, /workflow_dispatch:\n {4}inputs:\n {6}pr_number:\n {8}description: [^\n]+\n {8}required: true/);
  // `@codex review` is a PR comment: the token needs issues: write, nothing broader.
  assert.match(workflow, /^permissions:\n {2}contents: read\n {2}issues: write\n {2}pull-requests: read\n {2}statuses: write\n/mu);
  assert.match(workflow, /github\.paginate\(github\.rest\.pulls\.listReviews/);
  assert.match(workflow, /readReviewThreads\(\{github, owner, repo, number: pull_number\}\)/);
  assert.match(workflow, /const \{data: pull\} = await github\.rest\.pulls\.get/);
  assert.match(workflow, /core\.setOutput\('head_sha', pull\.head\.sha\)/);
  assert.match(workflow, /ref: \$\{\{ github\.workflow_sha \}\}/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /github\.paginate\(github\.rest\.issues\.listComments/);
  assert.doesNotMatch(workflow, /head_sha\s*=\s*['"]\$\{\{\s*github\.sha/);
});

function jobCondition(workflowText) {
  const match = /^ {4}if: (.+)$/mu.exec(workflowText);
  assert.notEqual(match, null, 'missing job-level if');
  return match[1];
}

function concurrencyGroup(workflowText) {
  const match = /^ {2}group: (.+)$/mu.exec(workflowText);
  assert.notEqual(match, null, 'missing workflow-level concurrency group');
  return match[1];
}

test('job condition and concurrency group share one verdict-relevance predicate', () => {
  const gate = fs.readFileSync(WORKFLOW_PATH, 'utf8');
  const v2 = fs.readFileSync(WORKFLOW_V2_PATH, 'utf8');
  assert.equal(jobCondition(gate), GATE_PREDICATE);
  assert.equal(jobCondition(v2), V2_PREDICATE);
  // A run skipped by the job `if:` is still a workflow run in its
  // concurrency group: without the predicate inside the group, a
  // non-qualifying comment posted within seconds of a directive would
  // cancel the in-flight evaluation and publish nothing (lost verdict).
  // Qualifying runs share the per-PR 'eval' group; every non-qualifying
  // run lands in its own github.run_id group and cancels nothing.
  for (const [name, text, predicate] of [
    ['gate', gate, GATE_PREDICATE],
    ['v2', v2, V2_PREDICATE],
  ]) {
    assert.ok(
      concurrencyGroup(text).includes(`(${(predicate)}) && 'eval' || github.run_id`),
      `${name} concurrency group must route non-qualifying runs into a unique group`,
    );
    assert.match(text, /cancel-in-progress: true/u, name);
  }
  // The issue_comment leg is byte-identical in both workflows and appears
  // exactly twice in each: once in the job `if:`, once in the group.
  for (const [name, text] of [['gate', gate], ['v2', v2]]) {
    assert.equal(text.split(ISSUE_COMMENT_PREDICATE).length - 1, 2, name);
  }
});

test('the verdict-relevance predicate inspects the pre-edit body', () => {
  // An edit that turns a directive into prose carries the marker only in
  // github.event.changes.body.from; without this clause such an edit would
  // be skipped and the deleted verdict would never be recomputed.
  for (const workflowPath of [WORKFLOW_PATH, WORKFLOW_V2_PATH]) {
    assert.ok(
      jobCondition(fs.readFileSync(workflowPath, 'utf8')).includes('github.event.changes.body.from'),
      workflowPath,
    );
  }
});

test('the gate predicate covers Codex reviews, every Codex comment edit, and manual dispatch', () => {
  const gate = fs.readFileSync(WORKFLOW_PATH, 'utf8');
  const v2 = fs.readFileSync(WORKFLOW_V2_PATH, 'utf8');
  const condition = jobCondition(gate);
  for (const event of ['pull_request', 'pull_request_review', 'workflow_dispatch']) {
    assert.ok(condition.includes(`github.event_name == '${event}'`), event);
  }
  // Codex's summary comment is edited in place on every review and carries no
  // 'Agent Review:' marker, so it is matched by author. The predicate carries
  // no action filter, so created, edited and deleted all qualify; the trigger
  // list is what admits `edited`.
  assert.equal(gate.split(CODEX_COMMENT_PREDICATE).length - 1, 2);
  assert.doesNotMatch(condition, /github\.event\.action/u);
  assert.match(gate, /issue_comment:\n {4}types: \[created, edited, deleted\]/u);
  // The v2 observer is observation-only and must not learn about Codex.
  assert.equal(v2.includes('chatgpt-codex-connector'), false);
  // A dispatch run joins the same per-PR evaluation group as the PR events.
  assert.ok(concurrencyGroup(gate).includes('|| inputs.pr_number }}'), 'dispatch group key');
});

function githubContains(haystack, needle) {
  // GitHub expression contains(): case-insensitive substring match; a
  // missing (null) haystack never matches.
  return typeof haystack === 'string' &&
    haystack.toLowerCase().includes(needle.toLowerCase());
}

test('the contains() filter is a superset of every accepted directive shape', () => {
  const accepted = [
    directive('PASS'),
    `${directive('BLOCKED')}\nConcrete English details`,
    directive('BLOCKED'),
    `\n${directive('PASS')}`,
    ` \t\n\t\n${directive('PASS')}\nExplanation`,
    `\uFEFF${directive('PASS')}\nExplanation`,
    ` \t\r\n\t\r\n${directive('PASS')}\r\nExplanation`,
  ];
  for (const body of accepted) {
    assert.equal(parseReviewDirectives(body, HEAD).currentHead, true, JSON.stringify(body));
  }
  const rejected = [
    `agent review: pass ${HEAD}`,
    `Agent Review PASS ${HEAD}`,
    'plain prose with no marker at all',
    `${directive('PASS')} `,
    directive('PASS', STALE),
  ];
  for (const body of rejected) {
    assert.equal(parseReviewDirectives(body, HEAD).currentHead, false, JSON.stringify(body));
  }
  // The load-bearing property: any body the parser accepts as a current-head
  // directive also passes the workflow's contains() filter, so no
  // verdict-setting comment can be skipped by the job `if:`.
  for (const body of [...accepted, ...rejected]) {
    if (parseReviewDirectives(body, HEAD).currentHead) {
      assert.equal(githubContains(body, 'Agent Review:'), true, JSON.stringify(body));
    }
  }
});

const V2_STEP = 'Evaluate comments with the exact trusted base parser';
const MAIN_HEAD = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const BASE_SHA = 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb';
const MOVED_SHA = 'cccccccccccccccccccccccccccccccccccccccc';

function v2Pull(head, base = BASE_SHA) {
  return {head: {sha: head}, base: {ref: 'main', sha: base}, updated_at: '2026-08-08T04:50:00Z'};
}

function v2Github({pullsGet, branchSha = BASE_SHA, comments = []}) {
  const statuses = [];
  const github = {
    paginate: async () => comments,
    rest: {
      pulls: {get: pullsGet},
      issues: {listComments() {}},
      repos: {
        getBranch: async () => ({data: {commit: {sha: branchSha}}}),
        getCommit: async () => ({data: {commit: {committer: {date: '2026-08-08T04:50:00Z'}}}}),
        getContent: async () => ({
          data: {
            type: 'file',
            encoding: 'base64',
            content: Buffer.from(
              fs.readFileSync(path.join(__dirname, 'agent-review-gate.js'), 'utf8'),
              'utf8',
            ).toString('base64'),
          },
        }),
        createCommitStatus: async (status) => statuses.push(status),
      },
    },
  };
  return {github, statuses};
}

function v2Context() {
  return {
    ...workflowContext(7),
    payload: {pull_request: {number: 2321, head: {sha: HEAD}}},
  };
}

test('v2 observer marks a stale base pending instead of leaving a stale success', async () => {
  const core = coreMock();
  const {github, statuses} = v2Github({
    pullsGet: async () => ({data: v2Pull(HEAD)}),
    branchSha: MOVED_SHA,
    comments: [comment(directive('PASS'))],
  });
  await runWorkflowScript(workflowScript(V2_STEP, WORKFLOW_V2_PATH), {
    github,
    context: v2Context(),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'pending');
  assert.equal(statuses[0].context, 'Agent Review Gate v2 observe');
  assert.equal(statuses[0].description, 'Not observed: PR base is not the current main head.');
  assert.deepEqual(core.failures, []);
  assert.equal(core.notices.length, 1);
  assert.match(core.notices[0], /base is not the current main head/u);
});

test('v2 observer marks a moved head or main pending instead of leaving a stale success', async () => {
  const core = coreMock();
  let pullsCalls = 0;
  const {github, statuses} = v2Github({
    pullsGet: async () => ({data: ++pullsCalls === 1 ? v2Pull(HEAD) : v2Pull(MOVED_SHA)}),
    comments: [comment(directive('PASS'))],
  });
  await runWorkflowScript(workflowScript(V2_STEP, WORKFLOW_V2_PATH), {
    github,
    context: v2Context(),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  assert.equal(pullsCalls, 2);
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'pending');
  assert.equal(statuses[0].context, 'Agent Review Gate v2 observe');
  assert.equal(statuses[0].description, 'Not observed: PR head or main moved during the observation.');
  assert.deepEqual(core.failures, []);
  assert.equal(core.notices.length, 1);
  assert.match(core.notices[0], /changed during review observation/u);
});

test('v2 observer still fails closed with a failure status on an API error', async () => {
  const core = coreMock();
  const {github, statuses} = v2Github({
    pullsGet: async () => { throw new Error('pulls API unavailable'); },
  });
  await runWorkflowScript(workflowScript(V2_STEP, WORKFLOW_V2_PATH), {
    github,
    context: v2Context(),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'failure');
  assert.equal(statuses[0].context, 'Agent Review Gate v2 observe');
  assert.equal(core.failures.length, 1);
});

test('AGENTS documents both exact head-bound directive forms', () => {
  const instructions = fs.readFileSync(AGENTS_PATH, 'utf8');
  assert.match(
    instructions,
    /^Agent Review: PASS <40-character-lowercase-head-sha>$/mu,
  );
  assert.match(
    instructions,
    /^Agent Review: BLOCKED <40-character-lowercase-head-sha>$/mu,
  );
  assert.match(instructions, /first non-blank line/u);
  assert.match(instructions, /BLOCKED directive must put concrete findings[\s\S]*subsequent lines/u);
});

test('known head survives getCommit failure and receives exact-head FAILURE', async () => {
  const resolverCore = coreMock();
  const resolverGithub = {
    rest: {
      pulls: {get: async () => ({data: {head: {sha: HEAD}}})},
      repos: {getCommit: async () => { throw new Error('commit API unavailable'); }},
    },
  };
  await assert.rejects(
    runWorkflowScript(workflowScript('Resolve pull request number'), {
      github: resolverGithub,
      context: {payload: {pull_request: {number: 2321}}, repo: {owner: 'rigplane', repo: 'rigplane-core'}},
      core: resolverCore,
      workspace: path.join(__dirname, '..', '..'),
    }),
    /commit API unavailable/,
  );
  assert.equal(resolverCore.outputs.head_sha, HEAD);

  // Even a Codex review of the head plus a PASS cannot turn an unknown commit
  // time into a green status.
  const core = coreMock();
  const {github, statuses} = gateGithub({
    comments: [comment(directive('PASS'))],
    reviews: [codexReview()],
  });
  await runWorkflowScript(
    updaterScript({committedAt: ''}),
    {
      github,
      context: workflowContext(2),
      core,
      workspace: path.join(__dirname, '..', '..'),
    },
  );
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'failure');
  assert.equal(statuses[0].description, 'Agent Review Gate evaluation failed closed.');
  assert.equal(core.failures.length, 1);
});

test('unresolved pull emits no guessed status target', async () => {
  const core = coreMock();
  const github = {
    rest: {
      pulls: {get: async () => { throw new Error('PR unavailable'); }},
      repos: {getCommit() { assert.fail('getCommit must not run'); }},
    },
  };
  await assert.rejects(
    runWorkflowScript(workflowScript('Resolve pull request number'), {
      github,
      context: {payload: {pull_request: {number: 2321}}, repo: {owner: 'rigplane', repo: 'rigplane-core'}},
      core,
      workspace: path.join(__dirname, '..', '..'),
    }),
    /PR unavailable/,
  );
  assert.deepEqual(core.outputs, {});
});

test('pagination and parser-load failures publish exact-head FAILURE and fail loudly', async () => {
  for (const scenario of ['pagination', 'parser']) {
    const core = coreMock();
    const {github, statuses} = gateGithub({
      overrides: scenario === 'pagination'
        ? {paginate: async () => { throw new Error('page 2 failed'); }}
        : {},
    });
    await runWorkflowScript(updaterScript(), {
      github,
      context: workflowContext(3),
      core,
      workspace: scenario === 'parser' ? '/definitely/missing' : path.join(__dirname, '..', '..'),
    });
    assert.equal(statuses.length, 1, scenario);
    assert.equal(statuses[0].sha, HEAD, scenario);
    assert.equal(statuses[0].state, 'failure', scenario);
    assert.equal(core.failures.length, 1, scenario);
  }
});

test('evaluator, builder, malformed output, and status API failures remain fail closed', async () => {
  const scenarios = [
    {
      name: 'evaluator',
      module: {
        evaluateReviewGate() { throw new Error('evaluator failed'); },
        buildCommitStatus,
      },
    },
    {
      name: 'builder',
      module: {
        evaluateReviewGate() {
          return {state: 'failure', passCount: 0, blockedCount: 0, malformedBlockedCount: 0};
        },
        buildCommitStatus() { throw new Error('builder failed'); },
      },
    },
    {
      name: 'malformed',
      module: {
        evaluateReviewGate() { return {state: 'success', passCount: 1, blockedCount: 0}; },
        buildCommitStatus,
      },
    },
  ];

  for (const scenario of scenarios) {
    const core = coreMock();
    // A Codex review of the head is present: a broken directive layer must
    // still deny, Codex cannot paper over it.
    const {github, statuses} = gateGithub({reviews: [codexReview()]});
    await runWorkflowScript(updaterScript(), {
      github,
      context: workflowContext(4),
      core,
      workspace: '/virtual/workspace',
      requireFn: realCodexRequire(scenario.module),
    });
    assert.equal(statuses.length, 1, scenario.name);
    assert.equal(statuses[0].sha, HEAD, scenario.name);
    assert.equal(statuses[0].state, 'failure', scenario.name);
    assert.equal(
      statuses[0].description,
      'Agent Review Gate evaluation failed closed.',
      scenario.name,
    );
  }

  const core = coreMock();
  const {github} = gateGithub({
    overrides: {
      rest: {
        issues: {listComments() {}, createComment: async () => {}},
        pulls: {listReviews() {}},
        repos: {createCommitStatus: async () => { throw new Error('status API unavailable'); }},
      },
    },
  });
  await assert.rejects(
    runWorkflowScript(updaterScript(), {
      github,
      context: workflowContext(5),
      core,
      workspace: path.join(__dirname, '..', '..'),
    }),
    /status API unavailable/,
  );
});

test('simulated head race remains bound to the originally resolved SHA', async () => {
  const newerHead = 'fedcba9876543210fedcba9876543210fedcba98';
  let liveHead = HEAD;
  const {github, statuses} = gateGithub({comments: [comment(directive('PASS'))]});
  const paginate = github.paginate;
  github.paginate = async (...args) => {
    liveHead = newerHead;
    return paginate(...args);
  };
  await runWorkflowScript(updaterScript({head: HEAD}), {
    github,
    context: workflowContext(6),
    core: coreMock(),
    workspace: path.join(__dirname, '..', '..'),
  });
  assert.equal(liveHead, newerHead);
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
});

test('strict first-line bootstrap token is compatible with the legacy substring gate', () => {
  const pass = directive('PASS');
  const blocked = `${directive('BLOCKED')}\nConcrete details`;
  assert.equal(pass.includes('Agent Review: PASS'), true);
  assert.equal(blocked.includes('Agent Review: PASS'), false);
});

// ---------------------------------------------------------------------------
// Codex-primary behaviour of the updater step (MOR-3616)
// ---------------------------------------------------------------------------

const CODEX_SUMMARY = (sha, status = 'completed') => ({
  user: CODEX_BOT,
  body: `Summary\n<!-- codex-review-state:v1 ${JSON.stringify({
    code_review: {commit_sha: sha, status},
  })} -->`,
  created_at: '2026-08-08T04:55:00Z',
  updated_at: '2026-08-08T04:55:00Z',
});

const CODEX_RECEIPT = (sha) => ({
  user: CODEX_BOT,
  body: `<!-- codex-review-receipt:v1 -->\n**Reviewed commit:** \`${sha}\``,
  created_at: '2026-08-08T04:55:00Z',
  updated_at: '2026-08-08T04:55:00Z',
});

test('Codex reviewed the exact head with no open threads: success without any PASS comment', async () => {
  const evidence = {
    review: {reviews: [codexReview()]},
    summary: {comments: [CODEX_SUMMARY(HEAD)]},
    receipt: {comments: [CODEX_RECEIPT(HEAD.slice(0, 10))]},
  };
  for (const [via, input] of Object.entries(evidence)) {
    const {statuses, core} = await runGate({
      ...input,
      threads: [codexThread('P1', {resolved: true}), codexThread('P3')],
    });
    assert.equal(statuses.length, 1, via);
    assert.equal(statuses[0].sha, HEAD, via);
    assert.equal(statuses[0].context, 'Agent Review Gate', via);
    assert.equal(statuses[0].state, 'success', via);
    assert.match(statuses[0].description, new RegExp(`Codex reviewed 1234567 \\(${via}\\)`, 'u'));
    assert.deepEqual(core.failures, [], via);
  }
});

test('Codex evidence for another commit, from another author, or not final proves nothing', async () => {
  const notEvidence = [
    {reviews: [codexReview({commit_id: STALE})]},
    {reviews: [codexReview({user: {login: 'someone', type: 'User'}})]},
    {reviews: [codexReview({user: {login: 'chatgpt-codex-connector[bot]', type: 'User'}})]},
    {reviews: [codexReview({state: 'PENDING'})]},
    {comments: [CODEX_SUMMARY(STALE)]},
    {comments: [CODEX_SUMMARY(HEAD, 'in_progress')]},
    {comments: [{...CODEX_SUMMARY(HEAD), user: {login: 'someone', type: 'User'}}]},
    {comments: [CODEX_RECEIPT(HEAD.slice(0, 9))]},
  ];
  for (const [index, input] of notEvidence.entries()) {
    const {statuses} = await runGate(input);
    assert.equal(statuses[0].state, 'failure', String(index));
    assert.match(statuses[0].description, /Waiting for Codex review/u, String(index));
  }
});

test('an unresolved Codex P1/P2 thread denies even with Codex review and a valid PASS', async () => {
  for (const priority of ['P1', 'P2']) {
    const withCodex = await runGate({
      reviews: [codexReview()],
      comments: [comment(directive('PASS'))],
      threads: [codexThread(priority)],
    });
    assert.equal(withCodex.statuses[0].state, 'failure', priority);
    assert.match(withCodex.statuses[0].description, new RegExp(`1 unresolved Codex P1/P2 thread.*${priority} src/rigplane/example.py:12`, 'u'));

    const backupOnly = await runGate({
      comments: [comment(directive('PASS'))],
      threads: [codexThread(priority)],
    });
    assert.equal(backupOnly.statuses[0].state, 'failure', priority);
    assert.match(backupOnly.statuses[0].description, /unresolved Codex/u);
  }

  // Resolved, P3, and non-Codex threads do not block.
  const ok = await runGate({
    reviews: [codexReview()],
    threads: [
      codexThread('P1', {resolved: true}),
      codexThread('P3'),
      codexThread('P1', {login: 'someone-else'}),
    ],
  });
  assert.equal(ok.statuses[0].state, 'success');
});

test('no Codex review of the head: a valid backup PASS succeeds', async () => {
  const {statuses, created} = await runGate({comments: [comment(directive('PASS'))]});
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].state, 'success');
  assert.match(statuses[0].description, /Backup cross-family PASS for 1234567/u);
  assert.deepEqual(created, []);

  // The existing strictness is untouched: untrusted, stale-SHA, and pre-commit PASS do not count.
  for (const bad of [
    comment(directive('PASS'), {author_association: 'NONE'}),
    comment(directive('PASS', STALE)),
    comment(directive('PASS'), {updated_at: '2026-08-08T04:49:59Z'}),
    comment(directive('PASS'), {minimized: {reason: 'outdated'}}),
  ]) {
    const denied = await runGate({comments: [bad]});
    assert.equal(denied.statuses[0].state, 'failure');
    assert.match(denied.statuses[0].description, /Waiting for Codex review/u);
  }
});

test('no Codex review and no PASS: failure that says it is waiting for Codex', async () => {
  const {statuses, core} = await runGate({comments: [comment('looks fine to me')]});
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'failure');
  assert.match(statuses[0].description, /^Waiting for Codex review of 1234567; backup review after 20 min\.$/u);
  assert.deepEqual(core.failures, []);
  assert.equal(core.notices.length, 1);
});

test('a BLOCKED directive wins over a Codex review and over a PASS', async () => {
  const blocked = comment(`${directive('BLOCKED')}\nConcrete blocker 7`);
  const cases = [
    {reviews: [codexReview()], comments: [blocked]},
    {reviews: [codexReview()], comments: [comment(directive('PASS')), blocked]},
    {comments: [comment(directive('PASS')), blocked]},
  ];
  for (const [index, input] of cases.entries()) {
    const {statuses} = await runGate(input);
    assert.equal(statuses[0].state, 'failure', String(index));
    assert.equal(
      statuses[0].description,
      'A trusted exact-head Agent Review BLOCKED directive is active.',
      String(index),
    );
  }

  const malformed = await runGate({
    reviews: [codexReview()],
    comments: [comment(directive('BLOCKED'))],
  });
  assert.equal(malformed.statuses[0].state, 'failure');
  assert.equal(
    malformed.statuses[0].description,
    'A trusted exact-head Agent Review BLOCKED directive is malformed.',
  );

  // A BLOCKED for another head or from an untrusted author is not binding.
  const notBinding = await runGate({
    reviews: [codexReview()],
    comments: [
      comment(`${directive('BLOCKED', STALE)}\nOld blocker`),
      comment(`${directive('BLOCKED')}\nOutsider`, {author_association: 'NONE'}),
    ],
  });
  assert.equal(notBinding.statuses[0].state, 'success');
});

test('a push asks Codex exactly once; drafts, reviewed heads, and other events do not', async () => {
  for (const action of ['synchronize', 'reopened']) {
    const {created, statuses} = await runGate({action});
    assert.equal(created.length, 1, action);
    assert.deepEqual(created[0], {
      owner: 'rigplane',
      repo: 'rigplane-core',
      issue_number: 2321,
      body: '@codex review',
    });
    assert.equal(statuses.length, 1, action);
    assert.equal(statuses[0].state, 'failure', action);
  }

  const none = [
    ['draft', {action: 'synchronize', draft: 'true'}],
    ['already reviewed head', {action: 'synchronize', reviews: [codexReview()]}],
    [
      'already requested for this head',
      {
        action: 'synchronize',
        comments: [comment('@codex review', {author_association: 'NONE', created_at: '2026-08-08T04:51:00Z'})],
      },
    ],
    ['opened', {action: 'opened'}],
    ['ready_for_review', {action: 'ready_for_review'}],
    ['review submitted', {action: 'submitted'}],
    ['issue comment', {action: 'created'}],
    ['dispatch', {action: undefined}],
  ];
  for (const [name, input] of none) {
    const {created} = await runGate(input);
    assert.deepEqual(created, [], name);
  }

  // A request made before the head commit does not count for the new head.
  const stale = await runGate({
    action: 'synchronize',
    comments: [comment('@codex review', {created_at: '2026-08-08T04:49:00Z'})],
  });
  assert.equal(stale.created.length, 1);
});

async function runBrokenGate(breakIt, {action = undefined} = {}) {
  // Codex reviewed and a PASS exists: only a fail-closed rule can deny.
  const fake = gateGithub({
    comments: [comment(directive('PASS'))],
    reviews: [codexReview()],
  });
  breakIt(fake.github);
  const core = coreMock();
  await runWorkflowScript(updaterScript(), {
    github: fake.github,
    context: workflowContext(21, action),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  return {...fake, core};
}

test('GraphQL, review-list, and request failures fail closed with a loud run failure', async () => {
  const breakers = {
    graphql: (github) => {
      github.graphql = async () => { throw new Error('GraphQL unavailable'); };
    },
    'graphql null repository': (github) => {
      github.graphql = async () => ({repository: null});
    },
    'review list': (github) => {
      const paginate = github.paginate;
      github.paginate = async (fn, ...rest) => {
        if (fn === github.rest.pulls.listReviews) {
          throw new Error('reviews unavailable');
        }
        return paginate(fn, ...rest);
      };
    },
  };
  for (const [name, breakIt] of Object.entries(breakers)) {
    const {statuses, core} = await runBrokenGate(breakIt);
    assert.equal(statuses.length, 1, name);
    assert.equal(statuses[0].sha, HEAD, name);
    assert.equal(statuses[0].state, 'failure', name);
    assert.equal(statuses[0].description, 'Agent Review Gate evaluation failed closed.', name);
    assert.equal(core.failures.length, 1, name);
    assert.equal(core.errors.length, 1, name);
  }

  // The request comment failing is an evaluation failure, not a silent skip.
  const noReview = gateGithub({comments: [comment(directive('PASS'))]});
  noReview.github.rest.issues.createComment = async () => { throw new Error('comment API down'); };
  const core = coreMock();
  await runWorkflowScript(updaterScript(), {
    github: noReview.github,
    context: workflowContext(22, 'synchronize'),
    core,
    workspace: path.join(__dirname, '..', '..'),
  });
  assert.equal(noReview.statuses.length, 1);
  assert.equal(noReview.statuses[0].state, 'failure');
  assert.equal(noReview.statuses[0].description, 'Agent Review Gate evaluation failed closed.');
  assert.equal(core.failures.length, 1);
});

test('review threads are read through every GraphQL page', async () => {
  const pages = [
    {pageInfo: {hasNextPage: true, endCursor: 'c1'}, nodes: [codexThread('P1', {resolved: true})]},
    {pageInfo: {hasNextPage: false, endCursor: null}, nodes: [codexThread('P2')]},
  ];
  const seen = [];
  const {statuses} = await runGate({
    reviews: [codexReview()],
    overrides: {
      graphql: async (query, variables) => {
        seen.push(variables.after);
        return {repository: {pullRequest: {reviewThreads: pages.shift()}}};
      },
    },
  });
  assert.deepEqual(seen, [null, 'c1']);
  assert.equal(statuses[0].state, 'failure');
  assert.match(statuses[0].description, /P2/u);
});

test('the resolver reads the PR number from a manual dispatch and reports the draft flag', async () => {
  for (const draft of [true, false, undefined]) {
    const core = coreMock();
    const github = {
      rest: {
        pulls: {get: async ({pull_number}) => {
          assert.equal(pull_number, 2321);
          return {data: {head: {sha: HEAD}, draft, updated_at: '2026-08-08T04:50:00Z'}};
        }},
        repos: {getCommit: async () => ({data: {commit: {committer: {date: '2026-08-08T04:50:00Z'}}}})},
      },
    };
    await runWorkflowScript(workflowScript('Resolve pull request number'), {
      github,
      context: {payload: {inputs: {pr_number: '2321'}}, repo: {owner: 'rigplane', repo: 'rigplane-core'}},
      core,
      workspace: path.join(__dirname, '..', '..'),
    });
    assert.equal(core.outputs.number, '2321');
    assert.equal(core.outputs.head_sha, HEAD);
    assert.equal(core.outputs.draft, String(draft === true));
  }
});

test('REST call budget matches the number the workflow comment states', async () => {
  const workflow = fs.readFileSync(WORKFLOW_PATH, 'utf8');
  assert.match(workflow, /Per-run REST calls, counted from the steps below: 3 fixed calls/u);
  assert.match(workflow, /\(pulls\.get, repos\.getCommit, repos\.createCommitStatus\)/u);
  assert.match(workflow, /at most one issues\.createComment/u);

  const restCalls = [];
  const core = coreMock();
  const resolverGithub = {
    rest: {
      pulls: {get: async () => { restCalls.push('pulls.get'); return {data: {head: {sha: HEAD}}}; }},
      repos: {getCommit: async () => {
        restCalls.push('repos.getCommit');
        return {data: {commit: {committer: {date: '2026-08-08T04:50:00Z'}}}};
      }},
    },
  };
  await runWorkflowScript(workflowScript('Resolve pull request number'), {
    github: resolverGithub,
    context: {payload: {pull_request: {number: 2321}}, repo: {owner: 'rigplane', repo: 'rigplane-core'}},
    core,
    workspace: path.join(__dirname, '..', '..'),
  });

  // Worst case: a synchronize on a non-draft PR that Codex has not reviewed.
  const run = await runGate({action: 'synchronize'});
  const fixed = [...restCalls, ...run.restCalls.filter((name) => name !== 'issues.createComment')];
  assert.deepEqual(fixed.sort(), ['pulls.get', 'repos.createCommitStatus', 'repos.getCommit']);
  assert.equal(run.restCalls.filter((name) => name === 'issues.createComment').length, 1);
  assert.equal(run.graphqlCalls.length, 1);
});

test('AGENTS documents the Codex-primary gate rules', () => {
  const instructions = fs.readFileSync(AGENTS_PATH, 'utf8');
  assert.match(instructions, /^### Primary reviewer: Codex$/mu);
  const section = /^### Primary reviewer: Codex$([\s\S]*?)(?=^#{1,3} )/mu.exec(instructions)[1];
  assert.match(section, /chatgpt-codex-connector\[bot\]/u);
  assert.match(section, /exact head SHA/u);
  assert.match(section, /P1\/P2/u);
  assert.match(section, /@codex review/u);
  assert.match(section, /does not review drafts/u);
  assert.match(section, /20 minutes/u);
  assert.match(section, /family differs from the builder/u);
  assert.match(section, /gh workflow run agent-review-gate\.yml -f pr_number=N/u);
});
