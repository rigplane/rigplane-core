"""MOR-4382: a full LAN reconnect behaves exactly like a fresh connect.

Production stack over the IC-705 LAN simulator: ``IcomRadio`` (auto reconnect
on) + ``WebServer``. The radio disappears for a few seconds (a Wi-Fi blip),
the data watchdog and the lifecycle give up on the soft path, the resident
reconnect loop logs in again. Afterwards the published state must hold the
frequency and the mode again, and a command must work.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable
from typing import Any

import pytest

from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from test_web_server import _http_get
from testkit.ic705_fakes import ic705_lan_fake

_DEADLINE_S = 40.0


async def _public_state(web: WebServer) -> dict[str, Any]:
    status, _, body = await _http_get("127.0.0.1", web.port, "/api/v1/state")
    assert status == 200
    return json.loads(body)


async def _wait_state(
    web: WebServer,
    predicate: Callable[[dict[str, Any]], bool],
    *,
    deadline_s: float,
    what: str,
) -> dict[str, Any]:
    deadline = time.monotonic() + deadline_s
    state = await _public_state(web)
    while not predicate(state):
        if time.monotonic() >= deadline:
            raise AssertionError(
                f"timed out after {deadline_s}s waiting for: {what}; "
                f"freq={state['main']['vfoA']['freqHz']!r} "
                f"mode={state['main']['mode']!r} "
                f"connection={state.get('connection')!r}"
            )
        await asyncio.sleep(0.2)
        state = await _public_state(web)
    return state


@contextlib.asynccontextmanager
async def _stack(
    **sim_kw: Any,
) -> AsyncIterator[tuple[WebServer, IcomRadio, MockIcomRadio]]:
    sim = ic705_lan_fake(single_owner=True, keepalive_hold_s=30.0, **sim_kw)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4382",
        password="mor4382",
        timeout=1.0,
        model="IC-705",
        auto_reconnect=True,
        reconnect_delay=0.5,
    )
    web: WebServer | None = None
    try:
        await radio.connect()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        await web.start()
        yield web, radio, sim
    finally:
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


def _has_freq_and_mode(state: dict[str, Any]) -> bool:
    return (
        state["main"]["vfoA"]["freqHz"] is not None
        and state["main"]["mode"] is not None
    )


async def _assert_recovered(web: WebServer, radio: IcomRadio) -> None:
    """freq/mode are published again and a command works on the new link."""
    state = await _wait_state(
        web,
        lambda s: (
            s["connection"]["rigConnected"] is True
            and s["connection"]["radioReady"] is True
            and _has_freq_and_mode(s)
        ),
        deadline_s=_DEADLINE_S,
        what="ready, with freq and mode re-acquired and published",
    )
    del state
    await radio.set_freq(7_074_000)
    assert await radio.get_freq() == 7_074_000
    await _wait_state(
        web,
        lambda s: s["main"]["vfoA"]["freqHz"] == 7_074_000,
        deadline_s=10.0,
        what="the commanded frequency is published",
    )


_REALTIME = pytest.mark.realtime(reason="real UDP simulator, real reconnect timing")


@pytest.mark.asyncio
@_REALTIME
async def test_civ_stream_stop_recovers_freq_and_mode() -> None:
    """Guard: the CI-V stream stops for 3 s and comes back (soft path)."""
    async with _stack() as (web, radio, sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        sim.civ_standby(True)
        await asyncio.sleep(3.0)
        sim.civ_standby(False)
        await _assert_recovered(web, radio)


@pytest.mark.asyncio
@_REALTIME
async def test_resident_reconnect_loop_republishes_freq_and_mode() -> None:
    """The full reconnect (``reconnect_loop``) re-selects the VFO identity.

    The web layer discards its VFO identity when the connection drops; only
    the reconnect callback re-selects it. The hard path used to skip it.
    """
    async with _stack() as (web, radio, sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        radio._conn_state = RadioConnectionState.RECONNECTING
        await radio._reconnect_loop()
        assert radio.connected
        await _assert_recovered(web, radio)


@pytest.mark.asyncio
@_REALTIME
async def test_owner_disconnect_connect_republishes_freq_and_mode() -> None:
    """DISCONNECT then CONNECT in the UI behaves like a fresh connect."""
    async with _stack() as (web, radio, sim):
        await _wait_state(web, _has_freq_and_mode, deadline_s=20.0, what="initial")
        await radio.disconnect()
        await radio.connect()
        await _assert_recovered(web, radio)
