"""Run ``rigplane`` through ``main()`` with only the radio replaced.

Parsing, backend-config building, dispatch and the command handlers are the
production ones; the only fake is ``create_radio`` (no hardware, no network),
which records the backend config it was handed and returns a radio that
answers ``status``. Used by tests that follow a command line from the user's
entry point to its printed outcome.
"""

from __future__ import annotations

import signal
import sys
from dataclasses import dataclass, field

import pytest

from rigplane import cli
from rigplane.backends.config import BackendConfig

FREQ_HZ = 14_074_000
MODE = "USB"


class StatusRadio:
    """Smallest radio the one-shot ``status`` command can read."""

    capabilities: frozenset[str] = frozenset()

    async def __aenter__(self) -> StatusRadio:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def get_freq(self) -> int:
        return FREQ_HZ

    async def get_mode(self) -> tuple[str, int]:
        return MODE, 1


@dataclass
class MainRun:
    exit_code: int
    stdout: str
    stderr: str
    configs: list[BackendConfig] = field(default_factory=list)


def run_main(
    argv: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> MainRun:
    """Run ``main()`` for *argv*; return exit code, output and radio configs."""
    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    monkeypatch.setenv("ICOM_PID_FILE", "")
    configs: list[BackendConfig] = []

    def _create(config: BackendConfig) -> StatusRadio:
        configs.append(config)
        return StatusRadio()

    def _exit(code: int) -> None:
        raise SystemExit(code)

    monkeypatch.setattr(sys, "argv", ["rigplane", *argv])
    monkeypatch.setattr("rigplane.cli.create_radio", _create)
    monkeypatch.setattr("rigplane.cli.acquire_resident_lock", lambda _identity: None)
    monkeypatch.setattr("rigplane.cli.os._exit", _exit)
    capsys.readouterr()
    handlers = (signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT))
    try:
        with pytest.raises(SystemExit) as excinfo:
            cli.main()
    finally:
        signal.signal(signal.SIGTERM, handlers[0])
        signal.signal(signal.SIGINT, handlers[1])
    out = capsys.readouterr()
    code = excinfo.value.code
    return MainRun(
        exit_code=code if isinstance(code, int) else 1,
        stdout=out.out,
        stderr=out.err,
        configs=configs,
    )
