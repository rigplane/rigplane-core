"""icom-lan diagnostic infrastructure (logging, contributors, bundle, upload).

Subsequent issues (#1388-#1401) build on this package.
"""

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
from icom_lan.diagnostics.contributor import BundleContext, DiagnosticContributor
from icom_lan.diagnostics.redaction import (
    REDACTORS,
    redact_credentials,
    redact_ips,
    redact_paths,
    redact_tokens,
)
from icom_lan.diagnostics.upload import (
    DEFAULT_ENDPOINT,
    HeaderProvider,
    ReportSubmitted,
    upload_bundle,
)

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
    "configure_diagnostic_logging",
    "discover",
    "redact_credentials",
    "redact_ips",
    "redact_paths",
    "redact_tokens",
    "register",
    "upload_bundle",
]
