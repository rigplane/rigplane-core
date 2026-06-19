"""In-suite consumer-driven contract (CDC) check (MOR-883).

Mirrors the dedicated `consumer-contracts-gate.yml` workflow inside the normal
pytest run so a core change that breaks a fielded consumer is caught even when
the dedicated path-filtered gate does not trigger. The real assertion logic
lives in `scripts/run_consumer_contracts.py`; this test imports its public
entrypoint and asserts it exits zero against HEAD.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNNER_PATH = REPO_ROOT / "scripts" / "run_consumer_contracts.py"

pytest.importorskip("jsonschema")


def _load_runner():
    spec = importlib.util.spec_from_file_location("run_consumer_contracts", RUNNER_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_consumer_expectations_satisfied() -> None:
    runner = _load_runner()
    assert runner.main() == 0, "a consumer expectation is violated against HEAD"
