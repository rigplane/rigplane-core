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

Pre-flight covers every gate in `publish.yml`'s `validate` job except the
radio-smoke receipt gate (receipts are produced against the tag in Step 8 —
there is nothing to pre-flight here) and the slow-marked tests: 2h below
reads a `quick` run, which deselects the slow tests that `validate` runs —
the `full.yml` dispatch with `rc=true` at the end of 2h is what covers
them. Otherwise: frontend (2g), ruff (2d), mypy (2f), import-linter (2e),
pytest (2h). Run sequentially. STOP on first failure.

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
# 2c. Sync deps (all extras, MOR-1978: the same set publish.yml's
# validate job installs)
uv sync --all-extras
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

### 2h. Python tests — read the record, do not run the suite

The suite record for a head is that head's `quick.yml` run, and the full suite
is not run on the laptop or the remote testbed for a head CI has run (CLAUDE.md
§Testing, §Agent working rules). 2a requires a clean tree and 2b expects
`main`, so the head under test here is `main`'s current HEAD, and its run is the
one `quick.yml`'s `push: branches: [main]` trigger produced. A bump prepared on
a branch reads that branch's PR run instead — `quick.yml` triggers on
`pull_request: branches: [main]` too.

```bash
gh run list --workflow "Tests (quick)" --branch main --limit 5 \
  --json databaseId,headSha,conclusion,createdAt
gh run view <id> --json jobs \
  --jq '.jobs[].steps[] | select(.name=="Run tests") | .conclusion'
```

The second command must print `success`. `quick.yml` gates `Run tests` on
`needs.classify.outputs.core == 'true'` (the `classify` job runs
`.github/scripts/classify-quick-paths.py`), so on a run whose changed paths did
not select `core`, the step prints `skipped` while the run's
own conclusion is still `success` — the run's conclusion is not the suite's.
Then confirm that run's commit still covers HEAD by asking the same classifier
about the paths that differ; do not keep a second path list here:

```bash
RUN_SHA=<run-sha>   # the full 40-character headSha printed above
git diff --quiet "$RUN_SHA" HEAD || \
  python3 .github/scripts/classify-quick-paths.py --repo . \
    --base "$RUN_SHA" --head "$(git rev-parse HEAD)"
```

No output means HEAD has the same tree as the run's commit, so the run is the
record. Otherwise the printed JSON must show `"core": false`.

`core` true → that run is not the record for HEAD. Stop and get a run at
HEAD.

The check is against HEAD, not against the commit that gets tagged: Step 6
commits the Step 5 edits on top of HEAD, so the released commit is HEAD plus a
`pyproject.toml` version line and a `docs/CHANGELOG.md` entry. `pyproject.toml`
is a `core` path, so that commit is a different head, with its own
`quick` run when it lands.

**Interpreter matrix before the tag.** `quick.yml` pins `UV_PYTHON: "3.11"`.
`publish.yml`'s `validate` matrix is `["3.12", "3.13"]` — 3.11 is dropped there
(see the comment on that matrix) — and it runs on `release: published`, so it
first reports after the tag and the GitHub Release exist. Its failure does stop
PyPI: `build` needs `validate` and `publish` needs `build`. What it cannot undo
is the tag and the Release, which is the last block of Error recovery below. So
dispatching `full.yml`'s 3.11/3.12/3.13 matrix before tagging is a safety net,
not redundant coverage — it moves a 3.12/3.13 failure to before the tag exists:

```bash
gh workflow run "Tests (full matrix)" --ref main -f rc=true
gh run list --workflow "Tests (full matrix)" --limit 3   # find the dispatched run
gh run watch <id>
```

Without `rc=true` the dispatched run deselects the slow-marked tests, so it
would not cover what `publish.yml`'s `validate` runs.

```bash
# 2i. Build smoke
uv build && rm -rf dist/
```

```bash
# 2j. Optional regression check (.claude/commands/regression-check.md).
# Its step 1 reads "the run on its ready PR", which a release cut from
# `main` does not have; 2h above is the record in that case.
```

```bash
# 2k. Warn-only: prior FAILED issues in .claude/queue/history.json
```

```bash
# 2l. Warn-only, frontend/skins changes: review open gaps in
# docs/internals/skins-presentation-boundary-gate.md (MOR-2034)
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
  GH release:     gh release create vNEW --draft → RX + TX radio smoke →
                  upload both receipts → publish the draft (triggers publish.yml → PyPI)

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

## Step 7 — Push and create the DRAFT GitHub release (SECOND confirmation gate)

Ask once: **Push commit + tag to origin and create the draft GitHub release?** On `no` → stop; print rollback. On `yes`:

```bash
git push --follow-tags
```

Failure → stop. On success:

```bash
NOTES=$(mktemp)
# Extract the [NEW] section from docs/CHANGELOG.md to $NOTES
gh release create vNEW --draft --notes-file "$NOTES" --title "NEW"
rm -f "$NOTES"
```

**State explicitly that a draft release triggers nothing: `publish.yml` fires only on `release: published`, which happens when the draft is published in Step 9. Pushing the tag alone does NOT publish to PyPI either.**

Print draft release URL.

## Step 8 — Radio smoke against the tag

Both receipts must be produced against the tagged commit before the draft is published — `publish.yml`'s `validate` job downloads them from the release and refuses anything missing, malformed, or non-PASS (format contract: `docs/development/release-gates.md`, "Radio-smoke receipts"). The tagged commit SHA: `SHA=$(git rev-parse vNEW^{commit})`.

1. **Build the wheel from the tag** — `git worktree add / checkout vNEW` (or reuse the current checkout, which IS the tag after Step 7) and `uv build`; this is the artifact `publish.yml` will rebuild and upload.
2. **RX smoke** — run the existing RX-only smoke procedure; it writes `radio-smoke-rx.json` (`kind: "rx"`, `core_version: NEW`, `git_sha: SHA`, `verdict: "PASS"`, non-empty all-PASS `radios`).
3. **TX smoke** — in the **rigplane-pro** repo, `qa/bench/dogfood/tx_smoke.py`: keys the IC-705 into the dummy load on the bench mini through the five pinned sequences. See `qa/bench/dogfood/README.md` there for the commands — do not copy code here. With `--release-fragment radio-smoke-tx.json --core-version NEW --git-sha SHA` it writes the TX receipt.
4. **Local pre-check** (same checker the workflow runs):

```bash
python3 scripts/release/check_radio_smoke.py check \
  --tag vNEW --git-sha "$SHA" \
  --rx radio-smoke-rx.json --tx radio-smoke-tx.json \
  --repo .
```

(`--repo` needs a checkout with full history and tags; it is what lets
the checker prove a version-only rc-to-final diff — see "Release
candidates" below. For receipts naming the final tag itself it is
unused.)

5. **Upload both receipts to the draft:**

```bash
gh release upload vNEW radio-smoke-rx.json radio-smoke-tx.json
```

Any FAIL, NOTRUN, or a smoke that cannot produce a receipt → STOP. Do not publish the draft; see Error recovery.

## Step 9 — Publish the release (THIRD confirmation gate)

Ask once: **Publish release vNEW? This triggers publish.yml → PyPI.** On `yes`:

```bash
gh release edit vNEW --draft=false
```

**State explicitly that publishing the draft is what triggers `publish.yml` (`on: release: published`); its `validate` job re-checks both receipts against the tag and `$GITHUB_SHA` before the test matrix, build, and PyPI upload.**

Print release URL.

## Release candidates (rc → version-only final)

A stable release goes through a candidate first; the candidate's radio
smoke counts for the final with no re-smoke, while `publish.yml`'s
`validate` job still runs its own checks on the final commit:

1. Cut `vNEWrc1` through the normal flow above, with the rc version in
   the version files: `project.version` in `pyproject.toml` AND the
   project's own `version` in `uv.lock` say `NEWrc1`; stage `uv.lock`
   together with `pyproject.toml` and the changelog. Smoke the rc; the
   receipts name the rc tag and the rc commit. Publish as a pre-release —
   `publish.yml` validates the receipts against the rc tag itself.
2. The final is one separate commit on top that differs from the
   candidate **only** in the version: the `version` in `pyproject.toml`
   AND the project's own `version` in `uv.lock` (staged together again),
   and the changelog headings. Tag it `vNEW`.
3. Create the draft `vNEW` release, upload the **rc's** receipts to it
   (no re-smoke — the receipts keep naming what was really tested), and
   publish. `publish.yml`'s receipt gate accepts them: it checks out full
   history with tags and the checker proves the version-only rc-to-final
   diff (`docs/development/release-gates.md`, "rc receipts for a
   version-only final"). The proof is four checks: both receipts name the
   same candidate; the tag resolves via `refs/tags/` and the candidate is
   an ancestor of the final; only identical-mode edits of the three
   version files; TOML documents match past the claimed versions; the
   final changelog carries the final heading and no rc heading. A mix of
   rc and final receipts fails. Changelog body integrity is not checked.
4. Any other difference between candidate and final — a code change, a
   dependency change — means a new rc (`vNEWrc2`) through the full flow,
   not a final.

## Step 10 — Post-release housekeeping

1. Update `.claude/metrics.json`: `releases_count` += 1, `last_release_version = "NEW"`.
2. Append to `.claude/workflow/release-notes.md`: version, date, commit count, headline.
3. Print monitor link: `https://github.com/rigplane/rigplane-core/actions`.

## Step 11 — Summary

```
Release vNEW complete.

  version bump:   NEW (pyproject.toml)
  CHANGELOG:      docs/CHANGELOG.md updated
  Git tag:        vNEW
  GitHub release: <URL>
  Radio receipts: radio-smoke-rx.json + radio-smoke-tx.json uploaded and checked
  Commits since vPREV: N

CI will now:
  - publish.yml → receipt gate → tests → PyPI
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
gh release create vNEW --draft --notes-file <path-to-extracted-changelog-section> --title "NEW"
```

**Radio smoke failed (Step 8) — receipt missing, NOTRUN, or any FAIL:** do NOT publish the draft. Fix the cause, re-run the smoke; only a pair of PASS receipts goes up. If the tag itself must move, delete the draft and the tag and cut a fresh one:
```bash
gh release delete vNEW --yes   # draft was never published
git push --delete origin vNEW
git tag -d vNEW
```

**Radio-smoke step red in `publish.yml` (the release was already published):** the release is public but PyPI was never touched — `build` needs the red `validate`, and `publish` needs `build`. Do NOT re-publish or "retry" this release, and do not edit its assets to force a pass: the receipts and the tag no longer tell one story. Fix the cause and cut the NEXT tag (new patch/rc version) through the full draft-and-smoke flow. The failed release and tag may stay as the record (or be deleted per owner), but they are never published to PyPI.

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
- If `publish.yml`'s `validate` gate changes, update Step 2.
- For Pro releases (Tauri shell), use the `release` skill in `rigplane-pro` (different beast — single-tag-then-handoff to operator's Mac terminal, not single-shot publish).
