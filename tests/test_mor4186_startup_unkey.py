"""MOR-4186: a radio left keyed is released when Core starts or reconnects.

A USB-serial radio has no link watchdog, so a SIGKILLed Core leaves it
transmitting: the restarted process connects, reports ready, and nothing
checks PTT. The fix lives at the one edge every backend already reports
through ``ManagedTxComposition.transport_ready`` — provider activation —
and reuses the managed-TX authority's FORCE_RECEIVE, whose settlement is
the confirming readback the actuators already perform.

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
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
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
    assert "readback=accepted" in messages[0]
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
        assert "readback=accepted" in messages[0]
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
        assert all("readback=accepted" in message for message in messages)
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
    assert "not a verified readback" in messages[0]
    assert caplog.records[-1].levelno == logging.INFO
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
        composition.validate_state_store(store)
        records = _startup_unkey_log_records(caplog)
        assert len(records) == 1
        message = records[0].getMessage()
        assert "readback=uncertain" in message
        assert "may still be transmitting" in message
        assert records[0].levelno == logging.WARNING
    finally:
        result = await _shutdown_terminated(composition)
    assert result is ShutdownResult.TERMINATED
