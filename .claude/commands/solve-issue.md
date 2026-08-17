# Autonomous Issue Resolution

You are the orchestrator for the autonomous issue resolution pipeline.
Process the issue specified in `$ARGUMENTS` (a GitHub issue number).

## Pre-flight

1. Read `.claude/workflow/task.md` — must be idle (no other issue in progress)
2. Read `.claude/knowledge/patterns.md` and `.claude/knowledge/failures.md`
3. Fetch issue: `gh issue view $ARGUMENTS --json number,title,body,labels,state`
4. Write issue context to `.claude/workflow/task.md`

## Pipeline: EXPLORE → PLAN → EXECUTE → REGCHECK → REVIEW → TEST → PR

### Phase 1: EXPLORE
Use `.claude/agents/researcher.md`.
- Read the issue, find affected files, check knowledge base
- Write findings to `.claude/workflow/research.md`
- **STOP if confidence < 0.6** — mark issue as SKIPPED in queue

### Phase 2: PLAN
Use `.claude/agents/planner.md`.
- Enter Plan Mode first — do NOT start coding
- Design minimal fix based on research
- Write plan to `.claude/workflow/plan.md`
- **STOP if plan violates guardrails** (>3 files, >400 LOC, architecture change)

### Phase 3: EXECUTE
Use `.claude/agents/executor.md`.
- Implement plan exactly as specified
- Write tests as specified
- Update `.claude/workflow/progress.md`
- Max 2 attempts per change

### Phase 4: REGCHECK (mandatory)
Run `/regression-check` (see `.claude/commands/regression-check.md`).
- Compare test results against baseline
- Write to `.claude/workflow/regression.md`
- If regression detected → back to EXECUTE (counts toward retry limit)
- Do NOT proceed to REVIEW with regressions

### Phase 5: REVIEW
Use `.claude/agents/reviewer.md`.
- Review all changes against plan
- Check for safety, correctness, layering
- Write to `.claude/workflow/review.md`
- If needs_changes → back to EXECUTE (max 2 review loops)

### Phase 6: TEST
Use `.claude/agents/qa.md`.
- Full test suite, lint, format, type check
- If the working tree is unchanged since REGCHECK, reuse that full-suite result (do not re-run an identical suite on the same code) and run only lint, format, and type check; any code change after REGCHECK requires a fresh full run
- If fails → back to EXECUTE (max 2 fix cycles)

### Phase 7: PR
- Create branch: `fix/$ARGUMENTS-<short-slug>` or `feat/$ARGUMENTS-<short-slug>`
- Commit with conventional message: `fix(#$ARGUMENTS): ...` or `feat(#$ARGUMENTS): ...`
- Push and create PR via `gh pr create`
- PR body references the issue: `Closes #$ARGUMENTS`

## Post-pipeline

1. Update `.claude/queue/history.json` with result
2. Update `.claude/metrics.json`
3. If success: update `.claude/knowledge/patterns.md` with what worked
4. If failure: run `/analyze-failure` (mandatory), then update `.claude/knowledge/failures.md`
5. Reset `.claude/workflow/` files to idle state
6. Update `.claude/queue/active_issue.json` to idle
7. **Cleanup workspace** (mandatory — runs on success, failure, and skip):
   - `git worktree remove <path> --force` (if worktree was used)
   - `git worktree prune` to clear any orphans
   - Never `rm -rf` worktree directories — always use git commands
   - Workspace may persist ONLY if explicitly marked for manual review

## Retry policy

- Max 2 execution attempts per step
- Max 2 review loops
- Max 2 test fix cycles
- If any limit exceeded → mark FAILED, log reason, update failures.md

## Guardrails (hard limits)

- Max 3 files modified per change
- Max 400 lines of code added/changed
- No architecture changes (no new modules, no protocol changes)
- No speculative improvements beyond the issue scope
- Stop immediately if confidence < 0.6 at any phase

## Stop conditions (skip the issue)

- Ambiguous issue with no clear reproduction
- Missing reproduction steps for a bug
- Hardware dependency that cannot be mocked
- Issue requires changes to >3 files
- Issue requires >400 LOC
