"""MOR-2586 (MOR-2574 step 3): the scheduler fits class cadences to the
transport budget and dispatches by class rank."""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from rigplane.backends.ic7300.serial import Ic7300SerialRadio
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionScheduler,
    civ_transport_budget_hz,
    provider_uses_civ_acquisition,
)
from rigplane.core.state_acquisition_policy import (
    ACQUISITION_BUDGET_MARGIN,
    ACQUISITION_CLASS_TABLE,
    FieldCapability,
    RadioAcquisitionProfile,
    fit_to_budget,
)
from rigplane.core.state_pipeline_contracts import (
    AcquisitionClass,
    ChangeSet,
    FieldPath,
    SourceMetadata,
    acquisition_class_for_path,
)
from rigplane.core.state_store import FreshnessClock
from rigplane.profiles.rig_loader import discover_rigs
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebServer
from test_icom7610_serial_radio import _FakeSerialCivLink

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

#: Query budgets: 1 / max(gap, round-trip estimate). LAN gap is 10 ms and
#: the measured IC-7610 p95 round trip is 9 ms, so the LAN cost is the gap.
#: Serial round trip is unmeasured, so its estimate is the 25 ms Icom gap.
_LAN_BUDGET_HZ = 1.0 / 0.010
_SERIAL_BUDGET_HZ = 1.0 / 0.025

_CIV_ACQUISITION: dict[str, RadioAcquisitionProfile] = {
    model: acquisition
    for model, rig in sorted(discover_rigs(RIGS_DIR).items())
    if (acquisition := rig.to_profile().state_acquisition) is not None
    and provider_uses_civ_acquisition(acquisition.provider)
}

_RANK = {klass: rank for rank, klass in enumerate(AcquisitionClass)}
_WINDOW = {False: "receive", True: "transmit"}
_EPSILON = 1e-9


def _base_cadences(scheduler: AcquisitionScheduler) -> dict[FieldPath, float]:
    return {
        FieldPath.parse(path): entry["baseCadenceSeconds"]
        for path, entry in scheduler.diagnostics()["cadenceByPath"].items()
    }


def _next_due(scheduler: AcquisitionScheduler, path: FieldPath) -> float:
    due: float = scheduler.diagnostics()["cadenceByPath"][str(path)]["nextDueMonotonic"]
    return due


def _polled_profile(*paths: FieldPath) -> RadioAcquisitionProfile:
    return RadioAcquisitionProfile(
        provider="test_provider",
        capabilities=tuple(FieldCapability(path=path, polling=True) for path in paths),
    )


def _completed(at: float) -> ChangeSet:
    return ChangeSet(
        revision=0,
        freshness_revision=1,
        observation_seq=1,
        changes=(),
        timestamp_monotonic=at,
        sources=(SourceMetadata(source="poll_response", provider="test_provider"),),
    )


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        record.getMessage()
        for record in caplog.records
        if record.levelno == logging.WARNING
        and record.name == "rigplane.core.acquisition_scheduler"
    ]


# --- (a) every bundled CI-V profile ------------------------------------------


@pytest.mark.parametrize("tx", [False, True], ids=["receive", "transmit"])
@pytest.mark.parametrize(
    "budget_hz", [_LAN_BUDGET_HZ, _SERIAL_BUDGET_HZ], ids=["10ms-gap", "25ms-gap"]
)
@pytest.mark.parametrize("model", sorted(_CIV_ACQUISITION))
def test_fitted_demand_is_within_the_margin_or_the_warning_says_why(
    model: str,
    budget_hz: float,
    tx: bool,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Per profile and window: demand is within 0.75 x budget, or the warning says not.

    Demand is every polled path at the cadence the scheduler reports for
    it, ``tx_only`` paths only in transmit. No poll group mixes explicit
    and class-derived paths, and explicit profile cadences are never
    changed. A class-derived path sits between its class's start cadence
    (nominal, or the ceiling for a class held there in transmit) and its
    ceiling, one cadence per class. When the demand fits, either nothing
    stretched, or it closes on the limit with every class ranked below the
    highest stretched one at its ceiling. When it does not fit, every
    class-derived path is at its start cadence if the explicit demand alone
    is at or over the transport budget, else at its ceiling, and the
    startup warning says which, with this window's numbers.
    """

    acquisition = _CIV_ACQUISITION[model]
    with caplog.at_level(logging.WARNING, logger="rigplane.core.acquisition_scheduler"):
        scheduler = AcquisitionScheduler(
            profile=acquisition,
            transport_budget_hz=budget_hz,
        )
    scheduler.note_tx_active(tx)
    limit = ACQUISITION_BUDGET_MARGIN * budget_hz
    for paths in scheduler._poll_cadence_groups().values():  # noqa: SLF001
        assert len({path in acquisition.field_policies for path in paths}) == 1, paths

    demand = explicit = 0.0
    class_cadence: dict[AcquisitionClass, float] = {}
    for path, cadence in _base_cadences(scheduler).items():
        policy = acquisition.policy_for(path)
        if path in acquisition.field_policies:
            assert cadence == policy.cadence_seconds, path
        if not tx and policy.tx_only:
            continue
        demand += 1.0 / cadence
        if path in acquisition.field_policies:
            explicit += 1.0 / cadence
        else:
            klass = acquisition_class_for_path(path)
            assert class_cadence.setdefault(klass, cadence) == cadence, (path, klass)

    stretched: list[AcquisitionClass] = []
    class_start: dict[AcquisitionClass, float] = {}
    for klass, cadence in class_cadence.items():
        entry = ACQUISITION_CLASS_TABLE[klass]
        start = class_start[klass] = (
            entry.ceiling_cadence_seconds
            if tx and entry.held_at_ceiling_in_tx
            else entry.nominal_cadence_seconds
        )
        assert start - _EPSILON <= cadence <= entry.ceiling_cadence_seconds + _EPSILON
        if cadence > start + _EPSILON:
            stretched.append(klass)

    window = _WINDOW[tx]
    warnings = _warnings(caplog)
    if demand <= limit + _EPSILON:
        if stretched:
            assert demand == pytest.approx(limit)
            top = min(stretched, key=_RANK.__getitem__)
            for klass, cadence in class_cadence.items():
                if _RANK[klass] > _RANK[top]:
                    assert cadence == pytest.approx(
                        ACQUISITION_CLASS_TABLE[klass].ceiling_cadence_seconds
                    ), klass
        assert not any(f"{window} " in message for message in warnings), warnings
    else:
        unstretched = explicit >= budget_hz - _EPSILON
        for klass, cadence in class_cadence.items():
            ceiling = ACQUISITION_CLASS_TABLE[klass].ceiling_cadence_seconds
            expected = class_start[klass] if unstretched else ceiling
            assert cadence == pytest.approx(expected), klass
        outcome = _UNSTRETCHED_OUTCOME if unstretched else _CEILING_OUTCOME
        part = f"{window} {demand:.2f} q/s ({explicit:.2f} q/s {outcome})"
        assert any(
            part in message and f"{limit:.2f} q/s limit" in message
            for message in warnings
        ), (part, warnings)


_UNSTRETCHED_OUTCOME = (
    "at explicit profile cadences, alone at or over the transport budget: "
    "class-derived poll groups not stretched"
)
_CEILING_OUTCOME = (
    "at explicit profile cadences, class-derived poll groups at their ceiling cadences"
)


def _explicit_hz(acquisition: RadioAcquisitionProfile, *, tx: bool) -> float:
    explicit = 0.0
    for path in acquisition.pollable_paths():
        policy = acquisition.field_policies.get(path)
        if policy is None or policy.cadence_seconds is None:
            continue
        if tx or not policy.tx_only:
            explicit += 1.0 / policy.cadence_seconds
    return explicit


def _window_cadences(
    acquisition: RadioAcquisitionProfile, budget_hz: float, *, tx: bool
) -> dict[FieldPath, float]:
    """Cadence of every path polled in the window, ``tx_only`` paths in TX only."""

    scheduler = AcquisitionScheduler(profile=acquisition, transport_budget_hz=budget_hz)
    scheduler.note_tx_active(tx)
    return {
        path: cadence
        for path, cadence in _base_cadences(scheduler).items()
        if tx or not acquisition.policy_for(path).tx_only
    }


def _class_derived_starts(
    acquisition: RadioAcquisitionProfile, cadences: dict[FieldPath, float], *, tx: bool
) -> dict[FieldPath, tuple[float, float, float]]:
    """``path -> (cadence, start cadence, ceiling)`` for class-derived paths."""

    result = {}
    for path, cadence in cadences.items():
        if path in acquisition.field_policies:
            continue
        entry = ACQUISITION_CLASS_TABLE[acquisition_class_for_path(path)]
        start = (
            entry.ceiling_cadence_seconds
            if tx and entry.held_at_ceiling_in_tx
            else entry.nominal_cadence_seconds
        )
        result[path] = (cadence, start, entry.ceiling_cadence_seconds)
    return result


@pytest.mark.parametrize(
    ("model", "budget_hz"),
    [("IC-7610", _LAN_BUDGET_HZ), ("IC-7300", _SERIAL_BUDGET_HZ)],
    ids=["ic7610-lan", "ic7300-serial"],
)
def test_receive_demand_fits_the_margin(
    model: str,
    budget_hz: float,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """MOR-2590/MOR-2593: the receive window fits 0.75 x budget.

    The IC-7610 over LAN and the IC-7300 over USB serial: the remaining
    explicit receive cadences are under the transport budget, every polled
    path together is within the limit, and no receive warning is logged.
    """

    acquisition = _CIV_ACQUISITION[model]
    assert _explicit_hz(acquisition, tx=False) < budget_hz

    with caplog.at_level(logging.WARNING, logger="rigplane.core.acquisition_scheduler"):
        cadences = _window_cadences(acquisition, budget_hz, tx=False)

    demand = sum(1.0 / cadence for cadence in cadences.values())
    assert demand <= ACQUISITION_BUDGET_MARGIN * budget_hz + _EPSILON
    assert not any("receive " in message for message in _warnings(caplog))


def test_ic7610_serial_receive_explicit_demand_under_the_budget_does_not_stretch() -> (
    None
):
    """IC-7610 over USB, receive: 40 q/s leaves the classes at their start.

    Explicit demand is under the 30 q/s limit, and so is the whole window
    at nominal cadences (25.9 q/s), so nothing stretches and the window
    stays under the 40 q/s transport budget.
    """

    acquisition = _CIV_ACQUISITION["IC-7610"]
    tx = False
    assert _explicit_hz(acquisition, tx=tx) < _SERIAL_BUDGET_HZ

    cadences = _window_cadences(acquisition, _SERIAL_BUDGET_HZ, tx=tx)

    class_derived = _class_derived_starts(acquisition, cadences, tx=tx)
    for path, (cadence, start, _ceiling) in class_derived.items():
        assert cadence == pytest.approx(start), path
    demand = sum(1.0 / cadence for cadence in cadences.values())
    assert demand == pytest.approx(25.9)
    assert demand < ACQUISITION_BUDGET_MARGIN * _SERIAL_BUDGET_HZ


def test_ic705_transmit_at_the_serial_budget_stretches_but_not_to_the_ceiling() -> None:
    """IC-705 transmit at 40 q/s: 20 q/s of explicit meters, classes stretch.

    The 30 q/s limit is above the explicit demand, so classes do stretch,
    and below the nominal window, so the fit closes on the limit before
    every class has reached its ceiling.
    """

    acquisition = _CIV_ACQUISITION["IC-705"]
    assert _explicit_hz(acquisition, tx=True) == pytest.approx(20.0)

    cadences = _window_cadences(acquisition, _SERIAL_BUDGET_HZ, tx=True)

    class_derived = _class_derived_starts(acquisition, cadences, tx=True)
    assert any(
        start < cadence < ceiling for cadence, start, ceiling in class_derived.values()
    )
    demand = sum(1.0 / cadence for cadence in cadences.values())
    assert demand == pytest.approx(ACQUISITION_BUDGET_MARGIN * _SERIAL_BUDGET_HZ)


def test_ic705_transmit_under_the_lan_budget_stays_at_its_start() -> None:
    """IC-705 transmit at 100 q/s: the 20 q/s of explicit meters fit, so nothing stretches."""

    acquisition = _CIV_ACQUISITION["IC-705"]
    cadences = _window_cadences(acquisition, _LAN_BUDGET_HZ, tx=True)

    class_derived = _class_derived_starts(acquisition, cadences, tx=True)
    for path, (cadence, start, _ceiling) in class_derived.items():
        assert cadence == pytest.approx(start), path


# --- (b) dispatch order -------------------------------------------------------


def test_dispatch_orders_by_priority_then_class_rank_then_deadline() -> None:
    ptt = FieldPath.global_("tx_state", "ptt")
    s_meter = FieldPath.parse("receiver.main.meters.s_meter")
    vd = FieldPath.global_("meters", "vd")
    mic_gain = FieldPath.global_("operator_controls", "mic_gain")
    span = FieldPath.parse("scope_controls.global.display.span")
    assert [
        acquisition_class_for_path(path) for path in (ptt, s_meter, vd, mic_gain, span)
    ] == [
        AcquisitionClass.KEYING,
        AcquisitionClass.METER,
        AcquisitionClass.SETTING,
        AcquisitionClass.SETTING,
        AcquisitionClass.MENU,
    ]
    clock = FreshnessClock(start=100.0)
    scheduler = AcquisitionScheduler(
        profile=_polled_profile(ptt, s_meter, vd, mic_gain, span),
        clock=clock,
    )

    # Deadlines alone would dispatch vd, mic_gain, s_meter, ptt.
    for path, max_age in ((vd, 1.0), (mic_gain, 3.0), (s_meter, 5.0), (ptt, 9.0)):
        scheduler.ensure_fresh(
            path,
            max_age=max_age,
            priority=AcquisitionPriority.BACKGROUND,
            reason="order",
        )
    scheduler.ensure_fresh(
        span,
        max_age=20.0,
        priority=AcquisitionPriority.USER,
        reason="order",
    )

    assert [request.paths for request in scheduler.dispatchable_requests()] == [
        (span,),
        (ptt,),
        (s_meter,),
        (vd,),
        (mic_gain,),
    ]


# --- (c) receive and transmit are fitted separately --------------------------

_LIVE = FieldPath.active("main", "freq_mode", "freq_hz")
_CONTROLS = (
    FieldPath.parse("receiver.main.operator_controls.af_level"),
    FieldPath.parse("receiver.main.operator_controls.rf_gain"),
    FieldPath.parse("receiver.main.operator_controls.squelch"),
    FieldPath.global_("operator_controls", "power_level"),
)
_TX_METERS = tuple(
    FieldPath.global_("meters", name) for name in ("power", "swr", "alc", "comp")
)
_SETTINGS = tuple(
    FieldPath.global_("operator_controls", name)
    for name in "mic_gain vox_gain anti_vox_gain monitor_gain key_speed cw_pitch "
    "break_in_delay vox_delay compressor_level drive_gain".split()
)
_WINDOWED_PATHS = (_LIVE, *_CONTROLS, *_TX_METERS, *_SETTINGS)


def _class_cadences(budget_hz: float, *, tx: bool) -> dict[AcquisitionClass, float]:
    scheduler = AcquisitionScheduler(
        profile=_polled_profile(*_WINDOWED_PATHS),
        transport_budget_hz=budget_hz,
    )
    scheduler.note_tx_active(tx)
    by_class: dict[AcquisitionClass, float] = {}
    for path, cadence in _base_cadences(scheduler).items():
        klass = acquisition_class_for_path(path)
        assert by_class.setdefault(klass, cadence) == cadence, (path, klass)
    return by_class


def test_receive_and_transmit_are_fitted_separately_as_the_class_table_says() -> None:
    """Transmit counts the TX meters and holds settings at 30 s; receive does neither.

    Receive demand at nominal cadences is 1 + 2 + 1 = 4 q/s, under the
    7.5 q/s limit of a 10 q/s budget, so nothing stretches. Transmit adds
    the four TX meters (16 q/s) and starts settings at their 30 s ceiling:
    control stretches to its 5 s ceiling, live has no headroom, and the TX
    meters close the rest. With a 100 q/s budget neither window stretches,
    and the settings still differ: 10 s in receive, 30 s in transmit.
    """

    for paths, klass in (
        (_SETTINGS, AcquisitionClass.SETTING),
        (_CONTROLS, AcquisitionClass.CONTROL),
        (_TX_METERS, AcquisitionClass.TX_METER),
        ((_LIVE,), AcquisitionClass.LIVE),
    ):
        assert {acquisition_class_for_path(path) for path in paths} == {klass}

    receive = _class_cadences(10.0, tx=False)
    assert receive[AcquisitionClass.SETTING] == pytest.approx(10.0)
    assert receive[AcquisitionClass.CONTROL] == pytest.approx(2.0)
    assert receive[AcquisitionClass.LIVE] == pytest.approx(1.0)

    transmit = _class_cadences(10.0, tx=True)
    assert transmit[AcquisitionClass.SETTING] == pytest.approx(30.0)
    assert transmit[AcquisitionClass.CONTROL] == pytest.approx(5.0)
    assert transmit[AcquisitionClass.LIVE] == pytest.approx(1.0)
    tx_meter_hz = 0.75 * 10.0 - 1 / 1.0 - 4 / 5.0 - 10 / 30.0
    assert transmit[AcquisitionClass.TX_METER] == pytest.approx(4 / tx_meter_hz)

    assert _class_cadences(100.0, tx=False)[AcquisitionClass.SETTING] == (
        pytest.approx(10.0)
    )
    slack_transmit = _class_cadences(100.0, tx=True)
    assert slack_transmit[AcquisitionClass.SETTING] == pytest.approx(30.0)
    assert slack_transmit[AcquisitionClass.TX_METER] == pytest.approx(0.25)


def test_a_completed_poll_waits_the_fitted_cadence_of_the_window_it_lands_in() -> None:
    clock = FreshnessClock(start=0.0)
    scheduler = AcquisitionScheduler(
        profile=_polled_profile(*_WINDOWED_PATHS),
        clock=clock,
        transport_budget_hz=10.0,
    )
    setting = _SETTINGS[0]

    queued = scheduler.due_requests(now=0.0, tx_active=False)
    request = next(request for request in queued if setting in request.paths)
    scheduler.record_acquisition_result(request, _completed(0.0))
    assert _next_due(scheduler, setting) == pytest.approx(10.0)

    queued = scheduler.due_requests(now=10.0, tx_active=True)
    request = next(request for request in queued if setting in request.paths)
    scheduler.record_acquisition_result(request, _completed(10.0))
    assert _next_due(scheduler, setting) == pytest.approx(40.0)


def test_a_group_that_never_completed_retries_at_its_fitted_cadence() -> None:
    """Transmit at 10 q/s fits control at 5 s (nominal 2 s), before any answer."""

    clock = FreshnessClock(start=0.0)
    scheduler = AcquisitionScheduler(
        profile=_polled_profile(*_WINDOWED_PATHS),
        clock=clock,
        transport_budget_hz=10.0,
    )
    control = _CONTROLS[0]

    queued = scheduler.due_requests(now=0.0, tx_active=True)
    entry = scheduler.diagnostics()["cadenceByPath"][str(control)]
    assert entry["currentCadenceSeconds"] == pytest.approx(5.0)
    request = next(request for request in queued if control in request.paths)
    scheduler.record_acquisition_failure(
        request, reason="acquisition_request_failed", now=0.0
    )
    assert _next_due(scheduler, control) == pytest.approx(5.0)


# --- (d) the budget is max(gap, round-trip estimate) --------------------------


@pytest.fixture
def default_civ_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ICOM_CIV_MIN_INTERVAL_MS", raising=False)
    monkeypatch.delenv("ICOM_SERIAL_CIV_MIN_INTERVAL_MS", raising=False)


def _lan_radio() -> IcomRadio:
    return IcomRadio("192.0.2.1", model="IC-7610")


def _serial_radio() -> Ic7300SerialRadio:
    return Ic7300SerialRadio(device="/dev/ttyUSB0", civ_link=_FakeSerialCivLink())


def test_the_budget_is_one_over_max_gap_and_round_trip(
    default_civ_gaps: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert civ_transport_budget_hz(_lan_radio()) == pytest.approx(_LAN_BUDGET_HZ)
    assert civ_transport_budget_hz(_serial_radio()) == pytest.approx(_SERIAL_BUDGET_HZ)
    monkeypatch.setenv("ICOM_CIV_MIN_INTERVAL_MS", "40")
    assert civ_transport_budget_hz(_lan_radio()) == pytest.approx(25.0)
    monkeypatch.setenv("ICOM_CIV_MIN_INTERVAL_MS", "5")
    # Gap 5 ms, round-trip estimate 9 ms: the budget follows the estimate.
    assert civ_transport_budget_hz(_lan_radio()) == pytest.approx(1.0 / 0.009)
    assert civ_transport_budget_hz(object()) is None


def _record_budgets(monkeypatch: pytest.MonkeyPatch, module: str) -> list[float | None]:
    budgets: list[float | None] = []

    class _Recording(AcquisitionScheduler):
        def __init__(self, **kwargs: Any) -> None:
            budgets.append(kwargs.get("transport_budget_hz"))
            super().__init__(**kwargs)

    monkeypatch.setattr(f"{module}.AcquisitionScheduler", _Recording)
    return budgets


_RADIOS = pytest.mark.parametrize(
    ("make_radio", "budget_hz"),
    [(_lan_radio, _LAN_BUDGET_HZ), (_serial_radio, _SERIAL_BUDGET_HZ)],
    ids=["lan", "serial"],
)


@_RADIOS
async def test_the_web_seat_builds_its_scheduler_on_the_radio_budget(
    make_radio: Any,
    budget_hz: float,
    default_civ_gaps: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets = _record_budgets(monkeypatch, "rigplane.web.server")

    WebServer(make_radio())

    assert budgets == [pytest.approx(budget_hz)]


@_RADIOS
async def test_the_rigctld_seat_builds_its_scheduler_on_the_radio_budget(
    make_radio: Any,
    budget_hz: float,
    default_civ_gaps: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    budgets = _record_budgets(monkeypatch, "rigplane.rigctld.server")

    RigctldServer(
        make_radio(), RigctldConfig(host="127.0.0.1", port=0)
    )._bootstrap_state_acquisition()

    assert budgets == [pytest.approx(budget_hz)]


# --- MOR-2599: the non-selected receiver's demotion leaves the fit alone -----


def test_the_fit_counts_the_undemoted_demand_as_the_upper_bound() -> None:
    """MOR-2599 budget rule: demotion never re-fits.

    The scheduler's fitted receive cadences for the IC-7610 over LAN are
    exactly what fit_to_budget settles on for the UNDEMOTED demand, whether
    or not the caller reports a selected receiver (MAIN or SUB). Demotion
    only lowers the demand the scheduler actually pays, so the fit is an
    upper bound and stays safe without re-fitting on every switch.
    """

    acquisition = _CIV_ACQUISITION["IC-7610"]
    counts: Counter[AcquisitionClass] = Counter()
    for path in acquisition.pollable_paths():
        if path not in acquisition.field_policies:
            counts[acquisition_class_for_path(path)] += 1
    expected = fit_to_budget(
        counts,
        _LAN_BUDGET_HZ,
        ACQUISITION_BUDGET_MARGIN,
        False,
        reserved_hz=_explicit_hz(acquisition, tx=False),
    )
    cadences = _window_cadences(acquisition, _LAN_BUDGET_HZ, tx=False)
    for path, cadence in cadences.items():
        if path in acquisition.field_policies:
            continue
        klass = acquisition_class_for_path(path)
        assert cadence == expected.effective_cadence_seconds[klass], path
