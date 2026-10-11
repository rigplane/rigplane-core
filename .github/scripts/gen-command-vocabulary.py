#!/usr/bin/env python3
"""Regenerate the web command-vocabulary fixture.

Pipeline:

    ControlHandler._COMMANDS (rigplane.web.handlers.control)
        + command_descriptor(name) (rigplane.core.command_dispatch)
        -> contracts/command-vocabulary.json

``commands`` maps every name in ``ControlHandler._COMMANDS`` to ``null``
when ``command_descriptor`` resolves no ``CommandDescriptor`` for it, else to:

* ``descriptor`` -- ``CommandDescriptor.name``;
* ``result_keys`` -- the keys ``CommandDescriptor.result`` returns
  (``result_names``, else ``argument_names``).

``tests/test_command_vocabulary_contract.py`` fails when this file is stale;
``frontend/src/lib/runtime/commands/__tests__/command-vocabulary-contract.isolated.test.ts``
checks ``intentSpecs`` in ``radio-intents.ts`` against it.

Usage:
    python .github/scripts/gen-command-vocabulary.py           # write the fixture
    python .github/scripts/gen-command-vocabulary.py --check   # exit 1 if stale
    python .github/scripts/gen-command-vocabulary.py --stdout  # print it only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo layout: .github/scripts/ -> repo root; the package lives under src/.
_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rigplane.core.command_dispatch import command_descriptor  # noqa: E402
from rigplane.web.handlers.control import ControlHandler  # noqa: E402, TID251

FIXTURE_PATH = _REPO_ROOT / "contracts" / "command-vocabulary.json"


def _entry(name: str) -> dict[str, Any] | None:
    descriptor = command_descriptor(name)
    if descriptor is None:
        return None
    keys = (
        descriptor.argument_names
        if descriptor.result_names is None
        else descriptor.result_names
    )
    return {"descriptor": descriptor.name, "result_keys": list(keys)}


def build_document() -> dict[str, Any]:
    names = ControlHandler._COMMANDS  # noqa: SLF001
    return {
        "generator": ".github/scripts/gen-command-vocabulary.py",
        "source": "rigplane.web.handlers.control.ControlHandler._COMMANDS",
        "commands": {name: _entry(name) for name in names},
    }


def fixture_document() -> str:
    return json.dumps(build_document(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="exit 1 if stale")
    mode.add_argument("--stdout", action="store_true", help="print, do not write")
    args = parser.parse_args(argv)

    document = fixture_document()
    if args.stdout:
        sys.stdout.write(document)
        return 0
    if args.check:
        current = (
            FIXTURE_PATH.read_text(encoding="utf-8") if FIXTURE_PATH.exists() else ""
        )
        if current != document:
            print(
                f"{FIXTURE_PATH.relative_to(_REPO_ROOT)} is stale; run "
                "`uv run python .github/scripts/gen-command-vocabulary.py`",
                file=sys.stderr,
            )
            return 1
        return 0
    FIXTURE_PATH.write_text(document, encoding="utf-8")
    print(f"wrote {FIXTURE_PATH.relative_to(_REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
