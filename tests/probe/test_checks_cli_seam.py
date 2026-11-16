"""``rigplane probe --checks`` through the real CLI (MOR-4464 B1, RED first).

Through the real parser and dispatch with only the serial boundary
faked: FakeCatRadio behind the production ``socket://`` open for Yaesu,
CivSerialFake behind the opener seam for CI-V. Synthetic checks only;
no shipped profile gains checks in B1.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

import rigplane.cli._probe as probe_cli
from rigplane.cli import main
from rigplane.probe.model import ProbeFile
from rigplane.profiles.probe_checks import (
    ProbeCheck,
    ProbeCheckStep,
    ProbeRestoreItem,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _synth_cat_check() -> ProbeCheck:
    return ProbeCheck(
        id="synth-freq-mode",
        title="Synthetic frequency and mode",
        explain_what="Sets VFO-A to 7.074 MHz and mode to 01 for a moment.",
        explain_after="Puts frequency and mode back exactly as they are now.",
        warnings=(),
        preconditions=(),
        witness=("get_freq", "get_mode"),
        steps=(
            ProbeCheckStep(
                kind="write", command="set_freq", values=(("freq", 7074000),)
            ),
            ProbeCheckStep(kind="read", command="get_freq"),
            ProbeCheckStep(
                kind="write", command="set_mode", values=(("mode", "1"),)
            ),
            ProbeCheckStep(kind="read", command="get_mode"),
        ),
        restore=(
            ProbeRestoreItem(read="get_freq", write="set_freq"),
            ProbeRestoreItem(read="get_mode", write="set_mode"),
        ),
    )


def _rigs_with(check) -> dict:
    from rigplane.profiles.rig_loader import load_rig

    base = load_rig(REPO_ROOT / "rigs" / "ftx1.toml")
    synth = dataclasses.replace(base, probe_checks=(check,))
    return {synth.model: synth}


class _FakeStdin:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        assert self._answers, "asked more prompts than scripted"
        return self._answers.pop(0) + "\n"


def _run_main(monkeypatch, argv: list[str], answers: list[str]):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(answers))
    monkeypatch.setattr("sys.argv", ["rigplane", *argv])
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


def test_checks_without_terminal_is_exit_2(tmp_path, monkeypatch) -> None:
    out = tmp_path / "probe.json"

    class _NoTty:
        def isatty(self) -> bool:
            return False

    monkeypatch.setattr(sys, "stdin", _NoTty())
    monkeypatch.setattr(
        "sys.argv",
        [
            "rigplane", "probe", "FTX-1", "--port", "fake",
            "--yes", "--out", str(out), "--checks",
        ],
    )

    async def refuse_open(device: str, baudrate=None, **kwargs):
        raise AssertionError("must not open the port without a terminal")

    monkeypatch.setattr(probe_cli, "open_serial_port", refuse_open)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    assert not out.exists()


def test_yes_does_not_consent_to_checks(tmp_path, monkeypatch) -> None:
    import rigplane.cli as cli_module
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    asyncio.run(radio.start())
    try:
        monkeypatch.setattr(
            "rigplane.rig_loader.discover_available_rigs",
            lambda *a, **k: _rigs_with(_synth_cat_check()),
        )
        monkeypatch.setattr(cli_module, "_run_probe", probe_cli.run)
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--yes", "--out", str(out), "--checks", "--label", "seam-n"],
            ["n", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read"
    assert document.checks == ()
    assert radio.frequency_hz == 14_074_000
    assert radio.mode_code == "2"


def test_full_cat_run_with_synthetic_check(tmp_path, monkeypatch) -> None:
    import rigplane.cli as cli_module
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    asyncio.run(radio.start())
    try:
        monkeypatch.setattr(
            "rigplane.rig_loader.discover_available_rigs",
            lambda *a, **k: _rigs_with(_synth_cat_check()),
        )
        monkeypatch.setattr(cli_module, "_run_probe", probe_cli.run)
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--out", str(out), "--checks", "--label", "seam-y"],
            ["y", "y", "y", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read+checks"
    assert len(document.checks) == 1
    record = document.checks[0]
    assert record.verified is True
    assert record.restored is True
    assert radio.frequency_hz == 14_074_000
    assert radio.mode_code == "2"


def test_checks_on_profile_without_checks_says_so(tmp_path, monkeypatch) -> None:
    out = tmp_path / "probe.json"
    seen: list[bytes] = []

    class _ScriptedSerial:
        def __init__(self) -> None:
            self._last: bytes | None = None

        def write(self, data: bytes) -> None:
            seen.append(bytes(data))
            self._last = bytes(data)

        async def drain(self) -> None:
            pass

        def close(self) -> None:
            pass

        async def readuntil(self, separator: bytes) -> bytes:
            assert self._last is not None
            if self._last == b"ID;":
                return b"ID0840;"
            return b"?;"

    async def fake_open(device: str, baudrate=None, **kwargs):
        pair = _ScriptedSerial()
        return pair, pair

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)
    code = _run_main(
        monkeypatch,
        ["probe", "FTX-1", "--port", "fake",
         "--yes", "--out", str(out), "--checks"],
        [],
    )
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read"
