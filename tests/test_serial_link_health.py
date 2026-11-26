"""Serial I/O loss and recovery through the public state projection (MOR-4841)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends import _icom_serial_base
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers import serial_civ_link
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat import poller as cat_poller
from rigplane.backends.yaesu_cat import radio as cat_radio
from rigplane.backends.yaesu_cat import transport as cat_transport
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core import civ
from rigplane.runtime import _civ_rx
from rigplane.runtime import radio as radio_runtime
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.testing.timing import VirtualClock
from rigplane.web import runtime_helpers
from rigplane.web.server import WebConfig, WebServer

from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver
from test_yaesu_audio import FakeAudioDriver


@pytest.fixture
def serial_clock(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> VirtualClock:
    # The fixture drives loop timers; only these modules' monotonic reads
    # share that clock. No process-wide clock or sleep patch is installed.
    for module in (
        _icom_serial_base,
        serial_civ_link,
        cat_poller,
        cat_radio,
        cat_transport,
        civ,
        _civ_rx,
        radio_runtime,
        runtime_helpers,
    ):
        monkeypatch.setattr(module, "time", SimpleNamespace(monotonic=virtual_clock))
    return virtual_clock


class _Cable:
    def __init__(self, personality: str) -> None:
        self.personality = personality
        self.present = True
        self.opens = 0
        self.reader = asyncio.StreamReader()
        self.writer: _Writer | None = None
        self.peer = _FakeSerialCivLink(model_id=0x94)

    async def open(self, *args: Any, **kwargs: Any) -> tuple[Any, Any]:
        if not self.present:
            raise OSError("could not open port COM3: device missing")
        self.opens += 1
        self.reader = asyncio.StreamReader()
        self.writer = _Writer(self, self.reader)
        await self.peer.connect()
        return self.reader, self.writer


class _Writer:
    def __init__(self, cable: _Cable, reader: asyncio.StreamReader) -> None:
        self.cable = cable
        self.reader = reader
        self.pending: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        if not self.cable.present:
            raise OSError("serial write failed: cable removed")
        self.pending.append(bytes(data))

    async def drain(self) -> None:
        for payload in self.pending:
            if self.cable.personality == "icom":
                await self.cable.peer.send(payload)
                while not self.cable.peer._responses.empty():
                    self.reader.feed_data(self.cable.peer._responses.get_nowait())
            else:
                response = {
                    b"ID;": b"ID0840;",
                    b"IF;": b"IF00000014074000+000000200003;",
                    b"FA;": b"FA014074000;",
                }.get(payload)
                if response is not None:
                    self.reader.feed_data(response)
        self.pending.clear()

    def close(self) -> None:
        self.closed = True
        self.reader.feed_eof()

    async def wait_closed(self) -> None:
        pass


async def _finish(clock: VirtualClock, operation: Any) -> Any:
    task = asyncio.create_task(operation)
    try:
        for _ in range(100):
            await clock.elapse(0)
            if task.done():
                return task.result()
            await clock.elapse(0.1)
        pytest.fail("serial operation did not complete in 10 virtual seconds")
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("personality", ["icom", "yaesu"])
@pytest.mark.parametrize("failure", ["write", "read", "eof"])
async def test_serial_loss_reaches_public_state_and_recovers(
    personality: str,
    failure: str,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    virtual_clock = serial_clock
    cable = _Cable(personality)
    poller: YaesuCatPoller | None = None
    poll_task: asyncio.Task[None] | None = None
    if personality == "icom":
        link = SerialCivLink(
            device="COM3",
            open_serial_connection=cable.open,
            require_optional_deps=False,
        )
        radio = Ic7300SerialRadio(
            device="COM3",
            civ_link=link,
            audio_driver=_FakeUsbAudioDriver(),
            _enumerate_serial_ports_fn=lambda: [],
        )
    else:
        monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
        radio = YaesuCatRadio(
            device="COM3", profile="ftx1", audio_driver=FakeAudioDriver()
        )

    server = WebServer(radio, WebConfig())
    try:
        await _finish(virtual_clock, radio.connect())
        initial = server.build_public_state()
        assert initial["connection"]["controlConnected"] is True
        assert initial["radioHealth"]["readiness"] == "ready"

        cable.present = False
        if failure == "read":
            cable.reader.set_exception(OSError("serial read failed: cable removed"))
        elif failure == "eof":
            cable.reader.feed_eof()
        if personality == "icom":
            if failure == "write":
                with pytest.raises(OSError, match="cable removed"):
                    await _finish(
                        virtual_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd")
                    )
        else:
            if failure != "write":
                with pytest.raises(cat_transport.CatTransportError):
                    await _finish(virtual_clock, radio._transport.readline())
            else:
                with pytest.raises(cat_transport.CatTransportError):
                    await radio._transport.query("FA;")

        if failure == "write":
            immediate = server.build_public_state()
            assert immediate["connection"]["controlConnected"] is False
            assert immediate["radioHealth"]["radioLink"] == "reconnecting"
            assert immediate["radioHealth"]["readiness"] != "ready"

        if personality == "yaesu":
            poller = YaesuCatPoller(radio)
            poller._paused.set()

            async def poll() -> None:
                await radio._transport.query("FA;")

            poll_task = asyncio.create_task(poller._run_poll_cycle("fast", poll, 0.2))

        # One existing Icom watchdog tick / projection poll, with no real sleep.
        await virtual_clock.elapse(0.2)
        for _ in range(120):
            state = server.build_public_state()
            assert state["connection"]["controlConnected"] is False
            assert state["connection"]["rigConnected"] is False
            assert state["radioHealth"]["radioLink"] == "reconnecting"
            assert state["radioHealth"]["readiness"] != "ready"
            await virtual_clock.elapse(0.2)

        previous_opens = cable.opens
        cable.present = True
        for _ in range(60):
            await virtual_clock.elapse(0.2)
            recovered = server.build_public_state()
            if recovered["radioHealth"]["readiness"] == "ready":
                break
        assert cable.opens > previous_opens
        assert recovered["connection"]["controlConnected"] is True
        assert recovered["connection"]["rigConnected"] is True
        assert recovered["radioHealth"]["radioLink"] == "connected"
        assert recovered["radioHealth"]["readiness"] == "ready"
        if poll_task is not None:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)
        await _finish(virtual_clock, radio.disconnect())
        stopped = server.build_public_state()
        assert stopped["connection"]["controlConnected"] is False
        assert stopped["radioHealth"]["radioLink"] != "reconnecting"
    finally:
        if poll_task is not None:
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)
        if poller is not None:
            await poller.stop()
        await _finish(virtual_clock, radio.disconnect())


@pytest.mark.parametrize("personality", ["icom", "yaesu"])
@pytest.mark.asyncio
async def test_serial_local_write_cannot_clear_link_loss(
    personality: str,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    virtual_clock = serial_clock
    cable = _Cable(personality)
    if personality == "icom":
        transport = SerialCivLink(
            device="COM3",
            open_serial_connection=cable.open,
            require_optional_deps=False,
        )

        async def operation() -> None:
            await transport.send_written(b"\xfe\xfe\x94\xe0\x03\xfd")

        close = transport.disconnect
    else:
        monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
        transport = cat_transport.YaesuCatTransport(device="COM3")

        async def operation() -> None:
            await transport._raw_write("FA;")

        close = transport.close
    await transport.connect()
    try:
        cable.present = False
        with pytest.raises((OSError, cat_transport.CatTransportError)):
            await _finish(virtual_clock, operation())
        assert transport.connected is False
        cable.present = True
        # Local acceptance alone is not evidence of a radio response.
        try:
            await _finish(virtual_clock, operation())
        except (OSError, cat_transport.CatTransportError):
            pass
        assert transport.connected is False
        writer = cable.writer
        await transport.connect()
        assert writer is not None and writer.closed
        assert transport.connected is True
    finally:
        await close()


@pytest.mark.asyncio
async def test_disconnect_closes_failed_serial_session(
    serial_clock: VirtualClock,
) -> None:
    cable = _Cable("icom")
    link = SerialCivLink(
        device="COM3",
        open_serial_connection=cable.open,
        require_optional_deps=False,
    )
    radio = Ic7300SerialRadio(
        device="COM3",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        _enumerate_serial_ports_fn=lambda: [],
    )
    try:
        await _finish(serial_clock, radio.connect())
        await radio._stop_civ_data_watchdog()
        writer = cable.writer
        assert writer is not None
        cable.present = False
        with pytest.raises(OSError):
            await _finish(serial_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd"))
        radio._conn_state = RadioConnectionState.RECONNECTING
        await _finish(serial_clock, radio.disconnect())
        assert writer.closed is True
        assert radio._civ_transport is None
        assert link._writer_task is None
    finally:
        await _finish(serial_clock, radio.disconnect())
