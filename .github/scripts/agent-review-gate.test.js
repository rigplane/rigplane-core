const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const {
  buildCommitStatus,
  evaluateReviewGate,
  parseReviewDirectives,
} = require('./agent-review-gate');

const HEAD = '1234567890abcdef1234567890abcdef12345678';
const STALE = 'abcdef1234567890abcdef1234567890abcdef12';
const COMMITTED_AT = new Date('2026-08-08T04:50:00Z');
const WORKFLOW_PATH = path.join(__dirname, '..', 'workflows', 'agent-review-gate.yml');

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

function workflowScript(stepName) {
  const workflow = fs.readFileSync(WORKFLOW_PATH, 'utf8');
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

function updaterScript({number = '2321', head = HEAD, committedAt = '2026-08-08T04:50:00Z'} = {}) {
  return workflowScript('Update required Agent Review Gate status')
    .replace("Number('${{ steps.pr.outputs.number }}')", `Number('${number}')`)
    .replace("'${{ steps.pr.outputs.head_sha }}'", `'${head}'`)
    .replace("'${{ steps.pr.outputs.committed_at }}'", `'${committedAt}'`);
}

function workflowContext(runId = 1) {
  return {
    repo: {owner: 'rigplane', repo: 'rigplane-core'},
    runId,
    serverUrl: 'https://github.test',
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
  assert.match(workflow, /const \{data: pull\} = await github\.rest\.pulls\.get/);
  assert.match(workflow, /core\.setOutput\('head_sha', pull\.head\.sha\)/);
  assert.match(workflow, /ref: \$\{\{ github\.workflow_sha \}\}/);
  assert.match(workflow, /persist-credentials: false/);
  assert.match(workflow, /github\.paginate\(github\.rest\.issues\.listComments/);
  assert.doesNotMatch(workflow, /head_sha\s*=\s*['"]\$\{\{\s*github\.sha/);
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

  const statuses = [];
  const github = {
    paginate: async () => [],
    rest: {
      issues: {listComments() {}},
      repos: {createCommitStatus: async (status) => statuses.push(status)},
    },
  };
  await runWorkflowScript(
    updaterScript({committedAt: ''}),
    {
      github,
      context: workflowContext(2),
      core: coreMock(),
      workspace: path.join(__dirname, '..', '..'),
    },
  );
  assert.equal(statuses.length, 1);
  assert.equal(statuses[0].sha, HEAD);
  assert.equal(statuses[0].state, 'failure');
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
    const statuses = [];
    const core = coreMock();
    const github = {
      paginate: scenario === 'pagination'
        ? async () => { throw new Error('page 2 failed'); }
        : async () => [],
      rest: {
        issues: {listComments() {}},
        repos: {createCommitStatus: async (status) => statuses.push(status)},
      },
    };
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
    const statuses = [];
    const core = coreMock();
    const github = {
      paginate: async () => [],
      rest: {
        issues: {listComments() {}},
        repos: {createCommitStatus: async (status) => statuses.push(status)},
      },
    };
    await runWorkflowScript(updaterScript(), {
      github,
      context: workflowContext(4),
      core,
      workspace: '/virtual/workspace',
      requireFn: () => scenario.module,
    });
    assert.equal(statuses.length, 1, scenario.name);
    assert.equal(statuses[0].sha, HEAD, scenario.name);
    assert.equal(statuses[0].state, 'failure', scenario.name);
  }

  const core = coreMock();
  const github = {
    paginate: async () => [],
    rest: {
      issues: {listComments() {}},
      repos: {createCommitStatus: async () => { throw new Error('status API unavailable'); }},
    },
  };
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
  const statuses = [];
  let liveHead = HEAD;
  const github = {
    paginate: async () => {
      liveHead = newerHead;
      return [comment(directive('PASS'))];
    },
    rest: {
      issues: {listComments() {}},
      repos: {createCommitStatus: async (status) => statuses.push(status)},
    },
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
