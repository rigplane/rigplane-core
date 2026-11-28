"""MOR-4930 with the real CI-V detector and receive pump, without UDP sockets."""

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
from rigplane.core.exceptions import DiscoveryTimeout
from rigplane.core.transport import ConnectionState, IcomTransport, _UdpProtocol
from rigplane.core.tx_safety import RadioTx, TxOutcome, TxOwner, TxSource
from rigplane.runtime import (
    _civ_rx,
    _control_phase,
    managed_radio_runtime,
    radio as radio_module,
    radio_reconnect,
    session_lifecycle,
)
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleState
from rigplane.testing.timing import VirtualClock

pytestmark = pytest.mark.timeout(10)


class ControlEndpoint(asyncio.DatagramTransport):
    def __init__(self, protocol: _UdpProtocol) -> None:
        super().__init__()
        self.protocol = protocol
        self.closed = False
        self.packets: list[bytes] = []

    def sendto(self, data: bytes, addr: object = None) -> None:
        self.packets.append(data)

    def close(self) -> None:
        asyncio.get_running_loop().call_later(0, self._finish_close)

    def _finish_close(self) -> None:
        self.closed = True
        self.protocol.connection_lost(None)


class Control(IcomTransport):
    """Mock endpoint and radio replies, with production discovery and close."""

    def __init__(self) -> None:
        super().__init__()
        self.endpoints: list[ControlEndpoint] = []
        self.logins: list[asyncio.Task[None] | None] = []
        self.down = False
        self.silent = False

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
    soft_discoveries: list[tuple[float, asyncio.Task[object] | None]] = field(
        default_factory=list
    )
    ptt_writes: list[tuple[float, bool]] = field(default_factory=list)
    ptt: bool = False
    stale: bool = False
    pause_soft: bool = False

    async def complete(self, operation, *, budget: float = 2.0):
        task = asyncio.ensure_future(operation)
        await self.clock.elapse(budget)
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            pytest.fail(f"{operation!r} exceeded {budget}s")
        return task.result()

    def assert_recovery_exit(self, *, stopped: bool = False) -> None:
        radio = self.radio
        tasks = (
            radio._civ_runtime._reconnect_task,
            radio._session_lifecycle._recover_task,
            radio._civ_data_watchdog_task,
        )
        covered = any(task is not None and not task.done() for task in tasks)
        if stopped:
            assert not covered
        if stopped or not covered or radio.radio_ready:
            assert not radio._civ_recovering
        if not stopped:
            assert covered, "recovery exited with neither a live owner nor a watchdog"


class Civ(ConnectMockTransport):
    def __init__(self, link: Link) -> None:
        super().__init__()
        self.link = link
        self.rx_packet_count = 0
        self._udp_transport: object | None = None
        self.opens = 0

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
            self.link.soft_discoveries.append(
                (self.link.clock.now, asyncio.current_task())
            )
            if self.link.pause_soft:
                await asyncio.sleep(0.5)
            if self.link.stale:
                # Same silent discovery waits as the real UDP transport, with
                # only the endpoint and packet send replaced.
                transport = IcomTransport()
                transport._raw_send = lambda _: self.link.soft_discoveries.append(
                    (self.link.clock.now, asyncio.current_task())
                )
                await transport._discover()
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
        if len(data) == 0x16 and data[-1] == 4:
            self.opens += 1
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
        monkeypatch.setattr(module, "asyncio", asyncio)
    monkeypatch.setattr(
        _control_phase,
        "_socket",
        SimpleNamespace(
            AF_INET=2, SOCK_DGRAM=2, socket=lambda *_: _FakeSocket(("192.0.2.2", 50004))
        ),
    )
    monkeypatch.setenv("RIGPLANE_MANAGED_TX", "1")
    radio = CoreRadio(
        "192.0.2.1", model="IC-7610", auto_reconnect=getattr(request, "param", False)
    )
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
        result.assert_recovery_exit(stopped=True)


async def test_ping_only_after_stall_cannot_bypass_deadline(link: Link) -> None:
    original = link.ports[-1]
    # Count 69 seconds from the last payload, not from fixture time.
    start = link.clock.now
    await link.clock.elapse(2.71)
    assert link.radio._civ_recovering
    assert original.opens >= 1
    assert original.opens == 2  # initial OpenClose plus the first nudge
    deadline = start + 69.01
    while link.clock.now < deadline:
        original.rx_packet_count += 1
        await link.clock.elapse(min(0.5, deadline - link.clock.now))
    assert len(link.ports) > 1, "69s after the last payload, zero recoveries"
    link.ports[-1].payload()
    await link.clock.elapse(0)
    link.assert_recovery_exit()
    assert link.radio.radio_ready


@pytest.mark.parametrize("join", ["watchdog", "pump"])
async def test_session_free_during_join_restores_readiness(
    link: Link, monkeypatch: pytest.MonkeyPatch, join: str
) -> None:
    radio = link.radio
    rt = radio._civ_runtime
    task = radio._civ_data_watchdog_task if join == "watchdog" else radio._civ_rx_task
    assert task is not None
    method = "stop_data_watchdog" if join == "watchdog" else "stop_pump"
    stop = getattr(rt, method)
    noticed = asyncio.Event()

    def notice_at_join() -> None:
        assert task.cancelling()
        assert link.control._udp_transport is not None
        link.control.notice()
        noticed.set()

    async def stop_at_join() -> None:
        if not noticed.is_set() and task in (
            radio._civ_data_watchdog_task,
            radio._civ_rx_task,
        ):
            asyncio.get_running_loop().call_soon(notice_at_join)
        await stop()

    monkeypatch.setattr(rt, method, stop_at_join)
    await link.clock.elapse(5.1)
    soft = asyncio.create_task(radio.soft_reconnect())
    try:
        await link.clock.elapse(4)
        assert noticed.is_set()
        assert len(link.control.logins) == 1
        link.ports[-1].payload()
        await link.clock.elapse(0)
        link.assert_recovery_exit()
        assert radio.radio_ready
    finally:
        if not soft.done():
            soft.cancel()
        await asyncio.gather(soft, return_exceptions=True)


async def test_reboot_hold_writes_ptt_off_within_14_seconds_of_reboot(
    link: Link,
) -> None:
    radio = link.radio
    await link.complete(radio.rearm_managed_tx())
    tx = radio._managed_tx_runtime
    assert tx is not None
    outcome = await link.complete(
        tx.request_on(TxOwner(TxSource.WEBSOCKET, "ws-reboot-hold"))
    )
    assert outcome.outcome is TxOutcome.ACCEPTED
    assert link.ptt
    # The stale session remains discoverable only after a new control login.
    # The notice precedes the control outage and arrives while SOFT owns repair.
    link.stale = True
    await link.clock.elapse(5.1)
    repair = asyncio.create_task(radio.soft_reconnect())
    try:
        await link.clock.elapse(0.2)
        assert link.soft_discoveries
        owner = link.soft_discoveries[0][1]
        assert owner is radio._session_lifecycle._recover_task
        link.control.notice()
        link.control.down = True
        await link.clock.elapse(1.5)
        assert not repair.done() and not link.control.logins
        assert radio._session_lifecycle._recover_task is owner
        link.control.down = False
        answers_at = link.clock.now
        await link.clock.elapse(14.0)
        assert link.control.logins == [owner]
        assert repair.done() and not repair.cancelled()
        repair.result()
        assert radio._managed_tx_runtime is tx
        off_writes = [t for t, on in link.ptt_writes if not on and t >= answers_at]
        assert off_writes and off_writes[0] - answers_at <= 14.0
        await link.clock.elapse(2)
        assert tx.tx_snapshot.radio_tx is RadioTx.OFF
        link.assert_recovery_exit()
    finally:
        if not repair.done():
            repair.cancel()
        await asyncio.gather(repair, return_exceptions=True)


async def test_recipient_zero_during_full_tail_rechecks_readiness(
    link: Link,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scope_ready() -> None:
        if len(link.control.logins) == 1:
            link.radio._civ_stream_ready = False
            link.control.notice(recipient=0)
        else:
            link.ports[-1].payload()

    monkeypatch.setattr(link.radio, "_scope_session_ready", scope_ready)
    link.radio._civ_stream_ready = False
    link.control.notice()
    await link.clock.elapse(4)
    assert len(link.control.logins) == 2
    assert link.control.logins[0] is link.control.logins[1]
    link.assert_recovery_exit()


async def hold_tx(link: Link, owner: str) -> None:
    await link.complete(link.radio.rearm_managed_tx())
    tx = link.radio._managed_tx_runtime
    assert tx is not None
    outcome = await link.complete(tx.request_on(TxOwner(TxSource.WEBSOCKET, owner)))
    assert outcome.outcome is TxOutcome.ACCEPTED and link.ptt
    assert tx.tx_snapshot.lease_id is not None


async def test_recipient_zero_while_ready_recovers_ping_only_link(link: Link) -> None:
    await hold_tx(link, "recipient-zero")
    original = link.ports[-1]
    original.payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready
    link.control.notice(recipient=0)
    for _ in range(28):
        original.rx_packet_count += 1
        await link.clock.elapse(0.5)
    assert len(link.control.logins) == 1
    assert link.ptt_writes[-1][1] is False
    link.ports[-1].payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready
    link.assert_recovery_exit()


async def test_current_payload_clears_recipient_zero_notice(link: Link) -> None:
    assert link.radio.radio_ready
    link.control.notice(recipient=0)
    link.ports[-1].payload()
    await link.clock.elapse(0)
    for _ in range(30):
        link.ports[-1].rx_packet_count += 1
        await link.clock.elapse(0.5)
    assert not link.control.logins
    link.assert_recovery_exit()


@pytest.mark.parametrize("cancel", ["waiter", "owner"])
async def test_full_recovery_survives_cancellation(link: Link, cancel: str) -> None:
    await hold_tx(link, "cancelled-waiter")
    lifecycle = link.radio._session_lifecycle
    link.control.silent = True
    link.control.notice()
    await link.clock.elapse(0.5)
    owner = lifecycle._recover_task
    assert owner is not None and not owner.done()
    assert len(link.control.endpoints) == 2 and not link.control.logins
    waiter = asyncio.create_task(link.radio.soft_reconnect())
    await link.clock.elapse(0)
    assert lifecycle._recovery_waiters[waiter] is owner
    (waiter if cancel == "waiter" else owner).cancel()
    await link.clock.elapse(0)
    assert waiter.cancelled()
    assert owner.cancelled() is (cancel == "owner")
    link.assert_recovery_exit()
    link.control.silent = False
    await link.clock.elapse(15)
    link.assert_recovery_exit()
    assert len(link.control.logins) == 1
    assert link.ptt_writes[-1][1] is False
    link.ports[-1].payload()
    await link.clock.elapse(0)
    assert link.radio.radio_ready


@pytest.mark.parametrize("link", [True, False], indirect=True, ids=["auto", "manual"])
async def test_unreachable_radio_stays_recovering_until_disconnect(link: Link) -> None:
    radio = link.radio
    lifecycle = radio._session_lifecycle
    link.stale = True
    link.control.silent = True
    await link.clock.elapse(120)
    assert lifecycle.state is LifecycleState.RECOVERING
    assert not radio.connected and not radio.radio_ready
    assert len(link.control.endpoints) > 2 and not link.control.logins
    owner = lifecycle._recover_task
    if owner is None or owner.done():
        await link.clock.elapse(2)
        owner = lifecycle._recover_task
    assert owner is not None and not owner.done()
    assert link.control._discover_ping_event is not None
    await link.complete(radio.disconnect(), budget=0.1)
    assert lifecycle.state is LifecycleState.DISCONNECTED
    assert all(endpoint.closed for endpoint in link.control.endpoints)
    attempts = len(link.control.endpoints)
    await link.clock.elapse(30)
    assert len(link.control.endpoints) == attempts
    link.assert_recovery_exit(stopped=True)


async def test_explicit_recovery_on_silent_link_raises_discovery_timeout(
    link: Link,
) -> None:
    assert not link.radio._auto_reconnect
    link.stale = True
    link.control.silent = True
    await link.clock.elapse(4)
    with pytest.raises(DiscoveryTimeout):
        await link.complete(link.radio.soft_reconnect(), budget=45)
    link.assert_recovery_exit()


@pytest.mark.parametrize("stop", ["disconnect", "shutdown"])
@pytest.mark.parametrize(
    "outcome", ["success", "error", "pump-error", "watchdog-error"]
)
async def test_owner_stop_releases_tx_debt_and_closes_transports(
    link: Link, monkeypatch: pytest.MonkeyPatch, stop: str, outcome: str
) -> None:
    radio, lifecycle = link.radio, link.radio._session_lifecycle
    await hold_tx(link, stop)
    tx = radio._managed_tx_runtime
    assert tx is not None
    lease = tx.tx_snapshot.lease_id
    release = AsyncMock(wraps=tx.shutdown)
    monkeypatch.setattr(tx, "shutdown", release)
    lifecycle._max_recovery_attempts = 1
    link.control.down = outcome == "error"
    failed_task = None
    if outcome in ("pump-error", "watchdog-error"):
        rt = radio._civ_runtime
        if outcome == "pump-error":
            loop_name = "_civ_rx_loop"
            stop_child, start_child = rt.stop_pump, rt.start_pump
            task_attr = "_civ_rx_task"
        else:
            loop_name = "_civ_data_watchdog_loop"
            stop_child, start_child = rt.stop_data_watchdog, rt.start_data_watchdog
            task_attr = "_civ_data_watchdog_task"
        child_loop = getattr(rt, loop_name)

        async def raise_on_cancel(**kwargs: int) -> None:
            await child_loop(**kwargs)
            raise OSError("child failed during cancellation")

        await stop_child()
        monkeypatch.setattr(rt, loop_name, raise_on_cancel)
        start_child()
        failed_task = getattr(radio, task_attr)
        assert failed_task is not None
    if outcome == "success":
        link.pause_soft = True
        await link.clock.elapse(5.1)
        repair = asyncio.create_task(radio.soft_reconnect())
        await link.clock.elapse(0.25)
    else:
        repair = None
    try:
        if failed_task is None:
            radio._civ_stream_ready = False
            link.control.notice()
            await link.clock.elapse(3)
        release.assert_not_awaited()
        assert radio._managed_tx_runtime is tx
        if outcome == "success":
            assert link.control.logins == [link.soft_discoveries[0][1]]
            assert repair is not None and repair.done()
            repair.result()
            assert any(not on for _, on in link.ptt_writes)
        else:
            assert link.ptt
            assert tx.tx_snapshot.lease_id == lease or tx.tx_snapshot.release_reason
        link.assert_recovery_exit()
        operation = (
            radio.disconnect if stop == "disconnect" else lifecycle.request_shutdown
        )
        await link.complete(operation(), budget=4)
        release.assert_awaited_once()
        if failed_task is not None:
            assert failed_task.cancelling()
            assert isinstance(failed_task.exception(), OSError)
            assert link.ptt_writes[-1][1] is False
        assert radio.managed_tx is None
        assert radio._civ_transport is None
        assert all(port._udp_transport is None for port in link.ports)
        assert link.control._udp_transport is None
        assert all(endpoint.closed for endpoint in link.control.endpoints)
        assert lifecycle.state is LifecycleState.DISCONNECTED
        link.assert_recovery_exit(stopped=True)
    finally:
        if repair is not None:
            if not repair.done():
                repair.cancel()
            await asyncio.gather(repair, return_exceptions=True)
