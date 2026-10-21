"""Unit tests for the pure simulator state engine (MOR-3599).

Red on main: ``rigplane_testkit.sim.engine`` does not exist there. All
tests drive ``SimEngine`` directly with a ``ManualClock`` — no sockets.
"""

from __future__ import annotations

import copy
import json
import random
from dataclasses import asdict

import pytest

from rigplane_testkit.sim.engine import (
    Capabilities,
    ManualClock,
    Marker,
    Ok,
    OutOfRange,
    ReceiverState,
    Refused,
    ScheduledOp,
    SelectReceiver,
    SetFrequency,
    SetLevel,
    SetMeter,
    SetMode,
    SetPtt,
    SetSelector,
    SetSplit,
    SetSwitch,
    SimEngine,
    SimState,
    Unsupported,
)

CAPS = Capabilities(
    receiver_count=2,
    levels={"af": (0, 255), "rf": (0, 255)},
    switches=frozenset({"nb", "preamp"}),
    selectors={"agc": (1, 2, 3), "att": (0, 2, 10, 20)},
)


def _engine(
    seed: int = 7,
    caps: Capabilities = CAPS,
    initial: SimState | None = None,
) -> tuple[SimEngine, ManualClock]:
    clock = ManualClock()
    return SimEngine(caps, clock=clock, seed=seed, initial=initial), clock


class _LyingInt(int):
    """Every comparison lies True; the old isinstance checks accepted -1."""

    def __ge__(self, other: object) -> bool:
        return True

    def __le__(self, other: object) -> bool:
        return True

    def __eq__(self, other: object) -> bool:
        return True


_LYING = _LyingInt(-1)


class _ScriptedClock:
    """Yields scripted readings; read ``fire_on`` runs ``callback`` first."""

    def __init__(self, *readings: float, fire_on: int = 0) -> None:
        self._readings = iter(readings)
        self.callback = lambda: None
        self.fire_on = fire_on
        self._reads = 0

    def __call__(self) -> float:
        self._reads += 1
        if self._reads == self.fire_on:
            self.callback()
        return next(self._readings)


def test_apply_ok_mutates_state_and_records_op() -> None:
    engine, _ = _engine()
    assert engine.apply(SetFrequency(1, 7_100_000)) == Ok()
    assert engine.state.receivers[1].frequency_hz == 7_100_000
    record = engine.trace[0]
    assert (record.n, record.t, record.kind, record.name) == (
        0,
        0.0,
        "op",
        "SetFrequency",
    )
    assert record.args == {"receiver": 1, "hz": 7_100_000}
    assert record.result["outcome"] == "ok"


@pytest.mark.parametrize(
    ("op", "outcome_type", "tag"),
    [
        (SetFrequency(2, 14_074_000), OutOfRange, "out_of_range"),  # unknown receiver
        (SetFrequency(0, 29_999), OutOfRange, "out_of_range"),  # below band edge
        (SetFrequency(0, "14m"), OutOfRange, "out_of_range"),  # str where int required
        (SetFrequency(0, True), OutOfRange, "out_of_range"),  # bool where int required
        (SetMode(0, "WSPR"), Unsupported, "unsupported"),  # mode not in caps
        (SetLevel(0, "vol", 5), Unsupported, "unsupported"),  # unknown level
        (SetLevel(0, [], 1), OutOfRange, "out_of_range"),  # unhashable level name
        (SetMeter([], 1), OutOfRange, "out_of_range"),  # non-str meter name
        (SetLevel(0, "af", 256), OutOfRange, "out_of_range"),  # above level max
        (SetSwitch(0, "turbo", True), Unsupported, "unsupported"),  # unknown switch
        (SetSwitch(0, "nb", 1), OutOfRange, "out_of_range"),  # int where bool required
        (SetSelector(0, "vox", 1), Unsupported, "unsupported"),  # unknown selector
        (SetSelector(0, [], 1), OutOfRange, "out_of_range"),  # unhashable name
        (SetSelector(2, "agc", 1), OutOfRange, "out_of_range"),  # unknown receiver
        (SetSelector(0, "agc", 0), OutOfRange, "out_of_range"),  # step not allowed
        (SetSelector(0, "agc", True), OutOfRange, "out_of_range"),  # bool not int
        (SetSelector(0, "agc", "2"), OutOfRange, "out_of_range"),  # str not int
        (SetSelector(0, "agc", 2.0), OutOfRange, "out_of_range"),  # float not int
        (SetSelector(0, "agc", None), OutOfRange, "out_of_range"),  # None not int
        (SetPtt("yes"), OutOfRange, "out_of_range"),  # str where bool required
        ("not-an-op", Unsupported, "unsupported"),  # non-dataclass op still records
        (SetFrequency, Unsupported, "unsupported"),  # op class, not an instance
        (SelectReceiver(-1), OutOfRange, "out_of_range"),
        (SelectReceiver(None), OutOfRange, "out_of_range"),  # None is not an index
        (SetFrequency(0, _LYING), OutOfRange, "out_of_range"),  # lying subclass
        (SetLevel(0, "af", _LYING), OutOfRange, "out_of_range"),
        (SetSelector(0, "agc", _LYING), OutOfRange, "out_of_range"),
        (SetMode(0, "USB", filter=_LYING), OutOfRange, "out_of_range"),
        (SelectReceiver(_LYING), OutOfRange, "out_of_range"),
        (SetMeter("smeter", _LYING), OutOfRange, "out_of_range"),
    ],
)
def test_rejected_op_outcome_and_trace_tag(
    op: object, outcome_type: type, tag: str
) -> None:
    engine, _ = _engine()
    outcome = engine.apply(op)
    assert isinstance(outcome, outcome_type)
    assert not isinstance(outcome, Ok)
    assert engine.trace[0].result["outcome"] == tag


def test_caps_gated_ptt_split_and_power_off_refusal() -> None:
    plain, _ = _engine(caps=Capabilities(has_ptt=False, has_split=False))
    assert isinstance(plain.apply(SetPtt(True)), Unsupported)
    assert isinstance(plain.apply(SetSplit(True)), Unsupported)
    dark, _ = _engine(
        caps=Capabilities(),
        initial=SimState(receivers=[ReceiverState()], power_on=False),
    )
    assert dark.apply(SetPtt(True)) == Refused(reason="power off")
    assert dark.state.ptt is False
    assert dark.trace[0].result["outcome"] == "refused"


def test_default_state_derives_from_caps() -> None:
    caps = Capabilities(modes=("FM",), frequency_range_hz=(118_000_000, 137_000_000))
    engine, _ = _engine(caps=caps)
    assert engine.read("mode") == Ok("FM")
    assert engine.read("frequency_hz") == Ok(118_000_000)  # clamped to band edge


def test_set_selector_sets_state_and_records_op() -> None:
    engine, _ = _engine()
    assert engine.apply(SetSelector(1, "agc", 3)) == Ok()
    assert engine.state.receivers[1].selectors["agc"] == 3
    assert engine.read("selector:agc", receiver=1) == Ok(3)
    record = engine.trace[0]
    assert (record.n, record.t, record.kind, record.name) == (
        0,
        0.0,
        "op",
        "SetSelector",
    )
    assert record.args == {"receiver": 1, "name": "agc", "value": 3}
    assert record.result["outcome"] == "ok"


def test_selector_initial_value_is_first_allowed_value() -> None:
    engine, _ = _engine()
    assert engine.read("selector:agc") == Ok(1)
    assert engine.state.receivers[1].selectors == {"agc": 1, "att": 0}
    custom, _ = _engine(
        initial=SimState(
            receivers=[ReceiverState(selectors={"agc": 3}), ReceiverState()]
        )
    )
    assert custom.read("selector:agc", receiver=0) == Ok(3)  # caller value kept
    assert custom.read("selector:agc", receiver=1) == Ok(1)  # missing ones seeded
    assert custom.state.receivers[0].selectors == {"agc": 3, "att": 0}


def test_failed_apply_never_mutates_state() -> None:
    engine, _ = _engine()
    engine.apply(SetFrequency(0, 14_100_000))
    before = copy.deepcopy(engine.state)
    for op in (
        SetFrequency(0, 1),
        SetMode(0, "WSPR"),
        SetLevel(0, "vol", 3),
        SetSwitch(0, "nope", True),
        SetSelector(0, "vox", 1),
        SetSelector(0, "agc", 9),
        SelectReceiver(9),
        SelectReceiver(None),  # rejected, so the selection cannot change
        SetMeter("smeter", "loud"),
    ):
        assert not isinstance(engine.apply(op), Ok)
    assert copy.deepcopy(engine.state) == before


def test_trace_numbers_are_gapless_across_outcomes() -> None:
    engine, _ = _engine()
    engine.apply(SetFrequency(0, 14_100_000))
    engine.apply(SetMode(0, "WSPR"))
    engine.apply(SetFrequency(0, 1))
    assert [r.n for r in engine.trace] == [0, 1, 2]
    assert [r.result["outcome"] for r in engine.trace] == [
        "ok",
        "unsupported",
        "out_of_range",
    ]


def test_two_engines_same_inputs_produce_identical_traces() -> None:
    def build_and_run() -> SimEngine:
        clock = ManualClock()
        engine = SimEngine(CAPS, clock=clock, seed=42)
        engine.apply(SetFrequency(0, 14_074_001))
        engine.schedule(1.0, Marker("mid"))
        engine.schedule(1.0, ScheduledOp(SetMode(0, "CW", filter=2)))
        engine.schedule(2.0, ScheduledOp(SetLevel(0, "af", 12)))
        engine.schedule(2.0, ScheduledOp(SetSelector(0, "agc", 2)))
        clock.advance(1.0)
        engine.advance_to(1.0)
        clock.advance(1.0)
        engine.advance_to(2.0)
        return engine

    engine_a = build_and_run()
    engine_b = build_and_run()
    assert engine_a.trace == engine_b.trace
    assert [r.t for r in engine_a.trace] == [0.0, 1.0, 1.0, 2.0, 2.0]
    assert engine_a.state == engine_b.state


def test_advance_to_orders_ties_by_insertion() -> None:
    engine, clock = _engine()
    engine.schedule(5.0, Marker("first"))
    engine.schedule(5.0, ScheduledOp(SetFrequency(0, 7_000_000)))
    engine.schedule(5.0, Marker("last"))
    engine.schedule(4.0, Marker("earlier"))
    clock.advance(5.0)
    produced = engine.advance_to(5.0)
    assert [r.name for r in produced] == ["earlier", "first", "SetFrequency", "last"]
    assert [r.kind for r in produced] == ["marker", "marker", "op", "marker"]
    assert [r.n for r in produced] == [0, 1, 2, 3]
    assert engine.state.receivers[0].frequency_hz == 7_000_000


def test_advance_to_noop_backwards_and_past_schedule_raise() -> None:
    engine, _ = _engine()
    assert engine.advance_to(3.0) == []
    with pytest.raises(ValueError):
        engine.advance_to(2.0)
    with pytest.raises(ValueError):
        engine.schedule(1.0, Marker("past"))
    with pytest.raises(ValueError):
        engine.schedule(float("nan"), Marker("nan"))  # would poison the queue
    with pytest.raises(ValueError):
        engine.advance_to(float("inf"))
    assert engine.advance_to(3.0) == []
    engine.schedule(3.0, Marker(object()))  # non-JSON name stays serialisable
    records = engine.advance_to(3.0)
    assert records[0].name == "<object>"
    assert json.loads(json.dumps(asdict(records[0]))) == asdict(records[0])


def test_initial_state_must_match_caps_receiver_count() -> None:
    short = SimState(receivers=[ReceiverState()])  # caps say 2 receivers
    with pytest.raises(ValueError):
        SimEngine(CAPS, clock=ManualClock(), initial=short)


def test_read_covers_every_name_family() -> None:
    engine, _ = _engine()
    engine.apply(SetMode(1, "CW", filter=3))
    engine.apply(SetMode(1, "FM"))  # filter omitted -> kept
    engine.apply(SetFrequency(1, 7_030_000))
    engine.apply(SetLevel(1, "af", 42))
    engine.apply(SetSwitch(1, "nb", True))
    engine.apply(SetSelector(1, "agc", 2))
    engine.apply(SelectReceiver(1))
    engine.apply(SetSplit(True))
    engine.apply(SetMeter("smeter", 59))
    engine.state.extra["firmware"] = "1.2"
    assert engine.read("frequency_hz") == Ok(7_030_000)
    assert engine.read("mode") == Ok("FM")
    assert engine.read("filter") == Ok(3)
    assert engine.read("data_mode") == Ok(False)
    assert engine.read("level:af") == Ok(42)
    assert engine.read("switch:nb") == Ok(True)
    assert engine.read("selector:agc") == Ok(2)  # selected receiver is 1
    assert engine.read("selector:agc", receiver=0) == Ok(1)  # seeded first allowed
    assert engine.read("ptt") == Ok(False)
    assert engine.read("split") == Ok(True)
    assert engine.read("selected_receiver") == Ok(1)
    assert engine.read("power_on") == Ok(True)
    assert engine.read("meter:smeter") == Ok(59)
    assert engine.read("extra:firmware") == Ok("1.2")
    assert engine.read("frequency_hz", receiver=0) == Ok(14_074_000)
    assert engine.read("switch:nb", receiver=0) == Ok(None)  # unset reads as None
    assert isinstance(engine.read("warp"), Unsupported)
    assert isinstance(engine.read("level:vol"), Unsupported)
    assert isinstance(engine.read("selector:vox"), Unsupported)
    assert isinstance(engine.read("frequency_hz", receiver=9), OutOfRange)
    assert isinstance(engine.read("selector:agc", receiver=9), OutOfRange)


def test_rng_same_stream_identity_and_cross_stream_independence() -> None:
    engine, _ = _engine(seed=5)
    jitter = engine.rng("jitter")
    assert engine.rng("jitter") is jitter
    twin, _ = _engine(seed=5)
    stream_a = [jitter.random() for _ in range(5)]
    stream_b = [engine.rng("drop").random() for _ in range(5)]
    twin_a = [twin.rng("jitter").random() for _ in range(5)]
    assert stream_a != stream_b
    assert stream_a == twin_a
    assert stream_a[0] == random.Random("5:jitter").random()  # spec derivation


def test_star_export_does_not_leak_and_module_state_is_immutable() -> None:
    import rigplane_testkit.sim.engine as engine_pkg
    from rigplane_testkit.sim.engine import core

    exported = set(vars(engine_pkg))
    assert not exported & {
        "heapq",
        "random",
        "dataclass",
        "count",
        "isfinite",
        "Protocol",
    }
    assert {"SimEngine", "Ok", "Marker", "SetSelector"} <= exported
    mutable = {
        name
        for name, value in vars(core).items()
        if not name.startswith("__") and type(value) in (list, dict, set)
    }
    assert mutable == set()  # _READ_PLURALS proxies, __all__ is a tuple


def test_trace_records_are_json_serialisable() -> None:
    engine, _ = _engine()
    engine.apply(SetFrequency(0, 14_074_000))
    engine.apply(SetMode(0, "FM", filter=2))
    engine.apply(SetFrequency(0, 1))  # failed record carries the rejected value
    engine.apply(SetSelector(0, "agc", 3))  # ok op with a JSON value
    engine.apply(SetSelector(0, "agc", object()))  # rejected with a non-JSON value
    engine.apply(SetFrequency(0, object()))  # rejected with a non-JSON value
    engine.apply(SetFrequency(0, 10**5000))  # huge int breaks str()/json limits
    engine.apply(SetLevel(0, "af", float("nan")))  # NaN breaks allow_nan=False
    engine.apply(SetLevel(0, "af", float("inf")))
    payload = [asdict(r) for r in engine.trace]
    json.dumps(payload, allow_nan=False)  # strictly JSON-safe, no NaN literals
    assert json.loads(json.dumps(payload)) == payload
    assert payload[3]["args"]["value"] == 3
    assert payload[4]["args"]["value"] == "<object>"
    assert payload[5]["args"]["hz"] == "<object>"
    assert payload[6]["args"]["hz"] == "<int:5001 digits>"
    assert payload[7]["args"]["value"] == "NaN"
    assert payload[8]["args"]["value"] == "Infinity"


def test_engine_deep_copies_the_caller_initial_state() -> None:
    initial = SimState(receivers=[ReceiverState(), ReceiverState()])
    engine_a, _ = _engine(initial=initial)
    engine_b, _ = _engine(initial=initial)
    engine_a.apply(SetFrequency(0, 7_100_000))
    assert initial.receivers[0].frequency_hz == 14_074_000  # caller untouched
    assert initial.receivers[0].selectors == {}  # selector seeding did not leak
    assert engine_b.state.receivers[0].frequency_hz == 14_074_000  # independent
    assert engine_b.state.receivers[1].selectors == {"agc": 1, "att": 0}


def test_read_never_raises_on_odd_names() -> None:
    engine, _ = _engine()

    class NoRepr:
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    class BadStr(str):  # str subclass keeps a failing __repr__
        def __repr__(self) -> str:
            raise RuntimeError("no repr")

    assert isinstance(engine.read(10**5000), Unsupported)  # repr() would raise
    assert isinstance(engine.read(NoRepr()), Unsupported)
    assert isinstance(engine.read(BadStr("warp")), Unsupported)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_manual_clock_rejects_nonfinite_start(bad: float) -> None:
    with pytest.raises(ValueError):
        ManualClock(start=bad)
    with pytest.raises(ValueError):
        ManualClock().advance(bad)
    with pytest.raises(ValueError):
        ManualClock().advance(-1.0)


@pytest.mark.parametrize("bad", [-1.0, float("nan")])
def test_engine_rejects_bad_clock_readings_before_any_change(bad: float) -> None:
    engine = SimEngine(CAPS, clock=_ScriptedClock(0.0, bad))
    with pytest.raises(ValueError):
        engine.apply(SetFrequency(0, 7_100_000))
    assert engine.state.receivers[0].frequency_hz == 14_074_000  # unmutated
    assert engine.trace == ()
    with pytest.raises(ValueError):
        SimEngine(CAPS, clock=_ScriptedClock(0.0, bad)).schedule(1.0, Marker("m"))
    engine = SimEngine(CAPS, clock=_ScriptedClock(0.0, 0.0, bad, 0.0, 0.0))
    engine.schedule(1.0, Marker("later"))
    with pytest.raises(ValueError):
        engine.advance_to(5.0)
    assert [r.name for r in engine.advance_to(5.0)] == ["later"]  # not consumed


def test_advance_to_is_not_reentrant() -> None:
    clock = _ScriptedClock(0.0, 0.0, 0.0)
    engine = SimEngine(CAPS, clock=clock)
    clock.callback = lambda: engine.advance_to(1.0)
    clock.fire_on = 2  # fires inside advance_to's clock validation
    with pytest.raises(RuntimeError, match="not re-entrant"):
        engine.advance_to(1.0)
    assert engine.advance_to(1.0) == []  # the flag was released


def test_schedule_during_event_cannot_target_the_past() -> None:
    clock = _ScriptedClock(0.0, 0.0, 5.0, 5.0, 5.0)
    engine = SimEngine(CAPS, clock=clock)
    engine.schedule(5.0, Marker("at-five"))

    def schedule_early() -> None:
        with pytest.raises(ValueError):
            engine.schedule(2.0, Marker("too-early"))

    clock.callback = schedule_early
    clock.fire_on = 4  # the marker's record read, with engine time at 5.0
    produced = engine.advance_to(5.0)
    assert [r.name for r in produced] == ["at-five"]
    assert [r.t for r in produced] == [5.0]


def test_record_numbers_stay_unique_when_clock_runs_mid_record() -> None:
    clock = _ScriptedClock(0.0, 0.0, 0.0)
    engine = SimEngine(CAPS, clock=clock)
    clock.callback = lambda: engine.apply(SetMeter("tick", 1))
    clock.fire_on = 2  # fires while apply() reads the clock, before it records
    engine.apply(SetFrequency(0, 7_100_000))
    assert [r.n for r in engine.trace] == [0, 1]
    assert [r.name for r in engine.trace] == ["SetMeter", "SetFrequency"]
