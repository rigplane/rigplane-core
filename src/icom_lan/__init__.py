"""Backwards-compatibility shim — ``icom_lan`` was renamed to ``rigplane`` in v2.0.0.

This module makes existing v1.x import paths keep working without
modification, while emitting a one-time :class:`DeprecationWarning` on
first import so users know to migrate.

Migration map::

    from icom_lan import IcomRadio              -> from rigplane import IcomRadio
    from icom_lan.runtime import IcomRadio      -> from rigplane.runtime import IcomRadio
    import icom_lan.web                         -> import rigplane.web

The shim re-exports everything via :pep:`562` ``__getattr__`` delegation
plus :data:`sys.modules` aliasing. It preserves :pep:`562` lazy loading
in :mod:`rigplane` — ``import icom_lan`` does *not* eagerly load tier-2
attributes.

This shim will be removed in a future major version.
"""

from __future__ import annotations

import pkgutil
import sys
import warnings
from typing import Any

import rigplane as _rigplane

# Emit deprecation once per Python process when this shim is first imported.
warnings.warn(
    "The 'icom_lan' import path is deprecated and will be removed in a "
    "future release. Replace `import icom_lan` with `import rigplane`. "
    "See https://rigplane.dev/migrate for the full migration guide.",
    DeprecationWarning,
    stacklevel=2,
)

# Mirror rigplane's top-level submodules into the icom_lan namespace via
# sys.modules aliasing. This makes ``import icom_lan.web`` and
# ``from icom_lan.runtime import IcomRadio`` work without any per-module
# stub files. Iterating ``rigplane.__path__`` keeps this list in lockstep
# with rigplane regardless of future additions/removals.
for _info in pkgutil.iter_modules(_rigplane.__path__):
    # Skip dunder modules — ``rigplane.__main__`` runs the CLI at import
    # time, and dunder modules generally aren't a public API surface.
    if _info.name.startswith("_"):
        continue
    _full = f"rigplane.{_info.name}"
    try:
        __import__(_full)
    except ImportError:
        # Some submodules (e.g. scope.render) require optional extras
        # (Pillow). Skip silently if the backing import fails — the same
        # ImportError will surface when the user accesses it directly.
        continue
    sys.modules[f"icom_lan.{_info.name}"] = sys.modules[_full]


def __getattr__(name: str) -> Any:
    """Delegate attribute lookup to :mod:`rigplane`.

    This keeps the lazy :pep:`562` resolution that :mod:`rigplane` uses for
    tier-2 names (``IcomRadio``, audio primitives, scope helpers, ...).
    Without this, an eager re-export loop would force-load every backing
    module at ``import icom_lan`` time.
    """
    if name.startswith("_"):
        raise AttributeError(f"module 'icom_lan' has no attribute {name!r}")
    return getattr(_rigplane, name)


def __dir__() -> list[str]:
    """Mirror ``dir(rigplane)`` so IDE/REPL discovery matches the canonical."""
    return dir(_rigplane)
