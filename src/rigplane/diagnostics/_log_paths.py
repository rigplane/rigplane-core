"""Shared Core log directory resolution."""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

__all__ = ["resolve_core_log_dir"]


def resolve_core_log_dir() -> Path:
    """Return the directory Core should use for file logs."""
    log_dir = os.environ.get("RIGPLANE_LOG_DIR", "").strip()
    if log_dir:
        return Path(log_dir).expanduser()
    return Path(platformdirs.user_cache_path("rigplane")) / "logs"
