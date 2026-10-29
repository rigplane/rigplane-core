"""MOR-3632 slice B: server-side TX frequency permit at PTT-on.

The permit is one pure core function
(:func:`rigplane.core.tx_target.evaluate_tx_frequency_permit`) over the
profile band rows and the server's published split-aware TX target, plus one
gate (:func:`rigplane.runtime.tx_frequency_permit.tx_frequency_permit_refusal`)
called by both PTT-on ingress paths — the WS control channel and the
rigctld ``T 1`` command. These tests drive the real seams: a real
``WebServer``/``CommandQueue``/``RadioPoller`` leg with a real
``ManagedTxAuthority`` for the WS path (the ``test_web_managed_ptt_rejection``
pattern), and a real ``RigctldHandler.execute(parse_line(...))`` for the
rigctld path. Simulated only; no hardware is touched.

The permit is BAND-ONLY: it decides solely from a FRESH KNOWN TX target that
carries a frequency.  An unknown, stale, missing, malformed, or
known-without-frequency target is "not applicable" here — the unknown-target
guard (MOR-3633) owns that case, so a radio that never publishes a target
keeps keying.

Failing-first: on the pre-slice-B code the permit functions this module
imports do not exist (the whole module errors red), and behaviourally
neither ingress path refuses PTT-on, so every refusal assertion below fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from test_managed_tx_authority import FakeConfigStore, FakeFence, FakeLane

from rigplane.core.command_service import CommandService
from rigplane.core.radio_protocol import Radio
from rigplane.core.state_pipeline_contracts import (
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
from rigplane.profiles import RadioProfile, resolve_radio_profile
from rigplane.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldConfig,
)
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import format_response, parse_line
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import ActuationOperation, ManagedTxIntentKind
from rigplane.runtime.tx_frequency_permit import (
    current_tx_target,
    tx_frequency_permit_refusal,
)
from rigplane.runtime.tx_interlock import TxInterlockRefusal
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.server import WebConfig, WebServer

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")
_PTT_PATH = FieldPath.global_("tx_state", "ptt")

# Band facts used below come from the shipped profiles, not from this file:
# IC-705 TX: 20m/2m(144-148)/70cm(430-450); rx_only: 2200m/630m/WFM/Air/Gen.
# FTX-1 TX: 40m/2m/70cm; its WFM/Air/Gen ranges carry no band rows at all.
# IC-7610 TX: HF+6m only (no 2m).
_IC705 = "IC-705"
_FTX1 = "FTX-1"
_IC7610 = "IC-7610"


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
    frequency_hz: int | None = 14_074_000,
    *,
    slot: TxSlot | None = "A",
    timestamp_monotonic: float | None = None,
) -> None:
    """Publish a FRESH known TX target — the state a keyed radio sits on.

    ``frequency_hz=None`` seeds the split-on / TX-VFO-unknown form.  No
    ``max_age``: the field never ages to STALE unless the fixture drives
    ``mark_stale_due`` itself.  Bound to the
    store's current provider generation (``apply_current``): a plain
    ``apply`` stamps generation 0, which a started server's rolled store
    silently drops.
    """

    store.apply_current(
        Observation(
            path=_TX_TARGET_PATH,
            value=_target(frequency_hz, slot=slot),
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=(
                time.monotonic() if timestamp_monotonic is None else timestamp_monotonic
            ),
        )
    )


# ---------------------------------------------------------------------------
# Pure permit function
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
def test_unknown_target_does_not_decide(model: str) -> None:
    # Band-only permit: no fresh known frequency means "not applicable", never
    # a refusal (the unknown-target guard owns that case).
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
        (_IC705, 14_074_000),  # 20m
        (_IC705, 145_000_000),  # 2m
        (_IC705, 435_000_000),  # 70cm
        (_FTX1, 7_074_000),  # 40m
        (_FTX1, 145_000_000),  # 2m
        (_FTX1, 432_100_000),  # 70cm
        (_IC7610, 14_074_000),  # 20m
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
        (100_000_000, "WFM"),
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
        _bands(_profile(_IC705)), _target(14_074_000)
    )
    assert decision.permitted


@pytest.mark.parametrize("frequency_hz", (100_000_000, 121_500_000, 60_000_000))
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
        _bands(_profile(_IC7610)), _target(145_000_000)
    )
    assert not decision.permitted
    assert decision.reason == "outside-tx-range"


def test_profile_without_band_rows_is_not_applicable() -> None:
    profile = replace(_profile(_IC705), freq_ranges=())
    assert evaluate_tx_frequency_permit((), _target(14_074_000)).permitted
    assert evaluate_tx_frequency_permit((), None).permitted
    # The gate agrees: no band rows, no refusal, whatever the target.
    store = StateStore()
    assert tx_frequency_permit_refusal(profile, store) is None
    assert tx_frequency_permit_refusal(profile, None) is None


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
def test_gate_does_not_refuse_an_unknown_target(model: str) -> None:
    # A banded profile with nothing published, a split-on/TX-VFO-unknown
    # target, and a store-less caller all pass the band permit.
    profile = _profile(model)
    store = StateStore()
    assert tx_frequency_permit_refusal(profile, store) is None
    seed_tx_target(store, None)
    assert tx_frequency_permit_refusal(profile, store) is None
    assert tx_frequency_permit_refusal(profile, None) is None


def test_gate_does_not_refuse_a_stale_target() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        Observation(
            path=_TX_TARGET_PATH,
            value=_target(100_000_000),  # WFM — would be refused if fresh
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=10.0,
            max_age=1.0,
        )
    )
    assert tx_frequency_permit_refusal(_profile(_IC705), store) is not None
    clock.advance(2.0)
    store.mark_stale_due()
    assert tx_frequency_permit_refusal(_profile(_IC705), store) is None


def test_gate_ignores_profile_less_and_mocked_radios() -> None:
    store = StateStore()
    assert tx_frequency_permit_refusal(None, store) is None
    assert tx_frequency_permit_refusal(MagicMock(), store) is None


# ---------------------------------------------------------------------------
# Store reader
# ---------------------------------------------------------------------------


def test_current_tx_target_reads_the_fresh_field() -> None:
    store = StateStore()
    assert current_tx_target(store.snapshot()) is None
    seed_tx_target(store, 145_000_000)
    target = current_tx_target(store.snapshot())
    assert isinstance(target, KnownTxTarget)
    assert target.frequency_hz == 145_000_000


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
    store.apply(
        Observation(
            path=_TX_TARGET_PATH,
            value=_target(145_000_000),
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=10.0,
            max_age=1.0,
        )
    )
    clock.advance(2.0)
    store.mark_stale_due()
    assert current_tx_target(store.snapshot()) is None

    # Malformed mappings and non-target junk read as unknown rather than
    # raising.
    assert (
        current_tx_target(_snapshot_with_tx_target_value({"status": "known"})) is None
    )
    assert current_tx_target(_snapshot_with_tx_target_value("bogus")) is None


# ---------------------------------------------------------------------------
# WS ingress (managed PTT) through the real server/queue/authority stack
# ---------------------------------------------------------------------------


class _LoopClock:
    """Authority clock on the loop base (as the poller-side deadlines are)."""

    def __call__(self) -> float:
        return asyncio.get_running_loop().time()


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


class _QueueStack:
    """Real WebServer + CommandQueue + RadioPoller leg + ManagedTxAuthority.

    Mirrors ``test_web_managed_ptt_rejection._QueueStack`` so the permit is
    exercised on the production PTT-on chain, not on a stand-in.
    """

    def __init__(self, model: str) -> None:
        self.lane = FakeLane([])
        self.authority = ManagedTxAuthority(
            self.lane,
            FakeConfigStore(10.0),
            FakeFence([]),
            provider_generation=7,
            clock=_LoopClock(),
        )
        self.radio = _radio(model)
        self.server = WebServer(self.radio, WebConfig(host="127.0.0.1", port=0))
        self.queue: CommandQueue = self.server.command_queue
        self.poller = RadioPoller(
            self.radio,
            self.queue,
            state_store=self.server.command_state_store,
            managed_tx_authority=self.authority,
        )
        self.enqueued = asyncio.Event()
        put_ordered = self.queue.put_ordered

        def notify_put(*args: Any, **kwargs: Any) -> Any:
            entry = put_ordered(*args, **kwargs)
            self.enqueued.set()
            return entry

        self.queue.put_ordered = notify_put

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


async def _drain_one(stack: _QueueStack) -> None:
    entry = stack.queue.take_entry()
    assert entry is not None
    with contextlib.suppress(Exception):
        await stack.poller._execute_queued_entry(entry)


async def _key(stack: _QueueStack, handler: ControlHandler) -> Any:
    stack.enqueued.clear()
    task = asyncio.create_task(handler._enqueue_command("ptt_on", {}))
    await asyncio.wait_for(stack.enqueued.wait(), timeout=5.0)
    await _drain_one(stack)
    return await task


async def _release(stack: _QueueStack) -> None:
    if (await stack.authority.snapshot()).state.intent.kind is not (
        ManagedTxIntentKind.RX
    ):
        submission = await stack.authority.submit_force_off()
        await submission.wait_settlement()
    await stack.authority.close()


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
async def test_ws_ptt_on_still_keys_when_tx_target_unknown(model: str) -> None:
    # A radio that never publishes a target (nothing seeded) keeps keying:
    # the band permit does not decide without a fresh known frequency.
    stack = _QueueStack(model)
    try:
        assert await _key(stack, stack.handler("a")) == {}
        assert stack.authority.tx_holder_nowait() == "websocket-a"
        assert [effect.operation for effect in stack.lane.effects] == [
            ActuationOperation.PTT_ON
        ]
    finally:
        await _release(stack)


async def test_ws_ptt_on_still_keys_when_split_tx_vfo_frequency_unknown() -> None:
    stack = _QueueStack(_IC705)
    seed_tx_target(stack.server.command_state_store, None)
    try:
        assert await _key(stack, stack.handler("a")) == {}
        assert [effect.operation for effect in stack.lane.effects] == [
            ActuationOperation.PTT_ON
        ]
    finally:
        await _release(stack)


@pytest.mark.parametrize(
    "frequency_hz",
    (100_000_000, 121_500_000, 60_000_000),
    ids=("wfm", "air", "gen"),
)
async def test_ws_ptt_on_refused_on_receive_only_frequencies(
    frequency_hz: int,
) -> None:
    stack = _QueueStack(_IC705)
    seed_tx_target(stack.server.command_state_store, frequency_hz)
    try:
        with pytest.raises(TxInterlockRefusal) as excinfo:
            await stack.handler("a")._enqueue_command("ptt_on", {})
        assert excinfo.value.reason_code == "receive_only_band"
        assert stack.queue.pending_count == 0
        assert stack.lane.effects == []
    finally:
        await _release(stack)


async def test_ws_ptt_on_refusal_reaches_the_client_with_its_reason_code() -> None:
    stack = _QueueStack(_IC705)
    seed_tx_target(stack.server.command_state_store, 100_000_000)
    try:
        ws = SimpleNamespace(send_text=AsyncMock())
        await stack.handler("a", ws=ws)._dispatch_command("cmd-1", "ptt_on", {})
        response = decode_json(ws.send_text.await_args.args[0])
        assert response["ok"] is False
        assert "receive_only_band" in response["message"]
        assert stack.queue.pending_count == 0
        assert stack.lane.effects == []
    finally:
        await _release(stack)


async def test_ws_ptt_on_refused_outside_every_tx_band() -> None:
    # IC-7610 is HF+6m only: 2 m is outside every transmit row.
    stack = _QueueStack(_IC7610)
    seed_tx_target(stack.server.command_state_store, 145_000_000)
    try:
        with pytest.raises(TxInterlockRefusal) as excinfo:
            await stack.handler("a")._enqueue_command("ptt_on", {})
        assert excinfo.value.reason_code == "outside_tx_range"
        assert stack.lane.effects == []
    finally:
        await _release(stack)


async def test_ws_ptt_on_refused_on_inverse_split_tx_in_wfm() -> None:
    # RX sits in a TX band, the split TX VFO sits in WFM: the split-aware
    # target is the WFM frequency, so the key is refused.
    stack = _QueueStack(_IC705)
    seed_tx_target(stack.server.command_state_store, 100_000_000, slot="B")
    try:
        with pytest.raises(TxInterlockRefusal) as excinfo:
            await stack.handler("a")._enqueue_command("ptt_on", {})
        assert excinfo.value.reason_code == "receive_only_band"
        assert stack.lane.effects == []
    finally:
        await _release(stack)


@pytest.mark.parametrize(
    ("model", "frequency_hz"),
    (
        (_IC705, 145_000_000),
        (_IC705, 435_000_000),
        (_IC705, 14_074_000),
        (_FTX1, 145_000_000),
        (_FTX1, 7_074_000),
        (_IC7610, 14_074_000),
    ),
    ids=("ic705-2m", "ic705-70cm", "ic705-20m", "ftx1-2m", "ftx1-40m", "ic7610-20m"),
)
async def test_ws_ptt_on_permitted_inside_tx_bands(
    model: str, frequency_hz: int
) -> None:
    stack = _QueueStack(model)
    seed_tx_target(stack.server.command_state_store, frequency_hz)
    try:
        assert await _key(stack, stack.handler("a")) == {}
        assert stack.authority.tx_holder_nowait() == "websocket-a"
        assert [effect.operation for effect in stack.lane.effects] == [
            ActuationOperation.PTT_ON
        ]
    finally:
        await _release(stack)


async def test_ws_ptt_off_passes_while_the_permit_would_refuse() -> None:
    stack = _QueueStack(_IC705)
    store = stack.server.command_state_store
    seed_tx_target(store, 145_000_000)
    try:
        handler = stack.handler("a")
        await _key(stack, handler)
        # The target retunes into WFM while keyed; PTT-off never consults
        # the permit.
        seed_tx_target(store, 100_000_000)
        assert await handler._enqueue_command("ptt_off", {}) == {}
        assert stack.authority.tx_holder_nowait() is None
    finally:
        await _release(stack)


# ---------------------------------------------------------------------------
# rigctld ingress (``T 1``) through the real parse/execute/format seam
# ---------------------------------------------------------------------------


def seed_fresh_rx(store: StateStore) -> None:
    """Seed the fresh known-RX PTT fact the unmanaged BLOCK gate requires."""

    store.apply_current(
        Observation(
            path=_PTT_PATH,
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=9.0,
            max_age=2.0,
        )
    )


def _rigctld_handler(
    model: str, *, managed: bool = False
) -> tuple[RigctldHandler, Any, StateStore]:
    radio = AsyncMock(spec=Radio)
    radio.profile = _profile(model)
    store = StateStore(freshness_clock=FreshnessClock(start=10.0))
    # The handler enforces all-or-nothing managed plumbing; the permit fires
    # before the managed authority/queue are touched, so stand-ins suffice
    # for those.  The command service is real: managed writes route through
    # it into the handler's own executor, which is where the gate lives.
    managed_kwargs = (
        {
            "managed_tx_authority": MagicMock(),
            "command_queue": MagicMock(),
            "command_service": CommandService(executor=MagicMock(), state_store=store),
        }
        if managed
        else {}
    )
    handler = RigctldHandler(
        radio, RigctldConfig(), state_store=store, **managed_kwargs
    )
    return handler, radio, store


@pytest.mark.parametrize("model", (_IC705, _FTX1, _IC7610))
@pytest.mark.parametrize("seeded", (None, "no-frequency"), ids=("unknown", "split-vfo"))
async def test_rigctld_ptt_on_still_keys_when_tx_target_unknown(
    model: str, seeded: str | None
) -> None:
    handler, radio, store = _rigctld_handler(model)
    seed_fresh_rx(store)
    if seeded:
        seed_tx_target(store, None, timestamp_monotonic=10.0)
    response = await handler.execute(parse_line(b"T 1"))
    assert response.ok
    radio.set_ptt.assert_awaited_once_with(True)


@pytest.mark.parametrize("managed", (False, True), ids=("unmanaged", "managed"))
async def test_rigctld_ptt_on_refusal_is_rprt_9_and_never_reaches_the_radio(
    managed: bool,
) -> None:
    handler, radio, store = _rigctld_handler(_IC705, managed=managed)
    seed_fresh_rx(store)
    seed_tx_target(store, 100_000_000, timestamp_monotonic=10.0)  # WFM
    command = parse_line(b"T 1")
    response = await handler.execute(command)
    assert response.error is HamlibError.ERJCTED
    assert format_response(command, response, ClientSession()) == b"RPRT -9\n"
    radio.set_ptt.assert_not_awaited()
    if managed:
        # Nothing reached the managed authority: no submission, no key.
        assert handler._managed_tx_authority.mock_calls == []


@pytest.mark.parametrize("frequency_hz", (100_000_000, 121_500_000), ids=("wfm", "air"))
async def test_rigctld_ptt_on_refused_on_receive_only_frequencies(
    frequency_hz: int,
) -> None:
    handler, radio, store = _rigctld_handler(_IC705)
    seed_fresh_rx(store)
    seed_tx_target(store, frequency_hz, timestamp_monotonic=10.0)
    response = await handler.execute(parse_line(b"T 1"))
    assert response.error is HamlibError.ERJCTED
    radio.set_ptt.assert_not_awaited()


@pytest.mark.parametrize(
    ("model", "frequency_hz"),
    ((_IC705, 145_000_000), (_IC705, 435_000_000), (_FTX1, 7_074_000)),
    ids=("ic705-2m", "ic705-70cm", "ftx1-40m"),
)
async def test_rigctld_ptt_on_permitted_inside_tx_bands(
    model: str, frequency_hz: int
) -> None:
    handler, radio, store = _rigctld_handler(model)
    seed_fresh_rx(store)
    seed_tx_target(store, frequency_hz, timestamp_monotonic=10.0)
    response = await handler.execute(parse_line(b"T 1"))
    assert response.ok
    radio.set_ptt.assert_awaited_once_with(True)


async def test_rigctld_ptt_off_passes_while_the_permit_would_refuse() -> None:
    handler, radio, store = _rigctld_handler(_IC705)
    seed_fresh_rx(store)
    seed_tx_target(store, 100_000_000, timestamp_monotonic=10.0)  # WFM
    response = await handler.execute(parse_line(b"T 0"))
    assert response.ok
    radio.set_ptt.assert_awaited_once_with(False)
