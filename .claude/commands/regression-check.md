# Regression Check

Detect test regressions after code changes.

## When to run

- Mandatory: after EXECUTE phase completes (before REVIEW)
- Optional: after PR creation

## Steps

1. Run full test suite: `uv run pytest tests/ -q --tb=short --ignore=tests/integration 2>&1`
2. Capture: total passed, failed, errors, warnings, runtime
3. Read `.claude/workflow/regression.md` for previous baseline (if exists)
4. Compare against baseline:
   - New failures (tests that passed before, fail now)
   - Increased failure count
   - Runtime increase > 2x for any test module
5. Write results to `.claude/workflow/regression.md`

## On regression detected

1. List failing tests with traceback summary
2. Identify suspected module(s) from test paths
3. Generate `git diff --stat` summary
4. If within current pipeline: signal back to executor for fix (counts toward retry limit)
5. If standalone run: append to `.claude/queue/queue.json` as new issue with:
   ```json
   {"type": "bug", "difficulty": "low", "score": 5, "reason": "regression detected by automated check"}
   ```
6. Update `.claude/metrics.json` → increment `regression_count`

## Output format

`.claude/workflow/regression.md`:
```
# Regression Check — YYYY-MM-DD
## Status: clean | regression
## Baseline: X passed, Y failed
## Current:  X passed, Y failed
## New failures: (list or "none")
## Suspected modules: (list or "none")
```

## Rules

- Do NOT modify source files
- Do NOT modify test files
- Do NOT skip this check in the pipeline
- Max 1 run per pipeline execution (no retry loops)
