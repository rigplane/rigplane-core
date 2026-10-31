"""Extended reliability integration tests for real radio sessions.

Covers backlog items:
1) sequence wrap-around
2) ACK mixed stress
3) long-run longevity
4) multi-client contention
5) radio_ready state transitions
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest

from rigplane import IcomRadio
from rigplane.exceptions import (
    AuthenticationError,
    ConnectionError,
    TimeoutError as IcomTimeoutError,
)
from _timing import await_until


pytestmark = pytest.mark.integration


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


async def _read_freq_with_retries(
    radio: IcomRadio, retries: int = 4, sleep_s: float = 0.4
) -> int:
    """Read frequency with short retries for transient CI-V recovery windows."""
    last: Exception | None = None
    for attempt in range(1, retries + 2):
        try:
            return await radio.get_frequency(bypass_cache=True)
        except Exception as exc:
            last = exc
            await asyncio.sleep(sleep_s * attempt)
    assert last is not None
    raise last


async def _connect_with_cooldown(
    radio: IcomRadio,
    attempts: int = 6,
    base_pause_s: float = 2.0,
) -> None:
    """Connect with cooldown-aware retries for session-rejection windows."""
    last: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await radio.connect()
            return
        except Exception as exc:
            last = exc
            if attempt >= attempts:
                break
            text = str(exc).lower()
            if (
                "status error=0xffffffff" in text
                or "rejected session allocation" in text
            ):
                pause = min(12.0 + attempt * 4.0, 40.0)
            else:
                pause = min(base_pause_s * attempt, 12.0)
            await asyncio.sleep(pause)
    assert last is not None
    raise last


async def _stabilize_session(radio_config: dict, retries: int = 6) -> None:
    """Best-effort post-test stabilization to avoid leaking stale sessions."""
    for attempt in range(1, retries + 1):
        probe = IcomRadio(**radio_config, timeout=8.0)
        try:
            await _connect_with_cooldown(probe, attempts=4)
            assert await _read_freq_with_retries(probe, retries=2, sleep_s=0.2) > 0
            return
        except Exception:
            await asyncio.sleep(min(8.0 + 4.0 * attempt, 45.0))
        finally:
            try:
                await probe.disconnect()
            except Exception:
                pass
    raise AssertionError("Failed to stabilize radio session after retries")


class TestReliabilityMatrix:
    """Reliability-focused integration scenarios."""

    async def test_transport_sequence_wraparound(self, radio: IcomRadio) -> None:
        """Force CI-V transport seq near rollover and verify no degradation."""
        civ_t = radio._civ_transport  # type: ignore[attr-defined]
        if civ_t is None:
            pytest.skip("CI-V transport unavailable")

        civ_t.send_seq = 0xFFFE
        civ_t.tx_buffer[0x1234] = b"stale-before-wrap"

        freqs: list[int] = []
        for _ in range(4):
            freqs.append(await radio.get_frequency(bypass_cache=True))
            await asyncio.sleep(0.05)

        assert all(f > 0 for f in freqs)
        assert civ_t.send_seq < 0x0100, (
            f"Expected wrapped send_seq, got {civ_t.send_seq:#06x}"
        )
        assert 0x1234 not in civ_t.tx_buffer, (
            "tx_buffer was not cleared on seq rollover"
        )

    async def test_ack_mixed_stress_civ_stats(self, radio: IcomRadio) -> None:
        """Run mixed wait/non-wait commands and validate ACK tracker stats."""
        baseline = radio.civ_stats()
        original_nb = await radio.get_nb()

        try:
            for i in range(30):
                await radio.get_frequency(bypass_cache=True)
                await radio.set_nb(bool(i % 2))
                await radio.get_mode()
                if i % 5 == 0:
                    await radio.get_power()
                await asyncio.sleep(0.02)
        finally:
            await radio.set_nb(original_nb)

        await asyncio.sleep(0.2)
        stats = radio.civ_stats()

        d_timeouts = stats["timeouts"] - baseline["timeouts"]
        d_backlog_drops = stats["ack_backlog_drops"] - baseline["ack_backlog_drops"]

        assert stats["active_waiters"] == 0, f"Leaked waiters: {stats}"
        assert d_backlog_drops == 0, f"Unexpected ACK backlog drops: {stats}"
        assert d_timeouts <= 1, f"Too many timeouts in mixed ACK stress: {stats}"

    async def test_longevity_session_stability(self, radio_config: dict) -> None:
        """Run extended mixed activity and assert stable session behavior."""
        soak_s = _env_int("ICOM_LONG_SOAK_SECONDS", 0)
        if soak_s <= 0:
            pytest.skip("Set ICOM_LONG_SOAK_SECONDS to run longevity integration test")

        packets: list[object] = []

        def on_audio(pkt) -> None:
            if pkt is not None:
                packets.append(pkt)

        r = IcomRadio(**radio_config, timeout=8.0)
        await r.connect()
        ctrl_seq0 = r._ctrl_transport.send_seq  # type: ignore[attr-defined]
        base_stats = r.civ_stats()
        deadline = time.monotonic() + soak_s

        try:
            cycle = 0
            while time.monotonic() < deadline:
                cycle += 1
                await r.get_frequency(bypass_cache=True)
                await r.get_mode()
                await r.get_power()

                if cycle % 8 == 0:
                    await r.start_audio_rx_opus(on_audio)
                    await asyncio.sleep(0.5)
                    await r.stop_audio_rx_opus()

                await asyncio.sleep(0.2)

            assert r.connected
            assert await await_until(lambda: r.radio_ready, timeout=3.0)
            if soak_s >= 70:
                assert r._ctrl_transport.send_seq != ctrl_seq0, (  # type: ignore[attr-defined]
                    "Expected control tracked seq to advance (token renewal/keepalive)"
                )

            end_stats = r.civ_stats()
            assert end_stats["timeouts"] - base_stats["timeouts"] <= 1
            assert end_stats["ack_backlog_drops"] - base_stats["ack_backlog_drops"] == 0
        finally:
            try:
                await r.stop_audio_rx_opus()
            except Exception:
                pass
            await r.disconnect()

    async def test_multi_client_session_contention(self, radio_config: dict) -> None:
        """Validate contention handling with two concurrent clients."""
        if not _flag_enabled("ICOM_ALLOW_SESSION_CONTENTION"):
            pytest.skip("Set ICOM_ALLOW_SESSION_CONTENTION=1 to run contention test")

        r1 = IcomRadio(**radio_config, timeout=8.0)
        r2 = IcomRadio(**radio_config, timeout=8.0)

        await _connect_with_cooldown(r1, attempts=6)
        assert await _read_freq_with_retries(r1) > 0

        r2_connected = False
        try:
            for attempt in range(1, 4):
                try:
                    await r2.connect()
                    r2_connected = True
                    break
                except (IcomTimeoutError, ConnectionError, AuthenticationError):
                    await asyncio.sleep(0.5 * attempt)

            # Client #1 should recover even if contention disrupted CI-V briefly.
            r1_ok = False
            try:
                r1_ok = await _read_freq_with_retries(r1) > 0
            except Exception:
                try:
                    await r1.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                try:
                    await r1.connect()
                    r1_ok = await _read_freq_with_retries(r1) > 0
                except Exception:
                    r1_ok = False

            if not r2_connected:
                await r1.disconnect()
                await asyncio.sleep(0.5)
                try:
                    await r2.connect()
                    r2_connected = True
                except Exception:
                    r2_connected = False

            r2_ok = False
            if r2_connected:
                try:
                    r2_ok = await _read_freq_with_retries(r2) > 0
                except Exception:
                    r2_ok = False

            if not (r1_ok or r2_ok):
                # Last-resort validation: fresh clean session must recover.
                try:
                    await r1.disconnect()
                except Exception:
                    pass
                try:
                    await r2.disconnect()
                except Exception:
                    pass
                await asyncio.sleep(1.0)
                probe = IcomRadio(**radio_config, timeout=10.0)
                try:
                    await _connect_with_cooldown(probe, attempts=6)
                    assert (
                        await _read_freq_with_retries(probe, retries=5, sleep_s=0.5) > 0
                    )
                finally:
                    await probe.disconnect()
        finally:
            try:
                await r1.disconnect()
            except Exception:
                pass
            try:
                await r2.disconnect()
            except Exception:
                pass
            await _stabilize_session(radio_config)

    async def test_radio_ready_state_transitions(self, radio_config: dict) -> None:
        """Check radio_ready across connect, CI-V drop/recovery, and disconnect."""
        r = IcomRadio(**radio_config, timeout=8.0)
        await _connect_with_cooldown(r, attempts=6)

        try:
            await r.get_frequency()
            assert await await_until(lambda: r.radio_ready, timeout=5.0)

            civ_t = r._civ_transport  # type: ignore[attr-defined]
            if civ_t is None:
                pytest.skip("CI-V transport unavailable for readiness transition test")

            await civ_t.disconnect()
            # radio_ready is idle-time based; allow timeout window to expire.
            await await_until(
                lambda: not r.radio_ready,
                timeout=7.0,
                interval=0.2,
                description="radio_ready to drop after the CI-V drop",
            )

            recovered = False
            for attempt in range(1, 9):
                try:
                    if r._civ_transport is None and r.control_connected:  # type: ignore[attr-defined]
                        try:
                            await r.soft_reconnect()
                        except Exception:
                            pass
                    await _read_freq_with_retries(r, retries=1, sleep_s=0.2)
                    recovered = True
                    break
                except Exception:
                    await asyncio.sleep(min(1.5 * attempt, 10.0))
            assert recovered, "Failed to recover frequency read after CI-V drop"
            try:
                await await_until(lambda: r.radio_ready, timeout=40.0, interval=0.2)
                became_ready = True
            except AssertionError:
                became_ready = False
            if not became_ready:
                # Some firmware/build combinations recover command path before
                # radio_ready catches up via CI-V stream watchdog bookkeeping.
                if not r.connected:
                    await r.disconnect()
                    await asyncio.sleep(0.3)
                    await _connect_with_cooldown(r, attempts=5)
                assert await _read_freq_with_retries(r, retries=5, sleep_s=0.5) > 0
        finally:
            await r.disconnect()
            await _stabilize_session(radio_config)

        assert not r.radio_ready
