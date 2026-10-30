"""MOR-3632/MOR-3633: the one positive-TX admission of the TX target.

``ManagedTxAuthority`` consults ONE probe, answered by
``ManagedTxComposition.tx_frequency_refusal``, under its lock after the
ordered queue barrier and immediately before any positive transition (a
momentary PTT or the latched ``transmit_on``); releases never consult it. The
probe reads the published ``tx_state.tx_target`` once, through ONE evaluator
(``runtime.tx_interlock.resolve_tx_target``: one freshness rule, the elapsed
age recomputed, our own last target-moving write as a fence), and refuses:

1. on a profile that publishes a target
   (``runtime._state_queries.profile_publishes_tx_target``), an unknown,
   stale, contradictory or frequency-less target ->
   ``ptt_tx_frequency_unknown`` (details ``txTargetReason``/``receiver``);
2. a known target inside a receive-only band row only ->
   ``receive_only_band``; outside every band row -> ``outside_tx_range``
   (the pure band permit ``core.tx_target.evaluate_tx_frequency_permit``).

One rule replaces the per-input write marks: after any acknowledged
target-changing write the target is UNKNOWN until a post-write observation
re-derives it (``StateStore.fence_tx_target``). These are the unit pins; the
public-interface pins (every managed PTT entry over real listeners and the
production poll loop) live in
``tests/integration/test_ptt_entry_tx_target_admission.py``. Simulated only.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from support.tx_target import seed_tx_target
from test_radio_poller_coverage import (
    _apply_tx_target_input,
    _make_radio,
    _seed_tx_target_ready,
)

from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
)
from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import RelativeVfoState
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.core.tx_target import (
    KnownTxTarget,
    TxSlot,
    UnknownTxTarget,
    evaluate_tx_frequency_permit,
)
from rigplane.core.tx_target_inputs import (
    intent_moves_tx_target,
    writes_tx_target_input,
)
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.runtime._poller_types import (
    MemoryToVfo,
    PttOn,
    QuickSplitTrigger,
    SelectVfo,
    SetBand,
    SetDualWatch,
    SetFreq,
    SetMemoryMode,
    SetMode,
    SetRitFrequency,
    SetSplit,
    VfoEqualize,
    VfoSwap,
)
from rigplane.runtime._state_queries import profile_publishes_tx_target
from rigplane.runtime.controller_authority import ControllerError
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
)
from rigplane.runtime.tx_interlock import (
    PTT_TX_FREQUENCY_UNKNOWN,
    moves_tx_target,
    ptt_on_tx_target_refusal,
    resolve_tx_target,
    tx_frequency_unknown_refusal,
)
from rigplane.web.radio_poller import CommandQueue, RadioPoller

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")
_ACTIVE_FREQ = FieldPath.active("0", "freq_mode", "freq_hz")
_UNSELECTED_FREQ = FieldPath.unselected("0", "freq_mode", "freq_hz")
_SPLIT = FieldPath.global_("tx_state", "split")

# Band facts used below come from the shipped profiles, not from this file:
# IC-705 TX: 20m/2m(144-148)/70cm(430-450); rx_only: 2200m/630m/WFM/Air/Gen.
# FTX-1 TX: 40m/2m/70cm; its WFM/Air/Gen ranges carry no band rows at all.
# IC-7610 TX: HF+6m only (no 2m).
_IC705 = "IC-705"
_IC7300 = "IC-7300"
_FTX1 = "FTX-1"
_IC7610 = "IC-7610"
_IC9700 = "IC-9700"
_WFM = 100_000_000
_TWO_METRES = 145_000_000
_TWENTY_METRES = 14_074_000


def _profile(model: str) -> RadioProfile:
    profile = resolve_radio_profile(model=model)
    assert isinstance(profile, RadioProfile)
    return profile


def _bands(profile: RadioProfile) -> tuple[Any, ...]:
    return tuple(band for rng in profile.freq_ranges for band in rng.bands)


def _target(frequency_hz: int | None, *, slot: TxSlot | None = "A") -> KnownTxTarget:
    return KnownTxTarget(receiver="MAIN", slot=slot, frequency_hz=frequency_hz)


def _fenced_at(store: StateStore) -> float:
    fence = store.tx_target_fence
    assert fence is not None
    return fence.fenced_at


# ---------------------------------------------------------------------------
# Pure permit function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
def test_unknown_target_does_not_decide(model: str) -> None:
    # The pure band permit decides only a known frequency; the probe refuses
    # an unknown target before it ever reaches the band rows.
    bands = _bands(_profile(model))
    for target in (
        None,
        UnknownTxTarget(reason="not-observed"),
        UnknownTxTarget(reason="stale"),
        _target(None),  # split on, TX VFO frequency unknown
    ):
        decision = evaluate_tx_frequency_permit(bands, target)
        assert decision.permitted
        assert decision.reason is None


@pytest.mark.parametrize(
    ("model", "frequency_hz"),
    (
        (_IC705, _TWENTY_METRES),
        (_IC705, _TWO_METRES),
        (_IC705, 435_000_000),  # 70cm
        (_FTX1, 7_074_000),  # 40m
        (_FTX1, _TWO_METRES),
        (_FTX1, 432_100_000),  # 70cm
        (_IC7610, _TWENTY_METRES),
        (_IC7610, 50_313_000),  # 6m
    ),
)
def test_inside_a_tx_band_is_permitted(model: str, frequency_hz: int) -> None:
    decision = evaluate_tx_frequency_permit(
        _bands(_profile(model)), _target(frequency_hz)
    )
    assert decision.permitted
    assert decision.reason is None


@pytest.mark.parametrize(
    ("frequency_hz", "band_name"),
    (
        (136_000, "2200m"),
        (475_000, "630m"),
        (_WFM, "WFM"),
        (121_500_000, "Air"),
        # General coverage outside every amateur row: 6m ends at 54 MHz.
        (60_000_000, "Gen"),
    ),
)
def test_ic705_receive_only_bands_are_refused(
    frequency_hz: int, band_name: str
) -> None:
    decision = evaluate_tx_frequency_permit(
        _bands(_profile(_IC705)), _target(frequency_hz)
    )
    assert not decision.permitted
    assert decision.reason == "receive-only-band"
    assert decision.band_name == band_name
    assert decision.frequency_hz == frequency_hz


def test_overlapping_tx_band_wins_over_general_coverage() -> None:
    # IC-705's rx_only "Gen" row (30 kHz-200 MHz) overlaps every HF/2m
    # amateur row; a frequency inside a TX row stays permitted.
    decision = evaluate_tx_frequency_permit(
        _bands(_profile(_IC705)), _target(_TWENTY_METRES)
    )
    assert decision.permitted


@pytest.mark.parametrize("frequency_hz", (_WFM, 121_500_000, 60_000_000))
def test_ftx1_wfm_air_gen_carry_no_bands_so_outside_tx_range(
    frequency_hz: int,
) -> None:
    decision = evaluate_tx_frequency_permit(
        _bands(_profile(_FTX1)), _target(frequency_hz)
    )
    assert not decision.permitted
    assert decision.reason == "outside-tx-range"


def test_ic7610_vhf_is_outside_tx_range() -> None:
    decision = evaluate_tx_frequency_permit(
        _bands(_profile(_IC7610)), _target(_TWO_METRES)
    )
    assert not decision.permitted
    assert decision.reason == "outside-tx-range"


def test_profile_without_band_rows_is_not_applicable() -> None:
    assert evaluate_tx_frequency_permit((), _target(_TWENTY_METRES)).permitted
    assert evaluate_tx_frequency_permit((), None).permitted


# ---------------------------------------------------------------------------
# The one evaluator: resolve_tx_target and the unknown-target refusal
# ---------------------------------------------------------------------------


def test_resolve_reads_the_fresh_field_and_names_a_missing_one() -> None:
    store = StateStore()
    store.begin_provider_generation()
    assert resolve_tx_target(store.snapshot()) == UnknownTxTarget("not-observed")
    seed_tx_target(store, _TWO_METRES)
    assert resolve_tx_target(store.snapshot()) == _target(_TWO_METRES)


def test_resolve_checks_elapsed_age_not_only_the_fresh_flag() -> None:
    # The freshness service clears the FRESH flag only on its next tick. Past
    # its own max_age the target is unknown before that tick, exactly as
    # ``resolve_rf_state`` reads the PTT field.
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    seed_tx_target(store, _WFM, timestamp_monotonic=10.0, max_age=1.0)
    assert resolve_tx_target(store.snapshot()) == _target(_WFM)
    clock.advance(2.0)  # no mark_stale_due(): the flag still says FRESH
    assert resolve_tx_target(store.snapshot()) == UnknownTxTarget("stale")
    store.mark_stale_due()
    assert resolve_tx_target(store.snapshot()) == UnknownTxTarget("stale")


def test_resolve_refuses_a_target_without_a_max_age_as_contradiction() -> None:
    store = StateStore()
    store.begin_provider_generation()
    store.apply_current(
        Observation(
            path=_TX_TARGET_PATH,
            value=_target(_TWENTY_METRES),
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
        )
    )
    assert resolve_tx_target(store.snapshot()) == UnknownTxTarget("contradiction")


def test_resolve_treats_a_target_older_than_the_write_fence_as_stale() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    seed_tx_target(store, _TWENTY_METRES, timestamp_monotonic=10.0, max_age=60.0)
    clock.advance(1.0)
    store.fence_tx_target()
    snapshot = store.snapshot()
    assert resolve_tx_target(snapshot) == _target(_TWENTY_METRES)
    assert resolve_tx_target(snapshot, fence=store.tx_target_fence) == (
        UnknownTxTarget("stale")
    )
    seed_tx_target(store, _TWO_METRES, timestamp_monotonic=11.0, max_age=60.0)
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        _target(_TWO_METRES)
    )


@pytest.mark.parametrize(
    ("value", "details"),
    (
        (None, {"txTargetReason": "not-observed"}),
        (UnknownTxTarget("stale"), {"txTargetReason": "stale"}),
        (UnknownTxTarget("contradiction"), {"txTargetReason": "contradiction"}),
        # Split on, TX VFO unknown: the receiver that would transmit is named.
        (_target(None), {"txTargetReason": "not-observed", "receiver": "MAIN"}),
        ("bogus", {"txTargetReason": "contradiction"}),
    ),
)
def test_unknown_target_refusal_vocabulary(value: object, details: Any) -> None:
    refusal = tx_frequency_unknown_refusal(value)
    assert refusal is not None
    assert refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN
    assert refusal.details == details
    assert tx_frequency_unknown_refusal(_target(_TWENTY_METRES)) is None


def test_a_profile_that_cannot_publish_skips_the_unknown_refusal() -> None:
    snapshot = StateStore().snapshot()
    assert ptt_on_tx_target_refusal(snapshot, tx_target_supported=False) is None
    refusal = ptt_on_tx_target_refusal(snapshot, tx_target_supported=True)
    assert refusal is not None and refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN


@pytest.mark.parametrize(
    ("model", "publishes"),
    (
        ("IC-705", True),  # selected/unselected derivation
        ("IC-7300", True),
        ("IC-7300MK2", True),
        ("IC-7610", True),  # get_tx_target (1C 03)
        ("FTX-1", True),  # pollable tx_target capability (FT;)
        ("IC-9700", False),
        ("TX-500", False),
        ("X6100", False),
        ("X6200", False),
    ),
)
def test_profile_publishes_tx_target_is_decided_by_profile_data(
    model: str, publishes: bool
) -> None:
    assert profile_publishes_tx_target(_profile(model)) is publishes


def test_anything_but_a_profile_does_not_publish() -> None:
    assert profile_publishes_tx_target(None) is False
    assert profile_publishes_tx_target(object()) is False


# ---------------------------------------------------------------------------
# One rule for our own writes: the target is unknown until read back
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "moves"),
    (
        (FieldPath.receiver("0", "freq_mode", "freq_hz"), True),
        (FieldPath.active("main", "freq_mode", "freq_hz"), True),
        (FieldPath.unselected("sub", "freq_mode", "freq_hz"), True),
        (FieldPath.vfo_slot("0", "B", "freq_mode", "freq_hz"), True),
        (_SPLIT, True),
        (FieldPath.active_slot("0"), True),
        (FieldPath.global_("slow_state", "active"), True),
        (FieldPath.receiver("0", "freq_mode", "mode"), False),
        (FieldPath.global_("tx_state", "ptt"), False),
    ),
)
def test_which_written_fields_move_the_tx_target(
    written: FieldPath, moves: bool
) -> None:
    assert writes_tx_target_input((written,)) is moves


def test_the_fence_only_moves_forward_and_a_new_generation_clears_it() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    assert store.tx_target_fence is None
    store.fence_tx_target(written_by=9.5)
    assert _fenced_at(store) == 9.5
    store.fence_tx_target(written_by=9.0)  # an older readback never rewinds it
    assert _fenced_at(store) == 9.5
    store.fence_tx_target(written_by=99.0)  # never later than now
    assert _fenced_at(store) == 10.0
    store.begin_provider_generation()
    assert store.tx_target_fence is None


class _GatedExecutor:
    """An executor whose write waits (a queue, a predecessor) before the wire."""

    def __init__(self, observations: tuple[Observation, ...] = ()) -> None:
        self.waiting = asyncio.Event()
        self.release = asyncio.Event()
        self.observations = observations

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        self.waiting.set()
        await self.release.wait()
        return CommandExecutionResult(observations=self.observations)


def _set_freq_intent() -> CommandIntent:
    return CommandIntent(
        id="cmd-1",
        name="set_freq",
        params={"freq": _TWO_METRES, "receiver": 0},
        source="rigctld",
        target=FieldPath.receiver("0", "freq_mode", "freq_hz"),
    )


async def test_a_read_landing_while_the_write_waits_does_not_confirm_it() -> None:
    # P1: the fence is the acknowledgement, never the moment the command was
    # sent. A cadence read that lands while the write still waits for the
    # wire carries the pre-write radio.
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    executor = _GatedExecutor()
    service = CommandService(executor=executor, state_store=store, clock=clock.now)
    task = asyncio.create_task(service.execute(_set_freq_intent()))
    await executor.waiting.wait()
    clock.advance(0.5)
    seed_tx_target(store, _TWENTY_METRES, timestamp_monotonic=clock.now())
    clock.advance(0.5)
    executor.release.set()
    await task
    assert _fenced_at(store) == 11.0
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        UnknownTxTarget("stale")
    )


async def test_the_writes_own_readback_is_post_write_evidence() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    readback = Observation(
        path=_TX_TARGET_PATH,
        value=_target(_TWO_METRES),
        source=SourceMetadata(source="command_response", provider="tests"),
        timestamp_monotonic=10.4,
        max_age=60.0,
    )
    executor = _GatedExecutor((readback,))
    service = CommandService(executor=executor, state_store=store, clock=clock.now)
    task = asyncio.create_task(service.execute(_set_freq_intent()))
    await executor.waiting.wait()
    clock.advance(1.0)
    executor.release.set()
    await task
    assert _fenced_at(store) == 10.4
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        _target(_TWO_METRES)
    )


async def test_a_non_moving_write_does_not_fence() -> None:
    store = StateStore()
    store.begin_provider_generation()
    gated = _GatedExecutor()
    gated.release.set()
    await CommandService(executor=gated, state_store=store).execute(
        CommandIntent(
            id="cmd-2",
            name="set_mode",
            params={"mode": "USB", "receiver": 0},
            source="rigctld",
            target=FieldPath.receiver("0", "freq_mode", "mode"),
        )
    )
    assert store.tx_target_fence is None


# ---------------------------------------------------------------------------
# The poller: fencing its writes and deriving only from post-write inputs
# ---------------------------------------------------------------------------


def _derived_poller(clock: FreshnessClock) -> tuple[RadioPoller, StateStore, int]:
    store = StateStore(freshness_clock=clock)
    generation = store.begin_provider_generation()
    poller = RadioPoller(
        _make_radio(model=_IC7300), CommandQueue(), state_store=store, clock=clock.now
    )
    return poller, store, generation


def _published(store: StateStore) -> object:
    return store.snapshot().field(_TX_TARGET_PATH).value


def _read_back(
    store: StateStore,
    generation: int,
    now: float,
    *,
    active: int,
    unselected: int = 7_150_000,
    split: bool = False,
) -> None:
    """One post-write read of every frequency input (one coherence window)."""
    for path, value in (
        (_ACTIVE_FREQ, active),
        (FieldPath.active("0", "freq_mode", "mode"), "USB"),
        (_UNSELECTED_FREQ, unselected),
        (FieldPath.unselected("0", "freq_mode", "mode"), "USB"),
        (_SPLIT, split),
    ):
        _apply_tx_target_input(store, path, value, generation=generation, now=now)


async def test_retune_withdraws_the_derived_target_until_the_read_back() -> None:
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(store, generation=generation, active_freq=_WFM, now=10.0)
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == _target(_WFM)

    clock.advance(0.25)
    poller._request_post_write_readback(  # noqa: SLF001
        SetFreq(freq=_TWENTY_METRES, receiver=0)
    )
    assert _fenced_at(store) == 10.25
    # A poll tick before the read lands must not republish the old frequency.
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")

    clock.advance(0.25)
    _read_back(store, generation, 10.5, active=_TWENTY_METRES)
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == _target(_TWENTY_METRES)


async def test_an_absolute_slot_alias_never_stands_in_for_the_relative_read() -> None:
    # P1: slots A and B both used to map onto ``active``, so reading the
    # opposite absolute slot cleared the changed slot's marker. Only a
    # post-write read of the relative input the derivation uses counts now.
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(store, generation=generation, active_freq=_WFM, now=10.0)
    clock.advance(0.25)
    poller._request_post_write_readback(  # noqa: SLF001
        SetFreq(freq=_TWENTY_METRES, receiver=0)
    )
    clock.advance(0.25)
    for slot in ("A", "B"):
        _apply_tx_target_input(
            store,
            FieldPath.vfo_slot("0", slot, "freq_mode", "freq_hz"),
            _TWENTY_METRES,
            generation=generation,
            now=10.5,
        )
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")


@pytest.mark.parametrize(
    "command",
    (
        VfoSwap(),
        VfoEqualize(),
        SetBand(band=5),
        MemoryToVfo(channel=1),
        SetMemoryMode(channel=1),
        SelectVfo("B"),
    ),
)
async def test_retuning_commands_without_field_metadata_fence_the_target(
    command: Any,
) -> None:
    # P1/P2: every command whose interlock family moves the TX frequency
    # fences the target (a memory recall included); the VFO identity is not
    # fenced, so the frequency reads alone restore a known target at once.
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(store, generation=generation, now=10.0)
    poller._publish_tx_target()  # noqa: SLF001
    clock.advance(0.25)
    poller._request_post_write_readback(command)  # noqa: SLF001
    assert _fenced_at(store) == 10.25
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")
    clock.advance(0.25)
    _read_back(store, generation, 10.5, active=_TWO_METRES)
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == _target(_TWO_METRES)


async def test_a_confirmed_vfo_selection_and_a_mode_write_do_not_fence() -> None:
    clock = FreshnessClock(start=10.0)
    poller, store, _ = _derived_poller(clock)
    poller._request_post_write_readback(  # noqa: SLF001
        SelectVfo("B"), selected_receiver=0
    )
    poller._request_post_write_readback(SetMode(mode="USB"))  # noqa: SLF001
    assert store.tx_target_fence is None


async def test_a_native_target_radio_fences_its_target_on_a_vfo_exchange() -> None:
    # P1: the IC-7610 MAIN/SUB exchange used to skip the fence, keeping the
    # pre-swap native 1C 03 verdict.
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    poller = RadioPoller(
        _make_radio(model=_IC7610), CommandQueue(), state_store=store, clock=clock.now
    )
    seed_tx_target(store, _TWENTY_METRES, timestamp_monotonic=10.0, max_age=60.0)
    clock.advance(0.25)
    poller._request_post_write_readback(VfoSwap())  # noqa: SLF001
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        UnknownTxTarget("stale")
    )


async def test_the_pre_key_settle_rederives_from_a_confirmed_readback() -> None:
    # P1: a confirmed read-back updated the inputs but not the published
    # target, so the next queued key read the previous one. The positive
    # entry's settle re-derives before the admission is released.
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(store, generation=generation, active_freq=_WFM, now=10.0)
    poller._publish_tx_target()  # noqa: SLF001
    clock.advance(0.25)
    poller._request_post_write_readback(  # noqa: SLF001
        SetFreq(freq=_TWENTY_METRES, receiver=0)
    )
    clock.advance(0.25)
    _read_back(store, generation, 10.5, active=_TWENTY_METRES)
    entry = SimpleNamespace(expires_at_monotonic=clock.now() + 5.0)
    await poller._settle_tx_target(entry)  # type: ignore[arg-type]  # noqa: SLF001
    assert _published(store) == _target(_TWENTY_METRES)


# ---------------------------------------------------------------------------
# Production composition: the authority's one admission seat
# ---------------------------------------------------------------------------


class _Actuator:
    """Managed-TX actuator double: remembers what reached the "wire"."""

    def __init__(self, profile: RadioProfile) -> None:
        self.profile = profile
        self.operations: list[ActuationOperation | AbortOperation] = []

    async def actuate(
        self,
        _token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Any,
    ) -> ActuationResult:
        if not is_current():
            return ActuationResult.REJECTED
        self.operations.append(operation)
        return ActuationResult.ACCEPTED

    @property
    def keyed(self) -> list[ActuationOperation | AbortOperation]:
        return [
            op
            for op in self.operations
            if op in (ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON)
        ]


async def _composition(
    tmp_path: Path, model: str, store: StateStore | None = None
) -> tuple[ManagedTxComposition, StateStore, _Actuator]:
    actuator = _Actuator(_profile(model))
    composition = ManagedTxComposition(actuator, config_path=tmp_path / "tx.json")
    store = store or StateStore()
    if store.provider_generation == 0:
        store.begin_provider_generation()
    await composition.transport_ready(object())
    await composition.bind_state_store(store)
    return composition, store, actuator


async def _unkey(composition: ManagedTxComposition) -> None:
    if (await composition.authority.snapshot()).state.intent.kind is not (
        ManagedTxIntentKind.RX
    ):
        submission = await composition.authority.submit_force_off()
        await submission.wait_settlement()
    await composition.shutdown(asyncio.Event())


async def test_probe_refuses_an_unknown_target_on_a_publishing_profile(
    tmp_path: Path,
) -> None:
    composition, store, _ = await _composition(tmp_path, _IC705)
    try:
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None
        assert refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN
        assert refusal.details == {"txTargetReason": "not-observed"}
        seed_tx_target(store, None)  # split on, TX VFO unknown
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None
        assert refusal.details == {"txTargetReason": "not-observed", "receiver": "MAIN"}
    finally:
        await _unkey(composition)


async def test_probe_is_not_applicable_on_a_profile_that_cannot_publish(
    tmp_path: Path,
) -> None:
    composition, store, _ = await _composition(tmp_path, _IC9700)
    try:
        assert profile_publishes_tx_target(_profile(_IC9700)) is False
        assert composition.tx_frequency_refusal() is None
        # Not even a published out-of-band target engages it.
        seed_tx_target(store, _TWENTY_METRES)
        assert composition.tx_frequency_refusal() is None
    finally:
        await _unkey(composition)


async def test_probe_reads_a_target_past_its_max_age_as_unknown(tmp_path: Path) -> None:
    clock = FreshnessClock(start=10.0)
    composition, store, _ = await _composition(
        tmp_path, _IC705, StateStore(freshness_clock=clock)
    )
    try:
        seed_tx_target(store, _WFM, timestamp_monotonic=10.0, max_age=1.0)
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None and refusal.reason_code == "receive_only_band"
        clock.advance(2.0)  # past max_age; the freshness tick has not run
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None
        assert refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN
        assert refusal.details == {"txTargetReason": "stale"}
    finally:
        await _unkey(composition)


async def test_a_write_makes_the_target_unknown_never_exempt(tmp_path: Path) -> None:
    # P1: a pending write used to disable the whole check -- it also exempted a
    # still-valid split TX VFO in WFM. Now the write fences the target and the
    # probe refuses it as unknown until a post-write read re-derives it.
    clock = FreshnessClock(start=10.0)
    composition, store, _ = await _composition(
        tmp_path, _IC705, StateStore(freshness_clock=clock)
    )
    try:
        seed_tx_target(store, _WFM, slot="B", timestamp_monotonic=10.0, max_age=60.0)
        clock.advance(0.5)
        store.fence_tx_target()  # the receive VFO A was retuned
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None
        assert refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN
        # The post-write read still finds TX on B in WFM: refused for the band.
        seed_tx_target(store, _WFM, slot="B", timestamp_monotonic=10.5, max_age=60.0)
        refusal = composition.tx_frequency_refusal()
        assert refusal is not None and refusal.reason_code == "receive_only_band"
    finally:
        await _unkey(composition)


async def test_probe_without_a_resolved_profile_is_not_applicable(
    tmp_path: Path,
) -> None:
    composition, store, actuator = await _composition(tmp_path, _IC705)
    try:
        seed_tx_target(store, _WFM)
        actuator.profile = replace(_profile(_IC705), freq_ranges=())
        assert composition.tx_frequency_refusal() is None
        actuator.profile = None  # type: ignore[assignment]
        assert composition.tx_frequency_refusal() is None
    finally:
        await _unkey(composition)


@pytest.mark.parametrize(
    ("model", "frequency_hz", "code"),
    (
        (_IC705, _WFM, "receive_only_band"),
        (_IC705, 121_500_000, "receive_only_band"),
        (_FTX1, _WFM, "outside_tx_range"),
        (_IC7610, _TWO_METRES, "outside_tx_range"),
        (_IC7610, None, PTT_TX_FREQUENCY_UNKNOWN),
    ),
)
@pytest.mark.parametrize("positive", ("ptt_down", "transmit_on"))
async def test_authority_refuses_every_positive_transition_the_same_way(
    tmp_path: Path, model: str, frequency_hz: int | None, code: str, positive: str
) -> None:
    composition, store, actuator = await _composition(tmp_path, model)
    authority = composition.authority
    if frequency_hz is not None:
        seed_tx_target(store, frequency_hz)
    try:
        with pytest.raises(ControllerError) as excinfo:
            if positive == "ptt_down":
                await authority.ptt_down("owner")
            else:
                await authority.transmit_on()
        assert excinfo.value.code == code
        assert excinfo.value.status == 409
        if code == PTT_TX_FREQUENCY_UNKNOWN:
            assert excinfo.value.details == {"txTargetReason": "not-observed"}
        assert actuator.keyed == []
        assert authority.tx_holder_nowait() is None
    finally:
        await _unkey(composition)


@pytest.mark.parametrize("positive", ("ptt_down", "transmit_on"))
async def test_authority_admits_inside_a_tx_band_and_release_ignores_the_permit(
    tmp_path: Path, positive: str
) -> None:
    composition, store, actuator = await _composition(tmp_path, _IC705)
    authority = composition.authority
    seed_tx_target(store, _TWO_METRES)
    try:
        if positive == "ptt_down":
            await authority.ptt_down("owner")
        else:
            await authority.transmit_on()
        assert actuator.keyed
        # The target retunes into WFM while keyed; releasing never consults it.
        seed_tx_target(store, _WFM)
        submission = await authority.submit_force_off()
        await submission.wait_settlement()
        assert (await authority.snapshot()).state.intent.kind is (
            ManagedTxIntentKind.RX
        )
    finally:
        await _unkey(composition)


async def test_a_retune_applied_while_the_key_waits_at_the_barrier_refuses_it(
    tmp_path: Path,
) -> None:
    # Time of check vs time of use: the key is queued behind a command that
    # retunes the radio. The verdict is taken after the barrier releases it,
    # so it sees the retuned target — not the one the request arrived under.
    composition, store, actuator = await _composition(tmp_path, _IC705)
    authority = composition.authority
    seed_tx_target(store, _TWO_METRES)
    ready: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    try:
        pending = authority.start_ptt_submission(True, "owner", ready=ready)
        await asyncio.sleep(0)
        seed_tx_target(store, _WFM)  # the queued retune executes first
        ready.set_result(None)
        with pytest.raises(ControllerError) as excinfo:
            await pending
        assert excinfo.value.code == "receive_only_band"
        assert actuator.keyed == []
        assert authority.tx_holder_nowait() is None
    finally:
        await _unkey(composition)


# ---------------------------------------------------------------------------
# One table of target-moving writes; fenced whatever the readback did; only a
# read SENT after the fence confirms it; no expired input feeds a derivation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("write", "moves"),
    (
        # Typed commands, by their interlock family.
        (SetFreq(freq=_TWENTY_METRES, receiver=1), True),
        (SetBand(band=5), True),
        (SelectVfo("B"), True),
        (VfoSwap(), True),
        (VfoEqualize(), True),
        (SetSplit(on=True), True),
        (SetDualWatch(on=True), True),
        (QuickSplitTrigger(), True),
        (MemoryToVfo(channel=1), True),
        (SetMemoryMode(channel=1), True),
        (SetMode(mode="USB"), False),
        (SetRitFrequency(freq=500), False),
        (PttOn(), False),
        # Descriptor-backed intents, by the family their descriptor declares:
        # the split TX VFO frequency has no profile-independent field.
        (
            CommandIntent(
                id="i", name="set_split_freq", params={"freq_hz": 1}, source="rigctld"
            ),
            True,
        ),
        (
            CommandIntent(
                id="m", name="set_split_mode", params={"mode": 1}, source="rigctld"
            ),
            False,
        ),
        # Every other intent, by the field it writes (rigctld F / V / S / U).
        (command_intent_from_request("set_freq", {"freq": 1}, source="rigctld"), True),
        (
            command_intent_from_request(
                "set_vfo", {"vfo": "VFOB", "receiver_count": 1}, source="rigctld"
            ),
            True,
        ),
        (
            command_intent_from_request(
                "set_split_vfo", {"on": True, "tx_vfo": "VFOB"}, source="rigctld"
            ),
            True,
        ),
        (
            command_intent_from_request(
                "set_func", {"func": "SPLIT", "on": True}, source="rigctld"
            ),
            True,
        ),
        (
            command_intent_from_request(
                "set_func", {"func": "NB", "on": True}, source="rigctld"
            ),
            False,
        ),
        (
            command_intent_from_request("set_mode", {"mode": "USB"}, source="rigctld"),
            False,
        ),
    ),
)
def test_one_table_decides_which_writes_move_the_tx_target(
    write: object, moves: bool
) -> None:
    assert moves_tx_target(write) is moves
    if isinstance(write, CommandIntent):
        assert intent_moves_tx_target(write) is moves


class _AckedThenFailed:
    """A write the radio acknowledged whose readback then failed."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        raise self.error


@pytest.mark.parametrize("model", (_IC705, _IC7300, _IC7610, _FTX1))
@pytest.mark.parametrize(
    "error",
    (
        CommandError("direct VFO frequency readback unavailable"),
        TimeoutError("readback timed out"),
        asyncio.CancelledError(),
    ),
    ids=("readback_rejected", "readback_timeout", "cancelled_behind_ack"),
)
async def test_a_write_whose_readback_fails_after_the_ack_still_fences(
    tmp_path: Path, model: str, error: BaseException
) -> None:
    # P1 (a): the radio retuned, the readback failed; the fence must not wait
    # for a success the executor never returns.
    composition, store, actuator = await _composition(tmp_path, model)
    authority = composition.authority
    seed_tx_target(store, _TWENTY_METRES)
    service = CommandService(executor=_AckedThenFailed(error), state_store=store)
    try:
        with pytest.raises(type(error)):
            await service.execute(_set_freq_intent())
        with pytest.raises(ControllerError) as excinfo:
            await authority.ptt_down("owner")
        assert excinfo.value.code == PTT_TX_FREQUENCY_UNKNOWN
        assert excinfo.value.details == {"txTargetReason": "stale"}
        assert actuator.keyed == []
        # A read sent after the write re-acquires the target; only then keys.
        seed_tx_target(store, _TWENTY_METRES + 1_000)
        await authority.ptt_down("owner")
        assert actuator.keyed
    finally:
        await _unkey(composition)


async def test_the_poller_fences_a_target_moving_write_that_raises() -> None:
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(
        store, generation=generation, active_freq=_TWENTY_METRES, now=10.0
    )
    poller._publish_tx_target()  # noqa: SLF001
    poller._radio.set_freq.side_effect = TimeoutError("no answer")  # noqa: SLF001
    clock.advance(0.25)
    with pytest.raises(TimeoutError):
        await poller._execute(SetFreq(freq=_WFM, receiver=0))  # noqa: SLF001
    assert _fenced_at(store) == 10.25
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")


async def test_a_confirmed_selection_fences_at_its_ack_even_if_the_readback_fails() -> (
    None
):
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(
        store, generation=generation, active_freq=_TWENTY_METRES, now=10.0
    )
    poller._publish_tx_target()  # noqa: SLF001
    radio = poller._radio  # noqa: SLF001
    radio._set_vfo_slot_confirmed = AsyncMock()  # noqa: SLF001
    radio.read_relative_vfo = AsyncMock(side_effect=CommandError("no readback"))
    clock.advance(0.25)
    with pytest.raises(CommandError):
        await poller._select_and_bind_vfo_slot(  # noqa: SLF001
            "B",
            receiver=0,
            active_name="MAIN",
            command_id=None,
            source="websocket",
            session_id=None,
            command_service=None,
            provider_generation=generation,
        )
    assert _fenced_at(store) == 10.25
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")


async def test_a_confirmed_selection_counts_its_own_post_ack_reads() -> None:
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(
        store, generation=generation, active_freq=_TWENTY_METRES, now=10.0
    )
    radio = poller._radio  # noqa: SLF001
    radio._set_vfo_slot_confirmed = AsyncMock()  # noqa: SLF001
    radio.read_relative_vfo = AsyncMock(
        side_effect=(
            RelativeVfoState(freq_hz=_TWO_METRES, mode="USB", filter_num=1),
            RelativeVfoState(freq_hz=_TWENTY_METRES, mode="USB", filter_num=1),
        )
    )
    clock.advance(0.25)
    await poller._select_and_bind_vfo_slot(  # noqa: SLF001
        "B",
        receiver=0,
        active_name="MAIN",
        command_id=None,
        source="websocket",
        session_id=None,
        command_service=None,
        provider_generation=generation,
    )
    # The frequencies were read after the selection; the split flag was not.
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")
    clock.advance(0.25)
    _apply_tx_target_input(store, _SPLIT, False, generation=generation, now=10.5)
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == _target(_TWO_METRES, slot="B")


def test_a_reply_decoded_after_the_fence_to_a_query_sent_before_it_is_stale() -> None:
    # P1 (b): a CI-V reply is stamped when decoded. The answer to a 1C 03 sent
    # before the write lands after the ACK and still names the old frequency.
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    generation = store.begin_provider_generation()
    seed_tx_target(store, _TWENTY_METRES, timestamp_monotonic=10.0, max_age=60.0)
    clock.advance(0.5)
    store.fence_tx_target()  # the retune to 2 m is acknowledged at 10.5

    def reply(frequency_hz: int, at: float) -> None:
        if at > clock.now():
            clock.advance(at - clock.now())
        store.apply(
            Observation(
                path=_TX_TARGET_PATH,
                value=_target(frequency_hz),
                source=SourceMetadata(source="poll_response", provider="tests"),
                timestamp_monotonic=at,
                max_age=60.0,
                provider_generation=generation,
            )
        )

    reply(_TWENTY_METRES, 10.6)  # pre-write query, decoded after the ACK
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        UnknownTxTarget("stale")
    )
    store.note_tx_target_reads((_TX_TARGET_PATH,), sent_at=10.7)
    reply(_TWENTY_METRES, 10.65)  # still landed before the post-write send
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        UnknownTxTarget("stale")
    )
    reply(_TWO_METRES, 10.8)  # answers the read sent after the write
    assert resolve_tx_target(store.snapshot(), fence=store.tx_target_fence) == (
        _target(_TWO_METRES)
    )


def test_a_read_sent_before_a_newer_fence_no_longer_counts() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.begin_provider_generation()
    store.fence_tx_target()
    store.note_tx_target_reads((_TX_TARGET_PATH,), sent_at=10.0)
    store.note_tx_target_reads((_TX_TARGET_PATH,), sent_at=9.0)  # before: ignored
    fence = store.tx_target_fence
    assert fence is not None and fence.reads == {_TX_TARGET_PATH: 10.0}
    clock.advance(1.0)
    store.fence_tx_target()
    fence = store.tx_target_fence
    assert fence is not None and fence.reads == {}
    # A read is stamped under one receiver spelling whichever the seat used.
    store.note_tx_target_reads(
        (FieldPath.active("main", "freq_mode", "freq_hz"),), sent_at=11.0
    )
    fence = store.tx_target_fence
    assert fence is not None and fence.admits(_ACTIVE_FREQ, 11.0)


async def test_the_poller_derivation_ignores_inputs_decoded_after_the_fence() -> None:
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(store, generation=generation, active_freq=_WFM, now=10.0)
    clock.advance(0.25)
    poller._request_post_write_readback(  # noqa: SLF001
        SetFreq(freq=_TWENTY_METRES, receiver=0)
    )
    clock.advance(0.25)
    # Every input lands again after the fence -- the old frequency included --
    # but answers queries sent before it: no post-write read went out.
    for path, value in (
        (_ACTIVE_FREQ, _WFM),
        (FieldPath.active("0", "freq_mode", "mode"), "USB"),
        (_UNSELECTED_FREQ, 7_150_000),
        (FieldPath.unselected("0", "freq_mode", "mode"), "USB"),
        (_SPLIT, False),
    ):
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="poll_response", provider="tests"),
                timestamp_monotonic=10.5,
                provider_generation=generation,
            )
        )
    assert store.snapshot().field(_ACTIVE_FREQ).last_observed_monotonic == 10.5
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")


async def test_the_acquisition_drain_stamps_its_sends_for_the_fence() -> None:
    from test_acquisition_drain import (
        _never_expired,
        _Reports,
        _request,
        _StubExecutor,
        _StubScheduler,
    )

    from rigplane.core.acquisition_drain import AcquisitionDrain
    from rigplane.core.acquisition_scheduler import AcquisitionExecutionResult

    store = StateStore()
    store.begin_provider_generation()
    store.fence_tx_target()
    request = _request(paths=(_TX_TARGET_PATH,))
    scheduler = _StubScheduler((request,))
    reports = _Reports()
    drain = AcquisitionDrain(
        scheduler=lambda: cast(Any, scheduler),
        executor=lambda: cast(
            Any,
            _StubExecutor(
                result=AcquisitionExecutionResult(sent_paths=(_TX_TARGET_PATH,))
            ),
        ),
        store=lambda: store,
        in_flight={},
        expired=_never_expired,
        dispatchable=lambda pending: pending,
        report_failure=reports.failure,
        report_executor_missing=reports.missing,
        report_executor_error=reports.error,
        report_sent=reports.sent_report,
    )
    before = time.monotonic()
    await drain.run_once()
    fence = store.tx_target_fence
    assert fence is not None
    sent_at = fence.reads[_TX_TARGET_PATH]
    assert sent_at == scheduler.dispatches[0][2]
    assert not fence.admits(_TX_TARGET_PATH, before)
    assert fence.admits(_TX_TARGET_PATH, sent_at)


async def test_the_yaesu_medium_read_stamps_its_send_for_the_fence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
    from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    store = StateStore()
    store.begin_provider_generation()
    store.fence_tx_target()
    observed_at: list[float] = []

    async def poll_medium(self: Any, **_kwargs: Any) -> tuple[Observation, ...]:
        observed_at.append(time.monotonic())
        return (
            Observation(
                path=_TX_TARGET_PATH,
                value=_target(_TWENTY_METRES),
                source=SourceMetadata(source="yaesu_poll_response", provider="t"),
                timestamp_monotonic=observed_at[-1],
                max_age=60.0,
            ),
        )

    monkeypatch.setattr(YaesuObservationAdapter, "poll_medium", poll_medium)
    poller = YaesuCatPoller(radio, observation_callback=lambda _: None)
    poller.bind_tx_target_reads(
        lambda paths, sent_at: store.note_tx_target_reads(paths, sent_at=sent_at)
    )
    await poller._emit_medium_observations()  # noqa: SLF001
    fence = store.tx_target_fence
    assert fence is not None
    assert fence.reads[_TX_TARGET_PATH] <= observed_at[0]
    assert fence.admits(_TX_TARGET_PATH, observed_at[0])


async def test_an_input_past_its_lifetime_never_feeds_a_derivation() -> None:
    # P2 (d): the FRESH flag lags expiry until the next freshness tick; the
    # pre-key re-derivation must not stamp an expired split as a target now.
    clock = FreshnessClock(start=10.0)
    poller, store, generation = _derived_poller(clock)
    _seed_tx_target_ready(
        store, generation=generation, active_freq=_TWENTY_METRES, now=10.0
    )
    _apply_tx_target_input(
        store, _SPLIT, False, generation=generation, now=10.0, max_age=0.5
    )
    poller._publish_tx_target()  # noqa: SLF001
    assert _published(store) == _target(_TWENTY_METRES)
    clock.advance(1.0)  # past the split's max_age; mark_stale_due never ran
    entry = SimpleNamespace(expires_at_monotonic=clock.now() + 5.0)
    await poller._settle_tx_target(entry)  # type: ignore[arg-type]  # noqa: SLF001
    assert _published(store) == UnknownTxTarget("stale")
    refusal = ptt_on_tx_target_refusal(
        store.snapshot(), tx_target_supported=True, fence=store.tx_target_fence
    )
    assert refusal is not None and refusal.reason_code == PTT_TX_FREQUENCY_UNKNOWN
