"""The quick-CI shard plugin partitions pytest deterministically (MOR-4373).

``.github/scripts/pytest_shard.py`` is loaded by the ``quick-python-tests``
matrix legs with ``-p pytest_shard`` (PYTHONPATH already carries
``.github/scripts`` for ``pytest_worker_diagnostics``). These tests load it
the same way a leg does — from its file under ``.github/scripts/`` — and run
child pytest processes against generated suites, because the property that
matters is cross-process determinism: every shard leg (and every xdist worker
inside a leg, which is just another pytest process collecting the same
nodeids) must compute the identical partition from the same collection.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from _pytest.pytester import RunResult

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / ".github" / "scripts" / "pytest_shard.py"

_SPEC = importlib.util.spec_from_file_location("pytest_shard_ci", PLUGIN)
assert _SPEC is not None and _SPEC.loader is not None
_pytest_shard = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_pytest_shard)

_SHARDS = 4


def _scripts_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """Give the child pytest the same import path the CI step has."""
    monkeypatch.setenv("PYTHONPATH", str(PLUGIN.parent))


def _make_suite(pytester: pytest.Pytester) -> None:
    """20 tests in 4 files: enough for a non-degenerate 4-way split."""
    for file_index in range(4):
        pytester.makepyfile(
            **{
                f"test_mod_{file_index}": "\n".join(
                    f"def test_case_{case}(): pass" for case in range(5)
                )
            }
        )


def _collected_lines(result: RunResult) -> list[str]:
    return [line for line in result.outlines if "::" in line]


def _summary_counts(result: RunResult) -> tuple[int, int]:
    """(kept, deselected) from the -q collect-only summary line.

    pytest 8 prints "20 tests collected" without deselection and
    "4/20 tests collected (16 deselected)" with it.
    """
    text = "\n".join(result.outlines)
    match = re.search(
        r"(?:(\d+)/)?(\d+) tests? collected(?: \((\d+) deselected\))?", text
    )
    assert match, text
    kept = int(match.group(1)) if match.group(1) is not None else int(match.group(2))
    return kept, int(match.group(3) or 0)


def _collect_shard(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, *args: str
) -> tuple[list[str], int, int]:
    _scripts_on_path(monkeypatch)
    result = pytester.runpytest_subprocess(
        "--collect-only", "-q", "-p", "pytest_shard", *args
    )
    assert result.ret == 0, result.stdout
    return _collected_lines(result), *_summary_counts(result)


def test_spec_parse_accepts_one_based_indices() -> None:
    for spec, expected in (
        ("1/1", (1, 1)),
        ("1/4", (1, 4)),
        ("2/3", (2, 3)),
        ("4/4", (4, 4)),
    ):
        assert _pytest_shard.parse_shard_spec(spec) == expected


def test_spec_parse_rejects_bad_specs() -> None:
    for spec in ("0/4", "5/4", "2/0", "2", "a/b", "1/", "/4", "2/3/4"):
        with pytest.raises(pytest.UsageError, match="--shard"):
            _pytest_shard.parse_shard_spec(spec)


def test_shard_assignment_is_a_pure_partition() -> None:
    nodeids = [
        f"tests/test_mod_{file_index}.py::test_case_{case}"
        for file_index in range(6)
        for case in range(5)
    ]
    for total in (2, 3, 4):
        buckets: dict[int, list[str]] = {index: [] for index in range(1, total + 1)}
        for nodeid in nodeids:
            buckets[_pytest_shard.shard_assignment(nodeid, total)].append(nodeid)
        assert sorted(n for bucket in buckets.values() for n in bucket) == sorted(
            nodeids
        ), total
    # A 4-way split of realistic ids must not be degenerate.
    assert len({_pytest_shard.shard_assignment(n, _SHARDS) for n in nodeids}) > 1


def test_every_item_lands_in_exactly_one_shard(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_suite(pytester)
    unsharded, total, deselected = _collect_shard(pytester, monkeypatch)
    assert total == 20
    assert deselected == 0
    shards: dict[int, list[str]] = {}
    for index in range(1, _SHARDS + 1):
        lines, collected, shard_deselected = _collect_shard(
            pytester, monkeypatch, "--shard", f"{index}/{_SHARDS}"
        )
        assert collected + shard_deselected == total
        assert collected == len(lines)
        shards[index] = lines
    union = sorted(n for lines in shards.values() for n in lines)
    assert union == sorted(unsharded)
    for left in range(1, _SHARDS + 1):
        for right in range(left + 1, _SHARDS + 1):
            assert not set(shards[left]) & set(shards[right])


def test_shard_contents_are_stable_between_runs(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_suite(pytester)
    first, _, _ = _collect_shard(pytester, monkeypatch, "--shard", f"2/{_SHARDS}")
    second, _, _ = _collect_shard(pytester, monkeypatch, "--shard", f"2/{_SHARDS}")
    assert first == second


def test_bad_spec_fails_the_run_with_a_usage_error(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_suite(pytester)
    _scripts_on_path(monkeypatch)
    for spec in ("0/4", "5/4", "two/4"):
        result = pytester.runpytest_subprocess(
            "--collect-only", "-q", "-p", "pytest_shard", "--shard", spec
        )
        assert result.ret == pytest.ExitCode.USAGE_ERROR, spec
        assert "--shard" in "\n".join(result.stderr.lines)


def test_shard_composes_with_marker_deselection(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytester.makepyfile(
        test_mixed=(
            "import pytest\n"
            "\n"
            "@pytest.mark.slow\n"
            "def test_slow_one(): pass\n"
            "@pytest.mark.slow\n"
            "def test_slow_two(): pass\n"
            "def test_fast_one(): pass\n"
            "def test_fast_two(): pass\n"
            "def test_fast_three(): pass\n"
            "def test_fast_four(): pass\n"
        )
    )
    unfiltered, _, _ = _collect_shard(pytester, monkeypatch)
    fast, collected, deselected = _collect_shard(
        pytester, monkeypatch, "-m", "not slow"
    )
    assert collected == 4
    assert deselected == 2
    # The shard filter must run AFTER pytest's own -m deselection: each shard
    # of the -m run is exactly (fast tests) ∩ (shard of the full run).
    expected_by_shard: dict[int, list[str]] = {1: [], 2: []}
    for nodeid in fast:
        expected_by_shard[_pytest_shard.shard_assignment(nodeid, 2)].append(nodeid)
    for index in (1, 2):
        lines, shard_collected, _ = _collect_shard(
            pytester, monkeypatch, "-m", "not slow", "--shard", f"{index}/2"
        )
        assert shard_collected == len(lines)
        assert sorted(lines) == sorted(expected_by_shard[index])
    assert sorted(n for lines in expected_by_shard.values() for n in lines) == sorted(
        fast
    )
    assert set(unfiltered) > set(fast)


def test_unsharded_run_is_unchanged_by_the_loaded_plugin(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_suite(pytester)
    _scripts_on_path(monkeypatch)
    without = pytester.runpytest_subprocess("--collect-only", "-q")
    assert without.ret == 0
    with_plugin, collected, deselected = _collect_shard(pytester, monkeypatch)
    assert collected == 20
    assert deselected == 0
    assert _collected_lines(without) == with_plugin
