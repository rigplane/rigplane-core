"""Grammar tests for ``rigplane.tci.protocol`` (MOR-3821 L1, spec 4.7).

Pure functions: parse ``name:arg,arg;`` text lines into commands and
format them back. Case-insensitive names, empty arguments discarded,
read vs set by argument presence, booleans ``true``/``1``.
"""

import pytest

from rigplane.tci.protocol import (
    TciCommand,
    format_command,
    parse_bool,
    parse_int,
    parse_line,
)


def test_parse_single_set() -> None:
    assert parse_line("vfo:0,0,14074000;") == [
        TciCommand(name="vfo", args=("0", "0", "14074000"))
    ]


def test_parse_read_has_no_args() -> None:
    (cmd,) = parse_line("drive:0;")
    assert cmd == TciCommand(name="drive", args=("0",))
    assert not cmd.is_read
    (bare,) = parse_line("ready;")
    assert bare == TciCommand(name="ready", args=())
    assert bare.is_read


def test_names_are_case_insensitive() -> None:
    assert parse_line("VFO:0,0;") == [TciCommand(name="vfo", args=("0", "0"))]
    assert parse_line("TrX:0,True;") == [TciCommand(name="trx", args=("0", "True"))]


def test_empty_arguments_discarded() -> None:
    assert parse_line("vfo:0,,14074000;") == [
        TciCommand(name="vfo", args=("0", "14074000"))
    ]
    assert parse_line("drive:0,;") == [TciCommand(name="drive", args=("0",))]


def test_multiple_commands_per_line() -> None:
    assert parse_line("vfo:0,0,14074000;modulation:0,USB;ready;") == [
        TciCommand(name="vfo", args=("0", "0", "14074000")),
        TciCommand(name="modulation", args=("0", "USB")),
        TciCommand(name="ready", args=()),
    ]


def test_empty_segments_ignored() -> None:
    assert parse_line("") == []
    assert parse_line("   ") == []
    assert parse_line("a:1;;b:2;") == [
        TciCommand(name="a", args=("1",)),
        TciCommand(name="b", args=("2",)),
    ]
    assert parse_line(":1;") == []


def test_unknown_command_parses_structurally() -> None:
    # The leaf has no command dictionary: an unknown name still parses,
    # and the adapter decides (spec 4.7 rule 5: ignored without a reply).
    assert parse_line("frobnicate:1,2;") == [
        TciCommand(name="frobnicate", args=("1", "2"))
    ]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("1", True),
        ("false", False),
        ("FALSE", False),
        ("0", False),
        ("yes", None),
        ("2", None),
        ("", None),
    ],
)
def test_parse_bool(text: str, expected: bool | None) -> None:
    assert parse_bool(text) is expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("14074000", 14074000),
        ("0", 0),
        ("-100", -100),
        ("+50", 50),
        ("abc", None),
        ("14.5", None),
        ("0x10", None),
        ("", None),
    ],
)
def test_parse_int(text: str, expected: int | None) -> None:
    assert parse_int(text) == expected


def test_format_round_trip() -> None:
    assert format_command("vfo", ("0", "0", "14074000")) == "vfo:0,0,14074000;"
    assert format_command("ready") == "ready;"
    for line in ("vfo:0,0,14074000;", "modulation:0,USB;", "ready;"):
        (cmd,) = parse_line(line)
        assert format_command(cmd.name, cmd.args) == line


def test_format_lowercases_name() -> None:
    assert format_command("VFO", ("0",)) == "vfo:0;"


@pytest.mark.parametrize(
    ("text", "args"),
    [
        ("audio_start:0;", ("0",)),
        ("audio_start:0,x;", ("0", "x")),
    ],
)
def test_audio_start_arity_pinned(text: str, args: tuple[str, ...]) -> None:
    # The leaf has no per-command arity: both shapes parse to the sent tuple
    # (the adapter decides); formatting round-trips.
    (cmd,) = parse_line(text)
    assert cmd == TciCommand(name="audio_start", args=args)
    assert format_command(cmd.name, cmd.args) == text


@pytest.mark.parametrize(
    "name",
    [
        "rx_enable",
        "ecoder_switch_1",
        "hw_sensors",
        "cw_mon",
        "channels_count",
    ],
)
def test_spec_21_names_parse_like_any_other(name: str) -> None:
    # 2.1-only names and the renamed channels_count: no special casing,
    # no crash; an unknown name behaves the same (adapter ignores it).
    (cmd,) = parse_line(f"{name}:0;")
    assert cmd == TciCommand(name=name, args=("0",))
    assert format_command(cmd.name, cmd.args) == f"{name}:0;"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("  Audio_Start : 0 , x ;", TciCommand(name="audio_start", args=("0", "x"))),
        ("RX_ENABLE:0;", TciCommand(name="rx_enable", args=("0",))),
        (
            "  Channels_Count : 0, 1 ;",
            TciCommand(name="channels_count", args=("0", "1")),
        ),
    ],
)
def test_mixed_case_and_whitespace_pinned(text: str, expected: TciCommand) -> None:
    assert parse_line(text) == [expected]
