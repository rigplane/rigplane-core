"""Tests for ``rigplane diagnose`` CLI subcommand."""

from __future__ import annotations

import argparse
import json
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane import diagnostics as _diagnostics_pkg
from rigplane.cli import _build_parser, _diagnose
from rigplane.diagnostics import (
    BundleContext,
    BundleTooLarge,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    ReportSubmitted,
    UploadFailed,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def parsed(tmp_path: Path):
    """Return a callable that parses ``diagnose`` args with a tmp default output."""

    def _parse(extra: list[str] | None = None) -> argparse.Namespace:
        p = _build_parser()
        argv = ["diagnose", "--output", str(tmp_path / "bundle.zip")]
        if extra:
            argv.extend(extra)
        return p.parse_args(argv)

    return _parse


def _write_fake_bundle(path: Path, manifest: dict[str, Any] | None = None) -> Path:
    """Create a minimal valid zip with a manifest.json entry."""
    payload = (
        manifest
        if manifest is not None
        else {
            "schema_version": "rigplane-bundle-v2",
            "submission_id": "test-submission",
            "generated_at_unix": 1_700_000_000,
            "app": {"name": "rigplane", "version": "0.0.0"},
        }
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(payload))
        zf.writestr("logs/rigplane.log", "fake log line\n")
    return path


@pytest.fixture
def fake_build_bundle(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Patch ``build_bundle`` to write a tiny zip and capture the BundleContext."""
    captured: dict[str, Any] = {}

    def _fake(ctx: BundleContext, output_path: Path) -> Path:
        captured["ctx"] = ctx
        captured["output"] = output_path
        _write_fake_bundle(output_path)
        return output_path

    # Patch the source module — ``_diagnose._run_async`` does a local
    # ``from rigplane.diagnostics import build_bundle`` at call time, so the
    # local import resolves via ``sys.modules['rigplane.diagnostics']``.
    monkeypatch.setattr(_diagnostics_pkg, "build_bundle", _fake)
    return captured


@pytest.fixture
def fake_upload(monkeypatch: pytest.MonkeyPatch):
    """Patch ``upload_bundle`` with an AsyncMock that returns a ReportSubmitted."""
    mock = AsyncMock(
        return_value=ReportSubmitted(
            report_id="rpt-123",
            support_url="https://reports.msmsoft.net/r/rpt-123",
            received_at_unix=1_700_000_001,
            auth_class="anonymous",
        )
    )
    # Patch the source module — see fake_build_bundle for rationale.
    monkeypatch.setattr(_diagnostics_pkg, "upload_bundle", mock)
    return mock


@pytest.fixture
def force_tty(monkeypatch: pytest.MonkeyPatch):
    """Force ``_is_tty()`` to return True."""
    monkeypatch.setattr(_diagnose, "_is_tty", lambda: True)


@pytest.fixture
def force_non_tty(monkeypatch: pytest.MonkeyPatch):
    """Force ``_is_tty()`` to return False."""
    monkeypatch.setattr(_diagnose, "_is_tty", lambda: False)


# ---------------------------------------------------------------------------
# Parser-level
# ---------------------------------------------------------------------------


class TestParser:
    def test_diagnose_command_registered(self):
        p = _build_parser()
        args = p.parse_args(["diagnose"])
        assert args.command == "diagnose"
        assert args.upload is False
        assert args.no_confirm is False
        assert args.include == []
        assert args.exclude == []

    def test_repeatable_include_exclude(self):
        p = _build_parser()
        args = p.parse_args(
            [
                "diagnose",
                "--include",
                "logs",
                "--include",
                "system",
                "--exclude",
                "audio",
            ]
        )
        assert args.include == ["logs", "system"]
        assert args.exclude == ["audio"]


# ---------------------------------------------------------------------------
# Save-only path (no --upload)
# ---------------------------------------------------------------------------


class TestSaveOnly:
    def test_save_only_default(self, parsed, fake_build_bundle, fake_upload, capsys):
        args = parsed()
        rc = _diagnose.run(args)
        assert rc == 0
        captured = fake_build_bundle
        assert "ctx" in captured
        assert captured["output"].exists()
        fake_upload.assert_not_awaited()
        out = capsys.readouterr().out
        assert "Bundle saved to" in out

    def test_no_upload_never_prompts(
        self, parsed, fake_build_bundle, fake_upload, monkeypatch, force_tty
    ):
        prompt_mock = MagicMock(return_value="y")
        monkeypatch.setattr(_diagnose, "_prompt", prompt_mock)
        args = parsed()
        rc = _diagnose.run(args)
        assert rc == 0
        prompt_mock.assert_not_called()
        fake_upload.assert_not_awaited()

    def test_default_output_path_used_when_omitted(
        self, fake_build_bundle, fake_upload, monkeypatch, tmp_path
    ):
        # Redirect Path.home() so the default output lands in tmp_path.
        monkeypatch.setattr(_diagnose.Path, "home", classmethod(lambda cls: tmp_path))
        p = _build_parser()
        args = p.parse_args(["diagnose"])
        rc = _diagnose.run(args)
        assert rc == 0
        # The output path written by _fake build_bundle was passed to it.
        assert fake_build_bundle["output"].parent == tmp_path
        assert fake_build_bundle["output"].name.startswith("rigplane-report-")


# ---------------------------------------------------------------------------
# --upload + non-TTY
# ---------------------------------------------------------------------------


class TestUploadNonTty:
    def test_upload_no_tty_no_confirm_saves_locally(
        self, parsed, fake_build_bundle, fake_upload, force_non_tty, capsys
    ):
        args = parsed(["--upload"])
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_not_awaited()
        err = capsys.readouterr().err
        assert "TTY" in err
        assert "Saved locally" in err or "saved locally" in err.lower()

    def test_upload_no_tty_with_no_confirm_uploads(
        self, parsed, fake_build_bundle, fake_upload, force_non_tty
    ):
        args = parsed(["--upload", "--no-confirm"])
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_awaited_once()
        # Metadata payload (1st positional after bundle path) is the manifest.
        bundle_arg, metadata_arg = fake_upload.await_args.args
        assert bundle_arg == fake_build_bundle["output"]
        assert metadata_arg["schema_version"] == "rigplane-bundle-v2"


# ---------------------------------------------------------------------------
# --upload + TTY
# ---------------------------------------------------------------------------


class TestUploadTty:
    def test_upload_tty_default_n_saves_locally(
        self,
        parsed,
        fake_build_bundle,
        fake_upload,
        monkeypatch,
        force_tty,
        capsys,
    ):
        # Empty input — Enter — must NOT upload.
        monkeypatch.setattr(_diagnose, "_prompt", MagicMock(return_value=""))
        args = parsed(["--upload", "--description", "boom"])
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_not_awaited()
        err = capsys.readouterr().err
        assert "Saved locally" in err or "not uploading" in err.lower()

    def test_upload_tty_yes_uploads(
        self, parsed, fake_build_bundle, fake_upload, monkeypatch, force_tty
    ):
        # Provide all fields via flags so only the final consent prompt fires.
        monkeypatch.setattr(_diagnose, "_prompt", MagicMock(return_value="y"))
        args = parsed(
            [
                "--upload",
                "--description",
                "boom",
                "--issue-ref",
                "https://x",
                "--email",
                "u@x",
                "--callsign",
                "K1ABC",
            ]
        )
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_awaited_once()

    def test_upload_tty_yes_uppercase_uploads(
        self, parsed, fake_build_bundle, fake_upload, monkeypatch, force_tty
    ):
        monkeypatch.setattr(_diagnose, "_prompt", MagicMock(return_value="Y"))
        args = parsed(
            [
                "--upload",
                "--description",
                "boom",
                "--issue-ref",
                "https://x",
                "--email",
                "u@x",
                "--callsign",
                "K1ABC",
            ]
        )
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_awaited_once()

    def test_upload_tty_collects_missing_description(
        self, parsed, fake_build_bundle, fake_upload, monkeypatch, force_tty
    ):
        # Sequence: description, issue, email, callsign, then final consent.
        replies = iter(["my problem", "", "", "", "n"])
        prompt_mock = MagicMock(side_effect=lambda _msg: next(replies))
        monkeypatch.setattr(_diagnose, "_prompt", prompt_mock)
        args = parsed(["--upload"])
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_not_awaited()
        # The collected description should be on BundleContext.
        ctx = fake_build_bundle["ctx"]
        assert ctx.user_description == "my problem"
        assert ctx.issue_ref is None
        assert ctx.contact_email is None
        assert ctx.contact_callsign is None


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------


class TestErrorMapping:
    @pytest.fixture
    def upload_fixture(self, parsed, fake_build_bundle, monkeypatch, force_non_tty):
        """Helper returning a callable that runs `--upload --no-confirm` with a
        specific upload_bundle exception and yields (rc, stderr)."""

        def _run(exc: BaseException, capsys: pytest.CaptureFixture[str]):
            mock = AsyncMock(side_effect=exc)
            monkeypatch.setattr(_diagnostics_pkg, "upload_bundle", mock)
            args = parsed(["--upload", "--no-confirm"])
            rc = _diagnose.run(args)
            return rc, capsys.readouterr().err

        return _run

    def test_rate_limited_exit_4(self, upload_fixture, capsys):
        rc, err = upload_fixture(RateLimited(retry_after_seconds=30), capsys)
        assert rc == 4
        assert "30s" in err
        assert "Rate limit" in err

    def test_bundle_too_large_exit_5(self, upload_fixture, fake_build_bundle, capsys):
        rc, err = upload_fixture(BundleTooLarge("bundle too large"), capsys)
        assert rc == 5
        # Bundle size from on-disk file (not from exception).
        size = fake_build_bundle["output"].stat().st_size
        assert str(size) in err
        assert "--exclude" in err

    def test_forbidden_content_exit_6(self, upload_fixture, capsys):
        rc, err = upload_fixture(
            ForbiddenContent(pattern="ssh_key", message="blocked"), capsys
        )
        assert rc == 6
        assert "forbidden" in err.lower()

    def test_metadata_invalid_exit_7(self, upload_fixture, capsys):
        rc, err = upload_fixture(
            MetadataInvalid(field="submission_id", message="bad uuid"), capsys
        )
        assert rc == 7
        assert "Metadata" in err

    def test_network_error_exit_8(self, upload_fixture, capsys):
        rc, err = upload_fixture(NetworkError("connection refused"), capsys)
        assert rc == 8
        assert "Upload failed" in err

    def test_upload_failed_exit_8(self, upload_fixture, capsys):
        rc, err = upload_fixture(
            UploadFailed(status=500, code="boom", message="internal"), capsys
        )
        assert rc == 8
        assert "Upload failed" in err


# ---------------------------------------------------------------------------
# Context propagation
# ---------------------------------------------------------------------------


class TestContextPropagation:
    def test_bundle_id_propagates_as_submission_id(
        self, parsed, fake_build_bundle, fake_upload
    ):
        bundle_id = "11111111-2222-3333-4444-555555555555"
        args = parsed(["--bundle-id", bundle_id])
        rc = _diagnose.run(args)
        assert rc == 0
        ctx = fake_build_bundle["ctx"]
        assert ctx.submission_id == bundle_id

    def test_email_and_callsign_optin_only(
        self, parsed, fake_build_bundle, fake_upload
    ):
        # No --email / --callsign on the command line → fields stay None.
        args = parsed()
        _diagnose.run(args)
        ctx = fake_build_bundle["ctx"]
        assert ctx.contact_email is None
        assert ctx.contact_callsign is None

    def test_email_and_callsign_pass_through_when_set(
        self, parsed, fake_build_bundle, fake_upload
    ):
        args = parsed(["--email", "ham@x", "--callsign", "K1ABC"])
        _diagnose.run(args)
        ctx = fake_build_bundle["ctx"]
        assert ctx.contact_email == "ham@x"
        assert ctx.contact_callsign == "K1ABC"

    def test_random_submission_id_when_no_bundle_id(
        self, parsed, fake_build_bundle, fake_upload
    ):
        args = parsed()
        _diagnose.run(args)
        ctx = fake_build_bundle["ctx"]
        # Standard UUID v4 length.
        assert len(ctx.submission_id) == 36


# ---------------------------------------------------------------------------
# Filter warning
# ---------------------------------------------------------------------------


class TestFilterWarning:
    def test_include_emits_warning(
        self, parsed, fake_build_bundle, fake_upload, capsys
    ):
        args = parsed(["--include", "logs"])
        rc = _diagnose.run(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "filtering" in err.lower()
        # Bundle still produced.
        assert fake_build_bundle["output"].exists()

    def test_exclude_emits_warning(
        self, parsed, fake_build_bundle, fake_upload, capsys
    ):
        args = parsed(["--exclude", "audio"])
        rc = _diagnose.run(args)
        assert rc == 0
        err = capsys.readouterr().err
        assert "filtering" in err.lower()


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


class TestEndpoint:
    def test_endpoint_passed_through(
        self, parsed, fake_build_bundle, fake_upload, force_non_tty
    ):
        args = parsed(
            ["--upload", "--no-confirm", "--endpoint", "https://example.test/up"]
        )
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_awaited_once()
        # endpoint kwarg flows to upload_bundle.
        kwargs = fake_upload.await_args.kwargs
        assert kwargs.get("endpoint") == "https://example.test/up"

    def test_endpoint_resolved_from_env_var_passed_to_upload(
        self,
        parsed,
        fake_build_bundle,
        fake_upload,
        force_non_tty,
        monkeypatch: pytest.MonkeyPatch,
    ):
        """When --endpoint is not passed but RIGPLANE_REPORT_ENDPOINT is set,
        upload_bundle must receive the resolved URL — NOT None — so consent
        is obtained for the URL that actually receives the data.
        """
        monkeypatch.setenv("RIGPLANE_REPORT_ENDPOINT", "https://my-org.example/u")
        args = parsed(["--upload", "--no-confirm"])
        rc = _diagnose.run(args)
        assert rc == 0
        fake_upload.assert_awaited_once()
        kwargs = fake_upload.await_args.kwargs
        assert kwargs.get("endpoint") == "https://my-org.example/u"

    def test_default_endpoint_resolution(self, monkeypatch):
        monkeypatch.delenv("RIGPLANE_REPORT_ENDPOINT", raising=False)
        from rigplane.diagnostics import DEFAULT_ENDPOINT

        assert _diagnose._resolve_endpoint(None) == DEFAULT_ENDPOINT
        assert _diagnose._resolve_endpoint("https://x") == "https://x"

    def test_env_endpoint_used(self, monkeypatch):
        monkeypatch.setenv("RIGPLANE_REPORT_ENDPOINT", "https://env.test/u")
        assert _diagnose._resolve_endpoint(None) == "https://env.test/u"
