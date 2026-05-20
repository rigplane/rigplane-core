"""rigplane diagnostic infrastructure (logging, contributors, bundle, upload).

Subsequent issues (#1388-#1401) build on this package.

Note (issue #1413): the ``upload`` submodule initializes the HTTP client stack.
Eagerly importing it here would make every ``import rigplane`` pay that cost,
so the four upload-related names
(``DEFAULT_ENDPOINT``, ``HeaderProvider``, ``ReportSubmitted``,
``upload_bundle``) are exposed lazily via :pep:`562` ``__getattr__``. They
remain importable as ``from rigplane.diagnostics import upload_bundle``;
the upload module import only fires on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rigplane.diagnostics._discovery import discover, register
from rigplane.diagnostics._errors import (
    BundleTooLarge,
    DiagnosticUploadError,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    UploadFailed,
)
from rigplane.diagnostics._logging import (
    SafeRotatingFileHandler,
    configure_diagnostic_logging,
)
from rigplane.diagnostics.bundle import build_bundle
from rigplane.diagnostics.contributor import BundleContext, DiagnosticContributor
from rigplane.diagnostics.redaction import (
    REDACTORS,
    redact_credentials,
    redact_ips,
    redact_paths,
    redact_tokens,
)

# Lazy re-exports from ``rigplane.diagnostics.upload``.
# Map: public name → attribute on ``upload`` module.
_LAZY_UPLOAD: dict[str, str] = {
    "DEFAULT_ENDPOINT": "DEFAULT_ENDPOINT",
    "HeaderProvider": "HeaderProvider",
    "ReportSubmitted": "ReportSubmitted",
    "upload_bundle": "upload_bundle",
}

if TYPE_CHECKING:
    # Make these names visible to typecheckers without eager runtime imports.
    from rigplane.diagnostics.upload import (  # noqa: F401
        DEFAULT_ENDPOINT,
        HeaderProvider,
        ReportSubmitted,
        upload_bundle,
    )


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy hook for upload-module re-exports.

    Defers the upload module import until a consumer actually accesses one of
    the upload names. Cached in ``globals()`` so subsequent lookups skip the hook.
    """
    target = _LAZY_UPLOAD.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module("rigplane.diagnostics.upload")
    attr = getattr(module, target)
    globals()[name] = attr  # cache so __getattr__ isn't called again
    return attr


def __dir__() -> list[str]:
    """Expose lazy upload names to ``dir()`` / IDE autocomplete."""
    return sorted({*globals().keys(), *_LAZY_UPLOAD.keys()})


__all__ = [
    "DEFAULT_ENDPOINT",
    "REDACTORS",
    "BundleContext",
    "BundleTooLarge",
    "DiagnosticContributor",
    "DiagnosticUploadError",
    "ForbiddenContent",
    "HeaderProvider",
    "MetadataInvalid",
    "NetworkError",
    "RateLimited",
    "ReportSubmitted",
    "SafeRotatingFileHandler",
    "UploadFailed",
    "build_bundle",
    "configure_diagnostic_logging",
    "discover",
    "redact_credentials",
    "redact_ips",
    "redact_paths",
    "redact_tokens",
    "register",
    "upload_bundle",
]
