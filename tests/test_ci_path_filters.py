"""Pins the `core` change-detection filter in quick.yml.

`.github/workflows/quick.yml` gates every quick-CI check (ruff, mypy,
import-linter, the validation dry-run golden gates, pytest) behind a
`dorny/paths-filter` step. Its `core` filter did not list `rigs/**` or the
repo-root `contracts/**`, so a PR that changed only a radio profile TOML
(under `rigs/`) or a consumer-contract fixture (under `contracts/`) ran
zero quick-CI checks and could merge having been tested by nothing
(MOR-1911).

This test pins the raw glob list with a small regex-based extraction
rather than a YAML parser: the `filters:` value is itself a nested YAML
document consumed by the `dorny/paths-filter` action, not a structure the
outer workflow's own schema exposes, and this repo has no YAML-parsing
dependency declared for tests to use (PyYAML is present only transitively,
via a docs tool). A regex anchored on the known block-scalar indentation
is simpler and does not risk that dependency disappearing.

Honest limit: this only proves the glob string is present in the filter
config, not that GitHub's `dorny/paths-filter` evaluates a `rigs/`-only or
`contracts/`-only diff as matching `core`. Per the action's documented glob
semantics (https://github.com/dorny/paths-filter, which follows
`micromatch`), a trailing `/**` matches the directory itself, everything
under it, and any depth of nested files, so `rigs/**` and `contracts/**`
are the correct globs for "any change anywhere under this directory" —
but that behavior lives in the action's own engine, outside this repo.
"""

from __future__ import annotations

import re
from pathlib import Path

QUICK_YML = (
    Path(__file__).resolve().parent.parent / ".github" / "workflows" / "quick.yml"
)

EXPECTED_CORE_GLOBS = {
    "src/**",
    "tests/**",
    "frontend/**",
    "pyproject.toml",
    "uv.lock",
    ".importlinter",
    ".github/scripts/**",
    ".github/workflows/**",
    "rigs/**",
    "contracts/**",
}

# Matches the `core:` list item block inside the `filters: |` block scalar,
# e.g.:
#             core:
#               - 'src/**'
#               - 'tests/**'
#             frontend:
_CORE_BLOCK_RE = re.compile(r"^ {12}core:\n((?:^ {14}- .*\n)+)", re.MULTILINE)
_GLOB_RE = re.compile(r"- '([^']*)'")


def _core_filter_globs() -> set[str]:
    text = QUICK_YML.read_text()
    match = _CORE_BLOCK_RE.search(text)
    assert match, "could not locate the `core:` filter block in quick.yml"
    return set(_GLOB_RE.findall(match.group(1)))


def test_core_filter_covers_rigs_and_contracts() -> None:
    globs = _core_filter_globs()
    assert "rigs/**" in globs, (
        "quick.yml's `core` filter is missing 'rigs/**' — a radio-profile-only "
        "PR would run zero quick-CI checks (MOR-1911)"
    )
    assert "contracts/**" in globs, (
        "quick.yml's `core` filter is missing 'contracts/**' — a "
        "consumer-contract-only PR would run zero quick-CI checks (MOR-1911)"
    )


def test_core_filter_matches_expected_set() -> None:
    assert _core_filter_globs() == EXPECTED_CORE_GLOBS
