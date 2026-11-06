"""MOR-4227: StateFreshnessService skips redundant per-tick rescans.

The tick's availability work (``_discard_declared_absent``'s snapshot scan
plus the ``due_requests`` resolution) reads only stored values and path
presence, both covered by the StateSnapshot counters — so an unchanged
store needs no rescan. These tests pin the skip and, critically, that the
cached map a ``due_requests`` pass receives still equals a fresh
resolution after every tick, discard or not.
"""

from __future__ import annotations

from typing import Any

import rigplane.core.acquisition_scheduler as scheduler_module
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    StateFreshnessService,
    resolve_available_when,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import get_radio_profile


def _observation(path: FieldPath, value: Any, *, at: float) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(source="poll_response", provider="test"),
        timestamp_monotonic=at,
    )


def _service() -> tuple[StateFreshnessService, StateStore]:
    acquisition = get_radio_profile("FTX-1").state_acquisition
    assert acquisition is not None
    store = StateStore()
    return (
        StateFreshnessService(
            store=store,
            scheduler=AcquisitionScheduler(profile=acquisition),
        ),
        store,
    )


def test_unchanged_store_tick_skips_the_availability_rescan(
    monkeypatch: Any,
) -> None:
    service, store = _service()
    mode = FieldPath.active("main", "freq_mode", "mode")
    store.apply(_observation(mode, "USB", at=1.0))

    calls = 0
    real = resolve_available_when

    def counting_resolve(profile: Any, snapshot: Any) -> Any:
        nonlocal calls
        calls += 1
        return real(profile, snapshot)

    monkeypatch.setattr(
        scheduler_module, "resolve_available_when", counting_resolve
    )

    service.tick(now=1.0)
    first_calls = calls
    assert first_calls >= 1

    # Nothing changed between ticks: the rescan is skipped entirely.
    service.tick(now=1.05)
    service.tick(now=1.10)
    assert calls == first_calls

    # A new observation moves a counter: the scan comes back.
    store.apply(_observation(mode, "FM", at=1.15))
    service.tick(now=1.15)
    assert calls > first_calls


def test_cached_availability_equals_a_fresh_resolution_after_every_tick() -> None:
    service, store = _service()
    scheduler = service._scheduler
    assert scheduler is not None
    profile = scheduler._profile
    mode = FieldPath.active("main", "freq_mode", "mode")

    for step, value in enumerate(("USB", "USB", "FM", "FM", "USB")):
        store.apply(_observation(mode, value, at=float(step)))
        service.tick(now=float(step))
        assert service._avail_cached == resolve_available_when(
            profile, store.snapshot()
        ), step


def test_discard_still_happens_after_a_skipped_tick() -> None:
    """The skip must not eat the discard: a contradiction observed after
    any number of no-change ticks still removes the absent field."""
    service, store = _service()
    mode = FieldPath.active("main", "freq_mode", "mode")
    notch = FieldPath.receiver("main", "operator_controls", "manual_notch_freq")

    store.apply(_observation(mode, "USB", at=1.0))
    store.apply(_observation(notch, 1500, at=1.0))
    service.tick(now=1.0)
    assert store.snapshot().field(notch).value == 1500

    # Idle ticks in between — the next real change must still be scanned.
    service.tick(now=1.05)
    service.tick(now=1.10)

    store.apply(_observation(mode, "FM", at=2.0))  # contradicts the clause
    service.tick(now=2.0)
    try:
        store.snapshot().field(notch)
    except KeyError:
        return
    raise AssertionError("the notch field must be discarded once mode is FM")
