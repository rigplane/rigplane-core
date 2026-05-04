"""Tests for the diagnostic upload web endpoints (issue #1396).

Covers ``/api/v1/diagnose/preview``, ``send``, ``save`` and
``DELETE /api/v1/diagnose/preview/{id}``.

We exercise the handlers via the ``WebServer._handle_diagnose_*`` entry
points (mirroring how ``test_web_post_body_cap.py`` invokes
``_handle_band_plan_config``). The route table itself is exercised
through ``WebServer._handle_http`` for the API-auth-inheritance test.
"""

from __future__ import annotations

import asyncio
import json
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from icom_lan.diagnostics import (
    BundleContext,
    DiagnosticUploadError,
    RateLimited,
    ReportSubmitted,
)
from icom_lan.diagnostics import _discovery
from icom_lan.web.handlers.diagnostics import (
    PREVIEW_TTL_SECONDS,
    DiagnosticsHandler,
    check_origin_or_loopback,
)
from icom_lan.web.server import WebConfig, WebServer


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_contributors() -> Any:
    """Isolate from runtime-registered AND built-in contributors.

    Mirrors the fixture in ``test_diagnostics_bundle.py`` so the bundle
    is empty (apart from manifest) and unit tests don't read the user's
    config / log dirs.
    """
    _discovery._RUNTIME_REGISTERED.clear()
    saved_built_in = list(_discovery._BUILT_IN_CONTRIBUTORS)
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    yield
    _discovery._RUNTIME_REGISTERED.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    _discovery._BUILT_IN_CONTRIBUTORS.extend(saved_built_in)


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


def _make_server(host: str = "127.0.0.1", auth_token: str = "") -> WebServer:
    cfg = WebConfig(host=host, port=8080, auth_token=auth_token)
    srv = WebServer(radio=None, config=cfg)
    return srv


def _post_headers(payload: bytes, **extra: str) -> dict[str, str]:
    h = {"content-length": str(len(payload))}
    h.update(extra)
    return h


class _OkContributor:
    """Minimal contributor that drops one file in the bundle."""

    name = "test-contrib"

    def contribute(self, ctx: BundleContext, output_dir: Path) -> None:
        (output_dir / "data.txt").write_text("hello", encoding="utf-8")


def _register_test_contributor() -> None:
    _discovery._BUILT_IN_CONTRIBUTORS.clear()
    # Discovery instantiates the class internally; pass the class itself.
    _discovery._BUILT_IN_CONTRIBUTORS.append(_OkContributor)


def _stub_dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Replace WebServer._resolve_diagnostic_dirs with a tmp-path version."""
    cfg = tmp_path / "config"
    log = tmp_path / "log"
    cfg.mkdir(exist_ok=True)
    log.mkdir(exist_ok=True)
    monkeypatch.setattr(
        WebServer,
        "_resolve_diagnostic_dirs",
        lambda self: (cfg, log),
    )


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


# ---------------------------------------------------------------------------
# Origin helper
# ---------------------------------------------------------------------------


def test_origin_helper_loopback_skip() -> None:
    allowed, reason = check_origin_or_loopback(None, "127.0.0.1", 8080)
    assert allowed is True
    assert "loopback" in reason


def test_origin_helper_match_exact() -> None:
    allowed, _ = check_origin_or_loopback(
        "http://192.168.1.5:8080", "192.168.1.5", 8080
    )
    assert allowed is True


def test_origin_helper_localhost_alias() -> None:
    allowed, _ = check_origin_or_loopback("http://localhost:8080", "192.168.1.5", 8080)
    assert allowed is True


def test_origin_helper_mismatch() -> None:
    allowed, reason = check_origin_or_loopback(
        "http://evil.example", "192.168.1.5", 8080
    )
    assert allowed is False
    assert reason == "origin_mismatch"


def test_origin_helper_missing() -> None:
    allowed, reason = check_origin_or_loopback(None, "192.168.1.5", 8080)
    assert allowed is False
    assert reason == "origin_missing"


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_preview_returns_csrf_and_manifest(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POST /preview yields preview_id, csrf_token, manifest, files, sizes."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        result = await _do_preview(srv, {"description": "test"})
        assert "preview_id" in result and result["preview_id"]
        assert "csrf_token" in result and result["csrf_token"]
        # Distinct random strings.
        assert result["preview_id"] != result["csrf_token"]
        assert isinstance(result["manifest"], dict)
        assert result["manifest"]["schema_version"] == "icom-lan-bundle-v1"
        # Bundle is non-empty (manifest.json + contributor file).
        assert isinstance(result["files"], list) and len(result["files"]) >= 2
        assert all(
            isinstance(f, dict) and "path" in f and "size" in f for f in result["files"]
        )
        assert result["total_size_bytes"] > 0
        assert "endpoint_url" in result and result["endpoint_url"].startswith("http")
        assert isinstance(result["redactions_applied"], list)
        assert "paths" in result["redactions_applied"]
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_send_requires_csrf_header(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """POST /send without X-Diagnostic-CSRF -> 403."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(body),  # no x-diagnostic-csrf
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "csrf_missing"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_send_with_valid_csrf_uploads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Valid CSRF + consent → mock upload_bundle is called and result returned."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        report = ReportSubmitted(
            report_id="r-42",
            support_url="https://example/r-42",
            received_at_unix=1730000000,
            auth_class="anonymous",
        )
        with patch(
            "icom_lan.web.handlers.diagnostics.upload_bundle",
            new=AsyncMock(return_value=report),
        ) as mock_upload:
            body = json.dumps(
                {"preview_id": preview["preview_id"], "consent": True}
            ).encode()
            writer = _FakeWriter()
            await srv._handle_diagnose_send(
                writer,  # type: ignore[arg-type]
                headers=_post_headers(
                    body, **{"x-diagnostic-csrf": preview["csrf_token"]}
                ),
                reader=_make_reader(body),
            )
            assert writer.response_status == 200, writer.response_body
            assert writer.response_json["report_id"] == "r-42"
            assert writer.response_json["support_url"] == "https://example/r-42"
            mock_upload.assert_awaited_once()
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_send_csrf_single_use(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A second send with the same CSRF after a successful upload → 403."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        report = ReportSubmitted("r1", "https://x", 0, "anonymous")
        with patch(
            "icom_lan.web.handlers.diagnostics.upload_bundle",
            new=AsyncMock(return_value=report),
        ):
            body = json.dumps(
                {"preview_id": preview["preview_id"], "consent": True}
            ).encode()
            writer1 = _FakeWriter()
            await srv._handle_diagnose_send(
                writer1,  # type: ignore[arg-type]
                headers=_post_headers(
                    body, **{"x-diagnostic-csrf": preview["csrf_token"]}
                ),
                reader=_make_reader(body),
            )
            assert writer1.response_status == 200

            writer2 = _FakeWriter()
            await srv._handle_diagnose_send(
                writer2,  # type: ignore[arg-type]
                headers=_post_headers(
                    body, **{"x-diagnostic-csrf": preview["csrf_token"]}
                ),
                reader=_make_reader(body),
            )
            assert writer2.response_status == 403
            assert writer2.response_json["error"] == "csrf_missing"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_save_returns_zip_bytes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Save returns the bundle as application/zip with attachment header."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
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
        headers = writer.response_headers
        assert headers["content-type"] == "application/zip"
        assert "attachment" in headers["content-disposition"]
        assert "icom-lan-report-" in headers["content-disposition"]
        # Body parses as a real ZIP.
        zip_path = tmp_path / "downloaded.zip"
        zip_path.write_bytes(writer.response_body)
        with zipfile.ZipFile(zip_path) as zf:
            names = zf.namelist()
        assert "manifest.json" in names
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_save_csrf_reusable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Save can be called twice with the same CSRF (it's not consumed)."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()

        writer1 = _FakeWriter()
        await srv._handle_diagnose_save(
            writer1,  # type: ignore[arg-type]
            headers=_post_headers(body, **{"x-diagnostic-csrf": preview["csrf_token"]}),
            reader=_make_reader(body),
        )
        writer2 = _FakeWriter()
        await srv._handle_diagnose_save(
            writer2,  # type: ignore[arg-type]
            headers=_post_headers(body, **{"x-diagnostic-csrf": preview["csrf_token"]}),
            reader=_make_reader(body),
        )
        assert writer1.response_status == 200
        assert writer2.response_status == 200
        assert writer1.response_body == writer2.response_body
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_delete_cleans_up_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DELETE removes the session and unlinks the bundle file."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        sess = srv._diagnostics._sessions[preview["preview_id"]]
        bundle_path = sess.bundle_path
        assert bundle_path.exists()

        writer = _FakeWriter()
        await srv._handle_diagnose_delete(
            writer,  # type: ignore[arg-type]
            headers={"x-diagnostic-csrf": preview["csrf_token"]},
            preview_id=preview["preview_id"],
        )
        assert writer.response_status == 204
        assert preview["preview_id"] not in srv._diagnostics._sessions
        assert not bundle_path.exists()
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_cross_origin_send_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-loopback bind + foreign Origin → 403 origin_mismatch."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="192.168.1.10")
    try:
        preview = await _do_preview(srv)
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{
                    "x-diagnostic-csrf": preview["csrf_token"],
                    "origin": "http://evil.example",
                },
            ),
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "origin_mismatch"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_loopback_bind_skips_origin_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bind 127.0.0.1 + missing Origin → request is allowed."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="127.0.0.1")
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_save(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body, **{"x-diagnostic-csrf": preview["csrf_token"]}
            ),  # no origin
            reader=_make_reader(body),
        )
        assert writer.response_status == 200
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_loopback_bind_still_requires_csrf(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bind 127.0.0.1, no CSRF header → 403 csrf_missing."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="127.0.0.1")
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_save(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(body),  # no CSRF
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "csrf_missing"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_preview_expiry_sweeps_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When a session is expired, the sweep_once helper removes it."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        sess = srv._diagnostics._sessions[preview["preview_id"]]
        bundle_path = sess.bundle_path

        # Backdate the session past the TTL.
        sess.created_at_unix -= PREVIEW_TTL_SECONDS + 60

        removed = await srv._diagnostics._sweep_once()
        assert removed >= 1
        assert preview["preview_id"] not in srv._diagnostics._sessions
        assert not bundle_path.exists()
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_send_after_expiry_returns_404(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """send on an expired session → 404 preview_not_found."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        sess = srv._diagnostics._sessions[preview["preview_id"]]
        sess.created_at_unix -= PREVIEW_TTL_SECONDS + 60
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(body, **{"x-diagnostic-csrf": preview["csrf_token"]}),
            reader=_make_reader(body),
        )
        assert writer.response_status == 404
        assert writer.response_json["error"] == "preview_not_found"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_api_auth_inherited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When auth_token is configured, /api/v1/diagnose/* requires Bearer auth."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(auth_token="t0p-s3cret")
    try:
        # No auth header → 401.
        body = json.dumps({"description": "test"}).encode()
        writer = _FakeWriter()
        await srv._handle_http(
            writer,  # type: ignore[arg-type]
            "POST",
            "/api/v1/diagnose/preview",
            _post_headers(body),
            _make_reader(body),
            None,
        )
        assert writer.response_status == 401

        # With correct Bearer → 200.
        body = json.dumps({"description": "test"}).encode()
        writer2 = _FakeWriter()
        await srv._handle_http(
            writer2,  # type: ignore[arg-type]
            "POST",
            "/api/v1/diagnose/preview",
            _post_headers(body, authorization="Bearer t0p-s3cret"),
            _make_reader(body),
            None,
        )
        assert writer2.response_status == 200
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_send_translates_rate_limited(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """upload_bundle raising RateLimited → 429 with retry_after_seconds."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        with patch(
            "icom_lan.web.handlers.diagnostics.upload_bundle",
            new=AsyncMock(side_effect=RateLimited(retry_after_seconds=42)),
        ):
            body = json.dumps(
                {"preview_id": preview["preview_id"], "consent": True}
            ).encode()
            writer = _FakeWriter()
            await srv._handle_diagnose_send(
                writer,  # type: ignore[arg-type]
                headers=_post_headers(
                    body, **{"x-diagnostic-csrf": preview["csrf_token"]}
                ),
                reader=_make_reader(body),
            )
            assert writer.response_status == 429
            assert writer.response_json["error"] == "rate_limited"
            assert writer.response_json.get("retry_after_seconds") == 42
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_delete_with_wrong_csrf_blocked(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DELETE with mismatched CSRF → 403 and session retained."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server()
    try:
        preview = await _do_preview(srv)
        writer = _FakeWriter()
        await srv._handle_diagnose_delete(
            writer,  # type: ignore[arg-type]
            headers={"x-diagnostic-csrf": "wrong-token"},
            preview_id=preview["preview_id"],
        )
        assert writer.response_status == 403
        # Session is still there.
        assert preview["preview_id"] in srv._diagnostics._sessions
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_wildcard_bind_with_host_header_allows_matching_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Server bound to 0.0.0.0 + matching Host/Origin → allowed."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="0.0.0.0")
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_save(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{
                    "x-diagnostic-csrf": preview["csrf_token"],
                    "host": "192.168.1.5:8080",
                    "origin": "http://192.168.1.5:8080",
                },
            ),
            reader=_make_reader(body),
        )
        assert writer.response_status == 200, writer.response_body
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_wildcard_bind_with_host_header_blocks_mismatched_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Server bound to 0.0.0.0 + Host=192.168.1.5 + Origin=evil → 403."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="0.0.0.0")
    try:
        preview = await _do_preview(srv)
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{
                    "x-diagnostic-csrf": preview["csrf_token"],
                    "host": "192.168.1.5:8080",
                    "origin": "http://evil.example",
                },
            ),
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "origin_mismatch"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_wildcard_bind_loopback_host_skips_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Server bound to 0.0.0.0 + Host=127.0.0.1 (no Origin) → allowed."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="0.0.0.0")
    try:
        preview = await _do_preview(srv)
        body = json.dumps({"preview_id": preview["preview_id"]}).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_save(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{
                    "x-diagnostic-csrf": preview["csrf_token"],
                    "host": "127.0.0.1:8080",
                },
            ),  # no origin
            reader=_make_reader(body),
        )
        assert writer.response_status == 200, writer.response_body
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_wildcard_bind_no_host_header_blocks(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Server bound to 0.0.0.0 with no Host header → 403 host_header_missing."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="0.0.0.0")
    try:
        preview = await _do_preview(srv)
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{
                    "x-diagnostic-csrf": preview["csrf_token"],
                    "origin": "http://192.168.1.5:8080",
                },
            ),  # no host
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "host_header_missing"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_origin_missing_returns_distinct_code(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Non-loopback bind with no Origin → 403 with code 'origin_missing'."""
    _register_test_contributor()
    _stub_dirs(monkeypatch, tmp_path)
    srv = _make_server(host="192.168.1.10")
    try:
        preview = await _do_preview(srv)
        body = json.dumps(
            {"preview_id": preview["preview_id"], "consent": True}
        ).encode()
        writer = _FakeWriter()
        await srv._handle_diagnose_send(
            writer,  # type: ignore[arg-type]
            headers=_post_headers(
                body,
                **{"x-diagnostic-csrf": preview["csrf_token"]},
            ),  # no origin, no host
            reader=_make_reader(body),
        )
        assert writer.response_status == 403
        assert writer.response_json["error"] == "origin_missing"
    finally:
        await srv._diagnostics.stop()


@pytest.mark.asyncio
async def test_handler_preview_endpoint_url_matches_resolved() -> None:
    """The preview's endpoint_url equals diagnostics.upload._resolve_endpoint(None)."""
    from icom_lan.diagnostics.upload import _resolve_endpoint

    handler = DiagnosticsHandler()
    _register_test_contributor()
    cfg = Path("/tmp")
    log = Path("/tmp")
    try:
        result = await handler.handle_preview({}, None, cfg, log)
        assert result["endpoint_url"] == _resolve_endpoint(None)
    finally:
        await handler.stop()


def test_diagnostics_upload_error_subtype_translation() -> None:
    """Sanity: typed errors are subclasses of DiagnosticUploadError."""
    assert issubclass(RateLimited, DiagnosticUploadError)
