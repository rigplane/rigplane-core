"""MOR-3678 (#4317 gate review): a CI-V profile's own limit above 512 is honoured.

The old handler capped CW text at 512 characters. With the limit now taken
only from the profile, a CI-V profile loaded from a TOML file with
``[cw_text] max_length = 600`` must send 513 characters, whole and in
order, through every user entry point:

* HTTP: ``POST /api/v1/radio/cw/send`` on a started ``WebServer``.
* WebSocket: a ``send_cw_text`` ``cmd`` frame on that server.
* CLI: real argv (``--backend serial --model IC-7300 ... cw TEXT``) through
  ``_build_parser`` / ``_run`` and ``create_radio``.

The radio is the production ``Ic7300SerialRadio``; only the serial port is
faked (``CivSerialFake``, which ACKs each CI-V 0x17 frame and keeps its
payload). The payloads are reassembled to prove nothing was cut or reordered.
"""

from __future__ import annotations

import asyncio
import string
from pathlib import Path

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.cli import _build_parser, _finalize_ptt_args, _run
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer

from rigplane_testkit.serial import CivSerialFake
from testkit.test_sim_cw_text_entry_points import _ws_command
from testkit.test_sim_ftx1_send_cw_text import _request, _settle

ROOT = Path(__file__).resolve().parents[2]
_SEND_CW = 0x17
_CIV_TEXT_PER_FRAME = 30
# 513 characters, no repeat within a frame, so a dropped, duplicated or
# reordered frame changes the reassembled text.
_ALPHABET = string.ascii_uppercase + string.digits
TEXT = "".join(_ALPHABET[i % len(_ALPHABET)] for i in range(513))


class _AckSendCw:
    """Answer table for the fake: ACK each 0x17 frame and keep its payload."""

    echo = True

    def __init__(self, civ_addr: int) -> None:
        self._civ_addr = civ_addr
        self.payloads: list[bytes] = []

    def answer(self, frame: bytes) -> bytes | None:
        parsed = parse_civ_frame(frame)
        if parsed.command != _SEND_CW:
            return None
        self.payloads.append(bytes(frame[5:-1]))
        return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, 0xFB)


def _rigs_dir(tmp_path: Path) -> Path:
    """A rigs directory whose ic7300.toml allows 600 characters of CW text."""
    text = (ROOT / "rigs" / "ic7300.toml").read_text(encoding="utf-8")
    assert "[cw_text]" not in text
    rigs = tmp_path / "rigs"
    rigs.mkdir()
    for shared in (ROOT / "rigs").glob("_*.toml"):
        (rigs / shared.name).write_bytes(shared.read_bytes())
    (rigs / "ic7300.toml").write_text(
        text + "\n[cw_text]\nmax_length = 600\n", encoding="utf-8"
    )
    return rigs


def _assert_whole_text(table: _AckSendCw) -> None:
    assert len(table.payloads) == -(-len(TEXT) // _CIV_TEXT_PER_FRAME)
    assert all(len(p) <= _CIV_TEXT_PER_FRAME for p in table.payloads)
    assert b"".join(table.payloads).decode("ascii") == TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["http", "websocket"])
async def test_civ_profile_limit_above_512_sends_513_characters(
    tmp_path: Path, entry: str
) -> None:
    profile = load_rig(_rigs_dir(tmp_path) / "ic7300.toml").to_profile()
    assert profile.cw_text.max_length == 600
    table = _AckSendCw(civ_addr=0x94)
    fake = CivSerialFake(recorded_answers=table)  # type: ignore[arg-type]
    link = SerialCivLink(
        device="civ-serial-fake", open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device="civ-serial-fake",
        civ_link=link,
        profile=profile,
        _enumerate_serial_ports_fn=lambda: [],
    )
    server = None
    try:
        await asyncio.wait_for(radio.connect(), 20)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        status, caps = await _request(server.port, "GET", "/api/v1/capabilities")
        assert status == 200
        assert caps["cwText"]["maxLength"] == 600
        if entry == "http":
            status, body = await _request(
                server.port, "POST", "/api/v1/radio/cw/send", {"text": TEXT}
            )
            assert (status, body) == (200, {"text": TEXT})
        else:
            response = await _ws_command(server.port, "send_cw_text", {"text": TEXT})
            assert response["ok"] is True, response
        await _settle()
        _assert_whole_text(table)
        assert fake.ptt is False
    finally:
        if server is not None:
            await server.stop()
        await radio.disconnect()


@pytest.mark.asyncio
async def test_cli_civ_profile_limit_above_512_sends_513_characters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rigs = _rigs_dir(tmp_path)
    table = _AckSendCw(civ_addr=0x94)
    fake = CivSerialFake(recorded_answers=table)  # type: ignore[arg-type]

    async def _open_fake_port(**_kwargs: object) -> tuple[object, object]:
        return await fake.open_serial_connection()

    # Only the serial port is faked: the opener, and port enumeration (which
    # would list this machine's USB devices).
    monkeypatch.setattr(
        "rigplane.backends.icom7610.drivers.serial_civ_link.open_serial_port",
        _open_fake_port,
    )
    monkeypatch.setattr(
        "rigplane.backends._icom_serial_base._IcomSerialRadioBase."
        "_default_enumerate_serial_ports",
        lambda _self: [],
    )
    # The profile registry and --model resolution read this rigs directory
    # (the source-checkout layout: no packaged rigs).
    monkeypatch.setattr("rigplane.profiles._RIG_DIRS", [rigs])
    for name in ("_profiles", "_by_normalized", "_by_id", "_by_civ_addr"):
        monkeypatch.setattr(f"rigplane.profiles.{name}", None)
    monkeypatch.setattr("rigplane.cli._rigs_dir", lambda: rigs)
    monkeypatch.setattr(
        "rigplane.profiles.rig_loader._discover_resource_rigs", lambda _root: {}
    )
    monkeypatch.delenv("ICOM_SERIAL_DEVICE", raising=False)

    parser = _build_parser()
    args = parser.parse_args(
        [
            "--backend",
            "serial",
            "--model",
            "IC-7300",
            "--serial-port",
            str(tmp_path / "civ-serial-fake"),
            "cw",
            TEXT,
        ]
    )
    _finalize_ptt_args(parser, args)
    rc = await _run(args)
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert captured.out.strip().splitlines()[-1] == f"CW: {TEXT}"
    _assert_whole_text(table)
    assert fake.ptt is False
