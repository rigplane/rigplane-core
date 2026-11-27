"""No unretrieved task exceptions on WebRTC close (MOR-4671).

Closing a session while its ICE gather is still running used to orphan
background work: aiortc's fire-and-forget ``__connect`` failed with
``InvalidStateError`` unretrieved (Sentry PRO-5), aioice's detached
``server_reflexive_candidate`` / ``relayed_candidate`` grandchildren died
with ``TransactionTimeout`` unretrieved (PRO-8), and STUN retry timers
firing on closed transports raised ``AttributeError: 'NoneType' ...
'call_exception_handler'`` (PRO-9). An ordinary close after the gather
can also race a pending connectivity check.

These tests drive the real answerer (``WebRtcSessionManager``) while ICE
gathering or connectivity checks are in flight. They fail on unretrieved
exceptions and callback errors reported by the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final
from unittest.mock import Mock

import pytest

pytest.importorskip("aioice", reason="needs the [webrtc] extra")
pytest.importorskip("aiortc", reason="needs the [webrtc] extra")

from rigplane.testing.timing import VirtualClock, await_until  # noqa: E402
from rigplane.web.transport.ice_gathering import (  # noqa: E402
    GATHER_TIMEOUT_S,
    IceServer,
)
from rigplane.web.transport.webrtc_contract import WebRtcAnswer  # noqa: E402
from rigplane.web.transport.webrtc_session import (  # noqa: E402
    WebRtcSessionManager,
)

# STUN magic cookie (RFC 5389): bytes 4..8 of every STUN-framed message,
# including the TURN Allocate the gather sends to a turn: server.
_STUN_MAGIC: Final = bytes((0x21, 0x12, 0xA4, 0x42))


class _Blackhole(asyncio.DatagramProtocol):
    """UDP double that receives everything and answers nothing.

    ``received`` fires on the first STUN-framed datagram, so a test can
    wait until the gather provably has STUN/TURN work in flight instead
    of guessing with a sleep.
    """

    def __init__(self) -> None:
        self.received = asyncio.Event()

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if len(data) >= 20 and bytes(data[4:8]) == _STUN_MAGIC:
            self.received.set()


class _Capture:
    """Loop exception handler recording unretrieved-exception noise."""

    def __init__(self) -> None:
        self.errors: list[str] = []

    def __call__(
        self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        message = context.get("message", "")
        if "never retrieved" in message or "Exception in callback" in message:
            self.errors.append(f"{message!r} exc={context.get('exception')!r}")

    async def assert_quiet(self, clock: VirtualClock) -> None:
        await clock.elapse(0)
        gc.collect()
        await clock.elapse(0)
        assert self.errors == []


@pytest.fixture
async def blackhole() -> AsyncIterator[Callable[[], Awaitable[tuple[_Blackhole, int]]]]:
    loop = asyncio.get_running_loop()
    with contextlib.ExitStack() as endpoints:

        async def open_endpoint() -> tuple[_Blackhole, int]:
            protocol = _Blackhole()
            transport, _ = await loop.create_datagram_endpoint(
                lambda: protocol, local_addr=("127.0.0.1", 0)
            )
            endpoints.callback(transport.close)
            return protocol, transport.get_extra_info("sockname")[1]

        yield open_endpoint


@pytest.fixture
async def capture() -> AsyncIterator[_Capture]:
    loop = asyncio.get_running_loop()
    capture = _Capture()
    previous = loop.get_exception_handler()
    loop.set_exception_handler(capture)
    try:
        yield capture
    finally:
        loop.set_exception_handler(previous)


async def _offer() -> tuple[Any, str, str]:
    from aiortc import RTCConfiguration, RTCPeerConnection

    pc = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    pc.createDataChannel("control")
    offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    desc = pc.localDescription
    assert desc is not None
    return pc, desc.sdp, desc.type


def _ice_servers(stun_port: int, turn_port: int) -> list[IceServer]:
    return [
        IceServer(urls=[f"stun:127.0.0.1:{stun_port}"]),
        IceServer(
            urls=[f"turn:127.0.0.1:{turn_port}"],
            username="u",
            credential="p",
        ),
    ]


@pytest.mark.timeout(15)
async def test_close_after_negotiate_leaves_no_unretrieved_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    capture: _Capture,
) -> None:
    """After gathering, close while the answerer's first ICE check is pending."""
    from aioice import ice, stun

    loop = asyncio.get_running_loop()
    manager = WebRtcSessionManager(None, None, "mor4671")
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["127.0.0.1"]
    )
    check_sent = asyncio.Event()
    pending_at_close: list[bool] = []
    ports = iter((40001, 40002))

    async def open_endpoint(
        protocol_factory: Callable[[], ice.StunProtocol], *, local_addr: tuple[str, int]
    ) -> tuple[asyncio.DatagramTransport, ice.StunProtocol]:
        # Keep the real ICE/STUN task and retry timer and replace only UDP I/O.
        protocol = protocol_factory()
        transport = Mock(spec=asyncio.DatagramTransport)
        address = (local_addr[0], next(ports))
        transport.get_extra_info.side_effect = {"sockname": address}.get
        sent = False
        closed = False

        def sendto(_data: bytes, _addr: tuple[str, int]) -> None:
            nonlocal sent
            assert not closed, "STUN retry sent on a closed transport"
            sent = True
            check_sent.set()

        def close() -> None:
            nonlocal closed
            closed = True
            if sent:
                pending_at_close.append(bool(protocol.transactions))
            loop.call_soon(protocol.connection_lost, None)

        transport.sendto.side_effect = sendto
        transport.close.side_effect = close
        protocol.connection_made(transport)
        return transport, protocol

    monkeypatch.setattr(loop, "create_datagram_endpoint", open_endpoint)
    offerer, sdp, typ = await _offer()
    try:
        answer = WebRtcAnswer.from_json(await manager.negotiate(sdp, typ))
        # The real transaction has sent once and cannot finish on frozen time.
        await await_until(check_sent.is_set, description="the first ICE check")
        await manager.close_session(answer.session_id)
    finally:
        await manager.close_all()
        await offerer.close()
    del offerer
    assert pending_at_close == [False]
    # Make the ICE driver's next tick and the first STUN retry due together.
    virtual_clock.advance(stun.RETRY_RTO)
    await capture.assert_quiet(virtual_clock)


@pytest.mark.timeout(15)
async def test_close_retrieves_gather_failure_before_snapshot(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock, capture: _Capture
) -> None:
    """A failed gather can leave the live set while close awaits a handler."""
    from aioice import ice
    from aiortc import RTCConfiguration, RTCPeerConnection

    loop = asyncio.get_running_loop()
    gathering = asyncio.Event()
    fail_gather = asyncio.Event()
    finish_handler = asyncio.Event()

    async def fail_endpoint(*args: Any, **kwargs: Any) -> Any:
        gathering.set()
        await fail_gather.wait()
        raise RuntimeError("injected ICE gather failure")

    async def handler() -> None:
        try:
            await asyncio.Future()
        finally:
            await finish_handler.wait()

    manager = WebRtcSessionManager(None, None, "mor4671")
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["127.0.0.1"]
    )
    monkeypatch.setattr(loop, "create_datagram_endpoint", fail_endpoint)
    offerer = RTCPeerConnection(RTCConfiguration(iceServers=[]))
    offerer.createDataChannel("control")
    offer = await offerer.createOffer()
    negotiate = asyncio.create_task(manager.negotiate(offer.sdp, offer.type))
    close: asyncio.Task[list[str]] | None = None
    try:
        await await_until(gathering.is_set, description="ICE gather endpoint creation")
        (session_id,) = manager.active_session_ids
        session = manager._sessions[session_id]  # noqa: SLF001 - observe gather ownership
        assert len(session.gather_inflight) == 1
        session.tasks.add(asyncio.create_task(handler()))
        await virtual_clock.elapse(0)
        close = asyncio.create_task(manager.close_all())
        with pytest.raises(asyncio.CancelledError):
            await negotiate
        # Drain the shield cancellation before letting the inner task fail.
        await virtual_clock.elapse(0)
        fail_gather.set()
        await virtual_clock.elapse(0)
        assert not session.gather_inflight
        assert not close.done()
    finally:
        fail_gather.set()
        finish_handler.set()
        if close is not None:
            await close
        await manager.close_all()
        await offerer.close()
    assert close.result() == [session_id]
    await capture.assert_quiet(virtual_clock)


@pytest.mark.timeout(15)
@pytest.mark.parametrize("enumeration_fails", [False, True], ids=["normal", "fallback"])
@pytest.mark.parametrize("cancel_close", [False, True], ids=["close", "cancel-close"])
async def test_close_during_gather_leaves_no_unretrieved_exceptions(
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
    blackhole: Callable[[], Awaitable[tuple[_Blackhole, int]]],
    capture: _Capture,
    enumeration_fails: bool,
    cancel_close: bool,
) -> None:
    """Close must cancel negotiation and settle its STUN/TURN work before teardown."""
    from aioice import ice

    from rigplane.web.transport import ice_gathering

    def no_adapters() -> list[Any]:
        raise OSError("no adapters")

    manager = WebRtcSessionManager(None, None, "mor4671")
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["127.0.0.1"]
    )
    if enumeration_fails:
        monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", no_adapters)
    stun, stun_port = await blackhole()
    turn, turn_port = await blackhole()
    offerer, sdp, typ = await _offer()
    negotiate = asyncio.create_task(
        manager.negotiate(sdp, typ, ice_servers=_ice_servers(stun_port, turn_port))
    )
    try:
        await await_until(stun.received.is_set, description="the STUN datagram")
        await await_until(turn.received.is_set, description="the TURN datagram")
        assert not negotiate.done()
        (session_id,) = manager.active_session_ids
        pc = manager._sessions[session_id].pc  # noqa: SLF001 - observe transport lifetime
        close = asyncio.create_task(manager.close_all())
        await virtual_clock.elapse(0)
        gathering_at_close = not close.done() and pc.connectionState != "closed"
        if cancel_close:
            close.cancel()
            await virtual_clock.elapse(0)
            gathering_at_close = gathering_at_close and not close.done()
        await virtual_clock.elapse(GATHER_TIMEOUT_S)
        if cancel_close:
            with pytest.raises(asyncio.CancelledError):
                await close
        else:
            assert await close == [session_id]
        assert gathering_at_close
        with pytest.raises(asyncio.CancelledError):
            await negotiate
        assert pc.connectionState == "closed"
        assert manager.active_session_ids == []
    finally:
        negotiate.cancel()
        await asyncio.gather(negotiate, return_exceptions=True)
        cleanup = asyncio.create_task(manager.close_all())
        await virtual_clock.elapse(GATHER_TIMEOUT_S)
        await cleanup
        await offerer.close()
    await capture.assert_quiet(virtual_clock)
