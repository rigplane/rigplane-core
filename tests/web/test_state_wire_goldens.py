"""The committed state wire goldens: current, and valid against the wire schema.

``scripts/gen_state_wire_goldens.py`` writes
``frontend/src/lib/transport/__tests__/fixtures/state-wire-goldens.json`` from
the web server's own producers. The vitest suite
``state-wire-goldens.integration.isolated.test.ts`` feeds the same file through
the frontend websocket client, stores and view-model adapter. These tests keep
the file equal to what the producers emit today and check each payload
against ``StateUpdateEnvelope``. A producer change fails them until the file
is regenerated and the diff reviewed:

    uv run python scripts/gen_state_wire_goldens.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from rigplane.profiles import get_radio_profile
from rigplane.web.runtime_helpers import declared_field_paths
from rigplane.web.state_schema import StateUpdateEnvelope

_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_PATH = _ROOT / "scripts" / "gen_state_wire_goldens.py"
_REGEN_COMMAND = "uv run python scripts/gen_state_wire_goldens.py"
_LIFECYCLE_PREFIXES = ("connection.connection.", "health.health.")


def _load_generator() -> Any:
    spec = importlib.util.spec_from_file_location(
        "gen_state_wire_goldens", _GENERATOR_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


_GENERATOR = _load_generator()
_GOLDENS: dict[str, dict[str, Any]] = json.loads(
    _GENERATOR.GOLDEN_PATH.read_text(encoding="utf-8")
)
_ENVELOPE_CASES = [
    (model, case)
    for model, entry in sorted(_GOLDENS.items())
    for case in ("allObserved", "allUnobserved")
    if case in entry
]


def _case_param(model: str, case: str) -> Any:
    return pytest.param(model, case, id=f"{model}-{case}")


def _declared_store_paths(model: str) -> set[str]:
    acquisition = get_radio_profile(model).state_acquisition
    if acquisition is None:
        return set()
    return {str(path) for path in declared_field_paths(acquisition)}


def _observed_store_paths(envelope: dict[str, Any]) -> set[str]:
    return {
        status["storePath"]
        for status in envelope["data"]["fieldStatus"].values()
        if status["observed"]
    }


def test_committed_golden_is_current() -> None:
    """The committed file equals what the producers emit now."""
    committed = _GENERATOR.GOLDEN_PATH.read_text(encoding="utf-8")

    assert committed == _GENERATOR.render_goldens(), (
        f"state-wire-goldens.json is stale; regenerate and commit it: {_REGEN_COMMAND}"
    )


def test_generator_check_reports_no_drift() -> None:
    """``--check`` agrees the committed file is byte-for-byte canonical."""
    result = subprocess.run(
        [sys.executable, str(_GENERATOR_PATH), "--check"],
        cwd=_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        f"--check reported drift; regenerate and commit it: {_REGEN_COMMAND}\n"
        f"{result.stdout}{result.stderr}"
    )


@pytest.mark.parametrize("model", sorted(_GOLDENS))
def test_all_observed_exists_only_for_profiles_with_acquisition(model: str) -> None:
    has_acquisition = get_radio_profile(model).state_acquisition is not None

    assert ("allObserved" in _GOLDENS[model]) is has_acquisition


@pytest.mark.parametrize(
    ("model", "case"), [_case_param(model, case) for model, case in _ENVELOPE_CASES]
)
def test_envelope_matches_wire_schema(model: str, case: str) -> None:
    validated = StateUpdateEnvelope.model_validate(_GOLDENS[model][case])

    assert validated.type == "full"


@pytest.mark.parametrize(("model", "case"), _ENVELOPE_CASES)
def test_envelope_and_capabilities_share_one_epoch(model: str, case: str) -> None:
    entry = _GOLDENS[model]
    envelope = entry[case]

    assert envelope["type"] == "full"
    assert envelope["stateContractVersion"] == envelope["data"]["stateContractVersion"]
    assert envelope["providerGeneration"] == envelope["data"]["providerGeneration"]
    assert envelope["providerGeneration"] == entry["capabilities"]["providerGeneration"]
    assert envelope["stateContractVersion"] == 1


@pytest.mark.parametrize(
    "model", [model for model, case in _ENVELOPE_CASES if case == "allObserved"]
)
def test_all_observed_observes_every_declared_path(model: str) -> None:
    declared = _declared_store_paths(model)
    observed = _observed_store_paths(_GOLDENS[model]["allObserved"])

    assert declared
    assert declared - observed == set()


@pytest.mark.parametrize("model", sorted(_GOLDENS))
def test_all_unobserved_observes_only_lifecycle_paths(model: str) -> None:
    observed = _observed_store_paths(_GOLDENS[model]["allUnobserved"])

    assert observed
    assert all(path.startswith(_LIFECYCLE_PREFIXES) for path in observed)
    assert _declared_store_paths(model) & observed == set()
