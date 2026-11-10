"""MOR-4352 fix round 1: the changed seats through their real entrypoints.

Local Codex review round 1 (PR rigplane/rigplane-core#4549) found the
whole-watt write quantum wired into the web seat only:

* rigctld ``L RFPOWER`` wrote the quantized raw byte (the executor's
  ``resolve_power_level_target`` call) but still recorded the unquantized
  ``round(value * 255)`` as the pending readback, so a same-session
  ``l RFPOWER`` answered the stale normalized level for the pending
  window instead of the admitted whole-watt readback;
* the synchronous SDK wrapper forwarded ``power_max_watts`` without the
  native unit, making the resolver guess a watts-native radio;
* neither the TCI nor the rigctld seat had an entrypoint regression
  pinning the wire byte.

The regressions here drive the REAL listeners — the TCI WebSocket
adapter server and the rigctld TCP server — over the loaded typed G7250
profile, with only the serial hardware faked: the whole-watt register
model of ``testkit/test_g7250_session_wire.py`` (a write of raw ``r``
stores ``floor(r * 100 / 255)`` watts; a read answers
``floor(W * 255 / 100)``).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from rigplane.backends.factory import create_radio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane_testkit.icom import _level_bcd_encode

from testkit.test_g7250_session_wire import (
    _ANSWER_WINDOW_S,
    _DEVICE,
    _G7250QuantizedPowerFake,
    _mount,
)


async def _g7250_radio(
    monkeypatch: pytest.MonkeyPatch, fake: _G7250QuantizedPowerFake
) -> Any:
    """The real CLI-built G7250 session over the quantizing serial fake."""
    opens: list[tuple[str, int]] = []
    _mount(monkeypatch, fake, opens)
    radio = create_radio(
        await _build_backend_config(
            _build_parser().parse_args(
                ["--model", "G7250", "--serial-port", _DEVICE, "status"]
            )
        )
    )
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    await asyncio.wait_for(radio.connect(), 30)
    return radio


def _rf_power_set_payloads(fake: _G7250QuantizedPowerFake) -> list[bytes]:
    """The BCD payloads of every ``14 0A`` SET frame the fake received."""
    return [
        frame.data
        for frame in fake.commands
        if frame.command == 0x14 and frame.sub == 0x0A and frame.data
    ]


# ---------------------------------------------------------------------------
# TCI: drive over the real WebSocket entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tci_drive_write_quantizes_the_wire_byte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``drive:0,11;`` writes raw 29 (11 W), never the rounded raw 28.

    The old conversion wrote ``round(0.11 * 255) = 28``, which the radio's
    whole-watt register stores as 10 W. The quantizing encoder writes the
    smallest raw whose stored watt is the target (ceil(11 * 255 / 100) =
    29), the radio stores 11 W, and its own readback for 11 W is raw 28 —
    the admitted level the write path reports.
    """
    from rigplane.core.state_store import StateStore
    from rigplane.tci_adapter.server import TciAdapterServer
    from rigplane.ws import WS_OP_TEXT
    from test_tci_server_sockets import RawClient

    fake = _G7250QuantizedPowerFake()
    radio = await _g7250_radio(monkeypatch, fake)
    server = TciAdapterServer(
        radio=radio,
        state_store=StateStore(),
        host="127.0.0.1",
        port=0,
    )
    await server.start()
    client = await RawClient.connect(server.bound_port)
    try:
        await client.read_until_ready()
        await client.send_text("drive:0,11;")

        line: str | None = None
        async with asyncio.timeout(5.0):
            while line is None:
                opcode, payload = await client.next_frame()
                if opcode != WS_OP_TEXT:
                    continue  # watchdog pings are not commands
                text = payload.decode()
                if text.startswith("drive:"):
                    line = text
        assert line == "drive:0,11;"

        # The wire SET carries raw 29, BCD-encoded: the smallest raw whose
        # stored watt (floor(29 * 100 / 255) = 11 W) is the target. The raw
        # the percentage rounds to (28) would store 10 W.
        assert _level_bcd_encode(29) in _rf_power_set_payloads(fake)
        assert fake.power_watts == 11

        # The final reported readback: the radio's own answer for the
        # stored 11 W is the admitted raw 28 (floor(11 * 255 / 100)).
        assert await radio.get_rf_power() == 28
    finally:
        await client.close()
        await server.stop()
        await radio.disconnect()


# ---------------------------------------------------------------------------
# rigctld: \set_level RFPOWER over the real TCP entrypoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rigctld_set_level_rfpower_reports_the_admitted_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``L RFPOWER 0`` writes raw 13; a same-session ``l RFPOWER`` answers
    the admitted 12/255 immediately, not ``round(0 * 255) = 0.000000``.

    The quantum clamps the bottom of the domain up to the 5 W menu floor
    (raw band minimum 12), written as the smallest raw that floor reads
    back as (13); the radio's own answer for 5 W is raw 12, so the pending
    readback and the real answer agree instead of contradicting each
    other for the pending window.
    """
    from rigplane.rigctld.contract import RigctldConfig
    from rigplane.rigctld.server import RigctldServer

    fake = _G7250QuantizedPowerFake()
    radio = await _g7250_radio(monkeypatch, fake)
    server = RigctldServer(
        radio,
        RigctldConfig(
            host="127.0.0.1",
            port=0,
            cache_ttl=0.0,  # always fresh -> deterministic reads
        ),
    )
    await server.start()
    assert server._server is not None  # noqa: SLF001
    host, port = server._server.sockets[0].getsockname()[:2]  # noqa: SLF001
    reader, writer = await asyncio.open_connection(str(host), int(port))

    async def _cmd(line: str) -> str:
        writer.write((line + "\n").encode("ascii"))
        await writer.drain()
        data = await asyncio.wait_for(reader.readline(), 5.0)
        return data.decode("ascii").rstrip("\n")

    try:
        # The bottom of the domain: wire raw 13, the register stores 5 W.
        assert await _cmd("L RFPOWER 0") == "RPRT 0"
        assert _level_bcd_encode(13) in _rf_power_set_payloads(fake)
        assert fake.power_watts == 5

        # Same session, immediately: the pending overlay must carry the
        # ADMITTED readback (12/255) the write resolved to.
        assert await _cmd("l RFPOWER") == f"{12 / 255:.6f}"
        # The radio's own answer agrees: the overlay reconciles with the
        # real readback instead of overriding it for the pending window.
        assert await radio.get_rf_power() == 12

        # One mid value: 0.5 targets the 50 W step, written as the smallest
        # raw storing 50 W (128), read back floor(50 * 255 / 100) = 127.
        assert await _cmd("L RFPOWER 0.5") == "RPRT 0"
        assert _level_bcd_encode(128) in _rf_power_set_payloads(fake)
        assert fake.power_watts == 50
        assert await _cmd("l RFPOWER") == f"{127 / 255:.6f}"
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
        await radio.disconnect()
