"""One attach-parameter contract for every client-channel transport.

The WebSocket upgrade and the WebRTC data-channel dispatch attach a
client channel through the same mechanism
(``WebServer.serve_client_channel``). Both adapters parse their
transport framing into :class:`ClientChannelParams` first — the
WebSocket query plus ``Sec-WebSocket-Protocol`` header, or the data
channel's first-text-frame attach frame — so ticket capture, handler
construction, caps, keepalive and cleanup never diverge again.

K5, attach order on data channels (loss-safe by rule, MOR-272): every
channel attaches on its own. The client attaches ``control`` first (its
``hello`` proves the attach), then sends each ``scope``/``audio`` attach
and resends it every 250 ms until the ``attached`` ack arrives, giving
up after 5 s. The server treats a repeated attach as the same attach
(re-acked, no new ticket or handler) and ignores, without closing the
channel, a lossy attach that arrives before the session's control
attach; the next resend attaches. ``api`` is reliable and never acked.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, Self

from ..runtime.controller_authority import ControllerError
from ..ws import WS_OP_TEXT  # noqa: TID251

if TYPE_CHECKING:
    from .transport.connection import Connection  # noqa: TID251

__all__ = [
    "ATTACH_ACK_KINDS",
    "ATTACH_ACK_VERSION",
    "ChannelAttachedAck",
    "ChannelKind",
    "ClientChannelParams",
    "ReAckingConnection",
    "TransportKind",
]

#: Client channel kinds. ``rigctl`` exists only on the WebSocket lane;
#: data channels carry ``control`` / ``scope`` / ``audio`` / ``api`` labels.
ChannelKind = Literal["control", "scope", "audio", "api", "rigctl"]

#: Version of the attach acknowledgement frame (a versioned wire change).
ATTACH_ACK_VERSION: Final = 1

#: The only channel kinds that get an attach ack: ``scope`` and
#: ``audio`` are unordered with ``maxRetransmits=0`` — the only channels
#: whose attach frame can be LOST. ``control`` (``hello`` proves the
#: attach) and ``api`` (binary, reliable) never send one.
ATTACH_ACK_KINDS: Final = frozenset({"scope", "audio"})


@dataclass(frozen=True, slots=True)
class ChannelAttachedAck:
    """The attach ack: one text frame sent after a lossy-channel attach
    (scope/audio only) and before the first handler byte, so the client
    can stop resending its attach; control, api and the WebSocket lane
    never send one."""

    channel: str
    version: int = ATTACH_ACK_VERSION

    def encode(self) -> str:
        return json.dumps(
            {"type": "attached", "v": self.version, "channel": self.channel}
        )

    @classmethod
    def from_frame(cls, payload: str | bytes) -> Self:
        """Parse an ack frame (the offerer side of :meth:`encode`).

        Raises :class:`ValueError` for anything that is not a JSON
        ``{"type": "attached", "v": int, "channel": str}`` object.
        """
        try:
            text = payload.decode("utf-8") if isinstance(payload, bytes) else payload
            obj = json.loads(text)
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid attach ack: {exc}") from exc
        if not isinstance(obj, dict) or obj.get("type") != "attached":
            raise ValueError("attach ack must carry {'type': 'attached', ...}")
        version = obj.get("v")
        channel = obj.get("channel")
        if type(version) is not int or type(channel) is not str:
            raise ValueError("attach ack needs an int 'v' and a str 'channel'")
        return cls(channel=channel, version=version)


def _is_attach_frame(payload: bytes) -> bool:
    """True for a JSON ``{"type": "attach", ...}`` frame."""
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return False
    return isinstance(obj, dict) and obj.get("type") == "attach"


class ReAckingConnection:
    """Re-acknowledge a repeated attach frame on a lossy data channel.

    Wraps an ALREADY-attached scope/audio channel: a resent attach frame
    (its ack was lost) is answered with the same ack inside ``recv`` and
    never reaches the handler — no new ticket, no new handler. A dead
    re-ack send does not kill the channel; its own EOF handles the rest.
    """

    __slots__ = ("_ack_text", "_conn")

    def __init__(self, conn: Connection, kind: str) -> None:
        self._conn = conn
        self._ack_text = ChannelAttachedAck(kind).encode()

    async def recv(self) -> tuple[int, bytes]:
        while True:
            opcode, payload = await self._conn.recv()
            if opcode == WS_OP_TEXT and _is_attach_frame(payload):
                with contextlib.suppress(Exception):
                    await self._conn.send_text(self._ack_text)
                continue
            return opcode, payload

    async def send_text(self, text: str) -> None:
        await self._conn.send_text(text)

    async def send_binary(self, data: bytes) -> None:
        await self._conn.send_binary(data)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        await self._conn.close(code, reason)

    def abort(self) -> None:
        self._conn.abort()

    def is_alive(self) -> bool:
        return self._conn.is_alive()


#: Per-transport tag carried explicitly from the adapter into the shared
#: attach path. The shared serve never branches on the connection's
#: concrete type; the adapter states which transport it is.
TransportKind = Literal["ws", "datachannel"]


@dataclass(frozen=True, slots=True)
class ClientChannelParams:
    """Transport-independent client channel parameters.

    ``controller_key`` is the session key the WebSocket lane reads from
    ``Sec-WebSocket-Protocol`` (``rigplane-controller-v1.<key>``) and the
    data-channel lane reads from its attach frame; ``None`` means the
    channel names no session (local-mode sessionless capture, WebSocket
    lane only — a data channel always names a session).
    ``controller_role`` is the WebSocket ``controller_role`` query value
    (``"primary"`` default); the shared resolve forces ``"auxiliary"``
    for non-control channels exactly as the WebSocket path always did.
    ``state_delta`` is ``2`` for MOR-4206 leaf deltas, else ``1``.
    """

    controller_key: str | None
    controller_role: str = "primary"
    state_delta: int = 1

    @classmethod
    def from_ws(
        cls,
        query: dict[str, list[str]] | None,
        protocols: list[str],
        path: str,
    ) -> Self:
        """Parse WebSocket query + pre-filtered protocol list.

        Exactly the historical upgrade rules: at most one
        ``rigplane-controller-v1.*`` protocol is accepted (more than one
        is an ambiguous key), the ``controller_role`` query value must be
        singular on ``/api/v1/ws`` (``"auxiliary"`` is forced everywhere
        else), and ``?state_delta=2`` selects leaf deltas. No protocol
        means the channel names no session; the shared resolver captures
        the ambient sessionless ticket in local mode and refuses the
        channel anywhere else. Raises :class:`ControllerError` with the
        same codes the upgrade path always answered with.
        """
        if len(protocols) > 1:
            raise ControllerError("controller_invalid")
        if path == "/api/v1/ws":
            role = (query or {}).get("controller_role", ["primary"])
            if len(role) != 1:
                raise ControllerError("invalid_request", 400)
            role_value = role[0]
        else:
            role_value = "auxiliary"
        return cls(
            controller_key=protocols[0].split(".", 1)[1] if protocols else None,
            controller_role=role_value,
            state_delta=2 if (query or {}).get("state_delta") == ["2"] else 1,
        )

    @classmethod
    def from_attach_frame(cls, payload: bytes, *, require_key: bool = True) -> Self:
        """Parse a data channel's first-text-frame attach frame.

        The frame is ``{"type": "attach", "controller_key": str,
        "state_delta": 1 | 2, "role": str}``; ``role`` defaults to
        ``"primary"`` when absent, and ``state_delta`` defaults to ``2``
        (leaf deltas). Control/scope/audio data channels admit only
        keyed (session) peers: a missing or null ``controller_key`` is
        refused, so the existing controller session TTL and expiry cover
        every data-channel peer — a keyless peer could otherwise hold TX
        past a silent loss with no session to expire it. The ``api``
        channel passes ``require_key=False``: it is how a browser
        acquires its first key (``POST /api/v1/controller``), every
        request it carries names its own key the way HTTP does, and it
        holds no transmitter. Anything else — bad JSON, a non-object, a
        wrong ``type``, or mistyped fields — raises :class:`ValueError`
        and the adapter closes the channel.
        """
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid attach frame: {exc}") from exc
        if not isinstance(obj, dict) or obj.get("type") != "attach":
            raise ValueError("attach frame must carry {'type': 'attach', ...}")
        key = obj.get("controller_key")
        if type(key) is not str and (key is not None or require_key):
            raise ValueError("attach frame 'controller_key' must be a session key")
        role = obj.get("role", "primary")
        if type(role) is not str:
            raise ValueError("attach frame 'role' must be a string")
        raw_delta = obj.get("state_delta", 2)
        if type(raw_delta) is not int or raw_delta not in (1, 2):
            raise ValueError("attach frame 'state_delta' must be 1 or 2")
        return cls(
            controller_key=key,
            controller_role=role,
            state_delta=raw_delta,
        )

    def to_attach_frame(self) -> str:
        """Encode the data-channel attach frame (the offerer side).

        The inverse of :meth:`from_attach_frame`: an offerer sends this as
        the first text frame on each data channel.
        """
        return json.dumps(
            {
                "type": "attach",
                "controller_key": self.controller_key,
                "role": self.controller_role,
                "state_delta": self.state_delta,
            }
        )
