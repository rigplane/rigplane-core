"""Helpers for optional / heavy dependencies.

Centralises the ``try: import X / except ImportError: raise ImportError(...)``
pattern so that error messages stay uniform across the codebase.

Each helper is a no-op if the dependency is already importable; on failure it
re-raises ``ImportError`` with a friendly install hint.

Notes
-----
- ``numpy``, ``sounddevice`` and ``opuslib`` ship with the core install since
  #1090 — the ``[bridge]`` / ``[audio]`` extras are no-op aliases.  The
  install hint therefore omits any extra.
- ``Pillow`` lives behind ``[scope]`` and ``pyserial-asyncio`` behind
  ``[serial]``.

Helpers raise ``ImportError`` chained from the original failure (``from exc``)
so callers can introspect ``__cause__`` for richer diagnostics.
"""

from __future__ import annotations


def _require_numpy() -> None:
    """Ensure ``numpy`` is importable, otherwise raise ``ImportError``."""
    try:
        import numpy  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "numpy is required for this feature. Install with: pip install icom-lan"
        ) from exc


def _require_sounddevice() -> None:
    """Ensure ``sounddevice`` is importable, otherwise raise ``ImportError``."""
    try:
        import sounddevice  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "sounddevice is required for audio device access. "
            "Install with: pip install icom-lan"
        ) from exc


def _require_opuslib() -> None:
    """Ensure ``opuslib`` is importable, otherwise raise ``ImportError``."""
    try:
        import opuslib  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "opuslib is required for Opus audio codec support. "
            "Install with: pip install icom-lan"
        ) from exc


def _require_pillow() -> None:
    """Ensure ``Pillow`` is importable, otherwise raise ``ImportError``."""
    try:
        import PIL  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Pillow is required for scope rendering. "
            "Install with: pip install icom-lan[scope]"
        ) from exc


def _require_pyserial_asyncio() -> None:
    """Ensure ``pyserial-asyncio`` is importable, otherwise raise ``ImportError``."""
    try:
        import serial_asyncio  # type: ignore[import-untyped]  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "pyserial-asyncio is required for serial backends. "
            "Install with: pip install icom-lan[serial]"
        ) from exc


__all__ = [
    "_require_numpy",
    "_require_sounddevice",
    "_require_opuslib",
    "_require_pillow",
    "_require_pyserial_asyncio",
]
