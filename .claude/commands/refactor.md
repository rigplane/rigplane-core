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

1. Run `uv run pytest tests/ -q --tb=short` — capture baseline (must pass)
2. If target area lacks tests → generate minimal regression tests first (`/generate-tests file <path>`)
3. Baseline must be green before proceeding. If not → STOP.

### Phase 3: PLAN (mandatory)

Write `.claude/workflow/refactor-plan.md`:
- **Goal:** what improves (readability, duplication, boundaries)
- **Non-goals:** what must NOT change (behavior, API, public interface)
- **Scope:** exact files and functions (max 3 files)
- **Steps:** ordered list of small, independently testable changes
- **Risks:** what could break, how to verify it didn't
- **Rollback:** `git checkout -- <files>` for each step

Guardrails apply: ≤3 files, ≤200 LOC delta, no new abstractions unless explicitly targeted.

### Phase 4: EXECUTE (strict)

1. Apply changes one step at a time from refactor-plan.md
2. After each step: `uv run pytest tests/ -q --tb=short -x`
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

1. Run full suite: `uv run pytest tests/ -q --tb=short`
2. Run lint: `uv run ruff check src/ tests/`
3. Compare pass/fail counts against Phase 2 baseline
4. Any new failure = behavior change → rollback all, mark FAILED

### Phase 6: REVIEW

Verify:
- Improved readability or reduced duplication
- No unintended changes (`git diff` review)
- No behavior changes (test counts match baseline)
- No new public API surface
- Write `review.md`

### Phase 7: PR

- Commit: `refactor: <area description>`
- PR body: what improved, what didn't change, test evidence

## Post-pipeline

- On success: save pattern to `.claude/knowledge/patterns.md`
- On failure: run `/analyze-failure`, save to `.claude/knowledge/failures.md`
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
