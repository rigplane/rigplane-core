# Failure Analysis

Analyze why an issue or pipeline run failed. Runs automatically on any FAILED outcome.

## Trigger

- Issue marked FAILED in pipeline
- Test failure during any phase
- Manual: `/analyze-failure N` where N is issue number

## Steps

1. Read state files:
   - `.claude/workflow/task.md` — what was attempted
   - `.claude/workflow/progress.md` — where it stopped
   - `.claude/workflow/review.md` — reviewer findings (if reached)
   - `.claude/workflow/regression.md` — regression data (if ran)

2. Classify failure (exactly one):
   - `invalid_plan` — plan was wrong, incomplete, or violated guardrails
   - `impl_error` — executor deviated or code was incorrect
   - `test_failure` — tests failed after correct implementation
   - `env_issue` — tooling, deps, or environment problem
   - `regression` — broke existing functionality
   - `workflow_violation` — phase skipped or agent exceeded permissions

3. Analyze root cause:
   - Which phase caused the failure
   - What specific action or decision went wrong
   - Was it preventable with existing guardrails

4. Extract learnings:
   - Reusable pattern (what to do next time)
   - Anti-pattern (what to avoid)
   - Missing guardrail (if any)

5. Save to `.claude/knowledge/failures.md`:
   ```
   ### YYYY-MM-DD — Issue #N: <title>
   - **Classification:** <type>
   - **Phase:** <which phase failed>
   - **Root cause:** <1-2 sentences>
   - **Anti-pattern:** <what to avoid>
   - **Recommendation:** <how to prevent>
   ```

6. Write analysis to `.claude/workflow/failure.md`

7. Update `.claude/metrics.json`:
   - Increment `failure_types.<classification>`

## Output

`.claude/workflow/failure.md`:
```
# Failure Analysis — YYYY-MM-DD
## Issue: #N
## Classification: <type>
## Phase: <phase>
## Root cause: <description>
## Pattern: <reusable insight>
## Anti-pattern: <what to avoid>
## Recommendation: <fix or prevention>
```

## Rules

- Do NOT modify source files
- Do NOT retry the failed work
- Do NOT propose fixes (that is the planner's job in the next attempt)
- Must run on every FAILED outcome — not optional
- Max 5 file reads for analysis
