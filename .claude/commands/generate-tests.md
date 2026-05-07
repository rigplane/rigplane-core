# Test Generation

Generate targeted tests for changed code. Runs post-merge or on demand.

## Input

`$ARGUMENTS`: one of:
- `diff` — analyze current `git diff`
- `issue N` — analyze changes from issue #N
- `file path/to/file.py` — analyze specific file

## Steps

1. Identify changed code:
   - `diff` → `git diff --name-only` + `git diff`
   - `issue N` → `gh pr list --search "N" --json files` or `git log --oneline`
   - `file` → read the file, find untested public functions

2. Read existing tests for affected modules (`tests/test_*.py`)

3. Identify gaps:
   - Uncovered public functions/methods
   - Missing edge cases (boundary values, None, empty, error paths)
   - Missing assertions on return values or state changes

4. Generate tests:
   - Follow existing test style (pytest, no classes unless module uses them)
   - Use existing fixtures from `conftest.py`
   - Use `FakeAudioBackend` for audio tests
   - Deterministic — no randomness, no time-dependent assertions
   - Minimal — one test per gap, focused assertions

5. Run generated tests: `uv run pytest <new_test_file> -q --tb=short`

6. Accept ONLY if:
   - All generated tests pass
   - No existing tests broken
   - Tests validate meaningful behavior (not trivial getters)

7. Commit: `test: add coverage for <area>`

## Output

`.claude/workflow/testgen.md`:
```
# Test Generation — YYYY-MM-DD
## Source: diff | issue N | file path
## Tests added: N
## Files: (list)
## Coverage gaps remaining: (list or "none")
```

Update `.claude/metrics.json` → increment `generated_tests_count`

## Rules

- Never generate tests for unstable or currently failing code
- Never overwrite or modify existing tests
- Never modify production code
- Never generate trivial tests (testing that `x == x`)
- Never duplicate existing test coverage
- Max 10 tests per run
