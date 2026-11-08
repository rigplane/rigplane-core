'use strict';

// Merge-queue counterpart of the PR-level Agent Review Gate. The merge_group
// event carries no PR comments, so the gate is re-derived through the API for
// the single PR named by the queue ref (the queue must be configured with a
// merge group of one PR). It fails closed.

const AGENT_REVIEW_CONTEXT = 'Agent Review Gate';

// head_ref is the short branch name; the full ref is tolerated too.
// The base part may not itself contain a pr-<n>-<sha> segment, so a ref naming several PRs is rejected.
const QUEUE_REF = /^(?:refs\/heads\/)?gh-readonly-queue\/((?:(?!pr-\d+-[0-9a-f]{40}).)+)\/pr-(\d+)-([0-9a-f]{40})$/u;

function parseQueueRef(ref) {
  const match = QUEUE_REF.exec(typeof ref === 'string' ? ref : '');
  if (!match) {
    return null;
  }
  // match[3] is the main commit the group was built on, not the PR head.
  return {base: match[1], number: Number(match[2])};
}

// pull: {number, head: {sha}}; statuses: commit statuses of the PR head.
function evaluateAgentReviewGate({pull, statuses}) {
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

async function run({github, context, core, ref, sha}) {
  const {owner, repo} = context.repo;
  const url = `${context.serverUrl}/${owner}/${repo}/actions/runs/${context.runId}`;
  const publish = (state, description) => github.rest.repos.createCommitStatus({
    owner, repo, sha, state, context: AGENT_REVIEW_CONTEXT, description: description.slice(0, 140), target_url: url,
  });
  const fail = async (reason) => {
    await publish('failure', reason);
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
    const {data: combined} = await github.rest.repos.getCombinedStatusForRef({
      owner, repo, ref: pull.head.sha, per_page: 100,
    });
    verdict = evaluateAgentReviewGate({pull, statuses: combined.statuses});
  } catch (error) {
    await fail(`agent-review evaluation failed closed: ${error instanceof Error ? error.message : String(error)}`);
    return;
  }
  if (!verdict.ok) {
    await fail(verdict.reason);
    return;
  }
  await publish('success', verdict.reason);
  core.info(verdict.reason);
}

module.exports = {parseQueueRef, evaluateAgentReviewGate, run};
