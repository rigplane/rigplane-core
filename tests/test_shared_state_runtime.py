"""Tests for icom_lan._shared_state_runtime helpers."""

from __future__ import annotations

import pytest

from icom_lan.runtime._shared_state_runtime import (
    DEFAULT_STATE_CACHE_TTL,
    is_cache_fresh,
    poll_frequency,
    poll_mode,
    poll_standard_fields,
)
from icom_lan.rigctld.state_cache import StateCache


# ---------------------------------------------------------------------------
# is_cache_fresh (existing tests)
# ---------------------------------------------------------------------------


def test_is_cache_fresh_false_when_ttl_none_or_non_positive() -> None:
    cache = StateCache()
    cache.update_freq(14_074_000)

    assert is_cache_fresh(cache, "freq", None) is False
    assert is_cache_fresh(cache, "freq", 0.0) is False
    assert is_cache_fresh(cache, "freq", -1.0) is False


def test_is_cache_fresh_delegates_to_state_cache() -> None:
    cache = StateCache()
    cache.update_freq(14_074_000)

    # Immediately after update the entry must be fresh.
    assert is_cache_fresh(cache, "freq", DEFAULT_STATE_CACHE_TTL) is True

    # Simulate staleness by moving the timestamp far into the past.
    cache.freq_ts -= 10.0
    assert is_cache_fresh(cache, "freq", DEFAULT_STATE_CACHE_TTL) is False


def test_is_cache_fresh_respects_never_written_fields() -> None:
    cache = StateCache()
    # No writes yet → timestamps are 0.0 and must be treated as stale.
    assert is_cache_fresh(cache, "mode", DEFAULT_STATE_CACHE_TTL) is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRadio:
    """Minimal Radio-like stub used by poll_* tests."""

    def __init__(
        self,
        freq: int = 14_074_000,
        mode: str = "USB",
        filter_width: int | None = 1,
        data_mode: bool = False,
        *,
        raise_on_freq: Exception | None = None,
        raise_on_mode: Exception | None = None,
        raise_on_data_mode: Exception | None = None,
    ) -> None:
        self._freq = freq
        self._mode = mode
        self._filter_width = filter_width
        self._data_mode = data_mode
        self._raise_on_freq = raise_on_freq
        self._raise_on_mode = raise_on_mode
        self._raise_on_data_mode = raise_on_data_mode
        self.get_freq_calls = 0
        self.get_mode_calls = 0
        self.get_data_mode_calls = 0

    async def get_freq(self, receiver: int = 0) -> int:
        self.get_freq_calls += 1
        if self._raise_on_freq is not None:
            raise self._raise_on_freq
        return self._freq

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        self.get_mode_calls += 1
        if self._raise_on_mode is not None:
            raise self._raise_on_mode
        return self._mode, self._filter_width

    async def get_data_mode(self) -> bool:
        self.get_data_mode_calls += 1
        if self._raise_on_data_mode is not None:
            raise self._raise_on_data_mode
        return self._data_mode


# ---------------------------------------------------------------------------
# poll_frequency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_frequency_returns_cached_when_fresh() -> None:
    cache = StateCache()
    cache.update_freq(14_074_000)
    radio = _FakeRadio(freq=21_000_000)  # different value — should not be used

    result = await poll_frequency(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result == 14_074_000
    assert radio.get_freq_calls == 0


@pytest.mark.asyncio
async def test_poll_frequency_polls_radio_when_stale() -> None:
    cache = StateCache()  # freq_ts == 0.0 → stale
    radio = _FakeRadio(freq=7_074_000)

    result = await poll_frequency(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result == 7_074_000
    assert cache.freq == 7_074_000
    assert radio.get_freq_calls == 1


@pytest.mark.asyncio
async def test_poll_frequency_returns_none_on_radio_error() -> None:
    cache = StateCache()
    original_ts = cache.freq_ts  # 0.0 (never written)
    radio = _FakeRadio(raise_on_freq=TimeoutError("simulated timeout"))

    result = await poll_frequency(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result is None
    assert cache.freq_ts == original_ts  # cache unchanged


# ---------------------------------------------------------------------------
# poll_mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_mode_returns_cached_when_fresh() -> None:
    cache = StateCache()
    cache.update_mode("CW", 2)
    radio = _FakeRadio(mode="USB", filter_width=1)  # should not be called

    result = await poll_mode(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result == ("CW", 2)
    assert radio.get_mode_calls == 0


@pytest.mark.asyncio
async def test_poll_mode_polls_radio_when_stale() -> None:
    cache = StateCache()  # mode_ts == 0.0 → stale
    radio = _FakeRadio(mode="LSB", filter_width=3)

    result = await poll_mode(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result == ("LSB", 3)
    assert cache.mode == "LSB"
    assert cache.filter_width == 3
    assert radio.get_mode_calls == 1


@pytest.mark.asyncio
async def test_poll_mode_uses_custom_mode_reader() -> None:
    cache = StateCache()
    radio = _FakeRadio()

    async def custom_reader() -> tuple[str, int | None]:
        return "CWR", 2

    result = await poll_mode(
        radio, cache, DEFAULT_STATE_CACHE_TTL, mode_reader=custom_reader
    )

    assert result == ("CWR", 2)
    assert radio.get_mode_calls == 0  # radio.get_mode not called


@pytest.mark.asyncio
async def test_poll_mode_returns_none_on_radio_error() -> None:
    cache = StateCache()
    original_ts = cache.mode_ts
    radio = _FakeRadio(raise_on_mode=TimeoutError("simulated timeout"))

    result = await poll_mode(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result is None
    assert cache.mode_ts == original_ts  # cache unchanged


# ---------------------------------------------------------------------------
# poll_standard_fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_standard_fields_returns_all_expected_fields() -> None:
    cache = StateCache()
    radio = _FakeRadio(freq=14_074_000, mode="USB", filter_width=1, data_mode=False)

    result = await poll_standard_fields(radio, cache, 0.0)  # ttl=0 → always poll

    assert result["freq"] == 14_074_000
    assert result["mode"] == "USB"
    assert result["filter_width"] == 1
    assert result["data_mode"] is False
    assert radio.get_freq_calls == 1
    assert radio.get_mode_calls == 1
    assert radio.get_data_mode_calls == 1


@pytest.mark.asyncio
async def test_poll_standard_fields_serves_fresh_cache() -> None:
    cache = StateCache()
    cache.update_freq(14_074_000)
    cache.update_mode("CW", 2)
    cache.update_data_mode(True)
    radio = _FakeRadio(freq=7_000_000, mode="USB")  # should not be called

    result = await poll_standard_fields(radio, cache, DEFAULT_STATE_CACHE_TTL)

    assert result["freq"] == 14_074_000
    assert result["mode"] == "CW"
    assert result["data_mode"] is True
    assert radio.get_freq_calls == 0
    assert radio.get_mode_calls == 0
    assert radio.get_data_mode_calls == 0


@pytest.mark.asyncio
async def test_poll_standard_fields_handles_freq_error_gracefully() -> None:
    cache = StateCache()
    radio = _FakeRadio(
        freq=14_074_000,
        mode="USB",
        raise_on_freq=TimeoutError("timeout"),
    )

    result = await poll_standard_fields(radio, cache, 0.0)

    # freq missing from result, but mode and data_mode still populated
    assert "freq" not in result
    assert result["mode"] == "USB"
    assert "data_mode" in result
