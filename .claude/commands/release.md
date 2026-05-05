# Release rigplane

Automate the full release process.

**Arguments** (`$ARGUMENTS`): any combination of:
- Bump: `patch` / `minor` / `major` / explicit version like `0.17.0`
- Flag: `--dry-run` (preview everything, no writes, no push, no tag)

If arguments omitted, the skill auto-suggests a bump type from commit prefixes since the last tag.

User-facing messages in Russian. Code / commit / release text in English.

---

## Step 1 — Determine target version

1. Read current version from `pyproject.toml` (`version = "..."`) and `src/rigplane/__init__.py` (`__version__ = "..."`). If mismatched, report both and proceed to sync.
2. Get previous tag: `git tag --list 'v*' --sort=-version:refname | head -1`.
3. If no bump argument passed, **auto-suggest**:
   - Run `git log vPREV..HEAD --no-merges --format='%s'`
   - If any line starts with `BREAKING CHANGE` or matches `^(feat|fix|refactor)!:` → MAJOR
   - Else if any line starts with `feat(` or `feat:` → MINOR
   - Else → PATCH
   - Show suggestion with reason: `Suggested: vX.Y.Z (PATCH — 6 fix commits, 0 feat, 0 breaking)`. Ask user to accept or override.
4. Compute `NEW_VERSION` from bump type.

## Step 2 — Pre-flight checks

Run sequentially. STOP on first failure with a clear error.

```bash
# 2a. Clean working tree (allow RELEASE_NOTES.md)
git status --porcelain | grep -v '^?? RELEASE_NOTES.md$' | grep -v '^ M RELEASE_NOTES.md$'
```
Any output → stop.

```bash
# 2b. On main
git branch --show-current
```
Not `main` → warn, ask confirmation.

```bash
# 2c. Python tests (unit + contract, skip hw)
uv run pytest tests/ -q --tb=short --ignore=tests/integration
```

```bash
# 2d. Python lint
uv run ruff check src/ tests/
```

```bash
# 2e. Python types
uv run mypy src/
```

```bash
# 2f. Python build artifact verification
uv build 2>&1 | tail -5
```
Must produce wheel + sdist in `dist/`. Clean up after: `rm -rf dist/`.

```bash
# 2g. Frontend gate (if frontend/package.json exists)
cd frontend && npm ci && npm run build && npx vitest run
```
Skip silently if `frontend/` is absent. Must pass if present.

```bash
# 2h. Regression check
```
Run `/regression-check`. Any regression → stop.

```bash
# 2i. Recent FAILED issues (warning only, not blocking)
```
Check `.claude/queue/history.json` for recent FAILED without resolution. Warn if found.

Report: "All pre-flight checks passed ✓" and continue.

## Step 3 — Generate CHANGELOG content

Read `CHANGELOG.md`. Find the `[Unreleased]` section (ends at next `## [` heading).

**If the section has real bullets** (non-empty, not just whitespace) → use it as-is.

**If the section is empty** → auto-generate from git log. Do NOT stop:
1. Run `git log vPREV_VERSION..HEAD --no-merges --format='%s%n%b%n---'`
2. Group by conventional prefix:
   - `feat(...)?:` / `feat!:` → **Added**
   - `refactor(...)?:` → **Changed**
   - `perf(...)?:` → **Changed**
   - `fix(...)?:` / `fix!:` → **Fixed**
   - `docs(...)?:` → **Docs**
   - `chore(...)?:` / `test(...)?:` / `build(...)?:` / `ci(...)?:` → skip (unless scope matches an interesting area)
   - Anything else → **Other**
3. For each entry: strip prefix, strip `(#NNN)` trailing issue refs, keep imperative tense, include referenced issue numbers as `(#NNN)` at end of bullet.
4. Detect breaking markers (`!:`, `BREAKING CHANGE:` in body) → surface under **Breaking Changes** sub-section.

This is a **draft** — present it to the user with an explicit prompt:
```
Draft CHANGELOG entries for vNEW_VERSION (auto-generated from N commits).
Edit in place, or press Enter to accept.
```
If the user edits, re-read before proceeding.

## Step 4 — Preview plan (FIRST confirmation gate)

Show the user a single consolidated preview:

```
Release plan for vNEW_VERSION:

  Bump:          vCURRENT → vNEW (TYPE)
  Commits:       N (X feat, Y fix, Z other)
  Files to edit: pyproject.toml, src/rigplane/__init__.py, CHANGELOG.md, RELEASE_NOTES.md [, CLAUDE.md]
  Changelog:     [count] Added / [count] Changed / [count] Fixed entries
  Tag:           vNEW_VERSION
  Push target:   origin/main (atomic --follow-tags)
  GH release:    vNEW_VERSION with RELEASE_NOTES.md

  Dry-run: [yes/no]
```

Ask: **Proceed with version bump, changelog update, and local commit?** (single yes/no)

On `no` → abort cleanly, no writes yet.
On dry-run → print what would be done and STOP here.

## Step 5 — Apply version bump and changelog (local only)

All writes local — nothing pushed yet.

1. `pyproject.toml` — `version = "OLD"` → `version = "NEW"`
2. `src/rigplane/__init__.py` — `__version__ = "OLD"` → `__version__ = "NEW"`
3. `CLAUDE.md` — `| **Version** | OLD |` → `| **Version** | NEW |` (if the row exists)
4. `CHANGELOG.md`:
   - Keep `## [Unreleased]` header (now empty) at top
   - Insert below: `## [NEW_VERSION] — YYYY-MM-DD` with today's date, followed by the content from Step 3
   - Footer links: update `[Unreleased]` to `vNEW...HEAD`, insert `[NEW_VERSION]: vPREV...vNEW`
5. `RELEASE_NOTES.md`: **derive from the CHANGELOG section you just inserted** — do NOT regenerate from commits.
   Structure:
   ```markdown
   # rigplane NEW_VERSION

   **Release date:** Month Day, Year

   [Copy of the CHANGELOG [NEW_VERSION] section content, verbatim]

   ## Install / Upgrade

   ```bash
   pip install rigplane==NEW_VERSION
   # or upgrade:
   pip install --upgrade rigplane
   ```
   ```

## Step 6 — Local commit and tag

Stage exactly the files modified in Step 5. Commit:

```bash
git commit -m "chore: release vNEW_VERSION"
git tag -a vNEW_VERSION -m "Release NEW_VERSION"
```

Show `git show HEAD --stat` and `git tag -l vNEW_VERSION`. No confirmation prompt here — user already approved in Step 4.

## Step 7 — Push and publish (SECOND confirmation gate)

Ask once: **Push commit + tag to origin and publish GitHub release?** (single yes/no)

On `no` → stop; print rollback instructions (see Error recovery) so the user can undo the local commit/tag.

On `yes`:
```bash
git push --follow-tags
```
If this fails → stop. Do NOT attempt the GitHub release. Print rollback (Error recovery).

On push success:
```bash
gh release create vNEW_VERSION --notes-file RELEASE_NOTES.md --title "NEW_VERSION"
```
Print the release URL.

## Step 8 — Post-release housekeeping

1. Update `.claude/metrics.json`: increment `releases_count`, set `last_release_version` to `"NEW_VERSION"`.
2. Save summary to `.claude/workflow/release-notes.md`: version, date, commit count, highlights.
3. Remove `RELEASE_NOTES.md` from disk (it was a one-shot artifact; the canonical source is now the CHANGELOG section + GH release body). Commit the removal if present:
   ```bash
   git rm RELEASE_NOTES.md && git commit -m "chore: clean up RELEASE_NOTES.md after release" && git push
   ```
   Skip if the file was already gitignored or never tracked.

## Step 9 — Summary

```
Release vNEW_VERSION complete.

  version bump:   NEW_VERSION (pyproject + __init__ + CLAUDE.md)
  CHANGELOG.md:   updated
  Git tag:        vNEW_VERSION
  GitHub release: [URL]
  Commits since vPREV_VERSION: N

CI will now:
  - Publish to PyPI (publish.yml)
  - Deploy docs (docs.yml)

Monitor: https://github.com/rigplane/rigplane-core/actions
```

---

## Error recovery

Print these verbatim when a step after local commit fails.

**If commit created but tag failed or not yet pushed:**
```bash
git reset --hard HEAD~1
git tag -d vNEW_VERSION  # if tag was created
```

**If commit + tag created locally, push failed:**
```bash
git tag -d vNEW_VERSION
git reset --hard HEAD~1
```

**If push succeeded but GitHub release failed:**
```bash
# Retry only the release step — commit + tag are already on origin
gh release create vNEW_VERSION --notes-file RELEASE_NOTES.md --title "NEW_VERSION"
```

**If you want to fully undo a pushed release:**
```bash
gh release delete vNEW_VERSION --yes
git push --delete origin vNEW_VERSION
git tag -d vNEW_VERSION
git revert HEAD  # or git push --force-with-lease origin main~1:main (dangerous)
```

Never roll back automatically. Always show commands and let the user decide.
