"""Provider wiring for fenced local CW and tuner work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    EffectToken,
)


def _fenced_radio(fence: TxAbortFence | None) -> YaesuCatRadio:
    radio = YaesuCatRadio("/dev/null", tx_abort_fence=fence)
    transport = radio._transport
    transport._connected = True
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
async def test_unmanaged_calls_keep_the_direct_write_contract() -> None:
    radio = _fenced_radio(None)
    radio._transport.write = AsyncMock()

    await radio.send_cw_text("CQ")
    await radio.set_tuner_status(2)

    assert [call.kwargs for call in radio._transport.write.await_args_list] == [{}, {}]
    assert radio._transport.write.await_args_list[0].args[0].startswith("KY ")
    assert radio._transport.write.await_args_list[1].args[0].startswith("AC")
