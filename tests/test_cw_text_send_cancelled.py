"""MOR-3700: ``await_fenced_cw_text`` tells an abort from a cancelled caller.

The production ``LocalTxWorkRunner`` and ``TxAbortFence``. A transmit abort
cancels only the fenced work task; the runner hands that on as
``CancelledError``. ``await_fenced_cw_text`` turns that into the defined
refusal, but a cancellation of the caller itself (server shutdown, a
client gone) must stay a cancellation, or the caller's task would never
end as cancelled. The seam tests (HTTP/WebSocket through a started
``WebServer``) are ``tests/testkit/test_sim_cw_text_entry_points.py``
(``test_stop_ends_pending_sends_before_they_play``) and
``tests/testkit/test_sim_cw_text_civ_cancelled_send.py``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from rigplane.profiles import CwTextSendCancelledError, await_fenced_cw_text
from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.runtime.managed_tx_fence import TxAbortFence


def _pending_work() -> tuple[asyncio.Event, Callable[..., object]]:
    entered = asyncio.Event()

    async def work(_is_current: Callable[[], bool]) -> None:
        entered.set()
        await asyncio.Event().wait()

    return entered, work


@pytest.mark.asyncio
async def test_an_abort_of_the_fenced_work_is_a_refusal() -> None:
    fence = TxAbortFence()
    entered, work = _pending_work()
    sending = asyncio.create_task(
        await_fenced_cw_text(LocalTxWorkRunner(fence).run(work), "FTX-1")
    )
    await entered.wait()
    result = await fence.force_off()
    assert result.failures == ()
    with pytest.raises(
        CwTextSendCancelledError,
        match="^CW text send to FTX-1 was cancelled by a transmit abort$",
    ):
        await sending
    assert not sending.cancelled()


@pytest.mark.asyncio
async def test_a_cancelled_caller_stays_cancelled() -> None:
    fence = TxAbortFence()
    entered, work = _pending_work()
    sending = asyncio.create_task(
        await_fenced_cw_text(LocalTxWorkRunner(fence).run(work), "FTX-1")
    )
    await entered.wait()
    sending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sending
    assert sending.cancelled()
