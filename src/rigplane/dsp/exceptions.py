"""DSP pipeline exceptions."""

from __future__ import annotations

__all__ = ["DSPBackendUnavailable", "DSPConfigError"]


class DSPBackendUnavailable(ImportError):
    """Raised when a required DSP backend (e.g. numpy) is not installed."""


class DSPConfigError(ValueError):
    """Raised on invalid DSP pipeline configuration."""
