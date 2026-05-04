"""icom-lan diagnostic infrastructure (logging, contributors, bundle, upload).

Subsequent issues (#1388-#1401) build on this package.

Note (issue #1413): the ``upload`` submodule imports ``aiohttp``, which is a
dev-only dependency (declared in ``[dependency-groups].dev``, not in
``[project].dependencies``). Eagerly importing it here would break ``import
icom_lan`` for runtime-only installs, so the four upload-related names
(``DEFAULT_ENDPOINT``, ``HeaderProvider``, ``ReportSubmitted``,
``upload_bundle``) are exposed lazily via :pep:`562` ``__getattr__``. They
remain importable as ``from icom_lan.diagnostics import upload_bundle``;
the ``aiohttp`` import only fires on first access.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from icom_lan.diagnostics._discovery import discover, register
from icom_lan.diagnostics._errors import (
    BundleTooLarge,
    DiagnosticUploadError,
    ForbiddenContent,
    MetadataInvalid,
    NetworkError,
    RateLimited,
    UploadFailed,
)
from icom_lan.diagnostics._logging import (
    SafeRotatingFileHandler,
    configure_diagnostic_logging,
)
from icom_lan.diagnostics.bundle import build_bundle
from icom_lan.diagnostics.contributor import BundleContext, DiagnosticContributor
from icom_lan.diagnostics.redaction import (
    REDACTORS,
    redact_credentials,
    redact_ips,
    redact_paths,
    redact_tokens,
)

# Lazy re-exports from ``icom_lan.diagnostics.upload`` (which imports aiohttp).
# Map: public name → attribute on ``upload`` module.
_LAZY_UPLOAD: dict[str, str] = {
    "DEFAULT_ENDPOINT": "DEFAULT_ENDPOINT",
    "HeaderProvider": "HeaderProvider",
    "ReportSubmitted": "ReportSubmitted",
    "upload_bundle": "upload_bundle",
}

if TYPE_CHECKING:
    # Make these names visible to typecheckers without triggering aiohttp.
    from icom_lan.diagnostics.upload import (  # noqa: F401
        DEFAULT_ENDPOINT,
        HeaderProvider,
        ReportSubmitted,
        upload_bundle,
    )


def __getattr__(name: str) -> Any:
    """:pep:`562` lazy hook for upload-module re-exports.

    Defers ``import aiohttp`` until a consumer actually accesses one of the
    upload names. Cached in ``globals()`` so subsequent lookups skip the hook.
    """
    target = _LAZY_UPLOAD.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    module = importlib.import_module("icom_lan.diagnostics.upload")
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
