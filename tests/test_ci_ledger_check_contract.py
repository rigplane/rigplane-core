"""Pins the assertion-free ledger freshness check in ``quick.yml`` (Group 5).

A product change that adds an assertion-free test stales
``tests/assertion_free_uncovered.txt``. The meta test
(``tests/test_meta_assertions.py``) already fails WITH THE DIFF against a
fresh scan, but it runs inside one shard of ``quick-python-tests``: the red
surfaced late, on a shard unrelated to the change. The ``quick-python`` job
now regenerates the ledger in memory first
(``scripts/regen_assertion_free_ledger.py --check``) and fails with the diff
plus the one regen command, so the offending change shows the exact ledger
line to add. This pin turns red if that step is removed, loses its core
condition, or is moved before the ``uv sync`` whose environment the regen
entry point needs (it loads ``tests/test_meta_assertions.py``, which imports
pytest).

Same regex-extraction approach as ``test_ci_lock_check_contract.py`` (no YAML
dependency for tests). Honest limit: it reads the text of one job in one file.
"""

from __future__ import annotations

import re
from pathlib import Path

_QUICK = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "quick.yml"
_STEP = "- name: Assertion-free ledger fresh"
_CHECK_RUN = "uv run python scripts/regen_assertion_free_ledger.py --check"


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


def test_quick_python_regenerates_the_ledger_for_core_changes() -> None:
    block = _step_block(_quick_python_job())
    assert re.search(rf"^\s+run: {_CHECK_RUN}\s*$", block, re.MULTILINE)
    condition = re.search(r"^\s+if: (.+)$", block, re.MULTILINE)
    assert condition is not None
    assert "needs.classify.outputs.core == 'true'" in condition.group(1)


def test_ledger_check_runs_after_the_sync_whose_env_it_needs() -> None:
    job = _quick_python_job()
    assert job.index(_STEP) > job.index("run: uv sync --all-extras")
