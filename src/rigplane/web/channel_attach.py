"""One attach-parameter contract for every client-channel transport.

The WebSocket upgrade and the WebRTC data-channel dispatch attach a
client channel through the same mechanism
(``WebServer.serve_client_channel``). Both adapters parse their
transport framing into :class:`ClientChannelParams` first — the
WebSocket query plus ``Sec-WebSocket-Protocol`` header, or the data
channel's first-text-frame attach frame — so ticket capture, handler
construction, caps, keepalive and cleanup never diverge again.

K5, attach order on data channels (MOR-272): every channel kind attaches
on its own, and every attach frame travels on a reliable ordered channel
(see :data:`DATA_CHANNELS`: the text frames of ``scope``/``audio`` ride
their reliable ``*-text`` companion), so an attach is never lost and
never resent. The client attaches ``control`` first (its ``hello``
proves the attach); the server holds a ``scope``/``audio`` attach that
arrives before its session's control attach, re-checking it until the
control attach lands or the 5 s attach bound closes the channel.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Final, Literal, Self, get_args

from ..runtime.controller_authority import ControllerError

__all__ = [
    "ATTACH_ACK_KINDS",
    "ATTACH_ACK_VERSION",
    "DATA_CHANNELS",
    "RIGCTL_MAX_FRAME_BYTES",
    "RIGCTL_VERSIONS",
    "ChannelAttachedAck",
    "ChannelKind",
    "ClientChannelParams",
    "DataChannelLabel",
    "DataChannelSpec",
    "FrameCarriage",
    "RigctlAttachErrorCode",
    "RigctlAttachErrorV1",
    "TransportKind",
    "data_channel_spec",
    "data_channels_for",
    "decode_rigctl_attach_result",
]

#: Client channel kinds shared by the transport adapters.
ChannelKind = Literal["control", "scope", "audio", "api", "rigctl"]

#: Every WebRTC data-channel label (the K5 channel set).
DataChannelLabel = Literal[
    "control", "api", "scope", "scope-text", "audio", "audio-text", "rigctl"
]

#: Which frames a data channel carries: ``all`` (text and binary, one
#: channel per kind), ``binary`` (the lossy half of a split kind) or
#: ``text`` (the reliable half of a split kind).
FrameCarriage = Literal["all", "binary", "text"]


@dataclass(frozen=True, slots=True)
class DataChannelSpec:
    """One WebRTC data channel of the K5 channel set.

    ``max_retransmits`` ``None`` means fully reliable. A kind with one
    ``all`` channel is served over that channel alone; a split kind
    (``scope``, ``audio``) has a lossy ``binary`` channel and a reliable
    ordered ``text`` companion, which both ends merge into the ONE
    :class:`~rigplane.web.transport.connection.Connection` its handler
    sees: text goes out on the companion, bytes on the lossy channel,
    and ``recv`` yields from both. Text commands (``audio_start``, scope
    subscriptions, the attach frame) can therefore never be lost; only
    time-sensitive binary frames tolerate loss.
    """

    label: DataChannelLabel
    kind: ChannelKind
    ordered: bool
    max_retransmits: int | None
    carries: FrameCarriage


#: Labels the answerer recognizes. The offerer opens rigctl on demand
#: after negotiating its version on the established peer.
DATA_CHANNELS: Final[tuple[DataChannelSpec, ...]] = (
    DataChannelSpec("control", "control", True, None, "all"),
    DataChannelSpec("api", "api", True, None, "all"),
    DataChannelSpec("scope", "scope", False, 0, "binary"),
    DataChannelSpec("scope-text", "scope", True, None, "text"),
    DataChannelSpec("audio", "audio", False, 0, "binary"),
    DataChannelSpec("audio-text", "audio", True, None, "text"),
    DataChannelSpec("rigctl", "rigctl", True, None, "all"),
)


def data_channel_spec(label: str) -> DataChannelSpec | None:
    """The :data:`DATA_CHANNELS` entry for ``label``; ``None`` if unknown."""
    return next((spec for spec in DATA_CHANNELS if spec.label == label), None)


def data_channels_for(kind: str) -> tuple[DataChannelSpec, ...]:
    """The channels that carry ``kind`` (one ``all``, or binary + text)."""
    return tuple(spec for spec in DATA_CHANNELS if spec.kind == kind)


#: Version of the attach acknowledgement frame (a versioned wire change).
ATTACH_ACK_VERSION: Final = 1

#: Kinds which acknowledge a data-channel attach. Rigctl additionally
#: waits for the rigctld connection to be admitted before sending its ack.
ATTACH_ACK_KINDS: Final = frozenset({"scope", "audio", "rigctl"})
RIGCTL_VERSIONS: Final = (1,)
RIGCTL_MAX_FRAME_BYTES: Final = 16 * 1024

RigctlAttachErrorCode = Literal[
    "unsupported_version",
    "invalid_attach",
    "controller_invalid",
    "controller_primary_required",
    "rigctl_busy",
    "rigctl_unavailable",
]


def _attach_object(payload: str | bytes) -> dict[str, object]:
    try:
        obj = json.loads(
            payload.decode("utf-8") if isinstance(payload, bytes) else payload
        )
    except (ValueError, RecursionError) as exc:
        raise ValueError("invalid attach JSON") from exc
    if not isinstance(obj, dict) or not isinstance(obj.get("type"), str):
        raise ValueError("attach frame needs a string 'type'")
    return obj


@dataclass(frozen=True, slots=True)
class ChannelAttachedAck:
    """The attach ack: one text frame sent after a scope/audio/rigctl
    data-channel attach and before the first handler byte, so the client
    opens its transport only once the server serves the channel; control,
    api and the WebSocket lane never send one."""

    channel: str
    version: int = ATTACH_ACK_VERSION

    def encode(self) -> str:
        if (
            type(self.version) is not int
            or self.version != ATTACH_ACK_VERSION
            or self.channel not in ATTACH_ACK_KINDS
        ):
            raise ValueError("unsupported attach ack version or channel")
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


@dataclass(frozen=True, slots=True)
class RigctlAttachErrorV1:
    """A CAT attach refusal. Receivers preserve future codes verbatim."""

    code: str
    version: int = 1
    channel: str = "rigctl"

    def encode(self) -> str:
        if (
            type(self.version) is not int
            or self.version != 1
            or self.channel != "rigctl"
            or self.code not in get_args(RigctlAttachErrorCode)
        ):
            raise ValueError("unsupported rigctl attach error")
        return json.dumps(
            {"type": "attach_error", "v": 1, "channel": "rigctl", "code": self.code}
        )


def decode_rigctl_attach_result(
    payload: str,
) -> ChannelAttachedAck | RigctlAttachErrorV1 | None:
    """Read RTC text during attach. Skip unknown kinds and preserve values.

    Callers match the returned version and channel against their request.
    """
    obj = _attach_object(payload)
    if obj["type"] == "attached":
        return ChannelAttachedAck.from_frame(payload)
    if obj["type"] != "attach_error":
        return None
    version, channel, code = obj.get("v"), obj.get("channel"), obj.get("code")
    if type(version) is not int or type(channel) is not str or type(code) is not str:
        raise ValueError("invalid rigctl attach error fields")
    return RigctlAttachErrorV1(code, version, channel)


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
    rigctl_version: int | None = None

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
        obj = _attach_object(payload)
        if obj["type"] != "attach":
            raise ValueError("attach frame must carry {'type': 'attach', ...}")
        return cls._from_object(obj, require_key=require_key)

    @classmethod
    def from_rigctl_attach_frame(cls, payload: bytes) -> Self | None:
        """Parse a CAT attach, skipping unknown kinds during the handshake."""
        obj = _attach_object(payload)
        if obj["type"] != "attach":
            return None
        version = obj.get("v")
        if type(version) is not int:
            raise ValueError("rigctl attach needs an int 'v'")
        return cls._from_object(obj, require_key=True, rigctl_version=version)

    @classmethod
    def _from_object(
        cls,
        obj: dict[str, object],
        *,
        require_key: bool,
        rigctl_version: int | None = None,
    ) -> Self:
        key = obj.get("controller_key")
        if type(key) is not str and (key is not None or require_key):
            raise ValueError("attach frame 'controller_key' must be a session key")
        role = obj.get("role", "primary" if rigctl_version is None else "auxiliary")
        if type(role) is not str:
            raise ValueError("attach frame 'role' must be a string")
        raw_delta = obj.get("state_delta", 2)
        if type(raw_delta) is not int or (
            rigctl_version is None and raw_delta not in (1, 2)
        ):
            raise ValueError("attach frame 'state_delta' must be 1 or 2")
        return cls(
            controller_key=key,
            controller_role=role,
            state_delta=raw_delta,
            rigctl_version=rigctl_version,
        )

    def to_attach_frame(self) -> str:
        """Encode the data-channel attach frame (the offerer side).

        The inverse of :meth:`from_attach_frame`: an offerer sends this as
        the first text frame on each data channel.
        """
        obj: dict[str, object] = {
            "type": "attach",
            "controller_key": self.controller_key,
            "role": self.controller_role,
            "state_delta": self.state_delta,
        }
        if self.rigctl_version is not None:
            if (
                type(self.rigctl_version) is not int
                or self.rigctl_version not in RIGCTL_VERSIONS
                or type(self.controller_key) is not str
                or not self.controller_key
                or self.controller_role != "auxiliary"
                or type(self.state_delta) is not int
                or self.state_delta not in (1, 2)
            ):
                raise ValueError("invalid outbound rigctl attach")
            obj["v"] = self.rigctl_version
            if self.state_delta == 2:
                del obj["state_delta"]
        return json.dumps(obj)
