'use strict';
const REPOSITORY = Object.freeze({ id: 1166377435, owner: 'rigplane', repo: 'rigplane-core', fullName: 'rigplane/rigplane-core', });
const LEGACY_WORKFLOW = Object.freeze({ id: 270532868, name: 'Tests (quick)', path: '.github/workflows/quick.yml', actionsAppId: 15368, });
const OBSERVATION_CONTEXT = 'quick-v2-observe';
const OBSERVER_PATH = '.github/scripts/quick-v2-metadata-policy-v1.js';
const POLICY_PATH = '.github/scripts/base-gate-policy-v1.js';
const CONTROL_PATHS = Object.freeze([ LEGACY_WORKFLOW.path, '.github/scripts/classify-quick-paths.py', POLICY_PATH, '.github/workflows/quick-v2.yml', OBSERVER_PATH, ]);
const SHA_PATTERN = /^[0-9a-f]{40}$/u;
const LIFECYCLE_ACTIONS = new Set([ 'opened', 'reopened', 'synchronize', 'ready_for_review', 'converted_to_draft', 'edited', ]);
class ObservationError extends Error { constructor(code, message, options = {}) { super(message);
    this.name = 'ObservationError';
    this.code = code;
    this.publish = options.publish ?? false;
    this.targetSha = options.targetSha ?? null;
  }
}
function assertSha(value, label) { if (typeof value !== 'string' || !SHA_PATTERN.test(value)) { throw new ObservationError('invalid_binding', `${label} is not an exact commit SHA`);
  }
  return value;
}
function relevantLifecycle(payload) { const action = payload?.action;
  if (!LIFECYCLE_ACTIONS.has(action)) { return false;
  }
  if (action !== 'edited') { return true;
  }
  const changes = payload?.changes;
  return changes !== null && typeof changes === 'object' && Object.prototype.hasOwnProperty.call(changes, 'base');
}
function stableAssessment(value) { const ordered = (item) => { if (Array.isArray(item)) { return item.map(ordered);
    }
    if (item !== null && typeof item === 'object') { return Object.fromEntries( Object.keys(item).sort().map((key) => [key, ordered(item[key])]), );
    }
    return item;
  };
  return JSON.stringify(ordered(value));
}
function statusDescription(code) { const descriptions = { admitted: 'Metadata/policy observation: waiting for canonical legacy quick.', draft: 'Metadata/policy observation: draft; substantive quick is not expected.', terminal_match: 'Metadata/policy observation: canonical legacy quick matched.', fork: 'Unknown: fork execution is outside this metadata observation.', stale_base: 'Unknown: pull request base is not the live main head.', stale_head: 'Unknown: legacy quick belongs to a stale pull request head.', stale_run: 'Unknown: legacy quick is not the latest run and attempt.', ambiguous_pull: 'Unknown: legacy quick has no unique current pull request.', control_diff: 'Unknown: candidate changes trusted quick observation controls.', route_mismatch: 'Unknown: trusted route and observed legacy jobs disagree.', skipped_substantive: 'Unknown: legacy quick succeeded while its worker was skipped.', terminal_failure: 'Metadata observation: canonical legacy quick did not succeed.', invalid_binding: 'Unknown: canonical legacy quick metadata did not bind.', };
  return descriptions[code] ?? descriptions.invalid_binding;
}
function assessment({kind, code, state = null, publish = false, targetSha = null, pullNumber = null, baseSha = null, headSha = null, runId = null, runAttempt = null, routes = null, metrics = null}) { return { kind, code, state, publish, targetSha, pullNumber, baseSha, headSha, runId, runAttempt, routes, description: statusDescription(code), metrics: metrics ?? { route_job_concordance: null, terminal_binding_coverage: false, v2_self_hosted_minutes: 0, false_success_publications: 0, stale_head_publications: 0, }, };
}
async function getTreeEntry(github, owner, repo, commitSha, repositoryPath) { assertSha(commitSha, 'tree commit');
  const parts = repositoryPath.split('/');
  if ( parts.length === 0 || parts.some((part) => part.length === 0 || part === '.' || part === '..') || repositoryPath.startsWith('/') || repositoryPath.includes('\\') || repositoryPath.includes('\0') ) { throw new ObservationError('invalid_binding', 'trusted control path is invalid');
  }
  const commitResponse = await github.request( 'GET /repos/{owner}/{repo}/git/commits/{commit_sha}', {owner, repo, commit_sha: commitSha}, );
  let treeSha = assertSha(commitResponse.data.tree?.sha, 'root tree');
  for (let index = 0; index < parts.length; index += 1) { const treeResponse = await github.request( 'GET /repos/{owner}/{repo}/git/trees/{tree_sha}', {owner, repo, tree_sha: treeSha}, );
    if (treeResponse.data.truncated === true || !Array.isArray(treeResponse.data.tree)) { throw new ObservationError('invalid_binding', 'trusted control tree is incomplete');
    }
    const matches = treeResponse.data.tree.filter((entry) => entry.path === parts[index]);
    if (matches.length !== 1) { throw new ObservationError('invalid_binding', 'trusted control tree path is ambiguous');
    }
    const entry = matches[0];
    const final = index === parts.length - 1;
    if (!final) { if (entry.mode !== '040000' || entry.type !== 'tree') { throw new ObservationError('invalid_binding', 'trusted control parent is not a tree');
      }
      treeSha = assertSha(entry.sha, 'nested tree');
      continue;
    }
    if (entry.mode !== '100644' || entry.type !== 'blob') { throw new ObservationError('invalid_binding', 'trusted control is not a regular non-executable file');
    }
    return {mode: entry.mode, type: entry.type, sha: assertSha(entry.sha, 'control blob')};
  }
  throw new ObservationError('invalid_binding', 'trusted control path did not resolve');
}
async function readBaseFile(github, owner, repo, baseSha, repositoryPath) { const entry = await getTreeEntry(github, owner, repo, baseSha, repositoryPath);
  const blobResponse = await github.request( 'GET /repos/{owner}/{repo}/git/blobs/{file_sha}', {owner, repo, file_sha: entry.sha}, );
  const blob = blobResponse.data;
  if ( blob.encoding !== 'base64' || typeof blob.content !== 'string' || !Number.isSafeInteger(blob.size) || blob.size <= 0 || blob.size > 250000 ) { throw new ObservationError('invalid_binding', 'trusted base control blob is invalid');
  }
  return Buffer.from(blob.content, 'base64').toString('utf8');
}
async function assertControlsUnchanged(github, owner, repo, baseSha, headSha) { for (const repositoryPath of CONTROL_PATHS) { const baseEntry = await getTreeEntry(github, owner, repo, baseSha, repositoryPath);
    let headEntry;
    try { headEntry = await getTreeEntry(github, owner, repo, headSha, repositoryPath);
    } catch { throw new ObservationError('control_diff', 'candidate control entry is invalid', { publish: true, targetSha: headSha, });
    }
    if (stableAssessment(baseEntry) !== stableAssessment(headEntry)) { throw new ObservationError('control_diff', 'candidate changes trusted controls', { publish: true, targetSha: headSha, });
    }
  }
}
async function loadRoutePolicy(github, owner, repo, baseSha) { const source = await readBaseFile(github, owner, repo, baseSha, POLICY_PATH);
  const policyModule = {exports: {}};
  new Function('module', 'exports', source)(policyModule, policyModule.exports);
  const policy = policyModule.exports;
  if (typeof policy.assertSha !== 'function' || typeof policy.classifyPullFiles !== 'function') { throw new ObservationError('invalid_binding', 'trusted route policy exports are invalid');
  }
  return policy;
}
async function listPullFiles(github, owner, repo, pull) { const files = await github.paginate( 'GET /repos/{owner}/{repo}/pulls/{pull_number}/files', {owner, repo, pull_number: pull.number, per_page: 100}, );
  if (!Array.isArray(files) || files.length !== pull.changed_files) { throw new ObservationError('invalid_binding', 'pull request file metadata is incomplete', { publish: true, targetSha: pull.head.sha, });
  }
  return files;
}
async function classifyPull(github, owner, repo, baseSha, pull) { try { const policy = await loadRoutePolicy(github, owner, repo, baseSha);
    const files = await listPullFiles(github, owner, repo, pull);
    policy.assertSha(pull.head.sha, 'head');
    policy.assertSha(baseSha, 'base');
    return policy.classifyPullFiles(files, pull.changed_files);
  } catch (error) { if (error instanceof ObservationError) { throw error;
    }
    throw new ObservationError('invalid_binding', 'trusted route classification failed', { publish: true, targetSha: pull.head?.sha ?? null, });
  }
}
function assertRepository(context, repository) { if ( context.repo.owner !== REPOSITORY.owner || context.repo.repo !== REPOSITORY.repo || repository?.id !== REPOSITORY.id || repository?.full_name !== REPOSITORY.fullName ) { throw new ObservationError('invalid_binding', 'event repository is not canonical');
  }
}
async function getPull(github, owner, repo, pullNumber) { if (!Number.isSafeInteger(pullNumber) || pullNumber <= 0) { throw new ObservationError('ambiguous_pull', 'pull request number is invalid');
  }
  const response = await github.request( 'GET /repos/{owner}/{repo}/pulls/{pull_number}', {owner, repo, pull_number: pullNumber}, );
  return response.data;
}
function bindPull(pull, liveBaseSha, expectedHeadSha = null) { const headSha = assertSha(pull.head?.sha, 'pull request head');
  const baseSha = assertSha(pull.base?.sha, 'pull request base');
  if (expectedHeadSha !== null && headSha !== expectedHeadSha) { throw new ObservationError('stale_head', 'run head is no longer current', { publish: false, targetSha: headSha, });
  }
  if ( pull.state !== 'open' || pull.base?.ref !== 'main' || pull.base?.repo?.id !== REPOSITORY.id || pull.base?.repo?.full_name !== REPOSITORY.fullName ) { throw new ObservationError('invalid_binding', 'pull request target is not canonical main', { publish: true, targetSha: headSha, });
  }
  if (baseSha !== liveBaseSha) { throw new ObservationError('stale_base', 'pull request base is not live main', { publish: true, targetSha: headSha, });
  }
  return { headSha, baseSha, sameRepository: pull.head?.repo?.id === REPOSITORY.id && pull.head?.repo?.full_name === REPOSITORY.fullName, };
}
async function assertMergeParents(github, owner, repo, pull, baseSha, headSha) { if (pull.merge_commit_sha === null || pull.merge_commit_sha === headSha) { return false;
  }
  const mergeSha = assertSha(pull.merge_commit_sha, 'pull request merge commit');
  const response = await github.request( 'GET /repos/{owner}/{repo}/git/commits/{commit_sha}', {owner, repo, commit_sha: mergeSha}, );
  const parents = response.data.parents;
  if ( !Array.isArray(parents) || parents.length !== 2 || parents[0]?.sha !== baseSha || parents[1]?.sha !== headSha ) { throw new ObservationError('invalid_binding', 'pull request merge parents do not bind base and head', { publish: true, targetSha: headSha, });
  }
  return true;
}
async function listAttemptJobs(github, owner, repo, runId, runAttempt) { const jobs = [];
  let totalCount = null;
  const iterator = github.paginate.iterator( 'GET /repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{attempt_number}/jobs', {owner, repo, run_id: runId, attempt_number: runAttempt, per_page: 100}, );
  for await (const response of iterator) { if (!Number.isSafeInteger(response.data.total_count) || !Array.isArray(response.data.jobs)) { throw new ObservationError('invalid_binding', 'legacy quick job metadata is invalid');
    }
    totalCount ??= response.data.total_count;
    jobs.push(...response.data.jobs);
  }
  if (totalCount === null || jobs.length !== totalCount) { throw new ObservationError('invalid_binding', 'legacy quick job topology is incomplete');
  }
  return jobs;
}
function evaluateJobTopology(run, jobs, routes, draft) { const names = jobs.map((job) => job.name);
  const expectedNames = ['classify', 'quick'];
  const topologyMatches = jobs.length === 2 && expectedNames.every((name) => names.filter((candidate) => candidate === name).length === 1) && jobs.every((job) => job.run_attempt === run.run_attempt && job.status === 'completed');
  const classify = jobs.find((job) => job.name === 'classify');
  const quick = jobs.find((job) => job.name === 'quick');
  const classifyMatches = classify?.conclusion === 'success' && Array.isArray(classify.labels) && classify.labels.includes('ubuntu-latest');
  const quickWasSubstantive = quick?.conclusion !== 'skipped' && typeof quick?.runner_name === 'string' && quick.runner_name.length > 0;
  const labelsMatch = Array.isArray(quick?.labels) && ['self-hosted', 'linux', 'build'].every((label) => quick.labels.includes(label));
  const routeConcordance = topologyMatches && classifyMatches && labelsMatch && routes.docs === false && draft === false && quickWasSubstantive;
  if (run.conclusion === 'success' && quick?.conclusion === 'skipped') { return {ok: false, code: 'skipped_substantive', routeConcordance: false};
  }
  if (!routeConcordance) { return {ok: false, code: 'route_mismatch', routeConcordance: false};
  }
  if (run.conclusion !== 'success' || quick.conclusion !== 'success') { return {ok: false, code: 'terminal_failure', routeConcordance: true};
  }
  return {ok: true, code: 'terminal_match', routeConcordance: true};
}
async function assessLifecycle({github, context, liveBaseSha}) { if (!relevantLifecycle(context.payload)) { return assessment({kind: 'lifecycle', code: 'irrelevant_edit'});
  }
  assertRepository(context, context.payload.repository);
  const pullNumber = context.payload.pull_request?.number;
  const pull = await getPull(github, REPOSITORY.owner, REPOSITORY.repo, pullNumber);
  let binding = null;
  try { binding = bindPull(pull, liveBaseSha);
    if (!binding.sameRepository) { return assessment({ kind: 'lifecycle', code: 'fork', state: 'failure', publish: true, targetSha: binding.headSha, pullNumber: pull.number, baseSha: binding.baseSha, headSha: binding.headSha, });
    }
    await assertControlsUnchanged( github, REPOSITORY.owner, REPOSITORY.repo, binding.baseSha, binding.headSha, );
    const routes = await classifyPull( github, REPOSITORY.owner, REPOSITORY.repo, binding.baseSha, pull, );
    const code = pull.draft ? 'draft' : 'admitted';
    return assessment({ kind: 'lifecycle', code, state: 'pending', publish: true, targetSha: binding.headSha, pullNumber: pull.number, baseSha: binding.baseSha, headSha: binding.headSha, routes, });
  } catch (error) { if (error instanceof ObservationError) { return assessment({ kind: 'lifecycle', code: error.code, state: 'failure', publish: error.publish, targetSha: error.targetSha, pullNumber: pull.number, baseSha: binding?.baseSha ?? pull.base?.sha ?? null, headSha: binding?.headSha ?? pull.head?.sha ?? null, });
    }
    throw error;
  }
}
async function uniqueCurrentPullForHead(github, owner, repo, headSha) { const associated = await github.paginate( 'GET /repos/{owner}/{repo}/commits/{commit_sha}/pulls', {owner, repo, commit_sha: headSha, per_page: 100}, );
  const matches = associated.filter((pull) => pull.state === 'open' && pull.head?.sha === headSha && pull.base?.ref === 'main' && pull.base?.repo?.id === REPOSITORY.id && pull.base?.repo?.full_name === REPOSITORY.fullName, );
  if (matches.length !== 1) { throw new ObservationError('ambiguous_pull', 'run has no unique current pull request', { publish: false, targetSha: headSha, });
  }
  return matches[0].number;
}
async function assertCanonicalRun(github, context, run, payloadRun) { assertRepository(context, run.repository);
  if ( run.workflow_id !== LEGACY_WORKFLOW.id || run.path !== LEGACY_WORKFLOW.path || run.name !== LEGACY_WORKFLOW.name || run.event !== 'pull_request' || run.status !== 'completed' ) { throw new ObservationError('invalid_binding', 'workflow run identity is not canonical');
  }
  const headSha = assertSha(run.head_sha, 'workflow run head');
  if (payloadRun.id !== run.id || payloadRun.run_attempt !== run.run_attempt) { throw new ObservationError('stale_run', 'workflow run attempt is no longer current', { publish: true, targetSha: headSha, });
  }
  const [workflowResponse, suiteResponse] = await Promise.all([ github.request('GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}', { owner: REPOSITORY.owner, repo: REPOSITORY.repo, workflow_id: LEGACY_WORKFLOW.id, }), github.request('GET /repos/{owner}/{repo}/check-suites/{check_suite_id}', { owner: REPOSITORY.owner, repo: REPOSITORY.repo, check_suite_id: run.check_suite_id, }), ]);
  const workflow = workflowResponse.data;
  const suite = suiteResponse.data;
  if ( workflow.id !== LEGACY_WORKFLOW.id || workflow.path !== LEGACY_WORKFLOW.path || workflow.name !== LEGACY_WORKFLOW.name || workflow.state !== 'active' || suite.head_sha !== headSha || suite.app?.id !== LEGACY_WORKFLOW.actionsAppId ) { throw new ObservationError('invalid_binding', 'workflow or check-suite identity did not bind', { publish: true, targetSha: headSha, });
  }
  return headSha;
}
async function assertLatestRun(github, owner, repo, run) { const runs = await github.paginate( 'GET /repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs', { owner, repo, workflow_id: LEGACY_WORKFLOW.id, event: 'pull_request', head_sha: run.head_sha, per_page: 100, }, );
  const canonical = runs.filter((candidate) => candidate.workflow_id === LEGACY_WORKFLOW.id && candidate.path === LEGACY_WORKFLOW.path && candidate.event === 'pull_request' && candidate.head_sha === run.head_sha, );
  canonical.sort((left, right) => (right.run_number - left.run_number) || (right.id - left.id), );
  if (canonical.length === 0 || canonical[0].id !== run.id || canonical[0].run_attempt !== run.run_attempt) { throw new ObservationError('stale_run', 'workflow run is not the latest for this head', { publish: true, targetSha: run.head_sha, });
  }
}
async function assessWorkflowRun({github, context, liveBaseSha}) { const payloadRun = context.payload.workflow_run;
  if (payloadRun?.event !== 'pull_request') { return assessment({kind: 'workflow_run', code: 'ignored_non_pr'});
  }
  const response = await github.request( 'GET /repos/{owner}/{repo}/actions/runs/{run_id}', {owner: REPOSITORY.owner, repo: REPOSITORY.repo, run_id: payloadRun.id}, );
  const run = response.data;
  let headSha = assertSha(run.head_sha, 'workflow run head');
  try { headSha = await assertCanonicalRun(github, context, run, payloadRun);
    const pullNumber = await uniqueCurrentPullForHead( github, REPOSITORY.owner, REPOSITORY.repo, headSha, );
    const pull = await getPull(github, REPOSITORY.owner, REPOSITORY.repo, pullNumber);
    const binding = bindPull(pull, liveBaseSha, headSha);
    if (!binding.sameRepository || run.head_repository?.id !== REPOSITORY.id || run.head_repository?.full_name !== REPOSITORY.fullName) { return assessment({ kind: 'workflow_run', code: 'fork', state: 'failure', publish: true, targetSha: headSha, pullNumber, baseSha: binding.baseSha, headSha, runId: run.id, runAttempt: run.run_attempt, });
    }
    await assertLatestRun(github, REPOSITORY.owner, REPOSITORY.repo, run);
    await assertControlsUnchanged( github, REPOSITORY.owner, REPOSITORY.repo, binding.baseSha, headSha, );
    const routes = await classifyPull( github, REPOSITORY.owner, REPOSITORY.repo, binding.baseSha, pull, );
    await assertMergeParents( github, REPOSITORY.owner, REPOSITORY.repo, pull, binding.baseSha, headSha, );
    const jobs = await listAttemptJobs( github, REPOSITORY.owner, REPOSITORY.repo, run.id, run.run_attempt, );
    const result = evaluateJobTopology(run, jobs, routes, pull.draft);
    return assessment({ kind: 'workflow_run', code: result.code, state: result.ok ? 'success' : 'failure', publish: true, targetSha: headSha, pullNumber, baseSha: binding.baseSha, headSha, runId: run.id, runAttempt: run.run_attempt, routes, metrics: { route_job_concordance: result.routeConcordance, terminal_binding_coverage: true, v2_self_hosted_minutes: 0, false_success_publications: result.ok ? 0 : 0, stale_head_publications: 0, }, });
  } catch (error) { if (error instanceof ObservationError) { return assessment({ kind: 'workflow_run', code: error.code, state: 'failure', publish: error.publish, targetSha: error.targetSha, headSha, runId: run.id, runAttempt: run.run_attempt, });
    }
    throw error;
  }
}
async function assess({github, context, liveBaseSha}) { assertSha(liveBaseSha, 'live main');
  if (context.eventName === 'pull_request_target') { return assessLifecycle({github, context, liveBaseSha});
  }
  if (context.eventName === 'workflow_run') { return assessWorkflowRun({github, context, liveBaseSha});
  }
  return assessment({kind: 'unknown', code: 'ignored_event'});
}
module.exports = { CONTROL_PATHS, LEGACY_WORKFLOW, OBSERVATION_CONTEXT, OBSERVER_PATH, REPOSITORY, assess, assertControlsUnchanged, bindPull, evaluateJobTopology, getTreeEntry, relevantLifecycle, stableAssessment, };
