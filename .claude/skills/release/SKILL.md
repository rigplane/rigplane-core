---
name: release
description: Cut and publish a rigplane PyPI release — version bump, changelog, tag, GitHub Release that triggers publish.yml. Use when the user asks to release, cut a tag, ship a patch/minor/major, or publish to PyPI.
argument-hint: "patch | minor | major | X.Y.Z [--dry-run]"
---

# Release rigplane-core

Cuts and publishes a release of the `rigplane` Python package to PyPI via the existing `publish.yml` workflow. Tailored to this repo's specifics: pure Python + Svelte/TS frontend, no Rust workspace, dynamic `__version__` via `importlib.metadata`, CHANGELOG symlinked from root, asymmetric CI gates.

**Arguments:**
- `patch` / `minor` / `major` — bump type (auto-suggest if omitted)
- Explicit version like `2.0.2`
- `--dry-run` — preview only; no edits, no commit, no push

User-facing prompts in Russian. Commits / CHANGELOG / tags / release notes in English.

---

## Step 1 — Determine target version

1. Read current version from `pyproject.toml` line 7: `version = "..."`. **This is the only source of truth.** Do NOT read or modify `src/rigplane/__init__.py` (it derives `__version__` from `importlib.metadata.version("rigplane")`); do NOT modify `src/icom_lan/__init__.py` (deprecation shim).
2. Get previous tag: `git tag --list 'v*' --sort=-version:refname | head -1`.
3. If no bump argument passed, **auto-suggest**:
   - `git log vPREV..HEAD --no-merges --format='%s'`
   - Any line with `BREAKING CHANGE` or `^\w+!:` → MAJOR
   - Else any `feat(...)?:` or `feat:` → MINOR
   - Else → PATCH
   - Show suggestion: `Suggested: vX.Y.Z (PATCH — N fix commits, M feat, K breaking)`. Ask accept/override.
4. Compute `NEW_VERSION` from bump type.

## Step 2 — Pre-flight checks

Match `publish.yml`'s `validate` job exactly so green pre-flight = green publish. Run sequentially. STOP on first failure.

```bash
# 2a. Clean tree
git status --porcelain
```
Any output → stop.

```bash
# 2b. On main
git branch --show-current
```
Not `main` → warn, ask confirmation.

```bash
# 2c. Sync deps
uv sync --extra dev --extra bridge
```

```bash
# 2d. Lint + format
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

```bash
# 2e. Architecture gate
uv run lint-imports
```

```bash
# 2f. Type check (CI scope: web boundary only)
uv run mypy --strict src/rigplane/web
```

```bash
# 2g. Frontend gate
cd frontend
npm ci
npm run check
npx vitest run
npm run build
cd ..
```

```bash
# 2h. Python tests
uv run pytest tests/ --ignore=tests/integration -n auto --tb=short --timeout=300 --timeout-method=thread
```

```bash
# 2i. Build smoke
uv build && rm -rf dist/
```

```bash
# 2j. Optional regression check (.claude/commands/regression-check.md)
```

```bash
# 2k. Warn-only: prior FAILED issues in .claude/queue/history.json
```

Report: "All pre-flight checks passed ✓" and continue.

## Step 3 — Generate CHANGELOG content

**Canonical file:** `docs/CHANGELOG.md`. The repo-root `CHANGELOG.md` is a **symlink** — never write through the symlink; always edit `docs/CHANGELOG.md`.

1. Read `## [Unreleased]` section. If non-empty, use as-is.
2. If empty → auto-generate from `git log vPREV..HEAD --no-merges --format='%s%n%b%n---'`:
   - `feat(...)?:` / `feat!:` → **Added**
   - `refactor(...)?:` / `perf(...)?:` → **Changed**
   - `fix(...)?:` / `fix!:` → **Fixed**
   - `docs(...)?:` → **Docs**
   - `chore|test|build|ci(...)?:` → skip unless scope is clearly user-facing
   - Other → **Other**
   - `!:` or `BREAKING CHANGE:` body marker → **Breaking changes** sub-section at top
3. Strip type prefix; preserve `(#NNN)` issue refs and short SHAs. Match v2.0.1 entry style:
   ```
   - LAN TX audio bridge: fix audio not flowing in transmit direction (#1434, ba364e54)
   ```
4. Present draft inline:
   ```
   Draft CHANGELOG entries for vNEW (auto-generated from N commits).
   Edit in place, or press Enter to accept.
   ```

## Step 4 — Preview plan (FIRST confirmation gate)

Show consolidated preview:

```
Release plan for vNEW:

  Bump:           vCURRENT → vNEW (TYPE)
  Commits:        N (X feat, Y fix, Z other)
  Files to edit:  pyproject.toml, docs/CHANGELOG.md
  Changelog:      [counts] Added / Changed / Fixed entries
  Tag:            vNEW
  Push target:    origin/main (atomic --follow-tags)
  GH release:     gh release create vNEW (triggers publish.yml → PyPI)

  Dry-run: [yes/no]
```

Ask: **Proceed with version bump, changelog update, and local commit?** Single yes/no.

## Step 5 — Apply edits (local only)

1. **`pyproject.toml`** — `version = "OLD"` → `version = "NEW"` (line 7).
2. **`docs/CHANGELOG.md`** — note: edit `docs/CHANGELOG.md` directly, NOT root `CHANGELOG.md` (symlink).
   - Keep `## [Unreleased]` header (now empty) at top.
   - Insert below: `## [NEW] — YYYY-MM-DD` with grouped sections from Step 3.
   - Footer: update `[Unreleased]: https://github.com/rigplane/rigplane-core/compare/vNEW...HEAD`. Insert `[NEW]: https://github.com/rigplane/rigplane-core/compare/vPREV...vNEW`.
   - **First-run repair**: if footer is missing entries for `[2.0.0]` and `[2.0.1]`, backfill with `https://github.com/rigplane/rigplane-core` URLs. Do NOT rewrite older `morozsm/icom-lan` URLs (GitHub auto-redirects).

**Files NOT to touch:**
- `src/rigplane/__init__.py` — `__version__` is dynamic via `importlib.metadata`
- `src/icom_lan/__init__.py` — deprecation shim, no version
- `CLAUDE.md` — no Version row exists post-rebrand
- `frontend/package.json` — `private: true`, currently `2.0.0` (cosmetic; leave unless explicitly asked)
- No `RELEASE_NOTES.md` — file is no longer used; pass changelog to `gh release create` via tempfile

## Step 6 — Commit + tag

```bash
git add pyproject.toml docs/CHANGELOG.md
git commit -m "chore(release): bump to NEW — <one-line summary>"
git tag -a vNEW -m "Release NEW"
```

Show `git show HEAD --stat` and `git tag -l vNEW`.

## Step 7 — Push and publish (SECOND confirmation gate)

Ask once: **Push commit + tag to origin and create GitHub release?** On `no` → stop; print rollback. On `yes`:

```bash
git push --follow-tags
```

Failure → stop. On success:

```bash
NOTES=$(mktemp)
# Extract the [NEW] section from docs/CHANGELOG.md to $NOTES
gh release create vNEW --notes-file "$NOTES" --title "NEW"
rm -f "$NOTES"
```

**State explicitly that `gh release create` is what triggers `publish.yml` (`on: release: published`); pushing the tag alone does NOT publish to PyPI.**

Print release URL.

## Step 8 — Post-release housekeeping

1. Update `.claude/metrics.json`: `releases_count` += 1, `last_release_version = "NEW"`.
2. Append to `.claude/workflow/release-notes.md`: version, date, commit count, headline.
3. Print monitor link: `https://github.com/rigplane/rigplane-core/actions`.

## Step 9 — Summary

```
Release vNEW complete.

  version bump:   NEW (pyproject.toml)
  CHANGELOG:      docs/CHANGELOG.md updated
  Git tag:        vNEW
  GitHub release: <URL>
  Commits since vPREV: N

CI will now:
  - publish.yml → PyPI
  - docs.yml → docs deploy

Monitor: https://github.com/rigplane/rigplane-core/actions
```

---

## Error recovery

Print these verbatim when a step fails. Never auto-rollback.

**Commit created but tag failed or not yet pushed:**
```bash
git reset --hard HEAD~1
git tag -d vNEW  # if tag was created
```

**Commit + tag created locally, push failed:**
```bash
git tag -d vNEW
git reset --hard HEAD~1
```

**Push succeeded but GitHub release failed:**
```bash
gh release create vNEW --notes-file <path-to-extracted-changelog-section> --title "NEW"
```

**Fully undo a pushed release:**
```bash
gh release delete vNEW --yes
git push --delete origin vNEW
git tag -d vNEW
git revert HEAD  # or git push --force-with-lease (dangerous; ask first)
```

---

## Notes for the operator

- This skill replaces the old global `Release icom-lan` slash command (`.claude/commands/release.md`), which had stale references to `src/icom_lan/__init__.py` (a shim, never canonical), `RELEASE_NOTES.md` (no longer used), `CLAUDE.md` Version row (no longer exists), and root `CHANGELOG.md` (symlink — must edit `docs/CHANGELOG.md`).
- Match `publish.yml`'s `validate` job exactly. If CI's validate gate changes, update Step 2.
- For Pro releases (Tauri shell), use the `release` skill in `rigplane-pro` (different beast — single-tag-then-handoff to operator's Mac terminal, not single-shot publish).
