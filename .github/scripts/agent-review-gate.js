'use strict';

const TRUSTED_ASSOCIATIONS = new Set(['OWNER', 'MEMBER', 'COLLABORATOR']);
const DIRECTIVE_PATTERN = /^Agent Review: (PASS|BLOCKED) ([0-9a-f]{40})$/u;
const ASCII_BLANK_PATTERN = /^[\x20\x09]*$/u;
const BLOCKER_DETAILS_PATTERN = /[\p{L}\p{N}]/u;
const HEAD_SHA_PATTERN = /^[0-9a-f]{40}$/u;

function noDirective() {
  return {
    pass: false,
    blocked: false,
    malformedBlocked: false,
    currentHead: false,
  };
}

function normalizeBody(body) {
  if (typeof body !== 'string') {
    return null;
  }

  let normalized = body;
  if (normalized.startsWith('\uFEFF')) {
    normalized = normalized.slice(1);
  }
  normalized = normalized.replace(/\r\n/g, '\n');
  if (normalized.includes('\r')) {
    return null;
  }
  return normalized;
}

function parseReviewDirectives(body, headSha) {
  if (!HEAD_SHA_PATTERN.test(headSha)) {
    return noDirective();
  }

  const normalized = normalizeBody(body);
  if (normalized === null) {
    return noDirective();
  }

  const lines = normalized.split('\n');
  const directiveIndex = lines.findIndex(
    (line) => !ASCII_BLANK_PATTERN.test(line),
  );
  if (directiveIndex === -1) {
    return noDirective();
  }

  const match = DIRECTIVE_PATTERN.exec(lines[directiveIndex]);
  if (match === null || match[2] !== headSha) {
    return noDirective();
  }

  if (match[1] === 'PASS') {
    return {
      pass: true,
      blocked: false,
      malformedBlocked: false,
      currentHead: true,
    };
  }

  const details = lines.slice(directiveIndex + 1).join('\n');
  const hasDetails = BLOCKER_DETAILS_PATTERN.test(details);
  return {
    pass: false,
    blocked: hasDetails,
    malformedBlocked: !hasDetails,
    currentHead: true,
  };
}

function effectiveCommentDate(comment) {
  const value = comment.updated_at ?? comment.created_at;
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? null : date;
}

function evaluateReviewGate({comments, headSha, committedAt}) {
  if (
    !Array.isArray(comments) ||
    !(committedAt instanceof Date) ||
    Number.isNaN(committedAt.valueOf())
  ) {
    return {
      state: 'failure',
      passCount: 0,
      blockedCount: 0,
      malformedBlockedCount: 0,
    };
  }

  let passCount = 0;
  let blockedCount = 0;
  let malformedBlockedCount = 0;
  for (const comment of comments) {
    if (
      comment === null ||
      typeof comment !== 'object' ||
      comment.minimized === true ||
      !TRUSTED_ASSOCIATIONS.has(comment.author_association)
    ) {
      continue;
    }

    const parsed = parseReviewDirectives(comment.body, headSha);
    if (!parsed.currentHead) {
      continue;
    }
    if (parsed.blocked) {
      blockedCount += 1;
    }
    if (parsed.malformedBlocked) {
      malformedBlockedCount += 1;
    }

    const effectiveDate = effectiveCommentDate(comment);
    if (parsed.pass && effectiveDate !== null && effectiveDate >= committedAt) {
      passCount += 1;
    }
  }

  return {
    state:
      blockedCount === 0 &&
      malformedBlockedCount === 0 &&
      passCount > 0
        ? 'success'
        : 'failure',
    passCount,
    blockedCount,
    malformedBlockedCount,
  };
}

function buildCommitStatus({
  owner,
  repo,
  headSha,
  targetUrl,
  result,
  evaluationFailed = false,
}) {
  const validResult =
    result !== null &&
    typeof result === 'object' &&
    (result.state === 'success' || result.state === 'failure') &&
    Number.isInteger(result.passCount) &&
    result.passCount >= 0 &&
    Number.isInteger(result.blockedCount) &&
    result.blockedCount >= 0 &&
    Number.isInteger(result.malformedBlockedCount) &&
    result.malformedBlockedCount >= 0 &&
    (result.state !== 'success' ||
      (result.passCount > 0 &&
        result.blockedCount === 0 &&
        result.malformedBlockedCount === 0));
  const failedClosed = evaluationFailed || !validResult;
  let description =
    'Missing trusted standalone exact-head Agent Review PASS directive.';
  if (failedClosed) {
    description = 'Agent Review Gate evaluation failed closed.';
  } else if (result.malformedBlockedCount > 0) {
    description =
      'A trusted exact-head Agent Review BLOCKED directive is malformed.';
  } else if (result.blockedCount > 0) {
    description =
      'A trusted exact-head Agent Review BLOCKED directive is active.';
  } else if (result.state === 'success') {
    description =
      'Trusted standalone Agent Review PASS is bound to the current head.';
  }

  return {
    owner,
    repo,
    sha: headSha,
    state: failedClosed ? 'failure' : result.state,
    context: 'Agent Review Gate',
    description,
    target_url: targetUrl,
  };
}

module.exports = {
  buildCommitStatus,
  evaluateReviewGate,
  parseReviewDirectives,
};
