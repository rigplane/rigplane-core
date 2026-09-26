"""Tests for baseline security response headers (issue #951).

Verifies that every HTTP response from WebServer carries the four required
security headers: X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
and Content-Security-Policy.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import pathlib
import re
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.rigctld.state_cache import StateCache
from rigplane.web.server import WebConfig, WebServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_radio() -> MagicMock:
    radio = MagicMock(name="radio")
    # Python 3.11 runtime-checkable Protocol isinstance() uses hasattr(), which
    # a bare MagicMock always satisfies — start_web_server would then spawn the
    # auto-generated MagicMock poller.start() as a coroutine (TypeError).
    # Python 3.12+ uses inspect.getattr_static() (gh-102433) and is immune.
    # Deleting the factory attrs makes both interpreters take the RadioPoller
    # branch.
    del radio.create_observation_poller
    del radio.create_state_poller
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    radio.model = "IC-7610"
    radio.capabilities = set()
    radio.state_cache = StateCache()
    radio.soft_disconnect = AsyncMock()
    radio.disconnect = AsyncMock()
    return radio


def _addr(server: WebServer) -> tuple[str, int]:
    assert server._server is not None
    return server._server.sockets[0].getsockname()


async def _read_http_response(
    reader: asyncio.StreamReader,
) -> tuple[int, dict[str, str], bytes]:
    """Read one complete HTTP response from ``reader``.

    A single ``reader.read(n)`` returns whatever one TCP segment happened to
    carry, so bodies larger than a segment (``/api/v1/state`` is ~32 KiB) were
    silently truncated mid-JSON. Read the headers up to the blank line, then
    take either ``Content-Length`` body bytes or everything until EOF (callers
    send ``Connection: close``, and the server always sets ``Content-Length``).
    """
    header_bytes = (await reader.readuntil(b"\r\n\r\n"))[:-4]

    lines = header_bytes.decode("ascii", errors="replace").split("\r\n")
    status_code = int(lines[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    raw_length = headers.get("content-length")
    if raw_length is None:
        body = await reader.read()  # until EOF
    else:
        body = await reader.readexactly(int(raw_length))

    return status_code, headers, body


async def _http_get(
    host: str, port: int, path: str
) -> tuple[int, dict[str, str], bytes]:
    """Minimal HTTP/1.1 GET over asyncio.  Returns (status, headers, body)."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        request = (
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        )
        writer.write(request.encode())
        await writer.drain()
        return await asyncio.wait_for(_read_http_response(reader), timeout=5.0)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def sec_server() -> WebServer:  # type: ignore[misc]
    config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    srv = WebServer(_make_radio(), config)
    await srv.start()
    yield srv  # type: ignore[misc]
    await srv.stop()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSecurityHeaders:
    """Each security header must be present on a plain HTTP GET response."""

    async def test_x_content_type_options(self, sec_server: WebServer) -> None:
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        assert headers.get("x-content-type-options") == "nosniff"

    async def test_x_frame_options(self, sec_server: WebServer) -> None:
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        assert headers.get("x-frame-options") == "DENY"

    async def test_referrer_policy(self, sec_server: WebServer) -> None:
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        assert headers.get("referrer-policy") == "no-referrer"

    async def test_content_security_policy(self, sec_server: WebServer) -> None:
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        csp = headers.get("content-security-policy", "")
        assert "default-src" in csp

    async def test_csp_font_origins(self, sec_server: WebServer) -> None:
        """CSP must permit the external font origins used by frontend/src/components-v2/theme/*.css.

        fonts.googleapis.com serves Google Fonts CSS stylesheets.
        fonts.gstatic.com serves the actual woff2 font binaries from Google.
        cdn.jsdelivr.net serves DSEG woff2 binaries used by digital VFO skins.
        """
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        csp = headers.get("content-security-policy", "")
        assert "fonts.googleapis.com" in csp, "style-src must allow Google Fonts CSS"
        assert "fonts.gstatic.com" in csp, "font-src must allow Google Fonts binaries"
        assert "cdn.jsdelivr.net" in csp, "font-src must allow jsDelivr (DSEG fonts)"

    async def test_all_headers_on_404(self, sec_server: WebServer) -> None:
        """Security headers must also appear on error responses (e.g. 404)."""
        host, port = _addr(sec_server)
        status, headers, _ = await _http_get(host, port, "/nonexistent")
        assert status == 404
        assert headers.get("x-content-type-options") == "nosniff"
        assert headers.get("x-frame-options") == "DENY"
        assert headers.get("referrer-policy") == "no-referrer"
        assert "default-src" in headers.get("content-security-policy", "")

    async def _csp(self, sec_server: WebServer) -> str:
        host, port = _addr(sec_server)
        _, headers, _ = await _http_get(host, port, "/api/v1/state")
        return headers.get("content-security-policy", "")

    async def test_csp_carries_hash_of_inline_sw_cleanup_script(
        self, sec_server: WebServer
    ) -> None:
        """MOR-2242 — script-src must carry the exact hash of index.html's inline
        service-worker-cleanup script.

        Without a ``script-src`` directive the ``default-src 'self'`` policy
        blocks the inline script, so the stale-service-worker cleanup never
        runs in production. The hash must match the script byte-for-byte:
        this test recomputes it from ``frontend/index.html`` so any edit to
        the inline script that forgets the CSP update fails here first.
        """
        index_html = (
            pathlib.Path(__file__).resolve().parents[1] / "frontend" / "index.html"
        ).read_bytes()
        match = re.search(rb"<script>\n(.*?)\n    </script>", index_html, re.S)
        assert match is not None, "inline SW cleanup script not found in index.html"
        digest = base64.b64encode(
            hashlib.sha256(match.group(1) + b"\n").digest()
        ).decode("ascii")
        csp = await self._csp(sec_server)
        assert f"'sha256-{digest}'" in csp, (
            "script-src must carry the current hash of index.html's inline script"
        )

    async def test_csp_allows_data_media(self, sec_server: WebServer) -> None:
        """MOR-2242 — media-src must allow data: for the no-sleep video.

        ``MobileRadioLayout.svelte`` keeps iOS Safari awake with a
        ``data:video/mp4`` source; without an explicit ``media-src`` the
        ``default-src 'self'`` policy blocks it and the browser logs a
        console error on every wake-lock attempt.
        """
        csp = await self._csp(sec_server)
        assert "media-src 'self' data:" in csp
