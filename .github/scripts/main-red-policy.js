'use strict';
const {createHash} = require('node:crypto');

const RED = new Set(['failure', 'timed_out', 'startup_failure', 'action_required', 'cancelled']);
const EVENTS = new Set(['push', 'schedule', 'workflow_dispatch']);
const PATHS = new Set(['.github/workflows/full.yml', '.github/workflows/quick.yml', '.github/workflows/pytest.yml', '.github/workflows/onboarding-workflow-tests.yml']);
const eligible = run => run.head_branch === 'main' && EVENTS.has(run.event);
const marker = value => `<!-- main-red:${value} -->`;
function identifier(value) {
  const hex = createHash('sha256').update(`rigplane-main-red:${value}`).digest('hex');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-5${hex.slice(13, 16)}-a${hex.slice(17, 20)}-${hex.slice(20, 32)}`;
}

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
  let green = null;
  for (const run of runs) {
    if (run.conclusion === 'success') { green = run; break; }
    if (RED.has(run.conclusion)) firstRed = run;
  }
  return firstRed ? {firstRed, green} : null;
}

function introducingMerge(sha, pulls) {
  const matches = pulls.filter(pr => pr.merged_at && pr.merge_commit_sha === sha && pr.base.ref === 'main');
  if (matches.length !== 1) throw new Error(`Cannot identify one introducing merge for ${sha}`);
  return matches[0];
}

async function publish(linear, input, dryRun = false) {
  const issue = await linear.find(input.key);
  const event = marker(input.eventKey);
  if (issue && (issue.description.includes(event) || issue.comments.some(comment => comment.body.includes(event)))) return {action: 'duplicate'};
  if (!issue && input.recovered) return {action: 'no-incident'};
  const action = issue ? 'comment' : 'create';
  if (!dryRun) {
    if (issue) await linear.comment(issue.id, `${event}\n${input.body}`, input.eventKey);
    else await linear.create({...input, body: `${marker(input.key)}\n${event}\n${input.body}`});
  }
  return {action};
}

function linearClient(fetch, token, team, title, owners) {
  async function query(queryText, variables) {
    const response = await fetch('https://api.linear.app/graphql', {
      method: 'POST', headers: {'Content-Type': 'application/json', Authorization: token},
      body: JSON.stringify({query: queryText, variables}), signal: AbortSignal.timeout(30000),
    });
    if (!response.ok) throw new Error(`Linear HTTP ${response.status}`);
    const result = await response.json();
    if (result.errors?.length || !result.data) throw new Error('Linear GraphQL request failed');
    return result.data;
  }
  async function pages(queryText, variables, select) {
    const nodes = [];
    let after = null;
    do {
      const page = select(await query(queryText, {...variables, after}));
      nodes.push(...page.nodes);
      after = page.pageInfo.hasNextPage ? page.pageInfo.endCursor : null;
    } while (after);
    return nodes;
  }
  const issues = () => pages(`query($team: ID!, $title: String!, $after: String) {
    issues(filter: {team: {id: {eq: $team}}, title: {contains: $title}}, includeArchived: true, first: 100, after: $after) {
      nodes {id description} pageInfo {hasNextPage endCursor}
    }
  }`, {team, title}, data => data.issues);
  async function comments(issue) {
    if (!issue) return null;
    return {...issue, description: issue.description || '', comments: await pages(`query($id: String!, $after: String) {
      issue(id: $id) {comments(first: 100, after: $after) {nodes {body} pageInfo {hasNextPage endCursor}}}
    }`, {id: issue.id}, data => data.issue.comments)};
  }
  async function comment(issueId, body, eventKey) {
    try {
      const data = await query(`mutation($input: CommentCreateInput!) {commentCreate(input: $input) {success}}`, {
        input: {id: identifier(eventKey), issueId, body},
      });
      if (!data.commentCreate.success) throw new Error('Linear comment creation failed');
    } catch (error) {
      const existing = await comments({id: issueId});
      if (!existing.comments.some(item => item.body.includes(marker(eventKey)))) throw error;
    }
  }
  return {
    find: async key => comments((await issues()).find(issue => issue.description?.includes(marker(key)))),
    findRun: async id => {
      for (const issue of await issues()) {
        const full = await comments(issue);
        if ([full.description, ...full.comments.map(comment => comment.body)].some(body => body.includes(`main-red:run-${id}-attempt-`))) return full;
      }
      return null;
    },
    create: async input => {
      const assigneeId = owners[input.owner];
      if (!assigneeId) throw new Error(`No Linear assignee mapping for GitHub author ${input.owner}`);
      const id = identifier(input.key);
      try {
        const data = await query(`mutation($input: IssueCreateInput!) {issueCreate(input: $input) {success issue {id}}}`, {
          input: {id, teamId: team, assigneeId, priority: 1, title: input.title, description: input.body},
        });
        if (!data.issueCreate.success || !data.issueCreate.issue?.id) throw new Error('Linear issue creation failed');
      } catch (error) {
        const existing = (await query(`query($id: String!) {issue(id: $id) {id description}}`, {id})).issue;
        if (!existing) throw error;
        if (!existing.description?.includes(marker(input.eventKey))) await comment(id, `${marker(input.eventKey)}\n${input.body}`, input.eventKey);
      }
    },
    comment,
  };
}

async function run({github, context, core, env = process.env, fetch = globalThis.fetch}) {
  const repo = context.repo;
  const current = context.payload.workflow_run || (await github.rest.actions.getWorkflowRun({...repo, run_id: Number(env.MAIN_RED_RUN_ID)})).data;
  if (!eligible(current) || !PATHS.has(current.path?.split('@')[0]) || current.head_repository?.full_name !== `${repo.owner}/${repo.repo}`) {
    core.info('Not an observed main workflow'); return;
  }
  const macos = current.path.split('@')[0].endsWith('/onboarding-workflow-tests.yml');
  const jobsCache = new Map();
  async function jobs(run) {
    if (!jobsCache.has(run.id)) jobsCache.set(run.id, await github.paginate(github.rest.actions.listJobsForWorkflowRunAttempt, {
      ...repo, run_id: run.id, attempt_number: run.run_attempt || 1, per_page: 100,
    }));
    return jobsCache.get(run.id).filter(job => !macos || job.name === 'browser-webkit-macos');
  }
  async function observe(run) {
    if (!macos) return run;
    const selected = await jobs(run);
    if (!selected.length || selected.every(job => job.conclusion === 'skipped')) return {...run, conclusion: 'skipped'};
    return {...run, conclusion: selected.every(job => job.conclusion === 'success') ? 'success' : 'failure'};
  }
  const observed = await observe(current);
  if (!RED.has(observed.conclusion) && observed.conclusion !== 'success') return;
  const history = [];
  for await (const response of github.paginate.iterator(github.rest.actions.listWorkflowRuns, {
    ...repo, workflow_id: current.workflow_id, branch: 'main', status: 'completed', per_page: 100,
  })) {
    for (const candidate of [...response.data.workflow_runs].sort((a, b) => b.run_number - a.run_number)) {
      if (!eligible(candidate) || candidate.run_number >= current.run_number) continue;
      const past = await observe(candidate);
      history.push(past);
      if (past.conclusion === 'success') break;
    }
    if (history.some(past => past.conclusion === 'success')) break;
  }
  const channel = `${repo.owner}/${repo.repo}/${current.workflow_id}`;
  const title = `Main red [${channel}]`;
  if (!env.LINEAR_API_KEY || !env.MAIN_RED_LINEAR_TEAM_ID) throw new Error('Configure the existing Linear secret name and team ID before activation');
  const linear = linearClient(fetch, env.LINEAR_API_KEY, env.MAIN_RED_LINEAR_TEAM_ID, title, JSON.parse(env.MAIN_RED_OWNER_MAP || '{}'));
  const recovered = observed.conclusion === 'success';
  const eventKey = `run-${current.id}-attempt-${current.run_attempt || 1}`;
  let episode = findEpisode(observed, history);
  if (!episode && recovered) {
    const issue = await linear.findRun(current.id);
    if (issue) {
      const key = /<!-- main-red:(episode:[^ ]+) -->/u.exec(issue.description)?.[1];
      if (!key) throw new Error('Incident has no episode marker');
      const result = await publish(linear, {key, eventKey, recovered, body: `Green rerun: ${current.html_url}\nTicket remains open.`}, env.MAIN_RED_DRY_RUN === 'true');
      core.info(result.action);
    }
    return;
  }
  if (!episode) return;
  const key = `episode:${channel}:green-${episode.green?.id || 'none'}`;
  const first = episode.firstRed;
  const details = [`${recovered ? 'Green; ticket remains open.' : 'Main is red.'}\nRun: ${current.html_url}`, `First red SHA: ${first.head_sha}`];
  let owner;
  if (!recovered) {
    const pulls = await github.paginate(github.rest.repos.listPullRequestsAssociatedWithCommit, {...repo, commit_sha: first.head_sha, per_page: 100});
    const pr = introducingMerge(first.head_sha, pulls);
    owner = pr.user.login;
    details.push(`First red merge: #${pr.number} (${first.head_sha}); GitHub lane/author: ${owner}.`,
      `Last green: ${episode.green ? `${episode.green.html_url || episode.green.id} (${episode.green.head_sha})` : 'not present in available history'}.`,
      'Attribution uses the first red main run; intervening merges require diagnosis.',
      `Target: green in under 4 h; deadline ${new Date(Date.parse(first.updated_at) + 4 * 3600000).toISOString()}.`);
    for (const job of (await jobs(current)).filter(job => RED.has(job.conclusion))) {
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
  const result = await publish(linear, {key, eventKey, title, owner, recovered, body: details.join('\n\n')}, env.MAIN_RED_DRY_RUN === 'true');
  core.info(JSON.stringify({...result, key, title, owner, run: current.html_url, dryRun: env.MAIN_RED_DRY_RUN === 'true'}));
}

module.exports = {extractFailures, findEpisode, introducingMerge, publish, linearClient, identifier, run};
