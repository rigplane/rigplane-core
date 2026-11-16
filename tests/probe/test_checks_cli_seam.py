"""``rigplane probe --checks`` through the real CLI (MOR-4464 B1, RED first).

Through the real parser and dispatch with only the serial boundary
faked: FakeCatRadio behind the production ``socket://`` open for Yaesu,
CivSerialFake behind the opener seam for CI-V. Synthetic checks only;
no shipped profile gains checks in B1.
"""

from __future__ import annotations

import asyncio
import dataclasses
import socket
import sys
from pathlib import Path

import pytest

import rigplane.cli as cli_module
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


def _synth_civ_check() -> ProbeCheck:
    from rigplane.types import bcd_encode

    return ProbeCheck(
        id="synth-civ-freq-level",
        title="Synthetic frequency and level",
        explain_what="Sets the frequency and one level for a moment.",
        explain_after="Puts frequency and level back exactly as they are now.",
        warnings=(),
        preconditions=(),
        witness=("get_freq", "get_rf_power"),
        steps=(
            ProbeCheckStep(
                kind="write",
                command="set_freq",
                values=(("data_hex", bcd_encode(7074000).hex()),),
            ),
            ProbeCheckStep(kind="read", command="get_freq"),
            ProbeCheckStep(
                kind="write",
                command="set_rf_power",
                values=(("data_hex", "64"),),
            ),
            ProbeCheckStep(kind="read", command="get_rf_power"),
        ),
        restore=(
            ProbeRestoreItem(read="get_freq", write="set_freq"),
            ProbeRestoreItem(read="get_rf_power", write="set_rf_power"),
        ),
    )


def _rigs_with(model: str, profile: str, check: ProbeCheck) -> dict:
    from rigplane.profiles.rig_loader import load_rig

    base = load_rig(REPO_ROOT / "rigs" / f"{profile}.toml")
    synth = dataclasses.replace(base, probe_checks=(check,))
    return {model: synth}


class _FakeStdin:
    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        assert self._answers, "asked more prompts than scripted"
        return self._answers.pop(0) + "\n"


def _run_main(monkeypatch, argv: list[str], answers: list[str]) -> int:
    monkeypatch.setattr(sys, "stdin", _FakeStdin(answers))
    monkeypatch.setattr("sys.argv", ["rigplane", *argv])
    opened: list[Path] = []
    monkeypatch.setattr(probe_cli, "open_result_folder", opened.append)
    with pytest.raises(SystemExit) as exc_info:
        main()
    return exc_info.value.code


def _patch_rigs(monkeypatch, rigs: dict) -> None:
    monkeypatch.setattr(
        "rigplane.rig_loader.discover_available_rigs", lambda *a, **k: rigs
    )
    monkeypatch.setattr(cli_module, "_run_probe", probe_cli.run)


def _cat_audit(radio) -> None:
    """Every sent CAT line is allow-listed and never never-listed."""
    from rigplane.probe.write_policy import YAESU_NEVER, YAESU_WRITE_ALLOW

    for line in radio.commands:
        verb = line[:2]
        if verb == "ID" or line in ("FA;", "MD0;", "TX;", "ST;", "IF;"):
            continue
        if line.endswith(";") and len(line) <= 4:
            continue  # a read line
        assert verb in YAESU_WRITE_ALLOW, line
        assert verb not in YAESU_NEVER, line


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
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    asyncio.run(radio.start())
    try:
        _patch_rigs(monkeypatch, _rigs_with("FTX-1", "ftx1", _synth_cat_check()))
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--yes", "--out", str(out), "--checks", "--label", "seam-n"],
            ["y", "y", "n", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read"
    assert document.checks == ()
    assert radio.frequency_hz == 14_074_000
    assert radio.mode_code == "2"


def test_per_check_default_is_no(tmp_path, monkeypatch) -> None:
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    asyncio.run(radio.start())
    try:
        _patch_rigs(monkeypatch, _rigs_with("FTX-1", "ftx1", _synth_cat_check()))
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--out", str(out), "--checks"],
            ["y", "y", "", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read"
    assert document.checks == ()
    assert not [line for line in radio.commands if line.startswith("FA0")]
    _cat_audit(radio)


def test_full_cat_run_with_synthetic_check(tmp_path, monkeypatch) -> None:
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    asyncio.run(radio.start())
    try:
        _patch_rigs(monkeypatch, _rigs_with("FTX-1", "ftx1", _synth_cat_check()))
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
    assert record.id == "synth-freq-mode"
    assert record.verified is True
    assert record.restored is True
    assert record.preconditions_ok is True
    assert radio.frequency_hz == 14_074_000
    assert radio.mode_code == "2"
    _cat_audit(radio)


def test_clamped_cat_write_is_unverified_exit_0(tmp_path, monkeypatch) -> None:
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    radio.clamp_write("FA", "FA007074000", "FA005000000")
    asyncio.run(radio.start())
    try:
        _patch_rigs(monkeypatch, _rigs_with("FTX-1", "ftx1", _synth_cat_check()))
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--out", str(out), "--checks"],
            ["y", "y", "y", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read+checks"
    (record,) = document.checks
    assert record.verified is False
    assert record.restored is True
    rows = {row.request_hex: row for row in record.steps}
    assert rows["FA007074000;".encode("ascii").hex()].reply_text == "FA005000000;"


def test_link_dies_mid_check_leaves_journal_exit_4(tmp_path, monkeypatch) -> None:
    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=14_074_000)
    radio.drop_after(12)
    asyncio.run(radio.start())
    try:
        _patch_rigs(monkeypatch, _rigs_with("FTX-1", "ftx1", _synth_cat_check()))
        code = _run_main(
            monkeypatch,
            ["probe", "FTX-1", "--port", radio.device_url,
             "--out", str(out), "--checks"],
            ["y", "y", "y", "y"],
        )
    finally:
        asyncio.run(radio.stop())
    assert code == 4
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read+checks"
    assert document.checks[0].verified is False
    journal = tmp_path / "probe.json.checks-journal.json"
    assert journal.exists()


def test_next_start_prints_pending_journal(tmp_path, monkeypatch, capsys) -> None:
    from rigplane.probe.journal import ChecksJournal, write_journal

    out = tmp_path / "probe.json"
    write_journal(
        tmp_path / "probe.json.checks-journal.json",
        ChecksJournal(
            check_id="synth-freq-mode",
            check_title="Synthetic frequency and mode",
            originals={"get_freq": "FA014074000;", "get_mode": "MD2;"},
            saved_utc="2026-11-16T00:00:00+00:00",
        ),
    )
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
    printed = capsys.readouterr().out
    assert "A check did not finish last time." in printed
    assert "Please set these by hand." in printed
    # The journal is left in place until the user deletes it.
    assert (tmp_path / "probe.json.checks-journal.json").exists()


def test_full_civ_run_with_synthetic_check_no_network(tmp_path, monkeypatch) -> None:
    from rigplane.core.serial_open import open_serial_port as real_open
    from rigplane_testkit.serial import CivSerialFake

    out = tmp_path / "probe.json"
    fake = CivSerialFake(model="IC-7300", echo=False)

    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        return await real_open(device, baudrate=baudrate, opener=fake.opener)

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)

    def _refuse_network(*args, **kwargs):
        raise AssertionError("probe must not open network connections here")

    monkeypatch.setattr(socket, "create_connection", _refuse_network)
    _patch_rigs(monkeypatch, _rigs_with("IC-7300", "ic7300", _synth_civ_check()))
    code = _run_main(
        monkeypatch,
        ["probe", "IC-7300", "--port", "fake-usb-serial",
         "--out", str(out), "--checks", "--label", "seam-civ"],
        ["y", "y", "y", "y"],
    )
    assert code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read+checks"
    assert len(document.checks) == 1
    record = document.checks[0]
    assert record.verified is True
    assert record.restored is True
    assert fake.frequency_hz == 14_074_000
    from rigplane.probe.write_policy import CIV_NEVER_ANY, CIV_WRITE_ALLOW

    for frame in fake.commands:
        assert frame.command in CIV_WRITE_ALLOW, frame.command
        assert (frame.command, None) not in CIV_NEVER_ANY, frame.command


def test_checks_on_profile_without_checks_says_so(tmp_path, monkeypatch, capsys) -> None:
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
    assert "has no write checks" in capsys.readouterr().out
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.mode == "read"
