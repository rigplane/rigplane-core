"""Unit contracts for the canonical transmit-observation vocabulary."""

import re
import subprocess
from dataclasses import MISSING, FrozenInstanceError, fields
from importlib import import_module, util
from pathlib import Path
from typing import get_type_hints

import pytest

from rigplane.core.tx_observation import (
    OBSERVED_PTT_PATH,
    RADIO_READBACK_SOURCES,
    TX_READ_DEADLINE_SECONDS,
    ObservedPtt,
    TxStateReading,
    legacy_ptt_bool,
    normalize_observed_ptt,
    project_observed_ptt,
)
from rigplane.core.state_pipeline_contracts import FieldPath, SourceMetadata
from rigplane.core.state_store import FieldSnapshot, FreshnessState, StateSnapshot

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LIVE_ROOTS = ("src/rigplane", "tests", "rigs")


def _retired_reference_patterns() -> tuple[bytes, ...]:
    retired_module = "tx_" + "authority"
    return (
        f"core/{retired_module}.py".encode(),
        f"core.{retired_module}".encode(),
        ("Transmit" + "Authority").encode(),
    )


def _retired_references(content: bytes) -> tuple[bytes, ...]:
    return tuple(
        pattern for pattern in _retired_reference_patterns() if pattern in content
    )


def _tracked_live_paths() -> tuple[Path, ...]:
    result = subprocess.run(
        ("git", "ls-files", "-z", "--", *_LIVE_ROOTS),
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
    )
    return tuple(
        _REPO_ROOT / raw_path.decode()
        for raw_path in result.stdout.split(b"\0")
        if raw_path
    )


def test_live_tree_has_no_retired_transmit_authority_references() -> None:
    violations = [
        f"{path.relative_to(_REPO_ROOT)}: {pattern.decode()}"
        for path in _tracked_live_paths()
        for pattern in _retired_references(path.read_bytes())
    ]
    assert violations == [], "retired TX authority references found:\n" + "\n".join(
        violations
    )


def test_retired_reference_guard_allows_observation_and_generic_authority() -> None:
    valid_references = b"authority tx_observation rigplane.core.tx_observation"
    assert _retired_references(valid_references) == ()


def test_retired_tx_authority_module_does_not_resolve() -> None:
    retired_module = ".".join(("rigplane", "core", "tx_" + "authority"))
    assert util.find_spec(retired_module) is None
    with pytest.raises(ModuleNotFoundError, match=re.escape(retired_module)):
        import_module(retired_module)


def test_tx_observation_contract_is_pinned() -> None:
    reading_fields = fields(TxStateReading)
    expected_fields = "value attributed source verified_readback failure".split()
    assert [field.name for field in reading_fields] == expected_fields
    assert get_type_hints(TxStateReading) == {
        "value": bool | None,
        "attributed": str | None,
        "source": str | None,
        "verified_readback": bool,
        "failure": str | None,
    }
    assert [field.default for field in reading_fields] == [
        MISSING,
        None,
        None,
        False,
        None,
    ]

    reading = TxStateReading(value=False)
    assert not hasattr(reading, "__dict__")
    with pytest.raises(FrozenInstanceError):
        reading.value = True  # type: ignore[misc]

    assert TX_READ_DEADLINE_SECONDS == 0.3
    assert RADIO_READBACK_SOURCES == frozenset(
        {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
    )


def test_observed_ptt_vocabulary_and_path_are_pinned() -> None:
    assert tuple(ObservedPtt) == (
        ObservedPtt.OFF,
        ObservedPtt.ON,
        ObservedPtt.UNKNOWN,
    )
    assert [state.value for state in ObservedPtt] == ["off", "on", "unknown"]
    assert OBSERVED_PTT_PATH == FieldPath.global_("tx_state", "observed_ptt")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (False, ObservedPtt.OFF),
        (True, ObservedPtt.ON),
        (None, ObservedPtt.UNKNOWN),
        (ObservedPtt.OFF, ObservedPtt.OFF),
        (ObservedPtt.ON, ObservedPtt.ON),
        (ObservedPtt.UNKNOWN, ObservedPtt.UNKNOWN),
    ],
)
def test_backend_ptt_values_normalize_to_the_canonical_vocabulary(
    value: bool | None | ObservedPtt,
    expected: ObservedPtt,
) -> None:
    assert normalize_observed_ptt(value) is expected


@pytest.mark.parametrize("value", (0, 1, "off", "on", object()))
def test_backend_ptt_normalization_rejects_coercible_or_malformed_values(
    value: object,
) -> None:
    assert normalize_observed_ptt(value) is ObservedPtt.UNKNOWN


def _snapshot(
    value: object = ObservedPtt.OFF,
    *,
    freshness: FreshnessState = FreshnessState.FRESH,
    field_generation: int = 7,
    snapshot_generation: int = 7,
    observed_at: float = 10.0,
    generated_at: float = 11.0,
    max_age: float | None = 2.0,
) -> StateSnapshot:
    field = FieldSnapshot(
        path=OBSERVED_PTT_PATH,
        value=value,
        freshness=freshness,
        last_observed_monotonic=observed_at,
        max_age=max_age,
        source=SourceMetadata(source="poll_response", provider="test"),
        provider_generation=field_generation,
    )
    return StateSnapshot(
        state_revision=1,
        freshness_revision=0,
        observation_seq=1,
        generated_at_monotonic=generated_at,
        fields=(field,),
        provider_generation=snapshot_generation,
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (ObservedPtt.OFF, ObservedPtt.OFF),
        (ObservedPtt.ON, ObservedPtt.ON),
        (ObservedPtt.UNKNOWN, ObservedPtt.UNKNOWN),
    ],
)
def test_snapshot_projection_returns_fresh_current_canonical_evidence(
    value: ObservedPtt,
    expected: ObservedPtt,
) -> None:
    assert project_observed_ptt(_snapshot(value)) is expected


def test_snapshot_projection_treats_missing_evidence_as_unknown() -> None:
    assert project_observed_ptt(StateSnapshot.empty()) is ObservedPtt.UNKNOWN


@pytest.mark.parametrize("value", (False, True, 0, 1, "off", "on", object()))
def test_snapshot_projection_treats_noncanonical_value_types_as_unknown(
    value: object,
) -> None:
    # Mutation proof: removing exact value-type validation makes bool/string rows fail.
    assert project_observed_ptt(_snapshot(value)) is ObservedPtt.UNKNOWN


def test_snapshot_projection_treats_stale_evidence_as_unknown() -> None:
    # Mutation proof: removing the FreshnessState check makes this fail as OFF.
    snapshot = _snapshot(freshness=FreshnessState.STALE)
    assert project_observed_ptt(snapshot) is ObservedPtt.UNKNOWN


def test_snapshot_projection_treats_wrong_generation_evidence_as_unknown() -> None:
    # Mutation proof: removing generation equality makes this fail as OFF.
    snapshot = _snapshot(field_generation=6, snapshot_generation=7)
    assert project_observed_ptt(snapshot) is ObservedPtt.UNKNOWN


@pytest.mark.parametrize(
    "snapshot",
    (
        _snapshot(generated_at=12.0),
        _snapshot(generated_at=9.0),
        _snapshot(max_age=None),
        _snapshot(max_age=0.0),
        _snapshot(max_age=float("inf")),
        _snapshot(observed_at=float("nan")),
    ),
)
def test_snapshot_projection_treats_expired_or_invalid_timing_as_unknown(
    snapshot: StateSnapshot,
) -> None:
    assert project_observed_ptt(snapshot) is ObservedPtt.UNKNOWN


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (ObservedPtt.ON, True),
        (ObservedPtt.OFF, False),
        (ObservedPtt.UNKNOWN, False),
    ],
)
def test_legacy_ptt_bool_is_an_explicit_lossy_projection(
    state: ObservedPtt,
    expected: bool,
) -> None:
    assert legacy_ptt_bool(state) is expected
