"""Cancelled WebRTC close must not poison ``pc.close()`` (MOR-1431).

``aiortc``'s ``RTCPeerConnection.close()`` arms an internal "closed" future
first and resolves it only at the end of teardown, so a *later* ``close()``
does ``await`` that future. Cancelling a close mid-teardown therefore leaves
the future pending forever and every subsequent ``await pc.close()`` hangs.

These tests pin the fix with a hermetic fake that reproduces exactly that
poisoning semantic (no ``[webrtc]`` extra needed): cancel the first close
mid-teardown, then assert a second ``pc.close()`` still completes.
"""

from __future__ import annotations

import asyncio
from typing import Any, Callable

import pytest

from rigplane.web.transport.webrtc import WebRtcDataChannelConnection
from rigplane.web.transport.webrtc_session import _Session


class _FakeChannel:
    """Minimal ``RTCDataChannel`` stand-in: sync close, handler registry."""

    def __init__(self) -> None:
        self.readyState = "open"
        self.label = "control"
        self.closed = False
        self._handlers: dict[str, Callable[..., None]] = {}

    def on(self, event: str, handler: Callable[..., None]) -> Callable[..., None]:
        self._handlers[event] = handler
        return handler

    def send(self, data: object) -> None:
        pass

    def close(self) -> None:
        self.closed = True
        self.readyState = "closed"


class _PoisonableFakePC:
    """Fake ``RTCPeerConnection`` with aiortc's close-poisoning semantic.

    The first ``close()`` arms ``_closed_future`` and resolves it only at the
    end of a teardown window; a later ``close()`` awaits that future. If the
    first close is cancelled inside the window, the future stays pending and
    every subsequent ``close()`` hangs — exactly the MOR-1431 poisoning.
    """

    def __init__(self) -> None:
        self.connectionState = "connected"
        self.teardown_completed = False
        self._closed_future: asyncio.Future[bool] | None = None

    async def close(self) -> None:
        if self._closed_future is not None:
            await self._closed_future
            return
        self._closed_future = asyncio.get_running_loop().create_future()
        await asyncio.sleep(0.05)  # teardown window where a cancel can land
        self.teardown_completed = True
        self.connectionState = "closed"
        self._closed_future.set_result(True)


async def _cancel_mid_teardown(close_coro: Any) -> None:
    """Run ``close_coro`` and cancel it once it is inside pc teardown."""
    task = asyncio.ensure_future(close_coro)
    await asyncio.sleep(0.01)  # let close() enter the pc teardown window
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_cancelled_conn_close_does_not_poison_pc_close() -> None:
    """Cancelling ``WebRtcDataChannelConnection.close()`` keeps pc usable."""
    pc = _PoisonableFakePC()
    conn = WebRtcDataChannelConnection(_FakeChannel(), pc)  # type: ignore[arg-type]

    await _cancel_mid_teardown(conn.close())

    # A subsequent pc.close() must complete instead of hanging on the
    # poisoned internal future.
    await asyncio.wait_for(pc.close(), timeout=2.0)
    assert pc.teardown_completed


@pytest.mark.asyncio
async def test_cancelled_session_close_still_completes_pc_teardown() -> None:
    """Cancelling ``_Session.close()`` still runs pc teardown to completion."""
    pc = _PoisonableFakePC()
    session = _Session(pc)  # type: ignore[arg-type]

    await _cancel_mid_teardown(session.close())

    # The shield detaches pc teardown from the cancelled close: it finishes
    # in the background, so a subsequent pc.close() resolves instead of
    # hanging on the poisoned internal future.
    await asyncio.wait_for(pc.close(), timeout=2.0)
    assert pc.teardown_completed
