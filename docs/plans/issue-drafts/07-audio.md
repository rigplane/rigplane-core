## Context

Step 7 of the internal-modularization migration: move 9 top-level `audio_*` and `_audio_*` files into `src/rigplane/audio/` as submodules. **`audio.backend` and `audio.dsp` paths are NOT moved** — they are the rigplane-pro stable contract and remain exactly where they are. This step likely splits into 7a/7b at execution time per plan size budget; the maintainer decides.

Plan section: [§4.1 Step 7 — `audio` top-level (likely split 7a/7b)](https://github.com/rigplane/rigplane-core/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-7--audio-top-level-likely-split-7a7b).

## Pre-conditions

Blocked by #1289 (Step 6: scope).

## Scope

Move these 9 files from `src/rigplane/` into `src/rigplane/audio/`:

1. `src/rigplane/audio_analyzer.py` → `src/rigplane/audio/analyzer.py`
2. `src/rigplane/audio_bridge.py` → `src/rigplane/audio/bridge.py`
3. `src/rigplane/audio_bus.py` → `src/rigplane/audio/bus.py`
4. `src/rigplane/audio_fft_scope.py` → `src/rigplane/audio/fft_scope.py`
5. `src/rigplane/_audio_codecs.py` → `src/rigplane/audio/_codecs.py`
6. `src/rigplane/_audio_transcoder.py` → `src/rigplane/audio/_transcoder.py`
7. `src/rigplane/_bridge_metrics.py` → `src/rigplane/audio/_bridge_metrics.py`
8. `src/rigplane/_bridge_state.py` → `src/rigplane/audio/_bridge_state.py`
9. `src/rigplane/usb_audio_resolve.py` → `src/rigplane/audio/_usb_resolve.py`

Add **9 re-export shim files** at the old top-level paths using the plan §5.1 template.

**`src/rigplane/audio/backend/` and `src/rigplane/audio/dsp/` subdirectories are untouched.** The `audio_*` LAZY_MAP entries inside `rigplane/audio/__init__.py` keep pointing at the old top-level paths until Step 13.

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No edits to `_LAZY_MAP` (deferred to Step 13; see plan §5.4).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (unchanged).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes (3 tests green).
- Public-import smoke check (each must succeed):
  - `uv run python -c "from rigplane.audio_bridge import AudioBridge"` (legacy via shim).
  - `uv run python -c "from rigplane.audio_bus import AudioBus"` (legacy via shim).
  - `uv run python -c "from rigplane.audio_analyzer import AudioAnalyzer"` (legacy via shim).
  - `uv run python -c "from rigplane.audio_fft_scope import AudioFftScope"` (legacy via shim).
  - `uv run python -c "from rigplane.audio import AudioBus, AudioBridge"` (canonical).
- **rigplane-pro contract paths (Tier 2 of three-tier validation per plan §9):**
  - `uv run python -c "from rigplane.audio import backend, dsp"`
  - `uv run python -c "from rigplane.audio.backend import *"`
  - `uv run python -c "from rigplane.audio.dsp import *"`
  - `uv run python -c "from rigplane.dsp import pipeline, exceptions"`
  - `uv run python -c "from rigplane.dsp.nodes import base"`

## Implementation prompt for the sub-agent

```
You are implementing one step of the rigplane internal modularization
work. Read these references first:
- /Users/moroz/Projects/rigplane-research/2026-04-29-internal-modularization-orchestrator.md
- docs/plans/2026-04-29-modularization-plan.md
- The full text of this issue, especially the Scope and Acceptance
  Criteria sections

Your scope is exactly the files listed in Scope. You may not modify
any other file. You may not change runtime behavior. You may not add
tests for new functionality.

Workflow:
1. Create branch refactor/modularization-step-7 from main
2. Move/edit only files in scope
3. Add re-export shims for backwards compatibility per the plan
4. Run pytest — must pass
5. Run mypy — must not introduce new errors
6. Run ruff check — must not introduce new errors
7. Run lint-imports — skip (not yet integrated; Step 13 introduces it)
8. Commit in atomic semantic commits per the plan
9. Push branch, open PR linking to this issue
10. PR description must follow the template in the orchestrator brief

Constraints: Do not modify any file outside the Scope list. Do not
change behaviour. Do not add tests for new functionality.

If anything is unclear or any check fails for non-obvious reasons,
stop and ask via PR comment. Do not guess.
```

## Reviewer note

- Verify the shim header (plan §5.1 template, verbatim) is present in every shim file.
- LAZY_MAP target tuples must be UNCHANGED.
- **Confirm `audio/backend/` and `audio/dsp/` subdirectories are untouched.** Step 7 must not move, rename, or edit any file inside those two paths — they are the rigplane-pro stable contract.
- **Confirm rigplane-pro smoke imports succeed (Tier 2 validation).** All five paths from plan §9 (`rigplane.audio.backend`, `rigplane.audio.dsp`, `rigplane.dsp.pipeline`, `rigplane.dsp.exceptions`, `rigplane.dsp.nodes.base`) must import cleanly after the PR.
- Confirm `audio_fft_scope`'s `audio → scope` edge (legitimate per plan §1.3) still resolves through the new layer.
