"""WebRTC signaling support for icom-lan.

Provides SDP offer/answer handling for low-latency audio delivery via
WebRTC.  The ``aiortc`` library is an optional dependency — when absent,
the module gracefully reports unavailability so clients can fall back to
the existing WebSocket audio path.

Usage from the web server::

    from .rtc import webrtc_available, handle_rtc_offer

    if webrtc_available():
        answer = await handle_rtc_offer(sdp_offer, radio)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..radio_protocol import Radio

__all__ = [
    "webrtc_available",
    "handle_rtc_offer",
    "rtc_capability_info",
]

logger = logging.getLogger(__name__)

_INSTALL_HINT = "WebRTC backend unavailable; install icom-lan[webrtc]."

# ---------------------------------------------------------------------------
# Lazy aiortc availability check
# ---------------------------------------------------------------------------

_aiortc_checked: bool = False
_aiortc_ok: bool = False


def webrtc_available() -> bool:
    """Return True if the aiortc library is importable."""
    global _aiortc_checked, _aiortc_ok  # noqa: PLW0603
    if not _aiortc_checked:
        try:
            import importlib

            importlib.import_module("aiortc")
            _aiortc_ok = True
        except ImportError:
            _aiortc_ok = False
        _aiortc_checked = True
    return _aiortc_ok


def rtc_capability_info() -> dict[str, Any]:
    """Return WebRTC capability metadata for /api/v1/info and /capabilities."""
    available = webrtc_available()
    return {
        "available": available,
        "reason": None if available else "aiortc not installed",
        "supportedDirections": ["rx"] if available else [],
    }


# ---------------------------------------------------------------------------
# SDP offer/answer
# ---------------------------------------------------------------------------


async def handle_rtc_offer(
    offer_sdp: str,
    offer_type: str,
    radio: "Radio | None",
) -> dict[str, Any]:
    """Process an SDP offer and return an answer or error dict.

    Returns a dict with either:
      {"status": "ok", "sdp": <answer_sdp>, "type": "answer"}
    or:
      {"status": "error", "code": <str>, "message": <str>}
    """
    if not webrtc_available():
        return {
            "status": "error",
            "code": "webrtc_unavailable",
            "message": _INSTALL_HINT,
        }

    from ..radio_protocol import AudioCapable

    if radio is None or not isinstance(radio, AudioCapable):
        return {
            "status": "error",
            "code": "audio_unavailable",
            "message": "Radio audio is not available.",
        }

    # --- aiortc is available; create a peer connection ---
    try:
        from aiortc import (  # type: ignore[import-not-found]
            RTCPeerConnection,
            RTCSessionDescription,
        )
    except ImportError:
        # Race / broken install — should not happen after webrtc_available()
        return {
            "status": "error",
            "code": "webrtc_unavailable",
            "message": _INSTALL_HINT,
        }

    pc = RTCPeerConnection()

    # Add a single audio transceiver in sendonly mode (RX audio to browser).
    # Actual media track wiring is a follow-up; for now we create a valid
    # SDP answer so clients can verify the signaling contract.
    pc.addTransceiver("audio", direction="sendonly")

    try:
        offer = RTCSessionDescription(sdp=offer_sdp, type=offer_type)
        await pc.setRemoteDescription(offer)
        answer = await pc.createAnswer()
        await pc.setLocalDescription(answer)
    except Exception as exc:
        logger.warning("WebRTC offer processing failed: %s", exc)
        await pc.close()
        return {
            "status": "error",
            "code": "sdp_error",
            "message": f"Failed to process SDP offer: {exc}",
        }

    local_desc = pc.localDescription
    if local_desc is None:
        await pc.close()
        return {
            "status": "error",
            "code": "sdp_error",
            "message": "Failed to generate SDP answer.",
        }

    logger.info("WebRTC signaling: answer generated for peer")

    # NOTE: The peer connection is created but no audio track is wired yet.
    # Full media relay from radio.audio_bus → WebRTC track is a follow-up.
    # For now we close immediately after answering — the client will see
    # ICE connection fail, which is expected for this scaffolding slice.
    # TODO(#104): wire audio track and manage PC lifecycle
    await pc.close()

    return {
        "status": "ok",
        "sdp": local_desc.sdp,
        "type": local_desc.type,
        "note": "Signaling scaffold only; media track not yet wired.",
    }
