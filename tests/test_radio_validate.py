"""Tests for the ``rigplane radio-validate <model>`` CLI verb (MOR-204, 204a).

``radio-validate`` is a thin, profile-driven wrapper over the existing
``validate`` run path (ADR D7): it adds a positional ``model``, reuses the
same flags, and adds ``--write-template`` to dump the generated in-memory
matrix as JSON. All tests here are dry-run / no-hardware: the generation
path runs entirely in-process.

The tests drive the *real* registered parser via ``_build_parser`` so the
wiring (dispatch routing + ``_COMMAND_NAMES`` + subparser registration) is
exercised, not just the module in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rigplane.cli import _build_parser, _radio_validate, _validate
from rigplane.validation import load_template


def _parse(argv: list[str]) -> Any:
    """Parse argv through the real top-level parser."""
    parser = _build_parser()
    return parser.parse_args(argv)


def test_radio_validate_dry_run_native_emits_artifact(capsys: Any) -> None:
    """``radio-validate X6200`` (dry-run, --json) exits 0, native artifact."""
    args = _parse(["radio-validate", "X6200", "--json"])
    rc = _radio_validate.run(args)
    assert rc == 0
    out = capsys.readouterr().out
    artifact = json.loads(out)
    assert artifact["metadata"]["provider"] == "native"
    # At least one check entry present across the levels.
    entries = [c for level in artifact["levels"] for c in level["checks"]]
    assert entries


def test_radio_validate_dry_run_hamlib_provider(capsys: Any) -> None:
    """``radio-validate X6200 --provider hamlib`` dry-run exits 0.

    Hamlib dump_caps may be unavailable in this environment (degraded); the
    generation path must still succeed and exit 0.
    """
    args = _parse(["radio-validate", "X6200", "--provider", "hamlib", "--json"])
    rc = _radio_validate.run(args)
    assert rc == 0


def test_radio_validate_missing_model_errors(capsys: Any) -> None:
    """No positional model and no global --model → exit 2 with a helpful error."""
    args = _parse(["radio-validate", "--json"])
    rc = _radio_validate.run(args)
    assert rc == 2
    err = capsys.readouterr().err
    assert "model" in err.lower()


def test_radio_validate_global_model_fallback(capsys: Any) -> None:
    """A global --model (before the subcommand) is used when the positional is omitted."""
    args = _parse(["--model", "X6200", "radio-validate", "--json"])
    rc = _radio_validate.run(args)
    assert rc == 0
    artifact = json.loads(capsys.readouterr().out)
    assert artifact["metadata"]["provider"] == "native"


def test_radio_validate_write_template(tmp_path: Path) -> None:
    """``--write-template PATH`` writes a valid MatrixTemplate JSON, exit 0, no hardware."""
    out = tmp_path / "m.json"
    args = _parse(["radio-validate", "X6200", "--write-template", str(out)])
    rc = _radio_validate.run(args)
    assert rc == 0
    assert out.exists()
    template = load_template(out)
    assert template.entries


def test_radio_validate_unknown_model_errors(capsys: Any) -> None:
    """An unknown model name → exit 2."""
    args = _parse(["radio-validate", "BOGUS_MODEL", "--json"])
    rc = _radio_validate.run(args)
    assert rc == 2


def test_radio_validate_delegates_to_validate(monkeypatch: Any) -> None:
    """The wrapper delegates to ``_validate.run`` with model set and template None."""
    captured: dict[str, Any] = {}

    def fake_run(args: Any) -> int:
        captured["model"] = getattr(args, "model", None)
        captured["template"] = getattr(args, "template", "MISSING")
        captured["provider"] = getattr(args, "provider", None)
        return 0

    monkeypatch.setattr(_validate, "run", fake_run)

    args = _parse(["radio-validate", "X6200"])
    rc = _radio_validate.run(args)

    assert rc == 0
    assert captured["model"] == "X6200"
    assert captured["template"] is None
    assert captured["provider"] == "native"


def test_radio_validate_command_registered() -> None:
    """The verb is wired into the parser and routes its own namespace."""
    args = _parse(["radio-validate", "X6200"])
    assert args.command == "radio-validate"
    assert args.provider == "native"
