"""WebRTC capability-exposure tests (MOR-308 / A2.4).

The throwaway ``POST /api/v1/rtc/offer`` signaling scaffold and its
``web/rtc.py`` module were removed in A2.4. The real WebRTC offer path is the
gated stateless SDP-exchange entrypoint covered by
``tests/test_webrtc_sdp_entrypoint.py``.

What remains here is the *capability advertising* surface that survives the
scaffold removal: the ``hasWebrtc`` flag in ``/api/v1/info`` and the ``webrtc``
block in ``/api/v1/capabilities`` (a stable API-contract field). Both source
their availability from the canonical ``webrtc_available()`` in
``rigplane.web.transport.webrtc``, re-exported into ``server`` so it can be
patched here without installing the ``[webrtc]`` extra.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from rigplane.web.server import WebConfig, WebServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Minimal asyncio.StreamWriter stand-in that captures bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


def _parse_response(writer: _FakeWriter) -> tuple[int, dict]:
    """Extract HTTP status code and JSON body from captured response."""
    text = writer.buffer.decode("ascii", errors="replace")
    status_line = text.split("\r\n", 1)[0]
    status_code = int(status_line.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    body = json.loads(text[body_start:]) if text[body_start:] else {}
    return status_code, body


def _make_radio(*, audio: bool = True, caps: set[str] | None = None):
    """Build a fake radio, optionally implementing AudioCapable."""
    from rigplane.radio_protocol import AudioCapable, ScopeCapable

    bases: list[type] = [ScopeCapable]
    if audio:
        bases.append(AudioCapable)

    class _FakeRadio(*bases):  # type: ignore[misc]
        pass

    radio = _FakeRadio()
    radio.model = "IC-7610"
    radio.connected = True
    radio.control_connected = False
    radio.radio_ready = True
    radio.capabilities = caps if caps is not None else {"audio", "scope"}
    return radio


# ---------------------------------------------------------------------------
# /api/v1/info — hasWebrtc flag
# ---------------------------------------------------------------------------


class TestInfoHasWebrtc:
    """The ``hasWebrtc`` capability flag in /api/v1/info."""

    @pytest.mark.asyncio
    async def test_false_when_aiortc_unavailable(self):
        srv = WebServer(radio=None)
        writer = _FakeWriter()

        with patch("rigplane.web.server.webrtc_available", return_value=False):
            await srv._serve_info(writer)  # noqa: SLF001

        _, body = _parse_response(writer)
        assert body["capabilities"]["hasWebrtc"] is False

    @pytest.mark.asyncio
    async def test_true_when_available_with_audio(self):
        radio = _make_radio()
        with patch("rigplane.web.server.AudioFftScope", MagicMock()):
            srv = WebServer(radio=radio)
        writer = _FakeWriter()

        with patch("rigplane.web.server.webrtc_available", return_value=True):
            await srv._serve_info(writer)  # noqa: SLF001

        _, body = _parse_response(writer)
        assert body["capabilities"]["hasWebrtc"] is True

    @pytest.mark.asyncio
    async def test_false_when_no_audio_cap(self):
        radio = _make_radio(audio=False, caps={"scope"})
        srv = WebServer(radio=radio)
        writer = _FakeWriter()

        # aiortc present, but the radio lacks the audio capability.
        with patch("rigplane.web.server.webrtc_available", return_value=True):
            await srv._serve_info(writer)  # noqa: SLF001

        _, body = _parse_response(writer)
        assert body["capabilities"]["hasWebrtc"] is False


# ---------------------------------------------------------------------------
# /api/v1/capabilities — webrtc block (stable API-contract field)
# ---------------------------------------------------------------------------


class TestCapabilitiesWebrtcBlock:
    """The ``webrtc`` block in /api/v1/capabilities."""

    @pytest.mark.asyncio
    async def test_present_and_unavailable_by_default(self):
        srv = WebServer(radio=None)
        writer = _FakeWriter()

        with patch("rigplane.web.server.webrtc_available", return_value=False):
            await srv._serve_capabilities(writer)  # noqa: SLF001

        _, body = _parse_response(writer)
        assert "webrtc" in body
        assert body["webrtc"]["available"] is False
        # Transport gate defaults OFF.
        assert body["webrtc"]["enabled"] is False

    @pytest.mark.asyncio
    async def test_reflects_available_and_enabled_gate(self):
        srv = WebServer(radio=None, config=WebConfig(webrtc_enabled=True))
        writer = _FakeWriter()

        with patch("rigplane.web.server.webrtc_available", return_value=True):
            await srv._serve_capabilities(writer)  # noqa: SLF001

        _, body = _parse_response(writer)
        assert body["webrtc"]["available"] is True
        assert body["webrtc"]["enabled"] is True
