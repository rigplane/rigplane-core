"""MOR-4186: a radio left keyed is released when Core starts or reconnects.

A USB-serial radio has no link watchdog, so a SIGKILLed Core leaves it
transmitting: the restarted process connects, reports ready, and nothing
checks PTT. The fix lives at the one edge every backend already reports
through ``ManagedTxComposition.transport_ready`` — provider activation —
and reuses the managed-TX authority's FORCE_RECEIVE, whose settlement is
the confirming readback the actuators already perform. PTT is read and
the release debt recorded BEFORE the provider is published, so no
ingress can be admitted while the read is in flight (review P1-1) and
``provider_available`` issues the force_receive under its own lock.

Fakes are built from the real backend types: the Icom rows run a real
``IcomRadio`` against the ``MockIcomRadio`` simulator with stateful PTT
(``rigplane_testkit``), and the Yaesu rows bind ``YaesuCatRadio``'s own
``actuate``/``read_ptt``/``read_transmit_state`` over an FTX-1 profile,
faking only the wire primitives (``_write``, ``read_ptt_token``).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTimeoutError
from rigplane.core.state_store import StateStore
from rigplane.core.tx_observation import TxStateReading
from rigplane.profiles import RadioProfile
from rigplane.rig_loader import load_rig
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from rigplane.runtime.radio import IcomRadio

_COMPOSITION_LOGGER = "rigplane.runtime.managed_tx_composition"


def _startup_unkey_records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == _COMPOSITION_LOGGER
        and record.getMessage().startswith("managed_tx.startup_unkey")
    ]


def _startup_unkey_log_records(caplog: pytest.LogCaptureFixture) -> list:
    return [
        record
        for record in caplog.records
        if record.name == _COMPOSITION_LOGGER
        and record.getMessage().startswith("managed_tx.startup_unkey")
    ]


async def _shutdown_terminated(
    composition: ManagedTxComposition,
) -> ShutdownResult:
    """The teardown for a radio whose release can never confirm.

    MOR-4102 shutdown semantics: shutdown still owes the rig a
    force_receive and waits for the drain or the termination signal; a
    radio that answers nothing can only leave through termination.
    """
    termination = asyncio.Event()
    termination.set()
    return await composition.shutdown(termination)


class _YaesuStartupFake:
    """FTX-1 fake: the real backend's TX methods over fake wire primitives."""

    actuate = YaesuCatRadio.actuate
    read_ptt = YaesuCatRadio.read_ptt
    read_transmit_state = YaesuCatRadio.read_transmit_state
    _interpret_ptt_token = YaesuCatRadio._interpret_ptt_token
    _cw_text_memory_type_code = YaesuCatRadio._cw_text_memory_type_code
    _end_earlier_cw_text_sends = YaesuCatRadio._end_earlier_cw_text_sends

    backend_id = "yaesu_cat"

    def __init__(self, *, ptt_token: str = "0") -> None:
        rig = load_rig(Path(__file__).resolve().parents[1] / "rigs" / "ftx1.toml")
        profile: RadioProfile = rig.to_profile()
        self.profile = profile
        self.model = profile.model
        self._poll_warned_fields: set[str] = set()
        self._cw_text_generation = 0
        self.ptt_token = ptt_token
        self.set_ptt_writes: list[str] = []
        self.stop_cw_writes = 0

    async def _write(
        self,
        command: str,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
        on_committed: Callable[[], None] | None = None,
        **params: str,
    ) -> None:
        del is_current, tier, on_committed
        if command == "set_ptt":
            self.set_ptt_writes.append(params["state"])
            # A radio that obeys: the write flips what a later read sees.
            self.ptt_token = params["state"]
        elif command == "send_cw":
            self.stop_cw_writes += 1

    async def read_ptt_token(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
    ) -> str:
        del is_current, tier
        return self.ptt_token


class _YaesuSilentFake(_YaesuStartupFake):
    """A radio whose PTT read never gets an answer (transport timeout)."""

    async def read_ptt_token(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
    ) -> str:
        del is_current, tier
        raise CatTimeoutError("Read timeout waiting for ';' terminator")


class _YaesuUnverifiedFake(_YaesuStartupFake):
    """A reading that claims TX without the verified-readback proof."""

    async def read_transmit_state(self) -> TxStateReading:
        return TxStateReading(value=True, source="yaesu_poll_response")


class _YaesuUnconfirmableReadbackFake(_YaesuStartupFake):
    """PTT answers on the ordinary read; the confirming readback never does.

    The startup read (``read_transmit_state``) goes through bare
    ``read_ptt_token()``; the FORCE_RECEIVE confirming readback passes
    ``is_current`` on the FORCE_RELEASE tier (``actuate``). Only the
    latter times out, so the one submission settles UNCERTAIN.
    """

    async def read_ptt_token(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
    ) -> str:
        if is_current is not None:
            raise CatTimeoutError("Read timeout waiting for ';' terminator")
        return self.ptt_token


class _YaesuRacingKeyFake(_YaesuStartupFake):
    """A live client keys the rig while the startup PTT read is in flight.

    The read reports the rig's real state, so on an unfixed head the
    racing key is admitted (the provider is already published there),
    the rig goes transmitting, and the read returns the verified True
    the startup step then releases — the P1-1 race. Before publication
    the racing key is refused and the read reports an untouched
    receiving rig.
    """

    def __init__(self, *, ptt_token: str = "0") -> None:
        super().__init__(ptt_token=ptt_token)
        self.composition: ManagedTxComposition | None = None
        self.race_outcome: ManagedTxOutcome | None = None

    async def read_transmit_state(self) -> TxStateReading:
        assert self.composition is not None
        submission = await self.composition.authority.submit_ptt(True, "race-seat")
        self.race_outcome = submission.outcome
        await submission.wait_settlement()
        return await _YaesuStartupFake.read_transmit_state(self)


class _YaesuFlakyLinkFake(_YaesuStartupFake):
    """A radio whose wire answers only while the link is up.

    The startup read additionally re-keys as the still-live managed
    owner, standing in for the client that keys the moment its
    reconnecting session sees the provider again.
    """

    def __init__(self, *, ptt_token: str = "0") -> None:
        super().__init__(ptt_token=ptt_token)
        self.link_up = True
        self.composition: ManagedTxComposition | None = None
        self.rekey_outcomes: list[ManagedTxOutcome] = []

    async def _write(
        self,
        command: str,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
        on_committed: Callable[[], None] | None = None,
        **params: str,
    ) -> None:
        if not self.link_up:
            raise CatTimeoutError("Write timeout waiting for ';' terminator")
        await super()._write(
            command,
            is_current=is_current,
            tier=tier,
            on_committed=on_committed,
            **params,
        )

    async def read_ptt_token(
        self,
        *,
        is_current: Callable[[], bool] | None = None,
        tier: Any = None,
    ) -> str:
        if not self.link_up:
            raise CatTimeoutError("Read timeout waiting for ';' terminator")
        return await super().read_ptt_token(is_current=is_current, tier=tier)

    async def read_transmit_state(self) -> TxStateReading:
        assert self.composition is not None
        submission = await self.composition.authority.submit_ptt(True, "seat-a")
        self.rekey_outcomes.append(submission.outcome)
        await submission.wait_settlement()
        return await _YaesuStartupFake.read_transmit_state(self)


class _YaesuSuspendedReadFake(_YaesuStartupFake):
    """A startup PTT read that suspends until released (rig keyed)."""

    def __init__(self, *, ptt_token: str = "1") -> None:
        super().__init__(ptt_token=ptt_token)
        self.read_started = asyncio.Event()
        self.release_read = asyncio.Event()

    async def read_transmit_state(self) -> TxStateReading:
        self.read_started.set()
        await self.release_read.wait()
        return await _YaesuStartupFake.read_transmit_state(self)


async def _activated_yaesu_composition(
    tmp_path: Path,
    fake: _YaesuStartupFake,
) -> tuple[ManagedTxComposition, StateStore]:
    composition = ManagedTxComposition(
        fake, config_path=tmp_path / f"{type(fake).__name__}.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    await composition.bind_state_store(store)
    return composition, store


async def _authority_is_clean(composition: ManagedTxComposition) -> bool:
    state = (await composition.authority.snapshot()).state
    return (
        state.intent.kind is ManagedTxIntentKind.RX
        and not state.release_required
        and state.pending_effect is None
    )


# ---------------------------------------------------------------------------
# Process start — the radio is already transmitting when Core connects
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_left_keyed_at_connect_gets_one_force_receive(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuStartupFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)

    assert fake.set_ptt_writes == ["0"]
    assert fake.ptt_token == "0"
    assert await _authority_is_clean(composition)
    composition.validate_state_store(store)
    messages = _startup_unkey_records(caplog)
    assert len(messages) == 1
    assert "managed_tx.startup_unkey" in messages[0]
    assert "backend=yaesu_cat" in messages[0]
    assert "model=FTX-1" in messages[0]
    assert "force_receive confirmed" in messages[0]
    assert caplog.records[-1].levelno == logging.WARNING
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_left_keyed_at_connect_gets_one_force_receive(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    from _perf_helpers import fast_connect
    from rigplane_testkit.icom import MockIcomRadio

    sim = MockIcomRadio(stateful_ptt=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    composition = ManagedTxComposition(
        radio, config_path=tmp_path / "icom-startup-unkey.json"
    )
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    try:
        # The SIGKILL aftermath: the radio is already transmitting when
        # this process connects — keyed with no owner in this process.
        sim._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
        caplog.set_level(logging.INFO)
        with fast_connect():
            await radio.connect()
        assert sim.ptt_on is True

        await composition.bind_state_store(store)

        assert sim.ptt_on is False
        assert [on for _, on in sim.ptt_set_writes] == [False]
        assert await _authority_is_clean(composition)
        composition.validate_state_store(store)
        messages = _startup_unkey_records(caplog)
        assert len(messages) == 1
        assert "backend=rigplane" in messages[0]
        assert "model=IC-7610" in messages[0]
        assert "force_receive confirmed" in messages[0]
        assert caplog.records[-1].levelno == logging.WARNING
    finally:
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()
        await sim.stop()


# ---------------------------------------------------------------------------
# PTT off at connect — no write at all
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_receiving_at_connect_is_never_written(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuStartupFake(ptt_token="0")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)

    assert fake.set_ptt_writes == []
    assert fake.stop_cw_writes == 0
    assert fake.ptt_token == "0"
    assert await _authority_is_clean(composition)
    assert _startup_unkey_records(caplog) == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_receiving_at_connect_is_never_written(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    from _perf_helpers import fast_connect
    from rigplane_testkit.icom import MockIcomRadio

    sim = MockIcomRadio(stateful_ptt=True)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    composition = ManagedTxComposition(
        radio, config_path=tmp_path / "icom-startup-unkey-off.json"
    )
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    try:
        caplog.set_level(logging.INFO)
        with fast_connect():
            await radio.connect()
        await composition.bind_state_store(store)

        assert sim.ptt_on is False
        assert sim.ptt_set_writes == []
        assert await _authority_is_clean(composition)
        assert _startup_unkey_records(caplog) == []
    finally:
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()
        await sim.stop()


# ---------------------------------------------------------------------------
# Reconnect — the same edge repeats
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_left_keyed_at_reconnect_is_released_again(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuStartupFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)
    try:
        assert fake.set_ptt_writes == ["0"]

        # The link dropped and came back; the rig was keyed again in
        # between (or the drop never unkeyed it). The poller's reconnect
        # reports the new transport: same edge, same release.
        fake.ptt_token = "1"
        store.begin_provider_generation()
        await composition.transport_ready(object())

        assert fake.set_ptt_writes == ["0", "0"]
        assert fake.ptt_token == "0"
        assert await _authority_is_clean(composition)
        composition.validate_state_store(store)
        messages = _startup_unkey_records(caplog)
        assert len(messages) == 2
        assert all("force_receive confirmed" in message for message in messages)
    finally:
        await composition.shutdown(asyncio.Event())


# ---------------------------------------------------------------------------
# Review P1-1/P1-2 — a TX this process owns is never released at the edge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tx_on_racing_the_startup_read_is_never_released(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """P1-1: a client TX ON admitted while the startup read is in flight.

    On the unfixed head the read runs after the provider is published,
    so the racing key is admitted, the rig goes transmitting, and the
    read returns the verified True whose force_off then releases the
    key this process just accepted. With the release recorded before
    publication the racing key is refused (no provider exists yet), the
    untouched receiving rig needs no release at all, and the owner keys
    normally once the provider is current.
    """
    fake = _YaesuRacingKeyFake(ptt_token="0")
    caplog.set_level(logging.INFO)
    composition = ManagedTxComposition(
        fake, config_path=tmp_path / "YaesuRacingKeyFake.json"
    )
    fake.composition = composition
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    await composition.bind_state_store(store)
    try:
        # The racing key was refused and never keyed the rig.
        assert fake.race_outcome is ManagedTxOutcome.REJECTED
        assert fake.set_ptt_writes == []
        assert _startup_unkey_records(caplog) == []

        # The owner keys normally once the provider is current, and
        # nothing releases the key for him. A held key carries its own
        # PTT_RELEASE plan — what must not exist is a FORCE_RECEIVE
        # debt over an RX intent, i.e. the intent itself proves the
        # key survived the edge.
        submission = await composition.authority.submit_ptt(True, "race-seat")
        assert submission.outcome is ManagedTxOutcome.ACCEPTED
        await submission.wait_settlement()
        assert fake.set_ptt_writes == ["1"]
        state = (await composition.authority.snapshot()).state
        assert state.intent == ManagedTxIntent.ptt("race-seat")
        assert state.last_actuation is not None
        assert state.last_actuation.result.value == "accepted"
        composition.validate_state_store(store)
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_owned_tx_reconnect_release_comes_only_from_the_authority(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """P1-2: at a reconnect the only release is the authority's own.

    A managed owner holds the key when the link drops; the authority's
    own release at ``transport_unavailable`` cannot confirm on the dead
    wire and stays owed. At ``transport_ready`` the startup unkey must
    not read PTT or release anything — the owed release and the live
    owner's re-key both belong to the authority, which retries the
    force_receive itself and hands the owner his key back.
    """
    fake = _YaesuFlakyLinkFake(ptt_token="0")
    caplog.set_level(logging.INFO)
    composition = ManagedTxComposition(
        fake, config_path=tmp_path / "YaesuFlakyLinkFake.json"
    )
    fake.composition = composition
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    await composition.bind_state_store(store)
    try:
        # The owner keys on the live link; the first edge's own re-key
        # attempt (the fake's) was refused before publication.
        submission = await composition.authority.submit_ptt(True, "seat-a")
        assert submission.outcome is ManagedTxOutcome.ACCEPTED
        await submission.wait_settlement()
        assert fake.set_ptt_writes == ["1"]
        assert fake.rekey_outcomes == [ManagedTxOutcome.REJECTED]

        # The link drops with the key held: the authority's own release
        # cannot confirm on the dead wire and stays owed.
        fake.link_up = False
        await composition.transport_unavailable(fake)
        state = (await composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required

        # The link returns with the rig still keyed. The reconnect edge
        # never read PTT (no new re-key attempt) and issued no release
        # of its own: the single "0" is the authority's own retry.
        fake.link_up = True
        store.begin_provider_generation()
        await composition.transport_ready(object())

        assert fake.rekey_outcomes == [ManagedTxOutcome.REJECTED]
        assert fake.set_ptt_writes == ["1", "0"]
        assert _startup_unkey_records(caplog) == []
        assert await _authority_is_clean(composition)
        composition.validate_state_store(store)

        # The still-live owner takes his key back on the new link, and
        # nothing releases it for him (the held key's own PTT_RELEASE
        # plan is not a debt; the intent proves the key survived).
        rekey = await composition.authority.submit_ptt(True, "seat-a")
        assert rekey.outcome is ManagedTxOutcome.ACCEPTED
        await rekey.wait_settlement()
        assert fake.set_ptt_writes == ["1", "0", "1"]
        state = (await composition.authority.snapshot()).state
        assert state.intent == ManagedTxIntent.ptt("seat-a")
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_yaesu_held_key_direct_reconnect_released_by_authority_only(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """P1-2 skip branch: a key the owner still holds is never touched.

    The poller reconnects without a transport loss; the authority's own
    ``start_provider_unavailable`` folds the held key into release debt
    and ``provider_available``'s retry releases the rig once. The
    startup release must skip without reading: no record, and no
    second FORCE_RECEIVE beyond the authority's own.
    """
    fake = _YaesuStartupFake(ptt_token="0")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)
    try:
        submission = await composition.authority.submit_ptt(True, "seat-a")
        assert submission.outcome is ManagedTxOutcome.ACCEPTED
        await submission.wait_settlement()
        assert fake.set_ptt_writes == ["1"]

        store.begin_provider_generation()
        await composition.transport_ready(object())

        # The single "0" is the authority's own retry of the folded
        # key; the startup edge added no release of its own.
        assert fake.set_ptt_writes == ["1", "0"]
        assert _startup_unkey_records(caplog) == []
        assert await _authority_is_clean(composition)
        composition.validate_state_store(store)
    finally:
        await composition.shutdown(asyncio.Event())


# ---------------------------------------------------------------------------
# PTT cannot be read — no blind writes, INFO only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_yaesu_silent_ptt_read_leaves_the_rig_untouched(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuSilentFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)

    assert fake.set_ptt_writes == []
    assert fake.stop_cw_writes == 0
    assert await _authority_is_clean(composition)
    composition.validate_state_store(store)
    messages = _startup_unkey_records(caplog)
    assert len(messages) == 1
    assert "PTT unreadable at connect (timeout)" in messages[0]
    assert caplog.records[-1].levelno == logging.INFO
    assert await _shutdown_terminated(composition) is ShutdownResult.TERMINATED


@pytest.mark.asyncio
async def test_cancelled_activation_joins_the_startup_release(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Round-2 review P1: cancellation joins the bounded publication.

    The PTT read is suspended with the rig keyed; cancelling the
    activation must not bisect the publication into a half-activated
    provider that shutdown would drain as clean. The read reaches its
    verified True, the debt is recorded, and the published provider's
    retry OFFs the rig before the cancellation is honoured.
    """
    fake = _YaesuSuspendedReadFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition = ManagedTxComposition(
        fake, config_path=tmp_path / "YaesuSuspendedReadFake.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    activation = asyncio.create_task(composition.bind_state_store(store))
    await asyncio.wait_for(fake.read_started.wait(), timeout=5)
    activation.cancel()
    # The verified TX answer arrives despite the cancellation.
    fake.release_read.set()
    with pytest.raises(asyncio.CancelledError):
        await activation

    # The joined publication released the keyed rig and finished.
    assert fake.set_ptt_writes == ["0"]
    assert await _authority_is_clean(composition)
    messages = _startup_unkey_records(caplog)
    assert len(messages) == 1
    assert "force_receive confirmed" in messages[0]
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_cancelled_publication_task_never_spins_and_owes_the_release(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Round-2 P1, inner half: cancelling the publication task itself.

    A shutdown-time blanket cancellation reaches the publication task
    too. The join must exit without spinning on the cancelled task, the
    abort retires the prepared provider, and the interrupted read
    leaves the release owed — shutdown never drains the provider as
    clean while the rig may still be keyed.
    """
    fake = _YaesuSuspendedReadFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition = ManagedTxComposition(
        fake, config_path=tmp_path / "YaesuSuspendedReadFakeCancel.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    activation = asyncio.create_task(composition.bind_state_store(store))
    await asyncio.wait_for(fake.read_started.wait(), timeout=5)

    publication = next(
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "managed-tx-provider-publication"
    )
    publication.cancel()
    activation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await activation

    # The interrupted read owes the release; nothing drained as clean.
    state = (await composition.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.release_required
    termination = asyncio.Event()
    termination.set()
    assert await composition.shutdown(termination) is ShutdownResult.TERMINATED


@pytest.mark.asyncio
async def test_cancelled_unstarted_publication_retires_and_owes(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    """Round-3 P1: a publication cancelled before its own bookkeeping.

    A shutdown-time blanket cancellation can reach the publication task
    before any of its own bookkeeping ran. The owning wrapper must
    still retire the prepared provider and record the release debt, so
    shutdown can never report DRAINED while the rig may be keyed.
    """
    retired: list[int] = []

    async def retire_provider(event: object) -> None:
        retired.append(getattr(event, "provider_generation"))

    fake = _YaesuStartupFake(ptt_token="1")
    composition = ManagedTxComposition(
        fake,
        config_path=tmp_path / "UnstartedPublication.json",
        retire_provider=retire_provider,
    )
    started = asyncio.Event()

    async def frozen_publication(event: object) -> None:
        del event
        started.set()
        # Never proceeds and runs none of the real bookkeeping, like a
        # task cancelled before its first instruction.
        await asyncio.Event().wait()

    composition._publish_provider_once = frozen_publication  # type: ignore[method-assign]
    caplog.set_level(logging.INFO)
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(fake)
    activation = asyncio.create_task(composition.bind_state_store(store))
    await asyncio.wait_for(started.wait(), timeout=5)

    publication = next(
        task
        for task in asyncio.all_tasks()
        if task.get_name() == "managed-tx-provider-publication"
    )
    publication.cancel()
    activation.cancel()
    with pytest.raises(asyncio.CancelledError):
        await activation

    assert retired == [1]
    state = (await composition.authority.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.release_required
    termination = asyncio.Event()
    termination.set()
    assert await composition.shutdown(termination) is ShutdownResult.TERMINATED


@pytest.mark.asyncio
async def test_yaesu_unverified_ptt_reading_never_triggers_a_write(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuUnverifiedFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    composition, store = await _activated_yaesu_composition(tmp_path, fake)

    assert fake.set_ptt_writes == []
    assert fake.stop_cw_writes == 0
    assert await _authority_is_clean(composition)
    composition.validate_state_store(store)
    messages = _startup_unkey_records(caplog)
    assert len(messages) == 1
    # An unverified reading that says ON is an operator-visible
    # anomaly (review P2), not an INFO footnote: the rig may really be
    # transmitting and Core refuses to act on it.
    assert "without a verified readback" in messages[0]
    assert caplog.records[-1].levelno == logging.WARNING
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_yaesu_unconfirmable_readback_is_one_bounded_submission(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    fake = _YaesuUnconfirmableReadbackFake(ptt_token="1")
    caplog.set_level(logging.INFO)
    # bind_state_store returning is the point: the startup unkey is one
    # bounded submission that settles and stops; it never loops itself.
    composition, store = await _activated_yaesu_composition(tmp_path, fake)
    try:
        assert fake.set_ptt_writes
        assert set(fake.set_ptt_writes) == {"0"}
        # The state is surfaced as an owed release, never as "not
        # transmitting" (intent RX + release debt is exactly that).
        state = (await composition.authority.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required
        # A client TX ON is refused while that release debt is owed
        # (review P1-1): the debt window closes only on a confirmed
        # force_receive readback, and until then keying over the
        # possibly still-keyed rig must be impossible.
        refused = await composition.authority.submit_transmit_on()
        assert refused.outcome is ManagedTxOutcome.REJECTED
        refused_state = refused.transition.state
        assert refused_state.intent.kind is ManagedTxIntentKind.RX
        assert refused_state.release_required
        composition.validate_state_store(store)
        records = _startup_unkey_log_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "release stays owed" in message
        assert "may still be transmitting" in message
        assert records[0].levelno == logging.WARNING
    finally:
        result = await _shutdown_terminated(composition)
    assert result is ShutdownResult.TERMINATED
