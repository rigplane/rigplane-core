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
import itertools
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from pydantic import ValidationError

from rigplane.profiles import get_radio_profile
from rigplane.web.runtime_helpers import declared_field_paths
from rigplane.web.state_schema import StateUpdateEnvelope

_ROOT = Path(__file__).resolve().parents[2]
_GENERATOR_PATH = _ROOT / "scripts" / "gen_state_wire_goldens.py"
_REGEN_COMMAND = "uv run python scripts/gen_state_wire_goldens.py"
_LIFECYCLE_PREFIXES = ("connection.connection.", "health.health.")
# Each ``time.monotonic`` call the web server makes advances the fake clock
# by this much, so any value derived from elapsed monotonic time (the radio
# health age) grows by whole seconds instead of staying below 1 ms.
_MONOTONIC_JUMP_S = 5.0


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

# FTX-1 and IC-705 declare the tone_squelch_type receiver field; the server
# publishes it as `toneSquelchType` once it is observed, and the schema has no
# such field.
_SCHEMA_GAP_CASES = frozenset({("FTX-1", "allObserved"), ("IC-705", "allObserved")})
_SCHEMA_GAP = pytest.mark.xfail(
    strict=True,
    raises=ValidationError,
    reason=(
        "ReceiverStatePublic has no toneSquelchType field, but the server "
        "publishes one once tone_squelch_type is observed"
    ),
)


def _case_param(model: str, case: str) -> Any:
    marks = [_SCHEMA_GAP] if (model, case) in _SCHEMA_GAP_CASES else []
    return pytest.param(model, case, marks=marks, id=f"{model}-{case}")


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


def test_monotonic_progress_does_not_drift_the_render() -> None:
    """The render still equals the committed file under a running clock.

    ``WebServer`` derives ``radioHealth.sinceMs`` from the monotonic clock,
    twice per envelope build (``server.py: _snapshot_for_delivery`` and
    ``_build_public_state_from_snapshot`` both call ``_build_radio_health``,
    and the second call sees the same health signature). Advancing the clock
    several seconds per call therefore turns an unpinned health age into a
    large ``sinceMs`` while the committed golden says 0.
    """
    committed = _GENERATOR.GOLDEN_PATH.read_text(encoding="utf-8")
    clock = itertools.count(time.monotonic(), _MONOTONIC_JUMP_S)

    with mock.patch("rigplane.web.server.time.monotonic", side_effect=clock.__next__):
        rendered = _GENERATOR.render_goldens()

    assert rendered == committed, (
        "the render varies with the monotonic clock; pin the health age "
        f"(radioHealth.sinceMs) in the generator: {_REGEN_COMMAND}"
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


@pytest.mark.parametrize(("model", "case"), sorted(_SCHEMA_GAP_CASES))
def test_schema_gap_is_only_tone_squelch_type(model: str, case: str) -> None:
    """The strict xfail above must not hide a second schema violation."""
    with pytest.raises(ValidationError) as caught:
        StateUpdateEnvelope.model_validate(_GOLDENS[model][case])

    failures = {(error["loc"][-1], error["type"]) for error in caught.value.errors()}
    assert failures == {("toneSquelchType", "extra_forbidden")}


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
