"""Unit tests for the Yaesu CAT protocol model (MOR-3602).

Pure: no sockets, a ManualClock, the FTX-1 capability profile and sim
profile loaded through the real loaders from explicit repo-root paths.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Callable
from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.sim.engine import (
    ManualClock,
    Ok,
    Refused,
    ScheduledOp,
    SetFrequency,
    SetMeter,
    Unsupported,
)
from rigplane_testkit.sim.model.yaesu_cat import (
    YaesuCatModel,
    build_ftx1_engine,
    seed_from_recording,
)
from rigplane_testkit.sim.profiles import load_sim_profile
from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

ROOT = Path(__file__).resolve().parents[2]
PROFILE_PATH = ROOT / "rigplane_testkit" / "sim" / "profiles" / "ftx1.toml"
MODEL_PATH = ROOT / "rigplane_testkit" / "sim" / "model" / "yaesu_cat.py"


def _model(
    recorded: RecordedCatAnswerTable | None = None,
    on_not_modelled: Callable[[str], None] | None = None,
) -> YaesuCatModel:
    rig = load_rig(ROOT / "rigs" / "ftx1.toml")
    profile = load_sim_profile(PROFILE_PATH, root=ROOT)
    engine = build_ftx1_engine(profile, rig, clock=ManualClock(), seed=7)
    return YaesuCatModel(
        engine,
        identity=rig.expected_identity_ids[0],
        recorded=recorded,
        on_not_modelled=on_not_modelled,
    )


def test_identity_read() -> None:
    assert _model().handle("ID;") == "ID0840;"


def test_initial_state_comes_from_the_sim_profile() -> None:
    model = _model()
    assert model.handle("FA;") == "FA147225000;"
    assert model.handle("MD0;") == "MD04;"  # FM
    assert model.handle("PS;") == "PS1;"
    assert model.handle("IF;") == "IF00000147225000+000000402003;"


@pytest.mark.parametrize(
    ("set_line", "read_line", "reply"),
    [
        ("FA014074000;", "FA;", "FA014074000;"),
        ("FB007074000;", "FB;", "FB007074000;"),
        ("MD02;", "MD0;", "MD02;"),
        ("MD1B;", "MD1;", "MD1B;"),
        ("TX1;", "TX;", "TX1;"),
        ("AI1;", "AI;", "AI1;"),
        ("ST1;", "ST;", "ST1;"),
        ("VS1;", "VS;", "VS1;"),
        ("AG0100;", "AG0;", "AG0100;"),
        ("AG1080;", "AG1;", "AG1080;"),
        ("RG0100;", "RG0;", "RG0100;"),
        ("RG1255;", "RG1;", "RG1255;"),
        ("SQ0100;", "SQ0;", "SQ0100;"),
        ("SQ1080;", "SQ1;", "SQ1080;"),
        ("NL0005;", "NL0;", "NL0005;"),
        ("NL1010;", "NL1;", "NL1010;"),
        ("GT01;", "GT0;", "GT01;"),
        ("GT14;", "GT1;", "GT14;"),
        ("PA01;", "PA0;", "PA01;"),
    ],
)
def test_set_then_read_round_trip(set_line: str, read_line: str, reply: str) -> None:
    model = _model()
    assert model.handle(set_line) is None
    assert model.handle(read_line) == reply


def test_boundary_values() -> None:
    model = _model()
    assert model.handle("AG0255;") is None
    assert model.handle("AG0;") == "AG0255;"
    for line in (
        "AG0256;",  # above the 0-255 level range
        "NL0011;",  # above the 000-010 NB range
        "GT05;",  # 5/6 are AUTO-speed readbacks, not settable
        "PA03;",  # preamp is 0-2
        "MD0G;",  # not an FTX-1 mode code
        "FA01407400;",  # 8 digits
        "FA0140740000;",  # 10 digits
        "SM0000;",  # S-meter is read-only
        "PS0;",  # the engine has no power op
        "IF00025147225000+000000412003;",  # SET on IF is refused
    ):
        assert model.handle(line) == "?;", line


def test_fa_set_refused_in_memory_mode_accepted_in_vfo() -> None:
    model = _model()
    model.engine.state.extra["main_memory_mode"] = True
    assert model.handle("FA014074000;") == "?;"
    assert model.handle("FA;") == "FA147225000;"  # state unchanged
    # FB in MEMORY mode is not observed on the real radio: stays accepted.
    assert model.handle("FB007074000;") is None
    assert model.handle("FB;") == "FB007074000;"
    model.engine.state.extra["main_memory_mode"] = False
    assert model.handle("FA014074000;") is None
    assert model.handle("FA;") == "FA014074000;"


def test_out_of_range_frequency_is_refused() -> None:
    model = _model()
    assert model.handle("FA999999999;") == "?;"  # above the 470 MHz top
    assert model.handle("FA;") == "FA147225000;"


def test_engine_state_keys_are_the_profile_control_names() -> None:
    # rigs/ftx1.toml declares af_level/rf_gain/squelch/nb_level; the codec
    # keys engine state with exactly those names, no aliases (ADR section
    # 4, docs/internals/sim-architecture.md).
    model = _model()
    assert model.handle("AG0100;") is None
    assert model.engine.read("level:af_level", 0) == Ok(100)
    assert model.handle("RG1080;") is None
    assert model.engine.read("level:rf_gain", 1) == Ok(80)
    assert model.handle("SQ0030;") is None
    assert model.engine.read("level:squelch", 0) == Ok(30)
    assert model.handle("NL0005;") is None
    assert model.engine.read("level:nb_level", 0) == Ok(5)
    assert model.handle("GT01;") is None
    assert model.engine.read("selector:agc", 0) == Ok(1)
    assert model.handle("PA02;") is None
    assert model.engine.read("selector:preamp", 0) == Ok(2)


def test_fa_fb_accept_the_full_cat_domain() -> None:
    # The oracle (rows vfo_a_freq/vfo_b_freq) declares 000030000-470000000
    # Hz for FA/FB: the lower bound is the CAT domain, not the lowest
    # [freq_ranges] receive range (100 kHz).
    model = _model()
    assert model.handle("FA000030000;") is None
    assert model.handle("FA;") == "FA000030000;"
    assert model.handle("FB000030000;") is None
    assert model.handle("FB;") == "FB000030000;"
    assert model.handle("FA470000000;") is None
    assert model.handle("FA;") == "FA470000000;"
    assert model.handle("FA000029999;") == "?;"  # below the CAT domain
    assert model.handle("FB470000001;") == "?;"  # above it
    assert model.handle("FA;") == "FA470000000;"  # refused SETs keep state


def test_s_meters_are_per_receiver_and_read_zero_while_agc_is_off() -> None:
    model = _model()
    model.engine.apply(SetMeter("s_meter_main", 120))
    model.engine.apply(SetMeter("s_meter_sub", 30))
    # Fresh engines have AGC 0 (OFF) on both receivers: the reading is 0.
    assert model.handle("SM0;") == "SM0000;"
    assert model.handle("SM1;") == "SM0000;"
    assert model.handle("GT01;") is None
    assert model.handle("GT11;") is None
    # Both AGCs on: each receiver reads its own meter, and the SUB read
    # still echoes the side digit as 0 (recorded quirk).
    assert model.handle("SM0;") == "SM0120;"
    assert model.handle("SM1;") == "SM0030;"


def test_unserved_verb_falls_to_the_recorded_table() -> None:
    rows = [
        {"dir": "c2r", "line": "FR;"},
        {"dir": "r2c", "line": "FR01;"},
        {"dir": "c2r", "line": "RA0;"},
        {"dir": "r2c", "line": "?;"},
        {"dir": "c2r", "line": "ZZ;"},  # overtaken by the next request: silence
        {"dir": "c2r", "line": "PC;"},
        {"dir": "r2c", "line": "PC2015;"},
    ]
    model = _model(recorded=RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.handle("FR;") == "FR01;"  # recorded data
    assert model.handle("RA0;") == "?;"  # recorded refusal
    assert model.handle("ZZ;") is None  # recorded silence
    assert model.handle("QQ;") == "?;"  # never recorded


def test_native_handler_wins_over_the_recorded_table() -> None:
    rows = [
        {"dir": "c2r", "line": "FA;"},
        {"dir": "r2c", "line": "FA999999999;"},
    ]
    model = _model(recorded=RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.handle("FA;") == "FA147225000;"  # engine state, not the table


def test_native_refusal_falls_through_to_the_recorded_table() -> None:
    # ADR section 5: native data/ACK wins; a native NAK falls to the
    # recording. GT05;/PA03; are refused natively (AUTO-speed readback /
    # preamp 0-2), so the recorded answer decides.
    rows = [
        {"dir": "c2r", "line": "GT05;"},
        {"dir": "r2c", "line": "GT05;"},  # recorded data for a refused line
        {"dir": "c2r", "line": "PA03;"},  # overtaken below: recorded silence
        {"dir": "c2r", "line": "FA;"},
        {"dir": "r2c", "line": "FA147225000;"},
    ]
    model = _model(recorded=RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.handle("GT05;") == "GT05;"  # recorded data beats ?;
    assert model.handle("PA03;") is None  # recorded silence beats ?;
    # No recorded FA SET exists, so the MEMORY-mode refusal stands.
    model.engine.state.extra["main_memory_mode"] = True
    assert model.handle("FA014074000;") == "?;"
    # Every line above had a native handler: nothing was not_modelled.
    assert model.not_modelled == {}


@pytest.mark.parametrize(
    "line",
    [
        "",
        "?",
        "FA",  # no terminator
        "FA;FB;",  # two lines
        "FÁ;",  # non-ASCII
        "F" * 64 + ";",  # 65 bytes
    ],
)
def test_malformed_lines_answer_refusal_without_raising(line: str) -> None:
    assert _model().handle(line) == "?;"


def test_non_string_input_answers_refusal_without_raising() -> None:
    assert _model().handle(None) == "?;"  # type: ignore[arg-type]


def test_unmodelled_requests_are_counted_and_reported_to_the_callback() -> None:
    # ADR section 4/5: a well-formed request with no native handler and no
    # recording is Unsupported("not_modelled"): it answers ?;, is counted
    # per line, and the observer callback is called once per request with
    # the request line. The engine trace is not written by the model.
    seen: list[str] = []
    model = _model(on_not_modelled=seen.append)
    reply, outcome = model.handle_with_outcome("QQ;")
    assert reply == "?;"
    assert outcome == Unsupported("not_modelled")
    assert model.handle("ZZ;") == "?;"
    assert model.handle("QQ;") == "?;"
    assert seen == ["QQ;", "ZZ;", "QQ;"]
    assert model.not_modelled == {"QQ;": 2, "ZZ;": 1}
    assert not [r for r in model.engine.trace if r.kind == "marker"]


@pytest.mark.parametrize("error", [RuntimeError, TypeError, OSError])
def test_a_failing_callback_still_answers_and_the_model_keeps_working(
    error: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    def callback(line: str) -> None:
        raise error(f"boom on {line}")

    model = _model(on_not_modelled=callback)
    with caplog.at_level(logging.ERROR, "rigplane_testkit.sim.model.yaesu_cat"):
        assert model.handle("QQ;") == "?;"  # the exception does not escape
    assert model.not_modelled == {"QQ;": 1}
    assert any(
        record.levelno == logging.ERROR and "on_not_modelled" in record.getMessage()
        for record in caplog.records
    )
    assert model.handle("FA;") == "FA147225000;"  # the model keeps working


def test_a_base_exception_from_the_callback_propagates() -> None:
    # Only Exception is swallowed: a cancellation (KeyboardInterrupt is a
    # BaseException) must reach the caller. The gap is counted first.
    def callback(line: str) -> None:
        raise KeyboardInterrupt

    model = _model(on_not_modelled=callback)
    with pytest.raises(KeyboardInterrupt):
        model.handle("QQ;")
    assert model.not_modelled == {"QQ;": 1}


@pytest.mark.parametrize("line", [";", "1;", "a;", "QQ?;", "QQ;;"])
def test_malformed_bodies_are_never_counted_reported_or_looked_up(
    line: str,
) -> None:
    # A body that is not a well-formed CAT command is Refused("malformed"):
    # not a coverage gap, and the recorded table is not consulted for it
    # (the table below even holds an answer to the very line asked).
    seen: list[str] = []
    rows = [
        {"dir": "c2r", "line": line},
        {"dir": "r2c", "line": "QQ1;"},
    ]
    model = _model(
        recorded=RecordedCatAnswerTable.from_cat_pairs(rows),
        on_not_modelled=seen.append,
    )
    reply, outcome = model.handle_with_outcome(line)
    assert reply == "?;"
    assert outcome == Refused("malformed")
    assert model.not_modelled == {}
    assert seen == []


def test_a_due_scheduled_op_is_not_drained_by_a_gap() -> None:
    # The old engine-trace mechanism drained due scheduled operations as a
    # side effect of sending an unsupported command; the callback design
    # never touches the engine.
    model = _model()
    model.engine.schedule(0.0, ScheduledOp(SetFrequency(0, 7_000_000)))
    assert model.handle("QQ;") == "?;"
    assert model.handle("FA;") == "FA147225000;"  # frequency unchanged
    model.engine.advance_to(0.0)  # the op was merely pending; now it lands
    assert model.handle("FA;") == "FA007000000;"


def test_handle_with_outcome_names_every_reply_class() -> None:
    rows = [
        {"dir": "c2r", "line": "FR;"},
        {"dir": "r2c", "line": "FR01;"},
    ]
    model = _model(recorded=RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.handle_with_outcome("FA;") == ("FA147225000;", Ok())  # native data
    assert model.handle_with_outcome("FA014074000;") == (None, Ok())  # accepted SET
    assert model.handle_with_outcome("FR;") == ("FR01;", Ok())  # recorded answer
    # A native refusal with no recorded entry stands (AUTO-speed readbacks
    # 5/6 are not settable).
    assert model.handle_with_outcome("GT05;") == ("?;", Refused("native_refusal"))


def test_counting_needs_no_callback_and_modelled_answers_are_not_counted() -> None:
    model = _model()
    assert model.handle("QQ;") == "?;"  # counted without a callback
    assert model.handle("GT05;") == "?;"  # a modelled refusal, not a gap
    for line in ("FA;FB;", "FA", "FÁ;", "F" * 64 + ";"):
        assert model.handle(line) == "?;"  # malformed: never counted
    assert model.not_modelled == {"QQ;": 1}
    assert not [r for r in model.engine.trace if r.kind == "marker"]


def test_a_served_set_never_replies() -> None:
    model = _model()
    for line in (
        "FA014074000;",
        "FB007074000;",
        "MD02;",
        "TX1;",
        "TX0;",
        "AI1;",
        "ST1;",
        "VS1;",
        "AG0100;",
        "RG1120;",
        "SQ0030;",
        "NL0005;",
        "GT01;",
        "PA01;",
    ):
        assert model.handle(line) is None, line


def test_seed_from_recording_uses_the_own_codec() -> None:
    model = _model()
    rows = [
        {"dir": "c2r", "line": "IF;"},
        {"dir": "r2c", "line": "IF00025147225000+000000412003;"},
    ]
    seed_from_recording(model, RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.handle("IF;") == "IF00025147225000+000000412003;"
    extra = model.engine.state.extra
    assert extra["memory_channel"] == 25
    assert extra["main_memory_mode"] is True
    # The seeded MEMORY mode makes the model refuse FA SETs, as recorded.
    assert model.handle("FA014074000;") == "?;"


def test_s_meter_wire_value_is_clamped_but_the_engine_keeps_what_was_set() -> None:
    model = _model()
    assert model.handle("GT01;") is None  # AGC on: the reading is reported
    assert model.handle("GT11;") is None
    model.engine.apply(SetMeter("s_meter_main", 1000))
    model.engine.apply(SetMeter("s_meter_sub", -1))
    # The CAT wire domain is 000-255: the reply reports the clamped value.
    assert model.handle("SM0;") == "SM0255;"
    assert model.handle("SM1;") == "SM0000;"
    # The engine stores whatever the scenario set.
    assert model.engine.read("meter:s_meter_main") == Ok(1000)
    assert model.engine.read("meter:s_meter_sub") == Ok(-1)


def test_seeding_ignores_an_s_meter_reading_outside_the_wire_domain() -> None:
    rows = [
        {"dir": "c2r", "line": "SM0;"},
        {"dir": "r2c", "line": "SM0300;"},  # outside 000-255: not seeded
        {"dir": "c2r", "line": "SM1;"},
        {"dir": "r2c", "line": "SM0255;"},  # top of the domain: seeded
    ]
    model = _model()
    seed_from_recording(model, RecordedCatAnswerTable.from_cat_pairs(rows))
    assert model.engine.read("meter:s_meter_main") == Ok(0)
    assert model.engine.read("meter:s_meter_sub") == Ok(255)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("memory_channel", 123456),  # wider than the 5-digit field
        ("memory_channel", -1),
        ("if_offset_hz", 12345),  # wider than the 4-digit field
        ("if_offset_hz", -12345),
    ],
)
def test_out_of_domain_extra_values_fall_back_to_the_default_in_the_if_reply(
    key: str, value: int
) -> None:
    model = _model()
    model.engine.state.extra[key] = value
    # Identical to the default reply: the exact 5/9/4-digit field widths.
    assert model.handle("IF;") == "IF00000147225000+000000402003;"


def test_if_tone_mode_is_clamped_into_its_one_digit_field() -> None:
    model = _model()
    model.engine.state.extra["if_tone_mode"] = 12
    assert model.handle("IF;") == "IF00000147225000+000000409003;"
    model.engine.state.extra["if_tone_mode"] = -3
    assert model.handle("IF;") == "IF00000147225000+000000400003;"


def test_out_of_domain_selector_and_flag_readbacks_fall_back_to_zero() -> None:
    model = _model()
    # The wire fields are one digit; engine state must not widen them.
    model.engine.state.receivers[0].selectors["agc"] = 12
    assert model.handle("GT0;") == "GT00;"
    model.engine.state.extra["auto_information"] = 7
    assert model.handle("AI;") == "AI0;"


def test_model_imports_only_the_allowed_modules() -> None:
    tree = ast.parse(MODEL_PATH.read_text())
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
    # The production parser/formatter must not generate the replies.
    assert "CatCommandParser" not in names
    assert "format_command" not in names
    for module in modules:
        # The model imports NOTHING from rigplane, not even under
        # TYPE_CHECKING: profile resolution belongs to the loader/factory
        # boundary (sim-architecture.md section 3). rigplane_testkit.*
        # imports stay allowed.
        assert module != "rigplane" and not module.startswith("rigplane."), module
        assert module.split(".")[0] != "tests", module


def test_same_seed_same_request_sequence_gives_identical_replies_and_trace() -> None:
    sequence = [
        "FA;",
        "MD0;",
        "FA014074000;",
        "MD02;",
        "GT01;",
        "SM0;",
        "AG0255;",
        "AG0;",
        "TX1;",
        "TX;",
        "QQ;",
    ]

    def run() -> tuple[list[str | None], object]:
        model = _model()
        return [model.handle(line) for line in sequence], model.engine.trace

    replies_a, trace_a = run()
    replies_b, trace_b = run()
    assert replies_a == replies_b
    assert trace_a == trace_b
