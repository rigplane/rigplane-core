"""MOR-3859: ``discover`` -> run the printed command, both through ``main()``.

Each test runs ``rigplane discover`` through ``main()``, takes the copy-paste
line it printed and runs exactly that line through ``main()`` again. Only
the hardware boundary is faked (discovery results, ``create_radio``); the
real ``status`` command prints the outcome, so config, dispatch and output
are all asserted. Environment variables that would redirect a connection
(``ICOM_PORT``, ``ICOM_SERIAL_*``) are set on purpose: a printed line must
win over them.
"""

from __future__ import annotations

import shlex
from typing import Any
from unittest.mock import AsyncMock

import pytest
from cli_main_harness import FREQ_HZ, MODE, MainRun, run_main

from rigplane.backends.config import (
    LanBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.discovery import RadioDiscoveryResult


def _discover_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *,
    lan: list[dict[str, Any]] | None = None,
    serial: list[RadioDiscoveryResult] | None = None,
) -> str:
    """Run ``rigplane discover`` through ``main()``; return its one command."""
    monkeypatch.setattr(
        "rigplane.discovery.discover_lan_radios", AsyncMock(return_value=lan or [])
    )
    monkeypatch.setattr(
        "rigplane.discovery.discover_serial_radios",
        AsyncMock(return_value=serial or []),
    )
    run = run_main(["discover", "--timeout", "0.1"], monkeypatch, capsys)
    assert run.exit_code == 0
    lines = [ln.strip() for ln in run.stdout.splitlines() if "rigplane --model" in ln]
    assert len(lines) == 1, run.stdout
    return lines[0]


def _replay(
    line: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> MainRun:
    tokens = shlex.split(line)
    assert tokens[0] == "rigplane"
    return run_main(tokens[1:], monkeypatch, capsys)


def _assert_status_printed(run: MainRun) -> None:
    assert run.exit_code == 0, run.stderr
    assert f"{FREQ_HZ:,} Hz" in run.stdout
    assert MODE in run.stdout


def test_ftx1_yaesu_printed_command_runs_through_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ICOM_SERIAL_BAUDRATE", "38400")
    monkeypatch.setenv("ICOM_SERIAL_DEVICE", "/dev/ttyENV")
    line = _discover_line(
        monkeypatch,
        capsys,
        serial=[
            RadioDiscoveryResult(
                port="/dev/ttyUSB1",
                protocol="yaesu_cat",
                model="FTX-1",
                profile_id="yaesu_ftx1",
                baudrate=9600,
                address="0840",
                description="FTX-1",
            )
        ],
    )
    assert line == (
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1 --serial-baud 9600 status"
    )
    run = _replay(line, monkeypatch, capsys)
    _assert_status_printed(run)
    (config,) = run.configs
    assert isinstance(config, YaesuCatBackendConfig)
    assert config.model == "FTX-1"
    assert config.device == "/dev/ttyUSB1"
    assert config.baudrate == 9600


def test_unknown_address_serial_printed_command_runs_once_model_is_chosen(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ICOM_SERIAL_BAUDRATE", "9600")
    line = _discover_line(
        monkeypatch,
        capsys,
        serial=[
            RadioDiscoveryResult(
                port="/dev/ttyUSB2",
                protocol="civ",
                model="Unknown (0x95)",
                profile_id="unknown",
                baudrate=115200,
                address=0x95,
                description="Unknown",
            )
        ],
    )
    assert line == (
        "rigplane --model <MODEL> --serial-port /dev/ttyUSB2"
        " --serial-baud 115200 --radio-addr 0x95 status"
    )
    # As printed, the placeholder is refused and points at `rigplane models`.
    refused = _replay(line, monkeypatch, capsys)
    assert refused.exit_code == 1
    assert refused.configs == []
    assert "Unknown model '<MODEL>'" in refused.stderr
    assert "rigplane models" in refused.stderr
    # With a model chosen from `rigplane models`, the discovered port, baud
    # rate and CI-V address are kept.
    run = _replay(line.replace("<MODEL>", "IC-7300"), monkeypatch, capsys)
    _assert_status_printed(run)
    (config,) = run.configs
    assert isinstance(config, SerialBackendConfig)
    assert config.device == "/dev/ttyUSB2"
    assert config.baudrate == 115200
    assert config.radio_addr == 0x95


def test_lan_printed_command_wins_over_conflicting_icom_port(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("ICOM_PORT", "50099")
    line = _discover_line(
        monkeypatch,
        capsys,
        lan=[
            {
                "host": "192.168.1.50",
                "remote_id": 2,
                "model": "IC-7610",
                "address": 0x98,
            }
        ],
    )
    assert line == (
        "rigplane --model IC-7610 --backend lan --host 192.168.1.50"
        " --control-port 50001 status"
    )
    run = _replay(line, monkeypatch, capsys)
    _assert_status_printed(run)
    (config,) = run.configs
    assert isinstance(config, LanBackendConfig)
    assert config.host == "192.168.1.50"
    assert config.port == 50001


def test_ic7300_printed_command_runs_through_main(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    line = _discover_line(
        monkeypatch,
        capsys,
        serial=[
            RadioDiscoveryResult(
                port="/dev/ttyUSB0",
                protocol="civ",
                model="IC-7300",
                profile_id="icom_ic7300",
                baudrate=115200,
                address=0x94,
                description="IC-7300",
            )
        ],
    )
    run = _replay(line, monkeypatch, capsys)
    _assert_status_printed(run)
    (config,) = run.configs
    assert isinstance(config, SerialBackendConfig)
    assert config.model == "IC-7300"
    assert config.baudrate == 115200
    assert config.radio_addr == 0x94
