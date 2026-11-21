"""Wire contract of the WebRTC answerer HTTP API (K6, MOR-272).

``POST /api/v1/transport/webrtc/offer``,
``POST /api/v1/transport/webrtc/ice-candidate`` and
``POST /api/v1/transport/webrtc/close`` bodies, their answers and
error envelopes, plus the ``webrtc`` block of ``GET /api/v1/capabilities``.
The server parses requests and builds responses through these types, so
an out-of-process offerer or station agent that uses them (re-exported
from ``rigplane``) speaks exactly what the answerer reads and writes.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Self, cast, get_args

from .ice_gathering import IceServer, parse_ice_servers  # noqa: TID251

__all__ = [
    "IceCandidateInit",
    "WebRtcAnswer",
    "WebRtcClose",
    "WebRtcCloseResult",
    "WebRtcErrorCode",
    "WebRtcIceCandidate",
    "WebRtcOffer",
    "WebRtcRequestError",
    "WebRtcTransportStatus",
]

#: Every ``code`` the answerer routes answer an error with.
WebRtcErrorCode = Literal[
    "webrtc_unavailable",
    "missing_sdp",
    "invalid_ice_servers",
    "sdp_error",
    "missing_session",
    "invalid_candidate",
    "ice_error",
    "invalid_session",
    "close_error",
]


class WebRtcRequestError(ValueError):
    """An answerer error: ``{"status": "error", "code", "message"}``."""

    def __init__(self, code: WebRtcErrorCode, message: str) -> None:
        super().__init__(message)
        self.code: WebRtcErrorCode = code
        self.message = message

    def to_json(self) -> dict[str, str]:
        return {"status": "error", "code": self.code, "message": self.message}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        code = payload.get("code")
        message = payload.get("message")
        if (
            payload.get("status") != "error"
            or code not in get_args(WebRtcErrorCode)
            or not isinstance(message, str)
        ):
            raise ValueError(f"not a WebRTC error envelope: {dict(payload)!r}")
        return cls(cast(WebRtcErrorCode, code), message)


@dataclass(frozen=True, slots=True)
class WebRtcOffer:
    """``POST /api/v1/transport/webrtc/offer`` request body.

    ``ice_servers`` empty means host-only gathering on the answerer.
    """

    sdp: str
    type: str = "offer"
    ice_servers: list[IceServer] = field(default_factory=list)

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        sdp = payload.get("sdp")
        if not isinstance(sdp, str) or not sdp.strip():
            raise WebRtcRequestError("missing_sdp", "Field 'sdp' required.")
        try:
            ice_servers = parse_ice_servers(payload.get("ice_servers"))
        except ValueError as exc:
            raise WebRtcRequestError("invalid_ice_servers", str(exc)) from exc
        offer_type = payload.get("type", "offer")
        if not isinstance(offer_type, str):
            raise WebRtcRequestError("sdp_error", "Field 'type' must be a string.")
        return cls(sdp=sdp, type=offer_type, ice_servers=ice_servers)

    def to_json(self) -> dict[str, Any]:
        return {
            "sdp": self.sdp,
            "type": self.type,
            "ice_servers": [
                {
                    "urls": list(s.urls),
                    "username": s.username,
                    "credential": s.credential,
                }
                for s in self.ice_servers
            ],
        }


@dataclass(frozen=True, slots=True)
class WebRtcAnswer:
    """Successful offer response: the answer SDP and the session id."""

    session_id: str
    sdp: str
    type: str

    def to_json(self) -> dict[str, str]:
        return {
            "status": "ok",
            "sessionId": self.session_id,
            "sdp": self.sdp,
            "type": self.type,
        }

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        session_id = payload.get("sessionId")
        sdp = payload.get("sdp")
        answer_type = payload.get("type")
        if not (
            isinstance(session_id, str)
            and isinstance(sdp, str)
            and isinstance(answer_type, str)
        ):
            raise ValueError(f"not a WebRTC answer: {dict(payload)!r}")
        return cls(session_id=session_id, sdp=sdp, type=answer_type)


def _invalid_candidate(detail: str) -> WebRtcRequestError:
    return WebRtcRequestError("invalid_candidate", detail)


@dataclass(frozen=True, slots=True)
class IceCandidateInit:
    """One trickled candidate: the W3C ``RTCIceCandidateInit`` fields.

    The same three fields as the Tower signalling ``IceCandidateInitV2``.
    """

    candidate: str
    sdpMid: str | None = None  # noqa: N815 - W3C wire name
    sdpMLineIndex: int | None = None  # noqa: N815 - W3C wire name

    @classmethod
    def from_json(cls, value: object) -> Self | None:
        """Parse a wire candidate; ``None`` means end of candidates.

        ``null``, ``{}`` and an empty ``candidate`` string all end the
        trickle (browsers send each form). Other keys a browser adds
        (``usernameFragment``) are ignored. A mistyped field raises
        :class:`WebRtcRequestError` ``invalid_candidate``.
        """
        if value is None:
            return None
        if not isinstance(value, dict):
            raise _invalid_candidate("'candidate' must be an object or null.")
        text = value.get("candidate", "")
        if not isinstance(text, str):
            raise _invalid_candidate("'candidate.candidate' must be a string.")
        if not text:
            return None
        mid = value.get("sdpMid")
        if mid is not None and not isinstance(mid, str):
            raise _invalid_candidate("'candidate.sdpMid' must be a string or null.")
        index = value.get("sdpMLineIndex")
        if index is not None and (type(index) is not int or index < 0):
            raise _invalid_candidate(
                "'candidate.sdpMLineIndex' must be a non-negative integer or null."
            )
        return cls(candidate=text, sdpMid=mid, sdpMLineIndex=index)

    def to_json(self) -> dict[str, Any]:
        return {
            "candidate": self.candidate,
            "sdpMid": self.sdpMid,
            "sdpMLineIndex": self.sdpMLineIndex,
        }


@dataclass(frozen=True, slots=True)
class WebRtcIceCandidate:
    """``POST /api/v1/transport/webrtc/ice-candidate`` request body.

    ``candidate`` ``None`` ends the trickle. The success response is
    ``{"status": "ok"}``.
    """

    session_id: str
    candidate: IceCandidateInit | None

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        session_id = payload.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise WebRtcRequestError("missing_session", "Field 'sessionId' required.")
        return cls(
            session_id=session_id,
            candidate=IceCandidateInit.from_json(payload.get("candidate")),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "sessionId": self.session_id,
            "candidate": None if self.candidate is None else self.candidate.to_json(),
        }


@dataclass(frozen=True, slots=True)
class WebRtcClose:
    """``POST /api/v1/transport/webrtc/close`` request body.

    ``session_id`` ``None`` (absent or ``null``) closes every live
    session; an id closes just that peer. Closing when nothing is open
    is a success that closed nothing.
    """

    session_id: str | None = None

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        session_id = payload.get("sessionId")
        if session_id is not None and not (
            isinstance(session_id, str) and session_id
        ):
            raise WebRtcRequestError(
                "invalid_session", "Field 'sessionId' must be a non-empty string."
            )
        return cls(session_id=session_id)

    def to_json(self) -> dict[str, str | None]:
        return {"sessionId": self.session_id}


@dataclass(frozen=True, slots=True)
class WebRtcCloseResult:
    """Successful close response: the ids of the sessions torn down."""

    closed: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {"status": "ok", "closed": list(self.closed)}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        closed = payload.get("closed")
        if payload.get("status") != "ok" or not (
            isinstance(closed, list) and all(isinstance(item, str) for item in closed)
        ):
            raise ValueError(f"not a WebRTC close result: {dict(payload)!r}")
        return cls(closed=tuple(closed))


@dataclass(frozen=True, slots=True)
class WebRtcTransportStatus:
    """The ``webrtc`` block of ``GET /api/v1/capabilities``.

    ``available``: the ``[webrtc]`` extra is installed; ``enabled``: the
    operator turned the answerer on (``--webrtc``). The answerer routes
    accept offers only when both are true.
    """

    available: bool
    enabled: bool

    def to_json(self) -> dict[str, bool]:
        return {"available": self.available, "enabled": self.enabled}

    @classmethod
    def from_json(cls, payload: Mapping[str, object]) -> Self:
        available = payload.get("available")
        enabled = payload.get("enabled")
        if type(available) is not bool or type(enabled) is not bool:
            raise ValueError(f"not a webrtc capabilities block: {dict(payload)!r}")
        return cls(available=available, enabled=enabled)
