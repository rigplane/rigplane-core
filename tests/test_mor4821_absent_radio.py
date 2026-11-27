"""MOR-4821: avoid polling a closed transport and align Icom link states.

Regression: with a saved serial radio whose port is absent (COM3 missing),
Core issued ~10 failed state requests/s while reporting "disconnected,
attempt 0, no retry scheduled" next to a health verdict of "reconnecting".

* Rate: ``RadioPoller._run`` sends fire-and-forget acquisition queries that
  never raise, so the existing exception-driven backoff never engaged and
  every pass re-sent at the full 0.1 s serial cadence. While the transport
  reports closed the poller sends nothing and waits for queue notifications
  or a transport recheck. The serial watchdog owns reconnect timing.
* Statuses: the connection block fell back to the ``connected`` boolean
  ("disconnected, attempt 0, no retry") on paths that never emit a
  reconnect-status callback (all serial backends), while health reads the
  live ``conn_state`` ("reconnecting"). The supported Icom states now agree.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from rigplane.backends._icom_serial_base import _IcomSerialRadioBase
from rigplane.runtime.radio import CoreRadio
from rigplane.testing.timing import VirtualClock

import pytest

from rigplane._connection_state import RadioConnectionState
from rigplane.core.acquisition_scheduler import (
    AcquisitionExecutionResult,
    AcquisitionScheduler,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import CommandQueue, PttOff, RadioPoller
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


class _SerialPropertiesRadio:
    """Use the real serial link properties and LAN freshness calculation."""

    connected = _IcomSerialRadioBase.connected
    control_connected = _IcomSerialRadioBase.control_connected
    radio_ready = CoreRadio.radio_ready

    def __init__(self) -> None:
        self.delegate = _absent_serial_radio()
        self._serial_session = SimpleNamespace(connected=False)
        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_transport = object()
        self._civ_recovering = False
        self._civ_stream_ready = True
        self._last_civ_data_received: float | None = None
        self._civ_ready_idle_timeout = 2.0

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)


@pytest.mark.timeout(30)
@pytest.mark.parametrize("open_before_release", [False, True])
async def test_absent_port_sends_no_acquisition_and_release_wakes_pacing(
    virtual_clock: VirtualClock,
    open_before_release: bool,
) -> None:
    radio = _SerialPropertiesRadio()
    poller, send_query = _poller_with_counted_wire(radio)
    task = asyncio.create_task(poller._run())  # noqa: SLF001
    try:
        await virtual_clock.elapse(35.0)
        assert send_query.await_count == 0
        if open_before_release:
            radio._serial_session.connected = True
            radio._conn_state = RadioConnectionState.CONNECTED
        poller._queue.put(PttOff())  # noqa: SLF001
        await virtual_clock.elapse(0.2)
        radio.set_ptt.assert_awaited_once_with(False)
    finally:
        task.cancel()
        await task


@pytest.mark.timeout(30)
async def test_open_transport_resumes_acquisition_before_freshness_reply(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import rigplane.runtime.radio as radio_module

    # Scope time.monotonic to the actual CoreRadio.radio_ready property.
    class _ClockTime:
        monotonic = staticmethod(virtual_clock)

        def __getattr__(self, name: str) -> Any:
            import time

            return getattr(time, name)

    monkeypatch.setattr(radio_module, "time", _ClockTime())
    radio = _SerialPropertiesRadio()
    poller, send_query = _poller_with_counted_wire(radio)
    original_send = poller._send_query  # noqa: SLF001
    query_times: list[float] = []

    async def counted_send() -> None:
        query_times.append(virtual_clock())
        previous_count = send_query.await_count
        await original_send()
        if send_query.await_count > previous_count:
            asyncio.get_running_loop().call_soon(
                setattr, radio, "_last_civ_data_received", virtual_clock()
            )

    monkeypatch.setattr(poller, "_send_query", counted_send)
    task = asyncio.create_task(poller._run())  # noqa: SLF001
    try:
        await virtual_clock.elapse(35.0)
        assert send_query.await_count == 0
        radio._serial_session.connected = True
        radio._conn_state = RadioConnectionState.CONNECTED
        assert not radio.radio_ready
        opened = virtual_clock()
        await virtual_clock.elapse(0.3)
        assert send_query.await_count >= 1
        assert radio.radio_ready
        await virtual_clock.elapse(10.0)
        # A 2 s freshness window must not strand polling at the 5 s cap.
        assert sum(t >= opened for t in query_times) >= 20
    finally:
        task.cancel()
        await task


@pytest.mark.timeout(30)
async def test_open_serial_identity_hold_allows_acquisition(
    virtual_clock: VirtualClock,
) -> None:
    radio = _SerialPropertiesRadio()
    poller, send_query = _poller_with_counted_wire(radio)
    task = asyncio.create_task(poller._run())  # noqa: SLF001
    try:
        await virtual_clock.elapse(35.0)
        radio._serial_session.connected = True
        radio._conn_state = RadioConnectionState.CONNECTING
        assert radio.control_connected and not radio.connected
        await virtual_clock.elapse(0.3)
        assert send_query.await_count >= 1
    finally:
        task.cancel()
        await task


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
def test_connection_and_health_agree_in_supported_icom_phases(
    conn_state: RadioConnectionState,
    connected: bool,
    serial_open_error: str | None,
    expected: str,
) -> None:
    """The connection block and health agree on these live Icom states."""
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
