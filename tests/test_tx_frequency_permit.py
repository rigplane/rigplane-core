"""MOR-3632 slice B: server-side TX frequency permit at PTT-on.

The permit is one pure core function
(:func:`rigplane.core.tx_target.evaluate_tx_frequency_permit`) over the
profile band rows and the server's published split-aware TX target. It is
consulted in ONE place: ``ManagedTxAuthority`` admission, under the
authority's lock, right before any positive transition (a momentary PTT or
the latched ``transmit_on``) is made — after the ordered queue barrier has
released it. The production composition (``ManagedTxComposition``) answers
the authority's ``tx_frequency_refusal`` from the store and the radio's
profile, so every managed ingress (WebSocket ``ptt_on``, rigctld ``T 1``,
HTTP ``transmit_on``) gets the same verdict from the same code.

The permit is BAND-ONLY: it decides solely from a CURRENT KNOWN TX target
that carries a frequency.  An unknown, stale, missing, malformed, or
known-without-frequency target — or one an acknowledged write of ours has
made wrong and nothing has read back yet — is "not applicable" here: the
unknown-target guard (MOR-3633) owns those cases, so a radio that never
publishes a target keeps keying.

The ingress tests drive the real seams: a real ``WebServer`` /
``CommandQueue`` / ``RadioPoller`` leg for WS, the real HTTP route for
``transmit_on``, and a live ``RigctldServer`` over a TCP socket for rigctld,
all with a real ``ManagedTxComposition``. Simulated only; no hardware.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from serial_stub import SerialMockRadio
from test_radio_poller_coverage import (
    _apply_tx_target_input,
    _make_radio,
    _seed_tx_target_ready,
)

from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.radio_protocol import Radio
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import (
    FieldSnapshot,
    FreshnessClock,
    FreshnessState,
    StateSnapshot,
    StateStore,
)
from rigplane.core.tx_target import (
    KnownTxTarget,
    TxSlot,
    UnknownTxTarget,
    evaluate_tx_frequency_permit,
)
from rigplane.core.tx_target_inputs import tx_target_inputs_written
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime._poller_types import (
    CommandQueue as PollerCommandQueue,
    SetBand,
    SetFreq,
    VfoEqualize,
    VfoSwap,
    execute_command_queue_entry,
    execute_positive_tx_queue_entry,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.controller_authority import ControllerError
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxIntentKind,
)
from rigplane.runtime.tx_interlock import current_tx_target
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")
_ACTIVE_FREQ = FieldPath.active("0", "freq_mode", "freq_hz")

# Band facts used below come from the shipped profiles, not from this file:
# IC-705 TX: 20m/2m(144-148)/70cm(430-450); rx_only: 2200m/630m/WFM/Air/Gen.
# FTX-1 TX: 40m/2m/70cm; its WFM/Air/Gen ranges carry no band rows at all.
# IC-7610 TX: HF+6m only (no 2m).
_IC705 = "IC-705"
_FTX1 = "FTX-1"
_IC7610 = "IC-7610"
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


def seed_tx_target(
    store: StateStore,
    frequency_hz: int | None = _TWENTY_METRES,
    *,
    slot: TxSlot | None = "A",
    timestamp_monotonic: float | None = None,
    max_age: float | None = None,
) -> None:
    """Publish a known TX target — the state a keyed radio sits on.

    ``frequency_hz=None`` seeds the split-on / TX-VFO-unknown form.  Bound to
    the store's current provider generation (``apply_current``): a plain
    ``apply`` stamps generation 0, which a rolled store silently drops.
    """

    store.apply_current(
        Observation(
            path=_TX_TARGET_PATH,
            value=_target(frequency_hz, slot=slot),
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=(
                time.monotonic() if timestamp_monotonic is None else timestamp_monotonic
            ),
            max_age=max_age,
        )
    )


# ---------------------------------------------------------------------------
# Pure permit function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
def test_unknown_target_does_not_decide(model: str) -> None:
    # Band-only permit: no known frequency means "not applicable", never a
    # refusal (the unknown-target guard owns that case).
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
# Store reader: current_tx_target
# ---------------------------------------------------------------------------


def test_current_tx_target_reads_the_fresh_field() -> None:
    store = StateStore()
    assert current_tx_target(store.snapshot()) is None
    seed_tx_target(store, _TWO_METRES)
    target = current_tx_target(store.snapshot())
    assert isinstance(target, KnownTxTarget)
    assert target.frequency_hz == _TWO_METRES


def _snapshot_with_tx_target_value(value: object) -> StateSnapshot:
    """A snapshot holding a RAW tx_target value, bypassing ingest validation.

    ``Observation`` normalizes this path's value to a ``TxTarget`` on ingest,
    so a raw mapping (or junk) can only meet ``current_tx_target`` through a
    rehydrated or golden snapshot — build that shape directly.
    """

    now = time.monotonic()
    return StateSnapshot(
        state_revision=0,
        freshness_revision=0,
        observation_seq=0,
        generated_at_monotonic=now,
        fields=(
            FieldSnapshot(
                path=_TX_TARGET_PATH,
                value=value,
                freshness=FreshnessState.FRESH,
                last_observed_monotonic=now,
                max_age=None,
                source=SourceMetadata(source="test", provider="tests"),
            ),
        ),
    )


def test_current_tx_target_parses_the_wire_mapping_form() -> None:
    target = current_tx_target(
        _snapshot_with_tx_target_value(_target(7_074_000).to_dict())
    )
    assert isinstance(target, KnownTxTarget)
    assert target.frequency_hz == 7_074_000


def test_current_tx_target_treats_stale_and_malformed_as_unknown() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    seed_tx_target(store, _TWO_METRES, timestamp_monotonic=10.0, max_age=1.0)
    clock.advance(2.0)
    store.mark_stale_due()
    assert current_tx_target(store.snapshot()) is None

    # Malformed mappings and non-target junk read as unknown rather than
    # raising.
    assert (
        current_tx_target(_snapshot_with_tx_target_value({"status": "known"})) is None
    )
    assert current_tx_target(_snapshot_with_tx_target_value("bogus")) is None


def test_current_tx_target_checks_elapsed_age_not_only_the_fresh_flag() -> None:
    # The freshness service clears the FRESH flag only on its next tick. Past
    # its own max_age the target is unknown even before that tick, exactly as
    # ``resolve_rf_state`` reads the PTT field.
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    seed_tx_target(store, _WFM, timestamp_monotonic=10.0, max_age=1.0)
    assert isinstance(current_tx_target(store.snapshot()), KnownTxTarget)
    clock.advance(2.0)  # no mark_stale_due(): the flag still says FRESH
    assert store.snapshot().field(_TX_TARGET_PATH).freshness is FreshnessState.FRESH
    assert current_tx_target(store.snapshot()) is None


# ---------------------------------------------------------------------------
# Own writes make the target wrong, not stale
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("written", "expected"),
    (
        (FieldPath.receiver("0", "freq_mode", "freq_hz"), (_ACTIVE_FREQ,)),
        (FieldPath.active("main", "freq_mode", "freq_hz"), (_ACTIVE_FREQ,)),
        (
            FieldPath.unselected("sub", "freq_mode", "freq_hz"),
            (FieldPath.unselected("1", "freq_mode", "freq_hz"),),
        ),
        (
            FieldPath.global_("tx_state", "split"),
            (FieldPath.global_("tx_state", "split"),),
        ),
        (FieldPath.active_slot("0"), (FieldPath.active_slot("0"),)),
        (FieldPath.receiver("0", "freq_mode", "mode"), ()),
        (FieldPath.global_("tx_state", "ptt"), ()),
    ),
)
def test_only_derivation_inputs_count_as_tx_target_inputs(
    written: FieldPath, expected: tuple[FieldPath, ...]
) -> None:
    assert tx_target_inputs_written((written,)) == expected


def _write_seen_by(store: StateStore) -> frozenset[FieldPath]:
    return store.pending_tx_target_inputs()


def test_a_marked_input_stays_pending_until_observed_again() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.mark_tx_target_inputs_written(
        (FieldPath.receiver("0", "freq_mode", "freq_hz"),)
    )
    assert _write_seen_by(store) == {_ACTIVE_FREQ}
    # The pre-write value, observed no later than the write, does not clear it.
    store.apply_current(
        Observation(
            path=_ACTIVE_FREQ,
            value=14_074_000,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=10.0,
        )
    )
    assert _write_seen_by(store) == {_ACTIVE_FREQ}
    # The first observation after the write does, whichever spelling the
    # provider publishes (Yaesu names the receiver "main").
    store.apply_current(
        Observation(
            path=FieldPath.active("main", "freq_mode", "freq_hz"),
            value=145_000_000,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=10.5,
        )
    )
    assert _write_seen_by(store) == frozenset()


def test_a_mark_expires_and_a_new_generation_clears_it() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.mark_tx_target_inputs_written((FieldPath.global_("tx_state", "split"),))
    assert _write_seen_by(store)
    clock.advance(60.0)  # nothing ever reads split back: do not withhold forever
    assert _write_seen_by(store) == frozenset()
    store.mark_tx_target_inputs_written((FieldPath.global_("tx_state", "split"),))
    store.begin_provider_generation()
    assert _write_seen_by(store) == frozenset()


class _Executor:
    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        return CommandExecutionResult()


async def test_an_acknowledged_retune_marks_the_input_it_wrote() -> None:
    store = StateStore()
    store.begin_provider_generation()
    service = CommandService(executor=_Executor(), state_store=store)
    intent = CommandIntent(
        id="cmd-1",
        name="set_freq",
        params={"freq": _TWO_METRES, "receiver": 0},
        source="rigctld",
        target=FieldPath.receiver("0", "freq_mode", "freq_hz"),
    )
    await service.execute(intent)
    assert _write_seen_by(store) == {_ACTIVE_FREQ}


async def test_a_failed_retune_marks_nothing() -> None:
    class _Failing:
        async def execute(self, intent: CommandIntent) -> Any:
            raise RuntimeError("radio did not acknowledge")

    store = StateStore()
    store.begin_provider_generation()
    service = CommandService(executor=_Failing(), state_store=store)
    intent = CommandIntent(
        id="cmd-1",
        name="set_freq",
        params={"freq": _TWO_METRES, "receiver": 0},
        source="rigctld",
        target=FieldPath.receiver("0", "freq_mode", "freq_hz"),
    )
    with pytest.raises(RuntimeError):
        await service.execute(intent)
    assert _write_seen_by(store) == frozenset()


async def test_retune_withdraws_the_derived_target_until_the_read_back() -> None:
    """The derived IC target never stands for the pre-write radio (IC-7300)."""

    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    generation = store.begin_provider_generation()
    _seed_tx_target_ready(store, generation=generation, active_freq=_WFM, now=10.0)
    poller = RadioPoller(
        _make_radio(model="IC-7300"),
        CommandQueue(),
        state_store=store,
        clock=clock.now,
    )
    poller._publish_tx_target()  # noqa: SLF001
    published = store.snapshot().field(_TX_TARGET_PATH)
    assert published.value == KnownTxTarget(
        receiver="MAIN", slot="A", frequency_hz=_WFM
    )

    # The write is acknowledged: the poller's post-write seat marks its input.
    poller._request_post_write_readback(  # noqa: SLF001
        SetFreq(freq=_TWENTY_METRES, receiver=0)
    )
    assert store.pending_tx_target_inputs() == {_ACTIVE_FREQ}
    # A poll tick before the read lands must not republish the old frequency.
    poller._publish_tx_target()  # noqa: SLF001
    assert store.snapshot().field(_TX_TARGET_PATH).value == UnknownTxTarget(
        reason="stale"
    )

    # The read lands (frequency and its sibling mode, one coherence window).
    clock.advance(0.5)
    _apply_tx_target_input(
        store, _ACTIVE_FREQ, _TWENTY_METRES, generation=generation, now=10.5
    )
    _apply_tx_target_input(
        store,
        FieldPath.active("0", "freq_mode", "mode"),
        "USB",
        generation=generation,
        now=10.5,
    )
    _apply_tx_target_input(
        store,
        FieldPath.unselected("0", "freq_mode", "freq_hz"),
        7_150_000,
        generation=generation,
        now=10.5,
    )
    _apply_tx_target_input(
        store,
        FieldPath.unselected("0", "freq_mode", "mode"),
        "USB",
        generation=generation,
        now=10.5,
    )
    poller._publish_tx_target()  # noqa: SLF001
    assert store.snapshot().field(_TX_TARGET_PATH).value == KnownTxTarget(
        receiver="MAIN", slot="A", frequency_hz=_TWENTY_METRES
    )


@pytest.mark.parametrize("command", (VfoSwap(), VfoEqualize(), SetBand(band=5)))
async def test_commands_that_retune_without_naming_a_field_mark_every_input(
    command: Any,
) -> None:
    store = StateStore()
    generation = store.begin_provider_generation()
    _seed_tx_target_ready(store, generation=generation)
    poller = RadioPoller(
        _make_radio(model="IC-7300"), CommandQueue(), state_store=store
    )
    poller._request_post_write_readback(command)  # noqa: SLF001
    assert store.pending_tx_target_inputs() == {
        _ACTIVE_FREQ,
        FieldPath.unselected("0", "freq_mode", "freq_hz"),
        FieldPath.active_slot("0"),
    }


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


async def test_probe_is_not_applicable_without_a_known_target(tmp_path: Path) -> None:
    composition, store, _ = await _composition(tmp_path, _IC705)
    try:
        assert composition.tx_frequency_refusal() is None
        seed_tx_target(store, None)  # split on, TX VFO unknown
        assert composition.tx_frequency_refusal() is None
    finally:
        await _unkey(composition)


async def test_probe_ignores_a_target_past_its_max_age(tmp_path: Path) -> None:
    clock = FreshnessClock(start=10.0)
    composition, store, _ = await _composition(
        tmp_path, _IC705, StateStore(freshness_clock=clock)
    )
    try:
        seed_tx_target(store, _WFM, timestamp_monotonic=10.0, max_age=1.0)
        assert composition.tx_frequency_refusal() == "receive_only_band"
        clock.advance(2.0)  # past max_age; the freshness tick has not run
        assert composition.tx_frequency_refusal() is None
    finally:
        await _unkey(composition)


async def test_probe_does_not_use_a_target_older_than_our_own_write(
    tmp_path: Path,
) -> None:
    composition, store, _ = await _composition(tmp_path, _IC705)
    try:
        seed_tx_target(store, _WFM)
        assert composition.tx_frequency_refusal() == "receive_only_band"
        store.mark_tx_target_inputs_written((_ACTIVE_FREQ,))
        assert composition.tx_frequency_refusal() is None
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
    ),
)
@pytest.mark.parametrize("positive", ("ptt_down", "transmit_on"))
async def test_authority_refuses_every_positive_transition_the_same_way(
    tmp_path: Path, model: str, frequency_hz: int, code: str, positive: str
) -> None:
    composition, store, actuator = await _composition(tmp_path, model)
    authority = composition.authority
    seed_tx_target(store, frequency_hz)
    try:
        with pytest.raises(ControllerError) as excinfo:
            if positive == "ptt_down":
                await authority.ptt_down("owner")
            else:
                await authority.transmit_on()
        assert excinfo.value.code == code
        assert excinfo.value.status == 409
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
# WS ingress (managed PTT) through the real server/queue/composition stack
# ---------------------------------------------------------------------------


def _radio(model: str) -> MagicMock:
    profile = _profile(model)
    radio = MagicMock(spec=Radio)
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.connected = True
    radio.radio_ready = True
    radio.set_ptt = AsyncMock()
    radio._civ_epoch = 1
    return radio


class _Stack:
    """Real WebServer + CommandQueue + RadioPoller + ManagedTxComposition."""

    def __init__(self, tmp_path: Path, model: str) -> None:
        self.tmp_path, self.model = tmp_path, model

    async def start(self) -> _Stack:
        self.radio = _radio(self.model)
        self.server = WebServer(self.radio, WebConfig(host="127.0.0.1", port=0))
        self.store = self.server.command_state_store
        self.composition, _, self.actuator = await _composition(
            self.tmp_path, self.model, self.store
        )
        self.authority = self.composition.authority
        self.queue: CommandQueue = self.server.command_queue
        self.poller = RadioPoller(
            self.radio,
            self.queue,
            state_store=self.store,
            managed_tx_authority=self.authority,
        )
        self.enqueued = asyncio.Event()
        put_ordered = self.queue.put_ordered

        def notify_put(*args: Any, **kwargs: Any) -> Any:
            entry = put_ordered(*args, **kwargs)
            self.enqueued.set()
            return entry

        self.queue.put_ordered = notify_put  # type: ignore[method-assign]
        return self

    def handler(self, name: str, *, ws: Any = None) -> ControlHandler:
        return ControlHandler(
            ws or MagicMock(),
            self.radio,
            "test",
            self.radio.model,
            server=self.server,
            managed_tx_authority=self.authority,
            session_id=f"websocket-{name}",
        )

    async def key(self, handler: ControlHandler) -> Any:
        self.enqueued.clear()
        task = asyncio.create_task(handler._enqueue_command("ptt_on", {}))
        await asyncio.wait_for(self.enqueued.wait(), timeout=5.0)
        entry = self.queue.take_entry()
        assert entry is not None
        with contextlib.suppress(Exception):
            await self.poller._execute_queued_entry(entry)
        return await task

    async def stop(self) -> None:
        await _unkey(self.composition)


@pytest.fixture
async def stack(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    started = await _Stack(tmp_path, getattr(request, "param", _IC705)).start()
    try:
        yield started
    finally:
        await started.stop()


async def test_ws_ptt_on_still_keys_when_tx_target_unknown(stack: _Stack) -> None:
    # A radio that never publishes a target (nothing seeded) keeps keying:
    # the band permit does not decide without a known frequency.
    assert await stack.key(stack.handler("a")) == {}
    assert stack.authority.tx_holder_nowait() == "websocket-a"
    assert stack.actuator.keyed == [ActuationOperation.PTT_ON]


@pytest.mark.parametrize("frequency_hz", (_WFM, 121_500_000, 60_000_000))
async def test_ws_ptt_on_refused_on_receive_only_frequencies(
    stack: _Stack, frequency_hz: int
) -> None:
    seed_tx_target(stack.store, frequency_hz)
    with pytest.raises(ControllerError) as excinfo:
        await stack.key(stack.handler("a"))
    assert excinfo.value.code == "receive_only_band"
    assert stack.actuator.keyed == []


async def test_ws_ptt_on_refusal_reaches_the_client_with_its_reason(
    stack: _Stack,
) -> None:
    seed_tx_target(stack.store, _WFM)
    ws = SimpleNamespace(send_text=AsyncMock())
    stack.enqueued.clear()
    task = asyncio.create_task(
        stack.handler("a", ws=ws)._dispatch_command("cmd-1", "ptt_on", {})
    )
    await asyncio.wait_for(stack.enqueued.wait(), timeout=5.0)
    entry = stack.queue.take_entry()
    assert entry is not None
    with contextlib.suppress(Exception):
        await stack.poller._execute_queued_entry(entry)
    await task
    response = decode_json(ws.send_text.await_args.args[0])
    assert response["ok"] is False
    assert "receive_only_band" in response["message"]
    assert stack.actuator.keyed == []


@pytest.mark.parametrize("stack", (_IC7610,), indirect=True)
async def test_ws_ptt_on_refused_outside_every_tx_band(stack: _Stack) -> None:
    # IC-7610 is HF+6m only: 2 m is outside every transmit row.
    seed_tx_target(stack.store, _TWO_METRES)
    with pytest.raises(ControllerError) as excinfo:
        await stack.key(stack.handler("a"))
    assert excinfo.value.code == "outside_tx_range"
    assert stack.actuator.keyed == []


async def test_ws_ptt_on_refused_on_inverse_split_tx_in_wfm(stack: _Stack) -> None:
    # RX sits in a TX band, the split TX VFO sits in WFM: the split-aware
    # target is the WFM frequency, so the key is refused.
    seed_tx_target(stack.store, _WFM, slot="B")
    with pytest.raises(ControllerError):
        await stack.key(stack.handler("a"))
    assert stack.actuator.keyed == []


@pytest.mark.parametrize(
    "stack,frequency_hz",
    (
        (_IC705, _TWO_METRES),
        (_IC705, 435_000_000),
        (_IC705, _TWENTY_METRES),
        (_FTX1, _TWO_METRES),
        (_FTX1, 7_074_000),
        (_IC7610, _TWENTY_METRES),
    ),
    indirect=["stack"],
)
async def test_ws_ptt_on_permitted_inside_tx_bands(
    stack: _Stack, frequency_hz: int
) -> None:
    seed_tx_target(stack.store, frequency_hz)
    assert await stack.key(stack.handler("a")) == {}
    assert stack.authority.tx_holder_nowait() == "websocket-a"
    assert stack.actuator.keyed == [ActuationOperation.PTT_ON]


async def test_ws_ptt_off_passes_while_the_permit_would_refuse(
    stack: _Stack,
) -> None:
    seed_tx_target(stack.store, _TWO_METRES)
    handler = stack.handler("a")
    await stack.key(handler)
    # The target retunes into WFM while keyed; PTT-off never consults the permit.
    seed_tx_target(stack.store, _WFM)
    assert await handler._enqueue_command("ptt_off", {}) == {}
    assert stack.authority.tx_holder_nowait() is None


async def test_ws_release_after_a_refusal_and_a_later_valid_key(
    stack: _Stack,
) -> None:
    seed_tx_target(stack.store, _WFM)
    handler = stack.handler("a")
    with pytest.raises(ControllerError):
        await stack.key(handler)
    assert stack.authority.tx_holder_nowait() is None
    # The refusal left nothing behind: the operator can retune and key again.
    seed_tx_target(stack.store, _TWO_METRES)
    assert await stack.key(handler) == {}
    assert stack.actuator.keyed == [ActuationOperation.PTT_ON]


# ---------------------------------------------------------------------------
# HTTP ingress: the latched transmit_on gets the same verdict
# ---------------------------------------------------------------------------


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    @property
    def status(self) -> int:
        return int(self.buffer.split(b" ", 2)[1])

    @property
    def payload(self) -> dict[str, object]:
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body or b"{}")


@pytest.mark.parametrize(
    "stack,frequency_hz,code",
    (
        (_IC705, _WFM, "receive_only_band"),
        (_IC7610, _TWO_METRES, "outside_tx_range"),
    ),
    indirect=["stack"],
)
async def test_http_transmit_on_gets_the_same_verdict_as_ws_ptt(
    stack: _Stack, frequency_hz: int, code: str
) -> None:
    seed_tx_target(stack.store, frequency_hz)
    # The same target refuses the WS key with the same code ...
    with pytest.raises(ControllerError) as excinfo:
        await stack.key(stack.handler("a"))
    assert excinfo.value.code == code
    # ... and the latched transmit_on over HTTP.
    server = stack.server
    server._production_managed_tx_port = SimpleNamespace(  # noqa: SLF001
        authority=stack.authority
    )

    async def enqueue_managed_positive_tx(
        *, ready: asyncio.Future[None], submission: asyncio.Task[Any], **_kw: Any
    ) -> Any:
        ready.set_result(None)
        return await submission

    server.enqueue_managed_positive_tx = enqueue_managed_positive_tx  # type: ignore[method-assign]

    body = json.dumps({"operation": "transmit_on"}).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    writer = _Writer()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers={"content-length": str(len(body))},
        reader=reader,
    )
    assert writer.status == 409
    assert writer.payload == {"error": code}
    assert stack.actuator.keyed == []


# ---------------------------------------------------------------------------
# rigctld ingress (``T 1``) over a live TCP socket
# ---------------------------------------------------------------------------


class _RigctldRadio(SerialMockRadio):
    """Serial mock that is also the managed-TX actuator and records F writes."""

    def __init__(self, model: str, store: StateStore) -> None:
        super().__init__(model=model)
        self.state_store = store
        self.freq_writes: list[int] = []
        self.actuator = _Actuator(self.profile)

    def supports_command(self, _name: str, **_kwargs: Any) -> bool:
        return True

    async def set_freq(self, freq: int, receiver: int = 0) -> None:
        self.freq_writes.append(freq)

    async def actuate(
        self, token: EffectToken, operation: Any, *, is_current: Any
    ) -> Any:
        return await self.actuator.actuate(token, operation, is_current=is_current)


class _NoDefault:
    async def execute(self, intent: CommandIntent) -> Any:
        raise AssertionError("shared default executor selected")


@pytest.fixture
async def rigctld(tmp_path: Path, request: pytest.FixtureRequest) -> Any:
    model = getattr(request, "param", _IC705)
    store = StateStore()
    generation = store.begin_provider_generation()
    store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=time.monotonic(),
            max_age=1e9,
        )
    )
    radio = _RigctldRadio(model, store)
    await radio.connect()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    await composition.transport_ready(object())
    await composition.bind_state_store(store)
    queue = PollerCommandQueue()
    queue.bind_connection_generation(lambda: "rigctld-connection-1")
    service = CommandService(executor=_NoDefault(), state_store=store)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0, command_timeout=1.0),
        managed_tx_authority=composition.authority,
        command_queue=queue,
        command_service=service,
    )
    await server.start()

    async def drain() -> None:
        while True:
            await queue.wait(0.01)
            while (entry := queue.take_entry()) is not None:

                async def execute(claimed: Any) -> None:
                    validate_command_queue_entry_currency(
                        claimed,
                        now=asyncio.get_running_loop().time(),
                        provider_generation=store.provider_generation,
                        connection_generation="rigctld-connection-1",
                        session_is_live=queue.session_is_live,
                        require_connection_generation=True,
                    )
                    if claimed.positive_tx_submission is not None:
                        await execute_positive_tx_queue_entry(claimed)

                with contextlib.suppress(Exception):
                    await execute_command_queue_entry(entry, execute)

    drain_task = asyncio.create_task(drain())
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    # The IC radio's own derivation, with the hardware read faked: the poller
    # computes the target from the observed inputs exactly as in production.
    poller = RadioPoller(_make_radio(model=model), CommandQueue(), state_store=store)
    rig = SimpleNamespace(
        store=store,
        generation=generation,
        radio=radio,
        poller=poller,
        reader=reader,
        writer=writer,
        actuator=radio.actuator,
        authority=composition.authority,
    )
    try:
        yield rig
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        await server.stop()
        drain_task.cancel()
        await asyncio.gather(drain_task, return_exceptions=True)
        await _unkey(composition)
        await radio.disconnect()


async def _released(rig: Any) -> None:
    """Wait (bounded) until the last unkey has settled and nothing is owed."""

    async def settled() -> None:
        while True:
            state = (await rig.authority.snapshot()).state
            if (
                state.intent.kind is ManagedTxIntentKind.RX
                and not state.release_required
            ):
                return
            await asyncio.sleep(0.005)

    await asyncio.wait_for(settled(), 3.0)


async def _send(rig: Any, line: str) -> bytes:
    rig.writer.write(f"{line}\n".encode())
    await rig.writer.drain()
    return await asyncio.wait_for(rig.reader.readline(), 3.0)


def _derive(rig: Any, freq: int, *, at: float | None = None) -> None:
    """The hardware read lands: the radio's inputs are observed, target derived."""

    _seed_tx_target_ready(
        rig.store,
        generation=rig.generation,
        active_freq=freq,
        now=time.monotonic() if at is None else at,
    )
    rig.poller._publish_tx_target()  # noqa: SLF001


def _target_value(rig: Any) -> Any:
    return rig.store.snapshot().field(_TX_TARGET_PATH).value


async def test_rigctld_retune_to_a_tx_band_then_key(rigctld: Any) -> None:
    _derive(rigctld, _WFM)
    assert _target_value(rigctld).frequency_hz == _WFM
    assert await _send(rigctld, "F 145000000") == b"RPRT 0\n"
    assert rigctld.radio.freq_writes == [_TWO_METRES]
    # The pre-write WFM target is gone the moment the write is acknowledged ...
    rigctld.poller._publish_tx_target()  # noqa: SLF001
    assert isinstance(_target_value(rigctld), UnknownTxTarget)
    # ... so an immediate key is not refused on the strength of the old radio.
    assert await _send(rigctld, "T 1") == b"RPRT 0\n"
    assert rigctld.actuator.keyed == [ActuationOperation.PTT_ON]
    assert await _send(rigctld, "T 0") == b"RPRT 0\n"
    await _released(rigctld)
    # Once the read-back lands the target is the new frequency, and keys.
    _derive(rigctld, _TWO_METRES)
    assert _target_value(rigctld).frequency_hz == _TWO_METRES
    assert await _send(rigctld, "T 1") == b"RPRT 0\n"


async def test_rigctld_retune_into_wfm_then_key_is_refused_once_read_back(
    rigctld: Any,
) -> None:
    _derive(rigctld, _TWENTY_METRES)
    assert await _send(rigctld, "F 100000000") == b"RPRT 0\n"
    assert rigctld.radio.freq_writes == [_WFM]
    await asyncio.sleep(0)  # the read is stamped after the write
    _derive(rigctld, _WFM)  # the read lands: the radio sits on WFM
    assert await _send(rigctld, "T 1") == b"RPRT -9\n"
    assert rigctld.actuator.keyed == []
    # Release after a refusal is a clean no-op, and a valid retune keys again.
    assert await _send(rigctld, "T 0") == b"RPRT 0\n"
    assert await _send(rigctld, "F 14074000") == b"RPRT 0\n"
    await asyncio.sleep(0)
    _derive(rigctld, _TWENTY_METRES)
    assert await _send(rigctld, "T 1") == b"RPRT 0\n"
    assert rigctld.actuator.keyed == [ActuationOperation.PTT_ON]


@pytest.mark.parametrize(
    "rigctld,frequency_hz",
    ((_IC705, _WFM), (_IC7610, _TWO_METRES)),
    indirect=["rigctld"],
)
async def test_rigctld_t1_refused_on_two_profiles(
    rigctld: Any, frequency_hz: int
) -> None:
    seed_tx_target(rigctld.store, frequency_hz)
    assert await _send(rigctld, "T 1") == b"RPRT -9\n"
    assert rigctld.actuator.keyed == []
