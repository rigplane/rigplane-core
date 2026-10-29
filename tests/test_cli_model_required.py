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
import shlex
import signal
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
from rigplane.rig_loader import RigLoadError, discover_available_rigs


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
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
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
    try:
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
    assert excinfo.value.code == 1
    assert signal.getsignal(signal.SIGTERM) is prev_sigterm
    assert signal.getsignal(signal.SIGINT) is prev_sigint
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
    assert (
        "rigplane --model IC-7300 --serial-port /dev/ttyUSB0"
        " --serial-baud 115200 --radio-addr 0x94 status" in out
    )


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
    assert (
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1"
        " --serial-baud 38400 status" in out
    )


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
    assert "rigplane --model <MODEL> --backend lan --host 192.168.1.60 status" in out
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
    assert "rigplane --model IC-7610 --backend lan --host 192.168.1.50 status" in out


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


@pytest.mark.asyncio
async def test_discover_serial_ftx1_off_default_baud_prints_serial_baud(
    capsys: pytest.CaptureFixture[str],
) -> None:
    args = _human_args()
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB1",
            protocol="yaesu_cat",
            model="FTX-1",
            profile_id="yaesu_ftx1",
            baudrate=9600,
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
    assert (
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1"
        " --serial-baud 9600 status" in out
    )


@pytest.mark.asyncio
async def test_discover_serial_ftx1_default_baud_prints_serial_baud(
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
    assert (
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1"
        " --serial-baud 38400 status" in out
    )


@pytest.mark.asyncio
async def test_discover_serial_ic7300_off_default_addr_prints_radio_addr(
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
            address=0x95,
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
    assert "--radio-addr 0x95" in out


@pytest.mark.asyncio
async def test_discover_serial_ic7300_default_addr_prints_radio_addr(
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
    assert (
        "rigplane --model IC-7300 --serial-port /dev/ttyUSB0"
        " --serial-baud 115200 --radio-addr 0x94 status" in out
    )


@pytest.mark.asyncio
async def test_discover_printed_commands_replay_into_backend_config(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every printed command re-parses into the discovered backend config."""
    args = _human_args()
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB1",
            protocol="yaesu_cat",
            model="FTX-1",
            profile_id="yaesu_ftx1",
            baudrate=9600,
            address="0840",
            description="FTX-1",
        ),
        RadioDiscoveryResult(
            port="/dev/ttyUSB0",
            protocol="civ",
            model="IC-7300",
            profile_id="icom_ic7300",
            baudrate=115200,
            address=0x95,
            description="IC-7300",
        ),
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
    lines = [line.strip() for line in out.splitlines() if "rigplane --model" in line]
    assert len(lines) == 2
    configs = {}
    for line in lines:
        tokens = shlex.split(line)
        assert tokens[0] == "rigplane"
        parsed = _build_parser().parse_args(tokens[1:])
        configs[parsed.model] = await _build_backend_config(parsed)
    ftx1 = configs["FTX-1"]
    assert isinstance(ftx1, YaesuCatBackendConfig)
    assert ftx1.baudrate == 9600
    ic7300 = configs["IC-7300"]
    assert isinstance(ic7300, SerialBackendConfig)
    assert ic7300.radio_addr == 0x95


@pytest.mark.asyncio
async def test_discover_printed_commands_win_over_environment(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Printed lines replay with the observed settings, not env defaults."""
    monkeypatch.setenv("ICOM_SERIAL_BAUDRATE", "9600")
    monkeypatch.setenv("ICOM_SERIAL_DEVICE", "/dev/ttyENV")
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
            AsyncMock(return_value=serial),
        ),
    ):
        assert await cli._cmd_discover(None, args) == 0
    out = capsys.readouterr().out
    lines = [line.strip() for line in out.splitlines() if "rigplane --model" in line]
    assert len(lines) == 2
    configs = {}
    for line in lines:
        tokens = shlex.split(line)
        assert tokens[0] == "rigplane"
        parsed = _build_parser().parse_args(tokens[1:])
        configs[parsed.model] = await _build_backend_config(parsed)
    ftx1 = configs["FTX-1"]
    assert isinstance(ftx1, YaesuCatBackendConfig)
    assert ftx1.baudrate == 38400
    assert ftx1.device == "/dev/ttyUSB1"
    lan_config = configs["IC-7610"]
    assert isinstance(lan_config, LanBackendConfig)
    assert lan_config.host == "192.168.1.50"


@pytest.mark.asyncio
async def test_discover_unknown_civ_model_prints_baud_and_addr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A `<MODEL>` placeholder line still carries baud and CI-V address."""
    args = _human_args()
    serial = [
        RadioDiscoveryResult(
            port="/dev/ttyUSB2",
            protocol="civ",
            model="Unknown (0x95)",
            profile_id="unknown",
            baudrate=115200,
            address=0x95,
            description="Unknown",
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
    line = (
        "rigplane --model <MODEL> --serial-port /dev/ttyUSB2"
        " --serial-baud 115200 --radio-addr 0x95 status"
    )
    assert line in out
    tokens = shlex.split(line.replace("<MODEL>", "IC-7300"))
    assert tokens[0] == "rigplane"
    parsed = _build_parser().parse_args(tokens[1:])
    config = await _build_backend_config(parsed)
    assert isinstance(config, SerialBackendConfig)
    assert config.radio_addr == 0x95


def test_main_discover_profile_load_failure_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A broken profile makes `discover` print `Error:` before any summary."""
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
    monkeypatch.setattr(sys, "argv", ["rigplane", "discover"])
    lan = [
        {"host": "192.168.1.50", "remote_id": 2, "model": "IC-7610", "address": 0x98}
    ]
    monkeypatch.setattr(
        "rigplane.discovery.discover_lan_radios",
        AsyncMock(return_value=lan),
    )
    monkeypatch.setattr(
        "rigplane.discovery.discover_serial_radios",
        AsyncMock(return_value=[]),
    )

    def _boom(*args: object, **kwargs: object) -> object:
        raise RigLoadError("boom")

    monkeypatch.setattr("rigplane.rig_loader.discover_available_rigs", _boom)
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
    assert "Found" not in captured.out


def test_discover_line_replays_through_main_into_status(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A printed `discover` line replays through `main()` into `status`."""
    prev_sigterm = signal.getsignal(signal.SIGTERM)
    prev_sigint = signal.getsignal(signal.SIGINT)
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
    monkeypatch.setattr(
        "rigplane.discovery.discover_serial_radios",
        AsyncMock(return_value=serial),
    )
    monkeypatch.setattr(
        "rigplane.discovery.discover_lan_radios",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(sys, "argv", ["rigplane", "discover"])
    try:
        with pytest.raises(SystemExit) as discover_exit:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
    assert discover_exit.value.code == 0
    assert signal.getsignal(signal.SIGTERM) is prev_sigterm
    assert signal.getsignal(signal.SIGINT) is prev_sigint
    out = capsys.readouterr().out
    matches = re.findall(r"^    rigplane .*$", out, re.MULTILINE)
    assert len(matches) == 1
    tokens = shlex.split(matches[0].strip())
    assert tokens[0] == "rigplane"

    seen: dict[str, object] = {}
    radio = AsyncMock()
    radio.__aenter__.return_value = radio
    radio.__aexit__.return_value = False
    radio.capabilities = set()
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.get_mode = AsyncMock(return_value=("USB", None))

    def _fake_create_radio(config: object) -> object:
        seen["config"] = config
        return radio

    monkeypatch.setattr("rigplane.cli.create_radio", _fake_create_radio)
    monkeypatch.setattr("rigplane.cli.guards_backend", lambda _config: False)

    def _fake_exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr("rigplane.cli.os._exit", _fake_exit)
    monkeypatch.setattr(sys, "argv", ["rigplane", *tokens[1:]])
    try:
        with pytest.raises(SystemExit) as status_exit:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, prev_sigterm)
        signal.signal(signal.SIGINT, prev_sigint)
    assert status_exit.value.code == 0
    assert signal.getsignal(signal.SIGTERM) is prev_sigterm
    assert signal.getsignal(signal.SIGINT) is prev_sigint
    config = seen["config"]
    assert isinstance(config, SerialBackendConfig)
    assert config.device == "/dev/ttyUSB0"
    assert config.baudrate == 115200
    assert config.radio_addr == 0x94
    assert config.model == "IC-7300"
    status_out = capsys.readouterr().out
    assert "14,074,000" in status_out
    assert "USB" in status_out
