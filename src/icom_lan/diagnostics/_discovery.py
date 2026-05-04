"""Diagnostic contributor discovery — built-in + entry points + runtime register."""

from __future__ import annotations

import importlib.metadata
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from icom_lan.diagnostics.contributor import DiagnosticContributor

from icom_lan.diagnostics.contributors import (
    AudioContributor,
    ConfigContributor,
    DependenciesContributor,
    ErrorsContributor,
    InvocationContributor,
    LogsContributor,
    RadioContributor,
    StateContributor,
    SystemContributor,
)

logger = logging.getLogger(__name__)

_ENTRY_POINT_GROUP = "icom_lan.diagnostics"

# Built-in contributors are populated incrementally by #1390-#1392.
# #1390: system, invocation, dependencies, config.
# #1391: radio, audio.
# #1392: logs, state, errors.
_BUILT_IN_CONTRIBUTORS: list[type["DiagnosticContributor"]] = [
    SystemContributor,
    InvocationContributor,
    DependenciesContributor,
    ConfigContributor,
    RadioContributor,
    AudioContributor,
    LogsContributor,
    StateContributor,
    ErrorsContributor,
]

_RUNTIME_REGISTERED: list[type["DiagnosticContributor"]] = []


def register(contributor_cls: type["DiagnosticContributor"]) -> None:
    """Programmatically register a contributor (for testing or dynamic plugins).

    Runtime-registered contributors win over entry points and built-ins
    when names collide.
    """
    _RUNTIME_REGISTERED.append(contributor_cls)


def discover() -> list["DiagnosticContributor"]:
    """Return contributor instances (built-in + entry-point + runtime), dedup by ``name``.

    Precedence (last wins on name collision):
        built-in  <  entry-point  <  runtime-registered
    """
    instances: dict[str, DiagnosticContributor] = {}

    for cls in _BUILT_IN_CONTRIBUTORS:
        try:
            inst = cls()
            instances[inst.name] = inst
        except Exception:
            logger.warning(
                "diagnostics: failed to instantiate built-in contributor %s",
                cls.__name__,
                exc_info=True,
            )

    eps: Iterable[importlib.metadata.EntryPoint]
    try:
        eps = importlib.metadata.entry_points(group=_ENTRY_POINT_GROUP)
    except Exception:
        logger.warning(
            "diagnostics: failed to enumerate entry points %s",
            _ENTRY_POINT_GROUP,
            exc_info=True,
        )
        eps = ()

    for ep in eps:
        try:
            cls = ep.load()
            inst = cls()
            instances[inst.name] = inst
        except Exception:
            logger.warning(
                "diagnostics: failed to load entry point %s",
                ep.name,
                exc_info=True,
            )

    for cls in _RUNTIME_REGISTERED:
        try:
            inst = cls()
            instances[inst.name] = inst
        except Exception:
            logger.warning(
                "diagnostics: failed to instantiate runtime-registered %s",
                cls.__name__,
                exc_info=True,
            )

    return list(instances.values())
