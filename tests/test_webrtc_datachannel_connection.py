"""Lab harness for the WebRTC DataChannel adapter (MOR-312 / A2.1).

Negotiates a loopback ``RTCPeerConnection`` pair, wraps the server-side
``control`` channel in :class:`WebRtcDataChannelConnection`, drives the
*unchanged* ``ControlHandler`` over it, and asserts the control frames that
arrive client-side are byte-identical to the WebSocket wire format.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest

from rigplane.web.transport.webrtc import (
    WebRtcDataChannelConnection,
    WebRtcUnavailableError,
    add_control_channel,
    webrtc_available,
)
from rigplane.web.websocket import WS_OP_BINARY, WS_OP_TEXT

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
