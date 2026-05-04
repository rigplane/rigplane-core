"""Tests for diagnostic bundle upload client."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from icom_lan.diagnostics import (
    BundleTooLarge,
    DiagnosticUploadError,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    ReportSubmitted,
    UploadFailed,
    upload_bundle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


HandlerType = Callable[[web.Request], Awaitable[web.StreamResponse]]


@pytest.fixture
def bundle_file(tmp_path: Path) -> Path:
    p = tmp_path / "bundle.zip"
    p.write_bytes(b"PK\x03\x04fake-zip-bytes")
    return p


@pytest_asyncio.fixture
async def make_server() -> AsyncIterator[
    Callable[[HandlerType], Awaitable[TestServer]]
]:
    servers: list[TestServer] = []

    async def _factory(handler: HandlerType) -> TestServer:
        app = web.Application()
        app.router.add_post("/v1/diagnostics/upload", handler)
        server = TestServer(app)
        await server.start_server()
        servers.append(server)
        return server

    yield _factory
    for s in servers:
        await s.close()


def _url(server: TestServer) -> str:
    return f"http://{server.host}:{server.port}/v1/diagnostics/upload"


def _success_body(**overrides: Any) -> dict[str, Any]:
    body = {
        "report_id": "rpt_abc123",
        "support_url": "https://support.example/rpt_abc123",
        "received_at_unix": 1234567890,
        "auth_class": "anonymous",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_upload_success_anonymous(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(_success_body())

    server = await make_server(handler)
    result = await upload_bundle(bundle_file, {"k": "v"}, endpoint=_url(server))
    assert isinstance(result, ReportSubmitted)
    assert result.report_id == "rpt_abc123"
    assert result.support_url == "https://support.example/rpt_abc123"
    assert result.received_at_unix == 1234567890
    assert result.auth_class == "anonymous"


async def test_upload_multipart_shape(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    captured: dict[str, Any] = {}

    async def handler(request: web.Request) -> web.Response:
        captured["content_type"] = request.headers.get("Content-Type", "")
        reader = await request.multipart()
        fields: dict[str, bytes] = {}
        async for part in reader:
            assert part.name is not None
            fields[part.name] = await part.read(decode=False)
        captured["fields"] = fields
        return web.json_response(_success_body())

    server = await make_server(handler)
    await upload_bundle(bundle_file, {"hello": "world"}, endpoint=_url(server))
    assert captured["content_type"].startswith("multipart/form-data")
    assert "metadata" in captured["fields"]
    assert "bundle" in captured["fields"]
    assert json.loads(captured["fields"]["metadata"].decode()) == {"hello": "world"}
    assert captured["fields"]["bundle"] == bundle_file.read_bytes()


async def test_upload_endpoint_arg_overrides_env(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = {"n": 0}

    async def handler(_request: web.Request) -> web.Response:
        hits["n"] += 1
        return web.json_response(_success_body())

    server = await make_server(handler)
    monkeypatch.setenv("ICOM_LAN_REPORT_ENDPOINT", "http://wrong.invalid/")
    await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert hits["n"] == 1


async def test_upload_endpoint_env_overrides_default(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hits = {"n": 0}

    async def handler(_request: web.Request) -> web.Response:
        hits["n"] += 1
        return web.json_response(_success_body())

    server = await make_server(handler)
    monkeypatch.setenv("ICOM_LAN_REPORT_ENDPOINT", _url(server))
    await upload_bundle(bundle_file, {})
    assert hits["n"] == 1


async def test_upload_header_provider_called(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    seen_auth: list[str | None] = []

    async def handler(request: web.Request) -> web.Response:
        seen_auth.append(request.headers.get("Authorization"))
        return web.json_response(_success_body())

    server = await make_server(handler)

    async def provider() -> dict[str, str]:
        return {"Authorization": "Bearer test-token"}

    await upload_bundle(
        bundle_file, {}, endpoint=_url(server), header_provider=provider
    )
    assert seen_auth == ["Bearer test-token"]


async def test_upload_401_retry_calls_header_provider_again(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    seen_auth: list[str | None] = []
    call_count = {"n": 0}

    async def handler(request: web.Request) -> web.Response:
        call_count["n"] += 1
        seen_auth.append(request.headers.get("Authorization"))
        if call_count["n"] == 1:
            return web.json_response({"error": {"code": "unauthorized"}}, status=401)
        return web.json_response(_success_body(auth_class="authenticated"))

    server = await make_server(handler)

    provider_calls = {"n": 0}

    async def provider() -> dict[str, str]:
        provider_calls["n"] += 1
        return {"Authorization": f"Bearer token-{provider_calls['n']}"}

    result = await upload_bundle(
        bundle_file, {}, endpoint=_url(server), header_provider=provider
    )
    assert provider_calls["n"] == 2
    assert seen_auth == ["Bearer token-1", "Bearer token-2"]
    assert result.auth_class == "authenticated"


async def test_upload_401_no_retry_without_header_provider(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    call_count = {"n": 0}

    async def handler(_request: web.Request) -> web.Response:
        call_count["n"] += 1
        return web.json_response({"error": {"code": "unauthorized"}}, status=401)

    server = await make_server(handler)

    with pytest.raises(DiagnosticUploadError):
        await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert call_count["n"] == 1


async def test_upload_429_raises_rate_limited(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {"error": {"code": "rate_limited", "retry_after_seconds": 60}}, status=429
        )

    server = await make_server(handler)
    with pytest.raises(RateLimited) as ei:
        await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert ei.value.retry_after_seconds == 60


async def test_upload_413_raises_bundle_too_large(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response({"error": {"code": "bundle_too_large"}}, status=413)

    server = await make_server(handler)
    with pytest.raises(BundleTooLarge):
        await upload_bundle(bundle_file, {}, endpoint=_url(server))


async def test_upload_422_raises_forbidden_content(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {"error": {"code": "forbidden_content", "pattern": "credit_card"}},
            status=422,
        )

    server = await make_server(handler)
    with pytest.raises(ForbiddenContent) as ei:
        await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert ei.value.pattern == "credit_card"


async def test_upload_400_raises_metadata_invalid(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(
            {"error": {"code": "metadata_invalid", "field": "version"}}, status=400
        )

    server = await make_server(handler)
    with pytest.raises(MetadataInvalid) as ei:
        await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert ei.value.field == "version"


async def test_upload_500_raises_upload_failed(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response({"error": {"code": "internal"}}, status=500)

    server = await make_server(handler)
    with pytest.raises(UploadFailed) as ei:
        await upload_bundle(bundle_file, {}, endpoint=_url(server))
    assert ei.value.status == 500


async def test_upload_network_error_on_unreachable(bundle_file: Path) -> None:
    with pytest.raises(NetworkError):
        await upload_bundle(
            bundle_file, {}, endpoint="http://127.0.0.1:1/v1/diagnostics/upload"
        )


async def test_upload_timeout_raises_network_error(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        await asyncio.sleep(2.0)
        return web.json_response(_success_body())

    server = await make_server(handler)
    with pytest.raises(NetworkError):
        await upload_bundle(bundle_file, {}, endpoint=_url(server), timeout_s=0.1)


async def test_upload_missing_bundle_raises_network_error(tmp_path: Path) -> None:
    missing = tmp_path / "does-not-exist.zip"
    with pytest.raises(NetworkError):
        await upload_bundle(missing, {}, endpoint="http://example.invalid/")


async def test_upload_non_dict_metadata_raises_metadata_invalid(
    bundle_file: Path,
) -> None:
    with pytest.raises(MetadataInvalid):
        await upload_bundle(bundle_file, None, endpoint="http://example.invalid/")  # type: ignore[arg-type]
    with pytest.raises(MetadataInvalid):
        await upload_bundle(bundle_file, [1, 2], endpoint="http://example.invalid/")  # type: ignore[arg-type]


async def test_upload_header_provider_failure_raises_network_error(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(_success_body())

    server = await make_server(handler)

    async def provider() -> dict[str, str]:
        raise RuntimeError("signer down")

    with pytest.raises(NetworkError):
        await upload_bundle(
            bundle_file, {}, endpoint=_url(server), header_provider=provider
        )


async def test_upload_header_provider_returns_non_dict_raises_network_error(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(_success_body())

    server = await make_server(handler)

    async def provider() -> dict[str, str]:
        return "not-a-dict"  # type: ignore[return-value]

    with pytest.raises(NetworkError):
        await upload_bundle(
            bundle_file, {}, endpoint=_url(server), header_provider=provider
        )


async def test_upload_header_provider_non_string_value_raises_network_error(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
) -> None:
    """Non-string header values must raise typed NetworkError, not aiohttp TypeError.

    Regression for Codex review on PR #1405: previously, a header dict with a
    non-string value (e.g. ``{"X-Test": 42}``) would pass the dict-isinstance
    check, then trip aiohttp's internal TypeError outside the typed exception
    handler.
    """

    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(_success_body())

    server = await make_server(handler)

    async def provider() -> dict[str, str]:
        return {"X-Test": 42}  # type: ignore[dict-item]

    with pytest.raises(NetworkError):
        await upload_bundle(
            bundle_file, {}, endpoint=_url(server), header_provider=provider
        )


@pytest.mark.parametrize("body", [[], "string-body", 42, None])
async def test_upload_2xx_non_object_body_returns_defaults(
    make_server: Callable[[HandlerType], Awaitable[TestServer]],
    bundle_file: Path,
    body: Any,
) -> None:
    """2xx response with a non-object JSON body must NOT raise AttributeError.

    Regression for Codex review on PR #1405: previously, ``body.get(...)`` was
    called without verifying ``isinstance(body, dict)`` and a 2xx response
    containing ``[]``/string/null bypassed typed exception handling.
    """

    async def handler(_request: web.Request) -> web.Response:
        return web.json_response(body)

    server = await make_server(handler)
    result = await upload_bundle(bundle_file, {"k": "v"}, endpoint=_url(server))
    assert isinstance(result, ReportSubmitted)
    assert result.report_id == ""
    assert result.support_url == ""
    assert result.received_at_unix == 0
    assert result.auth_class == "anonymous"
