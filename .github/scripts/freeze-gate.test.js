const assert = require('node:assert/strict');
const test = require('node:test');

const {evaluate} = require('./freeze-gate');

function pullRequest(overrides = {}) {
  return {
    title: 'fix: correct channel scan off-by-one',
    body: 'Change class: fix\n\nLinear: MOR-0000\n',
    labels: [],
    draft: false,
    ...overrides,
  };
}

test('valid change class passes', () => {
  const result = evaluate(pullRequest());
  assert.equal(result.ok, true, result.reason);
});

test('missing Change class line fails', () => {
  const result = evaluate(pullRequest({body: 'No class declared here.\n'}));
  assert.equal(result.ok, false);
  assert.match(result.reason, /Change class: <class>/);
  assert.match(result.reason, /regression-test/);
});

test('invalid change class fails', () => {
  const result = evaluate(
    pullRequest({body: 'Change class: feature\n'}),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /fix, regression-test/);
});

test('feat: title without freeze-exception label fails', () => {
  const result = evaluate(
    pullRequest({
      title: 'feat: add waterfall spectrum view',
      body: 'Change class: ci\n',
    }),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /without freeze-exception label/);
});

test('feat(scope) title with freeze-exception label passes', () => {
  const result = evaluate(
    pullRequest({
      title: 'feat(dsp): add noise blanker',
      body: 'Freeze exception: owner ruling 2026-10-12\n',
      labels: ['freeze-exception'],
    }),
  );
  assert.equal(result.ok, true, result.reason);
});

test('draft PR with valid change class passes', () => {
  const result = evaluate(pullRequest({draft: true}));
  assert.equal(result.ok, true, result.reason);
});

test('Change class line later in a multiline body passes', () => {
  const result = evaluate(
    pullRequest({
      body: 'Some context first.\n\nChange class: observability\n\nMore detail.\n',
    }),
  );
  assert.equal(result.ok, true, result.reason);
});
