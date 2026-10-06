"""Public alias projection must preserve a single observation's evidence."""

from dataclasses import replace

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot


def sample(path: FieldPath, value: int | str, at: float) -> Observation:
    return Observation(
        path=path,
        value=value,
        timestamp_monotonic=at,
        source=SourceMetadata(source="local_reconcile", provider="test"),
        quality=("reconciled",),
    )


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("older_receiver", ["0", "main"])
@pytest.mark.parametrize("older_active", [False, True])
def test_newest_public_frequency_alias_owns_value_and_evidence(
    reverse: bool, older_receiver: str, older_active: bool
) -> None:
    store = StateStore()
    latest = sample(FieldPath.active("0", "freq_mode", "freq_hz"), 7_134_000, 20.0)
    older_path = (
        FieldPath.active(older_receiver, "freq_mode", "freq_hz")
        if older_active
        else FieldPath.receiver(older_receiver, "freq_mode", "freq_hz")
    )
    observations = [
        latest,
        sample(older_path, 7_047_500, 10.0),
        sample(FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz"), 7_134_000, 20.0),
        sample(FieldPath.active_slot("0"), "A", 20.0),
        sample(FieldPath.vfo_slot("0", "B", "freq_mode", "freq_hz"), 14_288_000, 20.0),
    ]
    for observation in reversed(observations) if reverse else observations:
        store.apply(observation)
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=1
    )
    status = payload["fieldStatus"]["main.freqHz"]
    assert payload["main"]["activeSlot"] == "A"
    assert payload["main"]["vfoA"]["freqHz"] == 7_134_000
    assert payload["main"]["vfoB"]["freqHz"] == 14_288_000
    assert (
        payload["main"]["freqHz"],
        status["storePath"],
        status["lastObservedMonotonic"],
    ) == (7_134_000, str(latest.path), 20.0)
    assert status["source"]["source"] == "local_reconcile"
    assert status["quality"] == ["reconciled"]


@pytest.mark.parametrize("reverse", [False, True])
def test_equal_time_alias_selection_keeps_value_and_metadata_together(
    reverse: bool,
) -> None:
    observations = [
        sample(FieldPath.active("0", "freq_mode", "freq_hz"), 7_134_000, 20.0),
        sample(FieldPath.receiver("main", "freq_mode", "freq_hz"), 7_047_500, 20.0),
    ]
    store = StateStore()
    for observation in reversed(observations) if reverse else observations:
        store.apply(observation)
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=1
    )
    status = payload["fieldStatus"]["main.freqHz"]
    winners = {str(observation.path): observation for observation in observations}
    winner = winners[status["storePath"]]
    assert payload["main"]["freqHz"] == winner.value
    assert status["lastObservedMonotonic"] == winner.timestamp_monotonic


def test_old_generation_alias_cannot_override_current_frequency() -> None:
    store = StateStore()
    old = sample(FieldPath.receiver("main", "freq_mode", "freq_hz"), 7_047_500, 30.0)
    store.apply(old)
    generation = store.begin_provider_generation()
    latest = replace(
        sample(FieldPath.active("0", "freq_mode", "freq_hz"), 7_134_000, 20.0),
        provider_generation=generation,
    )
    store.apply(latest)
    store.apply(old)
    snapshot = store.snapshot()
    assert len(snapshot.fields) == 1
    assert snapshot.fields[0].provider_generation == generation
    payload = build_public_state_payload_from_snapshot(
        snapshot, radio=None, receiver_count=1
    )
    assert payload["main"]["freqHz"] == 7_134_000
    assert payload["fieldStatus"]["main.freqHz"]["storePath"] == str(latest.path)
