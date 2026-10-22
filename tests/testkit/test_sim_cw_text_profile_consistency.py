"""MOR-3678 (#4317 gate review) end to end: profile -> WebServer -> radio.

Two real stacks, each over real HTTP on an ephemeral loopback port:

* A Yaesu profile with no ``[cw_text]`` section (rigs/ftx1.toml with the
  section removed), loaded by ``load_rig`` and served by the production
  ``YaesuCatRadio`` over the simulated FTX-1 socket (``serve --radio
  ftx1``, ``socket://127.0.0.1``). It publishes ``supported: false`` and
  ``POST /api/v1/radio/cw/send`` answers 409; nothing reaches the radio.
* The production ``Ic7300SerialRadio`` (rigs/ic7300.toml, no section)
  against ``CivSerialFake`` through the documented ``SerialCivLink``
  opener seam (no OS port is opened). It publishes ``supported: true``
  and the same POST answers 200 with the text arriving as a 0x17 frame.

No serial port, no real radio.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer

from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.sim.__main__ import build_parser
from rigplane_testkit.sim.serve import build_stack
from testkit.test_sim_ftx1_send_cw_text import _request, _settle

ROOT = Path(__file__).resolve().parents[2]
_CW_TEXT_SECTION = re.compile(r"^\[cw_text\]\n(?:(?!\[).*\n)*", re.MULTILINE)
_SEND_CW = 0x17  # IC-7300 Full Manual §19: 17 = send CW message


@pytest.mark.asyncio
async def test_yaesu_profile_without_section_publishes_and_refuses_the_same(
    tmp_path: Path,
) -> None:
    text = (ROOT / "rigs" / "ftx1.toml").read_text(encoding="utf-8")
    assert _CW_TEXT_SECTION.search(text)
    for shared in (ROOT / "rigs").glob("_*.toml"):
        (tmp_path / shared.name).write_bytes(shared.read_bytes())
    path = tmp_path / "ftx1_no_cw_text.toml"
    path.write_text(_CW_TEXT_SECTION.sub("", text), encoding="utf-8")
    rig = load_rig(path)

    stack = build_stack(build_parser().parse_args(["serve", "--radio", "ftx1"]))
    await stack.backend.start()
    radio = server = None
    try:
        device = stack.backend.startup_fields()["device"]
        assert device.startswith("socket://127.0.0.1:"), device
        radio = YaesuCatRadio(device, profile=rig)
        await asyncio.wait_for(radio.connect(), 20)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        expected = {"supported": False, "maxLength": 512, "memoryChannel": None}

        status, caps = await _request(server.port, "GET", "/api/v1/capabilities")
        assert status == 200
        assert caps["cwText"] == expected
        status, info = await _request(server.port, "GET", "/api/v1/info")
        assert status == 200
        assert info["capabilities"]["cwText"] == expected

        status, body = await _request(
            server.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (
            409,
            {
                "error": "unsupported_command",
                "message": "FTX-1 does not support sending CW text yet",
            },
        )
        await _settle()
        model = stack.backend.radio.model
        assert model.keying_memory == {}
        assert model.keyer_play_requests == ()
    finally:
        if server is not None:
            await server.stop()
        if radio is not None:
            await radio.disconnect()
        await stack.backend.stop()
        stack.trace.close()


class _AckSendCw:
    """Answer table for the fake: ACK each 0x17 frame and keep its text."""

    echo = True

    def __init__(self, civ_addr: int) -> None:
        self._civ_addr = civ_addr
        self.texts: list[bytes] = []

    def answer(self, frame: bytes) -> bytes | None:
        parsed = parse_civ_frame(frame)
        if parsed.command != _SEND_CW:
            return None
        self.texts.append(bytes(frame[5:-1]))
        return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, 0xFB)


@pytest.mark.asyncio
async def test_icom_profile_publishes_support_and_sends() -> None:
    table = _AckSendCw(civ_addr=0x94)
    fake = CivSerialFake(recorded_answers=table)  # type: ignore[arg-type]
    link = SerialCivLink(
        device="civ-serial-fake", open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device="civ-serial-fake", civ_link=link, _enumerate_serial_ports_fn=lambda: []
    )
    server = None
    try:
        await asyncio.wait_for(radio.connect(), 20)
        assert radio.connected
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        expected = {"supported": True, "maxLength": 512, "memoryChannel": None}

        status, caps = await _request(server.port, "GET", "/api/v1/capabilities")
        assert status == 200
        assert caps["cwText"] == expected
        status, info = await _request(server.port, "GET", "/api/v1/info")
        assert status == 200
        assert info["capabilities"]["cwText"] == expected

        status, body = await _request(
            server.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (200, {"text": "CQ"})
        await _settle()
        assert table.texts == [b"CQ"]
        assert fake.ptt is False
    finally:
        if server is not None:
            await server.stop()
        await radio.disconnect()
