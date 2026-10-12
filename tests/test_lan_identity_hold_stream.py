"""The LAN identity hold re-requests the CI-V data stream before a re-read.

The hold stops the CI-V data watchdog for its duration, so after a soft
reconnect whose single OpenClose(open) did not restart the radio's stream,
nothing re-requests it: the held link then sends only unanswered ``19 00``
re-reads. ``CoreRadio._lan_identity_reread_loop`` must send one
OpenClose(open) before each re-read until any CI-V payload arrives within
the hold (``_last_civ_data_received`` newer than the hold start).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import replace
from unittest.mock import AsyncMock

from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from test_radio import MockTransport


def make_radio() -> tuple[IcomRadio, MockTransport]:
    """Real CoreRadio over a mock transport whose control session looks open."""
    radio = IcomRadio("192.168.1.100", model="IC-7610", timeout=0.5)
    radio._profile = replace(radio._profile, expected_identity_ids=("0B90",))
    transport = MockTransport()
    transport._udp_transport = object()  # control session looks open
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    return radio, transport


def _park_hold(radio: IcomRadio) -> None:
    """An owned ``no_response`` hold on the current epoch, without a gate."""
    radio._conn_state = RadioConnectionState.CONNECTING
    radio._connection_identity = RadioIdentity(
        status=RadioIdentityStatus.NO_RESPONSE, expected_model=radio.model
    )
    radio._lan_identity_owned_epoch = radio._civ_epoch


def _fast_reread(radio: IcomRadio) -> None:
    """Collapse the re-read backoff so loop turns land quickly."""
    radio._LAN_IDENTITY_REREAD_BACKOFF_S = (0.01,)
    radio._LAN_IDENTITY_REREAD_STEADY_S = 0.01


async def _wait_until(predicate: Callable[[], bool], timeout: float = 2.0) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            return False
        await asyncio.sleep(0.005)
    return True


async def _stop_reread(radio: IcomRadio) -> None:
    task = radio._lan_identity_reread_task
    radio._cancel_lan_identity_reread()
    if task is not None:
        with suppress(asyncio.CancelledError):
            await task


async def test_hold_re_requests_stream_before_every_reread() -> None:
    radio, _transport = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    # Production never enters the hold with None here: soft_reconnect stamps
    # _last_civ_data_received before the gate starts the hold.
    radio._last_civ_data_received = time.monotonic() - 1.0
    order: list[str] = []

    async def open_close(*, open_stream: bool) -> None:
        order.append("open" if open_stream else "close")

    async def read() -> bytes | None:
        order.append("read")
        return None

    radio._send_open_close = AsyncMock(side_effect=open_close)
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: len(order) >= 6)
    finally:
        await _stop_reread(radio)
    assert order[:6] == ["open", "read", "open", "read", "open", "read"]
    assert radio._send_open_close.await_count == 3
    assert all(
        call.kwargs == {"open_stream": True}
        for call in radio._send_open_close.await_args_list
    )


async def test_payload_since_hold_start_stops_the_stream_nudge() -> None:
    radio, _transport = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    # A stamp older than the hold start (as soft_reconnect leaves it) must
    # not stop the nudge; only a payload stamped within the hold may.
    radio._last_civ_data_received = time.monotonic() - 1.0
    reads = 0
    first_read_started = asyncio.Event()
    release_first_read = asyncio.Event()

    async def read() -> bytes | None:
        nonlocal reads
        reads += 1
        if reads == 1:
            # Park the loop so the payload stamp lands before the next turn.
            first_read_started.set()
            await release_first_read.wait()
        return None

    radio._send_open_close = AsyncMock()
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await asyncio.wait_for(first_read_started.wait(), 2)
        assert radio._send_open_close.await_count == 1
        # A CI-V payload arrived within the hold: the stamp postdates the
        # loop start, so the next re-read must not re-request the stream.
        radio._last_civ_data_received = time.monotonic()
        release_first_read.set()
        assert await _wait_until(lambda: reads >= 2)
    finally:
        await _stop_reread(radio)
    assert reads >= 2
    assert radio._send_open_close.await_count == 1


async def test_failing_stream_nudge_does_not_end_the_hold() -> None:
    radio, _transport = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    reads = 0

    async def read() -> bytes | None:
        nonlocal reads
        reads += 1
        return None

    radio._send_open_close = AsyncMock(side_effect=ConnectionError("nudge lost"))
    radio._read_lan_identity_payload = AsyncMock(side_effect=read)
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: reads >= 2)
        task = radio._lan_identity_reread_task
        assert task is not None and not task.done()
    finally:
        await _stop_reread(radio)
    assert reads >= 2
    assert radio._send_open_close.await_count >= 2


async def test_acceptable_answer_completes_the_hold() -> None:
    radio, _transport = make_radio()
    _park_hold(radio)
    _fast_reread(radio)
    radio._send_open_close = AsyncMock()
    radio._read_lan_identity_payload = AsyncMock(return_value=b"\x0b\x90")
    finish = AsyncMock()
    radio._finish_lan_identity_completion = finish
    radio._start_lan_identity_reread()
    try:
        assert await _wait_until(lambda: finish.await_count >= 1)
    finally:
        await _stop_reread(radio)
    finish.assert_awaited_once()
    identity = radio.connection_identity
    assert identity is not None
    assert identity.status is RadioIdentityStatus.VERIFIED
    assert radio._lan_identity_answer_epoch == radio._civ_epoch
