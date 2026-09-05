"""Provider wiring for fenced local CW and tuner work."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.commands.commander import Priority
from rigplane.core.types import CivFrame
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import CoreRadio


def _fenced_radio(fence: TxAbortFence | None) -> YaesuCatRadio:
    radio = YaesuCatRadio("/dev/null", tx_abort_fence=fence)
    transport = radio._transport
    transport._connected = True
    transport.query = AsyncMock(return_value="AC101")
    transport.flush_rx = AsyncMock(return_value=0)
    transport._drain_responses = AsyncMock(return_value=0)
    transport._raw_write = AsyncMock()
    return radio


def _effect_token() -> EffectToken:
    return EffectToken(1, 1, "force-off-test")


async def _hold_transport(
    radio: YaesuCatRadio,
) -> tuple[asyncio.Event, asyncio.Task[None]]:
    acquired = asyncio.Event()
    release = asyncio.Event()

    async def hold() -> None:
        async with radio._transport._exchange_gate.exchange():
            acquired.set()
            await release.wait()

    task = asyncio.create_task(hold())
    await acquired.wait()
    return release, task


async def _force_receive(radio: YaesuCatRadio) -> ActuationResult:
    return await radio.actuate(
        _effect_token(),
        ActuationOperation.FORCE_RECEIVE,
        is_current=lambda: True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "start_local_work",
    [
        pytest.param(lambda radio: radio.send_cw_text("CQ"), id="cw"),
        pytest.param(lambda radio: radio.set_tuner_status(2), id="tuner"),
    ],
)
async def test_force_receive_bypasses_queued_stale_local_work(
    start_local_work: Callable[[YaesuCatRadio], Awaitable[None]],
) -> None:
    fence = TxAbortFence()
    radio = _fenced_radio(fence)
    release, holder = await _hold_transport(radio)
    local = asyncio.create_task(start_local_work(radio))
    await asyncio.sleep(0)

    cleanup = fence.force_off()
    force_receive = asyncio.create_task(_force_receive(radio))
    await asyncio.sleep(0)
    release.set()

    assert await force_receive is ActuationResult.ACCEPTED
    with pytest.raises(CatTransportError, match="no longer current"):
        await local
    await holder
    await cleanup

    radio._transport._raw_write.assert_awaited_once()
    command = radio._transport._raw_write.await_args.args[0]
    assert command.startswith("TX0")


@pytest.mark.asyncio
async def test_force_receive_suppresses_remaining_cw_chunks() -> None:
    fence = TxAbortFence()
    radio = _fenced_radio(fence)
    first_drain_started = asyncio.Event()
    release_first_drain = asyncio.Event()
    drain_calls = 0

    async def drain(_command: str) -> int:
        nonlocal drain_calls
        drain_calls += 1
        if drain_calls == 1:
            first_drain_started.set()
            await release_first_drain.wait()
        return 0

    radio._transport._drain_responses = drain
    local = asyncio.create_task(radio.send_cw_text("A" * 25))
    await first_drain_started.wait()

    cleanup = fence.force_off()
    force_receive = asyncio.create_task(_force_receive(radio))
    await asyncio.sleep(0)
    release_first_drain.set()

    assert await force_receive is ActuationResult.ACCEPTED
    with pytest.raises(CatTransportError, match="no longer current"):
        await local
    await cleanup

    wire_commands = [
        call.args[0] for call in radio._transport._raw_write.await_args_list
    ]
    assert len(wire_commands) == 2
    assert wire_commands[0].startswith("KY ")
    assert wire_commands[1].startswith("TX0")


@pytest.mark.asyncio
async def test_unmanaged_calls_keep_native_writes_and_guard_generic_tuner() -> None:
    radio = _fenced_radio(None)
    radio._transport.write = AsyncMock()

    await radio.send_cw_text("CQ")
    await radio.set_tuner_status(2)

    cw_call, tuner_call = radio._transport.write.await_args_list
    assert cw_call.kwargs == {}
    assert tuner_call.kwargs["is_current"]() is True
    radio._transport.stats.reconnects += 1
    assert tuner_call.kwargs["is_current"]() is False
    radio._transport.query.assert_awaited_once_with("AC;")
    assert radio._transport.write.await_args_list[0].args[0].startswith("KY ")
    assert tuner_call.args == ("AC103;",)


def _icom_radio() -> CoreRadio:
    radio = CoreRadio("127.0.0.1", model="IC-7300")
    radio._check_connected = lambda: None
    return radio


def _ack() -> CivFrame:
    return CivFrame(to_addr=0xE0, from_addr=0x94, command=0xFB)


@pytest.mark.asyncio
async def test_icom_composition_injects_its_exact_runner_fence_and_authority(
    tmp_path,
) -> None:
    radio = _icom_radio()
    assert radio._local_tx_work is None
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")

    install_managed_tx_composition(radio, composition)

    assert radio._local_tx_work is composition.local_tx_work_runner
    assert radio._local_tx_work._abort_fence is composition._abort_fence
    assert composition.authority._abort_fence is composition._abort_fence
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_managed_cw_uses_one_operation_and_guards_every_chunk(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    predicates: list[Callable[[], bool] | None] = []

    async def send_raw(
        _frame: bytes,
        **kwargs: object,
    ) -> CivFrame:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        return _ack()

    radio._send_civ_raw = send_raw
    await radio.send_cw_text("A" * 31)

    runner.run.assert_awaited_once()
    assert len(predicates) == 2
    assert predicates[0] is predicates[1]
    assert predicates[0] is not None
    assert predicates[0]() is True
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_force_off_suppresses_late_cw_write_and_remaining_chunks(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    entered = asyncio.Event()
    release = asyncio.Event()
    wire: list[bytes] = []
    predicates: list[Callable[[], bool] | None] = []

    async def delayed_send_raw(
        frame: bytes,
        **kwargs: object,
    ) -> CivFrame:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        entered.set()
        await release.wait()
        if predicate is None or predicate():
            wire.append(frame)
        return _ack()

    radio._send_civ_raw = delayed_send_raw
    sending = asyncio.create_task(radio.send_cw_text("A" * 31))
    await entered.wait()

    cleanup = composition._abort_fence.force_off()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await sending
    result = await cleanup

    runner.run.assert_awaited_once()
    assert result.failures == ()
    assert len(predicates) == 2
    assert predicates[0] is predicates[1]
    assert predicates[0] is not None
    assert predicates[0]() is False
    assert wire == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 2])
async def test_icom_force_off_suppresses_late_tuner_write(
    tmp_path,
    value: int,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    entered = asyncio.Event()
    release = asyncio.Event()
    wire: list[bytes] = []
    predicates: list[Callable[[], bool] | None] = []

    async def delayed_send_raw(
        frame: bytes,
        **kwargs: object,
    ) -> None:
        predicate = kwargs.get("is_current")
        assert predicate is None or callable(predicate)
        predicates.append(predicate)
        entered.set()
        await release.wait()
        if predicate is None or predicate():
            wire.append(frame)

    radio._send_civ_raw = delayed_send_raw
    tuning = asyncio.create_task(radio.set_tuner_status(value))
    await entered.wait()

    cleanup = composition._abort_fence.force_off()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await tuning
    result = await cleanup

    runner.run.assert_awaited_once()
    assert result.failures == ()
    assert len(predicates) == 1
    assert predicates[0] is not None
    assert predicates[0]() is False
    assert wire == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_managed_empty_cw_stop_and_tuner_off_remain_direct(
    tmp_path,
) -> None:
    radio = _icom_radio()
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    runner = composition.local_tx_work_runner
    original_run = runner.run
    runner.run = AsyncMock(wraps=original_run)
    radio._send_civ_raw = AsyncMock(return_value=None)

    await radio.send_cw_text("")
    await radio.stop_cw_text()
    await radio.set_tuner_status(0)

    runner.run.assert_not_awaited()
    assert radio._send_civ_raw.await_args_list[0].kwargs == {
        "priority": Priority.IMMEDIATE
    }
    assert radio._send_civ_raw.await_args_list[1].kwargs == {"wait_response": False}
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_icom_unmanaged_calls_and_public_signatures_stay_direct() -> None:
    radio = _icom_radio()
    radio._send_civ_expect = AsyncMock(return_value=_ack())
    radio._send_civ_raw = AsyncMock(return_value=None)

    await radio.send_cw_text("CQ")
    await radio.set_tuner_status(2)

    assert radio._send_civ_expect.await_args.kwargs == {"label": "send_cw_text"}
    assert radio._send_civ_raw.await_args.kwargs == {"wait_response": False}
    assert "is_current" not in inspect.signature(CoreRadio.send_cw_text).parameters
    assert "is_current" not in inspect.signature(CoreRadio.stop_cw_text).parameters
    assert "is_current" not in inspect.signature(CoreRadio.set_tuner_status).parameters
