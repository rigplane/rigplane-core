"""Shared public command-builder census for tests."""

from __future__ import annotations

import functools
import importlib
import inspect
from pathlib import Path
from typing import Any

CommandBuilderKey = tuple[str, str]


@functools.lru_cache(maxsize=4)
def public_command_builders(command_dir: Path) -> dict[CommandBuilderKey, Any]:
    """Return public command-package functions whose signature accepts ``cmd_map``."""
    found: dict[CommandBuilderKey, Any] = {}
    for path in sorted(command_dir.glob("*.py")):
        if path.stem == "__init__":
            continue
        module = importlib.import_module(f"rigplane.commands.{path.stem}")
        for value in vars(module).values():
            if not inspect.isfunction(value) or value.__name__.startswith("_"):
                continue
            if value.__module__ != module.__name__:
                continue
            if "cmd_map" in inspect.signature(value).parameters:
                found[(path.name, value.__name__)] = value
    return found
