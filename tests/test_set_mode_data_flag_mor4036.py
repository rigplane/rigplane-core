"""MOR-4036: a mode change must not carry DATA into a mode without DATA.

Bench root cause (real IC-705, CI-V wire trace): USB-D -> CW sent
``26 00 03 01 01`` (CW with DATA on); CW cannot carry DATA, so the radio
NAKs and stays in USB-D while the fire-and-forget write still reports
``ok: true``.

This test drives the customer path through the real stack: the recorded
IC-705 LAN personality on :class:`MockIcomRadio` (with its new
``data_capable_modes`` NAK faithfulness), the production ``IcomRadio``
over UDP, and the ``set_mode`` web command through a started
``WebServer``. Only the radio and the network are fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import AsyncIterator

import pytest

from rigplane.runtime.radio import IcomRadio
from rigplane.types import Mode
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio
from test_web_split_writes import _request
from testkit.ic705_fakes import ic705_lan_fake

#: IC-705 instruction manual: DATA exists in SSB, AM, FM (bench-verified
#: 2026-11-02: ``26 00 03 01 01`` (CW+DATA) is NAKed, ``26 00 03 00 01``
#: is ACKed). Mode bytes of those modes on the CI-V wire.
_IC705_DATA_CAPABLE_MODES = frozenset(
    int(mode) for mode in (Mode.LSB, Mode.USB, Mode.AM, Mode.FM)
)

#: Bounded waits: the MAIN freq/mode/data-mode acquisition is fast, but the
#: whole startup (connect + first poll round) may take a few seconds.
_STARTUP_DEADLINE_S = 20.0
_SETTLE_DEADLINE_S = 5.0


@contextlib.asynccontextmanager
async def _stack() -> AsyncIterator[tuple[WebServer, IcomRadio, MockIcomRadio]]:
    sim = ic705_lan_fake(data_capable_modes=_IC705_DATA_CAPABLE_MODES)
    sim._mode = int(Mode.USB)
    sim._data_mode = 1  # the radio starts in USB-D
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4036",
        password="mor4036",
        timeout=1.0,
        model="IC-705",
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


async def _poll_until(what: str, deadline_s: float, predicate) -> None:
    """Fail loudly unless ``predicate`` holds within the deadline."""
    deadline = time.monotonic() + deadline_s
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError(f"timed out after {deadline_s}s waiting for: {what}")
        await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_web_set_mode_drops_data_flag_on_cw() -> None:
    """USB-D -> CW through the web command lands the radio in CW, DATA off."""
    async with _stack() as (web, radio, sim):
        # The runtime preserves the *observed* MAIN DATA flag, so first wait
        # until the poller has read the radio's USB-D state into the cache.
        await _poll_until(
            "MAIN observed as USB with DATA on",
            _STARTUP_DEADLINE_S,
            lambda: radio._radio_state.main.data_mode == 1,
        )

        status, result = await _request(web, "http", "set_mode", {"mode": "CW"})
        assert status == 200
        assert result["ok"] is True

        # The simulated radio must land in CW with DATA off: before the fix
        # the runtime sent ``26 00 03 01 01``, the radio NAKed it and stayed
        # in USB-D (this wait is the failing-first assertion).
        await _poll_until(
            "simulated radio reached CW",
            _SETTLE_DEADLINE_S,
            lambda: sim._mode == int(Mode.CW),
        )
        assert sim._data_mode == 0

        # Readback through the radio API agrees.
        mode_name, _ = await radio.get_mode()
        assert mode_name == "CW"
        assert await radio.get_data_mode() is False


@pytest.mark.asyncio
async def test_web_set_mode_keeps_data_flag_on_lsb() -> None:
    """USB-D -> LSB keeps DATA: LSB is a DATA-capable mode on the IC-705."""
    async with _stack() as (web, radio, sim):
        await _poll_until(
            "MAIN observed as USB with DATA on",
            _STARTUP_DEADLINE_S,
            lambda: radio._radio_state.main.data_mode == 1,
        )

        status, result = await _request(web, "http", "set_mode", {"mode": "LSB"})
        assert status == 200
        assert result["ok"] is True

        await _poll_until(
            "simulated radio reached LSB",
            _SETTLE_DEADLINE_S,
            lambda: sim._mode == int(Mode.LSB),
        )
        assert sim._data_mode == 1
        mode_name, _ = await radio.get_mode()
        assert mode_name == "LSB"
        assert await radio.get_data_mode() is True
