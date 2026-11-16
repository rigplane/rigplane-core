"""MOR-4515: the REAL served state parses through ``ServerStatePublic``.

``GET /api/v1/state`` of a production ``WebServer`` (production acquisition,
only the radio wire faked) must satisfy the producer's own typed contract
under ``strict=True``. Three profile families: an Icom (IC-7610 serial PTY
fake), a Yaesu (FTX-1 over the CAT simulator) and the G7250. The schema had
declared ``vdMeter`` as ``int`` while the server publishes volts as a float,
which only a strict parse of real output could show.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import ServerStatePublic
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake
from test_web_server import _addr, _http_get
from test_yaesu_cat_observation_adapter import _serve_g7250
from testkit.ic7610_fakes import ic7610_usb_fake

_ROOT = Path(__file__).resolve().parents[1]
_FTX1_RIG = _ROOT / "rigs" / "ftx1.toml"
_FTX1_SIM = _ROOT / "rigplane_testkit" / "sim" / "profiles" / "ftx1.toml"
_MIN_OBSERVED = 8

_Serve = Callable[[pytest.MonkeyPatch], AbstractAsyncContextManager[WebServer]]
# Meter leaves each wire fake really answers: waiting for them keeps the
# parse from running over an all-null payload.


@asynccontextmanager
async def _serve_icom(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WebServer]:
    endpoint = PtyEndpoint(ic7610_usb_fake())
    await endpoint.start()
    radio = Icom7610SerialRadio(
        device=endpoint.device, _enumerate_serial_ports_fn=lambda: []
    )
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, discovery=False, radio_model="IC-7610"),
    )
    try:
        await asyncio.wait_for(radio.connect(), 30)
        await asyncio.wait_for(server.start(), 60)
        yield server
    finally:
        try:
            await server.stop()
            await radio.disconnect()
        finally:
            await endpoint.stop()


@asynccontextmanager
async def _serve_ftx1(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[WebServer]:
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(_FTX1_RIG))
    fake = YaesuCatSerialFake.from_profile(_ROOT, _FTX1_SIM, rig_path=_FTX1_RIG)

    async def open_port(*args: Any, **kwargs: Any) -> Any:
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    await radio._transport.connect()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    try:
        await server.start()
        yield server
    finally:
        try:
            await server.stop()
        finally:
            await radio._transport.close()


@asynccontextmanager
async def _serve_g7250_server(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[WebServer]:
    async with _serve_g7250(monkeypatch) as (server, _fake):
        yield server


async def _served_state(
    server: WebServer, published: tuple[str, ...]
) -> ServerStatePublic:
    """Wait for the production poller to publish ``published`` (meter leaves
    the radio really answers), strict-parsing every body polled."""
    host, port = _addr(server)[:2]
    async with asyncio.timeout(30):
        while True:
            status, _, raw = await _http_get(host, port, "/api/v1/state")
            assert status == 200, f"GET /api/v1/state: HTTP {status}"
            state = ServerStatePublic.model_validate_json(raw, strict=True)
            observed = sum(1 for s in state.fieldStatus.values() if s.observed)
            if observed >= _MIN_OBSERVED and all(
                getattr(state, name) is not None for name in published
            ):
                return state
            await asyncio.sleep(0.1)


_PROFILES = [
    pytest.param(
        _serve_icom,
        ("vdMeter",),
        marks=pytest.mark.skipif(not pty_supported(), reason="requires a PTY"),
        id="icom-ic7610",
    ),
    pytest.param(_serve_ftx1, ("idMeter",), id="yaesu-ftx1"),
    pytest.param(_serve_g7250_server, (), id="g7250"),
]


@pytest.mark.realtime(reason="real serial/CAT wire fakes under the production poller")
@pytest.mark.parametrize(("serve", "published"), _PROFILES)
async def test_real_served_state_satisfies_the_strict_public_contract(
    serve: _Serve, published: tuple[str, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    async with serve(monkeypatch) as server:
        state = await _served_state(server, published)
    observed = [path for path, s in state.fieldStatus.items() if s.observed]
    assert len(observed) >= _MIN_OBSERVED, observed
