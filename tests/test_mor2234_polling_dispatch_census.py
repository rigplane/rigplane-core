"""MOR-2234 acceptance item 2: every declared polling path is dispatched.

For every shipped profile in ``rigs/`` that declares ``[state_acquisition]``,
every path the profile declares pollable (the ``polling_only`` TOML key,
which maps to the ``polling`` flag read by ``FieldCapability.can_poll``)
must be dispatched by the real acquisition machinery within a bounded
number of cadences, on a fake transport that answers. The MOR-1983 census
(``tests/test_state_queries.py``) covers resolvability, not dispatch.

Harness reused from ``tests/test_acquisition_scheduler.py``: the real
``AcquisitionScheduler`` paired with the real ``StateFreshnessService``
over a ``FreshnessClock``/``StateStore``, drained through the real
``IcomCivAcquisitionExecutor`` (via
``tests/_acquisition_query_helpers.py::recording_executor``) against a
fake transport that answers, tick by tick. This covers the CI-V
acquisition profiles (Icom and Xiegu); the Yaesu CAT profile (FTX-1)
drains no scheduler in production (``AcquisitionScheduler`` is never
drained on that backend), so it is out of scope here.

The only exemptions are paths whose declared policy withholds them in the
simulated state, computed from the profile's own policy, never from a
hand-written list:

- ``tx_only`` fields in a receive-only session (declared on
  ``AcquisitionPolicy``, read via ``profile.policy_for``);
- ``available_when`` conditions contradicted by the simulated state
  (resolved via ``resolve_available_when`` against the live snapshot).

"Dispatched" means a path reached the executor's ``execute`` with a
resolved query on the fake transport — one step past merely being queued
in ``scheduler.dispatchable_requests()``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    StateFreshnessService,
    resolve_available_when,
)
from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_target import KnownTxTarget
from rigplane.profiles import get_radio_profile
from rigplane.profiles.rig_loader import load_rig
from _acquisition_query_helpers import recording_executor

RIGS_DIR = Path(__file__).parents[1] / "rigs"

#: Simulated time between drain passes. 0.05 s keeps the adaptive-decay
#: cadence clocks honest (a step far below every declared cadence) while
#: staying cheap: the slowest declared class polls every 60 s, so two of
#: its cadences need ~2400 passes of pure scheduler math.
TICK_STEP_SECONDS = 0.05

#: Multiplier over the slowest declared pollable cadence: the slowest class
#: must run at least twice for its paths to be collected.
SLOWEST_CADENCE_RUNS = 2


def _answer(request_paths: tuple[FieldPath, ...], *, store: StateStore) -> ChangeSet:
    """Apply one fake-transport answer per path; return the credit changeset."""
    snapshot = store.snapshot()
    revision = snapshot.state_revision
    freshness_revision = snapshot.freshness_revision
    observation_seq = snapshot.observation_seq
    for path in request_paths:
        if path == FieldPath.global_("tx_state", "tx_target"):
            value: object = KnownTxTarget(
                receiver="MAIN", slot=None, frequency_hz=7_100_000
            )
        elif path.name == "freq_hz":
            value = 14_074_000
        else:
            value = 1
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(
                    source="poll_response",
                    provider="mor2234_census",
                    transport="fake",
                ),
                timestamp_monotonic=store.snapshot().generated_at_monotonic,
                max_age=60.0,
            )
        )
    return ChangeSet(
        revision=revision + 1,
        freshness_revision=freshness_revision + 1,
        observation_seq=observation_seq + 1,
        changes=(),
        timestamp_monotonic=store.snapshot().generated_at_monotonic,
        sources=(
            SourceMetadata(
                source="poll_response",
                provider="mor2234_census",
                transport="fake",
            ),
        ),
    )


def _simulate(
    *,
    model: str,
    profile_path: Path,
    slowest_cadence: float,
) -> tuple[set[FieldPath], dict[FieldPath, bool | None], set[FieldPath]]:
    """Run one receive-only session; return (sent, availability, tx_only)."""
    acquisition = load_rig(profile_path).to_profile().state_acquisition
    assert acquisition is not None
    pollable = set(acquisition.pollable_paths())
    tx_only_pollable = {
        path for path in pollable if acquisition.policy_for(path).tx_only
    }

    clock = FreshnessClock(start=1000.0)
    store = StateStore(freshness_clock=clock)
    scheduler = AcquisitionScheduler(profile=acquisition, clock=clock)
    service = StateFreshnessService(
        store=store, scheduler=scheduler, interval_seconds=TICK_STEP_SECONDS
    )
    executor, _sent = recording_executor(get_radio_profile(model))

    sent: set[FieldPath] = set()
    passes = int(slowest_cadence * SLOWEST_CADENCE_RUNS / TICK_STEP_SECONDS) + 2
    for _ in range(passes):
        service.tick(now=clock.now())
        for request in scheduler.dispatchable_requests():
            execution = asyncio.run(
                executor.execute(request, already_sent_paths=frozenset())
            )
            assert execution.failed_paths == (), (
                f"{model}: executor failed for {execution.failed_paths} "
                f"({execution.failure_reason})"
            )
            sent.update(execution.sent_paths)
            scheduler.record_acquisition_result(
                request, _answer(request.paths, store=store)
            )
        clock.advance(TICK_STEP_SECONDS)

    availability = resolve_available_when(acquisition, store.snapshot())
    return sent, availability, tx_only_pollable


def _shipped_profiles() -> list[tuple[str, str, Path]]:
    return sorted(
        (path.read_text().split('model = "')[1].split('"')[0], path.stem, path)
        for path in RIGS_DIR.glob("*.toml")
        if path.is_file()
        and not path.name.startswith("_")
        and "[state_acquisition]" in path.read_text()
        and 'provider = "yaesu_cat"' not in path.read_text()
    )


def _slowest_pollable_cadence(profile_path: Path) -> float:
    acquisition = load_rig(profile_path).to_profile().state_acquisition
    assert acquisition is not None
    cadences = [
        policy.cadence_seconds
        for path in acquisition.pollable_paths()
        if (policy := acquisition.policy_for(path)).cadence_seconds is not None
    ]
    assert cadences, f"{profile_path.name}: no pollable path with a cadence"
    return max(cadences)


@pytest.mark.parametrize(
    ("model", "stem", "profile_path"),
    _shipped_profiles(),
    ids=[stem for _, stem, _ in _shipped_profiles()],
)
def test_every_declared_polling_path_is_dispatched(
    model: str, stem: str, profile_path: Path
) -> None:
    acquisition = load_rig(profile_path).to_profile().state_acquisition
    assert acquisition is not None
    pollable = set(acquisition.pollable_paths())
    assert pollable, f"{model}: profile declares no pollable paths"
    slowest = _slowest_pollable_cadence(profile_path)

    dispatched, availability, tx_only_pollable = _simulate(
        model=model, profile_path=profile_path, slowest_cadence=slowest
    )

    available_when_exempt = {
        path
        for path in pollable
        if availability.get(path) is False and path not in dispatched
    }
    tx_only_exempt = {
        path
        for path in tx_only_pollable
        if path not in dispatched and path not in available_when_exempt
    }
    exempt = available_when_exempt | tx_only_exempt
    missing = pollable - dispatched - exempt

    exempt_lines = sorted(
        f"{path} ({'available_when contradicted in RX session' if path in available_when_exempt else 'tx_only, receive-only session'})"  # noqa: E501
        for path in exempt
    )
    assert not missing, (
        f"{model}: {len(missing)}/{len(pollable)} declared polling paths never "
        f"dispatched within {SLOWEST_CADENCE_RUNS}x the slowest cadence "
        f"({slowest}s): {sorted(str(p) for p in missing)}; "
        f"dispatched={len(dispatched)}, exempt={len(exempt)} "
        f"({'; '.join(exempt_lines) if exempt_lines else 'none'})"
    )
