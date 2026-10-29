"""RED tests: required --model and copy-paste `discover` commands.

Behaviour A: commands that need a radio model but get neither --model nor
--radio-addr must fail in ``_build_backend_config`` with a message that lists
every supported model (never a hardcoded list). Explicit backends that do not
need a profile (rigctld, yaesu-cat) and an explicit --radio-addr keep working.

Behaviour B: human ``rigplane discover`` output prints, after each connection
line, an indented ready-to-copy command built from the group's model resolved
through ``match_rig`` (or a literal ``<MODEL>`` placeholder with a pointer to
``rigplane models`` when the model is unknown). JSON mode is unchanged.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane import cli
from rigplane.backends.config import (
    LanBackendConfig,
    RigctldBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.cli._models import match_rig
from rigplane.discovery import RadioDiscoveryResult
from rigplane.rig_loader import discover_available_rigs


@pytest.fixture()
def parser() -> argparse.ArgumentParser:
    return _build_parser()


def _refuse_discovery(*args: object, **kwargs: object) -> object:
    """Fail the test if the missing model is filled in from discovery."""
    pytest.fail("missing --model must raise before any auto-discovery")


def _expected_supported() -> str:
    """Exact sorted model list the error message must contain."""
    return ", ".join(sorted(discover_available_rigs(cli._rigs_dir())))


# ---------------------------------------------------------------------------
# Behaviour A: --model becomes required
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--host", "1.2.3.4", "status"],
        ["--serial-port", "/dev/ttyUSB0", "status"],
    ],
)
async def test_missing_model_lan_and_serial_raise(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "_auto_discover_lan", _refuse_discovery)
    monkeypatch.setattr(cli, "_auto_discover_serial", _refuse_discovery)
    args = _build_parser().parse_args(argv)
    with pytest.raises(ValueError) as excinfo:
        await _build_backend_config(args)
    message = str(excinfo.value)
    assert message.startswith("Specify the radio model with --model.")
    assert "Supported: " + _expected_supported() in message
    assert message.endswith("Run `rigplane discover` to see the radios connected now.")


async def test_radio_addr_lan_keeps_working(parser: argparse.ArgumentParser) -> None:
    args = parser.parse_args(["--host", "1.2.3.4", "--radio-addr", "0x94", "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, LanBackendConfig)
    assert config.radio_addr == 0x94


async def test_radio_addr_serial_keeps_working(
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(
        ["--serial-port", "/dev/ttyUSB0", "--radio-addr", "0x94", "status"]
    )
    config = await _build_backend_config(args)
    assert isinstance(config, SerialBackendConfig)
    assert config.radio_addr == 0x94


async def test_rigctld_without_model_keeps_working(
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(["--backend", "rigctld", "--host", "1.2.3.4", "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, RigctldBackendConfig)


async def test_yaesu_cat_without_model_keeps_working(
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(
        ["--backend", "yaesu-cat", "--serial-port", "/dev/ttyUSB0", "status"]
    )
    config = await _build_backend_config(args)
    assert isinstance(config, YaesuCatBackendConfig)


async def test_model_with_host_unchanged(parser: argparse.ArgumentParser) -> None:
    args = parser.parse_args(["--model", "IC-7300", "--host", "1.2.3.4", "status"])
    config = await _build_backend_config(args)
    assert isinstance(config, LanBackendConfig)
    assert config.model == "IC-7300"
    assert config.radio_addr == 0x94


async def test_missing_model_message_lists_every_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "_auto_discover_lan", _refuse_discovery)
    monkeypatch.setattr(cli, "_auto_discover_serial", _refuse_discovery)
    args = _build_parser().parse_args(["--host", "1.2.3.4", "status"])
    with pytest.raises(ValueError) as excinfo:
        await _build_backend_config(args)
    message = str(excinfo.value)
    for name in discover_available_rigs(cli._rigs_dir()):
        assert name in message, name


def test_missing_model_end_to_end_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["rigplane", "--host", "1.2.3.4", "status"])

    # main() finishes one-shot commands with os._exit: raise SystemExit
    # instead so the test process survives (as in TestMainEntryPoint).
    def _fake_exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr("rigplane.cli.os._exit", _fake_exit)

    # Without a model there is nothing to connect to.
    def _no_radio(*args: object, **kwargs: object) -> object:
        raise AssertionError("must not create a radio without a model")

    monkeypatch.setattr("rigplane.cli.create_radio", _no_radio)
    with pytest.raises(SystemExit) as excinfo:
        cli.main()
    assert excinfo.value.code == 1
    assert "Specify the radio model with --model" in capsys.readouterr().err


async def test_unknown_model_still_says_unknown_model(
    parser: argparse.ArgumentParser,
) -> None:
    args = parser.parse_args(["--host", "1.2.3.4", "--model", "IC-FAKE", "status"])
    with pytest.raises(ValueError, match="Unknown model"):
        await _build_backend_config(args)


# ---------------------------------------------------------------------------
# Behaviour B: `discover` prints copy-paste commands
# ---------------------------------------------------------------------------


def _human_args() -> SimpleNamespace:
    return SimpleNamespace(serial_only=False, lan_only=False, timeout=0.1, json=False)


@pytest.mark.asyncio
async def test_discover_serial_civ_prints_copy_paste_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB0",
            protocol="civ",
            model="IC-7300",
            profile_id="icom_ic7300",
            baudrate=115200,
            address=0x94,
            description="IC-7300",
        )
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[]),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=serial),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    assert "rigplane --model IC-7300 --serial-port /dev/ttyUSB0 status" in out


@pytest.mark.asyncio
async def test_discover_serial_yaesu_prints_copy_paste_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB1",
            protocol="yaesu_cat",
            model="FTX-1",
            profile_id="yaesu_ftx1",
            baudrate=38400,
            address="0840",
            description="FTX-1",
        )
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=[]),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=serial),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    assert "rigplane --model FTX-1 --serial-port /dev/ttyUSB1 status" in out


@pytest.mark.asyncio
async def test_discover_lan_unknown_model_prints_placeholder_and_models_hint(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    lan = [{"host": "192.168.1.60", "remote_id": 1}]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=[]),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    assert "rigplane --model <MODEL> --host 192.168.1.60 status" in out
    assert "rigplane models" in out


@pytest.mark.asyncio
async def test_discover_lan_known_model_prints_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    lan = [
        {"host": "192.168.1.50", "remote_id": 2, "model": "IC-7610", "address": 0x98}
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=[]),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    assert "rigplane --model IC-7610 --host 192.168.1.50 status" in out


@pytest.mark.asyncio
async def test_discover_printed_models_resolve_via_match_rig(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    lan = [
        {"host": "192.168.1.50", "remote_id": 2, "model": "IC-7610", "address": 0x98},
        {"host": "192.168.1.60", "remote_id": 1},
    ]
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB0",
            protocol="civ",
            model="IC-7300",
            profile_id="icom_ic7300",
            baudrate=115200,
            address=0x94,
            description="IC-7300",
        )
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=serial),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    models = re.findall(r"rigplane --model (\S+)", out)
    assert models, "expected copy-paste `rigplane --model ...` lines"
    rigs = discover_available_rigs(cli._rigs_dir())
    for name in models:
        if name == "<MODEL>":
            continue
        rig = match_rig(rigs, name)
        assert rig is not None, name
        assert rig.model == name


@pytest.mark.asyncio
async def test_discover_json_has_no_commands_and_parses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = SimpleNamespace(serial_only=False, lan_only=False, timeout=0.1, json=True)
    lan = [
        {"host": "192.168.1.50", "remote_id": 2, "model": "IC-7610", "address": 0x98}
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=[]),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    assert "rigplane --model" not in out
    assert isinstance(json.loads(out), dict)


@pytest.mark.asyncio
async def test_discover_output_never_says_tested(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    lan = [{"host": "192.168.1.60", "remote_id": 1}]
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB0",
            protocol="civ",
            model="IC-7300",
            profile_id="icom_ic7300",
            baudrate=115200,
            address=0x94,
            description="IC-7300",
        )
    ]
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=serial),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    assert "tested" not in capsys.readouterr().out.lower()
