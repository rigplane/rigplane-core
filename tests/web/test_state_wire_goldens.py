"""The committed state wire goldens: current, and valid against the wire schema.

``scripts/gen_state_wire_goldens.py`` writes
``frontend/src/lib/transport/__tests__/fixtures/state-wire-goldens.json`` from
the web server's own producers. The vitest suite
``state-wire-goldens.integration.isolated.test.ts`` feeds the same file through
the frontend websocket client, stores and view-model adapter. These tests keep
the file equal to what the producers emit today and check each payload
against ``StateUpdateEnvelope``. Every profile in ``rigs/*.toml`` must have a
capabilities entry; the generator's excluded models contribute capabilities
only. A producer change fails them until the file
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

from rigplane.profiles import get_radio_profile
from rigplane.profiles.rig_loader import discover_rigs
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
_POLLED_DCD_CASES = ("polledDcdOpen", "polledDcdClosed", "polledDcdUnanswered")
_POLLED_DCD_DELTA_CASES = ("polledDcdDeltaOpen", "polledDcdDeltaClosed")
_POLLED_DCD_MODELS = _GENERATOR.polled_dcd_models()
_POLLED_DCD_ENVELOPES = [
    (model, case) for model in _POLLED_DCD_MODELS for case in _POLLED_DCD_CASES
]
_ALL_RIG_MODELS = sorted(discover_rigs(_ROOT / "rigs"))
# The generator keeps these profiles' state payloads out of the golden; their
# capabilities entries must still be there.
_EXCLUDED_MODELS = _GENERATOR._EXCLUDED_MODELS
_INCLUDED_MODELS = [model for model in _ALL_RIG_MODELS if model not in _EXCLUDED_MODELS]


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


@pytest.mark.parametrize("model", sorted(_EXCLUDED_MODELS))
def test_excluded_models_contribute_capabilities_only(model: str) -> None:
    assert sorted(_GOLDENS[model]) == ["capabilities"]


def test_every_rig_profile_has_a_capabilities_entry() -> None:
    """Every ``rigs/*.toml`` profile must have a capabilities entry."""
    assert _EXCLUDED_MODELS <= set(_ALL_RIG_MODELS)
    missing = [
        model
        for model in _ALL_RIG_MODELS
        if "capabilities" not in _GOLDENS.get(model, {})
    ]

    assert missing == []


@pytest.mark.parametrize("model", _INCLUDED_MODELS)
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


@pytest.mark.parametrize("model", _INCLUDED_MODELS)
def test_all_unobserved_observes_only_lifecycle_paths(model: str) -> None:
    observed = _observed_store_paths(_GOLDENS[model]["allUnobserved"])

    assert observed
    assert all(path.startswith(_LIFECYCLE_PREFIXES) for path in observed)
    assert _declared_store_paths(model) & observed == set()


def test_polled_dcd_cases_exist_exactly_for_the_profiles_that_poll_dcd() -> None:
    """The BUSY-lamp cases exist for every profile that polls ``dcd``, no more."""
    with_cases = {
        model
        for model, entry in _GOLDENS.items()
        if any(
            case in entry
            for case in (
                *_POLLED_DCD_CASES,
                *_POLLED_DCD_DELTA_CASES,
                "polledDcdCapabilities",
            )
        )
    }

    assert with_cases == set(_POLLED_DCD_MODELS)
    assert {"IC-705", "IC-7300"} <= with_cases
    for model in with_cases:
        assert set(_GOLDENS[model]) >= {
            *_POLLED_DCD_CASES,
            *_POLLED_DCD_DELTA_CASES,
            "polledDcdCapabilities",
        }


@pytest.mark.parametrize(("model", "case"), _POLLED_DCD_ENVELOPES)
def test_polled_dcd_envelope_matches_wire_schema(model: str, case: str) -> None:
    validated = StateUpdateEnvelope.model_validate(_GOLDENS[model][case])

    assert validated.type == "full"


@pytest.mark.parametrize("case", _POLLED_DCD_DELTA_CASES)
@pytest.mark.parametrize("model", _POLLED_DCD_MODELS)
def test_polled_dcd_delta_is_a_wire_delta_carrying_the_flipped_leaf(
    model: str, case: str
) -> None:
    """The live squelch-change frame: a schema-valid delta with dcd and its status."""
    envelope = _GOLDENS[model][case]
    validated = StateUpdateEnvelope.model_validate(envelope)
    opened = case == "polledDcdDeltaOpen"
    changed = envelope["changed"]

    assert validated.type == "delta"
    assert changed["main"]["dcd"] is opened
    assert changed["main"]["sMeterSqlOpen"] is opened
    assert changed["fieldStatus"]["main.dcd"]["observed"] is True
    assert changed["fieldStatus"]["main.dcd"]["availability"] == "available"
    # The delta belongs to the same stream as the polled full envelopes.
    full = _GOLDENS[model]["polledDcdOpen"]
    assert envelope["stateContractVersion"] == full["stateContractVersion"]
    assert envelope["providerGeneration"] == full["providerGeneration"]


@pytest.mark.parametrize(("model", "case"), _POLLED_DCD_ENVELOPES)
def test_polled_dcd_envelope_and_capabilities_share_one_epoch(
    model: str, case: str
) -> None:
    entry = _GOLDENS[model]
    envelope = entry[case]
    capabilities = entry["polledDcdCapabilities"]

    assert envelope["stateContractVersion"] == capabilities["stateContractVersion"]
    assert envelope["providerGeneration"] == capabilities["providerGeneration"]
    assert capabilities["model"] == model


@pytest.mark.parametrize("model", _POLLED_DCD_MODELS)
def test_polled_dcd_capabilities_declare_the_squelch_fact(model: str) -> None:
    """The BUSY lamp exists only with the ``squelch`` tag a radio-backed server serves."""
    entry = _GOLDENS[model]

    assert "squelch" in entry["polledDcdCapabilities"]["capabilities"]
    assert "squelch" not in entry["capabilities"]["capabilities"]


@pytest.mark.parametrize(
    ("case", "value"),
    [
        ("polledDcdOpen", True),
        ("polledDcdClosed", False),
        ("polledDcdUnanswered", None),
    ],
)
@pytest.mark.parametrize("model", _POLLED_DCD_MODELS)
def test_polled_squelch_reply_publishes_dcd_in_the_envelope(
    model: str, case: str, value: bool | None
) -> None:
    """A polled 0x15 0x01 reply moves dcd; no reply leaves it unobserved."""
    data = _GOLDENS[model][case]["data"]
    row = data["fieldStatus"]["main.dcd"]

    assert data["main"].get("dcd") is value
    assert data["main"].get("sMeterSqlOpen") is value
    assert row["observed"] is (value is not None)
    assert row["availability"] == ("available" if value is not None else "missing")
