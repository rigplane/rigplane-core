"""Regression check: docs/api/command-catalog.md stays aligned with ControlHandler._COMMANDS."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CATALOG_PATH = Path(__file__).parent.parent / "docs" / "api" / "command-catalog.md"
_MARKER_BEGIN = "<!-- catalog:begin -->"
_MARKER_END = "<!-- catalog:end -->"


def _catalog_region() -> str:
    text = CATALOG_PATH.read_text(encoding="utf-8")
    try:
        begin = text.index(_MARKER_BEGIN) + len(_MARKER_BEGIN)
        end = text.index(_MARKER_END)
    except ValueError as exc:
        pytest.fail(f"Command catalog marker missing in {CATALOG_PATH}: {exc}")
    return text[begin:end]


def _catalog_command_names() -> set[str]:
    """Extract command names from first-column `` `name` `` cells in the catalog region."""
    return set(re.findall(r"^\|\s*`([^`]+)`", _catalog_region(), re.MULTILINE))


def test_catalog_covers_all_commands() -> None:
    """Catalog covers every name in ControlHandler._COMMANDS — no more, no less."""
    from rigplane.web.handlers.control import ControlHandler

    catalog = _catalog_command_names()
    missing = ControlHandler._COMMANDS - catalog
    extra = catalog - ControlHandler._COMMANDS
    assert not missing, f"Commands in _COMMANDS missing from catalog: {sorted(missing)}"
    assert not extra, f"Catalog has names not in _COMMANDS: {sorted(extra)}"


def test_catalog_read_only_marked_no_batch() -> None:
    """Every _READ_ONLY_HANDLERS command is marked 'No' in the Batch column."""
    from rigplane.web.handlers.control import ControlHandler

    region = _catalog_region()
    # Match table rows where the 4th pipe-delimited column is 'No':
    # | `name` | params | capability | No | notes |
    batch_no_names: set[str] = set(
        re.findall(
            r"^\|\s*`([^`]+)`[^|]*\|[^|]*\|[^|]*\|\s*No\s*\|",
            region,
            re.MULTILINE,
        )
    )
    read_only = set(ControlHandler._READ_ONLY_HANDLERS.keys())
    missing_no = read_only - batch_no_names
    assert not missing_no, (
        f"Read-only commands not marked 'No' in Batch column: {sorted(missing_no)}"
    )
    extra_no = batch_no_names - read_only
    assert not extra_no, (
        f"Queue-backed commands mistakenly marked 'No' in Batch column: {sorted(extra_no)}"
    )
