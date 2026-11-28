"""LAN stop and connect races with mock UDP endpoints."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from _helpers import wrap_civ_in_udp
from test_radio_connect import (
    ConnectMockTransport,
    _build_conninfo,
    _build_login_response,
    _build_status,
    _FakeSocket,
)

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core import transport
from rigplane.core.transport import ConnectionState, IcomTransport, _UdpProtocol
from rigplane.core.tx_safety import TxOutcome, TxOwner, TxSource
from rigplane.runtime import (
    _civ_rx,
    _control_phase,
    managed_radio_runtime,
    radio as radio_module,
    radio_reconnect,
    session_lifecycle,
)
from rigplane.runtime.radio import CoreRadio
from rigplane.testing.timing import VirtualClock

pytestmark = pytest.mark.timeout(10)


class ControlEndpoint(asyncio.DatagramTransport):
    def __init__(self, protocol: _UdpProtocol, close_delay: float) -> None:
        super().__init__()
        self.protocol = protocol
        self.close_delay = close_delay
        self.closing = False
        self.closed = False
        self.packets: list[bytes] = []

    def sendto(self, data: bytes, addr: object = None) -> None:
        self.packets.append(data)

    def close(self) -> None:
        self.closing = True
        asyncio.get_running_loop().call_later(self.close_delay, self._finish_close)

    def _finish_close(self) -> None:
        self.closed = True
        self.protocol.connection_lost(None)


class Control(IcomTransport):
    """Mock endpoint and radio replies, with production discovery and close."""

    def __init__(self) -> None:
        super().__init__()
        self.endpoints: list[ControlEndpoint] = []
        self.logins: list[asyncio.Task[None] | None] = []
        self.close_delay = 0.0
        self.park_next_open: asyncio.Event | None = None

    async def connect(self, host: str, port: int, **kwargs: object) -> None:
        self.state = ConnectionState.CONNECTING
        self.my_id = 0x10000 + len(self.endpoints) + 1
        self._udp_close_event = asyncio.Event()
        protocol = _UdpProtocol(self)
        endpoint = ControlEndpoint(protocol, self.close_delay)
        self.endpoints.append(endpoint)
        protocol.connection_made(endpoint)
        park, self.park_next_open = self.park_next_open, None
        if park is not None:
            await park.wait()
        self.remote_id = 0xDEADBEEF

    async def reconnect(self, host: str, port: int, **kwargs: object) -> None:
        await self.connect(host, port, **kwargs)

    def queue_response(self, packet: bytes) -> None:
        self._packet_queue.put_nowait(packet)

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await super().send_tracked(data, is_current=is_current)
        if len(data) == 0x80:
            self.logins.append(asyncio.current_task())
            self.queue_response(_build_login_response(token=self.my_id))
            self.queue_response(_build_conninfo())
        elif len(data) == 0x40 and data[0x15] == 0x05:
            reply = bytearray(data)
            reply[0x14] = 0x02
            struct.pack_into(
                "<I",
                reply,
                _control_phase.ControlPhaseRuntime._RENEWAL_RESPONSE_OFFSET,
                0,
            )
            self._route_data_packet(bytes(reply), 0, 0, self.remote_id)
        elif len(data) == 0x90:
            self.queue_response(_build_status())

    def notice(self) -> None:
        data = bytearray(0x90)
        struct.pack_into("<III", data, 0, len(data), 0, self.remote_id)
        struct.pack_into("<I", data, 12, self.my_id)
        # Deliver from the UDP dispatch boundary. This is an unsolicited notice,
        # so it does not participate in ordinary data sequence numbering.
        self._route_data_packet(bytes(data), 0, 0, self.remote_id)


@dataclass
class Link:
    radio: CoreRadio
    control: Control
    clock: VirtualClock
    ports: list[Civ] = field(default_factory=list)
    ptt_writes: list[tuple[float, bool]] = field(default_factory=list)
    ptt: bool = False

    async def complete(self, operation, *, budget: float = 2.0):
        task = asyncio.ensure_future(operation)
        await self.clock.elapse(budget)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pytest.fail(f"{operation!r} exceeded {budget}s")
        return task.result()

    def assert_recovery_stopped(self) -> None:
        radio = self.radio
        tasks = (
            radio._civ_runtime._reconnect_task,
            radio._session_lifecycle._recover_task,
            radio._civ_data_watchdog_task,
        )
        assert all(task is None or task.done() for task in tasks)
        assert not radio._civ_recovering


class Civ(ConnectMockTransport):
    def __init__(self, link: Link) -> None:
        super().__init__()
        self.link = link
        self.rx_packet_count = 0
        self._udp_transport: object | None = None

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: object | None = None,
        **kwargs: object,
    ) -> None:
        self._udp_transport = object()
        if isinstance(sock, _FakeSocket):
            sock.close()
        await super().connect(host, port, local_host=local_host, local_port=local_port)

    async def disconnect(self) -> None:
        await super().disconnect()
        self._udp_transport = None

    def payload(self) -> None:
        frame = build_civ_frame(CONTROLLER_ADDR, self.link.radio._radio_addr, 0xFB)
        self.queue_response(wrap_civ_in_udp(frame))

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await super().send_tracked(data, is_current=is_current)
        payload = data[_civ_rx.CIV_HEADER_SIZE :]
        if not payload.startswith(b"\xfe\xfe"):
            return
        frame = parse_civ_frame(payload)
        if (frame.command, frame.sub) != (0x1C, 0x00):
            return
        if frame.data:
            self.link.ptt = bool(frame.data[0])
            self.link.ptt_writes.append((self.link.clock.now, self.link.ptt))
            reply = build_civ_frame(CONTROLLER_ADDR, frame.to_addr, 0xFB)
        else:
            reply = build_civ_frame(
                CONTROLLER_ADDR, frame.to_addr, 0x1C, 0x00, bytes([self.link.ptt])
            )
        self.queue_response(wrap_civ_in_udp(reply))


@pytest.fixture
async def link(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> AsyncIterator[Link]:
    clock = virtual_clock
    for module in (
        _civ_rx,
        _control_phase,
        radio_module,
        managed_radio_runtime,
        radio_reconnect,
        session_lifecycle,
        transport,
    ):
        clock.scope_to(monkeypatch, module)
        monkeypatch.setattr(module, "asyncio", asyncio)
    monkeypatch.setattr(
        _control_phase,
        "_socket",
        SimpleNamespace(
            AF_INET=2, SOCK_DGRAM=2, socket=lambda *_: _FakeSocket(("192.0.2.2", 50004))
        ),
    )
    monkeypatch.setenv("RIGPLANE_MANAGED_TX", "1")
    radio = CoreRadio("192.0.2.1", model="IC-7610", auto_reconnect=False)
    result = Link(radio, Control(), clock)

    def new_port() -> Civ:
        port = Civ(result)
        result.ports.append(port)
        return port

    monkeypatch.setattr("rigplane.transport.IcomCivTransport", new_port)
    monkeypatch.setattr(
        radio, "probe_connection_identity", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(radio, "_start_identity_read", lambda: None)
    monkeypatch.setattr(radio, "_scope_session_ready", AsyncMock())
    monkeypatch.setattr(radio, "_fetch_initial_state", AsyncMock())
    monkeypatch.setattr(radio, "_ensure_audio_transport", AsyncMock())
    radio._ctrl_transport = result.control  # type: ignore[assignment]
    try:
        await result.complete(radio._session_lifecycle.connect())
        result.control.logins.clear()
        result.ports[0].payload()
        await clock.elapse(0)
        assert radio.radio_ready
        yield result
    finally:
        await result.complete(radio.disconnect(), budget=4)
        result.assert_recovery_stopped()


async def hold_tx(link: Link, owner: str) -> None:
    await link.complete(link.radio.rearm_managed_tx())
    tx = link.radio._managed_tx_runtime
    assert tx is not None
    outcome = await link.complete(tx.request_on(TxOwner(TxSource.WEBSOCKET, owner)))
    assert outcome.outcome is TxOutcome.ACCEPTED and link.ptt
    assert tx.tx_snapshot.lease_id is not None


async def test_disconnect_at_recovery_pump_join_writes_off(
    link: Link, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = link.radio
    await hold_tx(link, "disconnect-at-join")
    await link.clock.elapse(5.1)
    stop = radio._civ_runtime.stop_pump
    disconnect: asyncio.Task[None] | None = None

    async def disconnect_at_join() -> None:
        nonlocal disconnect
        if disconnect is None:
            disconnect = asyncio.create_task(radio.disconnect())
        await stop()

    monkeypatch.setattr(radio._civ_runtime, "stop_pump", disconnect_at_join)
    repair = asyncio.create_task(radio.soft_reconnect())
    try:
        await link.clock.elapse(4)
        assert disconnect is not None and disconnect.done()
        disconnect.result()
        assert link.ptt_writes[-1][1] is False
        assert all(port._udp_transport is None for port in link.ports)
        assert all(endpoint.closed for endpoint in link.control.endpoints)
        link.assert_recovery_stopped()
    finally:
        repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


@pytest.mark.parametrize("stop", ["disconnect", "shutdown"])
async def test_stop_joins_control_retirement_before_reconnect(
    link: Link, stop: str
) -> None:
    radio = link.radio
    old_endpoint = link.control.endpoints[0]
    old_endpoint.close_delay = 0.8
    link.control.notice()
    await link.clock.elapse(0)
    assert old_endpoint.closing and not old_endpoint.closed
    operation = (
        radio.disconnect
        if stop == "disconnect"
        else (radio._session_lifecycle.request_shutdown)
    )
    stopping = asyncio.create_task(operation())
    await link.clock.elapse(0.1)
    returned_early = stopping.done()
    if not returned_early:
        await link.clock.elapse(0.8)
    assert stopping.done()
    stopping.result()
    await link.complete(radio.connect())
    assert old_endpoint.closed
    assert link.control.state is ConnectionState.CONNECTED
    assert not returned_early, "stop returned before the UDP close callback"
    assert link.control._udp_transport is link.control.endpoints[-1]


async def test_connect_joins_full_control_open(link: Link) -> None:
    link.control.park_next_open = opened = asyncio.Event()
    link.control.notice()
    await link.clock.elapse(0)
    assert len(link.control.endpoints) == 2 and not link.control.logins
    connecting = asyncio.create_task(link.radio.connect())
    try:
        await link.clock.elapse(0.5)
        assert len(link.control.endpoints) == 2, "connect opened a second endpoint"
        assert not connecting.done()
        opened.set()
        await link.complete(connecting)
        assert len(link.control.logins) == 1
        await link.complete(link.radio.disconnect())
        assert all(endpoint.closed for endpoint in link.control.endpoints)
        assert all(port._udp_transport is None for port in link.ports)
        link.assert_recovery_stopped()
    finally:
        opened.set()
        connecting.cancel()
        await asyncio.gather(connecting, return_exceptions=True)
