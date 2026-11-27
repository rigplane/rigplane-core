'use strict';

const RED = new Set(['failure', 'timed_out', 'startup_failure']);
const EVENTS = new Set(['push', 'schedule', 'workflow_dispatch']);
const PATHS = new Set(['.github/workflows/full.yml', '.github/workflows/quick.yml']);
const eligible = run => run.head_branch === 'main' && run.status === 'completed' && EVENTS.has(run.event);
const marker = value => `<!-- main-red:${value} -->`;

function extractFailures(log) {
  const ids = new Set();
  const assertions = new Set();
  for (const raw of log.split('\n')) {
    const line = raw.replace(/\x1b\[[0-9;]*m/gu, '').replace(/^\d{4}-\d\d-\d\dT\S+\s+/u, '').trim();
    const pytest = /^(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s+|$)/u.exec(line);
    const playwright = /^\d+\)\s+(.+\.(?:mjs|[jt]s):\d+:\d+\s+›.+)/u.exec(line);
    const vitest = /^FAIL\s+(.+\.[jt]sx?(?:\s+>.+)?)/u.exec(line);
    if (pytest) ids.add(pytest[1]);
    if (playwright) ids.add(playwright[1]);
    if (vitest) ids.add(vitest[1]);
    if (/AssertionError:|Error: expect|^E\s+assert |^Expected:|^Received:/u.test(line) && assertions.size < 10) assertions.add(line.slice(0, 500));
  }
  return {ids: [...ids], assertions: [...assertions]};
}

function findEpisode(current, history) {
  if (!eligible(current) || (!RED.has(current.conclusion) && current.conclusion !== 'success')) return null;
  const runs = history.filter(run => eligible(run) && run.run_number < current.run_number)
    .sort((a, b) => b.run_number - a.run_number);
  let firstRed = RED.has(current.conclusion) ? current : null;
  let firstRedPush = firstRed?.event === 'push' ? firstRed : null;
  let green = null;
  for (const run of runs) {
    if (run.conclusion === 'success') { green = run; break; }
    if (RED.has(run.conclusion)) {
      firstRed = run;
      if (run.event === 'push') firstRedPush = run;
    }
  }
  return firstRed ? {firstRed: firstRedPush || firstRed, firstObservedRed: firstRed, green} : null;
}

function introducingMerge(sha, pulls) {
  const matches = (pulls || []).filter(pr => pr?.merged_at && pr.merge_commit_sha === sha && pr.base?.ref === 'main');
  return matches.length === 1 ? matches[0] : null;
}

async function attribution(github, repo, first, green, hasRedPush) {
  const range = `${green?.head_sha || 'unknown baseline'}...${first.head_sha}`;
  const details = [`SHA range: ${range}.`];
  const merges = new Map();
  const cache = new Map();
  let lookupError;
  async function pullsAt(sha) {
    if (!cache.has(sha)) cache.set(sha, await github.paginate(github.rest.repos.listPullRequestsAssociatedWithCommit, {
      ...repo, commit_sha: sha, per_page: 100,
    }));
    return cache.get(sha).filter(pr => pr?.merged_at && pr.merge_commit_sha === sha && pr.base?.ref === 'main');
  }
  // A range can contain a queue batch, or no merge at all (direct push/revert).
  // Attribution uncertainty must not suppress the incident and its failure evidence.
  try {
    const commits = [];
    if (green && green.head_sha !== first.head_sha) {
      for await (const response of github.paginate.iterator(github.rest.repos.compareCommitsWithBasehead, {
        ...repo, basehead: range, per_page: 100,
      })) commits.push(...response.data.commits);
    }
    for (const sha of new Set([...commits.map(commit => commit.sha), first.head_sha])) {
      for (const pr of await pullsAt(sha)) merges.set(pr.number, pr);
    }
    // A queue batch can associate several PRs with the pushed SHA while each
    // PR retains a different merge SHA. Keep candidates inside this episode's
    // time window; an old associated PR on a revert is not a new batch.
    const associated = (cache.get(first.head_sha) || []).filter(pr => pr?.base?.ref === 'main'
      && green && Date.parse(pr.merged_at) > Date.parse(green.updated_at)
      && Date.parse(pr.merged_at) <= Date.parse(first.updated_at));
    if (associated.length > 1) for (const pr of associated) merges.set(pr.number, pr);
  } catch (error) { lookupError = `Attribution lookup unavailable (HTTP ${error.status || 'unknown'}).`; }
  const candidates = [...merges.values()].sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at) || a.number - b.number);
  const pr = !lookupError && hasRedPush && candidates.length === 1 ? introducingMerge(first.head_sha, candidates) : null;
  if (!lookupError && hasRedPush && candidates.length > 1) details.push(`Introducing merge batch: PRs ${candidates.map(item => `#${item.number}`).join(', ')}.`);
  else if (pr) details.push(`First red merge: #${pr.number} (${first.head_sha}); GitHub author: ${pr.user?.login ? `@${pr.user.login}` : 'unknown'}.`);
  else details.push('Introducing merge unknown; diagnose the SHA range.');
  if (!hasRedPush) {
    details.push(`No red push run found in the available episode history; attributing merges between the last green and the first observed ${first.event} red SHA.`);
    if (green && !lookupError) {
      const rangeMerges = green.head_sha === first.head_sha ? [] : candidates;
      details.push(`Merges in observed red range: ${rangeMerges.length ? `PRs ${rangeMerges.map(item => `#${item.number}`).join(', ')}` : 'no matching PRs found'}.`);
    } else details.push('Merges in observed red range: unavailable (missing baseline or failed lookup).');
  }
  let lastMerge = candidates.at(-1);
  let owner;
  try {
    // Direct pushes and reverts have no matching PR: walk ancestors to the most
    // recent merge instead of assigning the reverted PR's author or the pusher.
    if (!lastMerge) {
      for await (const response of github.paginate.iterator(github.rest.repos.listCommits, {
        ...repo, sha: first.head_sha, per_page: 100,
      })) {
        for (const commit of response.data) {
          const pulls = (await pullsAt(commit.sha)).sort((a, b) => Date.parse(a.merged_at) - Date.parse(b.merged_at) || a.number - b.number);
          if (pulls.length) { lastMerge = pulls.at(-1); break; }
        }
        if (lastMerge) break;
      }
    }
    if (lastMerge) {
      const response = await github.rest.pulls.get({...repo, pull_number: lastMerge.number});
      owner = response.data.merged_by?.login;
    }
  } catch (error) { lookupError = `Attribution lookup unavailable (HTTP ${error.status || 'unknown'}).`; }
  details.push(owner ? `Owner: @${owner} (last merger).` : 'Owner unknown: no last-merger evidence available.');
  if (lookupError) details.push(lookupError);
  return {details, author: pr?.user?.login, owner};
}

async function publish(issues, input, dryRun = false) {
  const issue = await issues.find(input.key);
  if (!issue && input.recovered) return {action: 'no-incident'};
  if (issue?.state === 'closed') return {action: 'resolved'};
  const event = marker(input.eventKey);
  const duplicate = issue && [issue.body, ...issue.comments.map(comment => comment.body)].some(body => body?.includes(event));
  if (duplicate && !input.recovered) return {action: 'duplicate'};
  const action = input.recovered ? 'close' : issue ? 'comment' : 'create';
  if (!dryRun) {
    if (!issue) await issues.create({...input, body: `${marker(input.key)}\n${event}\n${input.body}`});
    else {
      if (!duplicate) await issues.comment(issue.number, `${event}\n${input.body}`);
      if (input.recovered) await issues.close(issue.number);
    }
  }
  return {action};
}

function githubIssuesClient(github, repo) {
  const issues = async () => (await github.paginate(github.rest.issues.listForRepo, {
    ...repo, labels: 'main-red', state: 'all', per_page: 100,
  })).filter(issue => !issue.pull_request);
  async function comments(issue) {
    if (!issue) return null;
    return {...issue, comments: await github.paginate(github.rest.issues.listComments, {
      ...repo, issue_number: issue.number, per_page: 100,
    })};
  }
  return {
    open: async channel => (await issues()).filter(issue => issue.state === 'open'
      && issue.body?.includes(`<!-- main-red:episode:${channel}:green-`)),
    findEvent: async (channel, eventKey) => {
      for (const issue of await issues()) {
        if (!issue.body?.includes(`<!-- main-red:episode:${channel}:green-`)) continue;
        const full = await comments(issue);
        if ([full.body, ...full.comments.map(comment => comment.body)].some(body => body?.includes(marker(eventKey)))) return full;
      }
      return null;
    },
    find: async key => comments((await issues()).find(issue => issue.body?.includes(marker(key)))),
    findRun: async id => {
      for (const issue of await issues()) {
        const full = await comments(issue);
        if ([full.body, ...full.comments.map(comment => comment.body)].some(body => body?.includes(marker(`first-red-run-${id}`))
          || body?.includes(`<!-- main-red:run-${id}-attempt-`))) return full;
      }
      return null;
    },
    create: async input => {
      try { await github.rest.issues.getLabel({...repo, name: 'main-red'}); }
      catch (error) {
        if (error.status !== 404) throw error;
        await github.rest.issues.createLabel({...repo, name: 'main-red', color: 'b60205'});
      }
      await github.rest.issues.create({...repo, title: input.title, labels: ['main-red'], body: input.body});
    },
    comment: (number, body) => github.rest.issues.createComment({...repo, issue_number: number, body}),
    close: number => github.rest.issues.update({...repo, issue_number: number, state: 'closed', state_reason: 'completed'}),
  };
}

async function run({github, context, core, env = process.env, workflowPaths = PATHS, jobNamesByWorkflow = {}}) {
  const repo = context.repo;
  const current = context.payload.workflow_run || (await github.rest.actions.getWorkflowRun({...repo, run_id: Number(env.MAIN_RED_RUN_ID)})).data;
  if (!eligible(current) || !workflowPaths.has(current.path?.split('@')[0]) || current.head_repository?.full_name !== `${repo.owner}/${repo.repo}`) {
    core.info('Not an observed completed main workflow'); return;
  }
  const jobsCache = new Map();
  async function jobs(run) {
    if (!jobsCache.has(run.id)) jobsCache.set(run.id, await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
      ...repo, run_id: run.id, attempt_number: run.run_attempt || 1, per_page: 100,
    }));
    const name = jobNamesByWorkflow[run.path?.split('@')[0]];
    return jobsCache.get(run.id).filter(job => !name || job.name === name);
  }
  async function observe(run) {
    if ((!RED.has(run.conclusion) && run.conclusion !== 'success') || !jobNamesByWorkflow[run.path?.split('@')[0]]) return run;
    const selected = await jobs(run);
    const conclusion = selected.some(job => RED.has(job.conclusion)) ? 'failure'
      : selected.length && selected.every(job => job.conclusion === 'success') ? 'success' : 'neutral';
    return {...run, conclusion};
  }
  const observed = await observe(current);
  if (!RED.has(observed.conclusion) && observed.conclusion !== 'success') return;
  const channel = `${repo.owner}/${repo.repo}/${current.workflow_id}`;
  const issues = githubIssuesClient(github, repo);
  const open = await issues.open(channel);
  const anchorId = issue => /<!-- main-red:first-red-run-(\d+) -->/u.exec(issue.body)?.[1]
    || /<!-- main-red:run-(\d+)-attempt-\d+ -->/u.exec(issue.body)?.[1];
  const pending = new Set(open.map(anchorId).filter(Boolean));
  pending.delete(String(current.id));
  const observedRuns = [observed];
  const history = [];
  let newerGreen = null;
  for await (const response of github.paginate.iterator(github.rest.actions.listWorkflowRuns, {
    ...repo, workflow_id: current.workflow_id, branch: 'main', status: 'completed', per_page: 100,
  })) {
    for (const candidate of [...response.data].sort((a, b) => b.run_number - a.run_number)) {
      if (!eligible(candidate)) continue;
      if (candidate.run_number === current.run_number) continue;
      const past = await observe({...candidate, path: current.path});
      observedRuns.push(past);
      pending.delete(String(candidate.id));
      if (candidate.run_number > current.run_number) {
        if (past.conclusion === 'success') newerGreen = past;
        continue;
      }
      history.push(past);
    }
    if (!pending.size && history.some(past => past.conclusion === 'success')) break;
  }
  const title = `Main red [${channel}]`;
  const dryRun = env.MAIN_RED_DRY_RUN === 'true';
  for (const issue of open) {
    const first = observedRuns.find(run => String(run.id) === anchorId(issue));
    if (!first) continue;
    const green = observed.conclusion === 'success' && observed.run_number >= first.run_number ? observed
      : observedRuns.filter(run => run.conclusion === 'success' && run.run_number >= first.run_number)
        .sort((a, b) => a.run_number - b.run_number)[0];
    if (!green) continue;
    const key = /<!-- main-red:(episode:[^ ]+) -->/u.exec(issue.body)[1];
    const result = await publish(issues, {key, eventKey: `run-${green.id}-attempt-${green.run_attempt || 1}`,
      recovered: true, body: `Green main run: ${green.html_url || green.id}`}, dryRun);
    core.info(JSON.stringify({...result, key, dryRun}));
  }
  if (RED.has(observed.conclusion)) {
    const reported = await issues.findEvent(channel, `run-${current.id}-attempt-${current.run_attempt || 1}`);
    if (reported) {
      core.info(JSON.stringify({action: reported.state === 'closed' ? 'resolved' : 'duplicate', dryRun}));
      return;
    }
  }
  const recovery = observed.conclusion === 'success' ? observed : newerGreen;
  const eventRun = recovery || current;
  const eventKey = `run-${eventRun.id}-attempt-${eventRun.run_attempt || 1}`;
  const episode = findEpisode(observed, history);
  if (!episode && recovery) {
    const issue = await issues.findRun(current.id);
    if (issue) {
      const key = /<!-- main-red:(episode:[^ ]+) -->/u.exec(issue.body)?.[1];
      if (!key) throw new Error('Incident has no episode marker');
      const result = await publish(issues, {key, eventKey, recovered: true, body: `Green rerun: ${recovery.html_url}`}, dryRun);
      core.info(JSON.stringify({...result, key, dryRun}));
    }
    return;
  }
  if (!episode) return;
  const key = `episode:${channel}:green-${episode.green?.id || 'none'}`;
  const first = episode.firstRed;
  const details = recovery ? [`Green main run: ${recovery.html_url}`]
    : [marker(`first-red-run-${episode.firstObservedRed.id}`), `Main is red.\nRun: ${current.html_url}`, `${first.event === 'push' ? 'First red' : 'Observed red'} SHA: ${first.head_sha}`];
  let author;
  let owner;
  if (!recovery) {
    const attributionResult = await attribution(github, repo, first, episode.green, first.event === 'push');
    ({author, owner} = attributionResult);
    details.push(...attributionResult.details,
      ...(first.event === 'push' ? [`First red push: ${first.html_url || first.id} (${first.head_sha}).`] : []),
      `Last green: ${episode.green ? `${episode.green.html_url || episode.green.id} (${episode.green.head_sha})` : 'not present in available history'}.`,
      'Attribution uses the first red main push when available; the SHA range requires diagnosis.',
      `Target: green in under 4 h; deadline ${new Date(Date.parse(episode.firstRed.updated_at) + 4 * 3600000).toISOString()}.`);
    const failedJobs = (await jobs(current)).filter(job => RED.has(job.conclusion));
    if (!failedJobs.length) details.push('Failing tests: no failed-job evidence available; inspect the run.');
    for (const job of failedJobs) {
      let evidence = {ids: [], assertions: []};
      let unavailable = '';
      try {
        const response = await github.rest.actions.downloadJobLogsForWorkflowRun({...repo, job_id: job.id});
        evidence = extractFailures(Buffer.from(response.data).toString('utf8'));
      } catch (error) {
        unavailable = `Logs unavailable (HTTP ${error.status || 'unknown'}); cause requires log access.`;
      }
      const failedSteps = (job.steps || []).filter(step => RED.has(step.conclusion)).map(step => step.name);
      details.push(`Job: ${job.name} — ${job.html_url}\nFailed steps: ${failedSteps.join(', ') || 'no step evidence'}\n${unavailable}\nFailing test IDs:\n${evidence.ids.map(id => `- ${id}`).join('\n') || '- No test ID found; inspect the failed step log.'}\n${evidence.assertions.join('\n')}`);
    }
  }
  const result = await publish(issues, {key, eventKey, title, recovered: Boolean(recovery), body: details.join('\n\n')}, dryRun);
  core.info(JSON.stringify({...result, key, title, author, owner, run: eventRun.html_url, dryRun}));
}

module.exports = {extractFailures, findEpisode, introducingMerge, publish, githubIssuesClient, run};
