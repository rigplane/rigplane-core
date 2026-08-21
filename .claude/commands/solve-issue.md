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

### Phase 3: EXECUTE
Dispatch the `builder` role (`.claude/agents/builder.md`) with the plan as its spec.
- Implement the plan exactly as specified; write tests first
- Max 2 attempts per change

### Phase 4: REGCHECK (mandatory)
Run `/regression-check` (see `.claude/commands/regression-check.md`).
- Compare test results against baseline
- If regression detected → back to EXECUTE (counts toward retry limit)
- Do NOT proceed to REVIEW with regressions

### Phase 5: REVIEW
Dispatch the `verifier` role (`.claude/agents/verifier.md`).
- The verifier did not write the change and must not be the builder
- Review all changes against the plan; check safety, correctness, layering
- Have the verifier report its verdict back as text; you relay it
- If it reports needed changes → back to EXECUTE (max 2 review loops)

### Phase 6: TEST
Run the gates yourself, from CLAUDE.md § Commands: the standard pytest suite,
`ruff check`, `ruff format`, and `mypy`.
- If the working tree is unchanged since REGCHECK, reuse that full-suite result (do not re-run an identical suite on the same code) and run only lint, format, and type check; any code change after REGCHECK requires a fresh full run
- If a gate fails → back to EXECUTE (max 2 fix cycles)

### Phase 7: PR
- Commit with a conventional message: `fix(#$ARGUMENTS): ...` or `feat(#$ARGUMENTS): ...`
- Push and create the PR via `gh pr create`
- PR body references the issue: `Closes #$ARGUMENTS`

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
