"""MOR-4382 follow-up: commands on a connected-but-not-ready radio.

PR #4556 changed ``connect()``: when the radio does not answer CI-V yet, the
link stays up, ``conn_state`` becomes CONNECTED at once and the post-connect
tail runs on the radio's first answer. Consumers therefore reach commands on
a radio whose ``radio_ready`` is still False and whose receiver selection is
unknown. This file pins that window: every consumer-visible command answers
a clear, retriable not-ready outcome — never a "receiver not selected"
refusal (rigctld maps that to the terminal ``RPRT -9``), never a crash,
never a made-up value — while MOR-3591's genuine unselected-receiver
refusal survives once the radio has answered.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import pytest

from rigplane.core.exceptions import ReceiverNotSelectedError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.testing.timing import await_until
from rigplane_testkit.leaked_radio_guard import fake_connected

from _helpers import observe_selected_receiver
from test_radio import MockTransport
from testkit.ic705_fakes import ic705_lan_fake
from testkit.ic7610_fakes import ic7610_lan_fake

_REALTIME = pytest.mark.realtime(reason="real UDP simulator, real CI-V timing")

#: The simulator answers nothing on CI-V for this long after the login, so
#: ``connect()`` returns with the link up, the tail pending and every
#: receiver-scoped fact unknown (the window PR #4556 introduced).
_SILENT_AFTER_OPEN_S = 7.0

_NOT_READY_MATCH = "has not answered CI-V since"


def _unit_radio(model: str) -> IcomRadio:
    """A connected ``model`` radio whose CI-V wire is a silent MockTransport."""
    radio = IcomRadio("192.168.1.102", timeout=0.5, model=model)
    transport = MockTransport()
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    fake_connected(radio)
    radio._civ_min_interval = 0
    return radio


async def _exchange(
    writer: asyncio.StreamWriter, reader: asyncio.StreamReader, payload: bytes
) -> bytes:
    """One rigctld line exchange with a bound generous enough for the
    commander to time out behind any queued poller query first."""
    writer.write(payload)
    await writer.drain()
    return await asyncio.wait_for(reader.readline(), 15)


class TestSilentDualReceiverCommands:
    """A dual-receiver radio that never answered must not claim a receiver
    is not selected: the answer is a retriable not-ready timeout."""

    @pytest.mark.asyncio
    async def test_read_is_retriable_not_ready(self) -> None:
        radio = _unit_radio("IC-7610")
        radio._civ_runtime.start_worker()
        try:
            with pytest.raises(TimeoutError, match=_NOT_READY_MATCH) as excinfo:
                await asyncio.wait_for(radio.get_freq(), 10)
            assert not isinstance(excinfo.value, ReceiverNotSelectedError)
        finally:
            await radio._civ_runtime.stop_worker()
            radio._connected = False

    @pytest.mark.asyncio
    async def test_write_is_retriable_not_ready(self) -> None:
        radio = _unit_radio("IC-7610")
        radio._civ_runtime.start_worker()
        try:
            with pytest.raises(TimeoutError, match=_NOT_READY_MATCH) as excinfo:
                await asyncio.wait_for(radio.set_freq(14_074_000), 10)
            assert not isinstance(excinfo.value, ReceiverNotSelectedError)
        finally:
            await radio._civ_runtime.stop_worker()
            radio._connected = False


class TestSelectionRefusalSurvives:
    """MOR-3591 unchanged: once the radio answered and the selection is
    known, an unselected receiver without a route still refuses."""

    @pytest.mark.asyncio
    async def test_unselected_receiver_read_still_refused(self) -> None:
        radio = _unit_radio("IC-7610")
        radio._civ_runtime.start_worker()
        try:
            # The radio talked (the answer stamps CI-V liveness) and reports
            # SUB selected; MAIN's mode was never observed in this window.
            observe_selected_receiver(radio, 1)
            with pytest.raises(ReceiverNotSelectedError):
                await asyncio.wait_for(radio.get_mode_info(receiver=0), 10)
        finally:
            await radio._civ_runtime.stop_worker()
            radio._connected = False


@contextlib.asynccontextmanager
async def _silent_lan_radio(
    model: str,
) -> AsyncIterator[tuple[IcomRadio, object]]:
    """A real ``IcomRadio`` connected to a LAN simulator silent since open."""
    sim = (
        ic7610_lan_fake(
            single_owner=True,
            keepalive_hold_s=30.0,
            civ_silent_after_open_s=_SILENT_AFTER_OPEN_S,
        )
        if model == "IC-7610"
        else ic705_lan_fake(
            single_owner=True,
            keepalive_hold_s=30.0,
            civ_silent_after_open_s=_SILENT_AFTER_OPEN_S,
        )
    )
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4382",
        password="mor4382",
        timeout=1.0,
        model=model,
        auto_reconnect=True,
        reconnect_delay=0.5,
    )
    try:
        await radio.connect()
        # The window under test: PR #4556's connect returned while the radio
        # had answered nothing on CI-V.
        assert radio.connected
        assert not radio.radio_ready
        yield radio, sim
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


class TestCoreOverLanSimulator:
    """The observed defect: ``get_freq`` right after ``connect()`` on a
    dual-receiver IC-7610 that has not answered CI-V."""

    @pytest.mark.asyncio
    @_REALTIME
    async def test_get_freq_before_ready_is_retriable_not_ready(self) -> None:
        async with _silent_lan_radio("IC-7610") as (radio, _sim):
            with pytest.raises(TimeoutError, match=_NOT_READY_MATCH) as excinfo:
                await asyncio.wait_for(radio.get_freq(), 15)
            assert not isinstance(excinfo.value, ReceiverNotSelectedError)
            # The radio answers: the same call now returns the real value.
            await await_until(
                lambda: radio.radio_ready,
                timeout=30.0,
                description="the radio answers and becomes ready",
            )
            freq = await radio.get_freq()
            assert freq > 0


class TestRigctldOverLanSimulator:
    """A real rigctld server over TCP: WSJT-X's ``f`` must see a retriable
    ``RPRT -5`` while the radio is silent, and the real frequency on the
    same connection once it answers."""

    @pytest.mark.asyncio
    @_REALTIME
    @pytest.mark.parametrize("model", ["IC-7610", "IC-705"])
    async def test_get_freq_while_silent_answers_retriable_timeout(
        self, model: str
    ) -> None:
        async with _silent_lan_radio(model) as (radio, _sim):
            server = RigctldServer(
                radio,
                RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2),
                allow_not_ready=True,
            )
            try:
                await server.start()
                host, port = server._server.sockets[0].getsockname()[:2]
                reader, writer = await asyncio.open_connection(host, port)
                try:
                    reply = await _exchange(writer, reader, b"f\n")
                    assert reply == b"RPRT -5\n", reply
                    await await_until(
                        lambda: radio.radio_ready,
                        timeout=30.0,
                        description="the radio answers and becomes ready",
                    )
                    reply = await _exchange(writer, reader, b"f\n")
                    assert int(reply) > 0, reply
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await server.stop()
