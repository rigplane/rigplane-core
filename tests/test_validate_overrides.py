"""Tests for override-file wiring into the validate generate path (MOR-206).

The PURE merge core (``rigplane.validation.overrides``) is tested elsewhere;
these tests exercise the CLI WIRING in :mod:`rigplane.cli._validate`:

* ``_overrides_dir`` resolves the shipped override directory;
* ``_apply_overrides`` auto-applies a per-profile override FILE when it carries
  ``"override": true``, threading a ``metadata.overrides`` audit;
* a profile with no override file is unchanged;
* a full template (no ``override`` flag) is NOT auto-applied;
* the safety invariant survives wiring (an unsafe tx.ptt relax is rejected and
  surfaced in ``metadata.overrides.rejected``);
* ``--no-overrides`` skips the merge;
* a malformed override file degrades (stderr warning) without raising.

All tests are dry-run / no-hardware and monkeypatch ``_overrides_dir`` to a
``tmp_path`` so they never depend on shipped files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rigplane.cli import _build_parser, _radio_validate, _validate

# The profile-driven model used across these tests; its generated matrix
# contains a gated ``tx.ptt`` entry (level 5, manual_required) and a
# ``filter_width.set`` entry (level 3, supported).
MODEL = "IC-7610"
PROFILE_ID = "icom_ic7610"


def _parse(argv: list[str]) -> Any:
    parser = _build_parser()
    return parser.parse_args(argv)


def _write_override(
    path: Path, entries: list[dict[str, Any]], *, flag: bool = True
) -> None:
    """Write a sparse override file (the v1 template shape + ``override`` flag)."""
    doc: dict[str, Any] = {
        "schema_version": 1,
        "radio": {"model": MODEL, "profile_id": PROFILE_ID},
        "entries": entries,
    }
    if flag:
        doc["override"] = True
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_override_file_is_merged_and_audited(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """A profile WITH an override file: a replace + an exclude both take effect.

    The merged template drives the artifact, and ``metadata.overrides`` records
    the applied/excluded check_ids.
    """
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[
            {"check_id": "filter_width.set", "summary": "PATCHED summary"},
            {"check_id": "rf_gain.set", "declaration": "excluded"},
        ],
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    overrides = artifact["metadata"].get("overrides")
    assert overrides is not None
    assert "filter_width.set" in overrides["applied"]
    assert "rf_gain.set" in overrides["excluded"]

    checks = {c["check_id"]: c for level in artifact["levels"] for c in level["checks"]}
    # Replaced summary reached the executed matrix.
    assert checks["filter_width.set"]["summary"] == "PATCHED summary"
    # Excluded entry is gone from the executed matrix.
    assert "rf_gain.set" not in checks


def test_no_override_file_leaves_matrix_unchanged(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """No override file for the profile → full generated matrix; no audit."""
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    assert artifact["metadata"].get("overrides") is None
    checks = {c["check_id"] for level in artifact["levels"] for c in level["checks"]}
    # filter_width.set is part of the full generated IC-7610 matrix.
    assert "filter_width.set" in checks


def test_shipped_x6200_override_excludes_dead_nr_nb_level_checks(
    capsys: Any,
) -> None:
    """The X6200 shipped override drops dead level registers, not toggles."""
    args = _parse(["--model", "X6200", "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    overrides = artifact["metadata"].get("overrides")
    assert overrides is not None
    assert "nr_level.set" in overrides["excluded"]
    assert "nb_level.set" in overrides["excluded"]

    checks = {c["check_id"] for level in artifact["levels"] for c in level["checks"]}
    assert "nr_level.set" not in checks
    assert "nb_level.set" not in checks
    assert "nr.set" in checks
    assert "nb.set" in checks
    assert "comp_level.set" in checks


def test_full_template_without_flag_is_not_applied(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """A file lacking ``"override": true`` is a full template, not a patch."""
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[{"check_id": "filter_width.set", "summary": "SHOULD NOT APPLY"}],
        flag=False,
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    assert artifact["metadata"].get("overrides") is None
    checks = {c["check_id"]: c for level in artifact["levels"] for c in level["checks"]}
    assert checks["filter_width.set"]["summary"] != "SHOULD NOT APPLY"


def test_safety_invariant_rejects_tx_relax(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """An override that tries to relax tx.ptt is refused and surfaced."""
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[
            {
                "check_id": "tx.ptt",
                "declaration": "supported",
                "tx_adjacent": False,
                "summary": "attempted relax",
            }
        ],
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    overrides = artifact["metadata"].get("overrides")
    assert overrides is not None
    assert "tx.ptt" in overrides["rejected"]

    checks = {c["check_id"]: c for level in artifact["levels"] for c in level["checks"]}
    # tx.ptt stays gated: the declaration was not relaxed to "supported".
    assert checks["tx.ptt"]["declaration"] != "supported"


def test_no_overrides_flag_skips_merge(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """``--no-overrides`` skips the merge even when a file exists."""
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[{"check_id": "rf_gain.set", "declaration": "excluded"}],
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json", "--no-overrides"])
    rc = _validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    assert artifact["metadata"].get("overrides") is None
    checks = {c["check_id"] for level in artifact["levels"] for c in level["checks"]}
    # Not excluded — the override was skipped.
    assert "rf_gain.set" in checks


def test_malformed_override_degrades_without_raising(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """A malformed override file → stderr warning, validation still succeeds."""
    (tmp_path / f"{PROFILE_ID}.json").write_text("{ not valid json ", encoding="utf-8")
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["--model", MODEL, "validate", "--json"])
    rc = _validate.run(args)
    assert rc == 0
    captured = capsys.readouterr()
    artifact = json.loads(captured.out)

    assert artifact["metadata"].get("overrides") is None
    assert "override" in captured.err.lower()
    # The un-merged full matrix is intact.
    checks = {c["check_id"] for level in artifact["levels"] for c in level["checks"]}
    assert "filter_width.set" in checks


def test_radio_validate_path_applies_overrides(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """The ``radio-validate <model>`` delegation also applies overrides."""
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[{"check_id": "filter_width.set", "summary": "VIA RADIO-VALIDATE"}],
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    args = _parse(["radio-validate", MODEL, "--json"])
    rc = _radio_validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)

    overrides = artifact["metadata"].get("overrides")
    assert overrides is not None
    assert "filter_width.set" in overrides["applied"]
    checks = {c["check_id"]: c for level in artifact["levels"] for c in level["checks"]}
    assert checks["filter_width.set"]["summary"] == "VIA RADIO-VALIDATE"


def test_radio_validate_no_overrides_flag_parses_and_skips_merge(
    tmp_path: Any, monkeypatch: Any, capsys: Any
) -> None:
    """``radio-validate <model> --no-overrides`` parses and skips the merge.

    Regression for the review gap: the ``--no-overrides`` escape hatch existed
    only on the ``validate`` parser, so ``radio-validate`` rejected it at argparse
    time (SystemExit 2). This asserts the flag parses through the real
    ``_build_parser()`` AND that the override merge is skipped — while the same
    override file IS applied when the flag is absent.
    """
    _write_override(
        tmp_path / f"{PROFILE_ID}.json",
        entries=[{"check_id": "rf_gain.set", "declaration": "excluded"}],
    )
    monkeypatch.setattr(_validate, "_overrides_dir", lambda: tmp_path)

    # Without the flag: the override file IS applied (rf_gain.set excluded).
    args_on = _parse(["radio-validate", MODEL, "--json"])
    rc_on = _radio_validate.run(args_on)
    assert rc_on == 0
    artifact_on = json.loads(capsys.readouterr().out)
    assert artifact_on["metadata"].get("overrides") is not None
    checks_on = {
        c["check_id"] for level in artifact_on["levels"] for c in level["checks"]
    }
    assert "rf_gain.set" not in checks_on

    # With the flag: it PARSES (no SystemExit) and the merge is skipped.
    args_off = _parse(["radio-validate", MODEL, "--json", "--no-overrides"])
    assert args_off.no_overrides is True
    rc_off = _radio_validate.run(args_off)
    assert rc_off == 0
    artifact_off = json.loads(capsys.readouterr().out)
    assert artifact_off["metadata"].get("overrides") is None
    checks_off = {
        c["check_id"] for level in artifact_off["levels"] for c in level["checks"]
    }
    # Not excluded — the override was skipped.
    assert "rf_gain.set" in checks_off
