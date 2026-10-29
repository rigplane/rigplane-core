"""Tests for the ``rigplane models`` subcommand (list radio profiles).

RED suite: ``rigplane.cli._models`` does not exist yet, so importing this
file fails with ``ModuleNotFoundError`` until the feature is implemented.
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from pathlib import Path
from typing import Any

import pytest

from rigplane import cli
from rigplane.cli import _build_parser
from rigplane.cli import _models
from rigplane.rig_loader import RigLoadError, discover_available_rigs


def _json_entries(capsys) -> list[dict[str, Any]]:
    """Run ``models --json`` through the real parser; return the entries."""
    args = _build_parser().parse_args(["models", "--json"])
    assert args.command == "models"
    assert _models.run(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["models"], list)
    return payload["models"]


def _entries_by_model(capsys) -> dict[str, dict[str, Any]]:
    """Index the ``models --json`` entries by their ``--model`` value."""
    return {entry["model"]: entry for entry in _json_entries(capsys)}


# ---------------------------------------------------------------------------
# 1. Parser wiring
# ---------------------------------------------------------------------------


def test_models_parser_defaults() -> None:
    args = _build_parser().parse_args(["models"])
    assert args.command == "models"
    assert args.json is False


def test_models_parser_json_flag() -> None:
    args = _build_parser().parse_args(["models", "--json"])
    assert args.command == "models"
    assert args.json is True


def test_models_parser_global_model_before_command() -> None:
    args = _build_parser().parse_args(["--model", "IC-7300", "models"])
    assert args.command == "models"
    assert args.model == "IC-7300"


# ---------------------------------------------------------------------------
# 2. JSON content
# ---------------------------------------------------------------------------


def test_models_json_lists_every_profile(capsys) -> None:
    entries = _json_entries(capsys)
    rigs = discover_available_rigs(cli._rigs_dir())
    assert {entry["model"] for entry in entries} == set(rigs.keys())


def test_models_json_sorted_by_maker_then_model(capsys) -> None:
    entries = _json_entries(capsys)
    keys = [(entry["maker"].lower(), entry["model"].lower()) for entry in entries]
    assert keys == sorted(keys)


def test_models_json_entries_resolve_via_resolve_model(capsys) -> None:
    for entry in _json_entries(capsys):
        for value in (entry["model"], entry["id"]):
            resolved = cli._resolve_model(
                argparse.Namespace(model=value, radio_addr=None),
            )
            assert resolved[1] == entry["model"]


def test_models_json_expected_shipped_profiles(capsys) -> None:
    by_model = _entries_by_model(capsys)

    ic7610 = by_model["IC-7610"]
    assert ic7610["id"] == "icom_ic7610"
    assert ic7610["maker"] == "Icom"
    assert ic7610["name"] == "Icom IC-7610"
    assert ic7610["protocol"] == "civ"
    assert ic7610["transports"] == ["LAN", "USB CI-V"]
    assert ic7610["backends"] == ["lan", "serial"]
    assert "default_backend" not in ic7610
    assert ic7610["profile_only"] is False

    ic7300 = by_model["IC-7300"]
    assert ic7300["transports"] == ["USB CI-V"]
    assert ic7300["backends"] == ["serial"]
    assert "default_backend" not in ic7300
    assert ic7300["profile_only"] is False

    ftx1 = by_model["FTX-1"]
    assert ftx1["id"] == "yaesu_ftx1"
    assert ftx1["maker"] == "Yaesu"
    assert ftx1["name"] == "Yaesu FTX-1"
    assert ftx1["protocol"] == "yaesu_cat"
    assert ftx1["transports"] == ["USB CAT"]
    assert ftx1["backends"] == ["yaesu-cat"]
    assert "default_backend" not in ftx1
    assert ftx1["profile_only"] is False

    tx500 = by_model["TX-500"]
    assert tx500["maker"] == "Lab599"
    assert tx500["transports"] == ["USB CAT"]
    assert tx500["backends"] == []
    assert "default_backend" not in tx500
    assert tx500["profile_only"] is True

    x6100 = by_model["X6100"]
    assert x6100["transports"] == ["USB CI-V"]
    assert x6100["backends"] == []
    assert "default_backend" not in x6100
    assert x6100["profile_only"] is True

    ic705 = by_model["IC-705"]
    assert "LAN" in ic705["transports"]
    assert "USB CI-V" in ic705["transports"]


# ---------------------------------------------------------------------------
# 3. Text table
# ---------------------------------------------------------------------------


def test_models_text_table(capsys) -> None:
    args = _build_parser().parse_args(["models"])
    assert _models.run(args) == 0
    text = capsys.readouterr().out
    assert "--model" in text
    assert "Transports" in text
    assert "Backends" in text
    assert "Also accepted" in text
    assert "case-insensitive" in text
    assert "tested" not in text.lower()

    # Expected count comes from the JSON mode, not a hardcoded number.
    profile_only_count = sum(
        1 for entry in _json_entries(capsys) if entry["profile_only"]
    )

    args = _build_parser().parse_args(["models"])
    assert _models.run(args) == 0
    text = capsys.readouterr().out
    assert text.count("profile only, no backend yet") == profile_only_count
    assert "tested" not in text.lower()


# ---------------------------------------------------------------------------
# 4. match_rig
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["IC-7300", "ic-7300", "icom_ic7300", "icom-ic7300", "ICOM-IC7300"],
)
def test_match_rig_resolves_ic7300_spellings(name: str) -> None:
    rigs = discover_available_rigs(cli._rigs_dir())
    rig = _models.match_rig(rigs, name)
    assert rig is not None
    assert rig.model == "IC-7300"


def test_match_rig_unknown_returns_none() -> None:
    rigs = discover_available_rigs(cli._rigs_dir())
    assert _models.match_rig(rigs, "nope") is None


# ---------------------------------------------------------------------------
# 5. backend_for_protocol
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("protocol", "serial_port", "expected"),
    [
        ("yaesu_cat", False, "yaesu-cat"),
        ("yaesu_cat", True, "yaesu-cat"),
        ("civ", False, "lan"),
        ("civ", True, "serial"),
        ("kenwood_cat", False, None),
        ("kenwood_cat", True, None),
        (None, False, None),
        (None, True, None),
    ],
)
def test_backend_for_protocol(
    protocol: str | None, serial_port: bool, expected: str | None
) -> None:
    assert _models.backend_for_protocol(protocol, serial_port=serial_port) == expected


# ---------------------------------------------------------------------------
# 6. connect_command serial extras
# ---------------------------------------------------------------------------


def test_connect_command_plain_serial_has_no_extras() -> None:
    line = _models.connect_command("IC-7300", serial_port="/dev/ttyUSB0")
    assert line == (
        "rigplane --model IC-7300 --serial-port /dev/ttyUSB0 status"
    )


def test_connect_command_serial_baud() -> None:
    line = _models.connect_command(
        "FTX-1", serial_port="/dev/ttyUSB1", serial_baud=9600
    )
    assert line == (
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1"
        " --serial-baud 9600 status"
    )


def test_connect_command_radio_addr_uppercase_hex() -> None:
    line = _models.connect_command(
        "IC-7300", serial_port="/dev/ttyUSB0", radio_addr=0xAB
    )
    assert line.endswith("--radio-addr 0xAB status")


def test_connect_command_baud_and_addr_order() -> None:
    line = _models.connect_command(
        "IC-7300",
        serial_port="/dev/ttyUSB0",
        serial_baud=19200,
        radio_addr=0x95,
    )
    assert line == (
        "rigplane --model IC-7300 --serial-port /dev/ttyUSB0"
        " --serial-baud 19200 --radio-addr 0x95 status"
    )


# ---------------------------------------------------------------------------
# 7. _resolve_model unknown model
# ---------------------------------------------------------------------------


def test_resolve_model_unknown_mentions_models_command() -> None:
    args = argparse.Namespace(model="NO-SUCH-MODEL", radio_addr=None)
    with pytest.raises(ValueError, match="Unknown model") as excinfo:
        cli._resolve_model(args)
    assert "Available:" in str(excinfo.value)
    assert "rigplane models" in str(excinfo.value)


# ---------------------------------------------------------------------------
# 7. main() dispatch
# ---------------------------------------------------------------------------


def test_main_dispatches_models_json(monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "argv", ["rigplane", "models", "--json"])
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 0
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload["models"], list)
    assert payload["models"] != []


@pytest.mark.parametrize(
    "argv",
    [["rigplane", "--json", "models"], ["rigplane", "models", "--json"]],
)
def test_main_models_json_flag_position(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--json` works before and after the `models` subcommand."""
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(sys, "argv", argv)
    try:
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
    assert excinfo.value.code == 0
    assert signal.getsignal(signal.SIGTERM) is prev_sigterm
    assert signal.getsignal(signal.SIGINT) is prev_sigint
    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, dict)
    assert isinstance(payload["models"], list)
    assert payload["models"] != []


def test_main_models_profile_load_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken profile makes `models` print `Error:` and exit 1."""
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(sys, "argv", ["rigplane", "models"])

    def _boom(*args: object, **kwargs: object) -> object:
        raise RigLoadError("boom")

    monkeypatch.setattr("rigplane.cli._models.discover_available_rigs", _boom)
    try:
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
    assert excinfo.value.code == 1
    assert signal.getsignal(signal.SIGTERM) is prev_sigterm
    assert signal.getsignal(signal.SIGINT) is prev_sigint
    captured = capsys.readouterr()
    assert "Error:" in captured.err
    assert "boom" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out


# ---------------------------------------------------------------------------
# 8. Docs drift
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _readme_model_column() -> list[str]:
    """Return the ``--model`` values from the Supported radios table."""
    lines = (_repo_root() / "README.md").read_text(encoding="utf-8").splitlines()
    start = next(
        index
        for index, line in enumerate(lines)
        if line.strip() == "## Supported radios"
    )
    table: list[str] = []
    for line in lines[start + 1 :]:
        if line.strip().startswith("|"):
            table.append(line)
        elif table:
            break
    header = [cell.strip() for cell in table[0].strip().strip("|").split("|")]
    column = next(
        index for index, cell in enumerate(header) if cell == "`--model` value"
    )
    values: list[str] = []
    for row in table[1:]:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-{3,}:?", cell) is not None for cell in cells):
            continue
        values.append(cells[column].replace("`", "").strip())
    return values


def test_readme_supported_radios_match_profiles(capsys) -> None:
    values = _readme_model_column()
    assert values != []
    rigs = discover_available_rigs(cli._rigs_dir())
    for value in values:
        assert _models.match_rig(rigs, value) is not None, value
    command_models = {entry["model"] for entry in _json_entries(capsys)}
    assert set(values) <= command_models
    assert {"IC-7610", "IC-7300", "FTX-1", "TX-500", "X6100"} <= set(values)


def test_cli_guide_mentions_models_command() -> None:
    text = (_repo_root() / "docs" / "guide" / "cli.md").read_text(encoding="utf-8")
    assert "rigplane models" in text
