# Autonomous Issue Resolution

You are the orchestrator for the autonomous issue resolution pipeline.
Process the issue specified in `$ARGUMENTS` (a GitHub issue number).

Roles are dispatched by name. The four that exist are `builder`, `researcher`,
`scout` and `verifier` (`.claude/agents/`). If a phase below names a role you
cannot dispatch, **stop and report** — do not substitute another role and do
not do the phase yourself. Substituting at the REVIEW phase is what the rule
"the implementation agent never reviews its own work" exists to prevent.

## Pre-flight

1. Fetch issue: `gh issue view $ARGUMENTS --json number,title,body,labels,state`
2. Create an ephemeral worktree on a `codex/<topic>` branch off `origin/main`
   and do all work there — never on `main`, never in the shared checkout

## Pipeline: EXPLORE → PLAN → EXECUTE → REGCHECK → REVIEW → TEST → PR

### Phase 1: EXPLORE
Dispatch the `researcher` role (`.claude/agents/researcher.md`).
- Read the issue, find affected files
- Have the researcher report findings back as text
- **STOP if confidence < 0.6** — mark the issue SKIPPED

### Phase 2: PLAN
Plan yourself, as orchestrator. There is no planner role: design decisions
belong to the coordinator, not to a subagent.
- Enter Plan Mode first — do NOT start coding
- Design the minimal fix from the research findings
- **STOP if the plan crosses the hard ceiling** in CLAUDE.md §Guardrails, or needs an architecture change
- Record the pre-change baseline: the `quick.yml` run on `main` that CLAUDE.md §Agent working rules names as the baseline. Keep its pass/fail counts in the run's working notes, so Phase 4 REGCHECK has something to compare against

### Phase 3: EXECUTE
Dispatch the `builder` role (`.claude/agents/builder.md`) with the plan as its spec.
- Implement the plan exactly as specified; write tests first
- Max 2 attempts per change

### Phase 4: REGCHECK (mandatory)
The post-change result is CI's, so the PR opens here — before REVIEW, not after it.
- Commit with a conventional message: `fix(#$ARGUMENTS): ...` or `feat(#$ARGUMENTS): ...`
- Push, then `gh pr create --draft` with `Closes #$ARGUMENTS` in the body:
  `quick.yml` triggers on push/PR to `main`, so the branch has no run of its
  own until the PR exists (CLAUDE.md §Agent working rules)
- Run `/regression-check` (see `.claude/commands/regression-check.md`), which
  takes its numbers from that PR's `quick` run at this head
- Compare test results against baseline
- If regression detected → back to EXECUTE (counts toward retry limit); push
  the fix and read the `quick` run on the new head
- Do NOT proceed to REVIEW with regressions
- With `quick` green at this head, `gh pr ready`: the verifier reviews a ready
  PR, never a draft (AGENTS.md, "Draft PRs must not merge ... run `gh pr
  ready`, then complete checks and review")

### Phase 5: REVIEW
Dispatch the `verifier` role (`.claude/agents/verifier.md`) — on the PR Phase 4
took out of draft.
- The verifier did not write the change and must not be the builder
- Review all changes against the plan; check safety, correctness, layering
- Have the verifier report its verdict back as text; you relay it
- If it reports needed changes → back to EXECUTE (max 2 review loops)

### Phase 6: TEST
Read the four gates off the `quick` run for the head under review: the standard
pytest suite, `ruff check`, `ruff format`, and `mypy`.
- `quick.yml` runs pytest and ruff under its `core` path filter and
  `mypy --strict src/rigplane/web` under its `frontend` one; a gate whose
  filter did not match has no result in that run, and CLAUDE.md §Commands
  gives where the missing mypy is picked up
- If the head is unchanged since REGCHECK, this is the same run — read it again
  rather than asking for another; a new head gets its own `quick` run
- If a gate fails → back to EXECUTE (max 2 fix cycles), then read the `quick`
  run on the new head

### Phase 7: PR (merge readiness)
The PR is already open and out of draft since Phase 4; this phase is what makes
it mergeable.
- PR body references the issue: `Closes #$ARGUMENTS`, and says why the change
  is one unit of work if it crosses the soft threshold in CLAUDE.md §Guardrails
- Re-derive the size at the head you pushed — `git diff --stat
  origin/main...HEAD` — since CLAUDE.md §Guardrails measures per PR at that head
- The verdict is bound to that head: the `Agent Review: PASS <sha>` comment must
  name the PR's current head, so a push after Phase 5 needs a fresh verdict
  (CLAUDE.md §Language & Git)

## Post-pipeline

1. If failure: classify the outcome and record the reason in the PR or ticket,
   per CLAUDE.md §Failure handling (mandatory)
2. **Cleanup workspace** (mandatory — runs on success, failure, and skip):
   - `git worktree remove <path> --force`
   - `git worktree prune` to clear any orphans
   - Never `rm -rf` worktree directories — always use git commands
   - Workspace may persist ONLY if explicitly marked for manual review

## Retry policy

- Max 2 execution attempts per step
- Max 2 review loops
- Max 2 test fix cycles
- If any limit exceeded → mark FAILED and log the reason

## Guardrails

- Size limits: CLAUDE.md §Guardrails. The hard ceiling is not author-waivable;
  crossing the soft threshold is allowed, but justify the size in the PR body
- No architecture changes (no new modules, no protocol changes)
- No speculative improvements beyond the issue scope
- Stop immediately if confidence < 0.6 at any phase

## Stop conditions (skip the issue)

- Ambiguous issue with no clear reproduction
- Missing reproduction steps for a bug
- Hardware dependency that cannot be mocked
- Issue cannot be done within the hard ceiling in CLAUDE.md §Guardrails
