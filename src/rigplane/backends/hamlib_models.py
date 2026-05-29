"""Hamlib model metadata catalog loaded from installed Hamlib tools."""

from __future__ import annotations

import dataclasses
import logging
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = [
    "HamlibCaps",
    "HamlibModelCatalog",
    "HamlibModelMetadata",
    "load_hamlib_caps",
    "load_hamlib_model_catalog",
    "parse_hamlib_dump_caps",
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


# ---------------------------------------------------------------------------
# HamlibCaps — normalized dump_caps view
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HamlibCaps:
    """Normalized view of a Hamlib ``dump_caps`` output."""

    get_funcs: frozenset[str] = frozenset()
    set_funcs: frozenset[str] = frozenset()
    get_levels: frozenset[str] = frozenset()
    set_levels: frozenset[str] = frozenset()
    modes: frozenset[str] = frozenset()
    vfo_ops: frozenset[str] = frozenset()
    ptt_type: str | None = None
    has_set_freq: bool = False
    model_id: int | None = None
    degraded_reason: str | None = None


def _parse_token(raw: str) -> str:
    """Strip granularity annotation: ``RF(0..1/0.003922)`` → ``RF``."""
    paren = raw.find("(")
    return raw[:paren] if paren != -1 else raw


def _parse_token_list(value: str) -> frozenset[str]:
    """Parse a space-separated token list, stripping granularity annotations."""
    return frozenset(t for raw in value.split() if (t := _parse_token(raw)))


def parse_hamlib_dump_caps(text: str) -> HamlibCaps:
    """Parse ``rigctl -m <id> --dump-caps`` stdout into a :class:`HamlibCaps`.

    Only lines starting at column 0 (not indented) are dispatched, which
    excludes ``Extra functions:``/``Extra levels:`` indented sub-blocks.
    Never raises; returns a degraded :class:`HamlibCaps` on any error or
    when no recognized sections are found.
    """
    try:
        get_funcs: frozenset[str] = frozenset()
        set_funcs: frozenset[str] = frozenset()
        get_levels: frozenset[str] = frozenset()
        set_levels: frozenset[str] = frozenset()
        modes: frozenset[str] = frozenset()
        vfo_ops: frozenset[str] = frozenset()
        ptt_type: str | None = None
        has_set_freq: bool = False
        recognized = 0

        for line in text.splitlines():
            # Only dispatch column-0 lines; skip indented sub-blocks entirely.
            if not line or line[0] in (" ", "\t"):
                continue

            if line.startswith("Get functions:"):
                get_funcs = _parse_token_list(line[len("Get functions:") :])
                recognized += 1
            elif line.startswith("Set functions:"):
                set_funcs = _parse_token_list(line[len("Set functions:") :])
                recognized += 1
            elif line.startswith("Get level gran:"):
                get_levels = _parse_token_list(line[len("Get level gran:") :])
                recognized += 1
            elif line.startswith("Get level:"):
                get_levels = _parse_token_list(line[len("Get level:") :])
                recognized += 1
            elif line.startswith("Set level gran:"):
                set_levels = _parse_token_list(line[len("Set level gran:") :])
                recognized += 1
            elif line.startswith("Set level:"):
                set_levels = _parse_token_list(line[len("Set level:") :])
                recognized += 1
            elif line.startswith("Mode list:"):
                modes = _parse_token_list(line[len("Mode list:") :])
                recognized += 1
            elif line.startswith("VFO Ops:"):
                vfo_ops = _parse_token_list(line[len("VFO Ops:") :])
                recognized += 1
            elif line.startswith("PTT type:"):
                raw_val = line[len("PTT type:") :].strip()
                ptt_type = None if raw_val == "None" else (raw_val or None)
                recognized += 1
            elif line.startswith("Can set Frequency:"):
                raw_val = line[len("Can set Frequency:") :].strip()
                has_set_freq = raw_val == "Y"
                recognized += 1

        if recognized == 0:
            return HamlibCaps(
                degraded_reason="dump_caps parse produced no recognized sections"
            )

        return HamlibCaps(
            get_funcs=get_funcs,
            set_funcs=set_funcs,
            get_levels=get_levels,
            set_levels=set_levels,
            modes=modes,
            vfo_ops=vfo_ops,
            ptt_type=ptt_type,
            has_set_freq=has_set_freq,
        )
    except Exception:  # noqa: BLE001
        logger.debug("dump_caps parse failed", exc_info=True)
        return HamlibCaps(
            degraded_reason="dump_caps parse produced no recognized sections"
        )


def load_hamlib_caps(
    model_id: int,
    tool: str = "rigctl",
    timeout: float = _DEFAULT_TIMEOUT,
) -> HamlibCaps:
    """Shell out to ``rigctl -m <model_id> --dump-caps`` and parse the result.

    Mirrors ``_load_from_tool`` error-handling: missing tool, timeout, OS
    errors, and nonzero exit codes all degrade to an empty :class:`HamlibCaps`
    with a fixed ``degraded_reason``. Raw subprocess output is NEVER
    interpolated into ``degraded_reason``.
    """
    try:
        completed = subprocess.run(
            [tool, "-m", str(model_id), "--dump-caps"],
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        logger.debug("dump_caps tool not found: %s", tool)
        return HamlibCaps(
            degraded_reason="dump_caps unavailable: tool not found",
            model_id=model_id,
        )
    except subprocess.TimeoutExpired:
        logger.debug("dump_caps command timed out: %s model=%d", tool, model_id)
        return HamlibCaps(
            degraded_reason="dump_caps unavailable: command timed out",
            model_id=model_id,
        )
    except OSError as exc:
        logger.debug("dump_caps command failed to start: %s: %s", tool, exc)
        return HamlibCaps(
            degraded_reason="dump_caps unavailable: command failed",
            model_id=model_id,
        )

    if completed.returncode != 0:
        logger.debug(
            "dump_caps command failed: %s model=%d rc=%d",
            tool,
            model_id,
            completed.returncode,
        )
        return HamlibCaps(
            degraded_reason="dump_caps unavailable: command failed",
            model_id=model_id,
        )

    caps = parse_hamlib_dump_caps(completed.stdout or "")
    return dataclasses.replace(caps, model_id=model_id)
