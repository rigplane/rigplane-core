"""Extra StateCache coverage tests for fields not exercised in test_state_cache.py.

Covers:
- is_fresh() for swr, alc, rf_gain, af_level, attenuator, preamp (lines 136-147)
- update_rf_gain(), update_af_level() (lines 218-224)
- update_attenuator(), update_preamp(), update_swr(), update_alc() freshness
"""

from __future__ import annotations

import time
from typing import Any

import pytest

import rigplane.core._state_cache as state_cache_module
from rigplane.rigctld.state_cache import StateCache
from rigplane.testing.timing import VirtualClock


@pytest.fixture
def cache() -> StateCache:
    return StateCache()


def _scope_time_to(monkeypatch: pytest.MonkeyPatch, clock: VirtualClock) -> None:
    """Point ``_state_cache``'s own ``time`` name at ``clock``.

    The shared ``VirtualClock.scope_to`` also replaces the module's
    ``asyncio`` name, which ``rigplane.core._state_cache`` does not define
    (it only calls ``time.monotonic()``), so the same time view is applied
    here with the shared clock doing the timekeeping — no per-file clock.
    """

    class _TimeView:
        monotonic = staticmethod(clock)

        def __getattr__(self, name: str) -> Any:
            return getattr(time, name)

    monkeypatch.setattr(state_cache_module, "time", _TimeView())


# ---------------------------------------------------------------------------
# is_fresh for additional fields
# ---------------------------------------------------------------------------


def test_is_fresh_swr_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("swr", 1.0) is False


def test_is_fresh_alc_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("alc", 1.0) is False


def test_is_fresh_rf_gain_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("rf_gain", 1.0) is False


def test_is_fresh_af_level_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("af_level", 1.0) is False


def test_is_fresh_attenuator_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("attenuator", 1.0) is False


def test_is_fresh_preamp_never_set_returns_false(cache: StateCache) -> None:
    assert cache.is_fresh("preamp", 1.0) is False


def test_is_fresh_swr_after_update_returns_true(cache: StateCache) -> None:
    cache.update_swr(1.5)
    assert cache.is_fresh("swr", 1.0) is True


def test_is_fresh_alc_after_update_returns_true(cache: StateCache) -> None:
    cache.update_alc(0.8)
    assert cache.is_fresh("alc", 1.0) is True


def test_is_fresh_rf_gain_after_update_returns_true(cache: StateCache) -> None:
    cache.update_rf_gain(200.0)
    assert cache.is_fresh("rf_gain", 1.0) is True


def test_is_fresh_af_level_after_update_returns_true(cache: StateCache) -> None:
    cache.update_af_level(128.0)
    assert cache.is_fresh("af_level", 1.0) is True


def test_is_fresh_attenuator_after_update_returns_true(cache: StateCache) -> None:
    cache.update_attenuator(18)
    assert cache.is_fresh("attenuator", 1.0) is True


def test_is_fresh_preamp_after_update_returns_true(cache: StateCache) -> None:
    cache.update_preamp(1)
    assert cache.is_fresh("preamp", 1.0) is True


# ---------------------------------------------------------------------------
# update_rf_gain
# ---------------------------------------------------------------------------


def test_update_rf_gain_stores_value(cache: StateCache) -> None:
    cache.update_rf_gain(150.0)
    assert cache.rf_gain == pytest.approx(150.0)


def test_update_rf_gain_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_rf_gain(200.0)
    assert cache.rf_gain_ts >= before


# ---------------------------------------------------------------------------
# update_af_level
# ---------------------------------------------------------------------------


def test_update_af_level_stores_value(cache: StateCache) -> None:
    cache.update_af_level(64.0)
    assert cache.af_level == pytest.approx(64.0)


def test_update_af_level_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_af_level(32.0)
    assert cache.af_level_ts >= before


# ---------------------------------------------------------------------------
# update_swr / update_alc
# ---------------------------------------------------------------------------


def test_update_swr_stores_value(cache: StateCache) -> None:
    cache.update_swr(2.3)
    assert cache.swr == pytest.approx(2.3)


def test_update_swr_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_swr(1.0)
    assert cache.swr_ts >= before


def test_update_alc_stores_value(cache: StateCache) -> None:
    cache.update_alc(0.5)
    assert cache.alc == pytest.approx(0.5)


def test_update_alc_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_alc(0.9)
    assert cache.alc_ts >= before


# ---------------------------------------------------------------------------
# update_attenuator / update_preamp
# ---------------------------------------------------------------------------


def test_update_attenuator_stores_value(cache: StateCache) -> None:
    cache.update_attenuator(18)
    assert cache.attenuator == 18


def test_update_attenuator_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_attenuator(0)
    assert cache.attenuator_ts >= before


def test_update_preamp_stores_value(cache: StateCache) -> None:
    cache.update_preamp(2)
    assert cache.preamp == 2


def test_update_preamp_sets_timestamp(
    cache: StateCache, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = VirtualClock(start=1000.0)
    _scope_time_to(monkeypatch, clock)
    before = clock.now
    cache.update_preamp(1)
    assert cache.preamp_ts >= before


# ---------------------------------------------------------------------------
# snapshot includes all new fields
# ---------------------------------------------------------------------------


def test_snapshot_includes_swr(cache: StateCache) -> None:
    cache.update_swr(1.5)
    snap = cache.snapshot()
    assert snap["swr"] == pytest.approx(1.5)
    assert snap["swr_age"] is not None


def test_snapshot_includes_alc(cache: StateCache) -> None:
    cache.update_alc(0.3)
    snap = cache.snapshot()
    assert snap["alc"] == pytest.approx(0.3)
    assert snap["alc_age"] is not None


def test_snapshot_includes_rf_gain(cache: StateCache) -> None:
    cache.update_rf_gain(100.0)
    snap = cache.snapshot()
    assert snap["rf_gain"] == pytest.approx(100.0)
    assert snap["rf_gain_age"] is not None


def test_snapshot_includes_af_level(cache: StateCache) -> None:
    cache.update_af_level(50.0)
    snap = cache.snapshot()
    assert snap["af_level"] == pytest.approx(50.0)
    assert snap["af_level_age"] is not None


def test_snapshot_includes_attenuator(cache: StateCache) -> None:
    cache.update_attenuator(6)
    snap = cache.snapshot()
    assert snap["attenuator"] == 6
    assert snap["attenuator_age"] is not None


def test_snapshot_includes_preamp(cache: StateCache) -> None:
    cache.update_preamp(1)
    snap = cache.snapshot()
    assert snap["preamp"] == 1
    assert snap["preamp_age"] is not None
