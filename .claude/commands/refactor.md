# Refactor

Deterministic, test-safe refactoring workflow. Manual trigger only.

## Input

`$ARGUMENTS`: module path, area name, or code smell description.

## Invariant

**Refactoring MUST NOT change behavior.** If behavior changes at any point → FAIL immediately.

## Pipeline: EXPLORE → PLAN → EXECUTE → TEST → REVIEW → PR

No fast path. PLAN is always mandatory.

### Phase 1: EXPLORE

1. Read target module(s) identified by `$ARGUMENTS`
2. Identify code smells:
   - Duplication
   - Large functions (>50 LOC)
   - Unclear naming
   - Poor module boundaries
   - Dead code
3. Check existing test coverage for target area
4. Write findings to `.claude/workflow/research.md`

### Phase 2: VALIDATE PRECONDITIONS

1. Take the baseline from the `quick.yml` run on `main` that CLAUDE.md
   §Agent working rules names as the baseline (must be green)
2. If target area lacks tests → generate minimal regression tests first (`/generate-tests file <path>`)
3. Baseline must be green before proceeding. If not → STOP.

### Phase 3: PLAN (mandatory)

Write `.claude/workflow/refactor-plan.md`:
- **Goal:** what improves (readability, duplication, boundaries)
- **Non-goals:** what must NOT change (behavior, API, public interface)
- **Scope:** exact files and functions (inside the soft threshold in CLAUDE.md §Guardrails)
- **Steps:** ordered list of small, independently testable changes
- **Risks:** what could break, how to verify it didn't
- **Rollback:** `git checkout -- <files>` for each step

Guardrails apply (CLAUDE.md §Guardrails): the hard ceiling is not author-waivable;
no new abstractions unless explicitly targeted.

### Phase 4: EXECUTE (strict)

1. Dispatch the `builder` role (`.claude/agents/builder.md`) with
   `refactor-plan.md` as its spec; apply changes one step at a time
2. After each step: run the targeted files — the tests covering what that step
   touched — locally, `uv run pytest <paths> --tb=short -x`. The full suite is
   CI's (CLAUDE.md §Agent working rules)
3. If tests fail after any step:
   - Rollback that step: `git checkout -- <changed files>`
   - Mark step as failed in `progress.md`
   - If 2 consecutive step failures → STOP, mark FAILED
4. Update `progress.md` after each step

Rules:
- Follow plan exactly — no scope expansion
- No new features
- No behavior changes
- No unrelated cleanups

### Phase 5: TEST

The suite result is CI's, so the PR opens here — before REVIEW, not after it.

1. Commit (`refactor: <area description>`), push, then `gh pr create --draft`:
   `quick.yml` triggers on push/PR to `main`, so the branch has no run of its
   own until the PR exists (CLAUDE.md §Agent working rules)
2. Read the gates off that PR's `quick` run at this head: it runs the pytest
   suite, `ruff check` and `ruff format --check` under its `core` path filter,
   and `mypy --strict src/rigplane/web` under its `frontend` one
3. Compare pass/fail counts against Phase 2 baseline
4. Any new failure = behavior change → rollback all, mark FAILED

### Phase 6: REVIEW

Dispatch the `verifier` role (`.claude/agents/verifier.md`) — the
implementation agent never reviews its own work (CLAUDE.md §Language & Git).
Have it confirm:
- Improved readability or reduced duplication
- No unintended changes (`git diff` review)
- No behavior changes (test counts match baseline)
- No new public API surface

Relay its verdict and write `review.md`.

### Phase 7: PR

- `gh pr ready` on the draft opened in Phase 5
- PR body: what improved, what didn't change, and the `quick` run the test
  evidence comes from

## Post-pipeline

- On success: save pattern to `.claude/knowledge/patterns.md`
- On failure: classify the outcome and record the reason in the PR or ticket,
  per CLAUDE.md §Failure handling
- Cleanup workspace

## Safety guards

- Tests fail → rollback step and FAIL
- Scope expands beyond plan → STOP
- Behavior changes detected → rollback all, FAIL
- New features introduced → STOP, mark `workflow_violation`

## Rules

- Never triggered automatically — manual `/refactor <target>` only
- Never combined with feature work in same session
- Never modify files outside the plan
- Each step must be small enough to rollback independently
