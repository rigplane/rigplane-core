"""MOR-2234 acceptance item 2: every declared polling path is dispatched.

For every shipped profile in ``rigs/`` that declares ``[state_acquisition]``,
every path the profile declares pollable (the ``polling_only`` TOML key,
which maps to the ``polling`` flag read by ``FieldCapability.can_poll``)
must be dispatched by the real acquisition machinery within a bounded
number of cadences, on a fake transport that answers. The MOR-1983 census
(``tests/test_state_queries.py``) covers resolvability, not dispatch.

Two harnesses, one per backend family:

- CI-V profiles (Icom and Xiegu): reused from
  ``tests/test_acquisition_scheduler.py`` — the real
  ``AcquisitionScheduler`` paired with the real ``StateFreshnessService``
  over a ``FreshnessClock``/``StateStore``, drained through the real
  ``IcomCivAcquisitionExecutor`` (via
  ``tests/_acquisition_query_helpers.py::recording_executor``) against a
  fake transport that answers, tick by tick. "Dispatched" means a path
  reached the executor's ``execute`` with a resolved query — one step past
  merely being queued in ``scheduler.dispatchable_requests()``.
- FTX-1 (Yaesu CAT): reused from
  ``tests/test_yaesu_cat_observation_adapter.py::_make_radio`` — the real
  ``YaesuObservationAdapter`` over a mock CAT radio whose reads all answer,
  driven through the real ``YaesuCatPoller`` poll groups (medium/fast/slow
  lanes, the same ``_emit_*_observations`` the production loops call).
  "Dispatched" means a path was actually read and published as an
  observation in a poll cycle. The FTX-1 declares no scheduler-drained
  cadence; its polling lives in these poll groups, gated per path by the
  declared policy (``_can_poll``) and the runtime capabilities.

The only exemptions are paths whose declared policy withholds them in the
simulated state, computed from the profile's own policy, never from a
hand-written list:

- ``tx_only`` fields in a receive-only session (declared on
  ``AcquisitionPolicy``, read via ``profile.policy_for``);
- ``available_when`` conditions contradicted by the simulated state
  (resolved via ``resolve_available_when`` against the live snapshot).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

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
from rigplane.core.tx_observation import TxStateReading
from rigplane.core.tx_target import KnownTxTarget
from rigplane.core.types import BreakInMode
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


def _shipped_civ_profiles() -> list[tuple[str, str, Path]]:
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
    _shipped_civ_profiles(),
    ids=[stem for _, stem, _ in _shipped_civ_profiles()],
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


def _make_ftx1_radio() -> MagicMock:
    """Mock CAT radio answering every FTX-1 read (adapter-test harness)."""
    radio = MagicMock()
    radio.profile = get_radio_profile("FTX-1")
    radio.capabilities = {
        "dual_rx",
        "af_level",
        "rf_gain",
        "squelch",
        "meters",
        "filter_width",
        "if_shift",
        "tx",
        "vox",
        "compressor",
        "attenuator",
        "preamp",
        "nb",
        "nr",
        "notch",
        "split",
        "rit",
        "xit",
        "tuner",
        "dial_lock",
        "cw",
        "sql_type",
        "repeater_shift",
    }
    radio.read_freq = AsyncMock(
        side_effect=lambda receiver=0: 14_074_000 if receiver == 0 else 7_074_000
    )
    radio.read_mode = AsyncMock(
        side_effect=lambda receiver=0: ("USB", None) if receiver == 0 else ("LSB", None)
    )
    radio.read_transmit_state = AsyncMock(
        return_value=TxStateReading(False, "rx", "yaesu_poll_response", True)
    )
    radio.get_tx_func = AsyncMock(return_value=0)
    radio.get_rx_func = AsyncMock(return_value=0)
    radio.read_af_level = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 64
    )
    radio.read_rf_gain = AsyncMock(
        side_effect=lambda receiver=0: 180 if receiver == 0 else 90
    )
    radio.read_squelch = AsyncMock(
        side_effect=lambda receiver=0: 12 if receiver == 0 else 8
    )
    radio.read_attenuator = AsyncMock(return_value=True)
    radio.read_preamp = AsyncMock(return_value=2)
    radio.read_agc = AsyncMock(return_value=3)
    radio.read_filter_width = AsyncMock(return_value=500)
    radio.read_if_shift = AsyncMock(
        side_effect=lambda receiver=0: 200 if receiver == 0 else 210
    )
    radio.read_narrow = AsyncMock(side_effect=lambda receiver=0: receiver == 0)
    radio.read_nb_level = AsyncMock(
        side_effect=lambda receiver=0: 5 if receiver == 0 else 3
    )
    radio.read_nr_level = AsyncMock(
        side_effect=lambda receiver=0: 9 if receiver == 0 else 5
    )
    radio.read_auto_notch = AsyncMock(side_effect=lambda receiver=0: receiver == 0)
    radio.read_manual_notch = AsyncMock(side_effect=lambda receiver=0: receiver == 0)
    radio.read_manual_notch_freq = AsyncMock(
        side_effect=lambda receiver=0: 128 if receiver == 0 else 120
    )
    radio.read_s_meter = AsyncMock(
        side_effect=lambda receiver=0: 150 if receiver == 0 else 75
    )
    radio.read_alc_meter = AsyncMock(return_value=42)
    radio.read_comp_meter = AsyncMock(return_value=30)
    radio.read_power_meter = AsyncMock(return_value=180)
    radio.read_swr_meter = AsyncMock(return_value=120)
    radio.get_vd_meter = AsyncMock(return_value=212)
    radio.get_id_meter = AsyncMock(return_value=0)
    radio.read_power = AsyncMock(return_value=(2, 55))
    radio.read_mic_gain = AsyncMock(return_value=40)
    radio.read_processor = AsyncMock(return_value=True)
    radio.read_processor_level = AsyncMock(return_value=25)
    radio.read_vox = AsyncMock(return_value=True)
    radio.read_split = AsyncMock(return_value=True)
    radio.read_vfo_select = AsyncMock(return_value=1)
    radio.read_clarifier = AsyncMock(return_value=(True, False))
    radio.read_clarifier_freq = AsyncMock(return_value=-250)
    radio.get_tuner_status = AsyncMock(return_value=2)
    radio.read_lock = AsyncMock(return_value=True)
    radio.read_keyer_speed = AsyncMock(return_value=24)
    radio.read_cw_pitch = AsyncMock(return_value=600)
    radio.read_break_in = AsyncMock(return_value=BreakInMode.SEMI)
    radio.read_break_in_delay = AsyncMock(return_value=300)
    radio.read_cw_spot = AsyncMock(return_value=True)
    radio.read_sql_type = AsyncMock(return_value=1)
    radio.read_ctcss_tone_index = AsyncMock(return_value=8)
    radio.read_repeater_shift = AsyncMock(return_value=0)
    return radio


async def _run_ftx1_poll_cycles(*, cycles: int) -> set[FieldPath]:
    """Drive every Yaesu poll lane; return the published paths."""
    from rigplane.backends.yaesu_cat.poller import YaesuCatPoller

    radio = _make_ftx1_radio()
    collected: list[Observation] = []
    poller = YaesuCatPoller(
        radio,
        observation_callback=collected.extend,
        fast_interval=10.0,
        medium_interval=10.0,
        slow_interval=10.0,
        ema_alpha=1.0,
    )
    read: set[FieldPath] = set()
    for _ in range(cycles):
        await poller._poll_medium()  # noqa: SLF001
        await poller._poll_fast()  # noqa: SLF001
        await poller._poll_slow()  # noqa: SLF001
        read.update(item.path for item in collected)
        collected.clear()
    read_reads = radio.read_mic_gain.await_count
    assert read_reads >= cycles, (
        f"mock CAT transport did not answer mic_gain every cycle: "
        f"{read_reads} reads in {cycles} cycles"
    )
    return read


def test_ftx1_every_declared_polling_path_is_polled() -> None:
    """MOR-2234 acceptance item 2, FTX-1 leg.

    The FTX-1's production polling never drains ``AcquisitionScheduler``;
    it goes through the ``YaesuCatPoller`` poll groups, gated per path by
    the declared policy (``_can_poll``) and the runtime capabilities. This
    drives every poll lane on a mock CAT transport that answers every read
    and asserts every declared pollable path was actually read/published.
    """
    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None
    pollable = set(acquisition.pollable_paths())
    assert pollable, "FTX-1: profile declares no pollable paths"
    tx_only_pollable = {
        path for path in pollable if acquisition.policy_for(path).tx_only
    }

    read = asyncio.run(_run_ftx1_poll_cycles(cycles=2))

    # Availability resolves against an empty snapshot here: the
    # receive-only session observes freq/mode/PTT fresh, while the
    # conditional fields' clause sources stay unobserved (None), which the
    # poller treats as withheld exactly like a contradicted clause.
    store = StateStore()
    availability = resolve_available_when(acquisition, store.snapshot())
    deficit = pollable - read
    available_when_exempt = {
        path for path in deficit if availability.get(path) is not True
    }
    tx_only_exempt = {
        path
        for path in tx_only_pollable
        if path not in read and path not in available_when_exempt
    }
    exempt = available_when_exempt | tx_only_exempt
    missing = pollable - read - exempt

    exempt_lines = sorted(
        f"{path} ({'available_when withheld in RX session' if path in available_when_exempt else 'tx_only, receive-only session'})"  # noqa: E501
        for path in exempt
    )
    assert not missing, (
        f"FTX-1: {len(missing)}/{len(pollable)} declared polling paths never "
        f"read in 2 full poll cycles: {sorted(str(p) for p in missing)}; "
        f"read={len(read)}, exempt={len(exempt)} "
        f"({'; '.join(exempt_lines) if exempt_lines else 'none'})"
    )
