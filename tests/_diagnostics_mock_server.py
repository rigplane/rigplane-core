"""Mock receiver for ``diagnostic-bundle-v1`` — used by e2e tests.

Validates the multipart shape (``bundle`` file + ``metadata`` JSON), the
required-fields subset of the contract metadata schema, and emits the
configured response (200 / 400 / 401 / 413 / 422 / 429). Errors follow
the stable envelope ``{"error": {"code": ..., ...}}`` from
``docs/contracts/diagnostic-bundle-v1.md``.

The mock is stateful: it records every accepted bundle (metadata, size,
headers, returned response) and dedupes by ``submission_id`` so an e2e
test can assert end-to-end idempotency.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from aiohttp import web

# Subset of the contract's required fields the mock validates. The full
# contract additionally requires ``app.name``, ``app.version``,
# ``platform.os``, ``platform.arch``; we keep the mock's required set
# narrow to the fields the CLI/Web flows are guaranteed to set on every
# manifest, leaving the deeper schema validation to the production server.
_REQUIRED_FIELDS = {
    "schema_version",
    "submission_id",
    "generated_at_unix",
}


def _err(code: str, status: int, **extra: Any) -> web.Response:
    body = {"error": {"code": code, **extra}}
    headers: dict[str, str] = {}
    if "retry_after_seconds" in extra and isinstance(extra["retry_after_seconds"], int):
        headers["Retry-After"] = str(extra["retry_after_seconds"])
    return web.json_response(body, status=status, headers=headers)


@dataclass
class MockReceiver:
    """Stateful aiohttp app that records every received bundle."""

    received: list[dict[str, Any]] = field(default_factory=list)
    """Records: ``{metadata, bundle_size, headers, response}``."""

    response_mode: str = "success"
    """One of: ``success``, ``rate_limited``, ``bundle_too_large``,
    ``forbidden``, ``metadata_invalid``, ``401_once``."""

    _401_count: int = 0

    def app(self) -> web.Application:
        app = web.Application(client_max_size=100 * 1024 * 1024)
        app.router.add_post("/v1/diagnostics/upload", self._handle_upload)
        return app

    async def _handle_upload(self, request: web.Request) -> web.Response:
        # 1. Parse multipart.
        try:
            reader = await request.multipart()
        except Exception as exc:  # noqa: BLE001 — translate to typed error
            return _err("metadata_invalid", 400, message=f"multipart: {exc}")

        bundle_bytes = b""
        metadata: dict[str, Any] | None = None
        async for part in reader:
            if part.name == "bundle":
                bundle_bytes = await part.read(decode=False)
            elif part.name == "metadata":
                meta_text = await part.text()
                try:
                    parsed = json.loads(meta_text)
                except json.JSONDecodeError as exc:
                    return _err("metadata_invalid", 400, message=f"json: {exc}")
                if not isinstance(parsed, dict):
                    return _err(
                        "metadata_invalid", 400, message="metadata must be object"
                    )
                metadata = parsed

        if not bundle_bytes or metadata is None:
            return _err(
                "metadata_invalid", 400, message="missing bundle or metadata part"
            )

        # 2. Required-field check.
        missing = _REQUIRED_FIELDS - set(metadata.keys())
        if missing:
            return _err(
                "metadata_invalid",
                400,
                field=sorted(missing)[0],
                message=f"missing fields: {sorted(missing)}",
            )

        # 3. Configured-response branches (run BEFORE recording).
        if self.response_mode == "rate_limited":
            return _err("rate_limited", 429, retry_after_seconds=30)
        if self.response_mode == "bundle_too_large":
            return _err("bundle_too_large", 413)
        if self.response_mode == "forbidden":
            return _err("forbidden_content", 422, pattern="test-pattern")
        if self.response_mode == "metadata_invalid":
            return _err(
                "metadata_invalid", 400, field="test", message="test-mode rejection"
            )
        if self.response_mode == "401_once":
            self._401_count += 1
            if self._401_count == 1:
                return _err("unauthorized", 401)
            # second call falls through to success.

        # 4. Idempotency dedupe by ``submission_id``.
        submission_id = metadata["submission_id"]
        for prior in self.received:
            if prior["metadata"].get("submission_id") == submission_id:
                return web.json_response(prior["response"])

        report_id = f"rpt_{uuid.uuid4().hex[:16]}"
        response: dict[str, Any] = {
            "report_id": report_id,
            "support_url": f"https://reports.example/r/{report_id}",
            "received_at_unix": int(time.time()),
            "auth_class": (
                "authenticated" if request.headers.get("Authorization") else "anonymous"
            ),
        }
        self.received.append(
            {
                "metadata": metadata,
                "bundle_size": len(bundle_bytes),
                "headers": dict(request.headers),
                "response": response,
            }
        )
        return web.json_response(response)
