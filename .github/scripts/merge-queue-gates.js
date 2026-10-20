'use strict';

// Merge-queue counterparts of the two PR-level required gates. The merge_group
// event carries no PR comments or body, so both gates are re-derived through
// the API for the single PR named by the queue ref (the queue is configured
// with a merge group of one PR). Both fail closed.

const {evaluate: evaluateFreeze} = require('./freeze-gate');

const AGENT_REVIEW_CONTEXT = 'Agent Review Gate';

// head_ref is the short branch name; the full ref is tolerated too.
const QUEUE_REF = /^(?:refs\/heads\/)?gh-readonly-queue\/(.+)\/pr-(\d+)-([0-9a-f]{40})$/u;

function parseQueueRef(ref) {
  const match = QUEUE_REF.exec(typeof ref === 'string' ? ref : '');
  if (!match) {
    return null;
  }
  return {base: match[1], number: Number(match[2]), queuedHeadSha: match[3]};
}

// pull: {number, head: {sha}}; statuses: commit statuses of the PR head.
function evaluateAgentReviewGate({queued, pull, statuses}) {
  if (pull.head.sha !== queued.queuedHeadSha) {
    return {
      ok: false,
      reason: `PR #${pull.number} head ${pull.head.sha.slice(0, 7)} differs from the queued head ${queued.queuedHeadSha.slice(0, 7)}.`,
    };
  }
  const gate = (Array.isArray(statuses) ? statuses : []).find(
    (status) => status.context === AGENT_REVIEW_CONTEXT,
  );
  if (gate?.state !== 'success') {
    return {
      ok: false,
      reason: `PR #${pull.number} head has no passing ${AGENT_REVIEW_CONTEXT} (${gate?.state ?? 'missing'}).`,
    };
  }
  return {
    ok: true,
    reason: `PR #${pull.number} head ${pull.head.sha.slice(0, 7)} passed the ${AGENT_REVIEW_CONTEXT}.`,
  };
}

function evaluateFreezeForPull(pull) {
  return evaluateFreeze({
    title: pull.title,
    body: pull.body,
    labels: (pull.labels ?? []).map((label) => label.name),
    draft: pull.draft,
  });
}

// Shared by both workflow jobs. `gate` is 'agent-review' or 'freeze'.
async function run({github, context, core, gate, ref, sha}) {
  const {owner, repo} = context.repo;
  const url = `${context.serverUrl}/${owner}/${repo}/actions/runs/${context.runId}`;
  const publish = (state, description) => github.rest.repos.createCommitStatus({
    owner, repo, sha, state, context: AGENT_REVIEW_CONTEXT, description: description.slice(0, 140), target_url: url,
  });
  const fail = async (reason) => {
    if (gate === 'agent-review') {
      await publish('failure', reason);
    }
    core.setFailed(reason);
  };
  const queued = parseQueueRef(ref);
  if (!queued) {
    await fail(`Merge group ref "${ref}" does not name a single PR.`);
    return;
  }
  let verdict;
  try {
    const {data: pull} = await github.rest.pulls.get({owner, repo, pull_number: queued.number});
    if (gate === 'freeze') {
      verdict = evaluateFreezeForPull(pull);
    } else {
      const {data: combined} = await github.rest.repos.getCombinedStatusForRef({
        owner, repo, ref: pull.head.sha, per_page: 100,
      });
      verdict = evaluateAgentReviewGate({queued, pull, statuses: combined.statuses});
    }
  } catch (error) {
    await fail(`${gate} evaluation failed closed: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }
  if (!verdict.ok) {
    await fail(verdict.reason);
    return;
  }
  if (gate === 'agent-review') {
    await publish('success', verdict.reason);
  }
  core.info(verdict.reason);
}

module.exports = {parseQueueRef, evaluateAgentReviewGate, evaluateFreezeForPull, run};
