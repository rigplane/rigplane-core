"""No unretrieved task exceptions on WebRTC close (MOR-4671).

Closing a session while its ICE gather is still running used to orphan
background work: aiortc's fire-and-forget ``__connect`` failed with
``InvalidStateError`` unretrieved (Sentry PRO-5), aioice's detached
``server_reflexive_candidate`` / ``relayed_candidate`` grandchildren died
with ``TransactionTimeout`` unretrieved (PRO-8), and STUN retry timers
firing on closed transports raised ``AttributeError: 'NoneType' ...
'call_exception_handler'`` (PRO-9). An ordinary close after the gather
can also race a pending connectivity check.

These tests drive the real answerer (``WebRtcSessionManager``) against
loopback blackholes during ICE gathering and during connectivity checks.
They fail on any ``never retrieved`` / ``Exception in callback`` context
the loop reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Final

import pytest

pytest.importorskip("aioice", reason="needs the [webrtc] extra")
pytest.importorskip("aiortc", reason="needs the [webrtc] extra")

from rigplane.testing.timing import VirtualClock  # noqa: E402
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
        self.contexts: list[dict[str, Any]] = []

    def __call__(
        self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        message = context.get("message", "")
        if "never retrieved" in message or "Exception in callback" in message:
            self.contexts.append(context)

    def describe(self) -> list[str]:
        out = []
        for context in self.contexts:
            out.append(f"{context.get('message')!r} exc={context.get('exception')!r}")
        return out


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
    blackhole: Callable[[], Awaitable[tuple[_Blackhole, int]]],
    capture: _Capture,
) -> None:
    """After gathering, close while the answerer's first ICE check is pending."""
    from aioice import ice
    from aiortc.sdp import SessionDescription

    manager = WebRtcSessionManager(None, None, "mor4671")
    monkeypatch.setattr(
        ice, "get_host_addresses", lambda use_ipv4, use_ipv6: ["127.0.0.1"]
    )
    request = ice.StunProtocol.request
    close = ice.StunProtocol.close
    checks: dict[Any, asyncio.Task[Any]] = {}
    settled_at_close: list[bool] = []

    async def observe_request(protocol: Any, *args: Any, **kwargs: Any) -> Any:
        task = asyncio.current_task()
        assert task is not None
        checks[protocol] = task
        return await request(protocol, *args, **kwargs)

    async def observe_close(protocol: Any) -> None:
        if protocol in checks:
            settled_at_close.append(checks[protocol].done())
        await close(protocol)

    monkeypatch.setattr(ice.StunProtocol, "request", observe_request)
    monkeypatch.setattr(ice.StunProtocol, "close", observe_close)
    receiver, port = await blackhole()
    offerer, sdp, typ = await _offer()
    try:
        offer = SessionDescription.parse(sdp)
        (candidate,) = offer.media[0].ice_candidates
        candidate.port = port
        answer = WebRtcAnswer.from_json(await manager.negotiate(str(offer), typ))
        await receiver.received.wait()
        await manager.close_session(answer.session_id)
        # A delayed event-loop turn can make both the check driver and its
        # retry due together after the socket has closed.
        virtual_clock.advance(1)
        await virtual_clock.elapse(0)
        gc.collect()
        await virtual_clock.elapse(0)
        assert settled_at_close == [True]
        assert capture.describe() == []
    finally:
        await manager.close_all()
        await offerer.close()


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
        await stun.received.wait()
        await turn.received.wait()
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
        gc.collect()
        await virtual_clock.elapse(0)
        assert capture.describe() == []
    finally:
        negotiate.cancel()
        await asyncio.gather(negotiate, return_exceptions=True)
        cleanup = asyncio.create_task(manager.close_all())
        await virtual_clock.elapse(GATHER_TIMEOUT_S)
        await cleanup
        await offerer.close()
