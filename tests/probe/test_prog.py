"""Probe display name (MOR-4464 A2b): hints show the command the user ran.

Through the real entry points: ``rigplane probe`` keeps the default form,
while ``rigplane.cli.probe_main`` (the Pro sidecar hand-over) renders every
hint with the caller's ``prog`` and never with ``rigplane probe``.
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path

import pytest

import platformdirs
import rigplane.cli as cli_module
import rigplane.cli._probe as probe_cli
from rigplane.backends.discovery import RadioDiscoveryResult
from rigplane.cli import main, probe_main
from rigplane.probe import render
from rigplane.probe.render import DEFAULT_PROBE_PROG

PROG_WIN = (
    '"C:\\Program Files\\RigPlane Pro\\rigplane-pro\\rigplane-pro.exe" radio-probe'
)
PROG_SHORT = "RigPlanePro radio-probe"


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

    def __init__(self, answers: dict[bytes, bytes | None], seen: list[bytes]) -> None:
        self._answers = answers
        self._seen = seen
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
        reply = self._answers.get(self._last, b"?;")
        if reply is None:
            raise asyncio.TimeoutError()
        return reply


def _fake_openers(monkeypatch: pytest.MonkeyPatch, seen: list[bytes]) -> None:
    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        pair = _ScriptedSerial({b"ID;": b"ID0840;"}, seen)
        return pair, pair

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)


def _fake_discovery(
    monkeypatch: pytest.MonkeyPatch, results: list[RadioDiscoveryResult]
) -> None:
    async def fake_discover():
        return results

    monkeypatch.setattr(probe_cli, "discover_serial_radios", fake_discover)


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    argv: list[str],
    results: list[RadioDiscoveryResult],
) -> tuple[int, str]:
    """Run ``rigplane probe`` through ``main()``; return (exit, output)."""
    docs = tmp_path / "Documents"
    docs.mkdir(exist_ok=True)
    monkeypatch.setattr(platformdirs, "user_documents_dir", lambda *a: str(docs))
    monkeypatch.setattr(cli_module, "_run_probe", probe_cli.run)
    _fake_discovery(monkeypatch, results)
    seen: list[bytes] = []
    _fake_openers(monkeypatch, seen)
    monkeypatch.setattr(
        "sys.argv", ["rigplane", "probe", *argv, "--out", str(tmp_path / "p.json")]
    )
    buf = io.StringIO()
    monkeypatch.setattr(sys, "stdout", buf)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert isinstance(exc_info.value.code, int)
    return exc_info.value.code, buf.getvalue()


def test_default_prog_constant() -> None:
    assert DEFAULT_PROBE_PROG == "rigplane probe"


def test_render_spells_default_in_one_place_only() -> None:
    src = Path(render.__file__).read_text(encoding="utf-8")
    hits = [line for line in src.splitlines() if "rigplane probe" in line]
    assert len(hits) == 1, hits
    assert "DEFAULT_PROBE_PROG" in hits[0], hits


def test_default_no_radio_checklist_names_default_command(
    tmp_path, monkeypatch
) -> None:
    code, out = _run_main(monkeypatch, tmp_path, [], [])
    assert code == 3
    assert "rigplane probe <model> --port <device>" in out


def test_default_port_alone_names_default_command(tmp_path, monkeypatch) -> None:
    code, out = _run_main(monkeypatch, tmp_path, ["--port", "COM5"], [_cat()])
    assert code == 2
    assert "rigplane probe" in out


def test_default_single_no_tty_names_default_command(
    tmp_path, monkeypatch
) -> None:
    code, out = _run_main(monkeypatch, tmp_path, [], [_cat()])
    assert code == 2
    assert "rigplane probe FTX-1 --port fake-cat" in out


def test_default_several_no_tty_names_default_command(
    tmp_path, monkeypatch
) -> None:
    code, out = _run_main(
        monkeypatch, tmp_path, ["--yes"], [_civ(), _cat()]
    )
    assert code == 2
    assert "rigplane probe <model> --port <device>" in out


@pytest.mark.parametrize("prog", [PROG_WIN, PROG_SHORT])
def test_custom_prog_no_radio_checklist(
    tmp_path, monkeypatch, capsys, prog: str
) -> None:
    _fake_discovery(monkeypatch, [])
    code = probe_main([], prog=prog)
    assert code == 3
    out = capsys.readouterr().out
    assert f"{prog} <model> --port <device>" in out
    assert "rigplane probe" not in out


@pytest.mark.parametrize("prog", [PROG_WIN, PROG_SHORT])
def test_custom_prog_port_alone(tmp_path, monkeypatch, capsys, prog: str) -> None:
    _fake_discovery(monkeypatch, [_cat()])
    code = probe_main(["--port", "COM5"], prog=prog)
    assert code == 2
    out = capsys.readouterr().out
    assert prog in out
    assert "rigplane probe" not in out


@pytest.mark.parametrize("prog", [PROG_WIN, PROG_SHORT])
def test_custom_prog_single_no_tty(
    tmp_path, monkeypatch, capsys, prog: str
) -> None:
    _fake_discovery(monkeypatch, [_cat()])
    code = probe_main([], prog=prog)
    assert code == 2
    out = capsys.readouterr().out
    assert f"{prog} FTX-1 --port fake-cat" in out
    assert "rigplane probe" not in out


@pytest.mark.parametrize("prog", [PROG_WIN, PROG_SHORT])
def test_custom_prog_several_no_tty(
    tmp_path, monkeypatch, capsys, prog: str
) -> None:
    _fake_discovery(monkeypatch, [_civ(), _cat()])
    code = probe_main(["--yes"], prog=prog)
    assert code == 2
    out = capsys.readouterr().out
    assert f"{prog} <model> --port <device>" in out
    assert "rigplane probe" not in out


def test_custom_prog_help_usage(tmp_path, monkeypatch, capsys) -> None:
    code = probe_main(["--help"], prog=PROG_WIN)
    assert code == 0
    out = capsys.readouterr().out
    assert PROG_WIN in out
    assert "rigplane probe" not in out


def test_probe_main_returns_int_never_raises(
    tmp_path, monkeypatch, capsys
) -> None:
    _fake_discovery(monkeypatch, [])
    assert probe_main(["--port", "COM5"], prog=PROG_WIN) == 2
    assert probe_main([], prog=PROG_WIN) == 3
    # argparse itself raises SystemExit(2) on a bad option: caught, returned.
    assert probe_main(["--no-such-option"], prog=PROG_WIN) == 2
    assert probe_main(["--help"], prog=PROG_WIN) == 0


def test_probe_main_success_returns_zero(tmp_path, monkeypatch, capsys) -> None:
    _fake_discovery(monkeypatch, [_cat()])
    seen: list[bytes] = []
    _fake_openers(monkeypatch, seen)
    out = tmp_path / "p.json"
    code = probe_main(["--yes", "--out", str(out)], prog=PROG_WIN)
    assert code == 0
    assert seen
    assert out.exists()


def test_prog_is_exported_with_exact_signature() -> None:
    import inspect

    assert "probe_main" in cli_module.__all__
    sig = inspect.signature(cli_module.probe_main)
    params = list(sig.parameters.values())
    assert params[0].name == "argv"
    assert params[1].name == "prog"
    assert params[1].kind is inspect.Parameter.KEYWORD_ONLY
    assert params[1].default == DEFAULT_PROBE_PROG
    assert sig.return_annotation is int


def test_customer_strings_ascii_with_custom_prog() -> None:
    prog = PROG_SHORT
    texts = [
        render.probe_help(prog),
        render.no_radio_found_message(None, prog),
        render.no_radio_found_message("FT-991A", prog),
        render.port_without_profile_message(prog),
        render.single_no_tty_message("FT-991A", "COM5", prog),
        render.several_no_tty_message(prog),
    ]
    for text in texts:
        assert text.isascii(), repr(text)
    assert f"{prog} <model> --port <device>" in texts[1]
