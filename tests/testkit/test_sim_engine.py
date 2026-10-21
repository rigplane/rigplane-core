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


def test_star_export_does_not_leak_core_module_imports() -> None:
    import rigplane_testkit.sim.engine as engine_pkg

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


def test_trace_records_are_json_serialisable() -> None:
    engine, _ = _engine()
    engine.apply(SetFrequency(0, 14_074_000))
    engine.apply(SetMode(0, "FM", filter=2))
    engine.apply(SetFrequency(0, 1))  # failed record carries the rejected value
    engine.apply(SetSelector(0, "agc", 3))  # ok op with a JSON value
    engine.apply(SetSelector(0, "agc", object()))  # rejected with a non-JSON value
    engine.apply(SetFrequency(0, object()))  # rejected with a non-JSON value
    payload = [asdict(r) for r in engine.trace]
    assert json.loads(json.dumps(payload)) == payload
    assert payload[3]["args"]["value"] == 3
    assert payload[4]["args"]["value"] == "<object>"
    assert payload[-1]["args"]["hz"] == "<object>"
