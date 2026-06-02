"""Stateless SDP-exchange entrypoint + ICE trickle for the WebRTC transport.

This is the *answerer* side of a stand-alone / lab WebRTC session. It is a
sibling to the throwaway scaffold in :mod:`rigplane.web.rtc` (A2.4 / MOR-308
removes that scaffold) and does **no** Tower signaling — the broker path is
A3 / pro. The flow here is a plain HTTP SDP exchange used to drive and test
the A2.1 connection class + the A2.2 multi-channel set against the real
handlers.

Negotiation contract (one ``RTCPeerConnection`` per peer):

* The browser (offerer) creates the three DataChannels — ``control`` (ordered/
  reliable), ``scope`` and ``audio`` (unordered, lossy) — exactly as the A2.2
  factories configure them, then POSTs its SDP offer.
* :meth:`WebRtcSessionManager.negotiate` creates the answerer
  ``RTCPeerConnection``, registers an ``on("datachannel")`` callback that wraps
  each inbound channel in :class:`WebRtcDataChannelConnection` (the A2.1 seam)
  and dispatches it — by label — into the *unchanged* ``ControlHandler`` /
  ``ScopeHandler`` / ``AudioHandler`` (the A2.1 + A2.2 wiring), then
  ``setRemoteDescription`` → ``createAnswer`` → ``setLocalDescription`` and
  returns the answer SDP plus a session id.
* Unlike the scaffold, the peer connection is **kept alive** for the session;
  the handler tasks run until the PC closes. :meth:`close_all` tears every
  live session down (used on server shutdown).

ICE trickle: :meth:`add_ice_candidate` accepts a trickled candidate for a
known session id (the minimal sane contract — a POST keyed by session id).
Server→client trickle is not needed for the local/lab exchange: aiortc
gathers candidates synchronously into the SDP answer, so the answer already
carries the server's candidates.

``aiortc`` is optional (the ``[webrtc]`` extra). This module imports it lazily
so importing the module never fails; :func:`webrtc_available` reports
availability and the HTTP layer degrades to a clean "unavailable" response.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING, Any, Final

from ... import __version__
from .webrtc import (  # noqa: TID251
    WebRtcDataChannelConnection,
    webrtc_available,
)

if TYPE_CHECKING:
    from aiortc import (  # type: ignore[import-not-found]
        RTCDataChannel,
        RTCPeerConnection,
    )

    from ...radio_protocol import Radio
    from ..server import WebServer  # noqa: TID251

__all__ = [
    "WebRtcSessionError",
    "WebRtcSessionManager",
    "webrtc_available",
]

logger = logging.getLogger(__name__)

_INSTALL_HINT: Final = "WebRTC backend unavailable; install rigplane[webrtc]."

# Labels we dispatch into handlers; any other channel is ignored (logged).
_CONTROL: Final = "control"
_SCOPE: Final = "scope"
_AUDIO: Final = "audio"


class WebRtcSessionError(RuntimeError):
    """Raised when a session operation fails (bad SDP, unknown session)."""


class _Session:
    """One live peer: its ``RTCPeerConnection`` plus spawned handler tasks."""

    def __init__(self, pc: RTCPeerConnection) -> None:
        self.pc = pc
        self.tasks: list[asyncio.Task[None]] = []

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        await self.pc.close()


class WebRtcSessionManager:
    """Stateless-per-request SDP exchange that keeps live sessions.

    "Stateless" is from the *client's* perspective: each offer fully describes
    its session and gets one answer back. The manager itself holds the live
    ``RTCPeerConnection`` set so it can route trickled ICE and tear sessions
    down on shutdown.
    """

    def __init__(
        self,
        radio: "Radio | None",
        server: "WebServer | None",
        radio_model: str,
    ) -> None:
        self._radio = radio
        self._server = server
        self._radio_model = radio_model
        self._sessions: dict[str, _Session] = {}

    async def negotiate(self, offer_sdp: str, offer_type: str) -> dict[str, Any]:
        """Process an SDP offer and return ``{sessionId, sdp, type}``.

        Raises :class:`WebRtcSessionError` if WebRTC is unavailable or the SDP
        cannot be processed.
        """
        if not webrtc_available():
            raise WebRtcSessionError(_INSTALL_HINT)

        try:
            from aiortc import (  # type: ignore[import-not-found]
                RTCPeerConnection,
                RTCSessionDescription,
            )
        except ImportError as exc:  # pragma: no cover - race/broken install
            raise WebRtcSessionError(_INSTALL_HINT) from exc

        pc = RTCPeerConnection()
        session_id = uuid.uuid4().hex
        session = _Session(pc)
        self._sessions[session_id] = session

        @pc.on("datachannel")  # type: ignore[misc, no-untyped-call, untyped-decorator]
        def _on_datachannel(channel: RTCDataChannel) -> None:
            self._dispatch_channel(session, channel)

        @pc.on("connectionstatechange")  # type: ignore[misc, no-untyped-call, untyped-decorator]
        async def _on_state() -> None:
            if pc.connectionState in ("closed", "failed"):
                await self._drop(session_id)

        try:
            offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
            await pc.setRemoteDescription(offer)
            answer = await pc.createAnswer()
            await pc.setLocalDescription(answer)
        except Exception as exc:
            logger.warning("WebRTC session negotiation failed: %s", exc)
            await self._drop(session_id)
            raise WebRtcSessionError(f"Failed to process SDP offer: {exc}") from exc

        local_desc = pc.localDescription
        if local_desc is None:  # pragma: no cover - defensive
            await self._drop(session_id)
            raise WebRtcSessionError("Failed to generate SDP answer.")

        logger.info("WebRTC session %s negotiated (answer generated)", session_id)
        return {
            "sessionId": session_id,
            "sdp": local_desc.sdp,
            "type": local_desc.type,
        }

    async def add_ice_candidate(
        self, session_id: str, candidate: dict[str, Any] | None
    ) -> None:
        """Add a trickled ICE candidate to ``session_id``.

        A ``None`` / empty candidate marks end-of-candidates (a no-op for
        aiortc). Raises :class:`WebRtcSessionError` for an unknown session.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise WebRtcSessionError(f"Unknown session: {session_id}")

        if not candidate or not candidate.get("candidate"):
            # End-of-candidates sentinel; nothing to add.
            return

        from aiortc import (  # type: ignore[import-not-found]
            RTCIceCandidate,
        )
        from aiortc.sdp import (  # type: ignore[import-not-found]
            candidate_from_sdp,
        )

        try:
            parsed: RTCIceCandidate = candidate_from_sdp(
                str(candidate["candidate"]).split(":", 1)[-1]
                if str(candidate["candidate"]).startswith("candidate:")
                else str(candidate["candidate"])
            )
            parsed.sdpMid = candidate.get("sdpMid")
            sdp_mline = candidate.get("sdpMLineIndex")
            parsed.sdpMLineIndex = int(sdp_mline) if sdp_mline is not None else None
            await session.pc.addIceCandidate(parsed)
        except Exception as exc:
            raise WebRtcSessionError(f"Failed to add ICE candidate: {exc}") from exc

    async def close_all(self) -> None:
        """Tear down every live session (server shutdown)."""
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.debug("session close failed", exc_info=True)

    @property
    def active_session_ids(self) -> list[str]:
        """Session ids currently held (test/diagnostic visibility)."""
        return list(self._sessions)

    async def _drop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()

    def _dispatch_channel(self, session: _Session, channel: RTCDataChannel) -> None:
        """Wrap an inbound channel and spawn its handler by label."""
        # Local imports keep the module import-light and avoid a hard handler
        # dependency at module load (mirrors the lazy aiortc import).
        from ..handlers import (  # noqa: TID251
            AudioHandler,
            ControlHandler,
            ScopeHandler,
        )

        conn = WebRtcDataChannelConnection(channel, session.pc)
        label = channel.label
        handler: Any
        if label == _CONTROL:
            handler = ControlHandler(
                conn,
                self._radio,
                __version__,
                self._radio_model,
                server=self._server,
                read_only=(
                    self._server._config.read_only
                    if self._server is not None
                    else False
                ),
            )
        elif label == _SCOPE:
            handler = ScopeHandler(conn, self._radio, server=self._server)
        elif label == _AUDIO:
            broadcaster = (
                self._server._audio_broadcaster if self._server is not None else None
            )
            handler = AudioHandler(conn, self._radio, broadcaster)
        else:
            logger.info("WebRTC: ignoring unknown data channel %r", label)
            return

        session.tasks.append(asyncio.ensure_future(handler.run()))
