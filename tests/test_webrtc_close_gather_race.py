"""No unretrieved task exceptions on WebRTC close (MOR-4671).

Closing a session while its ICE gather is still running used to orphan
background work: aiortc's fire-and-forget ``__connect`` failed with
``InvalidStateError`` unretrieved (Sentry PRO-5), aioice's detached
``server_reflexive_candidate`` / ``relayed_candidate`` grandchildren died
with ``TransactionTimeout`` unretrieved (PRO-8), and STUN retry timers
firing on closed transports raised ``AttributeError: 'NoneType' ...
'call_exception_handler'`` (PRO-9). An ordinary close after the gather
finished was always clean.

These tests drive the real answerer (``WebRtcSessionManager``) with the
real aiortc/aioice gather against loopback blackhole STUN+TURN doubles
and fail on any ``never retrieved`` / ``Exception in callback`` context
the loop reports.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
from typing import Any, Final

import pytest

pytest.importorskip("aioice", reason="needs the [webrtc] extra")
pytest.importorskip("aiortc", reason="needs the [webrtc] extra")

from rigplane.testing.timing import await_until  # noqa: E402
from rigplane.web.transport.ice_gathering import IceServer  # noqa: E402
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
        self.transport: asyncio.DatagramTransport | None = None
        self.received = asyncio.Event()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

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


async def _blackhole_port() -> tuple[_Blackhole, int]:
    loop = asyncio.get_running_loop()
    double = _Blackhole()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: double, local_addr=("127.0.0.1", 0)
    )
    assert double.transport is not None
    return double, transport.get_extra_info("sockname")[1]


async def _offer() -> tuple[Any, str, str]:
    from aiortc import RTCPeerConnection

    pc = RTCPeerConnection()
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


async def _settle() -> None:
    for _ in range(2):
        gc.collect()
        await asyncio.sleep(0.5)
        gc.collect()
        await asyncio.sleep(0)


async def _wait_for_gather(
    stun: _Blackhole, turn: _Blackhole, negotiate: asyncio.Task[Any]
) -> None:
    """Wait until both doubles provably hold STUN/TURN work in flight."""
    await asyncio.wait_for(stun.received.wait(), timeout=5)
    await asyncio.wait_for(turn.received.wait(), timeout=5)
    assert not negotiate.done()  # the gather is still running, not finished


@pytest.mark.realtime(reason="real ICE/STUN wall-clock against loopback doubles")
@pytest.mark.asyncio
async def test_close_after_negotiate_leaves_no_unretrieved_exceptions() -> None:
    """Ordinary close: negotiate (host-only, fast), then close, then quiet."""
    loop = asyncio.get_running_loop()
    capture = _Capture()
    old = loop.get_exception_handler()
    loop.set_exception_handler(capture)
    offerer, sdp, typ = await _offer()
    manager = WebRtcSessionManager(None, None, "mor4671")
    try:
        answer = WebRtcAnswer.from_json(await manager.negotiate(sdp, typ))
        await manager.close_session(answer.session_id)
        await _settle()
        assert capture.describe() == []
    finally:
        with contextlib.suppress(Exception):
            await manager.close_all()
        await offerer.close()
        loop.set_exception_handler(old)


@pytest.mark.realtime(reason="real ICE/STUN wall-clock against loopback doubles")
@pytest.mark.asyncio
async def test_close_during_gather_leaves_no_unretrieved_exceptions() -> None:
    """Close racing a still-running gather: the negotiate is cancelled and
    every gather task it started is settled before the pc closes, so the
    loop reports nothing."""
    loop = asyncio.get_running_loop()
    capture = _Capture()
    old = loop.get_exception_handler()
    loop.set_exception_handler(capture)
    stun, stun_port = await _blackhole_port()
    turn, turn_port = await _blackhole_port()
    offerer, sdp, typ = await _offer()
    manager = WebRtcSessionManager(None, None, "mor4671")
    try:
        negotiate = asyncio.ensure_future(
            manager.negotiate(sdp, typ, ice_servers=_ice_servers(stun_port, turn_port))
        )
        await _wait_for_gather(stun, turn, negotiate)
        closed = await manager.close_all()
        assert len(closed) == 1
        with pytest.raises(asyncio.CancelledError):
            await negotiate
        await _settle()
        assert capture.describe() == []
    finally:
        with contextlib.suppress(Exception):
            await manager.close_all()
        await offerer.close()
        loop.set_exception_handler(old)


@pytest.mark.realtime(reason="real ICE/STUN wall-clock against loopback doubles")
@pytest.mark.asyncio
async def test_cancelled_close_still_tears_down_peer() -> None:
    """A close cancelled mid-gather (an HTTP close raced by server shutdown,
    which removes the session from the registry and then cancels client
    tasks) still runs the settle-then-close tail in order: the negotiate
    is cancelled, the peer is torn down, and the loop reports nothing."""
    loop = asyncio.get_running_loop()
    capture = _Capture()
    old = loop.get_exception_handler()
    loop.set_exception_handler(capture)
    stun, stun_port = await _blackhole_port()
    turn, turn_port = await _blackhole_port()
    offerer, sdp, typ = await _offer()
    manager = WebRtcSessionManager(None, None, "mor4671")
    try:
        negotiate = asyncio.ensure_future(
            manager.negotiate(sdp, typ, ice_servers=_ice_servers(stun_port, turn_port))
        )
        await _wait_for_gather(stun, turn, negotiate)
        (session_id,) = manager.active_session_ids
        pc = manager._sessions[session_id].pc  # noqa: SLF001 - teardown observation
        # Parked inside the settle wait (the gather cap outlives this
        # deadline), then cancelled from the outside like a shutdown would.
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.5):
                await manager.close_all()
        with pytest.raises(asyncio.CancelledError):
            await negotiate
        assert manager.active_session_ids == []
        await await_until(
            lambda: pc.connectionState == "closed",
            timeout=5,
            description="answerer pc torn down after a cancelled close",
        )
        await _settle()
        assert capture.describe() == []
    finally:
        with contextlib.suppress(Exception):
            await manager.close_all()
        await offerer.close()
        loop.set_exception_handler(old)


@pytest.mark.realtime(reason="real ICE/STUN wall-clock against loopback doubles")
@pytest.mark.asyncio
async def test_close_during_gather_with_enumeration_failure_leaves_no_noise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same race with local address enumeration failing (e.g. a Windows
    ``GetAdaptersAddresses`` failure): the fallback component call is
    tracked and settled like the normal ones, so the loop reports nothing."""
    import rigplane.web.transport.ice_gathering as ice_gathering

    def _boom() -> list[Any]:
        raise OSError("no adapters")

    monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", _boom)
    loop = asyncio.get_running_loop()
    capture = _Capture()
    old = loop.get_exception_handler()
    loop.set_exception_handler(capture)
    stun, stun_port = await _blackhole_port()
    turn, turn_port = await _blackhole_port()
    offerer, sdp, typ = await _offer()
    manager = WebRtcSessionManager(None, None, "mor4671")
    try:
        negotiate = asyncio.ensure_future(
            manager.negotiate(sdp, typ, ice_servers=_ice_servers(stun_port, turn_port))
        )
        await _wait_for_gather(stun, turn, negotiate)
        closed = await manager.close_all()
        assert len(closed) == 1
        with pytest.raises(asyncio.CancelledError):
            await negotiate
        await _settle()
        assert capture.describe() == []
    finally:
        with contextlib.suppress(Exception):
            await manager.close_all()
        await offerer.close()
        loop.set_exception_handler(old)
