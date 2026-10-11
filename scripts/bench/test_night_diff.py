"""Unit tests for scripts/bench/night_diff.py (inline artifact fixtures)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_SCRIPT = Path(__file__).with_name("night_diff.py")


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("bench_night_diff", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


night_diff = _load()


def _raw(checks: dict[str, str], *, generated_at: str, host: str) -> dict[str, object]:
    """A raw ``validate --json`` artifact: levels -> checks plus volatile fields."""
    return {
        "schema_version": 1,
        "generated_at": generated_at,
        "radio": {"model": "IC-7610", "profile_id": "icom_ic7610"},
        "transport": {"backend": "lan", "host": host, "port": 50001},
        "mode": "hardware",
        "levels": [
            {
                "level": "L1",
                "checks": [
                    {
                        "check_id": check_id,
                        "status": status,
                        "declaration": "supported",
                        "evidence": {"readback": generated_at},
                    }
                    for check_id, status in checks.items()
                ],
            }
        ],
    }


def test_identical_checks_with_different_volatile_fields_report_no_change() -> None:
    previous = _raw({"freq.read": "pass"}, generated_at="night-1", host="a")
    current = _raw({"freq.read": "pass"}, generated_at="night-2", host="b")

    diff = night_diff.diff_artifacts(previous, current)

    assert diff == {"added": [], "removed": [], "changed": []}


def test_added_removed_and_changed_checks_are_reported_by_id() -> None:
    previous = _raw(
        {"freq.read": "pass", "mode.read": "pass", "agc.set": "skip"},
        generated_at="n1",
        host="h",
    )
    current = _raw(
        {"freq.read": "fail", "agc.set": "skip", "nb.read": "pass"},
        generated_at="n2",
        host="h",
    )

    diff = night_diff.diff_artifacts(previous, current)

    assert diff["added"] == ["nb.read"]
    assert diff["removed"] == ["mode.read"]
    assert diff["changed"] == [
        {"check_id": "freq.read", "fields": {"status": ["pass", "fail"]}}
    ]


def test_normalized_previous_compares_equal_to_raw_current() -> None:
    raw = _raw({"freq.read": "pass"}, generated_at="n", host="h")
    normalized = {
        "schema_version": 1,
        "radio": {"model": "IC-7610", "profile_id": "icom_ic7610"},
        "mode": "hardware",
        "checks": [
            {"check_id": "freq.read", "status": "pass", "declaration": "supported"}
        ],
    }

    assert night_diff.diff_artifacts(normalized, raw) == {
        "added": [],
        "removed": [],
        "changed": [],
    }


def _write(path: Path, payload: dict[str, object]) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_main_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    same = _write(
        tmp_path / "a.json", _raw({"x.read": "pass"}, generated_at="1", host="h")
    )
    same2 = _write(
        tmp_path / "b.json", _raw({"x.read": "pass"}, generated_at="2", host="h")
    )
    other = _write(
        tmp_path / "c.json", _raw({"x.read": "fail"}, generated_at="3", host="h")
    )

    assert night_diff.main([str(same), str(same2), "--label", "IC-7610"]) == 0
    assert night_diff.main([str(same), str(other), "--label", "IC-7610"]) == 1
    out = capsys.readouterr().out
    assert "`x.read`: status pass -> fail" in out
    assert night_diff.main([str(tmp_path / "missing.json"), str(other)]) == 0
    assert "no previous night" in capsys.readouterr().out
