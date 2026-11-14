"""One attach-parameter contract for every client-channel transport.

The WebSocket upgrade and the WebRTC data-channel dispatch attach a
client channel through the same mechanism
(``WebServer.serve_client_channel``). Both adapters parse their
transport framing into :class:`ClientChannelParams` first — the
WebSocket query plus ``Sec-WebSocket-Protocol`` header, or the data
channel's first-text-frame attach frame — so ticket capture, handler
construction, caps, keepalive and cleanup never diverge again.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal, Self

from ..runtime.controller_authority import ControllerError

__all__ = ["ChannelKind", "ClientChannelParams"]

#: Client channel kinds. ``rigctl`` exists only on the WebSocket lane;
#: data channels carry ``control`` / ``scope`` / ``audio`` labels.
ChannelKind = Literal["control", "scope", "audio", "rigctl"]


@dataclass(frozen=True, slots=True)
class ClientChannelParams:
    """Transport-independent client channel parameters.

    ``controller_key`` is the session key the WebSocket lane reads from
    ``Sec-WebSocket-Protocol`` (``rigplane-controller-v1.<key>``) and the
    data-channel lane reads from its attach frame; ``None`` means the
    channel names no session (local-mode sessionless capture).
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

        Exactly the historical upgrade rules: one
        ``rigplane-controller-v1.*`` protocol is required, the
        ``controller_role`` query value must be singular on
        ``/api/v1/ws`` (``"auxiliary"`` is forced everywhere else), and
        ``?state_delta=2`` selects leaf deltas. Raises
        :class:`ControllerError` with the same codes the upgrade path
        always answered with.
        """
        if len(protocols) != 1:
            raise ControllerError("controller_invalid")
        if path == "/api/v1/ws":
            role = (query or {}).get("controller_role", ["primary"])
            if len(role) != 1:
                raise ControllerError("invalid_request", 400)
            role_value = role[0]
        else:
            role_value = "auxiliary"
        return cls(
            controller_key=protocols[0].split(".", 1)[1],
            controller_role=role_value,
            state_delta=2 if (query or {}).get("state_delta") == ["2"] else 1,
        )

    @classmethod
    def from_attach_frame(cls, payload: bytes) -> Self:
        """Parse a data channel's first-text-frame attach frame.

        The frame is ``{"type": "attach", "controller_key": str | null,
        "state_delta": 1 | 2, "role": str}``; ``controller_key`` and
        ``role`` default to ``None``/``"primary"`` when absent, and
        ``state_delta`` defaults to ``2`` (leaf deltas). Anything else —
        bad JSON, a non-object, a wrong ``type``, or mistyped fields —
        raises :class:`ValueError` and the adapter closes the channel.
        """
        try:
            obj = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"invalid attach frame: {exc}") from exc
        if not isinstance(obj, dict) or obj.get("type") != "attach":
            raise ValueError("attach frame must carry {'type': 'attach', ...}")
        key = obj.get("controller_key")
        if key is not None and type(key) is not str:
            raise ValueError("attach frame 'controller_key' must be str or null")
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
