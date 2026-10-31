"""Integration tests using MockIcomRadio — no real hardware required.

These tests exercise the full IcomRadio → IcomTransport → UDP stack against a
local mock server, verifying the connection lifecycle and CI-V commands end-to-end.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.state_store import FreshnessState
from rigplane.exceptions import AuthenticationError, CommandError, CommandRejectedError
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import parse_line
from rigplane.types import Mode

from _helpers import observe_selected_receiver
from _perf_helpers import fast_connect
from mock_server import MockIcomRadio


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


class TestConnect:
    async def test_connect_succeeds(self, mock_radio: MockIcomRadio) -> None:
        """Happy-path: connect and disconnect without error."""
        radio = IcomRadio(
            host="127.0.0.1",
            port=mock_radio.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        assert radio.connected
        await radio.disconnect()
        assert not radio.connected

    async def test_context_manager(self, mock_radio: MockIcomRadio) -> None:
        """async with IcomRadio(...) as r: should connect and disconnect cleanly."""
        with fast_connect():
            async with IcomRadio(
                host="127.0.0.1",
                port=mock_radio.control_port,
                username="testuser",
                password="testpass",
                model="IC-7610",
            ) as radio:
                assert radio.connected
        # After __aexit__, should be disconnected
        assert not radio.connected

    async def test_auth_failure(self, mock_radio: MockIcomRadio) -> None:
        """Auth failure returns an informative AuthenticationError."""
        mock_radio.auth_fail = True
        radio = IcomRadio(
            host="127.0.0.1",
            port=mock_radio.control_port,
            username="wronguser",
            password="wrongpass",
            model="IC-7610",
        )
        with fast_connect():
            with pytest.raises(AuthenticationError):
                await radio.connect()
        assert not radio.connected

    async def test_multiple_connect_disconnect_cycles(
        self, mock_radio: MockIcomRadio
    ) -> None:
        """Connect/disconnect can be repeated on the same radio object."""
        with fast_connect():
            for _ in range(3):
                radio = IcomRadio(
                    host="127.0.0.1",
                    port=mock_radio.control_port,
                    username="testuser",
                    password="testpass",
                    model="IC-7610",
                )
                await radio.connect()
                assert radio.connected
                await radio.disconnect()
                assert not radio.connected


# ---------------------------------------------------------------------------
# Frequency
# ---------------------------------------------------------------------------


class TestFrequency:
    async def test_get_frequency_default(self, connected_radio: IcomRadio) -> None:
        freq = await connected_radio.get_freq()
        assert freq == 14_074_000

    async def test_set_and_get_frequency(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        await connected_radio.set_freq(7_074_000)
        assert mock_radio._frequency == 7_074_000
        freq = await connected_radio.get_freq()
        assert freq == 7_074_000

    async def test_set_frequency_updates_internal_state(
        self, connected_radio: IcomRadio
    ) -> None:
        await connected_radio.set_freq(3_573_000)
        freq = await connected_radio.get_freq()
        assert freq == 3_573_000


# ---------------------------------------------------------------------------
# Mode
# ---------------------------------------------------------------------------


class TestMode:
    async def test_get_mode_default(self, connected_radio: IcomRadio) -> None:
        mode_name, _ = await connected_radio.get_mode()
        assert mode_name == "USB"

    async def test_set_and_get_mode(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_mode(Mode.LSB)
        mode_name, _ = await connected_radio.get_mode()
        assert mode_name == "LSB"

    async def test_set_mode_string(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_mode("AM")
        mode_name, _ = await connected_radio.get_mode()
        assert mode_name == "AM"

    async def test_get_mode_info_returns_filter(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio._filter = 2
        _mode, filt = await connected_radio.get_mode_info()
        assert filt == 2


# ---------------------------------------------------------------------------
# Power
# ---------------------------------------------------------------------------


class TestPower:
    async def test_get_power_default(self, connected_radio: IcomRadio) -> None:
        power = await connected_radio.get_rf_power()
        assert power == 100

    async def test_set_and_get_power(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_rf_power(200)
        power = await connected_radio.get_rf_power()
        assert power == 200

    async def test_set_power_zero(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_rf_power(0)
        power = await connected_radio.get_rf_power()
        assert power == 0

    async def test_set_power_max(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_rf_power(255)
        power = await connected_radio.get_rf_power()
        assert power == 255


# ---------------------------------------------------------------------------
# Meters
# ---------------------------------------------------------------------------


class TestMeters:
    async def test_get_s_meter(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_s_meter(150)
        value = await connected_radio.get_s_meter()
        assert value == 150

    async def test_get_swr(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        # IC-7610 profile interpolates raw=42 between (0, 1.0) and
        # (48, 1.5): 1.0 + 42/48 * 0.5 = 1.4375.
        mock_radio.set_swr(42)
        value = await connected_radio.get_swr()
        assert value == pytest.approx(1.4375)

    async def test_get_swr_meter_raw(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_swr(42)
        value = await connected_radio.get_swr_meter()
        assert value == 42

    async def test_s_meter_zero(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_s_meter(0)
        value = await connected_radio.get_s_meter()
        assert value == 0

    async def test_s_meter_max(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_s_meter(255)
        value = await connected_radio.get_s_meter()
        assert value == 255


class TestSquelchStatus:
    """MOR-3630: the 0x15 0x01 squelch-open poll the scheduler now sends."""

    async def test_sql_status_default_closed(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        assert await connected_radio.get_s_meter_sql_status() is False

    async def test_sql_status_open(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_squelch_open(True)
        assert await connected_radio.get_s_meter_sql_status() is True

    async def test_sql_status_closed(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        mock_radio.set_squelch_open(True)
        assert await connected_radio.get_s_meter_sql_status() is True
        mock_radio.set_squelch_open(False)
        assert await connected_radio.get_s_meter_sql_status() is False


# ---------------------------------------------------------------------------
# ATT / PREAMP (Command29)
# ---------------------------------------------------------------------------


class TestAttPreamp:
    async def test_get_attenuator_default_zero(
        self, connected_radio: IcomRadio
    ) -> None:
        level = await connected_radio.get_attenuator_level()
        assert level == 0

    async def test_set_and_get_attenuator(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_attenuator_level(18)
        level = await connected_radio.get_attenuator_level()
        assert level == 18

    async def test_attenuator_bool_on_refused_for_stepped_attenuator(
        self, connected_radio: IcomRadio
    ) -> None:
        """MOR-2086: the mock defaults to IC-7610, which declares 15
        non-zero attenuator steps -- the boolean "on" form has no single
        defined answer there, so it is refused rather than guessing."""
        with pytest.raises(CommandError, match="set_attenuator_level"):
            await connected_radio.set_attenuator(True)

    async def test_attenuator_bool_off(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_attenuator_level(18)
        await connected_radio.set_attenuator(False)
        assert await connected_radio.get_attenuator() is False

    async def test_get_preamp_default_zero(self, connected_radio: IcomRadio) -> None:
        level = await connected_radio.get_preamp()
        assert level == 0

    async def test_set_and_get_preamp(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_preamp(1)
        level = await connected_radio.get_preamp()
        assert level == 1

    async def test_preamp_level_2(self, connected_radio: IcomRadio) -> None:
        await connected_radio.set_preamp(2)
        level = await connected_radio.get_preamp()
        assert level == 2

    async def test_digisel_off_by_default_allows_preamp(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        """set_preamp should succeed when DIGI-SEL is off (default)."""
        mock_radio.set_digisel(False)
        await connected_radio.set_preamp(1)  # must not raise
        # fire-and-forget: allow background packet processing to settle
        for _ in range(20):
            if mock_radio._preamp == 1:
                break
            await asyncio.sleep(0.01)
        assert mock_radio._preamp == 1


# ---------------------------------------------------------------------------
# Keep-alive / ping
# ---------------------------------------------------------------------------


class TestKeepalive:
    async def test_stays_connected_over_time(self, connected_radio: IcomRadio) -> None:
        """Radio stays connected during a short wait (pings should keep it alive)."""
        assert connected_radio.connected
        await asyncio.sleep(0.3)  # > half ping interval — enough to verify keepalive
        assert connected_radio.connected


# ---------------------------------------------------------------------------
# Refused reads (MOR-3297)
# ---------------------------------------------------------------------------


class TestRefusedReadsDoNotBlockTheQueue:
    async def test_refused_menu_reads_settle_fast_and_do_not_delay_set_freq(
        self, mock_radio: MockIcomRadio
    ) -> None:
        """Six NAKed menu reads must not make the queued set_freq wait.

        ``_civ_rx.py: CivRuntime._execute_civ_raw`` waited out the whole
        ``_civ_get_timeout`` window per refused read (a NAK parked in the
        tracker's orphan backlog), so a set queued behind six refusals at
        ``Priority.NORMAL`` reached the wire seconds late. The frames read
        menu item 0x99 0x99 via 0x1A 05 — an item no Icom radio defines, so
        ``MockIcomRadio._dispatch`` answers NAK (unknown command → NAK).
        """
        radio = IcomRadio(
            host="127.0.0.1",
            port=mock_radio.control_port,
            username="testuser",
            password="testpass",
            model="IC-7610",
        )
        try:
            with fast_connect():
                await radio.connect()
            assert radio.connected
            # fast_connect skips the 0x07 0xD2 query: MAIN is selected.
            observe_selected_receiver(radio, 0)
            frames = [
                build_civ_frame(
                    IC_7610_ADDR, CONTROLLER_ADDR, 0x1A, sub=0x05, data=b"\x99\x99"
                )
                for _ in range(6)
            ]
            started = time.monotonic()
            results = await asyncio.gather(
                *(radio._send_civ_raw(frame) for frame in frames),
                radio.set_freq(14_074_000),
                return_exceptions=True,
            )
            elapsed = time.monotonic() - started
        finally:
            await radio.disconnect()

        reads, set_result = results[:6], results[6]
        assert all(isinstance(result, CommandRejectedError) for result in reads), reads
        assert set_result is None, set_result
        assert elapsed < 2.0, f"elapsed={elapsed:.3f}s"


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    async def test_disconnect_marks_not_connected(
        self, connected_radio: IcomRadio
    ) -> None:
        assert connected_radio.connected
        await connected_radio.disconnect()
        assert not connected_radio.connected

    async def test_double_disconnect_is_safe(self, connected_radio: IcomRadio) -> None:
        await connected_radio.disconnect()
        await connected_radio.disconnect()  # must not raise


# ---------------------------------------------------------------------------
# rigctld ingress must not launder the RadioState mirror into the store
# ---------------------------------------------------------------------------


class TestRigctldMirrorFallback:
    async def test_get_freq_does_not_write_stale_mirror_into_state_store(
        self, connected_radio: IcomRadio, mock_radio: MockIcomRadio
    ) -> None:
        """A rigctld ``f`` served from the mirror leaves the store untouched.

        The canonical freq observation is driven STALE (CI-V starved via
        ``stall_for``, then aged past its max_age), so the ``f`` below is
        answered from the ``RadioState`` mirror like before — but the
        mirror value must not be re-stamped into the store as a fresh
        ``state_poller`` observation.
        """
        await connected_radio.set_freq(7_074_000)
        assert await connected_radio.get_freq() == 7_074_000
        # The CI-V read populated both the mirror and the canonical store.
        assert connected_radio.radio_state.main.freq == 7_074_000
        store = connected_radio.state_store
        freq_fields = [
            field
            for field in store.snapshot().fields
            if str(field.path).endswith("freq_mode.freq_hz")
        ]
        assert freq_fields

        mock_radio.stall_for(2.0)
        store.mark_stale_due(now=time.monotonic() + 60.0)
        stale_before = [
            field
            for field in store.snapshot().fields
            if str(field.path).endswith("freq_mode.freq_hz")
        ]
        assert all(
            field.freshness is not FreshnessState.FRESH for field in stale_before
        )

        handler = RigctldHandler(connected_radio, RigctldConfig())
        resp = await handler.execute(parse_line(b"f"))

        assert resp.ok
        assert resp.values == ["7074000"]
        after = [
            field
            for field in store.snapshot().fields
            if str(field.path).endswith("freq_mode.freq_hz")
        ]
        assert after
        for field in after:
            assert field.freshness is FreshnessState.STALE
            assert field.source.source != "state_poller"
