'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');

const ROOT = path.resolve(__dirname, '..', '..');
const TARGET_ROOT = process.env.GATE_TARGET_ROOT
  ? path.resolve(process.env.GATE_TARGET_ROOT)
  : ROOT;
const routePolicy = require(path.join(ROOT, '.github/scripts/base-gate-policy-v1.js'));
const observer = require(path.join(
  ROOT, '.github/scripts/quick-v2-metadata-policy-v1.js',
));

function readTarget(relative) {
  return fs.readFileSync(path.join(TARGET_ROOT, relative), 'utf8');
}

function topologyErrors({quick, review, migration}) {
  const errors = [];
  if (
    !quick.includes('\n  pull_request_target:\n') ||
    !quick.includes('\n  workflow_run:\n') ||
    quick.includes('\n  pull_request:\n')
  ) {
    errors.push('quick observer must use hosted lifecycle and workflow-run events');
  }
  for (const event of [
    'opened', 'reopened', 'synchronize', 'ready_for_review',
    'converted_to_draft', 'edited',
  ]) {
    if (!quick.includes(event)) {
      errors.push(`quick observer is missing lifecycle event ${event}`);
    }
  }
  for (const required of [
    'workflows: ["Tests (quick)"]',
    'types: [completed]',
    'permissions: {}',
    'actions: read',
    'checks: read',
    'contents: read',
    'pull-requests: read',
    'statuses: write',
    'Read-only legacy quick metadata observer',
    'Exact-head metadata observation publisher',
    '.github/scripts/quick-v2-metadata-policy-v1.js',
  ]) {
    if (!quick.includes(required)) {
      errors.push(`quick observer is missing ${required}`);
    }
  }
  const statusWrites = quick.match(/statuses: write/gu) ?? [];
  const pinnedActions = quick.match(
    /actions\/github-script@ed597411d8f924073f98dfc5c65a23a2325f34cd/gu,
  ) ?? [];
  if (statusWrites.length !== 1 || pinnedActions.length !== 2) {
    errors.push('only the publisher may write statuses and both actions must be pinned');
  }
  for (const forbidden of [
    'self-hosted',
    'actions/checkout',
    'actions/upload-artifact',
    'actions/download-artifact',
    '/logs',
    '/artifacts',
    '/caches',
    'secrets.',
    'github.workflow_sha',
  ]) {
    if (quick.includes(forbidden)) {
      errors.push(`quick observer contains forbidden product/candidate surface: ${forbidden}`);
    }
  }
  if (
    !review.includes('\n  pull_request_target:\n') ||
    review.includes('\n  pull_request:\n') ||
    !review.includes("context: 'Agent Review Gate v2 observe'")
  ) {
    errors.push('review observation contract changed unexpectedly');
  }
  for (const requiredText of [
    'must never be configured as required checks',
    'metadata-only',
    'zero self-hosted',
    'route_job_concordance',
    'terminal_binding_coverage',
    'distinct from GitHub Actions app ID `15368`',
    '"strict": true',
  ]) {
    if (!migration.includes(requiredText)) {
      errors.push(`migration contract is missing: ${requiredText}`);
    }
  }
  return errors;
}

test('trusted route policy rejects incomplete and injected path metadata', () => {
  const docs = routePolicy.classifyPullFiles(
    [
      {filename: 'docs/guide.md'},
      {filename: 'frontend/README.MD'},
      {filename: 'mkdocs.yml'},
    ],
    3,
  );
  assert.deepEqual(docs, {core: false, frontend: false, ci: false, docs: true});
  for (const filename of [
    '../README.md',
    'docs/../src/radio.py',
  ]) {
    assert.throws(
      () => routePolicy.classifyPullFiles([{filename}], 1),
      undefined,
      filename,
    );
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
  const sources = {
    quick: readTarget('.github/workflows/quick-v2.yml'),
    review: readTarget('.github/workflows/agent-review-gate-v2.yml'),
    migration: readTarget('docs/internals/base-controlled-required-gates.md'),
  };
  assert.deepEqual(topologyErrors(sources), []);
  assert.equal(observer.LEGACY_WORKFLOW.id, 270532868);
  assert.equal(observer.LEGACY_WORKFLOW.path, '.github/workflows/quick.yml');
  assert.equal(observer.OBSERVATION_CONTEXT, 'quick-v2-observe');
});

test('topology contract catches self-hosted, artifact, permission, and pin regressions', () => {
  const sources = {
    quick: readTarget('.github/workflows/quick-v2.yml'),
    review: readTarget('.github/workflows/agent-review-gate-v2.yml'),
    migration: readTarget('docs/internals/base-controlled-required-gates.md'),
  };
  const mutants = [
    {...sources, quick: sources.quick.replace('runs-on: ubuntu-latest', 'runs-on: [self-hosted, linux, build]')},
    {...sources, quick: sources.quick.replace('permissions: {}', 'permissions:\n  statuses: write')},
    {...sources, quick: sources.quick.replace('actions: read', 'actions: read\n      statuses: write')},
    {...sources, quick: sources.quick.replace('with:\n          script:', 'uses: actions/upload-artifact@v4\n        with:\n          script:')},
    {...sources, quick: sources.quick.replace('ed597411d8f924073f98dfc5c65a23a2325f34cd', 'v8')},
    {...sources, quick: sources.quick.replace('workflow_run:', 'pull_request:')},
  ];
  for (const mutant of mutants) {
    assert.notDeepEqual(topologyErrors(mutant), []);
  }
});

test('edited lifecycle events are no-ops unless base metadata changed', () => {
  assert.equal(observer.relevantLifecycle({action: 'opened'}), true);
  assert.equal(observer.relevantLifecycle({action: 'converted_to_draft'}), true);
  assert.equal(observer.relevantLifecycle({
    action: 'edited', changes: {base: {ref: {from: 'release'}}},
  }), true);
  assert.equal(observer.relevantLifecycle({
    action: 'edited', changes: {title: {from: 'old'}},
  }), false);
  assert.equal(observer.relevantLifecycle({
    action: 'edited', changes: {body: {from: 'old'}},
  }), false);
});

test('terminal topology refuses skipped success and injected or incomplete jobs', () => {
  const run = {conclusion: 'success', run_attempt: 2};
  const routes = {core: true, frontend: false, ci: false, docs: false};
  const valid = [
    {
      name: 'classify', status: 'completed', conclusion: 'success',
      run_attempt: 2, runner_name: 'GitHub Actions 1', labels: ['ubuntu-latest'],
    },
    {
      name: 'quick', status: 'completed', conclusion: 'success',
      run_attempt: 2, runner_name: 'mm-build-core-1',
      labels: ['self-hosted', 'linux', 'build'],
    },
  ];
  assert.deepEqual(observer.evaluateJobTopology(run, valid, routes, false), {
    ok: true, code: 'terminal_match', routeConcordance: true,
  });
  const skipped = structuredClone(valid);
  skipped[1].conclusion = 'skipped';
  skipped[1].runner_name = null;
  assert.equal(
    observer.evaluateJobTopology(run, skipped, routes, false).code,
    'skipped_substantive',
  );
  const injected = [...valid, {...valid[1], name: 'quick-shadow'}];
  assert.equal(
    observer.evaluateJobTopology(run, injected, routes, false).code,
    'route_mismatch',
  );
  assert.equal(
    observer.evaluateJobTopology({...run, conclusion: 'cancelled'}, valid, routes, false).code,
    'terminal_failure',
  );
  assert.equal(
    observer.evaluateJobTopology(run, valid, {...routes, docs: true}, false).code,
    'route_mismatch',
  );
});

test('exact pull binding rejects stale base/head and identifies forks', () => {
  const base = 'b'.repeat(40);
  const head = 'a'.repeat(40);
  const pull = {
    state: 'open',
    base: {
      ref: 'main', sha: base,
      repo: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName},
    },
    head: {
      sha: head,
      repo: {id: observer.REPOSITORY.id, full_name: observer.REPOSITORY.fullName},
    },
  };
  assert.deepEqual(observer.bindPull(pull, base, head), {
    headSha: head, baseSha: base, sameRepository: true,
  });
  assert.throws(() => observer.bindPull(pull, 'c'.repeat(40), head), /live main/u);
  assert.throws(() => observer.bindPull(pull, base, 'd'.repeat(40)), /no longer current/u);
  const fork = structuredClone(pull);
  fork.head.repo = {id: 99, full_name: 'outside/fork'};
  assert.equal(observer.bindPull(fork, base, head).sameRepository, false);
});

function treeGithub(finalEntry, options = {}) {
  const root = '1'.repeat(40);
  const githubTree = '2'.repeat(40);
  const workflowsTree = '3'.repeat(40);
  const responses = {
    [root]: [{path: '.github', mode: '040000', type: 'tree', sha: githubTree}],
    [githubTree]: [{path: 'workflows', mode: '040000', type: 'tree', sha: workflowsTree}],
    [workflowsTree]: [finalEntry],
  };
  return {
    request: async (route, args) => {
      if (route.includes('/git/commits/')) {
        return {data: {tree: {sha: root}}};
      }
      if (route.includes('/git/trees/')) {
        const entries = responses[args.tree_sha] ?? [];
        return {
          data: {
            truncated: options.truncated === args.tree_sha,
            tree: options.duplicate === args.tree_sha ? [...entries, ...entries] : entries,
          },
        };
      }
      throw new Error(`unexpected route ${route}`);
    },
  };
}

test('tree walk accepts only complete 040000 parents and a 100644 blob', async () => {
  const blob = '4'.repeat(40);
  const validEntry = {path: 'quick.yml', mode: '100644', type: 'blob', sha: blob};
  const result = await observer.getTreeEntry(
    treeGithub(validEntry), 'rigplane', 'rigplane-core',
    'a'.repeat(40), '.github/workflows/quick.yml',
  );
  assert.deepEqual(result, {mode: '100644', type: 'blob', sha: blob});

  for (const [mode, type] of [
    ['100755', 'blob'],
    ['120000', 'blob'],
    ['160000', 'commit'],
  ]) {
    await assert.rejects(
      observer.getTreeEntry(
        treeGithub({...validEntry, mode, type}), 'rigplane', 'rigplane-core',
        'a'.repeat(40), '.github/workflows/quick.yml',
      ),
      /regular non-executable/u,
    );
  }
  await assert.rejects(
    observer.getTreeEntry(
      treeGithub(validEntry, {duplicate: '3'.repeat(40)}),
      'rigplane', 'rigplane-core', 'a'.repeat(40), '.github/workflows/quick.yml',
    ),
    /ambiguous/u,
  );
  await assert.rejects(
    observer.getTreeEntry(
      treeGithub(validEntry, {truncated: '2'.repeat(40)}),
      'rigplane', 'rigplane-core', 'a'.repeat(40), '.github/workflows/quick.yml',
    ),
    /incomplete/u,
  );
  await assert.rejects(
    observer.getTreeEntry(
      treeGithub(validEntry), 'rigplane', 'rigplane-core',
      'a'.repeat(40), '.github/../quick.yml',
    ),
    /path is invalid/u,
  );
});
