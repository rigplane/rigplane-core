"""Hamlib model metadata catalog loaded from installed Hamlib tools."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = [
    "HamlibModelCatalog",
    "HamlibModelMetadata",
    "load_hamlib_model_catalog",
    "parse_hamlib_model_list",
]

logger = logging.getLogger(__name__)

_DEFAULT_TOOLS = ("rigctld", "rigctl")
_DEFAULT_TIMEOUT = 2.0
_CATALOG_CACHE: dict[tuple[tuple[str, ...], float], "HamlibModelCatalog"] = {}


@dataclass(frozen=True)
class HamlibModelMetadata:
    """Metadata for one Hamlib rig model entry."""

    model_id: int
    name: str
    version: str | None = None
    status: str | None = None
    default_connection_hints: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class HamlibModelCatalog:
    """Parsed Hamlib model catalog, or a degraded empty catalog."""

    models: dict[int, HamlibModelMetadata]
    degraded_reason: str | None = None
    source_tool: str | None = None


def parse_hamlib_model_list(text: str) -> dict[int, HamlibModelMetadata]:
    """Parse ``rigctld -l`` / ``rigctl -l`` model output by stable columns.

    Hamlib model names may contain spaces. Parse each line from the right:
    first token is the numeric model ID, last token is status, penultimate
    token is version, and the middle tokens form the display name.
    """
    models: dict[int, HamlibModelMetadata] = {}
    for raw_line in text.splitlines():
        fields = raw_line.split()
        if len(fields) < 4:
            continue
        try:
            model_id = int(fields[0])
        except ValueError:
            continue

        name = " ".join(fields[1:-2]).strip()
        if not name:
            continue
        models[model_id] = HamlibModelMetadata(
            model_id=model_id,
            name=name,
            version=fields[-2],
            status=fields[-1],
        )
    return models


def load_hamlib_model_catalog(
    tools: Iterable[str] = _DEFAULT_TOOLS,
    timeout: float = _DEFAULT_TIMEOUT,
) -> HamlibModelCatalog:
    """Load and cache Hamlib rig model metadata from installed command-line tools.

    ``rigctld -l`` is preferred, with ``rigctl -l`` as fallback. Missing tools,
    timeouts, nonzero exits, and unparseable output degrade to an empty catalog
    instead of raising.
    """
    tool_tuple = tuple(tools)
    cache_key = (tool_tuple, timeout)
    cached = _CATALOG_CACHE.get(cache_key)
    if cached is not None:
        return cached

    last_reason = "hamlib model list unavailable: no tools configured"
    for tool in tool_tuple:
        catalog, reason = _load_from_tool(tool, timeout)
        if catalog is not None:
            _CATALOG_CACHE[cache_key] = catalog
            return catalog
        last_reason = reason

    degraded = HamlibModelCatalog(models={}, degraded_reason=last_reason)
    _CATALOG_CACHE[cache_key] = degraded
    return degraded


def _load_from_tool(
    tool: str,
    timeout: float,
) -> tuple[HamlibModelCatalog | None, str]:
    try:
        completed = subprocess.run(
            [tool, "-l"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        logger.debug("hamlib model list tool not found: %s", tool)
        return None, "hamlib model list unavailable: tool not found"
    except subprocess.TimeoutExpired:
        logger.debug("hamlib model list command timed out: %s", tool)
        return None, "hamlib model list unavailable: command timed out"
    except OSError as exc:
        logger.debug("hamlib model list command failed to start: %s: %s", tool, exc)
        return None, "hamlib model list unavailable: command failed"

    if completed.returncode != 0:
        logger.debug(
            "hamlib model list command failed: %s rc=%d",
            tool,
            completed.returncode,
        )
        return None, "hamlib model list unavailable: command failed"

    models = parse_hamlib_model_list(completed.stdout or "")
    if not models:
        logger.debug("hamlib model list had no parseable models: %s", tool)
        return None, "hamlib model list unavailable: no parseable models"

    return HamlibModelCatalog(models=models, source_tool=tool), ""
