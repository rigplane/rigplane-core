"""Tests for watchdog and auto-reconnect."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.exceptions import AuthenticationError
from rigplane.radio import IcomRadio
from rigplane.runtime.session_lifecycle import LifecycleEvent, LifecycleState
from rigplane.web.server import WebConfig, WebServer

from test_audio_session_health import _FakeWriter, _response_json
from test_radio import MockTransport
from test_radio_connect import ConnectMockTransport, _ConnectOnceStubs, _build_conninfo


@pytest.fixture
def radio() -> IcomRadio:
    r = IcomRadio(
        "192.168.1.100",
        auto_reconnect=True,
        reconnect_delay=0.1,
        reconnect_max_delay=0.5,
        watchdog_timeout=0.3,
        model="IC-7610",
    )
    mt = MockTransport()
    r._ctrl_transport = mt
    r._connected = True
    r._token = 0x1234
    return r


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_start_stop(self, radio: IcomRadio) -> None:
        radio._control_phase._start_watchdog()
        assert radio._watchdog_task is not None
        radio._control_phase._stop_watchdog()
        await asyncio.sleep(0.05)
        assert radio._watchdog_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        r = IcomRadio("192.168.1.100", model="IC-7610")
        r._control_phase._stop_watchdog()  # should not raise

    @pytest.mark.asyncio
    async def test_watchdog_triggers_on_timeout(self, radio: IcomRadio) -> None:
        """Watchdog should set connected=False after timeout with no activity."""
        radio._control_phase._start_watchdog()
        # Wait longer than watchdog_timeout
        await asyncio.sleep(0.6)
        assert not radio._connected
        # Reconnect task should have been started
        assert radio._reconnect_task is not None
        # Clean up
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        radio._control_phase._stop_watchdog()

    # The three tests below drive ``watchdog_loop`` on a FAKE clock: every
    # watchdog sleep advances the clock by exactly its interval (plus any
    # stall the test injects) and yields once. Nothing waits on wall time, so
    # a loaded runner cannot change the outcome (MOR-3419: the former
    # wall-clock window, and its "activity" that the watchdog never read).

    @pytest.fixture(params=["IC-7610", "IC-705"])
    def wd_radio(self, request: pytest.FixtureRequest) -> IcomRadio:
        """The watchdog is one shared mechanism: every case runs on two models."""
        r = IcomRadio(
            "192.168.1.100",
            auto_reconnect=True,
            reconnect_delay=0.1,
            reconnect_max_delay=0.5,
            watchdog_timeout=1.5,
            model=request.param,
        )
        r._ctrl_transport = MockTransport()
        r._connected = True
        r._token = 0x1234
        return r

    @staticmethod
    def _fake_clock(
        radio: IcomRadio, monkeypatch: pytest.MonkeyPatch, *, link_alive: bool
    ) -> SimpleNamespace:
        from rigplane.runtime import radio_reconnect

        clock = SimpleNamespace(now=1000.0, stall=0.0, sleeps=0)
        # The timeout is 3 check intervals, as in the sim-backed integration stack.

        async def fake_sleep(delay: float) -> None:
            clock.sleeps += 1
            stalled = clock.stall > 0
            clock.now += delay + clock.stall
            clock.stall = 0.0
            # A live link delivers a ping reply per interval; a loop that
            # did not run (the stall) delivered nothing.
            if link_alive and not stalled:
                radio._ctrl_transport.rx_packet_count += 1
            await asyncio.sleep(0)

        monkeypatch.setattr(
            radio_reconnect,
            "time",
            SimpleNamespace(monotonic=lambda: clock.now),
        )
        monkeypatch.setattr(
            radio_reconnect,
            "asyncio",
            SimpleNamespace(
                sleep=fake_sleep,
                create_task=asyncio.create_task,
                CancelledError=asyncio.CancelledError,
            ),
        )

        async def no_reconnect() -> None:
            return None

        radio._reconnect_loop = no_reconnect  # type: ignore[method-assign]
        return clock

    @staticmethod
    async def _spin(clock: SimpleNamespace, until_sleeps: int) -> None:
        """Yield to the loop until the watchdog has slept ``until_sleeps``
        times, or until it has stopped sleeping (it fired and returned)."""
        idle_yields = 0
        while clock.sleeps < until_sleeps and idle_yields < 50:
            before = clock.sleeps
            await asyncio.sleep(0)
            idle_yields = idle_yields + 1 if clock.sleeps == before else 0

    @pytest.mark.asyncio
    async def test_watchdog_does_not_trigger_with_activity(
        self, wd_radio: IcomRadio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A packet per interval keeps the watchdog quiet far past its timeout."""
        clock = self._fake_clock(wd_radio, monkeypatch, link_alive=True)
        wd_radio._control_phase._start_watchdog()
        await self._spin(clock, 40)  # 20 s of fake time >> the 1.5 s timeout
        wd_radio._control_phase._stop_watchdog()
        assert clock.sleeps >= 40
        assert wd_radio._reconnect_task is None

    @pytest.mark.asyncio
    async def test_watchdog_triggers_on_real_silence(
        self, wd_radio: IcomRadio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No packets for longer than the timeout still starts a reconnect."""
        clock = self._fake_clock(wd_radio, monkeypatch, link_alive=False)
        wd_radio._control_phase._start_watchdog()
        await self._spin(clock, 40)
        wd_radio._control_phase._stop_watchdog()
        assert wd_radio._reconnect_task is not None

    @pytest.mark.asyncio
    async def test_watchdog_ignores_an_event_loop_stall(
        self, wd_radio: IcomRadio, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """MOR-3868: a stall of the loop itself (CPU-starved runner) longer
        than the timeout, on a healthy link, is not a dead link."""
        clock = self._fake_clock(wd_radio, monkeypatch, link_alive=True)
        wd_radio._control_phase._start_watchdog()
        await self._spin(clock, 4)
        clock.stall = 1.7  # the CI signature: "no activity for 1.7s"
        stall_at = clock.sleeps
        await self._spin(clock, stall_at + 40)
        wd_radio._control_phase._stop_watchdog()
        assert clock.sleeps >= stall_at + 40  # the watchdog kept watching
        assert wd_radio._reconnect_task is None


class TestReconnect:
    @pytest.mark.asyncio
    async def test_stop_reconnect_when_not_started(self) -> None:
        r = IcomRadio("192.168.1.100", model="IC-7610")
        r._control_phase._stop_reconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_intentional_disconnect_stops_reconnect(
        self, radio: IcomRadio
    ) -> None:
        """Setting intentional_disconnect should prevent reconnect."""
        radio._intentional_disconnect = True
        radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
        await asyncio.sleep(0.1)
        # Should exit immediately
        assert radio._reconnect_task.done()

    @pytest.mark.asyncio
    async def test_reconnect_increments_delay(self, radio: IcomRadio) -> None:
        """Reconnect should use exponential backoff."""
        # The reconnect will fail (no real radio), but we verify it tries
        radio._connected = False
        radio._intentional_disconnect = False
        radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
        await asyncio.sleep(0.5)
        # Should still be trying (not connected, not intentional)
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        await asyncio.sleep(0.05)


class TestReconnectLifecycleRelease:
    """The reconnect loop routes its release through the session lifecycle."""

    @pytest.mark.asyncio
    async def test_stop_reconnect_from_inside_task_keeps_handle_for_external_stop(
        self, radio: IcomRadio
    ) -> None:
        """_stop_reconnect from inside the task it would cancel neither
        cancels the task nor clears the handle; the handle is cleared
        only on a real cancel, so a later external stop — a user
        disconnect() — can still end the loop."""
        radio._connected = False
        radio._intentional_disconnect = False

        async def _body() -> None:
            radio._reconnect_task = asyncio.current_task()
            radio._control_phase._stop_reconnect()
            await asyncio.sleep(0.05)

        task = asyncio.create_task(_body())
        await asyncio.sleep(0.01)  # let _body reach its internal stop
        assert not task.cancelled()
        assert radio._reconnect_task is task
        radio._control_phase._stop_reconnect()  # external stop: cancels
        with pytest.raises(asyncio.CancelledError):
            await task
        assert radio._reconnect_task is None

    @pytest.mark.asyncio
    async def test_reconnect_loop_releases_through_lifecycle(
        self, radio: IcomRadio
    ) -> None:
        """One reconnect iteration awaits the lifecycle disconnect before
        connect(); the lifecycle reset is what makes the login real."""
        calls: list[str] = []
        radio._connected = False
        radio._intentional_disconnect = False
        with (
            patch.object(
                radio._session_lifecycle,
                "disconnect",
                new_callable=AsyncMock,
                side_effect=lambda: calls.append("lifecycle"),
            ) as lifecycle_disconnect,
            patch.object(
                radio,
                "connect",
                new_callable=AsyncMock,
                side_effect=lambda: calls.append("connect"),
            ),
        ):
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert lifecycle_disconnect.await_count == 1
        assert calls == ["lifecycle", "connect"]


class TestReconnectOwnerStop:
    """An owner stop (``CoreRadio.disconnect`` increments
    ``_owner_stop_epoch``) inside the round's release, inside a connect that
    returns or fails with a retryable error, or during the backoff ends the
    loop with no status after the round's ``reconnecting``."""

    @pytest.mark.asyncio
    async def test_owner_stop_inside_release_skips_connect(
        self, radio: IcomRadio
    ) -> None:
        radio._connected = False
        radio._intentional_disconnect = False
        statuses: list[dict[str, Any]] = []
        radio._on_reconnect_status = statuses.append

        async def owner_stop_during_release() -> None:
            radio._owner_stop_epoch += 1

        with (
            patch.object(
                radio._session_lifecycle,
                "disconnect",
                new=AsyncMock(side_effect=owner_stop_during_release),
            ),
            patch.object(radio, "connect", new=AsyncMock()) as connect,
        ):
            await asyncio.wait_for(radio._reconnect_loop(), timeout=2.0)
        assert connect.await_count == 0
        assert [s["state"] for s in statuses] == ["reconnecting"]

    @pytest.mark.asyncio
    async def test_owner_stop_inside_connect_finishes_the_stop(
        self, radio: IcomRadio
    ) -> None:
        radio._connected = False
        radio._intentional_disconnect = False
        statuses: list[dict[str, Any]] = []
        radio._on_reconnect_status = statuses.append

        async def connect_with_owner_stop() -> None:
            radio._owner_stop_epoch += 1

        with (
            patch.object(radio._session_lifecycle, "disconnect", new=AsyncMock()),
            patch.object(
                radio, "connect", new=AsyncMock(side_effect=connect_with_owner_stop)
            ),
            patch.object(radio, "disconnect", new=AsyncMock()) as disconnect,
        ):
            await asyncio.wait_for(radio._reconnect_loop(), timeout=2.0)
        assert disconnect.await_count == 1
        assert [s["state"] for s in statuses] == ["reconnecting"]

    @pytest.mark.asyncio
    async def test_owner_stop_then_failed_connect_stays_disconnected(
        self, radio: IcomRadio
    ) -> None:
        radio._connected = False
        radio._intentional_disconnect = False
        statuses: list[dict[str, Any]] = []
        radio._on_reconnect_status = statuses.append

        async def connect_fails_after_owner_stop() -> None:
            radio._owner_stop_epoch += 1
            radio._conn_state = RadioConnectionState.DISCONNECTED
            raise ConnectionError("radio gone")

        with (
            patch.object(radio._session_lifecycle, "disconnect", new=AsyncMock()),
            patch.object(
                radio,
                "connect",
                new=AsyncMock(side_effect=connect_fails_after_owner_stop),
            ) as connect,
        ):
            await asyncio.wait_for(radio._reconnect_loop(), timeout=2.0)
        assert connect.await_count == 1
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        assert [s["state"] for s in statuses] == ["reconnecting"]

    @pytest.mark.asyncio
    async def test_owner_stop_during_backoff_starts_no_round(
        self, radio: IcomRadio
    ) -> None:
        radio._connected = False
        radio._intentional_disconnect = False
        statuses: list[dict[str, Any]] = []
        radio._on_reconnect_status = statuses.append

        async def owner_stop_during_backoff(delay: float) -> None:
            radio._owner_stop_epoch += 1

        with (
            patch.object(
                radio._session_lifecycle, "disconnect", new=AsyncMock()
            ) as release,
            patch.object(
                radio, "connect", new=AsyncMock(side_effect=OSError("radio gone"))
            ),
            patch(
                "asyncio.sleep",
                new=AsyncMock(side_effect=owner_stop_during_backoff),
            ),
        ):
            await asyncio.wait_for(radio._reconnect_loop(), timeout=2.0)
        assert release.await_count == 1
        assert [s["state"] for s in statuses] == ["reconnecting"]


class TestReadinessAbortInsideRecovery:
    @pytest.mark.asyncio
    async def test_readiness_abort_in_full_connect_recovery_keeps_recovering(
        self,
    ) -> None:
        """A startup-readiness abort in ``soft_reconnect``'s full-connect
        branch fails that recovery attempt only: no owner stop, no
        cancellation of the recovery task, and the next attempt connects."""
        radio = IcomRadio("192.168.1.100", username="u", password="p", model="IC-7610")
        mt = ConnectMockTransport()
        radio._ctrl_transport = mt
        mt.queue_response(_build_conninfo())
        mt.queue_response(_build_conninfo())
        lifecycle = radio._session_lifecycle
        lifecycle._recovery_backoff_s = (0.0,)
        events: list[LifecycleEvent] = []
        lifecycle.add_event_listener(events.append)
        aborted_in: list[asyncio.Task[Any]] = []
        readiness_calls = 0

        async def readiness_fails_once(*args: object, **kwargs: object) -> None:
            nonlocal readiness_calls
            readiness_calls += 1
            if not aborted_in:
                task = asyncio.current_task()
                assert task is not None
                aborted_in.append(task)
                raise RuntimeError("injected startup-readiness failure")

        try:
            with (
                _ConnectOnceStubs(radio),
                patch.object(
                    radio, "_gate_lan_identity", new=AsyncMock(return_value=True)
                ),
                patch(
                    "rigplane.runtime._control_phase.wait_for_radio_startup_ready",
                    new=readiness_fails_once,
                ),
                patch.object(radio, "rearm_managed_tx", new=AsyncMock()),
                patch.object(radio, "_ensure_audio_transport", new=AsyncMock()),
            ):
                await asyncio.wait_for(radio.soft_reconnect(), timeout=5.0)
            assert readiness_calls == 2
            assert aborted_in[0].cancelling() == 0
            assert [e.to_state for e in events] == [
                LifecycleState.RECOVERING,
                LifecycleState.RECOVERING,
                LifecycleState.CONNECTED,
            ]
            assert radio._conn_state is RadioConnectionState.CONNECTED
            assert radio._owner_stop_epoch == 0
        finally:
            radio._conn_state = RadioConnectionState.DISCONNECTED


class TestAutoReconnectConfig:
    def test_default_off(self) -> None:
        r = IcomRadio("192.168.1.100", model="IC-7610")
        assert r._auto_reconnect is False

    def test_enabled(self) -> None:
        r = IcomRadio("192.168.1.100", auto_reconnect=True, model="IC-7610")
        assert r._auto_reconnect is True
        assert r._reconnect_delay == 2.0
        assert r._reconnect_max_delay == 60.0
        assert r._watchdog_timeout == 30.0

    def test_custom_delays(self) -> None:
        r = IcomRadio(
            "192.168.1.100",
            auto_reconnect=True,
            reconnect_delay=5.0,
            reconnect_max_delay=120.0,
            watchdog_timeout=15.0,
            model="IC-7610",
        )
        assert r._reconnect_delay == 5.0
        assert r._reconnect_max_delay == 120.0
        assert r._watchdog_timeout == 15.0


class TestReconnectPermanentErrors:
    """Reconnect should abort immediately on permanent errors (#472)."""

    @pytest.mark.asyncio
    async def test_auth_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """AuthenticationError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = AuthenticationError("wrong password")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            # Should have tried only once
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_value_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """ValueError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = ValueError("invalid config")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_type_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """TypeError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = TypeError("bad arg")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_transient_error_retries(self, radio: IcomRadio) -> None:
        """Transient errors (OSError) should still retry."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = OSError("network unreachable")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            # Let it retry a few times (delay starts at 0.1)
            await asyncio.sleep(0.35)
            assert mock_connect.call_count >= 2
            assert radio._conn_state == RadioConnectionState.RECONNECTING
            # Clean up
            radio._intentional_disconnect = True
            radio._control_phase._stop_reconnect()
            await asyncio.sleep(0.05)


class TestExternalCatSessionResetOnConnect:
    """Regression for #1702: a leaked external-CAT session must not survive a
    (re)connect, or the cooperating pollers stay paused forever and rigctld/web
    serve a frozen ``radio_state`` (e.g. the FT8 default freq)."""

    @pytest.mark.asyncio
    async def test_connect_resets_leaked_external_cat_session(
        self, radio: IcomRadio
    ) -> None:
        """A begin_external_cat_session() that was never matched by an end()
        (managed runtime crash/restart, dropped Hamlib bridge) leaves the flag
        set. connect() must clear it so pollers resume after reconnect."""
        # Simulate a leaked external-CAT session (no matching end()).
        radio.begin_external_cat_session()
        assert radio.external_cat_session_active is True

        # Drive connect() without hardware: stub the single-attempt handshake
        # (``_connect_once``; A3 routes connect() through the lifecycle which
        # drives the control-phase mechanism) and the initial-state fetch so
        # only the reset behaviour under test runs. The stubbed link has no
        # CI-V endpoint, so the MOR-3064 ``19 00`` identity read is answered
        # directly with the IC-7610 token 0x98 (rigs/ic7610.toml
        # ``[identity]``); classification and the gate verdict stay real.
        with (
            patch.object(radio._control_phase, "_connect_once", new_callable=AsyncMock),
            patch.object(radio, "_fetch_initial_state", new_callable=AsyncMock),
            patch.object(
                radio,
                "_read_lan_identity_payload",
                new=AsyncMock(return_value=b"\x98"),
            ),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
        ):
            await radio.connect()

        assert radio.external_cat_session_active is False
        assert radio._external_cat_session_owner is None

    @pytest.mark.asyncio
    async def test_connect_keeps_session_clear_when_not_leaked(
        self, radio: IcomRadio
    ) -> None:
        """The reset is a no-op on a clean connect (no false-positive churn)."""
        assert radio.external_cat_session_active is False
        with (
            patch.object(radio._control_phase, "_connect_once", new_callable=AsyncMock),
            patch.object(radio, "_fetch_initial_state", new_callable=AsyncMock),
            patch.object(
                radio,
                "_read_lan_identity_payload",
                new=AsyncMock(return_value=b"\x98"),
            ),
            patch.object(radio._civ_runtime, "start_data_watchdog"),
        ):
            await radio.connect()
        assert radio.external_cat_session_active is False


class TestReconnectStatusCallback:
    """MOR-594: reconnect status surfaced via callback at each meaningful edge."""

    @pytest.mark.asyncio
    async def test_reports_each_attempt_then_connected(self, radio: IcomRadio) -> None:
        """Each attempt emits ``reconnecting`` with attempt + backoff; success
        emits ``connected``."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        reconnecting = [s for s in statuses if s["state"] == "reconnecting"]
        assert [s["attempt"] for s in reconnecting] == [1, 2, 3]
        # Backoff: reconnect_delay=0.1 doubling toward reconnect_max_delay=0.5.
        assert [s["next_retry_seconds"] for s in reconnecting] == [0.1, 0.2, 0.4]
        assert statuses[-1] == {
            "state": "connected",
            "attempt": 3,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_raising_callback_does_not_break_reconnect(
        self, radio: IcomRadio
    ) -> None:
        """A raising status callback must never break the reconnect loop."""

        def _boom(_status: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        radio.set_reconnect_status_callback(_boom)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert mock_connect.call_count == 2

    @pytest.mark.asyncio
    async def test_permanent_error_reports_disconnected(self, radio: IcomRadio) -> None:
        """A permanent-error abort must not leave the status stuck at
        ``reconnecting`` (no silent problems)."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = AuthenticationError("wrong password")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert statuses[-1] == {
            "state": "disconnected",
            "attempt": 1,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_watchdog_trigger_reports_first_attempt(
        self, radio: IcomRadio
    ) -> None:
        """The watchdog trigger point surfaces ``reconnecting`` immediately."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._control_phase._start_watchdog()
        await asyncio.sleep(0.6)  # > watchdog_timeout (0.3 s), no activity
        assert statuses, "watchdog trigger must surface a reconnecting status"
        assert statuses[0]["state"] == "reconnecting"
        assert statuses[0]["attempt"] == 1
        assert statuses[0]["next_retry_seconds"] == 0.1
        # Clean up
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        radio._control_phase._stop_watchdog()
        await asyncio.sleep(0.05)


def _web_radio_stub() -> SimpleNamespace:
    """Minimal radio surface for WebServer runtime-payload tests (MOR-581 shape)."""
    return SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
    )


class TestWebReconnectStatusSurface:
    """MOR-594: runtime payload ``connection`` block + ``connection_status``
    WS event — local-only surfacing, no telemetry."""

    @pytest.mark.asyncio
    async def test_runtime_payload_defaults_to_radio_connected(self) -> None:
        srv = WebServer(_web_radio_stub(), WebConfig(host="127.0.0.1", port=0))
        writer = _FakeWriter()
        await srv._handle_http(writer, "GET", "/api/v1/runtime")
        status, data = _response_json(writer)
        assert status == 200
        assert data["connection"] == {
            "state": "connected",
            "attempt": 0,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_runtime_payload_reflects_latest_status(self) -> None:
        srv = WebServer(_web_radio_stub(), WebConfig(host="127.0.0.1", port=0))
        srv._on_reconnect_status(
            {"state": "reconnecting", "attempt": 3, "next_retry_seconds": 4.0}
        )
        writer = _FakeWriter()
        await srv._handle_http(writer, "GET", "/api/v1/runtime")
        status, data = _response_json(writer)
        assert status == 200
        assert data["connection"] == {
            "state": "reconnecting",
            "attempt": 3,
            "next_retry_seconds": 4.0,
        }

    @pytest.mark.asyncio
    async def test_status_events_broadcast_on_control_ws_queues(
        self, radio: IcomRadio
    ) -> None:
        """Reconnect statuses are forwarded as ``connection_status`` events on
        the EXISTING control-WS event surface."""
        srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        srv._attach_reconnect_status_listener()  # start() wiring
        q: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=16)
        srv.register_control_event_queue(q)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        events: list[dict[str, Any]] = []
        while not q.empty():
            events.append(q.get_nowait())
        conn = [e for e in events if e.get("name") == "connection_status"]
        assert [e["data"]["state"] for e in conn] == [
            "reconnecting",
            "reconnecting",
            "connected",
        ]
        assert [e["data"]["attempt"] for e in conn] == [1, 2, 2]
        assert conn[0]["data"]["next_retry_seconds"] == 0.1
        assert conn[-1]["data"]["next_retry_seconds"] is None
        srv.unregister_control_event_queue(q)
        # stop() detaches — the radio must not keep a stale server reference.
        srv._detach_reconnect_status_listener()
        assert radio._on_reconnect_status is None
