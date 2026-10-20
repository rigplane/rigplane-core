"""Diagnostic-report upload endpoint: the one definition and its resolver.

Stdlib only on purpose: ``rigplane.cli`` reads the endpoint without importing
the HTTP client stack that ``rigplane.diagnostics.upload`` pulls in.
"""

from __future__ import annotations

import os

DEFAULT_ENDPOINT = "https://reports.rigplane.app/v1/diagnostics/upload"
"""Default diagnostic-bundle upload endpoint. Override via ``RIGPLANE_REPORT_ENDPOINT``."""


def resolve_endpoint(endpoint: str | None) -> str:
    """Return ``endpoint``, else ``RIGPLANE_REPORT_ENDPOINT`` if non-empty, else the default."""
    if endpoint is not None:
        return endpoint
    return os.environ.get("RIGPLANE_REPORT_ENDPOINT") or DEFAULT_ENDPOINT
