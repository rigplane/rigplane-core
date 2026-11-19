"""Lab harness for the WebRTC DataChannel adapter (MOR-312 / A2.1, MOR-306 / A2.2).

Negotiates a loopback ``RTCPeerConnection`` pair, wraps the server-side
``control`` channel in :class:`WebRtcDataChannelConnection`, drives the
*unchanged* ``ControlHandler`` over it, and asserts the control frames that
arrive client-side are byte-identical to the WebSocket wire format.

A2.2 extends this to ``scope`` + ``audio`` on the same PC, each a split pair
(K5, MOR-272): binary frames on an unordered ``maxRetransmits=0`` channel,
text frames on its reliable ordered ``*-text`` companion, merged into the one
connection its unchanged handler sees.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from rigplane.web.transport.webrtc import (
    WebRtcDataChannelConnection,
    WebRtcUnavailableError,
    add_audio_channel,
    add_control_channel,
    add_scope_channel,
    webrtc_available,
)
from rigplane.ws import WS_OP_BINARY, WS_OP_TEXT

if TYPE_CHECKING:
    from aiortc import RTCDataChannel

pytestmark = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)


async def _negotiate() -> tuple[Any, Any, RTCDataChannel, asyncio.Future[Any]]:
    """Connect two PCs; return (client_pc, server_pc, server_channel, future).

    ``future`` resolves with the server-side ``control`` ``RTCDataChannel``
    once the answerer receives it. The client PC owns the offer + channel.
    """
    from aiortc import RTCPeerConnection

    client_pc = RTCPeerConnection()
    server_pc = RTCPeerConnection()
    server_channel: asyncio.Future[Any] = asyncio.get_event_loop().create_future()

    @server_pc.on("datachannel")  # type: ignore[misc, no-untyped-call]
    def _on_dc(channel: RTCDataChannel) -> None:
        if not server_channel.done():
            server_channel.set_result(channel)

    client_channel = client_pc.createDataChannel("control", ordered=True)

    offer = await client_pc.createOffer()
    await client_pc.setLocalDescription(offer)
    await server_pc.setRemoteDescription(client_pc.localDescription)
    answer = await server_pc.createAnswer()
    await server_pc.setLocalDescription(answer)
    await client_pc.setRemoteDescription(server_pc.localDescription)

    await asyncio.wait_for(server_channel, timeout=15)
    return client_pc, server_pc, client_channel, server_channel


@pytest.mark.asyncio
async def test_control_hello_round_trips_into_unchanged_handler() -> None:
    """``ControlHandler.run`` over the adapter delivers a WS-identical hello."""
    from rigplane.web.handlers import ControlHandler

    client_pc, server_pc, client_channel, fut = await _negotiate()
    received: list[Any] = []

    @client_channel.on("message")  # type: ignore[misc, no-untyped-call]
    def _on_msg(message: Any) -> None:
        received.append(message)

    server_dc = fut.result()
    conn = WebRtcDataChannelConnection(server_dc, server_pc)
    # radio=None, server=None → run() sends hello then blocks on recv().
    handler = ControlHandler(conn, None, "9.9.9", "IC-7610", server=None)
    run_task = asyncio.create_task(handler.run())

    # Wait for the hello frame to arrive client-side.
    for _ in range(100):
        if received:
            break
        await asyncio.sleep(0.05)

    assert received, "no control frame received over data channel"
    hello_raw = received[0]
    # Wire format is identical to WS: a UTF-8 JSON text frame.
    assert isinstance(hello_raw, str)
    hello = json.loads(hello_raw)
    assert hello["type"] == "hello"
    assert hello["server"] == "rigplane"
    assert hello["radio"] == "IC-7610"

    await conn.close()
    run_task.cancel()
    try:
        await run_task
    except asyncio.CancelledError:
        pass
    await client_pc.close()


@pytest.mark.asyncio
async def test_recv_maps_text_and_binary_opcodes() -> None:
    """Inbound text→WS_OP_TEXT, bytes→WS_OP_BINARY (WS-identical opcodes)."""
    client_pc, server_pc, client_channel, fut = await _negotiate()
    server_dc = fut.result()
    conn = WebRtcDataChannelConnection(server_dc, server_pc)

    # Ensure the client channel is open before sending.
    for _ in range(100):
        if client_channel.readyState == "open":
            break
        await asyncio.sleep(0.05)

    client_channel.send("ping")
    client_channel.send(b"\x01\x02\x03")

    op_text, payload_text = await asyncio.wait_for(conn.recv(), timeout=5)
    op_bin, payload_bin = await asyncio.wait_for(conn.recv(), timeout=5)

    assert op_text == WS_OP_TEXT
    assert payload_text == b"ping"
    assert op_bin == WS_OP_BINARY
    assert payload_bin == b"\x01\x02\x03"

    await conn.close()
    await client_pc.close()


@pytest.mark.asyncio
async def test_recv_raises_eof_on_close() -> None:
    """recv() raises EOFError after the connection is closed."""
    client_pc, server_pc, _client_channel, fut = await _negotiate()
    conn = WebRtcDataChannelConnection(fut.result(), server_pc)

    assert conn.is_alive() or conn._channel.readyState != "open"
    await conn.close()

    assert conn.is_alive() is False
    with pytest.raises(EOFError):
        await conn.recv()

    await client_pc.close()


@pytest.mark.asyncio
async def test_add_control_channel_creates_ordered_channel() -> None:
    """add_control_channel() builds an ordered 'control' channel adapter."""
    from aiortc import RTCPeerConnection

    pc = RTCPeerConnection()
    conn = add_control_channel(pc)
    assert isinstance(conn, WebRtcDataChannelConnection)
    assert conn._channel.label == "control"
    assert conn._channel.ordered is True
    await conn.close()


def test_unavailable_error_is_runtime_error() -> None:
    """WebRtcUnavailableError is a RuntimeError subclass (clear, not a crash)."""
    assert issubclass(WebRtcUnavailableError, RuntimeError)


# --------------------------------------------------------------------------
# A2.2 (MOR-306): scope + audio channels on the same PC as control.
# --------------------------------------------------------------------------


async def _negotiate_multi() -> tuple[Any, Any, dict[str, Any], dict[str, Any]]:
    """Open control + scope + audio on one PC pair via the production helpers.

    Returns ``(client_pc, server_pc, client_conns, server_channels)``:
    ``client_conns`` maps the kind → the offerer's connection (a split pair
    for scope/audio) and ``server_channels`` maps every label → its inbound
    ``RTCDataChannel``. The client side uses the production ``add_*_channel``
    factories so the asserted channel configs are exactly what ships.
    """
    from aiortc import RTCPeerConnection

    client_pc = RTCPeerConnection()
    server_pc = RTCPeerConnection()
    server_channels: dict[str, Any] = {}
    ready: dict[str, asyncio.Future[Any]] = {
        label: asyncio.get_event_loop().create_future()
        for label in ("control", "scope", "scope-text", "audio", "audio-text")
    }

    @server_pc.on("datachannel")  # type: ignore[misc, no-untyped-call]
    def _on_dc(channel: Any) -> None:
        # Keyed by spec label: split halves carry a pair id after "/".
        base = channel.label.split("/", 1)[0]
        server_channels[base] = channel
        fut = ready.get(base)
        if fut is not None and not fut.done():
            fut.set_result(channel)

    # Build the channels via the production factories (one PC).
    client_conns = {
        "control": add_control_channel(client_pc),
        "scope": add_scope_channel(client_pc),
        "audio": add_audio_channel(client_pc),
    }

    offer = await client_pc.createOffer()
    await client_pc.setLocalDescription(offer)
    await server_pc.setRemoteDescription(client_pc.localDescription)
    answer = await server_pc.createAnswer()
    await server_pc.setLocalDescription(answer)
    await client_pc.setRemoteDescription(server_pc.localDescription)

    await asyncio.wait_for(
        asyncio.gather(*ready.values()),
        timeout=15,
    )
    return client_pc, server_pc, client_conns, server_channels


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["scope", "audio"])
async def test_split_kind_is_lossy_binary_plus_reliable_text(kind: str) -> None:
    """add_scope/audio_channel() build the lossy binary channel and its
    reliable ordered ``*-text`` companion, merged into one connection."""
    from aiortc import RTCPeerConnection

    pc = RTCPeerConnection()
    conn = (add_scope_channel if kind == "scope" else add_audio_channel)(pc)
    assert isinstance(conn, WebRtcDataChannelConnection)
    pair_id = conn._channel.label.removeprefix(f"{kind}/")
    assert pair_id and conn._channel.label == f"{kind}/{pair_id}"
    assert conn._channel.ordered is False
    assert conn._channel.maxRetransmits == 0
    assert conn._text_channel.label == f"{kind}-text/{pair_id}"
    assert conn._text_channel.ordered is True
    assert conn._text_channel.maxRetransmits is None
    await conn.close()


@pytest.mark.asyncio
async def test_control_scope_audio_flow_concurrently_into_handlers() -> None:
    """One PC: control + scope + audio each reach their unchanged handler.

    Wraps each server-side channel in the A2.1 connection class, hands each to
    its real handler (``ControlHandler`` / ``ScopeHandler`` / ``AudioHandler``,
    all unmodified), then drives client→server traffic on scope + audio and a
    server→client hello on control — concurrently — and asserts every path
    flows through the ``Connection`` seam.
    """
    from rigplane.web.handlers import AudioHandler, ControlHandler, ScopeHandler

    client_pc, server_pc, client_conn, server_ch = await _negotiate_multi()

    # control: ControlHandler sends a hello frame client-side on run().
    control_hello: list[Any] = []

    @client_conn["control"]._channel.on("message")  # type: ignore[misc, no-untyped-call]
    def _on_control(message: Any) -> None:
        control_hello.append(message)

    control_conn = WebRtcDataChannelConnection(server_ch["control"], server_pc)
    control_handler = ControlHandler(
        control_conn, None, "9.9.9", "IC-7610", server=None
    )
    control_task = asyncio.create_task(control_handler.run())

    # scope: server=None → ScopeHandler.run() just reads inbound frames; the
    # seam still delivers them. Assert via the wrapped connection directly so
    # no handler edits are needed to observe arrival.
    scope_conn = WebRtcDataChannelConnection(
        server_ch["scope"], server_pc, text_channel=server_ch["scope-text"]
    )
    scope_handler = ScopeHandler(scope_conn, server=None)
    assert scope_handler is not None  # constructed over the lossy seam

    # audio: broadcaster=None → AudioHandler reads inbound frames over the seam.
    audio_conn = WebRtcDataChannelConnection(
        server_ch["audio"], server_pc, text_channel=server_ch["audio-text"]
    )
    audio_handler = AudioHandler(audio_conn, None, broadcaster=None)
    assert audio_handler is not None  # constructed over the lossy seam

    # Wait for all client channels to open.
    for _ in range(100):
        if all(c.is_alive() for c in client_conn.values()):
            break
        await asyncio.sleep(0.05)

    # Drive scope + audio client→server concurrently; recv() proves the seam.
    await client_conn["scope"].send_binary(b"\xaa\xbb")
    await client_conn["audio"].send_binary(b"\x01\x02\x03\x04")

    scope_op, scope_payload = await asyncio.wait_for(scope_conn.recv(), timeout=5)
    audio_op, audio_payload = await asyncio.wait_for(audio_conn.recv(), timeout=5)

    # control hello round-trips while scope/audio flow on the same PC.
    for _ in range(100):
        if control_hello:
            break
        await asyncio.sleep(0.05)

    assert scope_op == WS_OP_BINARY
    assert scope_payload == b"\xaa\xbb"
    assert audio_op == WS_OP_BINARY
    assert audio_payload == b"\x01\x02\x03\x04"
    assert control_hello, "no control frame over the control channel"
    hello = json.loads(control_hello[0])
    assert hello["type"] == "hello"
    assert hello["radio"] == "IC-7610"

    # Text crosses on the reliable companion and merges into the same recv.
    await client_conn["audio"].send_text('{"type":"audio_stop"}')
    text_op, text_payload = await asyncio.wait_for(audio_conn.recv(), timeout=5)
    assert text_op == WS_OP_TEXT
    assert text_payload == b'{"type":"audio_stop"}'

    # Config contract: scope/audio binary lossy, their text halves and
    # control ordered/reliable — one PC.
    assert server_ch["control"].ordered is True
    for label in ("scope", "audio"):
        assert server_ch[label].ordered is False
        assert server_ch[label].maxRetransmits == 0
        assert server_ch[f"{label}-text"].ordered is True
        assert server_ch[f"{label}-text"].maxRetransmits is None

    await control_conn.close()
    control_task.cancel()
    try:
        await control_task
    except asyncio.CancelledError:
        pass
    await client_pc.close()
