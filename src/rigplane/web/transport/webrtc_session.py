"""Stateless SDP-exchange entrypoint + ICE trickle for the WebRTC transport.

This is the *answerer* side of a stand-alone / lab WebRTC session. It does
**no** Tower signaling — the broker path is
A3 / pro. The flow here is a plain HTTP SDP exchange used to drive and test
the A2.1 connection class + the A2.2 multi-channel set against the real
handlers.

Negotiation contract (one ``RTCPeerConnection`` per peer):

* The offerer creates the K5 channel set
  (:data:`~rigplane.web.channel_attach.DATA_CHANNELS`: ``control`` and
  ``api`` ordered/reliable; ``scope`` and ``audio`` lossy for binary frames,
  each with its reliable ordered ``*-text`` companion for text frames)
  exactly as the factories configure them, then POSTs its SDP offer.
* :meth:`WebRtcSessionManager.negotiate` creates the answerer
  ``RTCPeerConnection``, registers an ``on("datachannel")`` callback that wraps
  each inbound channel — a split kind's two channels paired into one — in
  :class:`WebRtcDataChannelConnection` (the A2.1 seam) and serves it — by
  kind — through the single shared attach path
  (``WebServer.attach_client_channel``): the same ticket, handlers, caps,
  supervision and cleanup the WebSocket lane uses. Each channel's first text
  frame must be the attach frame; without it the channel closes. Then
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
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING, Any, Final, cast

from ...runtime.controller_authority import ControllerError  # noqa: TID251
from ...ws import WS_OP_TEXT  # noqa: TID251
from ..channel_attach import (  # noqa: TID251
    ATTACH_ACK_KINDS,
    ChannelKind,
    ClientChannelParams,
    data_channel_spec,
)
from .webrtc_contract import IceCandidateInit, WebRtcAnswer  # noqa: TID251
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
    from .ice_gathering import IceGatheringPolicy, IceServer  # noqa: TID251

__all__ = [
    "WebRtcSessionError",
    "WebRtcSessionManager",
    "webrtc_available",
]

logger = logging.getLogger(__name__)

_INSTALL_HINT: Final = "WebRTC backend unavailable; install rigplane[webrtc]."

# A data channel that never sends its attach frame is closed after this;
# a split kind's half whose partner never arrives is closed after it too.
_ATTACH_TIMEOUT_S: Final = 5.0

# How often a scope/audio attach that arrived before its session's
# control attach is re-checked (K5: held, never closed, until control
# attaches or the attach bound passes).
_EARLY_ATTACH_RECHECK_S: Final = 0.05


class WebRtcSessionError(RuntimeError):
    """Raised when a session operation fails (bad SDP, unknown session)."""


class _Session:
    """One live peer: its ``RTCPeerConnection`` plus spawned handler tasks."""

    def __init__(self, pc: RTCPeerConnection) -> None:
        self.pc = pc
        self.tasks: list[asyncio.Task[None]] = []
        # Controller session owners attached through this peer's channels.
        # A ``disconnected``/``failed`` peer fences these immediately.
        self.controller_owners: set[str] = set()
        # The split-kind half (scope/audio) waiting for its partner, at most
        # ONE per (kind, carriage); it already queues its frames.
        self.unpaired: dict[tuple[str, str], WebRtcDataChannelConnection] = {}

    async def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        for task in self.tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Shielded: aiortc resolves its internal "closed" future only at the
        # end of pc teardown, so a cancel landing here would poison every
        # later ``pc.close()`` await (MOR-1431). The shielded teardown still
        # runs to completion; the CancelledError is re-raised afterwards.
        await asyncio.shield(self.pc.close())


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
        *,
        gather_policy: "IceGatheringPolicy | None" = None,
    ) -> None:
        self._radio = radio
        self._server = server
        self._radio_model = radio_model
        self._gather_policy = gather_policy
        self._sessions: dict[str, _Session] = {}
        # Process-wide, installed once: the hooks read each negotiation's
        # policy from a ContextVar, so concurrent negotiations stay separate.
        from .ice_gathering import install_ice_gathering_policy  # noqa: TID251

        install_ice_gathering_policy()

    async def negotiate(
        self,
        offer_sdp: str,
        offer_type: str,
        ice_servers: list[IceServer] | None = None,
    ) -> dict[str, Any]:
        """Process an SDP offer and return the :class:`WebRtcAnswer` wire body.

        ``ice_servers`` carries the client's STUN/TURN servers (``None`` or
        ``[]`` means host-only gathering with an explicit empty
        configuration). Gathering runs under the manager's gathering policy
        and is bounded at ``GATHER_TIMEOUT_S`` total: candidates found by
        then are used and the answer is produced from them.

        Raises :class:`WebRtcSessionError` if WebRTC is unavailable or the SDP
        cannot be processed.
        """
        if not webrtc_available():
            raise WebRtcSessionError(_INSTALL_HINT)

        try:
            from aiortc import (  # type: ignore[import-not-found]
                RTCConfiguration,
                RTCIceServer,
                RTCPeerConnection,
                RTCSessionDescription,
            )
        except ImportError as exc:  # pragma: no cover - race/broken install
            raise WebRtcSessionError(_INSTALL_HINT) from exc

        from .ice_gathering import (  # noqa: TID251
            IceGatheringPolicy,
            apply_ice_gathering_policy,
        )

        if ice_servers:
            rtc_servers = [
                RTCIceServer(
                    urls=list(server.urls),
                    username=server.username,
                    credential=server.credential,
                )
                for server in ice_servers
            ]
        else:
            rtc_servers = []
        pc = RTCPeerConnection(RTCConfiguration(iceServers=rtc_servers))
        session_id = uuid.uuid4().hex
        session = _Session(pc)
        self._sessions[session_id] = session

        @pc.on("datachannel")  # type: ignore[misc, no-untyped-call, untyped-decorator]
        def _on_datachannel(channel: RTCDataChannel) -> None:
            self._dispatch_channel(session_id, session, channel)

        @pc.on("connectionstatechange")  # type: ignore[misc, no-untyped-call, untyped-decorator]
        async def _on_state() -> None:
            if pc.connectionState in ("disconnected", "failed"):
                self._fence_peer(session_id)
            if pc.connectionState in ("closed", "failed"):
                await self._drop(session_id)

        try:
            policy = (
                self._gather_policy
                if self._gather_policy is not None
                else IceGatheringPolicy()
            )
            with apply_ice_gathering_policy(policy):
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
        return WebRtcAnswer(
            session_id=session_id, sdp=local_desc.sdp, type=local_desc.type
        ).to_json()

    async def add_ice_candidate(
        self, session_id: str, candidate: IceCandidateInit | None
    ) -> None:
        """Add a trickled ICE candidate to ``session_id``.

        ``None`` marks end-of-candidates (a no-op for aiortc). Raises
        :class:`WebRtcSessionError` for an unknown session.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise WebRtcSessionError(f"Unknown session: {session_id}")

        if candidate is None:
            # End-of-candidates sentinel; nothing to add.
            return

        from aiortc import (  # type: ignore[import-not-found]
            RTCIceCandidate,
        )
        from aiortc.sdp import (  # type: ignore[import-not-found]
            candidate_from_sdp,
        )

        try:
            text = candidate.candidate
            parsed: RTCIceCandidate = candidate_from_sdp(
                text.split(":", 1)[-1] if text.startswith("candidate:") else text
            )
            parsed.sdpMid = candidate.sdpMid
            parsed.sdpMLineIndex = candidate.sdpMLineIndex
            await session.pc.addIceCandidate(parsed)
        except Exception as exc:
            raise WebRtcSessionError(f"Failed to add ICE candidate: {exc}") from exc

    async def close_all(self) -> list[str]:
        """Tear down every live session; return the ids torn down.

        The one teardown path for the whole peer set: Core shutdown and
        the ``close`` route's close-all form both end here.
        """
        session_ids = list(self._sessions)
        sessions = list(self._sessions.values())
        self._sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:  # noqa: BLE001 - best-effort shutdown
                logger.debug("session close failed", exc_info=True)
        return session_ids

    async def close_session(self, session_id: str) -> None:
        """Tear down one live session through the existing drop path.

        Raises :class:`WebRtcSessionError` for an unknown session, like
        :meth:`add_ice_candidate`.
        """
        if session_id not in self._sessions:
            raise WebRtcSessionError(f"Unknown session: {session_id}")
        await self._drop(session_id)

    @property
    def active_session_ids(self) -> list[str]:
        """Session ids currently held (test/diagnostic visibility)."""
        return list(self._sessions)

    async def _drop(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is not None:
            await session.close()
        if not self._sessions:
            # The manager holds the radio/server refs from its creation;
            # reset it once the last peer is gone so the next offer builds
            # a fresh one (mirrors the shutdown reset in web_startup).
            server = self._server
            if server is not None and server._webrtc_sessions is self:  # noqa: SLF001
                server._webrtc_sessions = None  # noqa: SLF001

    def _fence_peer(self, session_id: str) -> None:
        """Fence the controller sessions attached through this peer, now.

        A ``disconnected``/``failed`` peer connection must not hold TX
        until its controller session expires. The sessions end through
        the existing session-loss path — the same ``_end_session`` the
        expiry watchdog calls: their channels close and the transmitter
        they hold is released. Unknown or already-ended owners are a
        no-op inside ``_end_session``.
        """
        session = self._sessions.get(session_id)
        server = self._server
        if session is None or server is None:
            return
        for owner in tuple(session.controller_owners):
            try:
                server._controller._end_session(owner)  # noqa: SLF001
            except Exception:
                logger.debug("webrtc: fence failed", exc_info=True)

    def _dispatch_channel(
        self, session_id: str, session: _Session, channel: RTCDataChannel
    ) -> None:
        """Wrap an inbound channel and serve it through the shared attach.

        A ``control``/``api`` channel is served alone. A split kind's half
        (``scope``/``audio`` binary, or its ``*-text`` companion) is paired
        with the other half of its kind, whatever order they arrive in; the
        pair becomes ONE connection. A client opens one pair per kind at a
        time, so at most one half waits per (kind, carriage): a second
        simultaneous half of the same key is closed at once. The half that
        arrives first is wrapped immediately, so every frame it receives
        before its partner (the attach frame included) is kept. A half
        whose partner does not arrive within the attach bound is closed,
        and so is a channel whose label is outside the channel set.
        """
        spec = data_channel_spec(channel.label)
        if spec is None:
            logger.info("WebRTC: closing unknown data channel %r", channel.label)
            with contextlib.suppress(Exception):
                channel.close()
            return
        if spec.carries == "all":
            conn = WebRtcDataChannelConnection(channel, session.pc)
        else:
            partner_carriage = "text" if spec.carries == "binary" else "binary"
            own_key = (spec.kind, spec.carries)
            partner = session.unpaired.pop((spec.kind, partner_carriage), None)
            if partner is None or not partner.is_alive():
                waiting = session.unpaired.get(own_key)
                if waiting is not None and waiting.is_alive():
                    logger.info(
                        "WebRTC: second %r half while one waits; closing", spec.label
                    )
                    with contextlib.suppress(Exception):
                        channel.close()
                    return
                first = WebRtcDataChannelConnection(channel, session.pc)
                session.unpaired[own_key] = first
                asyncio.get_running_loop().call_later(
                    _ATTACH_TIMEOUT_S, self._close_unpaired, session, own_key, first
                )
                return
            conn = partner
            conn.add_partner(channel, partner_is_text=spec.carries == "text")
        session.tasks.append(
            asyncio.ensure_future(
                self._attach_and_serve(session_id, session, conn, spec.kind)
            )
        )

    @staticmethod
    def _close_unpaired(
        session: _Session,
        key: tuple[str, str],
        conn: WebRtcDataChannelConnection,
    ) -> None:
        """Close a split-kind half still waiting for its partner."""
        if session.unpaired.get(key) is conn:
            del session.unpaired[key]
            logger.info("WebRTC: %r channel without its pair; closing", key[0])
            conn.abort()

    async def _attach_and_serve(
        self,
        session_id: str,
        session: _Session,
        conn: WebRtcDataChannelConnection,
        kind_label: str,
    ) -> None:
        """Read the attach frame, then serve the channel through the shared path.

        The FIRST text frame must be the attach frame
        (``ClientChannelParams.from_attach_frame``); a bad, non-text, or
        missing frame (5 s) closes the channel before any handler exists.
        A refused ticket closes it too. Otherwise the channel attaches
        through ``WebServer.attach_client_channel`` — the same ticket,
        handlers, caps, supervision and cleanup the WebSocket lane uses.
        The ``api`` label alone admits a keyless attach (it is how a
        browser acquires its first key); every other label still
        requires a session key.

        K5 attach order (MOR-272): every attach frame rides a reliable
        ordered channel (a split kind's ``*-text`` companion), so it is
        never lost and never resent. A scope/audio attach that arrives
        before its session's control attach
        (``controller_primary_required``) is held without closing the
        channel and re-checked until control attaches; the 5 s attach
        bound covers the whole wait.
        """
        # Validated against the channel set by _dispatch_channel.
        kind = cast(ChannelKind, kind_label)
        server = self._server
        if server is None:
            logger.warning("WebRTC: no server for data channel %r; closing", kind)
            with contextlib.suppress(Exception):
                await conn.close(1008, "no server")
            return
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _ATTACH_TIMEOUT_S
        try:
            async with asyncio.timeout_at(deadline):
                opcode, payload = await conn.recv()
        except (TimeoutError, EOFError):
            with contextlib.suppress(Exception):
                await conn.close(1000, "no attach frame")
            return
        if opcode != WS_OP_TEXT:
            with contextlib.suppress(Exception):
                await conn.close(1003, "attach frame must be text")
            return
        try:
            params = ClientChannelParams.from_attach_frame(
                payload, require_key=kind != "api"
            )
        except ValueError as exc:
            logger.info("WebRTC: bad attach frame on %r: %s", kind, exc)
            with contextlib.suppress(Exception):
                await conn.close(1008, "bad attach frame")
            return
        while True:
            conn.serving = True  # past the attach: the handler reads freely
            try:
                await server.attach_client_channel(
                    conn,
                    kind,
                    params,
                    (session_id, 0),
                    transport="datachannel",
                    track_owners=session.controller_owners,
                )
            except ControllerError as exc:
                if (
                    kind in ATTACH_ACK_KINDS
                    and exc.code == "controller_primary_required"
                    and loop.time() + _EARLY_ATTACH_RECHECK_S < deadline
                    and conn.is_alive()
                ):
                    logger.debug("WebRTC: %r attach before control; holding", kind)
                    conn.serving = False  # held: frames stay bounded
                    await asyncio.sleep(_EARLY_ATTACH_RECHECK_S)
                    continue
                logger.info("WebRTC: data channel %r refused: %s", kind, exc.code)
                with contextlib.suppress(Exception):
                    await conn.close(1008, "refused")
            return
