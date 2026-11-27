"""MOR-4821: absent radio must not be polled at full rate, and the two
status surfaces must agree.

Regression: with a saved serial radio whose port is absent (COM3 missing),
Core issued ~10 failed state requests/s while reporting "disconnected,
attempt 0, no retry scheduled" next to a health verdict of "reconnecting".

* Rate: ``RadioPoller._run`` sends fire-and-forget acquisition queries that
  never raise, so the existing exception-driven backoff never engaged and
  every pass re-sent at the full 0.1 s serial cadence. While the transport
  reports closed the poller sends nothing and idles on the existing capped
  backoff; the serial watchdog stays the single owner of reconnect timing
  (no second retry loop).
* Statuses: the connection block fell back to the ``connected`` boolean
  ("disconnected, attempt 0, no retry") on paths that never emit a
  reconnect-status callback (all serial backends), while health reads the
  live ``conn_state`` ("reconnecting"). Both now derive from the same
  live state.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane._connection_state import RadioConnectionState
from rigplane.core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionScheduler,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import CommandQueue, RadioPoller
from rigplane.web.runtime_helpers import classify_radio_health
from rigplane.web.server import WebServer
from test_radio_poller_coverage import (
    _acquisition_profile,
    _make_radio,
    _seed_fresh_rx,
    _tick_cadence,
)


def _absent_serial_radio() -> Any:
    """Fake IC-7300 whose USB/serial port is absent (MOR-4821 stand).

    Mirrors ``_IcomSerialRadioBase`` after a failed open plus
    ``start_reconnect_recovery``: the watchdog owns the link and reports
    ``RECONNECTING`` while ``connected`` stays ``False`` and the port-open
    error is recorded. Plain bools (not Mocks) so the production
    ``is False``/``is not False`` duck-typed reads behave as on the radio.
    """
    radio = _make_radio(model="IC-7300")
    radio.connected = False
    radio.radio_ready = False
    radio.conn_state = RadioConnectionState.RECONNECTING
    radio.serial_reconnects_total = 0
    radio.last_error = "Failed to connect serial session on COM3"
    radio.serial_open_error = "COM3: port not found"
    radio.civ_stats = lambda: {"timeouts": 0}  # noqa: E731
    return radio


def _poller_with_counted_wire(radio: Any) -> tuple[RadioPoller, AsyncMock]:
    """Poller whose only wire effect is a counted fire-and-forget send."""
    scheduler = AcquisitionScheduler(
        profile=_acquisition_profile(
            FieldPath.receiver("main", "meters", "s_meter"),
        )
    )
    radio._acquisition_scheduler = scheduler
    send_query = AsyncMock(return_value=None)

    async def _execute(request: Any, **kwargs: Any) -> AcquisitionExecutionResult:
        # The production fire-and-forget shape: the dispatch succeeds
        # (nothing raises) and the answer simply never arrives.
        await send_query(request)
        return AcquisitionExecutionResult(sent_paths=tuple(request.paths))

    executor = SimpleNamespace(execute=AsyncMock(side_effect=_execute))
    poller = RadioPoller(
        radio,
        CommandQueue(),
        radio_state=RadioState(),
        acquisition_executor=executor,
        diagnostics=StateDiagnosticsRecorder(enabled=True),
    )
    _seed_fresh_rx(poller)
    _tick_cadence(poller)
    assert scheduler.pending_requests(), "cadence must queue work to send"
    return poller, send_query


async def test_absent_port_sends_no_acquisition_and_backs_off() -> None:
    """While the transport is closed the poller sends nothing (MOR-4821).

    Bound: ZERO acquisition wire sends while ``connected`` is ``False``;
    the loop idles on the existing capped backoff (0.5 s steps to
    5.0 s max). Fails on origin/main: every pass re-sends at full rate.
    """
    poller, send_query = _poller_with_counted_wire(_absent_serial_radio())
    # A pass that reaches the bottom wait ends the run (the origin/main
    # path: the loop never backs off on its own).
    poller._queue.wait = AsyncMock(side_effect=asyncio.CancelledError())  # type: ignore[method-assign]
    sleeps: list[float] = []

    async def _sleep(delay: float) -> None:
        sleeps.append(delay)
        if len(sleeps) >= 4:
            raise asyncio.CancelledError()

    with patch("rigplane.web.radio_poller.asyncio.sleep", new=_sleep):
        await poller._run()  # noqa: SLF001

    assert send_query.await_count == 0
    assert sleeps[0] == 0.5
    assert sleeps == sorted(sleeps)
    assert all(0.0 < s <= 5.0 for s in sleeps)
    assert sleeps[-1] > sleeps[0]


async def test_poller_resumes_when_transport_opens() -> None:
    """Flipping ``connected`` resumes acquisition with no second loop.

    Lock test: passes before and after the fix; pins that the gate does
    not wedge the poller once the backend's own reconnect succeeds.
    """
    radio = _absent_serial_radio()
    poller, send_query = _poller_with_counted_wire(radio)
    waits = 0

    async def _wait(timeout: float | None = None) -> None:
        nonlocal waits
        waits += 1
        # End the run on the first bottom-wait after a send went out.
        if send_query.await_count >= 1:
            raise asyncio.CancelledError()

    poller._queue.wait = AsyncMock(side_effect=_wait)  # type: ignore[method-assign]

    async def _sleep(delay: float) -> None:
        # The watchdog's reconnect lands mid-run: the port opens.
        radio.connected = True
        radio.radio_ready = True
        radio.conn_state = RadioConnectionState.CONNECTED

    with patch("rigplane.web.radio_poller.asyncio.sleep", new=_sleep):
        await poller._run()  # noqa: SLF001

    assert send_query.await_count >= 1


def _connection_for(server_state: SimpleNamespace) -> dict[str, Any]:
    """Call the unbound producer the way the runtime payload does."""
    return WebServer._runtime_connection_payload(server_state)  # type: ignore[arg-type]


def _health_for(radio: Any) -> dict[str, Any]:
    """Health verdict for a server that started while the port was absent."""
    return classify_radio_health(
        radio,
        server_reachable=True,
        served_with_silent_link=False,
        served_without_port=True,
        operator_disconnected=False,
    )


@pytest.mark.parametrize(
    ("conn_state", "connected", "serial_open_error", "expected"),
    [
        # Absent from the start: watchdog retries the port (MOR-4821 stand).
        (
            RadioConnectionState.RECONNECTING,
            False,
            "COM3: port not found",
            "reconnecting",
        ),
        # Port reappeared but the radio is still silent (identity hold).
        (RadioConnectionState.CONNECTING, False, None, "reconnecting"),
        # Radio answering again.
        (RadioConnectionState.CONNECTED, True, None, "connected"),
        # Cleanly disconnected.
        (RadioConnectionState.DISCONNECTED, False, None, "disconnected"),
    ],
)
def test_connection_and_health_agree_in_every_phase(
    conn_state: RadioConnectionState,
    connected: bool,
    serial_open_error: str | None,
    expected: str,
) -> None:
    """The connection block and the health verdict share one source."""
    radio = SimpleNamespace(
        conn_state=conn_state,
        connected=connected,
        serial_reconnects_total=0,
        last_error=None,
    )
    if serial_open_error is not None:
        radio.serial_open_error = serial_open_error
    server_state = SimpleNamespace(_connection_status=None, _radio=radio)

    connection = _connection_for(server_state)
    health = _health_for(radio)

    assert connection["state"] == expected
    assert health["radioLink"] == expected


def test_stale_callback_never_overrides_live_state() -> None:
    """A cached LAN callback must not mask a newer live disconnect."""
    radio = SimpleNamespace(
        conn_state=RadioConnectionState.DISCONNECTED,
        connected=False,
        serial_reconnects_total=3,
        last_error=None,
    )
    server_state = SimpleNamespace(
        _connection_status={
            "state": "connected",
            "attempt": 2,
            "next_retry_seconds": None,
        },
        _radio=radio,
    )
    assert _connection_for(server_state)["state"] == "disconnected"
