"""Tests for RadioPoller — lifecycle, cache updates, error resilience."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.exceptions import (
    ConnectionError as IcomConnectionError,
    TimeoutError as IcomTimeoutError,
)
from rigplane.rigctld.circuit_breaker import CircuitBreaker, CircuitState
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.poller import RadioPoller
from rigplane.rigctld.state_cache import StateCache
from rigplane.types import Mode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def config() -> RigctldConfig:
    # Very short poll interval so tests run quickly.
    return RigctldConfig(poll_interval=0.01)


@pytest.fixture
def cache() -> StateCache:
    return StateCache()


@pytest.fixture
def mock_radio() -> AsyncMock:
    radio = AsyncMock()
    radio.get_freq.return_value = 14_074_000
    radio.get_mode_info.return_value = (Mode.USB, 1)
    radio.get_data_mode.return_value = False
    return radio


@pytest.fixture
def poller(
    mock_radio: AsyncMock, cache: StateCache, config: RigctldConfig
) -> RadioPoller:
    return RadioPoller(mock_radio, cache, config)


class _ContractModeRadio:
    def __init__(
        self,
        *,
        freq: int = 14_074_000,
        mode: str = "USB",
        filter_width: int | None = 1,
        data_mode: bool = False,
    ) -> None:
        self.freq = freq
        self.mode = mode
        self.filter_width = filter_width
        self.data_mode = data_mode
        self.get_mode_calls = 0

    async def get_freq(self, receiver: int = 0) -> int:
        assert receiver == 0
        return self.freq

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        assert receiver == 0
        self.get_mode_calls += 1
        return self.mode, self.filter_width

    async def get_data_mode(self) -> bool:
        return self.data_mode


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


async def test_start_creates_task(poller: RadioPoller) -> None:
    await poller.start()
    assert poller._task is not None
    await poller.stop()


async def test_stop_clears_task(poller: RadioPoller) -> None:
    await poller.start()
    await poller.stop()
    assert poller._task is None


async def test_start_is_idempotent(poller: RadioPoller) -> None:
    await poller.start()
    task_first = poller._task
    await poller.start()  # second call should be a no-op
    assert poller._task is task_first
    await poller.stop()


async def test_stop_before_start_is_safe(poller: RadioPoller) -> None:
    # Calling stop() before start() must not raise.
    await poller.stop()
    assert poller._task is None


async def test_double_stop_is_safe(poller: RadioPoller) -> None:
    await poller.start()
    await poller.stop()
    await poller.stop()  # second stop must not raise


# ---------------------------------------------------------------------------
# Cache updates
# ---------------------------------------------------------------------------


async def test_poll_updates_freq_in_cache(
    poller: RadioPoller,
    cache: StateCache,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_freq.return_value = 7_050_000
    await poller.start()
    # Wait long enough for at least one poll cycle.
    await asyncio.sleep(0.05)
    await poller.stop()
    assert cache.freq == 7_050_000
    assert cache.is_fresh("freq", 1.0) is True


async def test_poll_updates_mode_in_cache(
    poller: RadioPoller,
    cache: StateCache,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.CW, 3)
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert cache.mode == "CW"
    assert cache.filter_width == 3
    assert cache.is_fresh("mode", 1.0) is True


async def test_poll_converts_mode_enum_to_hamlib_string(
    poller: RadioPoller,
    cache: StateCache,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_mode_info.return_value = (Mode.LSB, None)
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert cache.mode == "LSB"
    assert cache.filter_width is None


async def test_poll_calls_radio_multiple_times(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    # With interval=0.01s and sleep=0.05s, expect at least 3 cycles.
    assert mock_radio.get_freq.await_count >= 3


async def test_poll_falls_back_to_core_radio_mode_contract(
    cache: StateCache,
    config: RigctldConfig,
) -> None:
    radio = _ContractModeRadio(mode="CW", filter_width=3, data_mode=True)
    poller = RadioPoller(radio, cache, config)
    await poller._poll_once()
    assert cache.mode == "CW"
    assert cache.filter_width == 3
    assert cache.data_mode is True
    assert radio.get_mode_calls == 1


# ---------------------------------------------------------------------------
# write_busy — skip cycle
# ---------------------------------------------------------------------------


async def test_write_busy_skips_poll(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    poller.write_busy = True
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    # Poller should not have called the radio at all while busy.
    mock_radio.get_freq.assert_not_awaited()


async def test_write_busy_released_resumes_polling(
    poller: RadioPoller,
    cache: StateCache,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_freq.return_value = 14_074_000
    poller.write_busy = True
    await poller.start()
    await asyncio.sleep(0.03)

    # Release the write lock; polling should resume.
    poller.write_busy = False
    await asyncio.sleep(0.05)
    await poller.stop()

    assert cache.is_fresh("freq", 1.0) is True


# ---------------------------------------------------------------------------
# Error resilience
# ---------------------------------------------------------------------------


async def test_timeout_on_get_frequency_does_not_crash(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_freq.side_effect = IcomTimeoutError("timeout")
    await poller.start()
    await asyncio.sleep(0.05)
    # Poller must still be running after timeout errors.
    assert poller._task is not None
    assert not poller._task.done()
    await poller.stop()


async def test_connection_error_on_get_frequency_does_not_crash(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_freq.side_effect = IcomConnectionError("lost")
    await poller.start()
    await asyncio.sleep(0.05)
    assert poller._task is not None
    assert not poller._task.done()
    await poller.stop()


async def test_timeout_on_get_mode_info_does_not_crash(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_mode_info.side_effect = IcomTimeoutError("timeout")
    await poller.start()
    await asyncio.sleep(0.05)
    assert poller._task is not None
    assert not poller._task.done()
    await poller.stop()


async def test_connection_error_on_get_mode_info_does_not_crash(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    mock_radio.get_mode_info.side_effect = IcomConnectionError("lost")
    await poller.start()
    await asyncio.sleep(0.05)
    assert poller._task is not None
    assert not poller._task.done()
    await poller.stop()


async def test_freq_error_does_not_prevent_mode_poll(
    poller: RadioPoller,
    cache: StateCache,
    mock_radio: AsyncMock,
) -> None:
    """Even if get_frequency fails, get_mode_info should still be called."""
    mock_radio.get_freq.side_effect = IcomTimeoutError("timeout")
    mock_radio.get_mode_info.return_value = (Mode.AM, 2)
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert cache.mode == "AM"
    assert cache.is_fresh("mode", 1.0) is True


async def test_timeout_logs_warning(
    poller: RadioPoller,
    mock_radio: AsyncMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_radio.get_freq.side_effect = IcomTimeoutError("timed out")
    import logging

    with caplog.at_level(logging.WARNING, logger="rigplane.rigctld.poller"):
        await poller.start()
        await asyncio.sleep(0.05)
        await poller.stop()
    assert any("get_freq" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Stop cancels the background task
# ---------------------------------------------------------------------------


async def test_stop_cancels_running_task(
    poller: RadioPoller,
) -> None:
    await poller.start()
    task = poller._task
    assert task is not None
    await poller.stop()
    assert task.cancelled() or task.done()


async def test_no_more_polls_after_stop(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    await poller.start()
    await asyncio.sleep(0.03)
    await poller.stop()
    count_after_stop = mock_radio.get_freq.await_count
    # Give a couple more intervals; count must not increase.
    await asyncio.sleep(0.05)
    assert mock_radio.get_freq.await_count == count_after_stop


# ---------------------------------------------------------------------------
# poll_interval respected
# ---------------------------------------------------------------------------


async def test_poll_interval_is_respected(
    mock_radio: AsyncMock,
    cache: StateCache,
) -> None:
    # Use a longer interval so we can assert exact call count.
    config = RigctldConfig(poll_interval=0.05)
    p = RadioPoller(mock_radio, cache, config)
    await p.start()
    await asyncio.sleep(0.12)  # ~2 cycles at 0.05s
    await p.stop()
    # Expect 2 or 3 calls (timing-dependent but bounded).
    count = mock_radio.get_freq.await_count
    assert 1 <= count <= 4, f"unexpected call count: {count}"


# ---------------------------------------------------------------------------
# Circuit breaker integration
# ---------------------------------------------------------------------------


@pytest.fixture
def cb() -> CircuitBreaker:
    return CircuitBreaker(failure_threshold=3, recovery_timeout=5.0)


async def test_poller_skips_poll_when_circuit_open(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
    cb: CircuitBreaker,
) -> None:
    """When circuit is OPEN, poller must not call the radio."""
    # Pre-open the circuit by recording three failures.
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)
    await p.start()
    await asyncio.sleep(0.05)
    await p.stop()

    mock_radio.get_freq.assert_not_awaited()


async def test_poller_records_success_on_successful_poll(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
    cb: CircuitBreaker,
) -> None:
    """Successful get_frequency should keep/close the circuit."""
    mock_radio.get_freq.return_value = 14_074_000
    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)
    await p.start()
    await asyncio.sleep(0.05)
    await p.stop()

    assert cb.state == CircuitState.CLOSED
    assert cb.consecutive_failures == 0


async def test_poller_records_failure_on_timeout(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
) -> None:
    """Consecutive timeouts should open the circuit."""
    cb = CircuitBreaker(failure_threshold=2, recovery_timeout=5.0)
    mock_radio.get_freq.side_effect = IcomTimeoutError("timeout")

    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)
    await p.start()
    await asyncio.sleep(0.05)
    await p.stop()

    # After two+ consecutive failures the circuit must be OPEN.
    assert cb.state == CircuitState.OPEN


async def test_poller_probe_on_half_open_success(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
    cb: CircuitBreaker,
) -> None:
    """When circuit is HALF_OPEN, a successful get_freq probe closes it."""
    # Force HALF_OPEN by pre-opening and patching time.
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()
    assert cb.state == CircuitState.OPEN

    import time as _time

    future_now = _time.monotonic() + 10.0
    mock_radio.get_freq.return_value = 14_074_000

    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)

    with patch(
        "rigplane.rigctld.circuit_breaker.time.monotonic", return_value=future_now
    ):
        # The state property now sees elapsed time → HALF_OPEN.
        assert cb.state == CircuitState.HALF_OPEN
        await p._poll_once()

    assert cb.state == CircuitState.CLOSED


async def test_poller_probe_on_half_open_failure_reopens(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
    cb: CircuitBreaker,
) -> None:
    """When circuit is HALF_OPEN, a failing get_freq probe re-opens it."""
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()

    import time as _time

    future_now = _time.monotonic() + 10.0
    mock_radio.get_freq.side_effect = IcomTimeoutError("timeout")

    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)

    with patch(
        "rigplane.rigctld.circuit_breaker.time.monotonic", return_value=future_now
    ):
        assert cb.state == CircuitState.HALF_OPEN
        await p._poll_once()

    assert cb._state == CircuitState.OPEN


async def test_poller_probe_does_not_poll_mode(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
    cb: CircuitBreaker,
) -> None:
    """HALF_OPEN probe must NOT call get_mode_info (only get_freq)."""
    cb.record_failure()
    cb.record_failure()
    cb.record_failure()

    import time as _time

    future_now = _time.monotonic() + 10.0
    mock_radio.get_freq.return_value = 14_074_000

    p = RadioPoller(mock_radio, cache, config, circuit_breaker=cb)

    with patch(
        "rigplane.rigctld.circuit_breaker.time.monotonic", return_value=future_now
    ):
        assert cb.state == CircuitState.HALF_OPEN
        await p._poll_once()

    mock_radio.get_mode_info.assert_not_awaited()


async def test_poller_without_circuit_breaker_still_works(
    mock_radio: AsyncMock,
    cache: StateCache,
    config: RigctldConfig,
) -> None:
    """Poller with no circuit breaker should behave exactly as before."""
    mock_radio.get_freq.return_value = 7_000_000
    p = RadioPoller(mock_radio, cache, config)  # no circuit_breaker
    await p.start()
    await asyncio.sleep(0.05)
    await p.stop()

    assert cache.freq == 7_000_000
    assert cache.is_fresh("freq", 1.0) is True


async def test_external_cat_session_quiesces_poller(
    poller: RadioPoller,
    mock_radio: AsyncMock,
) -> None:
    """While an external CAT session owns the wire, the poller issues no radio I/O.

    (MOR-166 slice 2 — the Hamlib bridge sets this flag so RigPlane's own
    polling does not pollute the external master's byte stream.)
    """
    mock_radio.get_freq.return_value = 7_000_000
    mock_radio.external_cat_session_active = True
    await poller.start()
    await asyncio.sleep(0.05)
    await poller.stop()
    assert mock_radio.get_freq.await_count == 0
