"""Opt-in terminal observations through the existing control events stream."""

from __future__ import annotations

import asyncio
import inspect
import sys
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane import WebRtcSessionEnded
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.testing.timing import ManualClock, VirtualClock
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport import ice_gathering, webrtc_session
from rigplane.web.transport.webrtc_session import (
    WebRtcSessionError,
    WebRtcSessionManager,
)

from test_webrtc_public_contracts import _FakeWriter, _parse_response, _reader


@dataclass
class _Description:
    sdp: str
    type: str


class _Peer:
    def __init__(self) -> None:
        self.connectionState = "new"
        self.localDescription: _Description | None = None
        self.handlers: dict[str, Callable[..., Any]] = {}
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_release.set()
        self.teardown_completed = False
        self.fail_negotiate = False
        self.fail_close = False

    def on(self, name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def register(handler: Callable[..., Any]) -> Callable[..., Any]:
            self.handlers[name] = handler
            return handler

        return register

    def emit_state(self, state: str) -> asyncio.Future[None] | None:
        self.connectionState = state
        result = self.handlers["connectionstatechange"]()
        if inspect.isawaitable(result):
            return asyncio.ensure_future(result)
        return None

    async def state(self, state: str) -> None:
        task = self.emit_state(state)
        if task is not None:
            await task

    async def setRemoteDescription(self, description: _Description) -> None:
        if self.fail_negotiate:
            raise ValueError("bad offer")

    async def createAnswer(self) -> _Description:
        return _Description("answer-sdp", "answer")

    async def setLocalDescription(self, description: _Description) -> None:
        self.localDescription = description

    async def close(self) -> None:
        self.close_started.set()
        await self.close_release.wait()
        if self.fail_close:
            raise RuntimeError("teardown failed")
        await self.state("closed")
        self.teardown_completed = True


@pytest.fixture
def peers(monkeypatch: pytest.MonkeyPatch) -> list[_Peer]:
    peers: list[_Peer] = []

    def make_peer(configuration: object) -> _Peer:
        peer = _Peer()
        peers.append(peer)
        return peer

    monkeypatch.setitem(
        sys.modules,
        "aiortc",
        SimpleNamespace(
            RTCPeerConnection=make_peer,
            RTCConfiguration=lambda **kwargs: kwargs,
            RTCIceServer=lambda **kwargs: kwargs,
            RTCSessionDescription=_Description,
        ),
    )
    monkeypatch.setattr(webrtc_session, "webrtc_available", lambda: True)
    monkeypatch.setattr("rigplane.web.server.webrtc_available", lambda: True)
    monkeypatch.setattr(ice_gathering, "install_ice_gathering_policy", lambda: None)
    return peers


def _setup(
    clock: ManualClock,
) -> tuple[WebServer, WebRtcSessionManager, BoundedQueue[dict[str, Any]]]:
    server = WebServer(None, WebConfig(webrtc_enabled=True))
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=1)
    server._control_event_queues.add(queue)
    manager = WebRtcSessionManager(None, server, "IC-7610", now=clock)
    server._webrtc_sessions = manager
    return server, manager, queue


async def _offer(server: WebServer, extra: dict[str, object]) -> dict[str, Any]:
    headers, reader = _reader({"sdp": "offer-sdp", **extra})
    writer = _FakeWriter()
    await server._handle_webrtc_offer(writer, headers, reader)
    code, body = _parse_response(writer)
    assert code == 200
    assert set(body) == {"status", "sessionId", "sdp", "type"}
    return body


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {"correlation_id": None}])
async def test_uncorrelated_offer_produces_no_event(
    peers: list[_Peer], extra: dict[str, object]
) -> None:
    server, manager, queue = _setup(ManualClock())
    answer = await _offer(server, extra)
    await manager.close_session(answer["sessionId"])
    assert peers[0].teardown_completed
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "correlation_id", [1, False, [], {}, "a" * 129, "a b", "a/b", "a\n", "é"]
)
async def test_invalid_correlation_id_rejected_before_peer_creation(
    peers: list[_Peer],
    correlation_id: object,
) -> None:
    server, _, queue = _setup(ManualClock())
    headers, reader = _reader({"sdp": "offer-sdp", "correlation_id": correlation_id})
    writer = _FakeWriter()
    await server._handle_webrtc_offer(writer, headers, reader)
    code, body = _parse_response(writer)
    assert code == 400
    assert body["code"] == "sdp_error"
    assert peers == []
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("correlation_id", ["", "Opaque_1:a.b-c"])
@pytest.mark.parametrize("reason", ["peer_failed", "peer_closed", "close_requested"])
async def test_terminal_reason_once_after_teardown(
    peers: list[_Peer],
    reason: str,
    correlation_id: str,
    virtual_clock: VirtualClock,
) -> None:
    now = ManualClock(1795737600.25)
    server, manager, queue = _setup(now)
    answer = await _offer(server, {"correlation_id": correlation_id})
    peer = peers[0]
    await peer.state("connected")
    await peer.state("disconnected")
    assert queue.empty()
    peer.close_release.clear()
    close = (
        manager.close_session(answer["sessionId"])
        if reason == "close_requested"
        else peer.state(reason.removeprefix("peer_"))
    )
    task = asyncio.create_task(close)
    await virtual_clock.elapse(0)
    assert peer.close_started.is_set()
    assert not peer.teardown_completed
    assert queue.empty()
    now.advance(2.5)
    peer.close_release.set()
    await task
    await virtual_clock.elapse(0)
    assert peer.teardown_completed
    envelope = queue.get_nowait()
    assert envelope == {
        "type": "event",
        "name": "webrtc_session_ended",
        "data": {
            "correlation_id": correlation_id,
            "ended_at": 1795737602,
            "reason": reason,
            "ever_connected": True,
            "path": "unknown",
        },
    }
    assert WebRtcSessionEnded.from_json(envelope["data"]).to_json() == envelope["data"]
    await peer.state("failed")
    await peer.state("closed")
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("states", "ever_connected"),
    [
        (("connected", "failed"), True),
        (("failed", "closed"), False),
        (("connected", "failed", "closed"), True),
    ],
)
async def test_back_to_back_peer_states_preserve_history_and_first_reason(
    peers: list[_Peer],
    states: tuple[str, ...],
    ever_connected: bool,
    virtual_clock: VirtualClock,
) -> None:
    now = ManualClock(1795737600)
    server, manager, queue = _setup(now)
    server._control_event_queues.remove(queue)
    queue = BoundedQueue(maxsize=10)
    server._control_event_queues.add(queue)
    answer = await manager.negotiate("offer", "offer", correlation_id="rapid")
    session = manager._sessions[answer["sessionId"]]
    peer = peers[0]
    peer.close_release.clear()

    for state in states:
        peer.emit_state(state)
    assert not peer.close_started.is_set()
    assert queue.empty()

    await virtual_clock.elapse(0)
    assert peer.close_started.is_set()
    assert not peer.teardown_completed
    assert queue.empty()
    now.advance(2)
    peer.close_release.set()
    await virtual_clock.elapse(0)

    assert peer.teardown_completed
    assert queue.get_nowait() == {
        "type": "event",
        "name": "webrtc_session_ended",
        "data": {
            "correlation_id": "rapid",
            "ended_at": 1795737602,
            "reason": "peer_failed",
            "ever_connected": ever_connected,
            "path": "unknown",
        },
    }
    assert queue.empty()
    assert manager.active_session_ids == []
    assert not session.tasks
    peer.emit_state("failed")
    peer.emit_state("closed")
    await virtual_clock.elapse(0)
    assert queue.empty()
    assert not session.tasks


@pytest.mark.asyncio
@pytest.mark.parametrize("close_all", [False, True])
async def test_peer_failure_reason_is_latched_before_an_explicit_close(
    peers: list[_Peer],
    close_all: bool,
    virtual_clock: VirtualClock,
) -> None:
    _, manager, queue = _setup(ManualClock())
    answer = await manager.negotiate("offer", "offer", correlation_id="race")
    peer = peers[0]
    peer.emit_state("connected")
    peer.emit_state("failed")
    if close_all:
        assert await manager.close_all() == [answer["sessionId"]]
    else:
        await manager.close_session(answer["sessionId"])
    await virtual_clock.elapse(0)
    assert peer.teardown_completed
    data = queue.get_nowait()["data"]
    assert data["reason"] == "peer_failed"
    assert data["ever_connected"] is True
    assert queue.empty()


@pytest.mark.asyncio
async def test_negotiation_failure_emits_after_teardown(
    peers: list[_Peer],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, manager, queue = _setup(ManualClock(1795737600))
    original = _Peer.setRemoteDescription

    async def fail(peer: _Peer, description: _Description) -> None:
        peer.fail_negotiate = True
        await original(peer, description)

    monkeypatch.setattr(_Peer, "setRemoteDescription", fail)
    with pytest.raises(WebRtcSessionError, match="bad offer"):
        await manager.negotiate("bad", "offer", correlation_id="negotiation")
    assert peers[0].teardown_completed
    data = queue.get_nowait()["data"]
    assert data["reason"] == "negotiation_failed"
    assert data["correlation_id"] == "negotiation"
    assert data["ever_connected"] is False
    await manager.close_all()
    await peers[0].state("failed")
    await peers[0].state("closed")
    assert queue.empty()


@pytest.mark.asyncio
async def test_close_all_emits_once_per_correlated_session(peers: list[_Peer]) -> None:
    server, manager, queue = _setup(ManualClock(1795737600))
    server._control_event_queues.remove(queue)
    queue = BoundedQueue(maxsize=10)
    server._control_event_queues.add(queue)
    for correlation in ("first", "second", None):
        await manager.negotiate("offer", "offer", correlation_id=correlation)
    assert len(await manager.close_all()) == 3
    assert all(peer.teardown_completed for peer in peers)
    data = [queue.get_nowait()["data"] for _ in range(2)]
    assert [fact["correlation_id"] for fact in data] == ["first", "second"]
    assert {fact["reason"] for fact in data} == {"all_sessions_closed"}
    assert all(fact["ever_connected"] is False for fact in data)
    assert await manager.close_all() == []
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("states", [("failed",), ("closed",), ("failed", "closed")])
async def test_close_all_latches_every_reason_before_awaiting_teardown(
    peers: list[_Peer],
    states: tuple[str, ...],
    virtual_clock: VirtualClock,
) -> None:
    server, manager, queue = _setup(ManualClock(1795737600))
    server._control_event_queues.remove(queue)
    queue = BoundedQueue(maxsize=10)
    server._control_event_queues.add(queue)
    answers = [
        await manager.negotiate("offer", "offer", correlation_id=correlation)
        for correlation in ("first", "second")
    ]
    sessions = list(manager._sessions.values())
    peers[0].close_release.clear()
    close = asyncio.create_task(manager.close_all())
    try:
        await virtual_clock.elapse(0)
        assert peers[0].close_started.is_set()
        assert not peers[0].teardown_completed
        assert not peers[1].close_started.is_set()
        assert manager.active_session_ids == []
        for state in states:
            peers[1].emit_state(state)
        await virtual_clock.elapse(0)
        assert not peers[1].close_started.is_set()
        assert queue.empty()
    finally:
        peers[0].close_release.set()
        closed = await close
        await virtual_clock.elapse(0)

    assert closed == [answer["sessionId"] for answer in answers]
    assert all(peer.teardown_completed for peer in peers)
    data = [queue.get_nowait()["data"] for _ in range(2)]
    assert [fact["correlation_id"] for fact in data] == ["first", "second"]
    assert [fact["reason"] for fact in data] == [
        "all_sessions_closed",
        "all_sessions_closed",
    ]
    assert queue.empty()
    for peer in peers:
        peer.emit_state("failed")
        peer.emit_state("closed")
    await virtual_clock.elapse(0)
    assert await manager.close_all() == []
    assert queue.empty()
    assert all(not session.tasks for session in sessions)


@pytest.mark.asyncio
async def test_full_event_queue_drops_without_blocking_or_retry(
    peers: list[_Peer],
    virtual_clock: VirtualClock,
) -> None:
    _, manager, queue = _setup(ManualClock())
    answer = await manager.negotiate("offer", "offer", correlation_id="full")
    occupied = {"type": "event", "name": "occupied", "data": {}}
    queue.put_nowait(occupied)
    task = asyncio.create_task(manager.close_session(answer["sessionId"]))
    await virtual_clock.elapse(0)
    assert task.done()
    await task
    assert peers[0].teardown_completed
    assert queue.get_nowait() == occupied
    await virtual_clock.elapse(1)
    await manager.close_all()
    assert queue.empty()


@pytest.mark.asyncio
async def test_repeated_session_close_preserves_first_reason_and_emits_once(
    peers: list[_Peer],
) -> None:
    _, manager, queue = _setup(ManualClock())
    answer = await manager.negotiate("offer", "offer", correlation_id="repeat")
    session = manager._sessions[answer["sessionId"]]
    await manager.close_session(answer["sessionId"])
    assert queue.get_nowait()["data"]["reason"] == "close_requested"
    await session.close(webrtc_session.WebRtcSessionEndReason.PEER_FAILED)
    assert peers[0].teardown_completed
    assert queue.empty()


@pytest.mark.asyncio
@pytest.mark.parametrize("close_all", [False, True])
async def test_cancelled_close_keeps_initiating_reason_and_emits_once(
    peers: list[_Peer],
    close_all: bool,
    virtual_clock: VirtualClock,
) -> None:
    now = ManualClock(1795737600)
    _, manager, queue = _setup(now)
    answer = await manager.negotiate("offer", "offer", correlation_id="cancel")
    peer = peers[0]
    peer.close_release.clear()
    task = asyncio.create_task(
        manager.close_all() if close_all else manager.close_session(answer["sessionId"])
    )
    await virtual_clock.elapse(0)
    assert peer.close_started.is_set()
    task.cancel()
    await virtual_clock.elapse(0)
    await peer.state("failed")
    assert not task.done()
    assert not peer.teardown_completed
    assert queue.empty()
    now.advance(2)
    peer.close_release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert peer.teardown_completed
    assert queue.get_nowait()["data"] == {
        "correlation_id": "cancel",
        "ended_at": 1795737602,
        "reason": "all_sessions_closed" if close_all else "close_requested",
        "ever_connected": False,
        "path": "unknown",
    }
    assert manager.active_session_ids == []
    await peer.state("closed")
    assert queue.empty()


@pytest.mark.asyncio
async def test_failed_teardown_does_not_publish_terminal_fact(
    peers: list[_Peer],
) -> None:
    _, manager, queue = _setup(ManualClock())
    answer = await manager.negotiate("offer", "offer", correlation_id="broken")
    peers[0].fail_close = True
    with pytest.raises(RuntimeError, match="teardown failed"):
        await manager.close_session(answer["sessionId"])
    assert not peers[0].teardown_completed
    assert queue.empty()


@pytest.mark.asyncio
async def test_peer_teardown_failure_reaches_dispatch_without_a_terminal_fact(
    peers: list[_Peer],
    virtual_clock: VirtualClock,
) -> None:
    _, manager, queue = _setup(ManualClock())
    answer = await manager.negotiate("offer", "offer", correlation_id="broken-peer")
    session = manager._sessions[answer["sessionId"]]
    peers[0].fail_close = True
    task = peers[0].emit_state("failed")
    assert task is not None
    await virtual_clock.elapse(0)
    assert task.done()
    with pytest.raises(RuntimeError, match="teardown failed"):
        await task
    assert not peers[0].teardown_completed
    assert queue.empty()
    assert not session.tasks
    assert manager.active_session_ids == []
