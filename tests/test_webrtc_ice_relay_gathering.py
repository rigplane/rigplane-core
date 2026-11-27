"""Bounded one-shot gathering keeps TURN results and settles server tasks."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import AsyncIterator, Callable
from typing import Any
from weakref import WeakSet

import pytest

ice = pytest.importorskip("aioice.ice", reason="needs the [webrtc] extra")

from rigplane.testing.timing import VirtualClock  # noqa: E402
from rigplane.web.transport import ice_gathering  # noqa: E402


class _Transport(asyncio.DatagramTransport):
    def __init__(self, protocol: Any, address: tuple[str, int]) -> None:
        self.protocol = protocol
        self.address = address

    def get_extra_info(self, name: str, default: Any = None) -> Any:
        return self.address if name == "sockname" else default

    def close(self) -> None:
        self.protocol.connection_lost(None)


class _Servers:
    def __init__(self) -> None:
        self.relay_delay = 0.2
        self.stun_delays = {"192.0.2.1": 10.0, "192.0.2.2": 10.0}
        self.stun_sources: list[str] = []
        self.cancelled: list[str] = []
        self.fail_cleanup = False
        self.fail_turn = False
        self.hide_related_address: bool | None = None
        self.tasks: WeakSet[asyncio.Task[Any]] = WeakSet()

    async def stun(self, protocol: Any, server: Any) -> tuple[Any, None]:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        host = protocol.local_candidate.host
        self.stun_sources.append(host)
        try:
            await asyncio.sleep(self.stun_delays[host])
        except asyncio.CancelledError:
            self.cancelled.append(host)
            if self.fail_cleanup:
                raise RuntimeError("STUN cleanup failure") from None
            raise
        candidate = ice.Candidate(
            foundation="srflx",
            component=protocol.local_candidate.component,
            transport="udp",
            priority=1,
            host="198.51.100.1",
            port=40000,
            type="srflx",
        )
        return candidate, None

    async def turn(self, **kwargs: Any) -> tuple[Any, Any]:
        task = asyncio.current_task()
        assert task is not None
        self.tasks.add(task)
        await asyncio.sleep(self.relay_delay)
        if self.fail_turn:
            raise RuntimeError("TURN allocation failure")
        self.hide_related_address = kwargs["hide_related_address"]
        candidate = ice.Candidate(
            foundation="relay",
            component=kwargs["component"],
            transport="udp",
            priority=1,
            host="203.0.113.1",
            port=50000,
            type="relay",
        )
        protocol = kwargs["protocol_factory"]()
        protocol.local_candidate = candidate
        protocol.connection_made(_Transport(protocol, (candidate.host, candidate.port)))
        return candidate, protocol


@pytest.fixture
async def servers(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Servers]:
    ice_gathering.install_ice_gathering_policy()
    servers = _Servers()
    monkeypatch.setattr(ice, "server_reflexive_candidate", servers.stun)
    monkeypatch.setattr(ice, "relayed_candidate", servers.turn)
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["192.0.2.1", "192.0.2.2"]
    )
    monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", lambda: [])

    async def bind(factory: Callable[[], Any], *, local_addr: tuple[str, int]) -> Any:
        protocol = factory()
        transport = _Transport(protocol, (local_addr[0], 30000))
        protocol.connection_made(transport)
        return transport, protocol

    monkeypatch.setattr(asyncio.get_running_loop(), "create_datagram_endpoint", bind)
    try:
        yield servers
    finally:
        tasks = list(servers.tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _connection(controlling: bool, **kwargs: Any) -> Any:
    return ice.Connection(
        ice_controlling=controlling,
        stun_server=("stun.example", 3478),
        turn_server=("turn.example", 3478),
        turn_username="user",
        turn_password="pass",
        **kwargs,
    )


@pytest.mark.parametrize("controlling", [True, False], ids=["offerer", "answerer"])
async def test_relay_finishes_gather_before_pending_stun(
    servers: _Servers,
    virtual_clock: VirtualClock,
    controlling: bool,
) -> None:
    connection = _connection(controlling)
    with ice_gathering.apply_ice_gathering_policy():
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(servers.relay_delay)
        assert gather.done(), "a completed relay must not wait for STUN or the cap"
        await gather
        assert {c.type for c in connection.local_candidates} == {"host", "relay"}
        assert sorted(servers.cancelled) == ["192.0.2.1", "192.0.2.2"]
        relay = next(c for c in connection.local_candidates if c.type == "relay")
        assert any(p.local_candidate is relay for p in connection._protocols)
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()


async def test_cap_keeps_completed_candidates_and_retrieves_cleanup_errors(
    servers: _Servers,
    virtual_clock: VirtualClock,
) -> None:
    servers.relay_delay = ice_gathering.GATHER_TIMEOUT_S
    servers.stun_delays["192.0.2.1"] = 0.1
    servers.fail_cleanup = True
    connection = _connection(False)
    loop = asyncio.get_running_loop()
    errors: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    with ice_gathering.apply_ice_gathering_policy():
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(ice_gathering.GATHER_TIMEOUT_S)
        assert gather.done()
        await gather
        assert {c.type for c in connection.local_candidates} == {
            "host",
            "srflx",
            "relay",
        }
        assert servers.cancelled == ["192.0.2.2"]
        gc.collect()
        await virtual_clock.elapse(0)
        assert errors == []
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()
        loop.set_exception_handler(previous)


async def test_early_relay_retrieves_pending_task_cleanup_failure(
    servers: _Servers,
    virtual_clock: VirtualClock,
) -> None:
    servers.fail_cleanup = True
    connection = _connection(True)
    loop = asyncio.get_running_loop()
    errors: list[dict[str, Any]] = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda loop, context: errors.append(context))
    with ice_gathering.apply_ice_gathering_policy():
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(servers.relay_delay)
        assert gather.done()
        await gather
        assert len(servers.cancelled) == 2
        gc.collect()
        await virtual_clock.elapse(0)
        assert errors == []
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()
        loop.set_exception_handler(previous)


async def test_failed_turn_still_gathers_srflx_until_cap(
    servers: _Servers,
    virtual_clock: VirtualClock,
) -> None:
    servers.fail_turn = True
    servers.stun_delays["192.0.2.1"] = 0.4
    connection = _connection(False)
    with ice_gathering.apply_ice_gathering_policy():
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(0.4)
        assert not gather.done()
        await virtual_clock.elapse(ice_gathering.GATHER_TIMEOUT_S - 0.4)
        assert gather.done()
        await gather
        assert {c.type for c in connection.local_candidates} == {"host", "srflx"}
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()


@pytest.mark.parametrize("relay_only", [True, False])
async def test_relay_preserves_address_and_transport_policy(
    monkeypatch: pytest.MonkeyPatch,
    servers: _Servers,
    virtual_clock: VirtualClock,
    relay_only: bool,
) -> None:
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["192.0.2.2", "192.0.2.1"]
    )
    monkeypatch.setattr(
        ice_gathering,
        "_enumerate_local_addresses",
        lambda: [ice_gathering.AddressInfo("192.0.2.1", "net", is_default_route=True)],
    )
    policy = ice.TransportPolicy.RELAY if relay_only else ice.TransportPolicy.ALL
    connection = _connection(True, transport_policy=policy)
    with ice_gathering.apply_ice_gathering_policy():
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(servers.relay_delay)
        assert gather.done()
        await gather
        assert servers.hide_related_address is relay_only
        assert servers.stun_sources == ([] if relay_only else ["192.0.2.1"])
        hosts = [c.host for c in connection.local_candidates if c.type == "host"]
        assert hosts == ([] if relay_only else ["192.0.2.1", "192.0.2.2"])
        assert connection.stun_server == ("stun.example", 3478)
        assert connection.turn_server == ("turn.example", 3478)
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()


async def test_cancelled_caller_leaves_relay_owned_by_tracked_gather(
    servers: _Servers,
    virtual_clock: VirtualClock,
) -> None:
    connection = _connection(False)
    inflight: set[asyncio.Task[Any]] = set()
    with (
        ice_gathering.apply_ice_gathering_policy(),
        ice_gathering.apply_ice_gather_scope(inflight),
    ):
        gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(0)
        assert len(inflight) == 1
        gather.cancel()
        with pytest.raises(asyncio.CancelledError):
            await gather
        await virtual_clock.elapse(servers.relay_delay)
        assert not inflight
        assert any(p.local_candidate.type == "relay" for p in connection._protocols)
        assert len(servers.cancelled) == 2
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await virtual_clock.elapse(ice_gathering.GATHER_TIMEOUT_S)
        await asyncio.gather(*inflight, return_exceptions=True)
        await connection.close()


async def test_no_active_policy_keeps_aioice_wait_behavior(
    servers: _Servers,
    virtual_clock: VirtualClock,
) -> None:
    connection = _connection(True)
    gather = asyncio.create_task(connection.gather_candidates())
    try:
        await virtual_clock.elapse(servers.relay_delay)
        assert not gather.done()
        await virtual_clock.elapse(5.0)
        assert gather.done()
        await gather
        assert {c.type for c in connection.local_candidates} == {"host", "relay"}
    finally:
        gather.cancel()
        await asyncio.gather(gather, return_exceptions=True)
        await connection.close()
