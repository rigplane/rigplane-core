"""MOR-4930 with the real CI-V detector and receive pump, without UDP sockets."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, TypeVar
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

_T = TypeVar("_T")


class ControlEndpoint(asyncio.DatagramTransport):
    def __init__(self, protocol: _UdpProtocol) -> None:
        super().__init__()
        self.protocol = protocol

    def sendto(self, data: bytes, addr: object = None) -> None:
        pass

    def close(self) -> None:
        asyncio.get_running_loop().call_later(0, self._finish_close)

    def _finish_close(self) -> None:
        self.protocol.connection_lost(None)


class Control(IcomTransport):
    """Mock endpoint and radio replies, with production discovery and close."""

    def __init__(self) -> None:
        super().__init__()
        self.endpoints: list[ControlEndpoint] = []
        self.logins: list[asyncio.Task[None] | None] = []
        self.down = False
        self.silent = False
        self.civ_dead = False

    async def connect(self, host: str, port: int, **kwargs: object) -> None:
        if self.down:
            raise TimeoutError("simulated radio is down")
        self.state = ConnectionState.CONNECTING
        self.my_id = 0x10000 + len(self.endpoints) + 1
        self._udp_close_event = asyncio.Event()
        protocol = _UdpProtocol(self)
        endpoint = ControlEndpoint(protocol)
        self.endpoints.append(endpoint)
        protocol.connection_made(endpoint)
        if self.silent:
            await self._discover()
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
            if self.civ_dead:
                return
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

    def notice(self, *, recipient: int | None = None) -> None:
        data = bytearray(0x90)
        struct.pack_into("<III", data, 0, len(data), 0, self.remote_id)
        struct.pack_into("<I", data, 12, self.my_id if recipient is None else recipient)
        # Deliver from the UDP dispatch boundary. This is an unsolicited notice,
        # so it does not participate in ordinary data sequence numbering.
        self._route_data_packet(bytes(data), 0, 0, self.remote_id)


@dataclass
class Link:
    radio: CoreRadio
    control: Control
    clock: VirtualClock
    ports: list[Civ] = field(default_factory=list)
    soft_discoveries: int = 0
    ptt_writes: list[bool] = field(default_factory=list)
    ptt: bool = False
    pause_soft: bool = False
    recovery_exits: list[tuple[bool, bool, bool]] = field(default_factory=list)

    async def complete(
        self, operation: Coroutine[Any, Any, _T], *, budget: float = 2.0
    ) -> _T:
        task = asyncio.create_task(operation)
        await self.clock.elapse(budget)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pytest.fail(f"test setup or cleanup did not finish: {operation!r}")
        return task.result()

    def recovery_state(self) -> tuple[bool, bool, bool]:
        radio = self.radio
        owner = any(
            task is not None and not task.done()
            for task in (
                radio._civ_runtime._reconnect_task,
                radio._session_lifecycle._recover_task,
            )
        )
        watchdog = radio._civ_data_watchdog_task
        armed = watchdog is not None and not watchdog.done()
        return owner, armed, radio._civ_recovering

    def assert_recovery_exits(self) -> None:
        assert self.recovery_exits, "no recovery task exited"
        for owner, armed, recovering in self.recovery_exits:
            if not owner and not armed:
                assert not recovering, "recovering flag has no owner or watchdog"
            assert owner or armed, "recovery exited without an owner or watchdog"

    async def ping_until(self, recovered: Callable[[], bool]) -> None:
        # Run the simulation through multiple ordinary watchdog windows so
        # a stranded test fails. Stop as soon as the outcome is observed.
        for _ in range(240):
            if recovered():
                return
            self.ports[-1].rx_packet_count += 1
            await self.clock.elapse(0.5)

    async def hold_tx(self, owner: str) -> None:
        await self.complete(self.radio.rearm_managed_tx())
        tx = self.radio._managed_tx_runtime
        assert tx is not None
        outcome = await self.complete(tx.request_on(TxOwner(TxSource.WEBSOCKET, owner)))
        assert outcome.outcome is TxOutcome.ACCEPTED and self.ptt
        assert tx.tx_snapshot.lease_id is not None


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
        if sock is None:
            self.link.soft_discoveries += 1
            if self.link.pause_soft:
                await asyncio.sleep(0.5)  # runs on the shared virtual loop
        elif isinstance(sock, _FakeSocket):
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
            self.link.ptt_writes.append(self.link.ptt)
            reply = build_civ_frame(CONTROLLER_ADDR, frame.to_addr, 0xFB)
        else:
            reply = build_civ_frame(
                CONTROLLER_ADDR, frame.to_addr, 0x1C, 0x00, bytes([self.link.ptt])
            )
        if not self.link.control.civ_dead:
            self.queue_response(wrap_civ_in_udp(reply))


@pytest.fixture(name="link")
async def link_fixture(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    request: pytest.FixtureRequest,
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
        monkeypatch.setattr(module, "asyncio", asyncio)  # schedule on the virtual loop
    monkeypatch.setattr(
        _control_phase,
        "_socket",
        SimpleNamespace(
            AF_INET=2, SOCK_DGRAM=2, socket=lambda *_: _FakeSocket(("192.0.2.2", 50004))
        ),
    )
    seed_data = getattr(request, "param", True)
    monkeypatch.setenv("RIGPLANE_MANAGED_TX", "1" if seed_data else "0")
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

    def track_exit(task: asyncio.Task[None]) -> None:
        result.recovery_exits.append(result.recovery_state())

    run_recover = radio._session_lifecycle._run_recover
    watchdog_recover = radio._civ_runtime._watchdog_recover

    async def tracked_lifecycle() -> None:
        task = asyncio.current_task()
        assert task is not None
        task.add_done_callback(track_exit)
        await run_recover()

    async def tracked_watchdog(
        *,
        full_reconnect_reason: str | None = None,
        preempt: asyncio.Task[None] | None = None,
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        task.add_done_callback(track_exit)
        await watchdog_recover(
            full_reconnect_reason=full_reconnect_reason, preempt=preempt
        )

    monkeypatch.setattr(radio._session_lifecycle, "_run_recover", tracked_lifecycle)
    monkeypatch.setattr(radio._civ_runtime, "_watchdog_recover", tracked_watchdog)
    try:
        await result.complete(radio._session_lifecycle.connect())
        result.control.logins.clear()
        if seed_data:
            result.ports[0].payload()
            await clock.elapse(0)
            assert radio.radio_ready
        yield result
    finally:
        await result.complete(radio.disconnect(), budget=4)
        assert result.recovery_state() == (False, False, False)
