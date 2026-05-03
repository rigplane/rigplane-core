"""HTTP upload client for diagnostic bundles."""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import aiohttp

from icom_lan.diagnostics._errors import (
    BundleTooLarge,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    UploadFailed,
)

DEFAULT_ENDPOINT = "https://reports.msmsoft.net/v1/diagnostics/upload"
"""Default diagnostic-bundle upload endpoint. Override via ``ICOM_LAN_REPORT_ENDPOINT``."""

HeaderProvider = Callable[[], Awaitable[dict[str, str]]]
"""Optional async hook returning extra HTTP headers (e.g. Authorization for Pro)."""


@dataclass(frozen=True)
class ReportSubmitted:
    """Response from a successful upload."""

    report_id: str
    support_url: str
    received_at_unix: int
    auth_class: str  # "anonymous" | "authenticated"


def _resolve_endpoint(endpoint: str | None) -> str:
    if endpoint is not None:
        return endpoint
    return os.environ.get("ICOM_LAN_REPORT_ENDPOINT", DEFAULT_ENDPOINT)


def _raise_for_error(status: int, body: dict[str, Any]) -> None:
    """Translate the server's stable-error-code response into a typed exception."""
    err = body.get("error") if isinstance(body, dict) else None
    code = (err or {}).get("code") if isinstance(err, dict) else None
    message = (err or {}).get("message", "") if isinstance(err, dict) else ""
    retry_after = (
        (err or {}).get("retry_after_seconds") if isinstance(err, dict) else None
    )

    if code == "rate_limited" or status == 429:
        ra = retry_after if isinstance(retry_after, int) else None
        raise RateLimited(retry_after_seconds=ra, message=message)
    if code == "bundle_too_large" or status == 413:
        raise BundleTooLarge(message)
    if code == "forbidden_content" or status == 422:
        pattern = (err or {}).get("pattern") if isinstance(err, dict) else None
        raise ForbiddenContent(
            pattern=pattern if isinstance(pattern, str) else None, message=message
        )
    if code == "metadata_invalid" or status == 400:
        field = (err or {}).get("field") if isinstance(err, dict) else None
        raise MetadataInvalid(
            field=field if isinstance(field, str) else None, message=message
        )
    raise UploadFailed(
        status=status, code=code if isinstance(code, str) else None, message=message
    )


async def _do_upload(
    session: aiohttp.ClientSession,
    url: str,
    bundle_path: Path,
    metadata: dict[str, Any],
    headers: dict[str, str],
) -> aiohttp.ClientResponse:
    form = aiohttp.FormData()
    form.add_field("metadata", json.dumps(metadata), content_type="application/json")
    form.add_field(
        "bundle",
        bundle_path.open("rb"),
        filename=bundle_path.name,
        content_type="application/zip",
    )
    return await session.post(url, data=form, headers=headers)


async def upload_bundle(
    bundle_path: Path,
    metadata: dict[str, Any],
    *,
    endpoint: str | None = None,
    header_provider: HeaderProvider | None = None,
    timeout_s: float = 60.0,
) -> ReportSubmitted:
    """POST a diagnostic bundle to the upload endpoint.

    See module docstring + :data:`DEFAULT_ENDPOINT`.

    Raises one of the typed exceptions in
    :mod:`icom_lan.diagnostics._errors` on failure.
    """
    if not isinstance(metadata, dict):
        raise MetadataInvalid(field=None, message="metadata must be a dict")
    if not bundle_path.is_file():
        raise NetworkError(f"bundle file not found: {bundle_path}")

    url = _resolve_endpoint(endpoint)
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async def _attempt(session: aiohttp.ClientSession) -> aiohttp.ClientResponse:
        headers: dict[str, str] = {}
        if header_provider is not None:
            try:
                extra = await header_provider()
            except Exception as exc:  # noqa: BLE001 — Pro signer error → typed
                raise NetworkError(f"header_provider failed: {exc}") from exc
            if not isinstance(extra, dict):
                raise NetworkError("header_provider must return dict[str, str]")
            headers.update(extra)
        return await _do_upload(session, url, bundle_path, metadata, headers)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            resp = await _attempt(session)
            if resp.status == 401 and header_provider is not None:
                resp.release()
                resp = await _attempt(session)
            try:
                body = await resp.json(content_type=None)
            except (aiohttp.ContentTypeError, json.JSONDecodeError):
                body = {}
            if 200 <= resp.status < 300:
                return ReportSubmitted(
                    report_id=str(body.get("report_id", "")),
                    support_url=str(body.get("support_url", "")),
                    received_at_unix=int(body.get("received_at_unix", 0)),
                    auth_class=str(body.get("auth_class", "anonymous")),
                )
            _raise_for_error(resp.status, body if isinstance(body, dict) else {})
            raise UploadFailed(status=resp.status, code=None, message="unreachable")
    except (aiohttp.ClientError, TimeoutError) as exc:
        raise NetworkError(f"upload failed: {exc}") from exc
