from __future__ import annotations

import asyncio
import errno
import struct
from unittest.mock import AsyncMock

import pytest

from rigplane.core.exceptions import ConnectionError
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleState


class _PortPeer:
    def __init__(self, collisions: int) -> None:
        self.collisions = collisions
        self.advertised_port: int | None = None
        self.binds: list[int] = []
        self.events: list[str] = []

    def transport(self) -> _Transport:
        return _Transport(self)


class _Transport:
    def __init__(self, peer: _PortPeer) -> None:
        self.peer = peer
        self.my_id = 1
        self.remote_id = 2
        self.local_port: int | None = None
        self._udp_transport: object | None = object()

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
    ) -> None:
        self.peer.binds.append(local_port)
        if local_port == self.peer.advertised_port and self.peer.collisions:
            self.peer.collisions -= 1
            self._udp_transport = None
            raise OSError(errno.EADDRINUSE, "Address already in use")
        self.local_port = local_port or 53003

    async def send_tracked(self, packet: bytes) -> None:
        if len(packet) == 0x90:
            self.peer.advertised_port = struct.unpack_from(">I", packet, 0x7C)[0]
        elif len(packet) == 0x40 and packet[0x15] == 0x01:
            self.peer.events.append("token-remove")

    async def disconnect(self) -> None:
        self.peer.events.append("control-close")
        self._udp_transport = None

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    def start_idle_loop(self) -> None:
        pass


async def _prepare(monkeypatch: pytest.MonkeyPatch, peer: _PortPeer) -> CoreRadio:
    radio = CoreRadio("192.0.2.1", model="IC-7610")
    radio._ctrl_transport = peer.transport()  # type: ignore[assignment]
    radio._civ_transport = None
    radio._civ_local_port = 52002
    radio._conn_state = RadioConnectionState.RECONNECTING
    radio._token = 1
    radio._session_lifecycle._max_recovery_attempts = 2
    radio._session_lifecycle._recovery_backoff_s = (0.0,)
    await radio._control_phase._send_conninfo(None, 52002, 52003)
    monkeypatch.setattr("rigplane.transport.IcomTransport", peer.transport)
    monkeypatch.setattr(radio._civ_runtime, "start_pump", lambda: None)
    monkeypatch.setattr(radio._civ_runtime, "start_worker", lambda: None)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    monkeypatch.setattr(radio._control_phase, "_after_reconnect", AsyncMock())
    return radio


@pytest.mark.timeout(10)
async def test_transient_collision_retries_the_advertised_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _PortPeer(collisions=1)
    radio = await _prepare(monkeypatch, peer)
    try:
        await radio.soft_reconnect()
        assert peer.advertised_port == 52002
        assert peer.binds == [52002, 52002]
        assert radio._civ_transport.local_port == peer.advertised_port
        assert radio._conn_state is RadioConnectionState.CONNECTED
        assert radio._session_lifecycle.state is LifecycleState.CONNECTED
        radio._control_phase._after_reconnect.assert_awaited_once()
    finally:
        await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_persistent_collision_exhausts_and_releases_without_port_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _PortPeer(collisions=2)
    radio = await _prepare(monkeypatch, peer)
    try:
        with pytest.raises(ConnectionError, match="Soft reconnect exhausted"):
            await radio.soft_reconnect()
        assert peer.binds == [peer.advertised_port, peer.advertised_port]
        assert peer.events == ["token-remove", "control-close"]
        assert radio._session_lifecycle.state is LifecycleState.DISCONNECTED
        assert radio._civ_transport is None
        radio._control_phase._after_reconnect.assert_not_awaited()
    finally:
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_registered_watchdog_port_collision_completes_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    peer = _PortPeer(collisions=2)
    radio = await _prepare(monkeypatch, peer)
    task = asyncio.create_task(radio._civ_runtime._watchdog_recover())
    radio._civ_runtime._reconnect_task = task
    await task
    assert peer.binds == [peer.advertised_port, peer.advertised_port]
    assert peer.events == ["token-remove", "control-close"]
    assert radio._session_lifecycle.state is LifecycleState.DISCONNECTED
    assert task.done() and not task.cancelled()
    assert radio._civ_runtime._reconnect_task is None
    assert radio._session_lifecycle._recover_task is None
    radio._control_phase._after_reconnect.assert_not_awaited()
