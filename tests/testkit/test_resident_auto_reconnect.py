"""A resident CLI command reconnects a LAN radio after a power-cycle.

The production ``IcomRadio`` is built by the CLI's own dispatch
(``rigplane.cli._run`` for ``serve``: ``_build_backend_config`` then
``create_radio``), so the reconnect settings it carries are the ones the
command passes. The command body is replaced by a coroutine that reboots the
single-owner ``MockIcomRadio`` and waits for the client to come back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

import pytest

from rigplane import cli
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

# Outlasts the lifecycle recovery (three soft attempts, each a CI-V discovery
# of DISCOVERY_RETRIES tries of DISCOVERY_TIMEOUT), so the radio is still dark
# when it exhausts.
_DOWN_FOR_S = 45.0
_RECOVERY_BUDGET_S = 15.0
_SETTLE_S = 3.0
_MARKERS = (
    "handing off to lifecycle recovery",
    "Soft reconnect exhausted",
    "Watchdog: no activity",
    "Reconnect attempt",
    "Reconnected successfully",
)


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=2.0)
    await server.start()
    yield server
    await server.stop()


class _Trail(logging.Handler):
    """Every INFO-and-above ``rigplane`` record, with seconds since creation."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self._t0 = time.monotonic()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        text = record.getMessage()
        if record.exc_info and record.exc_info[1] is not None:
            text = f"{text} | {record.exc_info[1]}"
        offset = time.monotonic() - self._t0
        self.lines.append(f"{offset:7.2f}s {record.name} {text}")

    def marker_order(self) -> list[str]:
        """The markers in log order, a repeated marker counted once in a row."""
        order: list[str] = []
        for line in self.lines:
            for marker in _MARKERS:
                if marker in line and (not order or order[-1] != marker):
                    order.append(marker)
        return order


async def test_serve_reconnects_a_lan_radio_after_a_power_cycle(
    sim: MockIcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("ICOM_PASS", "testpass")
    monkeypatch.setattr(cli, "check_ports_available", lambda _bindings: None)
    args = cli._build_parser().parse_args(
        [
            "--host",
            "127.0.0.1",
            "--control-port",
            str(sim.control_port),
            "--user",
            "testuser",
            "--model",
            "IC-7610",
            "serve",
            "--listen",
            "127.0.0.1",
        ]
    )
    facts: dict[str, Any] = {}
    statuses: list[dict[str, Any]] = []

    async def power_cycle(radio: Any, _args: Any) -> int:
        radio.set_reconnect_status_callback(statuses.append)
        facts["old_token"] = radio._token
        facts["old_owner"] = sim.owner
        logins_before = sim.login_count
        busy_before = sim.busy_rejects
        sim.reboot(at=0.1, down_for=_DOWN_FOR_S, busy_for=0.0)
        deadline = time.monotonic() + _DOWN_FOR_S + _RECOVERY_BUDGET_S
        while time.monotonic() < deadline:
            if statuses and statuses[-1]["state"] == "connected" and radio.connected:
                break
            await asyncio.sleep(0.05)
        facts["conn_state"] = radio.conn_state
        facts["connected"] = radio.connected
        facts["logins"] = sim.login_count - logins_before
        facts["busy"] = sim.busy_rejects - busy_before
        facts["owner"] = sim.owner
        await asyncio.sleep(_SETTLE_S)
        facts["settled_logins"] = sim.login_count - logins_before
        facts["settled_busy"] = sim.busy_rejects - busy_before
        facts["token"] = radio._token
        facts["sim_token"] = sim.token
        facts["freq"] = await radio.get_freq() if radio.connected else None
        return 0

    monkeypatch.setattr(cli, "_cmd_serve", power_cycle)
    trail = _Trail()
    rigplane_logger = logging.getLogger("rigplane")
    previous_level = rigplane_logger.level
    rigplane_logger.setLevel(logging.INFO)
    rigplane_logger.addHandler(trail)
    try:
        with fast_connect():
            rc = await cli._run(args)
    finally:
        rigplane_logger.removeHandler(trail)
        rigplane_logger.setLevel(previous_level)

    timeline = "\n".join(trail.lines)
    counts = (
        f"logins={facts['logins']}/{facts['settled_logins']} "
        f"busy_rejects={facts['busy']}/{facts['settled_busy']}"
    )
    assert rc == 0, capsys.readouterr().err
    # No double reconnect: at most one login was accepted, and the simulator
    # refused none as a second session.
    assert facts["logins"] <= 1 and facts["settled_logins"] <= 1, (
        f"{counts}\n{timeline}"
    )
    assert facts["busy"] == 0 and facts["settled_busy"] == 0, f"{counts}\n{timeline}"
    assert facts["connected"] is True, f"{counts}\n{timeline}"
    assert facts["conn_state"] is RadioConnectionState.CONNECTED
    states = [s["state"] for s in statuses]
    assert states[-1] == "connected" and states.count("connected") == 1, statuses
    assert "reconnecting" in states[:-1], statuses
    assert facts["settled_logins"] == 1, counts
    assert facts["token"] == facts["sim_token"] != facts["old_token"]
    assert facts["freq"] == 14_074_000
    assert trail.marker_order() == [
        "handing off to lifecycle recovery",
        "Soft reconnect exhausted",
        "Reconnect attempt",
        "Reconnected successfully",
    ], timeline
    assert facts["owner"] is not None and facts["owner"] != facts["old_owner"]
