"""Focused coverage for the installed-wheel radio-profile smoke gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".github/scripts/installed_profile_smoke.py"
)
RIGS_DIR = SCRIPT_PATH.parents[2] / "rigs"


def _load_smoke_module():
    spec = importlib.util.spec_from_file_location(
        "installed_profile_smoke", SCRIPT_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_catalog_contract_rejects_silent_profile_loss() -> None:
    smoke = _load_smoke_module()
    expected = tuple(smoke.EXPECTED_PROFILES)

    assert smoke.validate_catalog(expected) == expected
    with pytest.raises(smoke.SmokeFailure, match=r"missing=.*x6200\.toml"):
        smoke.validate_catalog(expected[:-1])


def test_catalog_contract_rejects_unreviewed_profile_addition() -> None:
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match=r"unexpected=.*future\.toml"):
        smoke.validate_catalog((*smoke.EXPECTED_PROFILES, "future.toml"))


def test_corrupt_profile_negative_proof_is_actionable() -> None:
    smoke = _load_smoke_module()

    diagnostic = smoke.prove_corrupt_profile_fails_closed(RIGS_DIR)

    assert "ftx1.toml" in diagnostic
    assert "failed" in diagnostic.lower() or "toml" in diagnostic.lower()


def test_ftx1_construction_seam_never_connects() -> None:
    smoke = _load_smoke_module()

    from rigplane.profiles.rig_loader import discover_rigs

    result = smoke.exercise_ftx1_construction(discover_rigs(RIGS_DIR))

    assert result == {
        "backend": "yaesu_cat",
        "connected": False,
        "model": "FTX-1",
        "profile_id": "yaesu_ftx1",
    }
