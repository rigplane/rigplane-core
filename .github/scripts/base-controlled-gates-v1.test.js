'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');

const CONTROL_ROOT = path.resolve(__dirname, '..', '..');
const TARGET_ROOT = process.env.GATE_TARGET_ROOT
  ? path.resolve(process.env.GATE_TARGET_ROOT)
  : CONTROL_ROOT;
const policy = require(path.join(CONTROL_ROOT, '.github/scripts/base-gate-policy-v1.js'));

function readTarget(relative) {
  return fs.readFileSync(path.join(TARGET_ROOT, relative), 'utf8');
}

function topologyErrors({quick, review, worker, migration}) {
  const errors = [];
  if (!quick.includes('\n  pull_request_target:\n') || quick.includes('\n  pull_request:\n')) {
    errors.push('quick controller must use only pull_request_target');
  }
  if (!review.includes('\n  pull_request_target:\n') || review.includes('\n  pull_request:\n')) {
    errors.push('review controller must use pull_request_target for PR events');
  }
  for (const [name, source] of [['quick', quick], ['review', review]]) {
    if (source.includes('github.workflow_sha') || source.includes('secrets.')) {
      errors.push(`${name} controller may not use candidate workflow refs or secrets`);
    }
  }
  if (!quick.includes("path: '.github/scripts/base-gate-policy-v1.js'")) {
    errors.push('quick controller must fetch versioned policy');
  }
  if (!quick.includes('ref: baseSha')) {
    errors.push('quick controller must fetch policy at exact base SHA');
  }
  if (!review.includes("path: '.github/scripts/agent-review-gate.js', ref: baseSha")) {
    errors.push('review controller must fetch parser at exact base SHA');
  }
  if (!quick.includes("context: 'quick-v2-observe'")) {
    errors.push('quick controller must publish only the observation context');
  }
  if (!review.includes("context: 'Agent Review Gate v2 observe'")) {
    errors.push('review controller must publish only the observation context');
  }
  if (!quick.includes('name: Read-only quick v2 worker')) {
    errors.push('quick controller must retain the isolated worker');
  }
  const workerStart = quick.indexOf('  worker:\n');
  const publishStart = quick.indexOf('  publish:\n');
  const workerJob = quick.slice(workerStart, publishStart);
  if (
    workerStart === -1 ||
    publishStart === -1 ||
    !workerJob.includes('permissions:\n      contents: read') ||
    workerJob.includes('statuses: write') ||
    workerJob.includes('pull-requests: write') ||
    workerJob.includes('issues: write')
  ) {
    errors.push('worker must retain its read-only job token');
  }
  const nonPersistedCredentials = workerJob.match(/persist-credentials: false/gu) ?? [];
  if (
    nonPersistedCredentials.length !== 2 ||
    workerJob.includes('persist-credentials: true')
  ) {
    errors.push('both control and candidate checkouts must not persist credentials');
  }
  if (!workerJob.includes('rev-list --parents -n 1 HEAD')) {
    errors.push('worker must bind the merge commit to exact parents');
  }
  if (
    !quick.includes("initialPull.head.repo?.full_name === `${owner}/${repo}`") ||
    !workerJob.includes("needs.admit.outputs.same_repo == 'true'") ||
    (quick.match(/currentBase\.commit\.sha !== baseSha/gu) ?? []).length !== 2
  ) {
    errors.push('quick observation must withhold forks and reject stale main');
  }
  if (!review.includes('currentBase.commit.sha !== baseSha')) {
    errors.push('review observation must reject stale main');
  }
  if (!worker.includes('.github/scripts/verify-immutable-controls-v1.sh')) {
    errors.push('worker must invoke the Git-object immutable-control verifier');
  }
  for (const requiredText of [
    'must never be configured as required checks',
    'distinct from GitHub Actions app ID `15368`',
    '"strict": true',
    'switch protection back to the still-running legacy contexts **before**',
  ]) {
    if (!migration.includes(requiredText)) {
      errors.push(`migration contract is missing: ${requiredText}`);
    }
  }
  return errors;
}

test('trusted policy rejects adversarial paths and fails unknown roots into core', () => {
  const docs = policy.classifyPullFiles(
    [
      {filename: 'docs/guide.md'},
      {filename: 'frontend/README.MD'},
      {filename: 'mkdocs.yml'},
    ],
    3,
  );
  assert.deepEqual(docs, {core: false, frontend: false, ci: false, docs: true});

  for (const filename of [
    'docs',
    '.claude',
    'frontend/README.rſt',
    'frontend/README.md\n',
    'frontend/README.rst\r\n',
  ]) {
    const result = policy.classifyPullFiles([{filename}], 1);
    assert.equal(result.docs, false, filename);
    assert.equal(result.core || result.frontend || result.ci, true, filename);
  }
  assert.deepEqual(policy.classifyPullFiles([{filename: 'CODEOWNERS'}], 1), {
    core: true,
    frontend: false,
    ci: false,
    docs: false,
  });
  assert.deepEqual(
    policy.classifyPullFiles([
      {filename: 'docs/new.md', previous_filename: 'src/rigplane/radio.py'},
    ], 1),
    {core: true, frontend: false, ci: false, docs: false},
  );
});

test('trusted policy fails closed on incomplete, capped, and invalid metadata', () => {
  assert.throws(() => policy.classifyPullFiles([{filename: 'docs/a.md'}], 2));
  assert.throws(() => policy.classifyPullFiles([{filename: 'docs/a.md'}], 3000));
  assert.throws(() => policy.classifyPullFiles([{filename: '../README.md'}], 1));
  assert.throws(() => policy.assertSha('refs/pull/1/merge', 'head'));
});

test('candidate v2 topology retains base control and isolated permissions', () => {
  const sources = {
    quick: readTarget('.github/workflows/quick-v2.yml'),
    review: readTarget('.github/workflows/agent-review-gate-v2.yml'),
    worker: readTarget('.github/scripts/quick-v2-worker-v1.sh'),
    migration: readTarget('docs/internals/base-controlled-required-gates.md'),
  };
  assert.deepEqual(topologyErrors(sources), []);
});

test('topology contract catches self-green workflow and helper mutations', () => {
  const sources = {
    quick: readTarget('.github/workflows/quick-v2.yml'),
    review: readTarget('.github/workflows/agent-review-gate-v2.yml'),
    worker: readTarget('.github/scripts/quick-v2-worker-v1.sh'),
    migration: readTarget('docs/internals/base-controlled-required-gates.md'),
  };
  const workerStart = sources.quick.indexOf('  worker:\n');
  const writeEnabledWorker =
    sources.quick.slice(0, workerStart) +
    sources.quick.slice(workerStart).replace(
      '      contents: read\n',
      '      contents: read\n      statuses: write\n',
    );
  const mutants = [
    {...sources, quick: sources.quick.replace('pull_request_target:', 'pull_request:')},
    {...sources, quick: sources.quick.replace('ref: baseSha', 'ref: github.workflow_sha')},
    {...sources, quick: sources.quick.replace('persist-credentials: false', 'persist-credentials: true')},
    {...sources, quick: writeEnabledWorker},
    {...sources, review: sources.review.replace('ref: baseSha', 'ref: github.workflow_sha')},
    {...sources, worker: sources.worker.replace('verify-immutable-controls-v1.sh', 'true')},
    {...sources, quick: sources.quick.replace("needs.admit.outputs.same_repo == 'true'", 'true')},
    {...sources, quick: sources.quick.replace('currentBase.commit.sha !== baseSha', 'false')},
    {...sources, migration: sources.migration.replace('"strict": true', '"strict": false')},
  ];
  for (const mutant of mutants) {
    assert.notDeepEqual(topologyErrors(mutant), []);
  }
});

test('immutable-control verifier rejects chmod and symlink mutations', () => {
  const sandbox = fs.mkdtempSync(path.join(os.tmpdir(), 'rigplane-gate-mode-'));
  const trusted = path.join(sandbox, 'trusted');
  const target = path.join(sandbox, 'target');
  const critical = [
    '.github/scripts/agent-review-gate.js',
    '.github/scripts/base-controlled-gates-v1.test.js',
    '.github/scripts/base-gate-policy-v1.js',
    '.github/scripts/quick-v2-worker-v1.sh',
    '.github/scripts/verify-immutable-controls-v1.sh',
    '.github/workflows/agent-review-gate-v2.yml',
    '.github/workflows/quick-v2.yml',
  ];
  const git = (repo, ...args) => childProcess.execFileSync(
    'git', ['-C', repo, ...args], {stdio: 'ignore'},
  );
  const commit = (repo, message) => {
    git(repo, 'add', '-A');
    git(repo, '-c', 'user.name=Gate Contract', '-c', 'user.email=gate@example.invalid', 'commit', '-m', message);
  };
  try {
    for (const repo of [trusted, target]) {
      fs.mkdirSync(repo, {recursive: true});
      git(repo, 'init');
      for (const relative of critical) {
        const destination = path.join(repo, relative);
        fs.mkdirSync(path.dirname(destination), {recursive: true});
        fs.copyFileSync(path.join(CONTROL_ROOT, relative), destination);
        fs.chmodSync(destination, relative.endsWith('.sh') ? 0o755 : 0o644);
      }
      commit(repo, 'trusted controls');
    }
    const verifier = path.join(CONTROL_ROOT, '.github/scripts/verify-immutable-controls-v1.sh');
    childProcess.execFileSync(verifier, [trusted, target], {stdio: 'ignore'});

    const parser = path.join(target, '.github/scripts/agent-review-gate.js');
    fs.chmodSync(parser, 0o755);
    commit(target, 'chmod mutation');
    assert.throws(() => childProcess.execFileSync(verifier, [trusted, target], {stdio: 'ignore'}));

    git(target, 'checkout', 'HEAD^', '--', '.');
    commit(target, 'restore controls');
    fs.unlinkSync(parser);
    fs.symlinkSync('base-gate-policy-v1.js', parser);
    commit(target, 'symlink mutation');
    assert.throws(() => childProcess.execFileSync(verifier, [trusted, target], {stdio: 'ignore'}));
  } finally {
    fs.rmSync(sandbox, {recursive: true, force: true});
  }
});
