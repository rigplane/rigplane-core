'use strict';

// Local-verdict gate (owner directive 2026-10-21, supersedes the Codex-primary gate of MOR-3616).
// Pure functions plus one paginated GraphQL reader; the workflow scripts in
// rigplane-pro and rigplane-core call them. Keep the two copies identical.
//
// PASS needs one thing: the repository's exact-head `Agent Review: PASS` directive,
// posted by the lane script (pro-gate.sh) after a LOCAL review of that exact head
// (Codex CLI on the owner's subscription; Spark when Codex built the change). Nobody
// waits for the GitHub Codex bot: its review and its threads are advisory and only
// appear in the status text. A BLOCKED directive for the head still wins.

const CODEX_LOGIN = 'chatgpt-codex-connector';
// Kept for old PR labels: the label no longer changes the verdict (the lane script picks the reviewer family).
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
  // The latest Codex review of this head decides (the API lists them oldest
  // first): a dismissal invalidates the summary and receipt comments too, until
  // Codex posts a fresh, non-dismissed review of the head.
  let latest = null;
  for (const review of reviews) {
    if (
      isCodexUser(review.user) &&
      review.commit_id === headSha &&
      review.state !== 'PENDING'
    ) {
      latest = review.state;
    }
  }
  if (latest === 'DISMISSED') {
    return {reviewed: false, via: null};
  }
  if (latest !== null) {
    return {reviewed: true, via: 'review'};
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
// parser. A BLOCKED directive wins over everything. `codex` and `unresolved`
// (the GitHub Codex bot) are advisory: they never change the state.
function buildCodexVerdict({codex, unresolved, backup, headSha}) {
  const short = headSha.slice(0, 7);
  if (backup.blocked) {
    return {state: 'failure', description: backup.description};
  }
  if (backup.pass) {
    const open = unresolved.length;
    const note = open > 0
      ? `Codex bot advisory: ${open} open P0-P2 thread(s).`
      : codex.reviewed ? 'Codex bot reviewed it.' : 'Codex bot advisory.';
    return {
      state: 'success',
      description: `Local review PASS for ${short}. ${note}`.slice(0, 140),
    };
  }
  return {
    state: 'failure',
    description: `Waiting for the local review PASS for ${short} (pro-gate.sh posts it).`,
  };
}

// Trusted commenters only (the lane script and the coordinator post with the owner's account, whose
// association is OWNER); a PR author without write access cannot satisfy the gate by commenting.
const TRUSTED_ASSOCIATIONS = new Set(['OWNER', 'MEMBER', 'COLLABORATOR']);

// True when a trusted, non-minimized comment carries `Agent Review: <VERDICT> <full head sha>`.
// PASS: the directive must be the first non-blank line, exactly (a quoted or fenced example, a line below
// prose, an extended sha token or another head do not count) and the comment must be newer than the head
// commit (`notBefore`), so it cannot outlive a force-push.
// BLOCKED: any unquoted, unfenced line equal to the directive counts (a blocker written below the
// explanation stays binding), and its age does not matter; a new head retires it by changing the sha.
function hasDirective({comments, verdict, headSha, notBefore = null}) {
  if (!SHA_PATTERN.test(headSha)) {
    return false;
  }
  const wanted = `Agent Review: ${verdict} ${headSha}`;
  return comments.some((comment) => {
    if (comment.minimized || !TRUSTED_ASSOCIATIONS.has(comment.author_association) || typeof comment.body !== 'string') {
      return false;
    }
    const lines = comment.body.replace(/\r\n/gu, '\n').split('\n');
    if (verdict === 'PASS') {
      if (lines.find((line) => line.trim() !== '') !== wanted) {
        return false;
      }
    } else {
      // Markdown fences: an opener of >= 3 backticks or tildes is closed only by a bare line of the
      // same character and at least the same length; nothing inside counts.
      let fence = null;
      let found = false;
      for (const line of lines) {
        const marker = /^ {0,3}(`{3,}|~{3,})(.*)$/u.exec(line);
        if (fence === null) {
          if (marker !== null) {
            fence = {char: marker[1][0], length: marker[1].length};
          } else if (line === wanted) {
            found = true;
          }
        } else if (marker !== null && marker[1][0] === fence.char && marker[1].length >= fence.length && marker[2].trim() === '') {
          fence = null;
        }
      }
      if (!found) {
        return false;
      }
    }
    if (notBefore === null) {
      return true;
    }
    const stamp = new Date(comment.updated_at ?? comment.created_at);
    return !Number.isNaN(stamp.valueOf()) && stamp >= notBefore;
  });
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
  buildCodexVerdict,
  codexReviewedHead,
  hasDirective,
  isCodexUser,
  readReviewThreads,
  unresolvedCodexFindings,
};
