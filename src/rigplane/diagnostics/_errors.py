"""Typed exceptions for diagnostic upload."""

from __future__ import annotations


class DiagnosticUploadError(Exception):
    """Base for diagnostic upload failures."""


class NetworkError(DiagnosticUploadError):
    """Connection refused, timeout, DNS failure, etc. — pre-HTTP."""


class MetadataInvalid(DiagnosticUploadError):
    """HTTP 400 — server rejected metadata schema."""

    def __init__(self, field: str | None = None, message: str = "") -> None:
        self.field = field
        super().__init__(message or f"metadata invalid (field={field!r})")


class BundleTooLarge(DiagnosticUploadError):
    """HTTP 413 — bundle exceeds server's 25 MiB cap."""


class ForbiddenContent(DiagnosticUploadError):
    """HTTP 422 — server's content scanner rejected the bundle."""

    def __init__(self, pattern: str | None = None, message: str = "") -> None:
        self.pattern = pattern
        super().__init__(message or f"forbidden content (pattern={pattern!r})")


class RateLimited(DiagnosticUploadError):
    """HTTP 429 — rate limit hit."""

    def __init__(
        self, retry_after_seconds: int | None = None, message: str = ""
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        super().__init__(
            message or f"rate limited (retry_after={retry_after_seconds}s)"
        )


class UploadFailed(DiagnosticUploadError):
    """Other non-2xx responses (5xx server errors, unhandled 4xx)."""

    def __init__(self, status: int, code: str | None = None, message: str = "") -> None:
        self.status = status
        self.code = code
        super().__init__(message or f"upload failed (status={status} code={code!r})")
