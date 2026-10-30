"""MOR-3859: ``discover`` -> run the printed command through ``main()``.

Every test discovers a radio, takes the copy-paste line ``discover`` printed
and runs exactly that line through ``main()`` with the hardware factory
replaced by a recorder, so the backend config the line really produces is
asserted, not a hand-built imitation of it. Environment variables that would
redirect a connection (``ICOM_PORT``, ``ICOM_SERIAL_*``) are set on purpose:
a printed line must win over them.
"""

from __future__ import annotations

import asyncio
import shlex
import signal
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane import cli
from rigplane.backends.config import (
    BackendConfig,
    LanBackendConfig,
    SerialBackendConfig,
    YaesuCatBackendConfig,
)
from rigplane.discovery import RadioDiscoveryResult


class _Radio:
    """Smallest radio the one-shot ``status`` path can hold."""

    capabilities: frozenset[str] = frozenset()

    async def __aenter__(self) -> _Radio:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None


@dataclass
class _Run:
    exit_code: int
    config: BackendConfig | None
    stderr: str


def _discover_lines(
    capsys: pytest.CaptureFixture[str],
    *,
    lan: list[dict[str, Any]] | None = None,
    serial: list[RadioDiscoveryResult] | None = None,
) -> list[str]:
    """Run ``discover`` and return the printed ``rigplane ...`` lines."""
    args = SimpleNamespace(serial_only=False, lan_only=False, timeout=0.1, json=False)
    with (
        patch(
            "rigplane.discovery.discover_lan_radios",
            AsyncMock(return_value=lan or []),
        ),
        patch(
            "rigplane.discovery.discover_serial_radios",
            AsyncMock(return_value=serial or []),
        ),
    ):
        assert asyncio.run(cli._cmd_discover(None, args)) == 0
    out = capsys.readouterr().out
    return [line.strip() for line in out.splitlines() if "rigplane --model" in line]


@pytest.fixture()
def run_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> Iterator[Callable[[str], _Run]]:
    """Return a runner that feeds one printed line to ``main()``."""
    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    monkeypatch.setenv("ICOM_PID_FILE", "")
    # main() installs signal handlers; restore them so the suite keeps its own.
    prev = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))

    def _run(line: str) -> _Run:
        tokens = shlex.split(line)
        assert tokens[0] == "rigplane"
        seen: list[BackendConfig] = []

        def _create(config: BackendConfig) -> _Radio:
            seen.append(config)
            return _Radio()

        def _exit(code: int) -> None:
            raise SystemExit(code)

        with (
            patch.object(sys, "argv", tokens),
            patch("rigplane.cli.create_radio", _create),
            patch("rigplane.cli.acquire_resident_lock"),
            patch("rigplane.cli._cmd_status", AsyncMock(return_value=0)),
            patch("rigplane.cli.os._exit", _exit),
        ):
            with pytest.raises(SystemExit) as excinfo:
                cli.main()
        return _Run(
            exit_code=int(excinfo.value.code or 0),
            config=seen[0] if seen else None,
            stderr=capsys.readouterr().err,
        )

    yield _run
    signal.signal(signal.SIGTERM, prev[0])
    signal.signal(signal.SIGINT, prev[1])


def test_ftx1_yaesu_printed_command_runs_through_main(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_printed: Callable[[str], _Run],
) -> None:
    monkeypatch.setenv("ICOM_SERIAL_BAUDRATE", "38400")
    monkeypatch.setenv("ICOM_SERIAL_DEVICE", "/dev/ttyENV")
    lines = _discover_lines(
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
    assert lines == [
        "rigplane --model FTX-1 --serial-port /dev/ttyUSB1 --serial-baud 9600 status"
    ]
    result = run_printed(lines[0])
    assert result.exit_code == 0
    assert isinstance(result.config, YaesuCatBackendConfig)
    assert result.config.model == "FTX-1"
    assert result.config.device == "/dev/ttyUSB1"
    assert result.config.baudrate == 9600


def test_unknown_address_serial_printed_command_runs_once_model_is_chosen(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_printed: Callable[[str], _Run],
) -> None:
    monkeypatch.setenv("ICOM_SERIAL_BAUDRATE", "9600")
    lines = _discover_lines(
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
    assert lines == [
        "rigplane --model <MODEL> --serial-port /dev/ttyUSB2"
        " --serial-baud 115200 --radio-addr 0x95 status"
    ]
    # As printed the placeholder is refused, pointing at `rigplane models`.
    refused = run_printed(lines[0])
    assert refused.exit_code == 1
    assert refused.config is None
    assert "Unknown model '<MODEL>'" in refused.stderr
    assert "rigplane models" in refused.stderr
    # The owner picks a model from `rigplane models`; the discovered port,
    # baud rate and CI-V address are kept.
    result = run_printed(lines[0].replace("<MODEL>", "IC-7300"))
    assert result.exit_code == 0
    assert isinstance(result.config, SerialBackendConfig)
    assert result.config.device == "/dev/ttyUSB2"
    assert result.config.baudrate == 115200
    assert result.config.radio_addr == 0x95


def test_lan_printed_command_wins_over_conflicting_icom_port(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    run_printed: Callable[[str], _Run],
) -> None:
    monkeypatch.setenv("ICOM_PORT", "50099")
    lines = _discover_lines(
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
    assert lines == [
        "rigplane --model IC-7610 --backend lan --host 192.168.1.50"
        " --control-port 50001 status"
    ]
    result = run_printed(lines[0])
    assert result.exit_code == 0
    assert isinstance(result.config, LanBackendConfig)
    assert result.config.host == "192.168.1.50"
    assert result.config.port == 50001


def test_ic7300_printed_command_runs_through_main(
    capsys: pytest.CaptureFixture[str],
    run_printed: Callable[[str], _Run],
) -> None:
    lines = _discover_lines(
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
    result = run_printed(lines[0])
    assert result.exit_code == 0
    assert isinstance(result.config, SerialBackendConfig)
    assert result.config.model == "IC-7300"
    assert result.config.baudrate == 115200
    assert result.config.radio_addr == 0x94
