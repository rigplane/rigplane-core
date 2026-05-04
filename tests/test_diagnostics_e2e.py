"""End-to-end tests for the diagnostic-bundle pipeline (issue #1400).

Wires:

* ``upload_bundle`` direct flow
* CLI ``icom-lan diagnose --upload`` (subprocess)
* Web ``/api/v1/diagnose/{preview,send,save}`` (in-process handler)

against a localhost mock receiver implementing
``docs/contracts/diagnostic-bundle-v1.md``. The mock validates the
multipart shape, required metadata fields, and mints stable success /
typed-error responses.

Performance budget: <60s for the entire file on CI (acceptance §9).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
import zipfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from icom_lan.diagnostics import (
    BundleContext,
    BundleTooLarge,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    ReportSubmitted,
    upload_bundle,
)
from icom_lan.diagnostics import _discovery
from icom_lan.web.server import WebConfig, WebServer
from _diagnostics_mock_server import MockReceiver

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_contributors() -> Any:
    """Empty the built-in contributor set so in-process bundles stay tiny.

    Mirrors the fixture in ``test_web_diagnostics.py`` / ``test_diagnostics_bundle.py``.
    The CLI subprocess test runs in a separate Python process and is
    isolated via env-var overrides (HOME / XDG_*) instead.
    """
    _discovery._RUNTIME_REGISTERED.clear()
    saved_built_in = list(_discovery._BUILT_IN_CONTRIBUTORS)
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.extend(saved_built_in)


class _OkContributor:
    """Tiny built-in contributor — keeps the bundle non-empty but cheap."""

    name = "test-contrib"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "data.txt").write_text("e2e-test-payload", encoding="utf-8")


def _register_test_contributor() -> None:
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.append(_OkContributor)


@pytest_asyncio.fixture
async def mock_pair() -> AsyncIterator[tuple[MockReceiver, str]]:
    """Yield ``(receiver, upload_url)`` for a freshly started mock server."""
    receiver = MockReceiver()
    server = TestServer(receiver.app())
    await server.start_server()
    try:
        url = f"http://{server.host}:{server.port}/v1/diagnostics/upload"
        yield receiver, url
    finally:
        await server.close()


@pytest.fixture
def bundle_file(tmp_path: Path) -> Path:
    """Minimal zip file used as a stand-in bundle for direct-upload tests."""
    p = tmp_path / "bundle.zip"
    p.write_bytes(b"PK\x03\x04fake-e2e-bundle")
    return p


def _metadata_for(submission_id: str | None = None) -> dict[str, Any]:
    """Mock-required metadata: schema_version + submission_id + generated_at_unix."""
    return {
        "schema_version": "icom-lan-bundle-v1",
        "submission_id": submission_id or str(uuid.uuid4()),
        "generated_at_unix": int(time.time()),
    }


# ---------------------------------------------------------------------------
# Group 1 — upload_bundle direct flow
# ---------------------------------------------------------------------------


async def test_upload_succeeds_against_mock(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    metadata = _metadata_for()

    result = await upload_bundle(bundle_file, metadata, endpoint=url)

    assert isinstance(result, ReportSubmitted)
    assert result.report_id.startswith("rpt_")
    assert result.support_url.startswith("https://reports.example/r/")
    assert result.auth_class == "anonymous"
    assert result.received_at_unix > 0
    assert len(receiver.received) == 1
    rec = receiver.received[0]
    assert rec["metadata"]["submission_id"] == metadata["submission_id"]
    assert rec["bundle_size"] == bundle_file.stat().st_size


async def test_upload_with_header_provider_passes_auth(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair

    async def provider() -> dict[str, str]:
        return {"Authorization": "Bearer fake-token"}

    result = await upload_bundle(
        bundle_file, _metadata_for(), endpoint=url, header_provider=provider
    )

    assert result.auth_class == "authenticated"
    assert receiver.received[0]["headers"].get("Authorization") == "Bearer fake-token"


async def test_upload_idempotency_same_submission_id(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    sub_id = str(uuid.uuid4())

    r1 = await upload_bundle(bundle_file, _metadata_for(sub_id), endpoint=url)
    r2 = await upload_bundle(bundle_file, _metadata_for(sub_id), endpoint=url)

    assert r1.report_id == r2.report_id
    assert r1.support_url == r2.support_url
    # Mock recorded the bundle exactly once — second call deduped.
    assert len(receiver.received) == 1


async def test_upload_401_retry_with_header_refresh(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    receiver.response_mode = "401_once"
    provider_calls = {"n": 0}

    async def provider() -> dict[str, str]:
        provider_calls["n"] += 1
        return {"Authorization": f"Bearer token-{provider_calls['n']}"}

    result = await upload_bundle(
        bundle_file, _metadata_for(), endpoint=url, header_provider=provider
    )

    # Provider called twice: initial 401 + retry.
    assert provider_calls["n"] == 2
    assert result.auth_class == "authenticated"
    # Mock recorded exactly one successful submission (the 401 didn't record).
    assert len(receiver.received) == 1


# ---------------------------------------------------------------------------
# Group 2 — Typed error mapping
# ---------------------------------------------------------------------------


async def test_rate_limited_raises_typed(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    receiver.response_mode = "rate_limited"

    with pytest.raises(RateLimited) as ei:
        await upload_bundle(bundle_file, _metadata_for(), endpoint=url)
    assert ei.value.retry_after_seconds == 30


async def test_bundle_too_large_raises_typed(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    receiver.response_mode = "bundle_too_large"

    with pytest.raises(BundleTooLarge):
        await upload_bundle(bundle_file, _metadata_for(), endpoint=url)


async def test_forbidden_content_raises_typed(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    receiver.response_mode = "forbidden"

    with pytest.raises(ForbiddenContent) as ei:
        await upload_bundle(bundle_file, _metadata_for(), endpoint=url)
    assert ei.value.pattern == "test-pattern"


async def test_metadata_invalid_raises_typed(
    mock_pair: tuple[MockReceiver, str], bundle_file: Path
) -> None:
    receiver, url = mock_pair
    receiver.response_mode = "metadata_invalid"

    with pytest.raises(MetadataInvalid) as ei:
        await upload_bundle(bundle_file, _metadata_for(), endpoint=url)
    assert ei.value.field == "test"


async def test_network_error_on_closed_port(bundle_file: Path) -> None:
    # Port 1 is privileged + nothing listening: connection refused.
    with pytest.raises(NetworkError):
        await upload_bundle(
            bundle_file,
            _metadata_for(),
            endpoint="http://127.0.0.1:1/v1/diagnostics/upload",
        )


# ---------------------------------------------------------------------------
# Group 3 — CLI flow (subprocess)
# ---------------------------------------------------------------------------


def _isolated_env(tmp_path: Path, endpoint: str) -> dict[str, str]:
    """Copy os.environ + redirect HOME / XDG_* into ``tmp_path``.

    Without this the CLI subprocess scans the developer's real
    ``~/.config/icom-lan`` and ``~/Library/Caches/icom-lan`` via
    platformdirs and the test stops being hermetic.
    """
    iso_home = tmp_path / "home"
    iso_cfg = tmp_path / "xdg_config"
    iso_cache = tmp_path / "xdg_cache"
    for p in (iso_home, iso_cfg, iso_cache):
        p.mkdir(exist_ok=True)
    env = os.environ.copy()
    env["HOME"] = str(iso_home)
    env["XDG_CONFIG_HOME"] = str(iso_cfg)
    env["XDG_CACHE_HOME"] = str(iso_cache)
    env["ICOM_LAN_REPORT_ENDPOINT"] = endpoint
    return env


async def _run_cli(
    argv: list[str], env: dict[str, str], timeout: float
) -> tuple[int, str, str]:
    """Run the CLI as a subprocess on the asyncio loop (so the mock server keeps serving)."""
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "icom_lan",
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise
    return proc.returncode or 0, stdout_b.decode(), stderr_b.decode()


async def test_cli_diagnose_upload_against_mock_no_confirm(
    mock_pair: tuple[MockReceiver, str], tmp_path: Path
) -> None:
    receiver, url = mock_pair
    output_zip = tmp_path / "report.zip"
    env = _isolated_env(tmp_path, url)

    rc, stdout, stderr = await _run_cli(
        [
            "diagnose",
            "--upload",
            "--no-confirm",
            "--endpoint",
            url,
            "--description",
            "e2e test",
            "--output",
            str(output_zip),
        ],
        env=env,
        timeout=15.0,
    )

    assert rc == 0, f"CLI exit={rc}\nstdout={stdout}\nstderr={stderr}"
    assert "Uploaded." in stdout, stdout
    assert "Support URL:" in stdout, stdout
    assert "https://reports.example/r/" in stdout, stdout
    assert output_zip.exists()
    assert len(receiver.received) == 1
    rec = receiver.received[0]
    # CLI manifest must include the required fields.
    assert rec["metadata"]["schema_version"] == "icom-lan-bundle-v1"
    assert "submission_id" in rec["metadata"]


async def test_cli_diagnose_save_only_no_upload(
    mock_pair: tuple[MockReceiver, str], tmp_path: Path
) -> None:
    receiver, url = mock_pair
    output_zip = tmp_path / "saved.zip"
    env = _isolated_env(tmp_path, url)

    rc, stdout, stderr = await _run_cli(
        [
            "diagnose",
            "--no-confirm",
            "--output",
            str(output_zip),
        ],
        env=env,
        timeout=15.0,
    )

    assert rc == 0, f"CLI exit={rc}\nstderr={stderr}"
    assert output_zip.exists()
    assert "Bundle saved to:" in stdout
    # No upload at all.
    assert receiver.received == []


# ---------------------------------------------------------------------------
# Group 4 — Web flow (in-process handler, mirrors test_web_diagnostics.py)
# ---------------------------------------------------------------------------


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        if name == "peername":
            return ("127.0.0.1", 55555)
        return default

    def is_closing(self) -> bool:
        return self.closed

    @property
    def response_status(self) -> int:
        line = self.buffer.split(b"\r\n", 1)[0]
        return int(line.split(b" ")[1])

    @property
    def response_headers(self) -> dict[str, str]:
        head = self.buffer.split(b"\r\n\r\n", 1)[0]
        out: dict[str, str] = {}
        for line in head.split(b"\r\n")[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                out[k.decode().strip().lower()] = v.decode().strip()
        return out

    @property
    def response_body(self) -> bytes:
        return bytes(self.buffer.split(b"\r\n\r\n", 1)[1])

    @property
    def response_json(self) -> dict[str, Any]:
        return json.loads(self.response_body)


def _make_reader(data: bytes = b"") -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    if data:
        reader.feed_data(data)
    reader.feed_eof()
    return reader


def _post_headers(payload: bytes, **extra: str) -> dict[str, str]:
    h = {"content-length": str(len(payload))}
    h.update(extra)
    return h


def _stub_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = tmp_path / "config"
    log = tmp_path / "log"
    cfg.mkdir(exist_ok=True)
    log.mkdir(exist_ok=True)
    monkeypatch.setattr(WebServer, "_resolve_diagnostic_dirs", lambda self: (cfg, log))


async def _do_preview(
    srv: WebServer, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    body = json.dumps(payload or {}).encode()
    writer = _FakeWriter()
    await srv._handle_diagnose_preview(
        writer,  # type: ignore[arg-type]
        headers=_post_headers(body),
        reader=_make_reader(body),
    )
    assert writer.response_status == 200, writer.response_body
    return writer.response_json


@pytest.mark.asyncio
async def test_web_diagnose_full_flow(
    mock_pair: tuple[MockReceiver, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receiver, url = mock_pair
    # upload_bundle, called from inside handle_send, resolves the endpoint
    # via ICOM_LAN_REPORT_ENDPOINT.
    monkeypatch.setenv("ICOM_LAN_REPORT_ENDPOINT", url)
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)

    srv = WebServer(radio=None, config=WebConfig(host="127.0.0.1", port=8080))
    try:
        preview = await _do_preview(srv, {"description": "web e2e"})
        # The preview's announced endpoint must be the mock URL too.
        assert preview["endpoint_url"] == url

        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(body, **{"x-diagnostic-csrf": preview["csrf_token"]}),
            reader=_make_reader(body),
        )
        assert writer.response_status == 200, writer.response_body
        resp = writer.response_json
        assert resp["report_id"].startswith("rpt_")
        assert resp["support_url"].startswith("https://reports.example/r/")

        # Mock confirmed receipt.
        assert len(receiver.received) == 1
        meta = receiver.received[0]["metadata"]
        assert meta["schema_version"] == "icom-lan-bundle-v1"
        assert "submission_id" in meta
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_web_diagnose_save_returns_zip_bytes_no_mock_traffic(
    mock_pair: tuple[MockReceiver, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receiver, url = mock_pair
    monkeypatch.setenv("ICOM_LAN_REPORT_ENDPOINT", url)
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)

    srv = WebServer(radio=None, config=WebConfig(host="127.0.0.1", port=8080))
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_save(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(body, **{"x-diagnostic-csrf": preview["csrf_token"]}),
            reader=_make_reader(body),
        )
        assert writer.response_status == 200
        assert writer.response_headers["content-type"] == "application/zip"

        # Body parses as a real ZIP.
        zip_path = tmp_path / "downloaded.zip"
        zip_path.write_bytes(writer.response_body)
        with zipfile.ZipFile(zip_path) as zf:
            assert "manifest.json" in zf.namelist()

        # Save MUST NOT touch the mock.
        assert receiver.received == []
    finally:
        await srv._diagnostics.stop()


def test_mock_receiver_constructs() -> None:
    """Quick sanity check — the mock initialises with empty state."""
    r = MockReceiver()
    assert r.received == []
    assert r.response_mode == "success"
