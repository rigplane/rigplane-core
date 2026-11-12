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
import json
from collections.abc import AsyncIterator

import pytest

from rigplane.core.exceptions import (
    RadioNotReadyError,
    ReceiverNotSelectedError,
    TimeoutError,
)
from rigplane.core.state_pipeline_contracts import CommandLifecycleEvent
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
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
            assert isinstance(excinfo.value, RadioNotReadyError)
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
            assert isinstance(excinfo.value, RadioNotReadyError)
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
    *,
    silence_s: float = _SILENT_AFTER_OPEN_S,
) -> AsyncIterator[tuple[IcomRadio, object]]:
    """A real ``IcomRadio`` connected to a LAN simulator silent since open."""
    sim = (
        ic7610_lan_fake(
            single_owner=True,
            keepalive_hold_s=30.0,
            civ_silent_after_open_s=silence_s,
        )
        if model == "IC-7610"
        else ic705_lan_fake(
            single_owner=True,
            keepalive_hold_s=30.0,
            civ_silent_after_open_s=silence_s,
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
            assert isinstance(excinfo.value, RadioNotReadyError)
            assert not isinstance(excinfo.value, ReceiverNotSelectedError)
            # The radio answers: the same call now returns the real value.
            await await_until(
                lambda: radio.radio_ready,
                timeout=30.0,
                description="the radio answers and becomes ready",
            )
            freq = await radio.get_freq()
            assert freq > 0


async def _http_command(
    web: WebServer, name: str, params: dict[str, object], *, cmd_id: str
) -> tuple[int, dict[str, object]]:
    """POST one command to the real HTTP API, with a bound that admits the
    commander timing out behind queued poller queries first."""
    body = json.dumps({"id": cmd_id, "name": name, "params": params}).encode()
    reader, writer = await asyncio.open_connection("127.0.0.1", web.port)
    try:
        writer.write(
            (
                "POST /api/v1/commands HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{web.port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 15)
    finally:
        writer.close()
        await writer.wait_closed()
    response = raw.split(b"\r\n\r\n", 1)[1].decode()
    return int(raw.split(b" ", 2)[1]), json.loads(response)


def _event(
    events: list[CommandLifecycleEvent], cmd_id: str, states: tuple[str, ...]
) -> CommandLifecycleEvent | None:
    return next(
        (e for e in reversed(events) if e.command_id == cmd_id and e.state in states),
        None,
    )


class TestWebCommandOverLanSimulator:
    """HTTP writes traverse the production queue and poller during silence."""

    @pytest.mark.asyncio
    @_REALTIME
    @pytest.mark.parametrize("model", ["IC-7610", "IC-705"])
    async def test_set_freq_while_silent_fails_retriable_then_recovers(
        self, model: str
    ) -> None:
        async with _silent_lan_radio(model, silence_s=20.0) as (radio, _sim):
            web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
            events: list[CommandLifecycleEvent] = []
            unsubscribe = web.command_service.subscribe_lifecycle(events.append)
            try:
                await web.start()
                status, result = await _http_command(
                    web, "set_freq", {"freq": 14_174_000}, cmd_id="mor4382-silent"
                )
                assert not radio.radio_ready
                assert status == 504 and result["error"] == "command_timeout", (
                    status,
                    result,
                )
                silent_id = next(e.command_id for e in events if e.source == "http")
                failure = await await_until(
                    lambda: _event(events, silent_id, ("failed", "timed_out")),
                    timeout=25.0,
                    description="the queued set_freq fails",
                )
                assert failure.state == "timed_out", failure
                if radio.profile.receiver_count > 1:
                    assert _NOT_READY_MATCH in str(failure.message), failure
                assert (failure.details or {}).get("reason") != (
                    "receiver_not_selected"
                ), failure
                # The radio answers: the same command through the same API
                # now completes on the radio.
                await await_until(
                    lambda: radio.radio_ready,
                    timeout=30.0,
                    description="the radio answers and becomes ready",
                )
                status, result = await _http_command(
                    web, "set_freq", {"freq": 14_174_000}, cmd_id="mor4382-awake"
                )
                assert status == 200 and result["ok"] is True, (status, result)
                # The HTTP response awaits the poller-settled future. Check
                # the radio readback as well, rather than an enqueue ack.
                assert await radio.get_freq() == 14_174_000
            finally:
                unsubscribe()
                await web.stop()


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


class TestRigctldMirrorReadiness:
    @pytest.mark.asyncio
    @_REALTIME
    @pytest.mark.parametrize(
        ("model", "receiver"), [("IC-7610", 0), ("IC-705", 0), ("IC-7610", 1)]
    )
    async def test_populated_mirror_during_silent_reconnect(
        self, model: str, receiver: int
    ) -> None:
        async with _silent_lan_radio(model) as (radio, sim):
            await await_until(
                lambda: radio.radio_ready and radio.radio_state.main.freq > 0,
                timeout=30.0,
                description="initial live frequency",
            )
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
                    if receiver == 1:
                        await radio.select_receiver(1)
                        assert await _exchange(writer, reader, b"\\chk_vfo\n") == b"1\n"
                    command = b"f VFOB\n" if receiver == 1 else b"f\n"
                    live_freq = await radio.get_freq(receiver=receiver)
                    assert await _exchange(writer, reader, command) == (
                        f"{live_freq}\n".encode()
                    )
                    await radio.disconnect()
                    sim.civ_silent_after_open_s = 20.0
                    await radio.connect()
                    assert radio.connected and not radio.radio_ready
                    mirror = (
                        radio.radio_state.sub
                        if receiver == 1
                        else radio.radio_state.main
                    )
                    assert mirror.freq == live_freq
                    reply = await _exchange(writer, reader, command)
                    assert reply == b"RPRT -5\n", reply
                    assert await _exchange(writer, reader, b"v\n") == b"RPRT -5\n"
                    await await_until(
                        lambda: radio.radio_ready,
                        timeout=30.0,
                        description="reconnected radio answers again",
                    )
                    if receiver == 1:
                        await radio.select_receiver(1)
                    recovered_freq = await radio.get_freq(receiver=receiver)
                    assert (
                        await _exchange(writer, reader, command)
                        == f"{recovered_freq}\n".encode()
                    )
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    @_REALTIME
    @pytest.mark.parametrize("model", ["IC-7610", "IC-705"])
    @pytest.mark.parametrize(
        "command",
        [b"m\n", b"v\n", b"t\n", b"l STRENGTH\n", b"l RFPOWER\n"],
    )
    async def test_mirror_reads_during_silence(
        self, model: str, command: bytes
    ) -> None:
        async with _silent_lan_radio(model, silence_s=20.0) as (radio, _sim):
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
                    reply = await _exchange(writer, reader, command)
                    assert reply == b"RPRT -5\n", reply
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await server.stop()

    @pytest.mark.asyncio
    @_REALTIME
    @pytest.mark.parametrize("command", [b"f VFOB\n", b"m VFOB\n"])
    @pytest.mark.parametrize("ready", [False, True])
    async def test_unobserved_sub_is_unavailable(
        self, command: bytes, ready: bool
    ) -> None:
        async with _silent_lan_radio("IC-7610", silence_s=20.0) as (radio, _sim):
            if ready:
                await await_until(
                    lambda: radio.radio_ready,
                    timeout=30.0,
                    description="MAIN answers before SUB observation",
                )
            assert radio.radio_state.sub.freq == 0
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
                    assert await _exchange(writer, reader, b"\\chk_vfo\n") == b"1\n"
                    reply = await _exchange(writer, reader, command)
                    assert reply == b"RPRT -5\n", reply
                finally:
                    writer.close()
                    await writer.wait_closed()
            finally:
                await server.stop()
