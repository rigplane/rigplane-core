"""Tests for StateCache — freshness checks, update helpers, snapshot."""

from __future__ import annotations

import pytest

import rigplane.core._state_cache as state_cache_module
from rigplane.rigctld.state_cache import StateCache
from rigplane.testing.timing import VirtualClock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def cache() -> StateCache:
    return StateCache()


# ---------------------------------------------------------------------------
# Initial state
# ---------------------------------------------------------------------------


def test_default_freq_is_zero(cache: StateCache) -> None:
    assert cache.freq == 0


def test_default_mode_is_usb(cache: StateCache) -> None:
    assert cache.mode == "USB"


def test_default_filter_width_is_none(cache: StateCache) -> None:
    assert cache.filter_width is None


def test_default_vfo_is_vfoa(cache: StateCache) -> None:
    assert cache.vfo == "VFOA"


def test_default_ptt_is_false(cache: StateCache) -> None:
    assert cache.ptt is False


def test_default_s_meter_is_none(cache: StateCache) -> None:
    assert cache.s_meter is None


def test_default_rf_power_is_none(cache: StateCache) -> None:
    assert cache.rf_power is None


# ---------------------------------------------------------------------------
# is_fresh — never-updated fields
# ---------------------------------------------------------------------------


def test_is_fresh_freq_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("freq", 1.0) is False


def test_is_fresh_mode_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("mode", 1.0) is False


def test_is_fresh_vfo_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("vfo", 1.0) is False


def test_is_fresh_ptt_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("ptt", 1.0) is False


def test_is_fresh_s_meter_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("s_meter", 1.0) is False


def test_is_fresh_rf_power_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("rf_power", 1.0) is False


# ---------------------------------------------------------------------------
# is_fresh — after update
# ---------------------------------------------------------------------------


def test_is_fresh_freq_after_update_returns_true(cache: StateCache) -> None:
    cache.update_freq(14_074_000)
    assert cache.is_fresh("freq", 1.0) is True


def test_is_fresh_mode_after_update_returns_true(cache: StateCache) -> None:
    cache.update_mode("CW", 2)
    assert cache.is_fresh("mode", 1.0) is True


def test_is_fresh_ptt_after_update_returns_true(cache: StateCache) -> None:
    cache.update_ptt(True)
    assert cache.is_fresh("ptt", 1.0) is True


def test_is_fresh_s_meter_after_update_returns_true(cache: StateCache) -> None:
    cache.update_s_meter(120)
    assert cache.is_fresh("s_meter", 1.0) is True


def test_is_fresh_rf_power_after_update_returns_true(cache: StateCache) -> None:
    cache.update_rf_power(0.75)
    assert cache.is_fresh("rf_power", 1.0) is True


# ---------------------------------------------------------------------------
# is_fresh — zero TTL always returns False
# ---------------------------------------------------------------------------


def test_is_fresh_zero_ttl_always_false_after_update(cache: StateCache) -> None:
    cache.update_freq(14_074_000)
    # Even immediately after update, 0.0 TTL means never fresh.
    assert cache.is_fresh("freq", 0.0) is False


def test_is_fresh_zero_ttl_mode(cache: StateCache) -> None:
    cache.update_mode("USB", 1)
    assert cache.is_fresh("mode", 0.0) is False


# ---------------------------------------------------------------------------
# is_fresh — expired (simulate old timestamp)
# ---------------------------------------------------------------------------


def test_is_fresh_expired_when_timestamp_is_old(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    cache.update_freq(14_074_000)
    # Age the entry 10 s on the virtual clock so it looks old.
    clock.advance(10.0)
    assert cache.is_fresh("freq", 5.0) is False


def test_is_fresh_not_expired_within_ttl(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    cache.update_freq(14_074_000)
    # Slightly age but stay within TTL.
    clock.advance(0.1)
    assert cache.is_fresh("freq", 0.5) is True


# ---------------------------------------------------------------------------
# update_freq / invalidate_freq
# ---------------------------------------------------------------------------


def test_update_freq_stores_value(cache: StateCache) -> None:
    cache.update_freq(7_050_000)
    assert cache.freq == 7_050_000


def test_update_freq_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    before = clock.now
    cache.update_freq(14_074_000)
    assert cache.freq_ts >= before


def test_invalidate_freq_clears_timestamp(cache: StateCache) -> None:
    cache.update_freq(14_074_000)
    assert cache.freq_ts > 0.0
    cache.invalidate_freq()
    assert cache.freq_ts == 0.0


def test_invalidate_freq_makes_is_fresh_false(cache: StateCache) -> None:
    cache.update_freq(14_074_000)
    assert cache.is_fresh("freq", 1.0) is True
    cache.invalidate_freq()
    assert cache.is_fresh("freq", 1.0) is False


# ---------------------------------------------------------------------------
# update_mode / invalidate_mode
# ---------------------------------------------------------------------------


def test_update_mode_stores_mode_str(cache: StateCache) -> None:
    cache.update_mode("CW", 3)
    assert cache.mode == "CW"


def test_update_mode_stores_filter_width(cache: StateCache) -> None:
    cache.update_mode("USB", 2)
    assert cache.filter_width == 2


def test_update_mode_stores_none_filter(cache: StateCache) -> None:
    cache.update_mode("LSB", None)
    assert cache.filter_width is None


def test_update_mode_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    before = clock.now
    cache.update_mode("USB", 1)
    assert cache.mode_ts >= before


def test_invalidate_mode_clears_timestamp(cache: StateCache) -> None:
    cache.update_mode("USB", 1)
    assert cache.mode_ts > 0.0
    cache.invalidate_mode()
    assert cache.mode_ts == 0.0


def test_invalidate_mode_makes_is_fresh_false(cache: StateCache) -> None:
    cache.update_mode("USB", 1)
    assert cache.is_fresh("mode", 1.0) is True
    cache.invalidate_mode()
    assert cache.is_fresh("mode", 1.0) is False


# ---------------------------------------------------------------------------
# update_ptt
# ---------------------------------------------------------------------------


def test_update_ptt_true(cache: StateCache) -> None:
    cache.update_ptt(True)
    assert cache.ptt is True


def test_update_ptt_false(cache: StateCache) -> None:
    cache.update_ptt(True)
    cache.update_ptt(False)
    assert cache.ptt is False


def test_update_ptt_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    before = clock.now
    cache.update_ptt(True)
    assert cache.ptt_ts >= before


# ---------------------------------------------------------------------------
# update_s_meter / update_rf_power
# ---------------------------------------------------------------------------


def test_update_s_meter_stores_value(cache: StateCache) -> None:
    cache.update_s_meter(120)
    assert cache.s_meter == 120


def test_update_s_meter_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    before = clock.now
    cache.update_s_meter(0)
    assert cache.s_meter_ts >= before


def test_update_rf_power_stores_value(cache: StateCache) -> None:
    cache.update_rf_power(0.5)
    assert cache.rf_power == pytest.approx(0.5)


def test_update_rf_power_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    clock.scope_to(monkeypatch, state_cache_module)
    before = clock.now
    cache.update_rf_power(1.0)
    assert cache.rf_power_ts >= before


# ---------------------------------------------------------------------------
# snapshot
# ---------------------------------------------------------------------------


def test_snapshot_returns_dict(cache: StateCache) -> None:
    snap = cache.snapshot()
    assert isinstance(snap, dict)


def test_snapshot_contains_all_value_keys(cache: StateCache) -> None:
    snap = cache.snapshot()
    for key in ("freq", "mode", "filter_width", "vfo", "ptt", "s_meter", "rf_power"):
        assert key in snap, f"missing key: {key}"


def test_snapshot_contains_all_age_keys(cache: StateCache) -> None:
    snap = cache.snapshot()
    for key in (
        "freq_age",
        "mode_age",
        "vfo_age",
        "ptt_age",
        "s_meter_age",
        "rf_power_age",
    ):
        assert key in snap, f"missing age key: {key}"


def test_snapshot_age_is_none_for_unset_field(cache: StateCache) -> None:
    snap = cache.snapshot()
    assert snap["freq_age"] is None
    assert snap["mode_age"] is None
    assert snap["s_meter_age"] is None


def test_snapshot_age_is_float_for_set_field(cache: StateCache) -> None:
    cache.update_freq(14_074_000)
    snap = cache.snapshot()
    assert isinstance(snap["freq_age"], float)
    assert snap["freq_age"] >= 0.0  # type: ignore[operator]


def test_snapshot_freq_value_matches(cache: StateCache) -> None:
    cache.update_freq(7_200_000)
    snap = cache.snapshot()
    assert snap["freq"] == 7_200_000


def test_snapshot_mode_value_matches(cache: StateCache) -> None:
    cache.update_mode("LSB", 1)
    snap = cache.snapshot()
    assert snap["mode"] == "LSB"
    assert snap["filter_width"] == 1


def test_snapshot_ptt_value_matches(cache: StateCache) -> None:
    cache.update_ptt(True)
    snap = cache.snapshot()
    assert snap["ptt"] is True


def test_snapshot_s_meter_matches(cache: StateCache) -> None:
    cache.update_s_meter(200)
    snap = cache.snapshot()
    assert snap["s_meter"] == 200


def test_snapshot_rf_power_matches(cache: StateCache) -> None:
    cache.update_rf_power(0.25)
    snap = cache.snapshot()
    assert snap["rf_power"] == pytest.approx(0.25)
