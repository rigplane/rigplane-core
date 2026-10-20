const assert = require('node:assert/strict');
const test = require('node:test');

const {evaluate, evaluateFromEnvironment} = require('./freeze-gate');

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

// --- Class-line anchor pinning ---

test('Change class value with trailing characters fails', () => {
  // Pins the \s*$ anchor: "fixes" must not match the "fix" alternative.
  const result = evaluate(pullRequest({body: 'Change class: fixes\n'}));
  assert.equal(result.ok, false);
  assert.match(result.reason, /Change class: <class>/);
});

test('Change class line with a leading prefix fails', () => {
  // Pins the ^ anchor: the class declaration must start the line.
  const result = evaluate(
    pullRequest({body: 'prefix Change class: fix\n'}),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /Change class: <class>/);
});

test('Change class line embedded in a sentence fails', () => {
  // Pins the ^ anchor together with the literal "Change class: " start.
  const result = evaluate(
    pullRequest({body: 'Note: Change class: fix\n'}),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /Change class: <class>/);
});

// --- Title-form pinning ---

test('feat(scope): title without freeze-exception label fails', () => {
  // Pins the "(" alternative in /^feat(\(|!|:)/.
  const result = evaluate(
    pullRequest({
      title: 'feat(dsp): x',
      body: 'Change class: fix\n',
    }),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /without freeze-exception label/);
});

test('feat! title without freeze-exception label fails', () => {
  // Pins the "!" alternative in /^feat(\(|!|:)/.
  const result = evaluate(
    pullRequest({
      title: 'feat!: x',
      body: 'Change class: fix\n',
    }),
  );
  assert.equal(result.ok, false);
  assert.match(result.reason, /without freeze-exception label/);
});

test('feature: title with a valid change class passes', () => {
  // Pins that the title pattern does not match a bare "feat" prefix.
  const result = evaluate(
    pullRequest({title: 'feature: tidy docs'}),
  );
  assert.equal(result.ok, true, result.reason);
});

test('feat with space before colon passes with a valid change class', () => {
  // "feat : x" is not a conventional-commits feature title.
  const result = evaluate(
    pullRequest({title: 'feat : x'}),
  );
  assert.equal(result.ok, true, result.reason);
});

// --- CLI entry (evaluateFromEnvironment) ---

test('environment entry passes with freeze-exception label', () => {
  const result = evaluateFromEnvironment({
    PR_TITLE: 'feat: add waterfall spectrum view',
    PR_BODY: 'Freeze exception: owner ruling 2026-10-12\n',
    PR_LABELS: '["freeze-exception"]',
    PR_DRAFT: 'false',
  });
  assert.equal(result.ok, true, result.reason);
});

test('environment entry fails closed on unparseable labels', () => {
  // Invalid JSON labels must degrade to no labels: a feat: title is blocked.
  const result = evaluateFromEnvironment({
    PR_TITLE: 'feat: add waterfall spectrum view',
    PR_BODY: 'Change class: fix\n',
    PR_LABELS: 'not json',
    PR_DRAFT: 'false',
  });
  assert.equal(result.ok, false);
  assert.match(result.reason, /without freeze-exception label/);
});
