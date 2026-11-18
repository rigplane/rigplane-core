"""MOR-4558: a LAN radio that is powered off for 6 s comes back by itself.

Customer-gate row C24/R15 (``link-fault-lan power_cycle_6s``, Pro nightly
step S6): on the same session the simulated IC-7610 first drops the session
with a notice, the stack recovers, and 3 s later the radio goes dark for 6 s
(no notice) and answers again. Core 3.1.4 dropped the link and was ready
again ~12 s after the power-off; Core 3.2.0 kept reporting "connected" with
``radio_ready`` false (``/readyz`` 503) for the whole 30 s window.

Production stack over the LAN simulator, configured like the bench
(``sim_fixed.py``): single owner, recorded LAN answer table, scope stream,
the recorded keepalive hold. ``IcomRadio`` with auto reconnect plus
``WebServer`` (poller, readiness), watched through ``/readyz`` only, like
``fault_probe.py``. Nothing in the connect path is stubbed.

A 35 s outage is pinned too: it outlasts the soft recovery, which gives up
while the radio is dark and hands over to the full reconnect loop (that path
recovered on 3.2.0 as well, ~4.7 s after power-on).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any

import aiohttp
import pytest

from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer

from testkit.ic7610_fakes import ic7610_lan_fake

_RECOVERY_LIMIT_S = 30.0  # nightly_steps.RECOVERY_LIMIT_S
_PROBE_MARGIN_S = 15.0


class _Trail(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._t0 = time.monotonic()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        offset = time.monotonic() - self._t0
        self.lines.append(f"{offset:7.2f}s {record.name} {record.getMessage()[:220]}")


async def _probe(
    session: aiohttp.ClientSession,
    url: str,
    radio: IcomRadio,
    inject: Any,
    samples: list[str],
    window_s: float,
) -> float | None:
    """Inject, then sample ``/readyz`` every 0.5 s like ``fault_probe.py``.

    Returns seconds from injection to the first ready sample after an outage
    was seen, or ``None``.
    """
    t0 = time.monotonic()
    inject()
    last = None
    outage = False
    while time.monotonic() - t0 < window_s:
        try:
            async with session.get(
                url + "/readyz", timeout=aiohttp.ClientTimeout(total=2)
            ) as reply:
                status = reply.status
        except Exception as exc:  # noqa: BLE001 - the probe records it
            status = f"err:{type(exc).__name__}"
        seen = (status, getattr(radio.conn_state, "value", None), radio.radio_ready)
        if seen != last:
            samples.append(
                f"{time.monotonic() - t0:6.1f}s readyz={seen[0]} {seen[1]} ready={seen[2]}"
            )
            last = seen
        if status != 200:
            outage = True
        elif outage:
            return time.monotonic() - t0
        await asyncio.sleep(0.5)
    return None


@pytest.mark.realtime(reason="real UDP simulator, real outage and reconnect timing")
@pytest.mark.parametrize(
    ("down_for_s", "limit_s"),
    [
        # The bench row: ready again within 30 s of the power-off.
        (6.0, _RECOVERY_LIMIT_S),
        # Outlasts the soft recovery: ready again within 15 s of power-on.
        (35.0, 35.0 + 15.0),
    ],
    ids=["6s", "35s"],
)
async def test_lan_radio_recovers_after_a_power_cycle(
    down_for_s: float, limit_s: float
) -> None:
    sim = ic7610_lan_fake(single_owner=True, scope_stream=True)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
        auto_reconnect=True,
    )
    trail = _Trail()
    rigplane_logger = logging.getLogger("rigplane")
    previous_level = rigplane_logger.level
    rigplane_logger.setLevel(logging.INFO)
    rigplane_logger.addHandler(trail)
    web: WebServer | None = None
    samples: list[str] = []
    recovered: dict[str, float | None] = {}
    try:
        await radio.connect()
        web = WebServer(
            radio,
            WebConfig(
                host="127.0.0.1",
                port=0,
                discovery=False,
                await_initial_state=True,
                radio_model="IC-7610",
            ),
        )
        await web.start()
        url = f"http://127.0.0.1:{web.port}"
        async with aiohttp.ClientSession(trust_env=False) as session:
            deadline = time.monotonic() + 30.0
            while radio.radio_ready is not True:
                assert time.monotonic() < deadline, "never ready before the faults"
                await asyncio.sleep(0.2)
            samples.append("--- session_drop notice ---")
            recovered["session_drop"] = await _probe(
                session,
                url,
                radio,
                lambda: sim.drop_session(at=0.0, notice=True),
                samples,
            )
            await asyncio.sleep(3.0)
            samples.append(f"--- power_cycle {down_for_s} s ---")
            recovered["power_cycle"] = await _probe(
                session,
                url,
                radio,
                lambda: sim.reboot(at=0.0, down_for=down_for_s, notice=False),
                samples,
                limit_s + _PROBE_MARGIN_S,
            )
    finally:
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()
        rigplane_logger.removeHandler(trail)
        rigplane_logger.setLevel(previous_level)

    report = (
        f"recovered={recovered}\n"
        + "\n".join(samples)
        + "\n--- log ---\n"
        + "\n".join(trail.lines[-200:])
    )
    print(report)
    assert recovered.get("session_drop") is not None, report
    power_cycle = recovered.get("power_cycle")
    assert power_cycle is not None and power_cycle <= limit_s, report
