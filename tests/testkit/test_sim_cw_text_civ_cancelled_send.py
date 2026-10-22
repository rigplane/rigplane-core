"""MOR-3700: a CI-V CW text send a transmit abort ends answers with a refusal.

The CI-V twin of the FTX-1 pending-send cases in
``test_sim_cw_text_entry_points.py``. ``rigplane web``'s production
composition (``ManagedTxComposition``, ``install_managed_tx_composition``,
``_ManagedTxRadioSession``, the server's managed-TX wiring) around the
production ``Ic7300SerialRadio``; only the serial port is faked
(``CivSerialFake``). The fake ACKs the first CI-V 0x17 frame of a two-frame
send and then goes quiet, leaving the second unanswered, so the send is mid-way when a
managed force-off arrives over HTTP. The send's caller (HTTP request or
WebSocket command) used to get no answer at all (the force-off cancelled
the request with the fenced work); it now gets ``command_cancelled``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.cli import _ManagedTxRadioSession
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.rig_loader import load_rig
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)

from rigplane_testkit.serial import CivSerialFake
from testkit.test_sim_cw_text_civ_long_text import _rigs_dir
from testkit.test_sim_cw_text_entry_points import _send_outcome, _ws_send_outcome
from testkit.test_sim_ftx1_send_cw_text import _request

_SEND_CW = 0x17
_QUIET_S = 1.0
# Two CI-V frames (30 characters each): the first is ACKed, the second held.
TEXT = "A" * 30 + "B" * 30
ABORTED = "CW text send to IC-7300 was cancelled by a transmit abort"


class _HoldSecondFrame:
    """Answer table: ACK the first 0x17 text frame, then the radio goes quiet.

    Fake hardware only. After that ACK the fake answers nothing for a short
    window (``CivSerialFake.silence``), so the send's second frame waits for
    an ACK that does not come.
    """

    echo = True

    def __init__(self, civ_addr: int) -> None:
        self._civ_addr = civ_addr
        self.fake: CivSerialFake | None = None
        self.payloads: list[bytes] = []
        self.holding = asyncio.Event()

    def answer(self, frame: bytes) -> bytes | None:
        parsed = parse_civ_frame(frame)
        if parsed.command != _SEND_CW:
            return None
        self.payloads.append(bytes(frame[5:-1]))
        if len(self.payloads) == 1:
            assert self.fake is not None
            self.fake.silence(_QUIET_S)
            self.holding.set()
        return build_civ_frame(CONTROLLER_ADDR, self._civ_addr, 0xFB)


@asynccontextmanager
async def _managed_civ_web(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    """The composition order of ``rigplane web`` around an IC-7300."""
    profile = load_rig(_rigs_dir(tmp_path) / "ic7300.toml").to_profile()
    table = _HoldSecondFrame(civ_addr=0x94)
    fake = CivSerialFake(recorded_answers=table)  # type: ignore[arg-type]
    table.fake = fake
    link = SerialCivLink(
        device="civ-serial-fake", open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device="civ-serial-fake",
        civ_link=link,
        profile=profile,
        _enumerate_serial_ports_fn=lambda: [],
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)
    async with _ManagedTxRadioSession(radio, composition, None):
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        prepare_managed_tx_observation_generation(server)
        await composition.bind_state_store(server.command_state_store)
        attach_managed_tx_composition(server, composition)
        await server.start()
        try:
            yield SimpleNamespace(port=server.port, table=table, fake=fake)
        finally:
            await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["http", "websocket"])
async def test_force_off_mid_send_answers_command_cancelled(
    tmp_path: Path, entry: str
) -> None:
    async with _managed_civ_web(tmp_path) as s:
        outcome = _send_outcome if entry == "http" else _ws_send_outcome
        sending = asyncio.create_task(outcome(s.port, TEXT))
        await asyncio.wait_for(s.table.holding.wait(), 5)
        # The second frame goes out and waits for its ACK.
        await asyncio.sleep(0.1)
        assert not sending.done()
        status, body = await _request(
            s.port,
            "POST",
            "/api/v1/managed-transmit/command",
            {"operation": "force_off"},
        )
        assert status == 202, body
        result = await asyncio.wait_for(sending, 15)
        if entry == "http":
            assert result == (
                409,
                {"error": "command_cancelled", "message": ABORTED},
            )
        else:
            assert result == {
                "type": "response",
                "id": "cw-1",
                "ok": False,
                "error": "command_cancelled",
                "message": ABORTED,
            }
        # Only the first frame was answered; the send never completed.
        assert s.table.payloads == [b"A" * 30]
        assert s.fake.ptt is False
