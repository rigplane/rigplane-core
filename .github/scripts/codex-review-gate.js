'use strict';

// Codex-primary review verdict for the `Agent Review Gate` status (MOR-3616).
// Pure functions plus one paginated GraphQL reader; the workflow scripts in
// rigplane-pro and rigplane-core call them. Keep the two copies identical.
//
// PASS needs one of:
//   1. Codex (chatgpt-codex-connector[bot]) reviewed the exact head SHA, or
//   2. the repository's existing exact-head `Agent Review: PASS` directive from
//      the cross-family backup reviewer (GLM / Spark) -- never silent: a person
//      or lane script posts it only after the Codex window closed.
// and in both cases no Codex P0/P1/P2 review thread may be unresolved.

const CODEX_LOGIN = 'chatgpt-codex-connector';
const WAIT_MINUTES = 20;
// Set on a PR built by a GPT/Codex-family agent: Codex is then not an independent reviewer.
const GPT_BUILT_LABEL = 'built-by-gpt';
const SHA_PATTERN = /^[0-9a-f]{40}$/u;
const BADGE_PATTERN = /!\[P([012]) Badge\]/u;
const STATE_PATTERN = /<!-- codex-review-state:v1 (\{.*?\}) -->/su;
const RECEIPT_PATTERN =
  /<!-- codex-review-receipt:v1 -->[\s\S]*?\*\*Reviewed commit:\*\*\s*`([0-9a-f]{10,40})`/u;

// REST users are `chatgpt-codex-connector[bot]` / type Bot; GraphQL authors
// drop the `[bot]` suffix. `author_association` is NONE for the app, so the
// login and the Bot type are the identity.
function isCodexUser(user) {
  if (user === null || typeof user !== 'object') {
    return false;
  }
  const login = String(user.login ?? '').replace(/\[bot\]$/u, '');
  const type = user.type ?? user.__typename;
  return login === CODEX_LOGIN && type === 'Bot';
}

function codexReviewedHead({reviews, comments, headSha}) {
  if (!SHA_PATTERN.test(headSha)) {
    return {reviewed: false, via: null};
  }
  for (const review of reviews) {
    if (
      isCodexUser(review.user) &&
      review.commit_id === headSha &&
      review.state !== 'PENDING' &&
      review.state !== 'DISMISSED'
    ) {
      return {reviewed: true, via: 'review'};
    }
  }
  for (const comment of comments) {
    if (!isCodexUser(comment.user) || typeof comment.body !== 'string') {
      continue;
    }
    const state = STATE_PATTERN.exec(comment.body);
    if (state !== null) {
      try {
        const code = JSON.parse(state[1]).code_review ?? {};
        if (code.commit_sha === headSha && code.status === 'completed') {
          return {reviewed: true, via: 'summary'};
        }
      } catch {
        // A malformed state block proves nothing; keep looking.
      }
    }
    const receipt = RECEIPT_PATTERN.exec(comment.body);
    if (receipt !== null && headSha.startsWith(receipt[1])) {
      return {reviewed: true, via: 'receipt'};
    }
  }
  return {reviewed: false, via: null};
}

// Threads come from GraphQL `reviewThreads`. A thread counts when Codex wrote
// its first comment with a P0/P1/P2 badge and it is not resolved, outdated or not.
function unresolvedCodexFindings(threads) {
  const open = [];
  for (const thread of threads) {
    const first = thread.comments?.nodes?.[0];
    if (thread.isResolved || !first || !isCodexUser(first.author)) {
      continue;
    }
    const badge = BADGE_PATTERN.exec(first.body ?? '');
    if (badge !== null) {
      open.push({
        priority: `P${badge[1]}`,
        path: thread.path ?? '-',
        line: thread.line ?? thread.originalLine ?? '-',
        url: first.url ?? '',
      });
    }
  }
  return open;
}

// `backup`: {pass, blocked, description} from the repository's own directive
// parser. A BLOCKED directive wins over everything, as it does today.
function buildCodexVerdict({codex, unresolved, backup, headSha, gptBuilt = false}) {
  const short = headSha.slice(0, 7);
  const codexCounts = codex.reviewed && !gptBuilt;
  if (backup.blocked) {
    return {state: 'failure', description: backup.description};
  }
  if (unresolved.length > 0) {
    const list = unresolved
      .slice(0, 3)
      .map((f) => `${f.priority} ${f.path}:${f.line}`)
      .join(', ');
    return {
      state: 'failure',
      description: `${unresolved.length} unresolved Codex P0-P2 thread(s): ${list}`.slice(0, 140),
    };
  }
  if (codexCounts) {
    return {
      state: 'success',
      description: `Codex reviewed ${short} (${codex.via}); no open P1/P2 threads.`,
    };
  }
  if (backup.pass) {
    return {
      state: 'success',
      description: `Backup cross-family PASS for ${short}; Codex did not review it.`,
    };
  }
  if (gptBuilt) {
    return {
      state: 'failure',
      description: `Label ${GPT_BUILT_LABEL}: Codex is not independent; backup PASS needed for ${short}.`,
    };
  }
  return {
    state: 'failure',
    description:
      `Waiting for Codex review of ${short}; backup review after ${WAIT_MINUTES} min.`,
  };
}

const THREADS_QUERY = `query($owner: String!, $repo: String!, $number: Int!, $after: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved path line originalLine
          comments(first: 1) { nodes { url body author { login __typename } } }
        }
      }
    }
  }
}`;

async function readReviewThreads({github, owner, repo, number}) {
  const threads = [];
  let after = null;
  for (;;) {
    const data = await github.graphql(THREADS_QUERY, {owner, repo, number, after});
    const page = data.repository.pullRequest.reviewThreads;
    threads.push(...page.nodes);
    if (!page.pageInfo.hasNextPage) {
      return threads;
    }
    after = page.pageInfo.endCursor;
  }
}

module.exports = {
  CODEX_LOGIN,
  GPT_BUILT_LABEL,
  WAIT_MINUTES,
  buildCodexVerdict,
  codexReviewedHead,
  isCodexUser,
  readReviewThreads,
  unresolvedCodexFindings,
};
