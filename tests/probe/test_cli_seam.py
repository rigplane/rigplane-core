"""``rigplane probe`` through the real CLI parser and dispatch (MOR-4464 finding 7).

Every test starts from the real argument parser
(``rigplane.cli._build_parser``) and reaches the production dispatch
target, faking only the serial boundary: the CI-V fake sits behind the
serial opener, the CAT fake behind the production ``socket://`` open.
Removing the ``probe`` parser registration or the dispatch branch in
``main`` must make these tests fail.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

import rigplane.cli as cli_module
import rigplane.cli._probe as probe_cli
from rigplane.cli import _build_parser, main
from rigplane.cli._probe import _any_answer
from rigplane.probe.model import (
    IdentityReport,
    ProbeFile,
    RadioInfo,
    Snapshot,
    TerminationInfo,
    ToolInfo,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def _parse_probe_argv(*argv: str):
    args = _build_parser().parse_args(["probe", *argv])
    assert args.command == "probe"
    return args


def test_parser_registers_probe_subcommand(tmp_path) -> None:
    out = tmp_path / "probe.json"
    args = _parse_probe_argv(
        "FTX-1", "--port", "socket://127.0.0.1:1", "--yes", "--out", str(out)
    )
    assert args.profile == "FTX-1"
    assert args.port == "socket://127.0.0.1:1"
    assert args.yes is True
    assert args.out == str(out)


def test_main_dispatches_probe_to_the_probe_runner(monkeypatch) -> None:
    # Removing the ``elif args.command == "probe"`` branch in ``main``
    # must break this: the stub below is reached only through it.
    calls: list = []

    def stub(args) -> int:
        calls.append(args)
        return 0

    monkeypatch.setattr(cli_module, "_run_probe", stub)
    monkeypatch.setattr(
        "sys.argv",
        ["rigplane", "probe", "FTX-1", "--port", "socket://127.0.0.1:1", "--yes"],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert len(calls) == 1
    assert calls[0].command == "probe"
    assert calls[0].profile == "FTX-1"


async def test_end_to_end_cat_ftx1_through_real_cli(tmp_path, monkeypatch) -> None:
    import asyncio

    from rigplane_testkit.yaesu import FakeCatRadio

    out = tmp_path / "ftx1-probe.json"
    radio = FakeCatRadio(profile="ftx1", frequency_hz=7_074_000)
    await radio.start()
    try:
        monkeypatch.setattr(
            "sys.argv",
            [
                "rigplane",
                "probe",
                "FTX-1",
                "--port",
                radio.device_url,
                "--yes",
                "--out",
                str(out),
                "--label",
                "seam-cat",
            ],
        )
        # The fake's socket server is bound to this test's event loop,
        # while main() runs its own asyncio.run: execute main() in a
        # worker thread so the server loop keeps answering.
        with pytest.raises(SystemExit) as exc_info:
            await asyncio.to_thread(main)
    finally:
        await radio.stop()
    assert exc_info.value.code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.schema == "rigplane-probe-v1"
    assert document.label == "seam-cat"
    assert document.radio.identity_matches_profile is True
    assert document.radio.identity_reported.parsed == "0840"
    assert document.commands
    assert radio.frequency_hz == 7_074_000


def test_end_to_end_civ_ic7300_through_real_cli(tmp_path, monkeypatch) -> None:
    import rigplane.cli._probe as probe_cli
    from rigplane.core.serial_open import open_serial_port as real_open
    from rigplane_testkit.serial import CivSerialFake

    out = tmp_path / "ic7300-probe.json"
    fake = CivSerialFake(model="IC-7300", echo=False)

    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        return await real_open(device, baudrate=baudrate, opener=fake.opener)

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)
    # The CI-V path must not open any network connection: the opener is
    # faked and every other step is local files and prints.
    import socket

    def _refuse_network(*args, **kwargs):
        raise AssertionError("probe must not open network connections here")

    monkeypatch.setattr(socket, "create_connection", _refuse_network)
    monkeypatch.setattr(
        "sys.argv",
        [
            "rigplane",
            "probe",
            "IC-7300",
            "--port",
            "fake-usb-serial",
            "--yes",
            "--out",
            str(out),
            "--label",
            "seam-civ",
        ],
    )
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.schema == "rigplane-probe-v1"
    assert document.label == "seam-civ"
    assert document.radio.identity_matches_profile is True
    assert document.commands


# --- MOR-4464 gate P1: an answered identity must survive later silence ---
#
# The serial boundary below is faked the same way as above (the opener is
# replaced, everything above it — parser, dispatch, production exchange,
# runner — is real). ``_ScriptedSerial`` answers per request body so the
# gate's exact sequence can be replayed: silence raises ``TimeoutError``
# at once instead of waiting out the window, which the production
# exchange maps to an unanswered read either way.


class _ScriptedSerial:
    """One scripted reply per request body; ``None`` means silence."""

    def __init__(
        self,
        answers: dict[bytes, bytes | None],
        seen: list[bytes],
        *,
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


def _serial_opener(pair: _ScriptedSerial):
    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        return pair, pair

    return fake_open


def _seam_argv(out: Path, label: str) -> list[str]:
    return [
        "rigplane",
        "probe",
        "FTX-1",
        "--port",
        "fake-usb-serial",
        "--yes",
        "--out",
        str(out),
        "--label",
        label,
    ]


def test_answered_identity_then_silent_snapshot_still_reaches_main_pass(
    tmp_path, monkeypatch, capsys
) -> None:
    # The gate's sequence: ID; answers ID0840;, the five snapshot reads
    # stay silent, later reads such as FB; answer. The main pass must run
    # and the CLI must exit 0 with no "did not answer" message.
    out = tmp_path / "ftx1-probe.json"
    seen: list[bytes] = []
    silent = {b"IF;", b"FA;", b"MD0;", b"ST;", b"TX;"}
    pair = _ScriptedSerial(
        {b"ID;": b"ID0840;", **{body: None for body in silent}}, seen
    )
    monkeypatch.setattr(probe_cli, "open_serial_port", _serial_opener(pair))
    monkeypatch.setattr("sys.argv", _seam_argv(out, "gate-p1-a"))
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "did not answer any question" not in capsys.readouterr().out
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.radio.identity_reported.raw == "ID0840;"
    assert document.radio.identity_reported.parsed == "0840"
    assert document.radio.identity_matches_profile is True
    assert document.commands
    assert document.termination.status == "completed"
    assert b"FB;" in seen


def test_answered_identity_then_total_silence_stops_each_phase(
    tmp_path, monkeypatch, capsys
) -> None:
    # ID; answers, everything else stays silent: the before snapshot ends
    # after its five silent reads and the main pass stops after five more.
    # The CLI still exits 0 — the identity answered — with no message.
    out = tmp_path / "ftx1-probe.json"
    seen: list[bytes] = []
    pair = _ScriptedSerial({b"ID;": b"ID0840;"}, seen, default=None)
    monkeypatch.setattr(probe_cli, "open_serial_port", _serial_opener(pair))
    monkeypatch.setattr("sys.argv", _seam_argv(out, "gate-p1-b"))
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "did not answer any question" not in capsys.readouterr().out
    document = ProbeFile.from_json(out.read_text(encoding="utf-8"))
    assert document.radio.identity_reported.raw == "ID0840;"
    assert document.termination.status == "silence_stop"
    assert [row.outcome for row in document.commands] == ["silent"] * 5
    assert len(seen) == 1 + 5 + 5


def _file_for_any_answer(
    *,
    identity_raw: str = "",
    snapshot_before: dict[str, str | None] | None = None,
) -> ProbeFile:
    return ProbeFile(
        tool=ToolInfo(
            rigplane_version="test", python="test", os="test", created_utc="test"
        ),
        mode="read",
        radio=RadioInfo(
            profile="FTX-1",
            profile_sha256="0" * 64,
            protocol="yaesu_cat",
            port_kind="usb",
            baud=38400,
            identity_reported=IdentityReport(
                raw=identity_raw, parsed="0840" if identity_raw else ""
            ),
            identity_matches_profile=bool(identity_raw),
        ),
        private_reads_included=False,
        label="",
        snapshot=Snapshot(before=dict(snapshot_before or {}), after={}, changed=[]),
        termination=TerminationInfo(status="completed"),
    )


def test_any_answer_counts_identity_only() -> None:
    assert _any_answer(_file_for_any_answer(identity_raw="ID0840;")) is True


def test_any_answer_counts_snapshot_only() -> None:
    assert (
        _any_answer(_file_for_any_answer(snapshot_before={"get_freq": "FA14074000;"}))
        is True
    )


def test_any_answer_rejects_total_silence() -> None:
    assert _any_answer(_file_for_any_answer()) is False
