"""Unit tests for the Yaesu CAT protocol model (MOR-3602).

Pure: no sockets, a ManualClock, the FTX-1 capability profile and sim
profile loaded through the real loaders from explicit repo-root paths.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.sim.engine import ManualClock, SetMeter
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


def _model(recorded: RecordedCatAnswerTable | None = None) -> YaesuCatModel:
    rig = load_rig(ROOT / "rigs" / "ftx1.toml")
    profile = load_sim_profile(PROFILE_PATH, root=ROOT)
    engine = build_ftx1_engine(profile, rig, clock=ManualClock(), seed=7)
    return YaesuCatModel(
        engine, identity=rig.expected_identity_ids[0], recorded=recorded
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


def test_s_meter_zero_while_agc_off_and_sub_echoes_side_zero() -> None:
    model = _model()
    model.engine.apply(SetMeter("s", 120))
    # Fresh engines have AGC 0 (OFF) on both receivers: the reading is 0.
    assert model.handle("SM0;") == "SM0000;"
    assert model.handle("GT01;") is None
    assert model.handle("SM0;") == "SM0120;"
    assert model.handle("GT11;") is None
    assert model.handle("SM1;") == "SM0120;"  # SUB read, side digit echoed as 0


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
        assert not module.startswith("rigplane.backends"), module
        assert not module.startswith("rigplane.commands"), module
        assert module.split(".")[0] != "tests", module
        if module == "rigplane" or module.startswith("rigplane."):
            assert module.startswith("rigplane.profiles"), module


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
