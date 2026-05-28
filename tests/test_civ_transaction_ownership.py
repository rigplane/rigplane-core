"""Scoped raw CI-V transaction ownership and poller quiesce coverage."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from unittest.mock import AsyncMock

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState
from rigplane.rigctld.state_cache import StateCache
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetFreq


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport):
    r = IcomRadio("192.168.1.100", timeout=0.05)
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    r._radio_addr = IC_7610_ADDR
    r._civ_ack_sink_grace = 0.001
    yield r
    r._connected = False
    r._civ_transport = None
    r._ctrl_transport = None


async def _wait_until(predicate: Callable[[], bool], *, timeout: float = 0.5) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.005)
    assert predicate()


async def test_raw_transaction_holding_owner_blocks_competing_external_owner(
    radio: IcomRadio,
) -> None:
    task = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=1.0)
    )
    try:
        await _wait_until(lambda: radio.external_cat_session_active is True)

        with pytest.raises(RuntimeError, match="CI-V stream is already owned"):
            radio.begin_external_cat_session()
    finally:
        task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert radio.external_cat_session_active is False


async def test_legacy_end_does_not_release_active_raw_transaction_owner(
    radio: IcomRadio,
) -> None:
    queue = CommandQueue()
    queue.put_ordered(SetFreq(7_074_000))
    poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())
    poller._execute = AsyncMock()  # noqa: SLF001
    poller._send_query = AsyncMock()  # noqa: SLF001
    poller._poll_unselected_slot = AsyncMock()  # noqa: SLF001

    task = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=1.0)
    )
    poller_started = False
    try:
        await _wait_until(lambda: radio.external_cat_session_active is True)

        radio.end_external_cat_session()

        poller.start()
        poller_started = True
        await asyncio.sleep(0.05)

        assert radio.external_cat_session_active is True
        poller._execute.assert_not_awaited()  # noqa: SLF001
        assert queue.has_commands is True
        with pytest.raises(RuntimeError, match="CI-V stream is already owned"):
            radio.begin_external_cat_session()
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if poller_started:
            poller.stop()
            await asyncio.sleep(0)

    assert radio.external_cat_session_active is False


async def test_raw_transaction_quiesces_web_poller_and_defers_queue_until_release(
    radio: IcomRadio,
) -> None:
    queue = CommandQueue()
    queue.put_ordered(SetFreq(7_074_000))
    poller = RadioPoller(radio, StateCache(), queue, radio_state=RadioState())
    poller._execute = AsyncMock()  # noqa: SLF001
    poller._send_query = AsyncMock()  # noqa: SLF001
    poller._poll_unselected_slot = AsyncMock()  # noqa: SLF001

    task = asyncio.create_task(
        radio.send_civ_transaction(0x03, expect="data", timeout=1.0)
    )
    poller_started = False
    try:
        await _wait_until(lambda: radio.external_cat_session_active is True)

        poller.start()
        poller_started = True
        await asyncio.sleep(0.05)

        poller._execute.assert_not_awaited()  # noqa: SLF001
        assert queue.has_commands is True

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert radio.external_cat_session_active is False

        await _wait_until(lambda: poller._execute.await_count == 1)  # noqa: SLF001
        assert queue.has_commands is False
    finally:
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        if poller_started:
            poller.stop()
            await asyncio.sleep(0)
