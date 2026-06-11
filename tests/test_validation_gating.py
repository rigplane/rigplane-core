"""Tests for golden-gate normalization and regression gating (validation).

Covers the pure layer (``rigplane.validation.gating``) and its CLI wiring in
``rigplane.cli._validate``:

* ``normalize_artifact`` strips volatile fields (core version/commit,
  timestamps, transport endpoint, evidence) so two runs that only differ in
  those produce IDENTICAL normalized keys, while a status change produces a
  different key;
* ``gate_artifacts`` classifies regressions (pass->fail, new failing check,
  missing check, declaration drift) vs non-blocking improvements/additions;
* ``rigplane validate --gate`` exits 0 against a matching golden and 1 on a
  regression; ``--regen-golden`` writes the normalized artifact;
* the committed IC-7610 dry-run golden (``tests/golden/validation/``) matches
  the live generated matrix and gates clean.

All tests are dry-run / no-hardware and use the REAL schema dataclasses
(MagicMock hides signature bugs).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from rigplane.cli import _build_parser, _validate
from rigplane.validation.gating import (
    GateReport,
    format_gate_report,
    gate_artifacts,
    normalize_artifact,
)
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckResult,
    CheckStatus,
    FailureDomain,
    LevelResult,
    OperatorSafetyBlock,
    RadioTarget,
    TransportInfo,
    ValidationArtifact,
    ValidationLevel,
)

MODEL = "IC-7610"

_GOLDEN_FIXTURE = (
    Path(__file__).resolve().parent / "golden" / "validation" / "ic7610.dry-run.json"
)
_REGEN_COMMAND = (
    "uv run rigplane --model IC-7610 validate --dry-run "
    "--regen-golden tests/golden/validation/ic7610.dry-run.json"
)


def _make_artifact(
    *,
    checks: list[CheckResult],
    core_version: str = "2.9.0",
    core_commit: str | None = "abc123",
    generated_at: str | None = "2026-06-10T00:00:00.000Z",
    host: str | None = "192.168.55.40",
    port: int | None = 50001,
) -> ValidationArtifact:
    """Build a real ``ValidationArtifact`` around *checks* (single level)."""
    return ValidationArtifact(
        radio=RadioTarget(model=MODEL, profile_id="icom_ic7610"),
        transport=TransportInfo(backend="udp", host=host, port=port),
        safety=OperatorSafetyBlock(),
        levels=[LevelResult(level=ValidationLevel.BASIC_CONTROL, checks=checks)],
        core_version=core_version,
        core_commit=core_commit,
        generated_at=generated_at,
    )


def _check(
    check_id: str = "freq.set",
    status: CheckStatus = CheckStatus.PASS,
    declaration: CapabilityDeclaration = CapabilityDeclaration.SUPPORTED,
    failure_domain: FailureDomain | None = None,
    **kwargs: Any,
) -> CheckResult:
    return CheckResult(
        check_id=check_id,
        capability="freq",
        level=ValidationLevel.BASIC_CONTROL,
        status=status,
        declaration=declaration,
        summary="Set frequency and read it back.",
        failure_domain=failure_domain,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# normalize_artifact
# ---------------------------------------------------------------------------


def test_normalize_strips_volatile_fields() -> None:
    """Two artifacts differing ONLY in volatile fields normalize identically."""
    a = _make_artifact(
        checks=[_check(started_at="2026-06-10T00:00:00Z", evidence={"raw": "fe"})]
    )
    b = _make_artifact(
        checks=[_check(started_at="2026-06-11T09:30:00Z", evidence={"raw": "fd"})],
        core_version="2.10.0",
        core_commit="def456",
        generated_at="2026-06-11T09:30:01.000Z",
        host="127.0.0.1",
        port=4532,
    )
    assert normalize_artifact(a.to_dict()) == normalize_artifact(b.to_dict())


def test_normalize_differs_on_status_change() -> None:
    """A status flip survives normalization (it is the comparison key)."""
    a = _make_artifact(checks=[_check(status=CheckStatus.PASS)])
    b = _make_artifact(
        checks=[
            _check(
                status=CheckStatus.FAIL,
                failure_domain=FailureDomain.COMMAND_EXECUTION,
            )
        ]
    )
    assert normalize_artifact(a.to_dict()) != normalize_artifact(b.to_dict())


def test_normalize_keeps_stable_key_fields_and_sorts() -> None:
    """Normalized rows carry exactly the stable key fields, sorted by check_id."""
    artifact = _make_artifact(
        checks=[
            _check(check_id="mode.set"),
            _check(
                check_id="freq.set",
                status=CheckStatus.BLOCKED,
                failure_domain=FailureDomain.COMMAND_EXECUTION,
            ),
        ]
    )
    normalized = normalize_artifact(artifact.to_dict())
    assert normalized["radio"] == {"model": MODEL, "profile_id": "icom_ic7610"}
    assert "transport" not in normalized
    assert "core_version" not in normalized
    assert "generated_at" not in normalized
    rows = normalized["checks"]
    assert isinstance(rows, list)
    assert [row["check_id"] for row in rows] == ["freq.set", "mode.set"]
    assert rows[0] == {
        "check_id": "freq.set",
        "status": "blocked",
        "declaration": "supported",
        "failure_domain": "command_execution",
    }
    assert rows[1] == {
        "check_id": "mode.set",
        "status": "pass",
        "declaration": "supported",
    }


def test_normalize_is_idempotent() -> None:
    """Normalizing an already-normalized dict is a no-op (goldens are stored
    normalized, so the gate must accept both shapes)."""
    normalized = normalize_artifact(_make_artifact(checks=[_check()]).to_dict())
    assert normalize_artifact(normalized) == normalized


# ---------------------------------------------------------------------------
# gate_artifacts
# ---------------------------------------------------------------------------


def test_gate_identical_artifacts_ok() -> None:
    artifact = _make_artifact(checks=[_check()]).to_dict()
    report = gate_artifacts(artifact, copy.deepcopy(artifact))
    assert isinstance(report, GateReport)
    assert report.ok
    assert report.regressions == []
    assert report.matched == 1


def test_gate_pass_to_fail_is_regression() -> None:
    golden = _make_artifact(checks=[_check(status=CheckStatus.PASS)]).to_dict()
    current = _make_artifact(
        checks=[
            _check(
                status=CheckStatus.FAIL,
                failure_domain=FailureDomain.COMMAND_EXECUTION,
            )
        ]
    ).to_dict()
    report = gate_artifacts(current, golden)
    assert not report.ok
    assert any("freq.set" in line and "fail" in line for line in report.regressions)


def test_gate_missing_check_is_regression() -> None:
    golden = _make_artifact(
        checks=[_check(check_id="freq.set"), _check(check_id="mode.set")]
    ).to_dict()
    current = _make_artifact(checks=[_check(check_id="freq.set")]).to_dict()
    report = gate_artifacts(current, golden)
    assert not report.ok
    assert any("mode.set" in line and "missing" in line for line in report.regressions)


def test_gate_new_failing_check_is_regression_but_new_pass_is_addition() -> None:
    golden = _make_artifact(checks=[_check(check_id="freq.set")]).to_dict()
    current = _make_artifact(
        checks=[
            _check(check_id="freq.set"),
            _check(
                check_id="mode.set",
                status=CheckStatus.FAIL,
                failure_domain=FailureDomain.READBACK,
            ),
            _check(check_id="ptt.set", status=CheckStatus.PASS),
        ]
    ).to_dict()
    report = gate_artifacts(current, golden)
    assert not report.ok
    assert any("mode.set" in line for line in report.regressions)
    assert any("ptt.set" in line for line in report.additions)
    assert not any("ptt.set" in line for line in report.regressions)


def test_gate_declaration_drift_is_regression() -> None:
    golden = _make_artifact(checks=[_check()]).to_dict()
    current = _make_artifact(
        checks=[_check(declaration=CapabilityDeclaration.MANUAL_REQUIRED)]
    ).to_dict()
    report = gate_artifacts(current, golden)
    assert not report.ok
    assert any("declaration" in line for line in report.regressions)


def test_gate_fail_to_pass_is_nonblocking_improvement() -> None:
    golden = _make_artifact(
        checks=[
            _check(
                status=CheckStatus.FAIL,
                failure_domain=FailureDomain.COMMAND_EXECUTION,
            )
        ]
    ).to_dict()
    current = _make_artifact(checks=[_check(status=CheckStatus.PASS)]).to_dict()
    report = gate_artifacts(current, golden)
    assert report.ok
    assert any("freq.set" in line for line in report.improvements)


def test_format_gate_report_mentions_verdict() -> None:
    artifact = _make_artifact(checks=[_check()]).to_dict()
    ok_text = format_gate_report(
        gate_artifacts(artifact, copy.deepcopy(artifact)), golden_path="g.json"
    )
    assert "PASS" in ok_text
    bad = gate_artifacts(_make_artifact(checks=[]).to_dict(), copy.deepcopy(artifact))
    fail_text = format_gate_report(bad, golden_path="g.json")
    assert "FAIL" in fail_text
    assert "freq.set" in fail_text


# ---------------------------------------------------------------------------
# CLI wiring: --gate (dry-run, no hardware)
# ---------------------------------------------------------------------------


def _parse(argv: list[str]) -> Any:
    return _build_parser().parse_args(argv)


def _run_validate(argv: list[str], capsys: Any) -> tuple[int, str, str]:
    rc = _validate.run(_parse(argv))
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def _write_dry_run_golden(path: Path, capsys: Any) -> dict[str, Any]:
    """Run a dry-run, normalize the JSON artifact, write it to *path*."""
    rc, out, _err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--json"], capsys
    )
    assert rc == 0
    normalized = normalize_artifact(json.loads(out))
    path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
    return normalized


def test_cli_gate_against_own_golden_exits_zero(tmp_path: Path, capsys: Any) -> None:
    golden = tmp_path / "golden.json"
    _write_dry_run_golden(golden, capsys)
    rc, _out, err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--gate", str(golden)],
        capsys,
    )
    assert rc == 0
    assert "PASS" in err


def test_cli_gate_detects_mutated_golden(tmp_path: Path, capsys: Any) -> None:
    """Mutating one check's status in the golden makes the gate exit non-zero."""
    golden = tmp_path / "golden.json"
    data = _write_dry_run_golden(golden, capsys)
    # Claim a check passed in the golden; the dry-run can never satisfy that.
    data["checks"][0]["status"] = "pass"
    golden.write_text(json.dumps(data), encoding="utf-8")
    rc, _out, err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--gate", str(golden)],
        capsys,
    )
    assert rc == 1
    assert "FAIL" in err


def test_cli_gate_missing_golden_exits_two(tmp_path: Path, capsys: Any) -> None:
    rc, _out, err = _run_validate(
        [
            "--model",
            MODEL,
            "validate",
            "--dry-run",
            "--gate",
            str(tmp_path / "nope.json"),
        ],
        capsys,
    )
    assert rc == 2
    assert "cannot load golden" in err


# ---------------------------------------------------------------------------
# CLI wiring: --regen-golden
# ---------------------------------------------------------------------------


def test_cli_regen_golden_writes_normalized_artifact(
    tmp_path: Path, capsys: Any
) -> None:
    golden = tmp_path / "nested" / "golden.json"
    rc, _out, err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--regen-golden", str(golden)],
        capsys,
    )
    assert rc == 0
    assert "Golden written to" in err
    data = json.loads(golden.read_text(encoding="utf-8"))
    assert "levels" not in data
    assert "core_version" not in data
    assert "transport" not in data
    assert data["mode"] == "dry-run"
    assert data["radio"] == {"model": MODEL, "profile_id": "icom_ic7610"}
    rows = data["checks"]
    assert rows and all("check_id" in row and "status" in row for row in rows)


def test_cli_regen_then_gate_round_trips(tmp_path: Path, capsys: Any) -> None:
    """A freshly regenerated golden gates its own dry-run clean (exit 0)."""
    golden = tmp_path / "golden.json"
    rc, _out, _err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--regen-golden", str(golden)],
        capsys,
    )
    assert rc == 0
    rc, _out, err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--gate", str(golden)],
        capsys,
    )
    assert rc == 0
    assert "PASS" in err


# ---------------------------------------------------------------------------
# Committed IC-7610 dry-run golden fixture
# ---------------------------------------------------------------------------


def test_committed_ic7610_golden_matches_live_dry_run(capsys: Any) -> None:
    """The committed golden equals the live normalized dry-run output exactly.

    Regenerate after an intentional matrix change with::

        uv run rigplane --model IC-7610 validate --dry-run \
          --regen-golden tests/golden/validation/ic7610.dry-run.json
    """
    rc, out, _err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--json"], capsys
    )
    assert rc == 0
    live = normalize_artifact(json.loads(out))
    committed = json.loads(_GOLDEN_FIXTURE.read_text(encoding="utf-8"))
    assert live == committed, f"Golden fixture is stale; regen: {_REGEN_COMMAND}"


def test_cli_gate_against_committed_golden_exits_zero(capsys: Any) -> None:
    rc, _out, err = _run_validate(
        ["--model", MODEL, "validate", "--dry-run", "--gate", str(_GOLDEN_FIXTURE)],
        capsys,
    )
    assert rc == 0
    assert "PASS" in err


# ---------------------------------------------------------------------------
# MOR-667: --interactive prompter construction (TTY gate, no-hang)
# ---------------------------------------------------------------------------


def test_build_prompter_none_without_interactive_flag() -> None:
    args = _parse(["--model", MODEL, "validate", "--hardware"])
    assert _validate._build_prompter(args) is None


def test_build_prompter_none_when_stdin_not_a_tty(
    monkeypatch: Any, capsys: Any
) -> None:
    """--interactive without a TTY must NOT build a prompter (no stdin hang)."""
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    args = _parse(["--model", MODEL, "validate", "--hardware", "--interactive"])
    assert _validate._build_prompter(args) is None
    err = capsys.readouterr().err
    assert "not a TTY" in err


def test_build_prompter_built_when_interactive_and_tty(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    args = _parse(["--model", MODEL, "validate", "--hardware", "--interactive"])
    prompter = _validate._build_prompter(args)
    assert prompter is not None
    assert prompter.assume_yes is False


def test_build_prompter_assume_yes_propagates(monkeypatch: Any) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    args = _parse(
        ["--model", MODEL, "validate", "--hardware", "--interactive", "--assume-yes"]
    )
    prompter = _validate._build_prompter(args)
    assert prompter is not None
    assert prompter.assume_yes is True
