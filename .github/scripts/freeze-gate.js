'use strict';

// Freeze gate: machine-checks the stabilization-freeze PR contract declared
// in AGENTS.md ("Stabilization freeze (from 2026-10-13)"). The workflow
// .github/workflows/freeze-gate.yml runs this script on every pull_request
// event that can change the verdict, including draft state transitions.

const ALLOWED_CLASSES = [
  'fix',
  'regression-test',
  'characterization-test',
  'testability',
  'observability',
  'ci',
  'docs',
  'release',
  'chore',
];

const EXCEPTION_LABEL = 'freeze-exception';
const FEATURE_TITLE_PATTERN = /^feat(\(|!|:)/;
const CHANGE_CLASS_PATTERN = new RegExp(
  `^Change class: (${ALLOWED_CLASSES.join('|')})\\s*$`,
  'm',
);

function evaluate({title, body, labels, draft}) {
  // Drafts are evaluated the same way as ready PRs; the flag is accepted so
  // callers can pass the full event context without special-casing.
  void draft;

  const labelNames = Array.isArray(labels) ? labels : [];
  if (labelNames.includes(EXCEPTION_LABEL)) {
    return {
      ok: true,
      reason: `freeze-exception label present; owner exception applies.`,
    };
  }

  const prTitle = typeof title === 'string' ? title : '';
  if (FEATURE_TITLE_PATTERN.test(prTitle)) {
    return {
      ok: false,
      reason: `feature PR without freeze-exception label: title "${prTitle}" matches /^feat(\\(|!|:)/. New features are prohibited during the stabilization freeze without an owner exception.`,
    };
  }

  const prBody = typeof body === 'string' ? body : '';
  if (CHANGE_CLASS_PATTERN.test(prBody)) {
    return {
      ok: true,
      reason: `PR body declares an allowed freeze change class.`,
    };
  }

  return {
    ok: false,
    reason: `PR body must contain a line "Change class: <class>" with exactly one of: ${ALLOWED_CLASSES.join(', ')}.`,
  };
}

function evaluateFromEnvironment(env) {
  let labels = [];
  const rawLabels = env.PR_LABELS;
  if (typeof rawLabels === 'string' && rawLabels.length > 0) {
    try {
      const parsed = JSON.parse(rawLabels);
      if (Array.isArray(parsed)) {
        labels = parsed.filter((name) => typeof name === 'string');
      }
    } catch {
      labels = [];
    }
  }

  return evaluate({
    title: env.PR_TITLE,
    body: env.PR_BODY,
    labels,
    draft: env.PR_DRAFT === 'true',
  });
}

module.exports = {
  ALLOWED_CLASSES,
  EXCEPTION_LABEL,
  evaluate,
  evaluateFromEnvironment,
};

if (require.main === module) {
  const result = evaluateFromEnvironment(process.env);
  const stream = result.ok ? process.stdout : process.stderr;
  stream.write(`${result.ok ? 'OK' : 'FAIL'}: ${result.reason}\n`);
  if (!result.ok) {
    process.exit(1);
  }
}
