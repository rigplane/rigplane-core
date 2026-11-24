"""The split scope/audio data channel merges into one Connection (K5, MOR-272).

A lossy kind's text frames ride a reliable ordered ``*-text`` companion and
only its binary frames ride the unordered ``maxRetransmits=0`` channel; the
adapter merges the pair into the ONE ``Connection`` the unchanged handler
sees. These tests drive the adapter over in-memory channels (no aiortc), so
the routing rules are pinned deterministically.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.testing.timing import VirtualClock
from rigplane.web import channel_attach
from rigplane.web.channel_attach import (
    DATA_CHANNELS,
    ChannelAttachedAck,
    ClientChannelParams,
    data_channel_spec,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.transport import webrtc, webrtc_session
from rigplane.web.transport.webrtc import WebRtcDataChannelConnection
from rigplane.web.transport.webrtc_contract import WebRtcTransportStatus
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT


class _FakeChannel:
    """The ``RTCDataChannel`` surface the adapter uses, in memory."""

    def __init__(
        self,
        label: str,
        *,
        ordered: bool = True,
        maxRetransmits: int | None = None,
        maxPacketLifeTime: int | None = None,
    ) -> None:
        self.label = label
        self.id = 0
        self.bufferedAmount = 0
        self.ordered = ordered
        self.maxRetransmits = maxRetransmits
        self.maxPacketLifeTime = maxPacketLifeTime
        self.readyState = "open"
        self.sent: list[str | bytes] = []
        self._listeners: dict[str, list[Callable[..., None]]] = {}

    def on(self, event: str, listener: Callable[..., None]) -> None:
        self._listeners.setdefault(event, []).append(listener)

    def send(self, data: str | bytes) -> None:
        self.sent.append(data)

    def close(self) -> None:
        if self.readyState != "closed":
            self.readyState = "closed"
            for listener in self._listeners.get("close", []):
                listener()

    def deliver(self, message: str | bytes) -> None:
        for listener in self._listeners.get("message", []):
            listener(message)


class _FakePc:
    connectionState = "connected"

    def __init__(self) -> None:
        self.created: list[_FakeChannel] = []

    def createDataChannel(self, label: str, **options: Any) -> _FakeChannel:  # noqa: N802
        channel = _FakeChannel(label, **options)
        self.created.append(channel)
        return channel


def _pair() -> tuple[WebRtcDataChannelConnection, _FakeChannel, _FakeChannel]:
    binary = _FakeChannel("audio", ordered=False, maxRetransmits=0)
    text = _FakeChannel("audio-text")
    conn = WebRtcDataChannelConnection(
        binary,  # type: ignore[arg-type]
        _FakePc(),  # type: ignore[arg-type]
        text_channel=text,  # type: ignore[arg-type]
    )
    return conn, binary, text


def test_channel_set_contract() -> None:
    """Control/api/rigctl one reliable channel each; scope/audio a
    lossy binary channel plus a reliable ordered text companion."""
    assert [
        (s.label, s.kind, s.ordered, s.max_retransmits, s.carries)
        for s in DATA_CHANNELS
    ] == [
        ("control", "control", True, None, "all"),
        ("api", "api", True, None, "all"),
        ("scope", "scope", False, 0, "binary"),
        ("scope-text", "scope", True, None, "text"),
        ("audio", "audio", False, 0, "binary"),
        ("audio-text", "audio", True, None, "text"),
        ("rigctl", "rigctl", True, None, "all"),
    ]
    assert data_channel_spec("future") is None


@pytest.mark.parametrize("versions", [None, [], [99]])
def test_old_or_incompatible_station_does_not_open_rigctl(
    versions: list[int] | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload: dict[str, object] = {"available": True, "enabled": True}
    if versions is not None:
        payload["rigctl_versions"] = versions
    status = WebRtcTransportStatus.from_json(payload)
    assert status.select_rigctl_version() is None
    monkeypatch.setattr(webrtc, "webrtc_available", lambda: True)
    pc = _FakePc()
    with pytest.raises(
        webrtc.WebRtcUnavailableError, match="compatible rigctl version"
    ):
        webrtc.add_data_channel(pc, "rigctl", transport_status=status)  # type: ignore[arg-type]
    assert not pc.created


def test_text_goes_to_the_reliable_companion_and_bytes_to_the_lossy_channel() -> None:
    conn, binary, text = _pair()

    async def _run() -> None:
        await conn.send_text('{"type":"audio_start","direction":"rx"}')
        await conn.send_binary(b"\x01\x02")

    asyncio.run(_run())
    assert text.sent == ['{"type":"audio_start","direction":"rx"}']
    assert binary.sent == [b"\x01\x02"]


def test_recv_merges_both_channels_in_arrival_order() -> None:
    conn, binary, text = _pair()

    async def _run() -> list[tuple[int, bytes]]:
        binary.deliver(b"\xaa")
        text.deliver('{"type":"audio_stop"}')
        binary.deliver(b"\xbb")
        return [await conn.recv() for _ in range(3)]

    assert asyncio.run(_run()) == [
        (WS_OP_BINARY, b"\xaa"),
        (WS_OP_TEXT, b'{"type":"audio_stop"}'),
        (WS_OP_BINARY, b"\xbb"),
    ]


@pytest.mark.parametrize("closing", ["binary", "text"])
def test_either_half_closing_ends_the_merged_connection(closing: str) -> None:
    conn, binary, text = _pair()
    assert conn.is_alive()
    (binary if closing == "binary" else text).close()
    assert not conn.is_alive()

    async def _run() -> None:
        with pytest.raises(EOFError):
            await conn.recv()

    asyncio.run(_run())


def test_close_and_abort_close_both_halves() -> None:
    conn, binary, text = _pair()
    asyncio.run(conn.close())
    assert (binary.readyState, text.readyState) == ("closed", "closed")

    conn, binary, text = _pair()
    conn.abort()
    assert (binary.readyState, text.readyState) == ("closed", "closed")


def test_is_alive_needs_both_halves_open() -> None:
    conn, binary, _text = _pair()
    binary.readyState = "connecting"
    assert not conn.is_alive()


@pytest.mark.parametrize("kind", ["scope", "audio"])
def test_factory_builds_the_pair_from_the_contract(
    kind: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webrtc, "webrtc_available", lambda: True)
    pc = _FakePc()
    conn = webrtc.add_data_channel(pc, kind)  # type: ignore[arg-type]
    assert [(c.label, c.ordered, c.maxRetransmits) for c in pc.created] == [
        (kind, False, 0),
        (f"{kind}-text", True, None),
    ]
    assert conn._channel is pc.created[0]
    assert conn._text_channel is pc.created[1]


def test_lossy_attach_resend_mechanism_is_gone() -> None:
    """One mechanism: attach frames ride the reliable companion, so the
    K5 resend rule, its server re-ack wrapper and the client resend timer
    are deleted, not kept beside it."""
    assert not hasattr(channel_attach, "ReAckingConnection")
    root = Path(__file__).resolve().parents[1]
    client = (root / "frontend/src/lib/transport/datachannel-transport.ts").read_text(
        encoding="utf-8"
    )
    assert "ATTACH_RESEND_MS" not in client
    assert "setInterval" not in client


def _answerer() -> tuple[Any, Any, list[Any]]:
    """A session manager whose serve step only records the connections it
    would serve, plus one fake peer session."""
    served: list[Any] = []
    manager = webrtc_session.WebRtcSessionManager(None, None, "IC-7610")

    async def _capture(_sid: str, _session: Any, conn: Any, _kind: str) -> None:
        served.append(conn)

    manager._attach_and_serve = _capture  # type: ignore[method-assign]
    session = webrtc_session._Session(_FakePc())  # type: ignore[arg-type]
    # Registered like negotiate() does: dispatch serves only a live session.
    manager._sessions["sid"] = session
    return manager, session, served


def test_halves_pair_in_either_order() -> None:
    async def _run() -> None:
        manager, session, served = _answerer()
        text, binary = _FakeChannel("audio-text"), _FakeChannel("audio")
        manager._dispatch_channel("sid", session, text)
        manager._dispatch_channel("sid", session, binary)
        await asyncio.gather(*session.tasks)
        (conn,) = served
        assert (conn._channel, conn._text_channel) == (binary, text)
        assert session.unpaired == {}

    asyncio.run(_run())


def test_a_second_simultaneous_half_of_the_same_key_is_closed_at_once() -> None:
    """One pair per kind at a time: while an ``audio`` half waits, another
    ``audio`` half is closed instead of risking a crossed pair; the waiting
    half still pairs with the next ``audio-text``."""

    async def _run() -> None:
        manager, session, served = _answerer()
        first, second = _FakeChannel("audio"), _FakeChannel("audio")
        text = _FakeChannel("audio-text")
        manager._dispatch_channel("sid", session, first)
        manager._dispatch_channel("sid", session, second)
        assert (first.readyState, second.readyState) == ("open", "closed")
        manager._dispatch_channel("sid", session, text)
        await asyncio.gather(*session.tasks)
        (conn,) = served
        assert (conn._channel, conn._text_channel) == (first, text)

    asyncio.run(_run())


def test_frames_before_the_partner_arrives_are_kept() -> None:
    """The text half arrives first and carries the attach before its lossy
    partner exists: the merged connection still yields that attach first."""

    async def _run() -> list[tuple[int, bytes]]:
        manager, session, served = _answerer()
        text, binary = _FakeChannel("scope-text"), _FakeChannel("scope")
        manager._dispatch_channel("sid", session, text)
        text.deliver('{"type":"attach"}')
        manager._dispatch_channel("sid", session, binary)
        binary.deliver(b"\x01")
        await asyncio.gather(*session.tasks)
        (conn,) = served
        return [await conn.recv(), await conn.recv()]

    assert asyncio.run(_run()) == [
        (WS_OP_TEXT, b'{"type":"attach"}'),
        (WS_OP_BINARY, b"\x01"),
    ]


async def test_failed_peer_fences_cat_owner_before_drop(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    handlers: dict[str, Callable[..., Any]] = {}
    pc = Mock(
        connectionState="connected",
        localDescription=SimpleNamespace(sdp="answer", type="answer"),
        setRemoteDescription=AsyncMock(),
        createAnswer=AsyncMock(),
        setLocalDescription=AsyncMock(),
        close=AsyncMock(),
    )
    pc.on.side_effect = lambda event: (
        lambda handler: handlers.setdefault(event, handler)
    )
    monkeypatch.setitem(
        sys.modules,
        "aiortc",
        SimpleNamespace(
            RTCConfiguration=SimpleNamespace,
            RTCIceServer=SimpleNamespace,
            RTCPeerConnection=lambda _config: pc,
            RTCSessionDescription=SimpleNamespace,
        ),
    )
    monkeypatch.setattr(webrtc_session, "webrtc_available", lambda: True)

    web = WebServer(config=WebConfig(webrtc_enabled=True, radio_model="IC-7610"))
    manager = web._webrtc_session_manager()  # noqa: SLF001
    controller = web._controller  # noqa: SLF001
    monkeypatch.setattr(controller, "_clock", virtual_clock)
    tx = Mock(spec=ManagedTxAuthority)
    tx.tx_holder_nowait.return_value = None
    monkeypatch.setattr(web, "_managed_tx_authority", lambda: tx)

    async def disconnect(owner: str) -> ManagedTxOutcome:
        assert tx.tx_holder_nowait() == owner
        tx.tx_holder_nowait.return_value = None
        return ManagedTxOutcome.ACCEPTED

    tx.owner_disconnect.side_effect = disconnect

    def open_cat(reader: Any, writer: Any, owner: str) -> asyncio.Task[None]:
        async def serve() -> None:
            assert await reader.readline() == b"T 1\n"
            tx.tx_holder_nowait.return_value = owner
            writer.write(b"RPRT 0\n")
            await writer.drain()
            await reader.readline()

        return asyncio.create_task(serve())

    web.attach_rigctl_channel(open_cat)
    drop = AsyncMock(wraps=manager._drop)  # noqa: SLF001
    monkeypatch.setattr(manager, "_drop", drop)
    end_session = controller._end_session  # noqa: SLF001
    fenced: list[tuple[str, int, list[str]]] = []

    def record_fence(owner: str) -> None:
        fenced.append((owner, drop.await_count, manager.active_session_ids))
        end_session(owner)

    monkeypatch.setattr(controller, "_end_session", record_fence)
    try:
        answer = await manager.negotiate("offer", "offer")
        key = controller.acquire("pro")["controller_key"]
        assert isinstance(key, str)
        owner = controller.credential(key).tx_owner
        assert owner is not None

        control, cat = _FakeChannel("control"), _FakeChannel("rigctl")
        handlers["datachannel"](control)
        control.deliver(
            ClientChannelParams(
                controller_key=key, controller_role="primary", state_delta=2
            ).to_attach_frame()
        )
        await virtual_clock.elapse(0)
        assert control.sent and json.loads(control.sent[0])["type"] == "hello"
        assert controller.is_session_owner(owner)
        handlers["datachannel"](cat)
        cat.deliver(
            ClientChannelParams(
                controller_key=key, controller_role="auxiliary", rigctl_version=1
            ).to_attach_frame()
        )
        await virtual_clock.elapse(0)
        assert cat.sent == [ChannelAttachedAck("rigctl").encode()]
        cat.deliver("T 1\n")
        await virtual_clock.elapse(0)
        assert cat.sent[-1] == "RPRT 0\n"
        assert tx.tx_holder_nowait() == owner
        tx.owner_disconnect.assert_not_awaited()

        pc.connectionState = "failed"
        failed = asyncio.create_task(handlers["connectionstatechange"]())
        await virtual_clock.elapse(0)
        assert failed.done()
        await failed

        assert fenced == [(owner, 1, [answer["sessionId"]])]
        tx.owner_disconnect.assert_awaited_once_with(owner)
        assert tx.tx_holder_nowait() is None
        assert (control.readyState, cat.readyState) == ("closed", "closed")
        assert manager.active_session_ids == []
        pc.close.assert_awaited_once()
    finally:
        await manager.close_all()
        controller.revoke()
        await controller.settle()


@pytest.mark.parametrize(
    ("label", "max_packet_lifetime"),
    [("scope/a/b", None), ("scope/a", None), ("rigctl", 100), ("nope", None)],
)
def test_a_disallowed_channel_is_closed(
    label: str, max_packet_lifetime: int | None
) -> None:
    async def _run() -> None:
        manager, session, served = _answerer()
        channel = _FakeChannel(label, maxPacketLifeTime=max_packet_lifetime)
        manager._dispatch_channel("sid", session, channel)
        assert channel.readyState == "closed"
        assert served == [] and session.unpaired == {}

    asyncio.run(_run())


def test_frames_before_serve_are_bounded_then_unbounded_once_served() -> None:
    """Every channel, not only a waiting half: unread frames before the
    connection is served are capped; past the cap the connection closes.
    Once served, the handler's own pace governs (no cap)."""
    conn, binary, text = _pair()
    for _ in range(webrtc.MAX_FRAMES_BEFORE_SERVE):
        text.deliver("{}")
    assert conn.is_alive()
    binary.deliver(b"\x00")  # one too many before serve
    assert not conn.is_alive()
    assert (binary.readyState, text.readyState) == ("closed", "closed")

    served_conn, served_binary, _ = _pair()
    served_conn.serving = True
    for _ in range(webrtc.MAX_FRAMES_BEFORE_SERVE * 2):
        served_binary.deliver(b"\x00")
    assert served_conn.is_alive()
