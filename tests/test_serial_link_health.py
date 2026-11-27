"""Serial I/O loss and recovery through the public state projection (MOR-4841)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
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
from rigplane.commands import parse_civ_frame
from rigplane.core.exceptions import ConnectionError as RadioConnectionError
from rigplane.runtime import _civ_rx
from rigplane.runtime import radio as radio_runtime
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.testing.timing import VirtualClock, await_until
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
        self.respond = True
        self.reply_overrides: dict[bytes, bytes] = {}
        self.transmitting = False

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
        self.fail_next = False
        self.writes: list[bytes] = []

    def write(self, data: bytes) -> None:
        if self.fail_next:
            self.fail_next = False
            raise OSError("transient write failure; stream remains usable")
        if not self.cable.present:
            raise OSError("serial write failed: cable removed")
        self.writes.append(bytes(data))
        self.pending.append(bytes(data))

    async def drain(self) -> None:
        for payload in self.pending:
            if self.cable.personality == "icom":
                body = payload[4:-1]
                if body in (b"\x1c\x00\x00", b"\x1c\x00\x01"):
                    self.cable.transmitting = bool(body[-1])
            elif payload in (b"TX0;", b"TX1;"):
                self.cable.transmitting = payload == b"TX1;"
            if not self.cable.respond:
                continue
            if self.cable.personality == "icom":
                await self.cable.peer.send(payload)
                body = payload[4:-1]
                response = {
                    b"\x03": b"\x03\x00\x40\x07\x14\x00",
                    b"\x1c\x00": b"\x1c\x00" + bytes((self.cable.transmitting,)),
                }.get(body)
                if response is not None:
                    self.reader.feed_data(
                        bytes((0xFE, 0xFE, payload[3], payload[2])) + response + b"\xfd"
                    )
                while not self.cable.peer._responses.empty():
                    self.reader.feed_data(self.cable.peer._responses.get_nowait())
            else:
                response = {
                    b"ID;": b"ID0840;",
                    b"IF;": b"IF00000014074000+000000200003;",
                    b"FA;": b"FA014074000;",
                    b"TX;": b"TX1;" if self.cable.transmitting else b"TX0;",
                }.get(payload)
                response = self.cable.reply_overrides.get(payload, response)
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


async def _route_serial_response(radio: Any, response: bytes | None) -> None:
    record_response = getattr(response, "record_response", None)
    if record_response is None:
        return
    try:
        frame = parse_civ_frame(response)
    except ValueError:
        return
    await radio._civ_runtime._route_civ_frame(
        frame, generation=radio._civ_epoch, record_response=record_response
    )


async def _api_json(server: WebServer, path: str = "/api/v1/info") -> dict[str, Any]:
    async def request() -> dict[str, Any]:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
        try:
            writer.write(
                f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n".encode("ascii")
            )
            await writer.drain()
            headers = await reader.readuntil(b"\r\n\r\n")
            assert headers.split(b"\r\n", 1)[0] == b"HTTP/1.1 200 OK"
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            return json.loads(await reader.readexactly(length))
        finally:
            writer.close()
            await writer.wait_closed()

    task = asyncio.create_task(request())
    try:
        await await_until(
            task.done, interval=0.001, description="serial health HTTP response"
        )
        return task.result()
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

    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, discovery=False, read_only=True),
    )
    try:
        await _finish(virtual_clock, radio.connect())
        await _finish(virtual_clock, server.start())
        if personality == "yaesu":
            assert isinstance(server._state_poller, YaesuCatPoller)
        else:
            assert server._radio_poller is not None
        initial = await _api_json(server)
        assert initial["connection"]["controlConnected"] is True
        assert initial["connection"]["radioReady"] is True
        initial_state = await _api_json(server, "/api/v1/state")
        assert initial_state["radioHealth"]["readiness"] == "ready"

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
            if failure == "write":
                with pytest.raises(cat_transport.CatTransportError):
                    await _finish(virtual_clock, radio._transport.query("FA;"))

        if failure == "write":
            immediate = await _api_json(server)
            assert immediate["connection"]["controlConnected"] is False
            assert immediate["connection"]["radioReady"] is False

        # The server owns the production poller; no recovery coroutine is replaced.
        await virtual_clock.elapse(0.2)
        for _ in range(120):
            state = await _api_json(server)
            assert state["connection"]["controlConnected"] is False
            assert state["connection"]["rigConnected"] is False
            assert state["connection"]["radioReady"] is False
            public_state = await _api_json(server, "/api/v1/state")
            assert public_state["radioHealth"]["radioLink"] == "reconnecting"
            assert public_state["radioHealth"]["readiness"] != "ready"
            await virtual_clock.elapse(0.2)

        previous_opens = cable.opens
        cable.present = True
        for _ in range(60):
            await virtual_clock.elapse(0.2)
            recovered = await _api_json(server)
            if recovered["connection"]["radioReady"] is True:
                break
        assert cable.opens > previous_opens
        assert recovered["connection"]["controlConnected"] is True
        assert recovered["connection"]["rigConnected"] is True
        assert recovered["connection"]["radioReady"] is True
        recovered_state = await _api_json(server, "/api/v1/state")
        assert recovered_state["radioHealth"]["radioLink"] == "connected"
        assert recovered_state["radioHealth"]["readiness"] == "ready"
        await _finish(virtual_clock, radio.disconnect())
        server.note_operator_disconnect()
        stopped_info = await _api_json(server)
        assert stopped_info["connection"]["controlConnected"] is False
        stopped_state = await _api_json(server, "/api/v1/state")
        assert stopped_state["connection"]["controlConnected"] is False
        assert stopped_state["radioHealth"]["radioLink"] != "reconnecting"
    finally:
        try:
            await _finish(virtual_clock, server.stop())
        finally:
            await _finish(virtual_clock, radio.disconnect())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "noise",
    [
        b"garbage",
        b"\xfe\xfe\xfd",
        b"\xfe\xfe\xe0\x94\xfd",
        b"\xfe\xfe\x94\xe0\x03\xfd",
        b"\xfe\xfe\xe1\x94\x03\x00\x40\x07\x14\x00\xfd",
        b"\xfe\xfe\xe0\xe1\x03\x00\x40\x07\x14\x00\xfd",
        b"\xfe\xfe\xe0\x95\x03\x00\x40\x07\x14\x00\xfd",
        b"\xfe\xfe\xe0\x94\x1c\x00\xfd",
        b"\xfe\xfe\xe0\x94\xfb\x00\xfd",
        b"\xfe\xfe\xe0\x94\xfa\x00\xfd",
        b"\xfe\xfe\xe0\x94\x03\x00\x40",
    ],
    ids=[
        "garbage",
        "short-frame",
        "no-command",
        "echo",
        "other-destination",
        "controller-source",
        "other-radio-source",
        "empty-ptt",
        "malformed-ack",
        "malformed-nak",
        "partial",
    ],
)
async def test_icom_recovery_requires_a_parsed_radio_frame(
    noise: bytes, serial_clock: VirtualClock
) -> None:
    cable = _Cable("icom")
    cable.respond = False
    link = SerialCivLink(
        device="COM3", open_serial_connection=cable.open, require_optional_deps=False
    )
    radio = Ic7300SerialRadio(
        device="COM3",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        _enumerate_serial_ports_fn=lambda: [],
    )
    await link.connect()
    try:
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        with pytest.raises(OSError, match="transient"):
            await _finish(serial_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd"))
        assert link.connected is False
        cable.reader.feed_data(noise)
        try:
            noise_response = await _finish(serial_clock, link.receive(timeout=0.3))
            await _route_serial_response(radio, noise_response)
        except serial_civ_link.SerialFrameTimeoutError:
            pass
        assert link.connected is False
        assert link.healthy is False

        valid = b"\xfe\xfe\xe0\x94\x03\x00\x40\x07\x14\x00\xfd"
        cable.reader.feed_data(valid)
        response = await _finish(serial_clock, link.receive(timeout=0.3))
        assert response == valid
        await _route_serial_response(radio, response)
        assert link.connected is True
        assert link.healthy is True
        assert cable.opens == 1
    finally:
        await link.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "noise",
    [b"FA;", b"garbage;", b"FAgarbage;", b"FA123;", b"FA\xff;", b"FB014074000;"],
    ids=[
        "echo",
        "garbage",
        "matching-prefix-garbage",
        "short-value",
        "non-ascii",
        "other-query",
    ],
)
async def test_yaesu_recovery_requires_a_parsed_matching_reply(
    noise: bytes,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cable = _Cable("yaesu")
    monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
    radio = YaesuCatRadio(device="COM3", profile="ftx1", audio_driver=FakeAudioDriver())
    await radio._transport.connect()
    try:
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        with pytest.raises(cat_transport.CatTransportError, match="transient"):
            await _finish(serial_clock, radio._transport._raw_write("FA;"))
        assert radio.control_connected is False
        cable.reply_overrides[b"FA;"] = noise
        with pytest.raises((cat_transport.CatTransportError, ValueError)):
            await _finish(serial_clock, radio.get_freq())
        assert radio.control_connected is False

        cable.reply_overrides[b"FA;"] = b"FA014074000;"
        assert await _finish(serial_clock, radio.get_freq()) == 14_074_000
        assert radio.control_connected is True
        assert cable.opens == 1
    finally:
        await radio._transport.close()


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


@pytest.mark.asyncio
@pytest.mark.parametrize("personality", ["icom", "yaesu"])
@pytest.mark.parametrize(
    "operation",
    [
        "raw-off",
        "force-receive",
        "stop-cw",
        "stop-tune",
        "read",
        "stale-force-receive",
        "stale-stop-cw",
        "stale-stop-tune",
    ],
)
async def test_usable_serial_stream_admits_release_and_recovery_after_failure(
    personality: str,
    operation: str,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cable = _Cable(personality)
    if operation == "force-receive":
        cable.peer.ptt_off_answer = None
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
        await _finish(serial_clock, radio.connect())
        await radio._stop_civ_data_watchdog()
        await _finish(serial_clock, radio.set_ptt(True))
        await _finish(serial_clock, radio.get_freq())
        # A new receive must be admitted after the failure, rather than an
        # already-pending read hiding SerialCivTransport's admission check.
        await radio._stop_civ_rx_pump()
        release = bytes(radio._commands.ptt_off(to_addr=radio._radio_addr))
        failure_write = link.send_written
        failure_payload = b"\xfe\xfe\x94\xe0\x03\xfd"
    else:
        monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
        radio = YaesuCatRadio(
            device="COM3", profile="ftx1", audio_driver=FakeAudioDriver()
        )
        await radio._transport.connect()
        await _finish(serial_clock, radio.set_ptt(True))
        link = radio._transport
        release = b"TX0;"
        failure_write = link._raw_write
        failure_payload = "FA;"

    try:
        writer = cable.writer
        assert writer is not None
        assert cable.transmitting is True
        writer.fail_next = True
        with pytest.raises((OSError, cat_transport.CatTransportError)):
            await _finish(serial_clock, failure_write(failure_payload))
        assert radio.control_connected is False
        assert radio.connected is False
        assert radio.radio_ready is False
        before = len(writer.writes)
        opens = cable.opens
        if personality == "icom":
            radio._start_civ_rx_pump()
        if operation == "raw-off":
            cable.respond = False
            await _finish(serial_clock, radio.set_ptt(False))
            assert writer.writes[before:] == [release]
            assert radio.control_connected is False
            assert cable.transmitting is False
        elif operation == "read":
            assert await _finish(serial_clock, radio.get_freq()) == 14_074_000
            assert radio.control_connected is True
            assert radio.connected is True
            assert radio.radio_ready is True
        else:
            stale = operation.startswith("stale-")
            semantic = operation.removeprefix("stale-")
            action = {
                "force-receive": ActuationOperation.FORCE_RECEIVE,
                "stop-cw": AbortOperation.STOP_CW,
                "stop-tune": AbortOperation.STOP_TUNE,
            }[semantic]
            if stale:
                try:
                    result = await _finish(
                        serial_clock,
                        radio.actuate(
                            EffectToken(1, 1, "stale-serial-recovery"),
                            action,
                            is_current=lambda: False,
                        ),
                    )
                except (RadioConnectionError, cat_transport.CatTransportError):
                    pass
                else:
                    assert action is ActuationOperation.FORCE_RECEIVE
                    assert result is ActuationResult.UNCERTAIN
                assert writer.writes[before:] == []
                assert cable.transmitting is True
                assert cable.opens == opens
                assert writer.closed is False
                return
            assert (
                await _finish(
                    serial_clock,
                    radio.actuate(
                        EffectToken(1, 1, "serial-recovery"),
                        action,
                        is_current=lambda: True,
                    ),
                )
                is ActuationResult.ACCEPTED
            )
            if operation == "force-receive":
                assert writer.writes[before] == release
                if personality == "yaesu":
                    assert writer.writes[before:] == [b"TX0;", b"TX;"]
                else:
                    assert writer.writes[before:] == [
                        release,
                        b"\xfe\xfe\x94\xe0\x1c\x00\xfd",
                    ]
                assert radio.control_connected is True
                assert radio.connected is True
                assert radio.radio_ready is True
                assert cable.transmitting is False
            elif personality == "yaesu":
                assert writer.writes[before:] == [
                    b"KY00;" if operation == "stop-cw" else b"AC000;"
                ]
            else:
                expected = (
                    radio._commands.stop_cw(to_addr=radio._radio_addr)
                    if operation == "stop-cw"
                    else radio._commands.set_tuner_status(0, to_addr=radio._radio_addr)
                )
                assert writer.writes[before:] == [bytes(expected)]
        assert cable.opens == opens
        assert cable.writer is writer
        assert writer.closed is False
    finally:
        if personality == "icom":
            await _finish(serial_clock, radio.disconnect())
        else:
            await radio._transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("personality", ["icom", "yaesu"])
@pytest.mark.parametrize("operation", ["raw-on", "ptt-on", "transmit-on"])
@pytest.mark.parametrize("write_fails", [False, True], ids=["usable", "write-error"])
async def test_serial_key_uses_existing_write_path_after_link_failure(
    personality: str,
    operation: str,
    write_fails: bool,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cable = _Cable(personality)
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
        await _finish(serial_clock, radio.connect())
        await radio._stop_civ_data_watchdog()
        failure_write = link.send_written
        failure_payload = b"\xfe\xfe\x94\xe0\x03\xfd"
        key = bytes(radio._commands.ptt_on(to_addr=radio._radio_addr))
    else:
        monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
        radio = YaesuCatRadio(
            device="COM3", profile="ftx1", audio_driver=FakeAudioDriver()
        )
        link = radio._transport
        await link.connect()
        failure_write = link._raw_write
        failure_payload = "FA;"
        key = b"TX1;"

    try:
        writer = cable.writer
        assert writer is not None
        cable.respond = False
        writer.fail_next = True
        with pytest.raises(
            (OSError, cat_transport.CatTransportError), match="transient"
        ):
            await _finish(serial_clock, failure_write(failure_payload))
        assert radio.control_connected is False
        before = list(writer.writes)
        writer.fail_next = write_fails

        if operation == "raw-on":
            pending = radio.set_ptt(True)
        else:
            action = {
                "ptt-on": ActuationOperation.PTT_ON,
                "transmit-on": ActuationOperation.TRANSMIT_ON,
            }[operation]
            pending = radio.actuate(
                EffectToken(1, 1, "serial-write"), action, is_current=lambda: True
            )
        if write_fails:
            with pytest.raises(
                (OSError, cat_transport.CatTransportError), match="transient"
            ):
                await _finish(serial_clock, pending)
            assert writer.fail_next is False
            assert writer.writes == before
        else:
            result = await _finish(serial_clock, pending)
            if operation != "raw-on":
                assert result is ActuationResult.ACCEPTED
            assert writer.writes[len(before) :] == [key]
            assert cable.transmitting is True
        assert radio.control_connected is False
        assert radio.connected is False
        assert radio.radio_ready is False
        assert cable.opens == 1
        assert cable.writer is writer
        assert writer.closed is False
    finally:
        if personality == "icom":
            await _finish(serial_clock, radio.disconnect())
        else:
            await link.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "personality,malformed,recovery_reply",
    [
        ("icom", b"\xfe\xfe\xe0\x94\x1c\x00\xfd", b"\xfe\xfe\xe0\x94\x1c\x00\x00\xfd"),
        (
            "icom",
            b"\xfe\xfe\xe0\x94\x1c\x00\x09\xfd",
            b"\xfe\xfe\xe0\x94\x1c\x00\x01\xfd",
        ),
        (
            "icom",
            b"\xfe\xfe\xe0\x94\x1c\x00\x00\x00\xfd",
            b"\xfe\xfe\xe0\x94\x1c\x00\x00\xfd",
        ),
        (
            "icom",
            b"\xfe\xfe\xe0\x94\x03\xff\xff\xff\xff\xff\xfd",
            b"\xfe\xfe\xe0\x94\x1c\x00\x00\xfd",
        ),
        ("yaesu", b"TX9;", b"TX0;"),
        ("yaesu", b"TX9;", b"TX1;"),
        ("yaesu", b"TX9;", b"TX2;"),
        ("yaesu-default-policy", b"TX9;", b"TX0;"),
        ("yaesu-default-policy", b"TX9;", b"TX1;"),
        ("yaesu-default-policy", b"TX9;", b"TX2;"),
    ],
    ids=[
        "icom-empty-ptt",
        "icom-unknown-ptt",
        "icom-long-ptt",
        "icom-invalid-frequency",
        "yaesu-profile-recover-rx",
        "yaesu-profile-recover-cat-tx",
        "yaesu-profile-recover-other-tx",
        "yaesu-default-recover-rx",
        "yaesu-default-recover-cat-tx",
        "yaesu-default-recover-other-tx",
    ],
)
async def test_malformed_serial_reply_cannot_clear_link_failure(
    personality: str,
    malformed: bytes,
    recovery_reply: bytes,
    serial_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cable = _Cable("icom" if personality == "icom" else "yaesu")
    routed = asyncio.Event()
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
        await _finish(serial_clock, radio.connect())
        await radio._stop_civ_data_watchdog()
        await _finish(serial_clock, radio.get_freq())
        route = radio._civ_runtime._route_civ_frame

        async def observe_route(frame: Any, **kwargs: Any) -> None:
            await route(frame, **kwargs)
            routed.set()

        monkeypatch.setattr(radio._civ_runtime, "_route_civ_frame", observe_route)
        failure_payload = b"\xfe\xfe\x94\xe0\x03\xfd"
        failure_write = link.send_written
        release = bytes(radio._commands.ptt_off(to_addr=radio._radio_addr))
    else:
        monkeypatch.setattr(cat_transport, "open_serial_port", cable.open)
        radio = YaesuCatRadio(
            device="COM3", profile="ftx1", audio_driver=FakeAudioDriver()
        )
        if personality == "yaesu-default-policy":
            radio._profile_cache = replace(
                radio.profile,
                tx_policy=replace(radio.profile.tx_policy, tx_state_map={}),
            )
        link = radio._transport
        await link.connect()
        failure_payload = "FA;"
        failure_write = link._raw_write
        release = b"TX0;"

    try:
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        cable.respond = False
        with pytest.raises((OSError, cat_transport.CatTransportError)):
            await _finish(serial_clock, failure_write(failure_payload))
        assert radio.control_connected is False
        assert radio.radio_ready is False
        stamp = radio.last_valid_frame_monotonic
        if personality == "icom":
            routed.clear()
            cable.reader.feed_data(malformed)
            await _finish(serial_clock, routed.wait())
        else:
            cable.respond = True
            cable.reply_overrides[b"TX;"] = malformed
            assert await _finish(serial_clock, radio.get_ptt()) is True
            cable.respond = False
        assert radio.control_connected is False
        assert radio.radio_ready is False
        assert radio.last_valid_frame_monotonic == stamp
        before_release = len(writer.writes)
        await _finish(serial_clock, radio.set_ptt(False))
        assert writer.writes[before_release:] == [release]
        assert radio.control_connected is False
        assert radio.radio_ready is False

        await serial_clock.elapse(0.1)
        if personality == "icom":
            routed.clear()
            cable.reader.feed_data(recovery_reply)
            await _finish(serial_clock, routed.wait())
        else:
            cable.respond = True
            cable.reply_overrides[b"TX;"] = recovery_reply
            assert await _finish(serial_clock, radio.get_ptt()) is (
                recovery_reply != b"TX0;"
            )
        assert radio.control_connected is True
        assert radio.radio_ready is True
        assert radio.last_valid_frame_monotonic != stamp
        cable.respond = False
        await _finish(serial_clock, radio.set_ptt(True))
        assert writer.writes[-1] == (
            bytes(radio._commands.ptt_on(to_addr=radio._radio_addr))
            if personality == "icom"
            else b"TX1;"
        )
        assert cable.opens == 1
        assert cable.writer is writer
        assert writer.closed is False
    finally:
        if personality == "icom":
            await _finish(serial_clock, radio.disconnect())
        else:
            await link.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        b"\xfe\xfe\xe0\x94\xfb\xfd",
        b"\xfe\xfe\xe0\x94\xfa\xfd",
        b"\xfe\xfe\xe0\x94\x19\x00\x94\xfd",
        b"\xfe\xfe\xe0\x94\x03\x00\x40\x07\x14\x00\xfd",
        b"\xfe\xfe\xe0\x94\x1c\x00\x00\xfd",
        b"\xfe\xfe\xe0\x94\x1c\x00\x01\xfd",
    ],
    ids=["ack", "nak", "identity", "frequency", "ptt-rx", "ptt-tx"],
)
async def test_icom_qualified_replies_recover_without_reopen(
    response: bytes, serial_clock: VirtualClock
) -> None:
    cable = _Cable("icom")
    cable.respond = False
    link = SerialCivLink(
        device="COM3", open_serial_connection=cable.open, require_optional_deps=False
    )
    radio = Ic7300SerialRadio(
        device="COM3",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        _enumerate_serial_ports_fn=lambda: [],
    )
    await link.connect()
    try:
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        with pytest.raises(OSError, match="transient"):
            await _finish(serial_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd"))
        assert link.connected is False
        assert link.healthy is False
        cable.reader.feed_data(response)
        received = await _finish(serial_clock, link.receive(timeout=0.3))
        assert received == response
        await _route_serial_response(radio, received)
        assert link.connected is True
        assert link.healthy is True
        assert cable.opens == 1
        assert cable.writer is writer
        assert writer.closed is False
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_icom_read_completed_before_write_failure_cannot_clear_it(
    serial_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    cable = _Cable("icom")
    cable.respond = False
    link = SerialCivLink(
        device="COM3", open_serial_connection=cable.open, require_optional_deps=False
    )
    radio = Ic7300SerialRadio(
        device="COM3",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        _enumerate_serial_ports_fn=lambda: [],
    )
    read_completed = asyncio.Event()
    release_read = asyncio.Event()
    trace: list[str] = []
    read_once = link._read_once

    async def hold_completed_read() -> bytes | None:
        chunk = await read_once()
        trace.append("read_completed")
        read_completed.set()
        await release_read.wait()
        return chunk

    monkeypatch.setattr(link, "_read_once", hold_completed_read)
    await link.connect()
    reading = asyncio.create_task(link.receive(timeout=1.0))
    try:
        response = b"\xfe\xfe\xe0\x94\x03\x00\x40\x07\x14\x00\xfd"
        cable.reader.feed_data(response)
        await _finish(serial_clock, read_completed.wait())
        assert reading.done() is False
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        with pytest.raises(OSError, match="transient"):
            await _finish(serial_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd"))
        trace.append("write_failed")
        assert link.connected is False
        assert link.healthy is False

        release_read.set()
        held = await reading
        assert held == response
        await _route_serial_response(radio, held)
        trace.append("routed")
        assert trace == ["read_completed", "write_failed", "routed"]
        assert link.connected is False
        assert link.healthy is False

        cable.reader.feed_data(response)
        fresh = await _finish(serial_clock, link.receive(timeout=0.3))
        await _route_serial_response(radio, fresh)
        assert link.connected is True
        assert link.healthy is True
        assert cable.opens == 1
        assert cable.writer is writer
        assert writer.closed is False
    finally:
        reading.cancel()
        await asyncio.gather(reading, return_exceptions=True)
        await link.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("reopen", [False, True], ids=["later-error", "new-session"])
async def test_icom_deferred_response_cannot_clear_a_later_failure(
    reopen: bool, serial_clock: VirtualClock
) -> None:
    cable = _Cable("icom")
    cable.respond = False
    link = SerialCivLink(
        device="COM3", open_serial_connection=cable.open, require_optional_deps=False
    )
    radio = Ic7300SerialRadio(
        device="COM3",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        _enumerate_serial_ports_fn=lambda: [],
    )
    await link.connect()
    try:
        response = b"\xfe\xfe\xe0\x94\x1c\x00\x00\xfd"
        cable.reader.feed_data(response + response)
        held = await _finish(serial_clock, link.receive(timeout=0.3))
        if reopen:
            await link.disconnect()
            await link.connect()
        writer = cable.writer
        assert writer is not None
        writer.fail_next = True
        with pytest.raises(OSError, match="transient"):
            await _finish(serial_clock, link.send_written(b"\xfe\xfe\x94\xe0\x03\xfd"))
        assert link.connected is False
        await _route_serial_response(radio, held)
        assert link.connected is False
        assert link.healthy is False
        if not reopen:
            queued = await _finish(serial_clock, link.receive(timeout=0.3))
            await _route_serial_response(radio, queued)
            assert link.connected is False
            assert link.healthy is False
        cable.reader.feed_data(response)
        fresh = await _finish(serial_clock, link.receive(timeout=0.3))
        await _route_serial_response(radio, fresh)
        assert link.connected is True
        assert link.healthy is True
        assert cable.opens == (2 if reopen else 1)
    finally:
        await link.disconnect()
