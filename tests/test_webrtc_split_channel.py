"""The split scope/audio data channel merges into one Connection (K5, MOR-272).

A lossy kind's text frames ride a reliable ordered ``*-text`` companion and
only its binary frames ride the unordered ``maxRetransmits=0`` channel; the
adapter merges the pair into the ONE ``Connection`` the unchanged handler
sees. These tests drive the adapter over in-memory channels (no aiortc), so
the routing rules are pinned deterministically.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rigplane.web import channel_attach
from rigplane.web.channel_attach import DATA_CHANNELS, data_channel_spec
from rigplane.web.transport import webrtc
from rigplane.web.transport.webrtc import WebRtcDataChannelConnection
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT


class _FakeChannel:
    """The ``RTCDataChannel`` surface the adapter uses, in memory."""

    def __init__(
        self, label: str, *, ordered: bool = True, maxRetransmits: int | None = None
    ) -> None:
        self.label = label
        self.ordered = ordered
        self.maxRetransmits = maxRetransmits
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
    """The K5 set: control/api one reliable channel each; scope/audio a
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
    ]
    assert data_channel_spec("rigctl") is None


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
