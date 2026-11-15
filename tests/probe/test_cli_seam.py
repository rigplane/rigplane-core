"""``rigplane probe`` through the real CLI parser and dispatch (MOR-4464 finding 7).

Every test starts from the real argument parser
(``rigplane.cli._build_parser``) and reaches the production dispatch
target, faking only the serial boundary: the CI-V fake sits behind the
serial opener, the CAT fake behind the production ``socket://`` open.
Removing the ``probe`` parser registration or the dispatch branch in
``main`` must make these tests fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import rigplane.cli as cli_module
from rigplane.cli import _build_parser, main
from rigplane.probe.model import ProbeFile

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
        with pytest.raises(SystemExit) as exc_info:
            main()
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


async def test_end_to_end_civ_ic7300_through_real_cli(tmp_path, monkeypatch) -> None:
    import rigplane.cli._probe as probe_cli
    from rigplane.core.serial_open import open_serial_port as real_open
    from rigplane_testkit.serial import CivSerialFake

    out = tmp_path / "ic7300-probe.json"
    fake = CivSerialFake(model="IC-7300", echo=False)

    async def fake_open(device: str, baudrate: int | None = None, **kwargs):
        return await real_open(device, baudrate=baudrate, opener=fake.opener)

    monkeypatch.setattr(probe_cli, "open_serial_port", fake_open)
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
