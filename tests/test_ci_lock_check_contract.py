"""Pins the lockfile drift check in ``quick.yml`` (MOR-4341).

``uv sync`` re-resolves silently, so a ``pyproject.toml`` change without a
regenerated ``uv.lock`` never failed CI: ``eesdr-tci`` and ``websockets`` sat
missing from the lock on main for days. The ``quick-python`` job now runs
``uv lock --check`` first. This pin turns red if that step is removed, loses
its core/web condition, or is moved after the ``uv sync`` that would repair the
drift in place.

Same regex-extraction approach as ``test_ci_pytest_invocation.py`` (no YAML
dependency for tests). Honest limit: it reads the text of one job in one file.
"""

from __future__ import annotations

import re
from pathlib import Path

_QUICK = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "quick.yml"
_STEP = "- name: Lockfile matches pyproject.toml"


def _quick_python_job() -> str:
    text = _QUICK.read_text(encoding="utf-8")
    start = text.index("\n  quick-python:")
    end = text.index("\n  quick-python-tests:", start)
    return text[start:end]


def _step_block(job: str) -> str:
    start = job.index(_STEP)
    match = re.search(r"\n      - ", job[start + len(_STEP) :])
    end = start + len(_STEP) + match.start() if match else len(job)
    return job[start:end]


def test_quick_python_runs_uv_lock_check_for_core_and_web_changes() -> None:
    block = _step_block(_quick_python_job())
    assert re.search(r"^\s+run: uv lock --check\s*$", block, re.MULTILINE)
    condition = re.search(r"^\s+if: (.+)$", block, re.MULTILINE)
    assert condition is not None
    assert "needs.classify.outputs.core == 'true'" in condition.group(1)
    assert "needs.classify.outputs.web == 'true'" in condition.group(1)


def test_uv_lock_check_runs_before_the_sync_that_would_repair_drift() -> None:
    job = _quick_python_job()
    assert job.index(_STEP) < job.index("run: uv sync --all-extras")
