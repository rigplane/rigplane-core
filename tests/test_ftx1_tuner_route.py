"""FTX AC route preservation through public and typed tuner commands."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.observations import YaesuObservationAdapter
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.core.exceptions import CommandError
from rigplane.runtime._poller_types import SetTunerStatus
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.web.radio_poller import CommandQueue
from test_managed_tx_authority import authority as make_authority
from test_web_ptt_readonly import _make_handler
from test_yaesu_cat_poller import _set_fresh_ptt_observation


def radio_with_answer(answer: str = "AC101") -> YaesuCatRadio:
    radio = YaesuCatRadio("/dev/null", audio_driver=MagicMock())
    radio._transport._connected = True
    radio._transport.query = AsyncMock(return_value=answer)
    radio._transport.write = AsyncMock()
    return radio


@pytest.mark.asyncio
@pytest.mark.parametrize("src", (0, 1))
@pytest.mark.parametrize("path", ("public", "typed", "web"))
async def test_same_route_off_readback_restore(src: int, path: str) -> None:
    radio = radio_with_answer(f"AC{src}01")
    poller = YaesuCatPoller(radio)
    _set_fresh_ptt_observation(poller, active=False)
    handler, _ = _make_handler(radio=radio)

    async def set_status(value: int) -> None:
        if path == "typed":
            await poller._execute_command(SetTunerStatus(value))
        elif path == "web":
            await handler._enqueue_command("set_tuner_status", {"value": value})
        else:
            await radio.set_tuner_status(value)

    assert await radio.get_tuner_status() == 1
    await set_status(0)
    radio._transport.query.return_value = f"AC{src}00"
    assert await radio.get_tuner_status() == 0
    await set_status(1)
    radio._transport.query.return_value = f"AC{src}01"
    assert await radio.get_tuner_status() == 1
    assert [call.args[0] for call in radio._transport.write.await_args_list] == [
        f"AC{src}00;",
        f"AC{src}01;",
    ]
    assert radio._transport.query.await_count == 5
    assert all(call.args == ("AC;",) for call in radio._transport.query.await_args_list)


@pytest.mark.asyncio
async def test_managed_handler_admits_off_and_rejects_positive_during_tx() -> None:
    radio = radio_with_answer("AC101")
    managed, *_ = make_authority()
    handler, _ = _make_handler(radio=radio, authority=managed)
    queue = CommandQueue()
    handler._server.command_queue = queue
    poller = YaesuCatPoller(radio, command_queue=queue)
    poller.bind_provider_generation(
        capture=lambda: handler._server.command_state_store.provider_generation
    )
    poller.bind_managed_tx_authority(managed)

    async def execute(value: int) -> dict[str, object]:
        pending = asyncio.create_task(
            handler._enqueue_command("set_tuner_status", {"value": value})
        )
        await queue.wait(timeout=0.1)
        await poller._drain_commands()
        return await pending

    try:
        submission = await managed.submit_ptt(True, "owner")
        await submission.wait_settlement()

        assert await execute(0) == {"value": 0}
        assert [call.args[0] for call in radio._transport.query.await_args_list] == [
            "AC;"
        ]
        assert [call.args[0] for call in radio._transport.write.await_args_list] == [
            "AC100;"
        ]

        with pytest.raises(CommandError, match="transmit authority"):
            await execute(1)
        assert radio._transport.query.await_count == 1
        assert radio._transport.write.await_count == 1
    finally:
        release = await managed.submit_ptt(False, "owner")
        await release.wait_settlement()
        await managed.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw, expected", (("AC100", 0), ("AC101", 1), ("AC103", 2)))
async def test_generic_state_domain(raw: str, expected: int) -> None:
    radio = radio_with_answer(raw)
    assert await radio.get_tuner_status() == expected


@pytest.mark.asyncio
async def test_generic_start_uses_native_three_and_raw_api_stays_native() -> None:
    radio = radio_with_answer("AC103")
    assert await radio.read_tuner() == 3
    assert await radio.get_tuner() == 3
    await radio.set_tuner_status(2)
    assert radio._transport.write.await_args.args == ("AC103;",)
    await radio.set_tuner(3, src=1, typ=0)
    assert radio._transport.write.await_args.args == ("AC103;",)
    await radio.set_tuner(1)
    assert radio._transport.write.await_args.args == ("AC001;",)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer", ("AC201", "AC111", "AC121", "AC102", "AC10x", "AC10", "AC1000")
)
async def test_unknown_route_or_state_never_writes(answer: str) -> None:
    radio = radio_with_answer(answer)
    with pytest.raises((ValueError, CommandError)):
        await radio.get_tuner_status()
    with pytest.raises(CommandError):
        await radio.set_tuner_status(0)
    radio._transport.write.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("value", (-1, 3, True, "1"))
async def test_invalid_generic_value_does_not_query_or_write(value: object) -> None:
    radio = radio_with_answer()
    with pytest.raises(ValueError):
        await radio.set_tuner_status(value)
    radio._transport.query.assert_not_awaited()
    radio._transport.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_each_write_reacquires_route_and_does_not_reuse_success_after_failure() -> (
    None
):
    radio = radio_with_answer()
    await radio.set_tuner_status(0)
    radio._transport.query.return_value = "AC001"
    await radio.set_tuner_status(1)
    radio._transport.query.return_value = "AC121"
    with pytest.raises(CommandError):
        await radio.set_tuner_status(0)
    assert [call.args[0] for call in radio._transport.write.await_args_list] == [
        "AC100;",
        "AC001;",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ("reconnect", "writer", "transport", "provider", "binding")
)
@pytest.mark.parametrize("boundary", ("reply", "write_gate"))
async def test_currency_change_prevents_write(change: str, boundary: str) -> None:
    radio = radio_with_answer()
    transport = radio._transport
    generation = [1]
    poller = YaesuCatPoller(radio)
    poller.bind_provider_generation(capture=lambda: generation[0])
    raw_write = AsyncMock()
    transport._raw_write = raw_write
    transport.flush_rx = AsyncMock()
    transport._drain_responses = AsyncMock(return_value=0)
    del transport.write  # Exercise the real exchange gate and final currency check.

    def invalidate() -> None:
        if change == "reconnect":
            transport.stats.reconnects += 1
        elif change == "writer":
            transport._writer = MagicMock()
        elif change == "transport":
            radio._transport = radio_with_answer()._transport
        elif change == "provider":
            generation[0] += 1
        else:
            poller.bind_provider_generation(capture=lambda: generation[0])

    if boundary == "reply":

        async def query(_command: str) -> str:
            invalidate()
            return "AC101"

        transport.query.side_effect = query
    else:
        transport.flush_rx.side_effect = invalidate

    with pytest.raises((CommandError, CatTransportError)):
        await radio.set_tuner_status(0)
    raw_write.assert_not_awaited()
    if radio._transport is not transport:
        radio._transport.write.assert_not_awaited()


@pytest.mark.asyncio
async def test_force_receive_has_no_tuner_acquisition_dependency() -> None:
    radio = radio_with_answer()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def query(_command: str) -> str:
        entered.set()
        await release.wait()
        return "AC101"

    radio._transport.query.side_effect = query
    operation = asyncio.create_task(radio.set_tuner_status(0))
    await entered.wait()
    try:
        result = await radio.actuate(
            EffectToken(7, 3, "test"),
            ActuationOperation.FORCE_RECEIVE,
            is_current=lambda: True,
        )
        assert result is ActuationResult.ACCEPTED
        assert radio._transport.write.await_args.args == ("TX0;",)
        assert not operation.done()
    finally:
        release.set()
        await operation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer, expected", (("AC103", 2), ("AC101", 1), ("AC102", None), ("AC121", None))
)
async def test_real_ac_observation_normalizes_or_omits_unknown(
    monkeypatch: pytest.MonkeyPatch,
    answer: str,
    expected: int | None,
) -> None:
    radio = radio_with_answer(answer)
    path = FieldPath.global_("operator_controls", "tuner_status")
    monkeypatch.setattr(
        YaesuObservationAdapter, "_can_poll", lambda self, field: field == path
    )
    observations = await YaesuObservationAdapter.from_radio(radio).poll_tx_controls()
    if expected is None:
        assert observations == ()
    else:
        assert len(observations) == 1
        assert observations[0].path == path
        assert observations[0].value == expected
        assert observations[0].source.native_id == "get_tuner_status"
    assert radio.radio_state.tuner_status == 0


@pytest.mark.asyncio
async def test_positive_ac_acquisition_is_inside_one_abort_fence() -> None:
    radio = radio_with_answer()
    fence = TxAbortFence()
    runner = LocalTxWorkRunner(fence)
    runner.run = AsyncMock(wraps=runner.run)
    radio._local_tx_work = runner
    entered = asyncio.Event()
    release = asyncio.Event()

    async def query(_command: str) -> str:
        entered.set()
        await release.wait()
        return "AC101"

    radio._transport.query.side_effect = query
    operation = asyncio.create_task(radio.set_tuner_status(1))
    await entered.wait()
    cleanup = fence.force_off()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await operation
    await cleanup
    runner.run.assert_awaited_once()
    # The final guard must still reject a transport that delays admission.
    for call in radio._transport.write.await_args_list:
        assert call.kwargs["is_current"]() is False
