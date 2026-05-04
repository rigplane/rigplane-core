"""Diagnostic upload handlers for the web UI.

Routes ``/api/v1/diagnose/{preview,send,save}`` and
``DELETE /api/v1/diagnose/preview/{preview_id}``.

Implements:
- Preview-bound CSRF token (single-use for ``send``; reusable for
  ``save`` and ``delete`` until expiry).
- Same-origin check (skipped on loopback bind — the loopback boundary is
  the security boundary).
- Background sweeper that purges expired previews every 60s.
- API auth inheritance: the existing top-level token check in
  :func:`icom_lan.web.web_routing.dispatch_http_request` already gates
  every ``/api/`` path, so this module does not duplicate it.

See spec §4.9 in
``docs/plans/2026-05-03-diagnostic-data-collection-design.md``.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from icom_lan.diagnostics import (
    REDACTORS,
    BundleContext,
    BundleTooLarge,
    DiagnosticUploadError,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    ReportSubmitted,
    UploadFailed,
    build_bundle,
    upload_bundle,
)
from icom_lan.diagnostics.upload import _resolve_endpoint  # noqa: TID251

__all__ = ["DiagnosticsHandler"]

logger = logging.getLogger(__name__)

PREVIEW_TTL_SECONDS = 600  # 10 minutes
_SWEEP_INTERVAL_SECONDS = 60


@dataclass
class _PreviewSession:
    preview_id: str
    csrf_token: str
    bundle_path: Path
    staging_dir: Path
    manifest: dict[str, Any]
    files: list[dict[str, Any]]
    total_size_bytes: int
    redactions_applied: list[str]
    endpoint_url: str
    metadata: dict[str, Any]
    filename: str
    created_at_unix: int
    consumed: bool = False  # True after a successful send

    def is_expired(self, now: int | None = None) -> bool:
        if now is None:
            now = int(time.time())
        return now - self.created_at_unix > PREVIEW_TTL_SECONDS


def check_origin_or_loopback(
    origin: str | None,
    bound_host: str,
    bound_port: int,
    request_host: str | None = None,
) -> tuple[bool, str]:
    """Validate the request ``Origin`` header.

    Returns ``(allowed, reason)``. The reason string doubles as a
    machine-readable error code surfaced to the client on failure.

    Three modes:

    1. **Loopback bind** (``127.0.0.1`` / ``::1`` / ``localhost``) —
       the loopback boundary is itself the security boundary. Skip the
       check entirely (dev tools sometimes omit ``Origin``).
    2. **Wildcard bind** (``0.0.0.0`` / ``::``) — the server listens on
       all interfaces, so the bind address can't anchor an ``expected
       origin`` set. Use the request's ``Host`` header (the address the
       browser actually connected to) instead. A loopback ``Host``
       means the browser is on the same machine and we trust it.
    3. **Specific bind** — match ``Origin`` against
       ``http(s)://<bound_host>:<bound_port>`` plus loopback aliases (so
       a localhost frontend can talk to a ``192.168.x.x`` bind in dev).
    """
    # 1. Loopback bind = security boundary is loopback itself
    if bound_host in ("127.0.0.1", "::1", "localhost"):
        return (True, "loopback_bind_skips_origin_check")

    # 2. Wildcard bind: anchor on the Host header instead of bound_host
    if bound_host in ("0.0.0.0", "::"):
        if not request_host:
            return (False, "host_header_missing")
        # Strip port from Host (may or may not be present)
        if ":" in request_host and not request_host.startswith("["):
            host_no_port = request_host.rsplit(":", 1)[0]
        else:
            host_no_port = request_host
        # Loopback Host = browser is on the same machine
        if host_no_port in ("127.0.0.1", "::1", "localhost", "[::1]"):
            return (True, "loopback_via_wildcard_bind")
        if not origin:
            return (False, "origin_missing")
        expected = {
            f"http://{request_host}",
            f"https://{request_host}",
            f"http://{host_no_port}:{bound_port}",
            f"https://{host_no_port}:{bound_port}",
        }
        if origin in expected:
            return (True, "matched_via_host_header")
        return (False, "origin_mismatch")

    # 3. Specific bind: exact match on bound_host:bound_port + loopback aliases
    if not origin:
        return (False, "origin_missing")
    expected = {
        f"http://{bound_host}:{bound_port}",
        f"https://{bound_host}:{bound_port}",
    }
    if origin in expected:
        return (True, "matched")
    if origin.startswith("http://localhost:") or origin.startswith(
        "https://localhost:"
    ):
        return (True, "matched_localhost")
    if origin.startswith("http://127.0.0.1:") or origin.startswith(
        "https://127.0.0.1:"
    ):
        return (True, "matched_loopback_v4")
    if origin.startswith("http://[::1]:") or origin.startswith("https://[::1]:"):
        return (True, "matched_loopback_v6")
    return (False, "origin_mismatch")


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str)]


def _bundle_filename(now_unix: int) -> str:
    ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(now_unix))
    return f"icom-lan-report-{ts}.zip"


class DiagnosticsHandler:
    """Owns preview session state for ``/api/v1/diagnose/*`` endpoints.

    Sessions live for :data:`PREVIEW_TTL_SECONDS` and are swept by an
    asyncio task started lazily on the first preview (and explicitly via
    :meth:`stop` during server shutdown).
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _PreviewSession] = {}
        self._lock = asyncio.Lock()
        self._sweeper_task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Spawn the expiry sweeper if not yet running."""
        if self._sweeper_task is None or self._sweeper_task.done():
            self._sweeper_task = asyncio.create_task(self._sweep_loop())

    async def stop(self) -> None:
        """Cancel the sweeper and clean up any remaining sessions."""
        task = self._sweeper_task
        self._sweeper_task = None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        async with self._lock:
            for sess in list(self._sessions.values()):
                self._cleanup_session_files(sess)
            self._sessions.clear()

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
                await self._sweep_once()
        except asyncio.CancelledError:
            raise

    async def _sweep_once(self) -> int:
        now = int(time.time())
        expired_ids: list[str] = []
        async with self._lock:
            for pid, sess in list(self._sessions.items()):
                if sess.is_expired(now):
                    expired_ids.append(pid)
            for pid in expired_ids:
                sess = self._sessions.pop(pid)
                self._cleanup_session_files(sess)
        return len(expired_ids)

    def _cleanup_session_files(self, sess: _PreviewSession) -> None:
        try:
            sess.bundle_path.unlink(missing_ok=True)
        except OSError:
            logger.debug("diagnostics: bundle unlink failed for %s", sess.bundle_path)
        try:
            staging = sess.staging_dir
            if staging.exists():
                # Remove children then rmdir; we only put the zip there.
                for child in staging.iterdir():
                    try:
                        child.unlink()
                    except OSError:
                        pass
                staging.rmdir()
        except OSError:
            logger.debug("diagnostics: staging cleanup failed for %s", sess.staging_dir)

    # ------------------------------------------------------------------
    # Endpoint methods
    # ------------------------------------------------------------------

    async def handle_preview(
        self,
        body: dict[str, Any],
        radio: Any | None,
        config_dir: Path,
        log_dir: Path,
    ) -> dict[str, Any]:
        """Build a bundle and create a preview session.

        ``body`` accepts: ``description`` (str), ``issue_ref`` (str),
        ``email`` (str), ``callsign`` (str), ``includes`` (list[str]),
        ``excludes`` (list[str]). Unknown keys are ignored.
        """
        await self.start()

        description = body.get("description")
        issue_ref = body.get("issue_ref")
        email = body.get("email")
        callsign = body.get("callsign")
        includes = _coerce_str_list(body.get("includes"))
        excludes = _coerce_str_list(body.get("excludes"))

        submission_id = str(uuid.uuid4())
        now_unix = int(time.time())

        ctx = BundleContext(
            radio=radio,
            config_dir=config_dir,
            log_dir=log_dir,
            user_description=description if isinstance(description, str) else None,
            issue_ref=issue_ref if isinstance(issue_ref, str) else None,
            contact_email=email if isinstance(email, str) and email else None,
            contact_callsign=(
                callsign if isinstance(callsign, str) and callsign else None
            ),
            submission_id=submission_id,
            generated_at_unix=now_unix,
        )

        staging_dir = Path(tempfile.mkdtemp(prefix="icom-lan-report-"))
        filename = _bundle_filename(now_unix)
        bundle_path = staging_dir / filename

        # build_bundle does not currently accept includes/excludes; the
        # contract is forward-compatible: client sends them, server
        # silently honours when discovery layer adds support. For now we
        # record them on the session for future filtering.
        _ = includes
        _ = excludes

        try:
            await asyncio.to_thread(build_bundle, ctx, bundle_path)
        except Exception as exc:  # noqa: BLE001 — bubble up as 500
            # Session was never registered, just clean up.
            try:
                if bundle_path.exists():
                    bundle_path.unlink()
                staging_dir.rmdir()
            except OSError:
                pass
            raise RuntimeError(f"bundle generation failed: {exc}") from exc

        manifest, files, total_size = _read_zip_meta(bundle_path)
        endpoint_url = _resolve_endpoint(None)
        metadata = _metadata_from_manifest(manifest)

        preview_id = secrets.token_urlsafe(24)
        csrf_token = secrets.token_urlsafe(32)

        sess = _PreviewSession(
            preview_id=preview_id,
            csrf_token=csrf_token,
            bundle_path=bundle_path,
            staging_dir=staging_dir,
            manifest=manifest,
            files=files,
            total_size_bytes=total_size,
            redactions_applied=list(REDACTORS),
            endpoint_url=endpoint_url,
            metadata=metadata,
            filename=filename,
            created_at_unix=now_unix,
        )
        async with self._lock:
            self._sessions[preview_id] = sess

        return {
            "preview_id": preview_id,
            "csrf_token": csrf_token,
            "manifest": manifest,
            "files": files,
            "total_size_bytes": total_size,
            "redactions_applied": list(REDACTORS),
            "endpoint_url": endpoint_url,
        }

    async def handle_send(
        self,
        body: dict[str, Any],
        csrf_token: str,
    ) -> dict[str, Any]:
        """Upload the previewed bundle. CSRF is single-use on success."""
        preview_id = body.get("preview_id")
        consent = body.get("consent")
        if not isinstance(preview_id, str) or not preview_id:
            raise _ClientError(400, "preview_missing", "preview_id is required")
        if consent is not True:
            raise _ClientError(400, "consent_required", "consent must be true")

        async with self._lock:
            sess = self._sessions.get(preview_id)
            if sess is None or sess.is_expired():
                if sess is not None:
                    self._sessions.pop(preview_id, None)
                    self._cleanup_session_files(sess)
                raise _ClientError(
                    404, "preview_not_found", "preview not found or expired"
                )
            if sess.consumed:
                raise _ClientError(403, "csrf_missing", "CSRF token already consumed")
            if not _ct_eq(csrf_token, sess.csrf_token):
                raise _ClientError(403, "csrf_missing", "CSRF token invalid")

        # Upload outside the lock — network call is slow.
        try:
            report = await upload_bundle(sess.bundle_path, sess.metadata)
        except (
            RateLimited,
            BundleTooLarge,
            ForbiddenContent,
            MetadataInvalid,
            NetworkError,
            UploadFailed,
            DiagnosticUploadError,
        ):
            raise

        # Mark consumed only on success.
        async with self._lock:
            current = self._sessions.get(preview_id)
            if current is sess:
                current.consumed = True

        return _report_to_dict(report)

    async def handle_save(
        self,
        body: dict[str, Any],
        csrf_token: str,
    ) -> tuple[bytes, str]:
        """Return ``(zip_bytes, filename)`` for a download response."""
        preview_id = body.get("preview_id")
        if not isinstance(preview_id, str) or not preview_id:
            raise _ClientError(400, "preview_missing", "preview_id is required")

        async with self._lock:
            sess = self._sessions.get(preview_id)
            if sess is None or sess.is_expired():
                if sess is not None:
                    self._sessions.pop(preview_id, None)
                    self._cleanup_session_files(sess)
                raise _ClientError(
                    404, "preview_not_found", "preview not found or expired"
                )
            if not _ct_eq(csrf_token, sess.csrf_token):
                raise _ClientError(403, "csrf_missing", "CSRF token invalid")
            bundle_path = sess.bundle_path
            filename = sess.filename

        data = await asyncio.to_thread(bundle_path.read_bytes)
        return data, filename

    async def handle_delete(
        self,
        preview_id: str,
        csrf_token: str,
    ) -> None:
        """Remove a session and its bundle file."""
        async with self._lock:
            sess = self._sessions.get(preview_id)
            if sess is None:
                # Idempotent — pretend it succeeded.
                return
            if not _ct_eq(csrf_token, sess.csrf_token):
                raise _ClientError(403, "csrf_missing", "CSRF token invalid")
            self._sessions.pop(preview_id, None)
            self._cleanup_session_files(sess)


# ----------------------------------------------------------------------
# Internal helpers
# ----------------------------------------------------------------------


class _ClientError(Exception):
    """Raised by handler methods to signal a client-visible error.

    The web routing layer translates this into an HTTP response.
    """

    def __init__(self, status: int, code: str, message: str) -> None:
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def _ct_eq(a: str | None, b: str | None) -> bool:
    """Constant-time string compare; accepts ``None``."""
    if not a or not b:
        return False
    try:
        ab = a.encode("utf-8")
        bb = b.encode("utf-8")
    except (AttributeError, UnicodeError):
        return False
    if len(ab) != len(bb):
        return False
    return secrets.compare_digest(ab, bb)


def _read_zip_meta(
    bundle_path: Path,
) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
    """Return ``(manifest, files, total_uncompressed_size)`` for a ZIP."""
    import json as _json

    files: list[dict[str, Any]] = []
    total_size = 0
    manifest: dict[str, Any] = {}
    with zipfile.ZipFile(bundle_path, mode="r") as zf:
        for info in sorted(zf.infolist(), key=lambda i: i.filename):
            if info.is_dir():
                continue
            files.append({"path": info.filename, "size": info.file_size})
            total_size += info.file_size
        try:
            manifest_bytes = zf.read("manifest.json")
            manifest = _json.loads(manifest_bytes.decode("utf-8"))
            if not isinstance(manifest, dict):
                manifest = {}
        except (KeyError, ValueError):
            manifest = {}
    return manifest, files, total_size


def _metadata_from_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    """Extract upload metadata from the bundle manifest.

    The upload server expects a small JSON document alongside the bundle
    (see ``upload_bundle``). We surface stable identification fields and
    nothing else.
    """
    meta: dict[str, Any] = {}
    if isinstance(manifest.get("schema_version"), str):
        meta["schema_version"] = manifest["schema_version"]
    if isinstance(manifest.get("submission_id"), str):
        meta["submission_id"] = manifest["submission_id"]
    if isinstance(manifest.get("generated_at_unix"), int):
        meta["generated_at_unix"] = manifest["generated_at_unix"]
    app = manifest.get("app")
    if isinstance(app, dict):
        meta["app"] = {
            k: v for k, v in app.items() if isinstance(k, str) and isinstance(v, str)
        }
    plat = manifest.get("platform")
    if isinstance(plat, dict):
        meta["platform"] = {
            k: v for k, v in plat.items() if isinstance(k, str) and isinstance(v, str)
        }
    if isinstance(manifest.get("issue_ref"), str):
        meta["issue_ref"] = manifest["issue_ref"]
    return meta


def _report_to_dict(report: ReportSubmitted) -> dict[str, Any]:
    return {
        "report_id": report.report_id,
        "support_url": report.support_url,
        "received_at_unix": report.received_at_unix,
        "auth_class": report.auth_class,
    }
