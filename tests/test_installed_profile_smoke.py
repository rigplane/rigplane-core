"""Focused coverage for the installed-wheel radio-profile smoke gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / ".github/scripts/installed_profile_smoke.py"
)
RIGS_DIR = SCRIPT_PATH.parents[2] / "rigs"
BAND_PLANS_DIR = SCRIPT_PATH.parents[2] / "band-plans"


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


def test_installed_catalog_matches_source_profiles() -> None:
    from rigplane.profiles.rig_loader import discover_rigs

    smoke = _load_smoke_module()
    names = smoke._profile_resource_names(RIGS_DIR)
    assert smoke.validate_catalog(names) == names

    rigs = discover_rigs(RIGS_DIR)
    assert set(rigs) == {model for _, model in smoke.EXPECTED_PROFILES.values()}
    for profile_id, model in smoke.EXPECTED_PROFILES.values():
        assert rigs[model].id == profile_id


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


def _copy_band_plans(tmp_path: Path) -> Path:
    import shutil

    packaged = tmp_path / "rigplane" / "band-plans"
    shutil.copytree(BAND_PLANS_DIR, packaged)
    return packaged


def test_band_plan_proof_accepts_the_repository_plans(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    packaged = _copy_band_plans(tmp_path)

    counts = smoke.prove_band_plans_installed(packaged, packaged, tmp_path / "rigplane")

    assert set(counts) == set(smoke.BAND_PLAN_PROBES)
    assert all(count > 0 for count in counts.values())


def test_band_plan_proof_rejects_a_missing_plan_file(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    packaged = _copy_band_plans(tmp_path)
    (packaged / "iaru-r3.toml").unlink()

    with pytest.raises(smoke.SmokeFailure, match=r"missing:.*iaru-r3\.toml"):
        smoke.prove_band_plans_installed(packaged, packaged, tmp_path / "rigplane")


def test_band_plan_proof_rejects_a_wheel_without_the_directory(tmp_path: Path) -> None:
    smoke = _load_smoke_module()

    with pytest.raises(smoke.SmokeFailure, match=r"rigplane/band-plans is missing"):
        smoke.prove_band_plans_installed(
            tmp_path / "rigplane" / "band-plans", tmp_path, tmp_path / "rigplane"
        )


def test_band_plan_proof_rejects_a_loader_that_reads_elsewhere(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    packaged = _copy_band_plans(tmp_path)

    with pytest.raises(smoke.SmokeFailure, match="not the packaged directory"):
        smoke.prove_band_plans_installed(
            packaged, tmp_path / "elsewhere", tmp_path / "rigplane"
        )


def test_band_plan_proof_rejects_a_plan_without_vhf(tmp_path: Path) -> None:
    smoke = _load_smoke_module()
    packaged = _copy_band_plans(tmp_path)
    (packaged / "arrl-hf.toml").write_text(
        '[meta]\nlayer = "ham"\n\n[[band]]\nname = "40m"\nstart = 7000000\n'
        "end = 7300000\n\n  [[band.segment]]\n  start = 7000000\n"
        '  end = 7300000\n  mode = "cw"\n  label = "CW"\n',
        encoding="utf-8",
    )

    with pytest.raises(smoke.SmokeFailure, match="no ham segment at"):
        smoke.prove_band_plans_installed(packaged, packaged, tmp_path / "rigplane")
