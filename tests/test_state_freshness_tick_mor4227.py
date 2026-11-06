"""MOR-4227: StateFreshnessService resolves availability once per tick.

The tick's availability work (``_discard_declared_absent``'s snapshot scan
plus the ``due_requests`` resolution) reads only stored values and path
presence. The tick resolves ONCE per tick through
``AcquisitionScheduler.note_availability`` (the one existing map — round-1
finding 2 killed the service's second cache) and skips its own O(N)
discard scan while the store counters over its own store's lineage stand
still. These tests pin that split and, critically, that the map a
``due_requests`` pass receives still equals a fresh resolution after
every tick, discard or not.
"""

from __future__ import annotations

from typing import Any

import rigplane.core.acquisition_scheduler as scheduler_module
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    StateFreshnessService,
    StateSnapshot,
    _availability_counters,
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


def test_unchanged_store_tick_resolves_once_and_skips_the_discard_scan(
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

    monkeypatch.setattr(scheduler_module, "resolve_available_when", counting_resolve)

    service.tick(now=1.0)
    # The first tick also primes never-observed fields; the availability
    # work itself is ONE shared resolution (before MOR-4227 the tick
    # resolved twice).
    first_calls = calls
    assert first_calls >= 1
    scanned = service._scan_counters
    assert scanned is not None

    # Nothing changed between ticks: the discard scan stays skipped while
    # each tick still resolves its one shared map.
    service.tick(now=1.05)
    assert calls == first_calls + 1
    service.tick(now=1.10)
    assert calls == first_calls + 2
    assert service._scan_counters == scanned

    # A new observation moves a counter: the scan comes back.
    store.apply(_observation(mode, "FM", at=1.15))
    service.tick(now=1.15)
    assert calls == first_calls + 3
    assert service._scan_counters != scanned


def test_note_availability_resolves_per_call_across_store_lineages() -> None:
    """Round-1 finding 2: ``note_availability`` re-resolves on every call;
    no counter-keyed cache on the scheduler. Two fresh stores with the
    same write pattern collide on the StateSnapshot counters while
    differing in content — a counter-keyed scheduler cache served the
    stale resolution and broke the dispatch gate (caught by the full
    suite on tests/test_acquisition_scheduler.py's hand-built snapshots).
    """
    service, _store = _service()
    scheduler = service._scheduler
    assert scheduler is not None
    mode = FieldPath.active("main", "freq_mode", "mode")
    notch = FieldPath.receiver("main", "operator_controls", "manual_notch_freq")

    def hand_built(value: str) -> StateSnapshot:
        store = StateStore()
        store.apply(_observation(mode, value, at=1.0))
        return store.snapshot()

    usb, fm = hand_built("USB"), hand_built("FM")
    assert _availability_counters(usb) == _availability_counters(fm)

    assert scheduler.note_availability(usb)[notch] is True
    assert scheduler.note_availability(fm)[notch] is False


def test_cached_availability_equals_a_fresh_resolution_after_every_tick() -> None:
    service, store = _service()
    scheduler = service._scheduler
    assert scheduler is not None
    profile = scheduler._profile
    mode = FieldPath.active("main", "freq_mode", "mode")

    for step, value in enumerate(("USB", "USB", "FM", "FM", "USB")):
        store.apply(_observation(mode, value, at=float(step)))
        service.tick(now=float(step))
        # The map due_requests received (now cached on the scheduler, the
        # one mechanism) still equals a fresh resolution after every tick.
        assert scheduler._availability == resolve_available_when(
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
