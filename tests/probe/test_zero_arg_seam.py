"""Zero-argument ``rigplane probe`` through the real CLI (MOR-4464 PR A2).

Every test starts from the real argument parser and reaches the
production dispatch, faking only the serial boundary (the opener) and
radio discovery: the discovery callable is replaced on
``rigplane.cli._probe``, the port opener the same way, and the folder
opener records instead of spawning a process. The Documents folder is
redirected through ``platformdirs.user_documents_dir``. Nothing here
touches a radio, a TTY, or a real port.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

import platformdirs
import rigplane.cli as cli_module
import rigplane.cli._probe as probe_cli
from rigplane.backends.discovery import RadioDiscoveryResult
from rigplane.cli import main
from rigplane.probe.model import ProbeFile


def _civ(
    model: str = "IC-7300",
    profile_id: str = "icom_ic7300",
    port: str = "fake-civ",
    baudrate: int = 115200,
    address: int = 0x94,
) -> RadioDiscoveryResult:
    return RadioDiscoveryResult(
        port=port,
        protocol="civ",
        model=model,
        profile_id=profile_id,
        baudrate=baudrate,
        address=address,
    )


def _cat(
    model: str = "FTX-1",
    profile_id: str = "yaesu_ftx1",
    port: str = "fake-cat",
    baudrate: int = 38400,
    address: str = "0840",
) -> RadioDiscoveryResult:
    return RadioDiscoveryResult(
        port=port,
        protocol="yaesu_cat",
        model=model,
        profile_id=profile_id,
        baudrate=baudrate,
        address=address,
    )


class _ScriptedSerial:
    """One scripted reply per request body; ``None`` means silence."""

    def __init__(
        self,
        answers: dict[bytes, bytes | None],
        seen: list[bytes],
        default: bytes | None = b"?;",
    ) -> None:
        self._answers = answers
        self._seen = seen
        self._default = default
        self._last: bytes | None = None

    def write(self, data: bytes) -> None:
        self._seen.append(bytes(data))
        self._last = bytes(data)

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def readuntil(self, separator: bytes) -> bytes:
        assert self._last is not None
        reply = self._answers.get(self._last, self._default)
        if reply is None:
            raise asyncio.TimeoutError()
        return reply


def _fake_openers(monkeypatch, seen: list[bytes]) -> None:
    """Fake the serial opener for both CI-V and CAT probe exchanges."""

    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        pair = _ScriptedSerial({b"ID;": b"ID0840;"}, seen)
        return pair, pair

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)


def _fake_discovery(monkeypatch, results: list[RadioDiscoveryResult]) -> None:
    async def fake_discover():
        return results

    monkeypatch.setattr(probe_cli, "discover_serial_radios", fake_discover)


class _FakeStdin:
    """A TTY stdin feeding scripted answers to ``input()``."""

    def __init__(self, answers: list[str]) -> None:
        self._answers = list(answers)

    def isatty(self) -> bool:
        return True

    def readline(self) -> str:
        assert self._answers, "asked more prompts than scripted"
        return self._answers.pop(0) + "\n"


def _checked_cat_openers(
    monkeypatch, seen: list[bytes], *, device: str, baud: int
) -> None:
    """Fake the serial opener, verifying the selected device and baud."""

    async def fake_open(open_device: str, baudrate: int | None = None, **kwargs):
        assert open_device == device, f"expected {device}, got {open_device}"
        assert baudrate == baud, f"expected {baud}, got {baudrate}"
        pair = _ScriptedSerial({b"ID;": b"ID0840;"}, seen)
        return pair, pair

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)


def _checked_civ_openers(
    monkeypatch, seen: list[bytes], *, device: str, baud: int, addr: int
) -> None:
    """Fake the serial opener, verifying device, baud and CI-V address."""
    from rigplane.commands import CONTROLLER_ADDR, build_civ_frame

    nak = build_civ_frame(addr, CONTROLLER_ADDR, 0xFA)

    async def fake_open(open_device: str, baudrate: int | None = None, **kwargs):
        assert open_device == device, f"expected {device}, got {open_device}"
        assert baudrate == baud, f"expected {baud}, got {baudrate}"
        pair = _ScriptedSerial({}, seen, default=nak)
        return pair, pair

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)


def _run_probe(
    monkeypatch,
    tmp_path: Path,
    argv: list[str],
    *,
    results: list[RadioDiscoveryResult],
    answers: list[str] | None = None,
    tty: bool = False,
    no_out: bool = False,
    opener=None,
    wire: list[bytes] | None = None,
) -> tuple[int, str, list[Path]]:
    """Run ``rigplane probe`` through ``main()``; return (exit, output, opened).

    ``opener`` replaces the default serial fake; it receives
    ``(monkeypatch, wire)`` so a test can verify device, baud and wire
    bytes. ``wire`` collects every written frame when given.
    """
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda *a: str(docs))
    monkeypatch.setattr(cli_module, "_run_probe", probe_cli.run)
    _fake_discovery(monkeypatch, results)
    seen: list[bytes] = wire if wire is not None else []
    if opener is not None:
        opener(monkeypatch, seen)
    else:
        _fake_openers(monkeypatch, seen)
    opened: list[Path] = []
    monkeypatch.setattr(probe_cli, "open_result_folder", opened.append)
    out_argv: list[str] = [] if no_out else ["--out", str(tmp_path / "p.json")]
    if tty:
        assert answers is not None
        monkeypatch.setattr(sys, "stdin", _FakeStdin(answers))
        monkeypatch.setattr("sys.argv", ["rigplane", "probe", *argv, *out_argv])
    else:
        monkeypatch.setattr("sys.argv", ["rigplane", "probe", *argv, *out_argv])
    import io

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert isinstance(exc_info.value.code, int)
    code: int = exc_info.value.code
    return code, buf.getvalue(), opened


def test_zero_arg_single_radio_confirm_with_enter(tmp_path, monkeypatch) -> None:
    code, out, opened = _run_probe(
        monkeypatch,
        tmp_path,
        [],
        results=[_cat()],
        answers=["", "y", "y"],
        tty=True,
    )
    assert code == 0
    assert "Found: FTX-1 on fake-cat" in out
    assert "Is this your radio?" in out
    document = ProbeFile.from_json((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert document.radio.identity_matches_profile is True
    assert opened == [tmp_path]  # --out names the file, so its folder opens


def test_zero_arg_single_radio_decline_sends_nothing(
    tmp_path, monkeypatch, capsys
) -> None:
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda *a: str(docs))
    _fake_discovery(monkeypatch, [_cat()])

    async def refuse_open(device: str, baudrate: int | None = None, **kwargs):
        raise AssertionError("must not open the port")

    monkeypatch.setattr(probe_cli, "open_serial_port", refuse_open)
    monkeypatch.setattr(sys, "stdin", _FakeStdin(["n"]))
    monkeypatch.setattr("sys.argv", ["rigplane", "probe"])
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "Found: FTX-1 on fake-cat" in capsys.readouterr().out
    assert not list(docs.glob("*.json"))
    assert not list(tmp_path.glob("FTX-1-radio-probe-*.json"))


def test_zero_arg_several_radios_numbered_choice(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(
        monkeypatch,
        tmp_path,
        [],
        results=[_civ(port="fake-civ"), _cat(port="fake-cat")],
        answers=["2", "y", "y"],
        tty=True,
    )
    assert code == 0
    assert "1. IC-7300 on fake-civ" in out
    assert "2. FTX-1 on fake-cat" in out
    document = ProbeFile.from_json((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert document.radio.profile == "FTX-1"


def test_zero_arg_several_radios_bad_number_then_quit(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(
        monkeypatch,
        tmp_path,
        [],
        results=[_civ(port="fake-civ"), _cat(port="fake-cat")],
        answers=["9", ""],
        tty=True,
    )
    assert code == 0
    assert "not one of the numbers" in out
    assert not (tmp_path / "p.json").exists()


def test_zero_arg_no_radios_checklist_exit_3(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(monkeypatch, tmp_path, [], results=[])
    assert code == 3
    assert "No radio found." in out
    assert "the USB cable is firmly connected" in out
    assert "rigplane probe <model> --port <device>" in out


def test_profile_without_port_filters_discovery(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(
        monkeypatch,
        tmp_path,
        ["FTX-1"],
        results=[_civ(port="fake-civ"), _cat(port="fake-cat")],
        answers=["", "y", "y"],
        tty=True,
    )
    assert code == 0
    assert "Found: FTX-1 on fake-cat" in out
    document = ProbeFile.from_json((tmp_path / "p.json").read_text(encoding="utf-8"))
    assert document.radio.profile == "FTX-1"


def test_port_without_profile_is_exit_2(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(
        monkeypatch, tmp_path, ["--port", "COM5"], results=[_cat()]
    )
    assert code == 2
    assert "name your radio model too" in out


def test_discovered_unsupported_protocol_is_exit_2(tmp_path, monkeypatch) -> None:
    tx500 = RadioDiscoveryResult(
        port="fake-kenwood",
        protocol="kenwood_cat",
        model="TX-500",
        profile_id="lab599_tx500",
        baudrate=38400,
        address="500",
    )
    code, out, _ = _run_probe(monkeypatch, tmp_path, [], results=[tx500])
    assert code == 2
    assert "not supported yet" in out


def test_yes_takes_single_radio_without_tty(tmp_path, monkeypatch) -> None:
    code, out, _ = _run_probe(
        monkeypatch, tmp_path, ["--yes"], results=[_cat()], answers=None, tty=False
    )
    assert code == 0
    assert "Found: FTX-1 on fake-cat" in out
    assert (tmp_path / "p.json").exists()


def test_several_radios_without_tty_is_exit_2_listing(tmp_path, monkeypatch) -> None:
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda *a: str(docs))
    _fake_discovery(monkeypatch, [_civ(port="fake-civ"), _cat(port="fake-cat")])

    async def refuse_open(device: str, baudrate: int | None = None, **kwargs):
        raise AssertionError("must not open the port")

    monkeypatch.setattr(probe_cli, "open_serial_port", refuse_open)
    monkeypatch.setattr(
        "sys.argv",
        ["rigplane", "probe", "--yes", "--out", str(tmp_path / "p.json")],
    )
    import io

    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 2
    out = buf.getvalue()
    assert "IC-7300" in out and "FTX-1" in out
    assert not (tmp_path / "p.json").exists()


# --- P1-2: successful real-CLI runs WITHOUT --out (the Documents default) ---


def test_default_save_cat_run_without_out(tmp_path, monkeypatch) -> None:
    # No --out: the dated Documents file is written, the full path is
    # printed, and the save folder is opened. The serial fake verifies
    # the selected device and a baud above the profile default.
    from datetime import datetime, timezone
    from functools import partial

    wire: list[bytes] = []
    code, out, opened = _run_probe(
        monkeypatch,
        tmp_path,
        [],
        results=[_cat(port="fake-cat-nbaud", baudrate=57600)],
        answers=["", "y", "y"],
        tty=True,
        no_out=True,
        opener=partial(
            _checked_cat_openers, device="fake-cat-nbaud", baud=57600
        ),
        wire=wire,
    )
    assert code == 0
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    matches = list((tmp_path / "Documents").glob(f"FTX-1-radio-probe-{stamp}.json"))
    assert len(matches) == 1
    saved = matches[0]
    closing = [line for line in out.splitlines() if "Saved to" in line]
    assert len(closing) == 1
    assert str(saved.resolve()) in closing[0]
    assert opened == [saved.resolve().parent]
    assert wire, "the probe must send on the selected port"
    assert all(not frame.startswith(b"\xfe\xfe") for frame in wire)
    document = ProbeFile.from_json(saved.read_text(encoding="utf-8"))
    assert document.radio.profile == "FTX-1"
    assert document.radio.identity_reported.parsed == "0840"


def test_default_save_civ_run_without_out_uses_suffix(tmp_path, monkeypatch) -> None:
    # No --out with the base name taken: both captures survive, the
    # closing line names the real (-2) path, and every CI-V frame on the
    # wire carries the discovered (non-profile) destination address.
    from datetime import datetime, timezone
    from functools import partial

    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    first = docs / f"IC-7300-radio-probe-{stamp}.json"
    first.write_text('{"other": "run"}', encoding="utf-8")
    wire: list[bytes] = []
    code, out, opened = _run_probe(
        monkeypatch,
        tmp_path,
        [],
        results=[_civ(port="fake-civ-naddr", baudrate=19200, address=0x96)],
        answers=["", "y", "y"],
        tty=True,
        no_out=True,
        opener=partial(
            _checked_civ_openers,
            device="fake-civ-naddr",
            baud=19200,
            addr=0x96,
        ),
        wire=wire,
    )
    assert code == 0
    assert first.read_text(encoding="utf-8") == '{"other": "run"}'
    second = docs / f"IC-7300-radio-probe-{stamp}-2.json"
    document = ProbeFile.from_json(second.read_text(encoding="utf-8"))
    assert document.radio.profile == "IC-7300"
    closing = [line for line in out.splitlines() if "Saved to" in line]
    assert len(closing) == 1
    assert str(second.resolve()) in closing[0]
    assert opened == [second.resolve().parent]
    assert wire, "the probe must send on the selected port"
    for frame in wire:
        assert frame[:3] == b"\xfe\xfe\x96"


def test_probe_main_discovery_oserror_is_exit_3(tmp_path, monkeypatch, capsys) -> None:
    # P2-1: a USB device disappearing mid-scan is exit 3 with one plain
    # sentence through the real entry point, not a traceback, and no
    # capture file is written.
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda *a: str(docs))

    async def boom():
        raise OSError("device vanished mid-scan")

    monkeypatch.setattr(probe_cli, "discover_serial_radios", boom)
    code = cli_module.probe_main([])
    assert code == 3
    captured = capsys.readouterr()
    assert "Could not scan the USB ports" in captured.out
    assert "Traceback" not in captured.out + captured.err
    assert list(tmp_path.rglob("*.json")) == []
