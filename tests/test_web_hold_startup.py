"""MOR-3363: ``rigplane web`` comes up when the first connect lands in the hold.

The recorded IC-7610 allocated its session but never answered the
``0x19 00`` identity read, so the radio sat at
``connected=False, radio_ready=False, control_connected=True`` with a
``NO_RESPONSE`` identity. The bundled rigctld's start-up check aborted
the whole process before the web listener ever bound.

The simulator (``rigplane_testkit/icom/simulator.py``) cannot stage
this: it always answers ``0x19 0x00`` with its model id and offers no
knob to withhold that answer, and the simulator is another lane's. So
these tests use a narrow fake carrying exactly the field state the
start-up check reads, started in the order ``cli: _cmd_web`` uses
(bundled rigctld first, web server second).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.radio_state import RadioState
from rigplane.rigctld.contract import RigctldConfig, RigctldResponse
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.startup_checks import assert_radio_startup_ready
from rigplane.web.server import WebConfig, WebServer


class _HoldRadio:
    """A LAN radio whose first connect landed in the identity hold.

    The session is allocated (control connected) but the identity read
    never answered, so the radio is neither connected nor ready while a
    real ``NO_RESPONSE`` identity owns the link — the state the web
    gate already serves through (``web_startup: _serve_with_identity_pending``).
    """

    backend_id = "icom_lan"
    model = "FAKE-HOLD"
    capabilities: set[str] = set()

    def __init__(self) -> None:
        self.connected = False
        self.control_connected = True
        self.radio_ready = False
        self.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.NO_RESPONSE,
            expected_model="IC-7610",
        )
        self.radio_state = RadioState()

    def supports_command(self, _command: str) -> bool:
        return False


class _HoldHandler:
    """Answers the way the held radio does: refused until ready."""

    def __init__(self, radio: _HoldRadio) -> None:
        self._radio = radio

    async def execute(self, _cmd: object, **_kwargs: object) -> RigctldResponse:
        if not self._radio.radio_ready:
            raise ConnectionError("radio is not ready")
        return RigctldResponse(values=["14074000"], error=0)


def _web_config() -> WebConfig:
    return WebConfig(
        host="127.0.0.1",
        port=0,
        discovery=False,
        await_initial_state=True,
    )


async def _close_writer(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _get_readyz(server: WebServer) -> tuple[int, dict[str, Any]]:
    """GET /readyz over a real socket: status code plus the JSON body."""

    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            b"GET /readyz HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n"
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(65536), timeout=5.0)
    finally:
        await _close_writer(writer)
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body.decode())


async def _rigctld_line(rig: RigctldServer, line: bytes) -> bytes:
    """Send one command line to the rigctld listener; return its answer."""

    assert rig._server is not None
    host, port = rig._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(line)
        await writer.drain()
        return await asyncio.wait_for(reader.read(4096), timeout=5.0)
    finally:
        await _close_writer(writer)


@pytest.mark.asyncio
async def test_web_and_bundled_rigctld_come_up_against_identity_hold() -> None:
    """The MOR-3363 composition: hold-state radio, both servers listen."""

    radio = _HoldRadio()
    rig = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        _handler=_HoldHandler(radio),
        allow_not_ready=True,
    )
    # The bundled seat starts first, exactly as ``cli: _cmd_web`` does —
    # this is the call that aborted the whole process with
    # "rigctld startup aborted: radio is not ready ...".
    await rig.start()
    assert rig._server is not None

    server = WebServer(radio, _web_config())
    fake_poller = MagicMock(
        drain_tx_safety_commands=AsyncMock(),
        select_vfo_a_on_connect=AsyncMock(),
    )
    try:
        with patch("rigplane.web.web_startup.RadioPoller", return_value=fake_poller):
            await server.start()
        assert server._server is not None
        # The web gate released through the identity-pending branch, the
        # same branch a serial hold takes — not through the abort.
        assert server._served_with_silent_link is True

        status, payload = await _get_readyz(server)
        assert status == 503
        assert payload == {"status": "not_ready", "radioReady": False}

        # The bundled rigctld listens and answers a radio command with an
        # error RPRT while the radio is not ready.
        assert await _rigctld_line(rig, b"f\n") == b"RPRT -6\n"

        # The hold resolves (auto-reconnect owns the recovery): the exact
        # check that aborted start-up now passes, rigctld serves normally,
        # and /readyz reports ready honestly.
        radio.connected = True
        radio.radio_ready = True
        assert_radio_startup_ready(radio, component="rigctld startup")
        assert await _rigctld_line(rig, b"f\n") == b"14074000\n"
        status, payload = await _get_readyz(server)
        assert status == 200
        assert payload == {"status": "ready", "radioReady": True}
    finally:
        await server.stop()
        await rig.stop()


@pytest.mark.asyncio
async def test_standalone_rigctld_still_aborts_against_identity_hold() -> None:
    """The default seat keeps the fail-hard check; only web mode opts out."""

    radio = _HoldRadio()
    rig = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        _handler=_HoldHandler(radio),
    )
    with pytest.raises(RuntimeError, match="rigctld startup aborted"):
        await rig.start()
