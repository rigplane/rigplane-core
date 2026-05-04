"""Tests for ``icom_lan.diagnostics.bundle.build_bundle``."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from icom_lan.diagnostics import (
    BundleContext,
    build_bundle,
    register,
)
from icom_lan.diagnostics import _discovery


@pytest.fixture(autouse=True)
def _isolate_contributors() -> Any:
    """Isolate bundle tests from runtime-registered AND built-in contributors."""
    _discovery._RUNTIME_REGISTERED.clear()
    saved_built_in = list(_discovery._BUILT_IN_CONTRIBUTORS)
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.extend(saved_built_in)


def _make_ctx(**overrides: Any) -> BundleContext:
    base: dict[str, Any] = {
        "radio": None,
        "config_dir": Path("/tmp/cfg"),
        "log_dir": Path("/tmp/log"),
        "user_description": None,
        "issue_ref": None,
        "contact_email": None,
        "contact_callsign": None,
        "submission_id": "sub-123",
        "generated_at_unix": 1700000000,
    }
    base.update(overrides)
    return BundleContext(**base)


class _OkContributor:
    name = "ok"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "data.json").write_text("{}", encoding="utf-8")


class _OkContributor2:
    name = "ok2"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "info.txt").write_text("hello", encoding="utf-8")


class _AlphaContributor:
    name = "alpha"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "data.json").write_text("{}", encoding="utf-8")


class _FailingContributor:
    name = "fail"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        raise RuntimeError("boom")


class _FailingContributor2:
    name = "fail2"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        raise RuntimeError("kaput")


class _ValueErrorContributor:
    name = "verr"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        raise ValueError("kaboom")


class _RadioNoneAssertContributor:
    name = "radio-none"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        assert ctx.radio is None
        (output_dir / "ok.txt").write_text("ok", encoding="utf-8")


class _FlakyContributor:
    """Writes a file then raises — exercises partial-output cleanup."""

    name = "flaky"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "partial.txt").write_text("half-written", encoding="utf-8")
        raise RuntimeError("died after partial write")


def _read_manifest(zip_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(zip_path) as zf:
        return json.loads(zf.read("manifest.json"))


def test_build_bundle_returns_existing_zip(tmp_path: Path) -> None:
    register(_OkContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    assert result.exists()
    assert result == output_path.resolve()
    with zipfile.ZipFile(result) as zf:
        # If invalid, badzipfile would have raised on open
        assert "manifest.json" in zf.namelist()


def test_full_success_records_contributors(tmp_path: Path) -> None:
    register(_OkContributor)
    register(_OkContributor2)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)
    contributors = manifest.get("contributors", [])
    names = {c["name"] for c in contributors}
    assert {"ok", "ok2"}.issubset(names)
    assert "warnings" not in manifest


def test_partial_failure_records_warning(tmp_path: Path) -> None:
    register(_OkContributor)
    register(_FailingContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)
    contributor_names = {c["name"] for c in manifest.get("contributors", [])}
    assert "ok" in contributor_names

    warnings = manifest.get("warnings", [])
    matching = [w for w in warnings if w["contributor"] == "fail"]
    assert len(matching) == 1
    msg = matching[0]["message"]
    assert "RuntimeError" in msg
    assert "boom" in msg


def test_all_fail_still_produces_zip(tmp_path: Path) -> None:
    register(_FailingContributor)
    register(_FailingContributor2)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    assert result.exists()
    manifest = _read_manifest(result)
    warning_names = {w["contributor"] for w in manifest.get("warnings", [])}
    assert {"fail", "fail2"}.issubset(warning_names)

    contributor_names = {c["name"] for c in manifest.get("contributors", [])}
    assert "fail" not in contributor_names
    assert "fail2" not in contributor_names


def test_no_radio_does_not_break_assembly(tmp_path: Path) -> None:
    register(_RadioNoneAssertContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(radio=None), output_path)

    assert result.exists()
    manifest = _read_manifest(result)
    contributor_names = {c["name"] for c in manifest.get("contributors", [])}
    assert "radio-none" in contributor_names


def test_required_fields_present_no_nulls(tmp_path: Path) -> None:
    register(_OkContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)

    assert manifest["schema_version"]
    assert manifest["submission_id"]
    assert manifest["generated_at_unix"]
    assert manifest["app"]["name"]
    assert manifest["app"]["version"]
    assert manifest["platform"]["os"]
    assert manifest["platform"]["arch"]

    def _walk(value: Any) -> None:
        if isinstance(value, dict):
            for v in value.values():
                _walk(v)
        elif isinstance(value, list):
            for v in value:
                _walk(v)
        else:
            assert value is not None

    _walk(manifest)


def test_optional_fields_omitted_when_absent(tmp_path: Path) -> None:
    register(_OkContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)
    assert "user_description" not in manifest
    assert "issue_ref" not in manifest
    assert "contact" not in manifest
    assert manifest["schema_version"] == "icom-lan-bundle-v1"


def test_optional_fields_present_when_set(tmp_path: Path) -> None:
    register(_OkContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(
        _make_ctx(
            user_description="hi",
            issue_ref="https://x",
            contact_email="a@b",
            contact_callsign="K1ABC",
        ),
        output_path,
    )

    manifest = _read_manifest(result)
    assert manifest["user_description"] == "hi"
    assert manifest["issue_ref"] == "https://x"
    assert manifest["contact"] == {"email": "a@b", "callsign": "K1ABC"}


def test_zip_layout_has_manifest_at_root_and_contributor_subdirs(
    tmp_path: Path,
) -> None:
    register(_AlphaContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    with zipfile.ZipFile(result) as zf:
        names = set(zf.namelist())

    assert "manifest.json" in names
    assert "alpha/data.json" in names


def test_record_warning_message_uses_repr(tmp_path: Path) -> None:
    register(_ValueErrorContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)
    warnings = manifest.get("warnings", [])
    assert warnings
    msg = warnings[0]["message"]
    assert "ValueError" in msg
    assert "kaboom" in msg


def test_partial_failure_cleans_up_partial_output(tmp_path: Path) -> None:
    """A contributor that writes files then raises must NOT leak those files into the zip.

    Regression for Codex review on PR #1408: previously, a per-contributor
    failure recorded a manifest warning but left the partial files in the
    staging directory, so they ended up in the bundle alongside the warning.
    """
    register(_FlakyContributor)
    output_path = tmp_path / "report.zip"
    result = build_bundle(_make_ctx(), output_path)

    manifest = _read_manifest(result)
    # Warning was recorded.
    warnings = manifest.get("warnings", [])
    matching = [w for w in warnings if w["contributor"] == "flaky"]
    assert len(matching) == 1
    # And NO partial file leaked into the zip.
    with zipfile.ZipFile(result) as zf:
        names = zf.namelist()
    assert not any(n.startswith("flaky/") for n in names), (
        f"partial files leaked: {names}"
    )
