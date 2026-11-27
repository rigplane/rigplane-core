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
  return firstRed ? {firstRed: firstRedPush || firstRed, green} : null;
}

function introducingMerge(sha, pulls = []) {
  const matches = pulls.filter(pr => pr?.merged_at && pr.merge_commit_sha === sha && pr.base?.ref === 'main');
  return matches.length === 1 ? matches[0] : null;
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
    find: async key => comments((await issues()).find(issue => issue.body?.includes(marker(key)))),
    findRun: async id => {
      for (const issue of await issues()) {
        const full = await comments(issue);
        if ([full.body, ...full.comments.map(comment => comment.body)].some(body => body?.includes(`<!-- main-red:run-${id}-attempt-`))) return full;
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

async function run({github, context, core, env = process.env}) {
  const repo = context.repo;
  const current = context.payload.workflow_run || (await github.rest.actions.getWorkflowRun({...repo, run_id: Number(env.MAIN_RED_RUN_ID)})).data;
  if (!eligible(current) || !PATHS.has(current.path?.split('@')[0]) || current.head_repository?.full_name !== `${repo.owner}/${repo.repo}`) {
    core.info('Not an observed completed main workflow'); return;
  }
  if (!RED.has(current.conclusion) && current.conclusion !== 'success') return;
  const history = [];
  let newerGreen = null;
  for await (const response of github.paginate.iterator(github.rest.actions.listWorkflowRuns, {
    ...repo, workflow_id: current.workflow_id, branch: 'main', status: 'completed', per_page: 100,
  })) {
    for (const candidate of [...response.data.workflow_runs].sort((a, b) => b.run_number - a.run_number)) {
      if (!eligible(candidate)) continue;
      if (candidate.run_number > current.run_number && candidate.conclusion === 'success') newerGreen = candidate;
      if (candidate.run_number >= current.run_number) continue;
      history.push(candidate);
      if (candidate.conclusion === 'success') break;
    }
    if (history.some(past => past.conclusion === 'success')) break;
  }
  const channel = `${repo.owner}/${repo.repo}/${current.workflow_id}`;
  const title = `Main red [${channel}]`;
  const issues = githubIssuesClient(github, repo);
  const recovery = current.conclusion === 'success' ? current : newerGreen;
  const eventRun = recovery || current;
  const eventKey = `run-${eventRun.id}-attempt-${eventRun.run_attempt || 1}`;
  const dryRun = env.MAIN_RED_DRY_RUN === 'true';
  const episode = findEpisode(current, history);
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
    : [`Main is red.\nRun: ${current.html_url}`, `${first.event === 'push' ? 'First red' : 'Observed red'} SHA: ${first.head_sha}`];
  let author;
  if (!recovery) {
    const pulls = first.event === 'push' ? await github.paginate(github.rest.repos.listPullRequestsAssociatedWithCommit, {
      ...repo, commit_sha: first.head_sha, per_page: 100,
    }) : [];
    const pr = introducingMerge(first.head_sha, pulls);
    const merged = pulls.filter(item => item.merged_at && item.base?.ref === 'main'
      && Date.parse(item.merged_at) <= Date.parse(first.updated_at));
    const batch = !pr && merged.length > 1;
    let ownerPr = pr;
    if (!ownerPr) {
      const candidates = batch ? merged : await github.paginate(github.rest.pulls.list, {
        ...repo, state: 'closed', base: 'main', sort: 'updated', direction: 'desc', per_page: 100,
      });
      ownerPr = candidates.filter(item => item.merged_at && Date.parse(item.merged_at) <= Date.parse(first.updated_at))
        .sort((a, b) => Date.parse(b.merged_at) - Date.parse(a.merged_at))[0];
    }
    const owner = ownerPr ? (await github.rest.pulls.get({...repo, pull_number: ownerPr.number})).data.merged_by?.login : null;
    author = pr?.user?.login;
    details.push(pr ? `First red merge: #${pr.number} (${first.head_sha}); GitHub author: ${author ? `@${author}` : 'unknown'}.`
      : batch ? `batch: PRs ${merged.map(item => `#${item.number}`).join(', ')}.` : 'introducing merge unknown.',
      `SHA range: ${episode.green?.head_sha || 'unknown baseline'}...${first.head_sha}.`,
      `Owner: ${owner ? `@${owner}` : 'unknown (no merger found in available history)'}.`,
      `Last green: ${episode.green ? `${episode.green.html_url || episode.green.id} (${episode.green.head_sha})` : 'not present in available history'}.`,
      first.event === 'push' ? 'Attribution uses the first red push run; intervening merges require diagnosis.'
        : 'No red push run in available workflow history; introducing merge unknown.',
      `Target: green in under 4 h; deadline ${new Date(Date.parse(first.updated_at) + 4 * 3600000).toISOString()}.`);
    const jobs = await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
      ...repo, run_id: current.id, attempt_number: current.run_attempt || 1, per_page: 100,
    });
    const failedJobs = jobs.filter(job => RED.has(job.conclusion));
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
  core.info(JSON.stringify({...result, key, title, author, run: eventRun.html_url, dryRun}));
}

module.exports = {extractFailures, findEpisode, introducingMerge, publish, githubIssuesClient, run};
