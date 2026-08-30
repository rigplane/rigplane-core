"""Step 1 measurement scaffolding for the profile-driven command-bytes plan.

Temporary. This file, its install call in ``commands/__init__.py``, the env
var it is gated on (``core/env_config.py:
get_command_fallback_audit_enabled``), and the charter exception recorded
for it in ``commands/LAYER.md`` are all deleted in Step Z of
``docs/plans/2026-08-29-profile-driven-command-bytes.md`` (§4, §8.1 Q4).

It exists to answer one question before that plan touches a single builder:
which exported command builders are being called without a ``cmd_map`` --
i.e. which calls are still on the hardcoded fallback path. It changes no
byte on the wire and no builder's return value; it only wraps each exported
``cmd_map``-taking builder so a fallback call logs itself.

The wrapping happens only when ``RIGPLANE_COMMAND_FALLBACK_AUDIT`` is set
(``core/env_config.py: get_command_fallback_audit_enabled``); off, this
module's ``install`` is a no-op and the names ``commands/__init__.py``
exports stay the raw functions the sub-modules define.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any

from rigplane.core.env_config import get_command_fallback_audit_enabled

__all__ = ["install"]

logger = logging.getLogger(__name__)


def _wrap(fn: Any) -> Any:
    """Wrap *fn* so a call with ``cmd_map`` left at ``None`` logs once.

    Preserves *fn*'s return value and exception behaviour exactly: it never
    substitutes its own error for one *fn* would have raised, and it always
    returns exactly what ``fn(*args, **kwargs)`` returns.
    """
    signature = inspect.signature(fn)

    @functools.wraps(fn)
    def _audited(*args: Any, **kwargs: Any) -> Any:
        try:
            bound = signature.bind(*args, **kwargs)
        except TypeError:
            # Let fn raise its own error for a call its signature rejects,
            # unshadowed by a binding failure diagnosed here instead.
            return fn(*args, **kwargs)
        bound.apply_defaults()
        if bound.arguments.get("cmd_map") is None:
            logger.warning(
                "command fallback audit: %s called without cmd_map "
                "(hardcoded fallback engaged)",
                fn.__qualname__,
            )
        return fn(*args, **kwargs)

    return _audited


def install(namespace: dict[str, Any]) -> None:
    """Replace every ``cmd_map``-taking function in *namespace* in place.

    Called once, from the bottom of ``commands/__init__.py``, with its own
    ``globals()`` after every builder re-export is bound. A no-op unless
    ``RIGPLANE_COMMAND_FALLBACK_AUDIT`` is set: with the flag off,
    *namespace* is left untouched.

    A builder reachable under more than one name in *namespace* (a
    backward-compat alias such as ``speech = get_speech``) is wrapped once;
    every name bound to it receives the same wrapped object, so the alias
    relationship -- and therefore "one call, one warning" regardless of
    which name it came through -- survives wrapping.
    """
    if not get_command_fallback_audit_enabled():
        return
    wrapped_by_id: dict[int, Any] = {}
    for name, value in list(namespace.items()):
        if not inspect.isfunction(value):
            continue
        if "cmd_map" not in inspect.signature(value).parameters:
            continue
        key = id(value)
        if key not in wrapped_by_id:
            wrapped_by_id[key] = _wrap(value)
        namespace[name] = wrapped_by_id[key]
