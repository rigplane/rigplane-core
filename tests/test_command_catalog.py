"""Regression check: command catalog docs stay in sync with ControlHandler._COMMANDS.

If this test fails, it means a command was added to ControlHandler._COMMANDS
without documenting it in docs/api/command-catalog.md.  Add a section for the
new command before pushing.
"""

from __future__ import annotations

from pathlib import Path

from rigplane.web.handlers.control import ControlHandler

_CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "api" / "command-catalog.md"
)

# Commands that are intentionally omitted from the public catalog because they
# are backward-compat aliases documented under their preferred canonical name.
_ALIAS_ONLY: frozenset[str] = frozenset(
    {
        "set_power",  # alias for set_rf_power
        "set_squelch",  # alias for set_sql
        "set_compressor",  # alias for set_comp
        "select_vfo",  # alias for set_vfo
        "set_ipplus",  # alias for set_ip_plus
        "set_attenuator",  # alias for set_att
    }
)


def _catalog_text() -> str:
    return _CATALOG_PATH.read_text(encoding="utf-8")


def test_all_commands_documented_in_catalog() -> None:
    """Every command in ControlHandler._COMMANDS must appear in the catalog."""
    catalog = _catalog_text()
    missing = []
    for name in sorted(ControlHandler._COMMANDS):  # noqa: SLF001
        if name in _ALIAS_ONLY:
            continue
        # The catalog documents each command with a ### `<name>` heading.
        marker = f"`{name}`"
        if marker not in catalog:
            missing.append(name)

    assert not missing, (
        "The following commands are in ControlHandler._COMMANDS but not "
        "documented in docs/api/command-catalog.md:\n"
        + "\n".join(f"  - {n}" for n in missing)
        + "\n\nAdd a ### `<name>` section for each missing command."
    )


def test_catalog_commands_exist_in_handler() -> None:
    """Every ### `<name>` heading in the catalog must be in _COMMANDS (or alias list)."""
    import re

    catalog = _catalog_text()
    # Match ### `command_name` headings (command names use lowercase + underscores)
    documented = set(re.findall(r"###\s+`([a-z][a-z0-9_]*)`", catalog))
    # Remove the alias variants that are documented inside combined headings
    # like `set_att` / `set_attenuator` — both appear in the markdown
    all_known = ControlHandler._COMMANDS | _ALIAS_ONLY  # noqa: SLF001
    stale = [n for n in sorted(documented) if n not in all_known]
    assert not stale, (
        "The following command names appear in the catalog heading but are NOT "
        "in ControlHandler._COMMANDS (stale docs):\n"
        + "\n".join(f"  - {n}" for n in stale)
    )
