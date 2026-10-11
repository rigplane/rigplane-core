"""Command vocabulary: ``ControlHandler._COMMANDS`` vs the committed fixture.

``.github/scripts/gen-command-vocabulary.py`` writes
``contracts/command-vocabulary.json``; the vitest file
``frontend/src/lib/runtime/commands/__tests__/command-vocabulary-contract.isolated.test.ts``
checks ``intentSpecs`` in ``radio-intents.ts`` against the same fixture.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPO_ROOT / ".github" / "scripts" / "gen-command-vocabulary.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_command_vocabulary", GENERATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_command_vocabulary_fixture_is_fresh() -> None:
    generator = _load_generator()
    committed = generator.FIXTURE_PATH.read_text(encoding="utf-8")
    assert committed == generator.fixture_document(), (
        "contracts/command-vocabulary.json is stale. Run "
        "`uv run python .github/scripts/gen-command-vocabulary.py` and commit the "
        "result; the frontend intent check reads the same file."
    )
