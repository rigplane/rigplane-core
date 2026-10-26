"""FTX-1 native split gate: MAIN/SUB relative split via installed rigctld.

Composes MOR-4347 (native relative split roles) with MOR-4350 (protocol
boundary): a dual-RX native profile without ``split_tx_target`` must serve
``i``/``I``/``x``/``X`` through the structurally implemented relative
read/write contract, MAIN RX selection never moves (no ``VS``/``TX``
writes), and unknown Icom profiles stay ENIMPL (covered by the existing
dual-RX split suites).
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatCommandRejected
from rigplane.rigctld.audit import AUDIT_LOGGER_NAME
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from test_rigctld_installed_hamlib import _audit, _rigctl
from test_rigctld_wsjtx_split_mk2 import _exchange, _rf_state
from test_yaesu_relative_vfo import RelativePeer


class FtxSplitPeer(RelativePeer):
    """RelativePeer plus the identity/PTT/split frames a live rigctld
    session touches; ``ST`` writes move split state and are recorded."""

    def __init__(self):
        super().__init__()
        self.split = False

    async def query(self, frame, **kwargs):
        if frame == "ID;":
            return "ID0840"
        if frame == "TX;":
            return "TX0"
        if frame == "ST;":
            return f"ST{int(self.split)}"
        return await super().query(frame, **kwargs)

    async def write(self, frame, **kwargs):
        if kwargs.get("is_current") is not None:
            assert kwargs["is_current"]()
        self.writes.append(frame)
        if frame == self.reject:
            raise CatCommandRejected("?;")
        if frame == self.ignore:
            return
        if frame in ("ST0;", "ST1;"):
            self.split = frame == "ST1;"
        elif frame.startswith(("MD0", "MD1")):
            self.modes[int(frame[2])] = frame[3]
        elif frame.startswith(("FA", "FB")):
            self.freqs[0 if frame.startswith("FA") else 1] = int(frame[2:-1])
        else:
            raise AssertionError(f"selector, swap, filter or RF write: {frame}")


@asynccontextmanager
async def _ftx_server():
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    peer = FtxSplitPeer()
    radio._transport.query = AsyncMock(side_effect=peer.query)
    radio._transport.write = AsyncMock(side_effect=peer.write)
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    server = RigctldServer(radio, config, allow_not_ready=True)
    await server.start()
    _rf_state(server, False)
    try:
        yield peer, server
    finally:
        await server.stop()
        radio._transport._connected = False


@pytest.mark.asyncio
async def test_installed_client_split_mode_uses_native_ftx_roles(caplog):
    """X's implicit split enable and a PKTUSB->USB roundtrip stay on SUB."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        main_mode_before = server._radio.radio_state.receiver("MAIN").mode
        for mode, code, native in (
            ("PKTUSB", "C", "DATA-U"),
            ("USB", "2", "USB"),
        ):
            caplog.clear()
            await _rigctl(server, True, "X", "VFOA", mode, "0")
            records = _audit(caplog)
            assert any(
                r.long_cmd == "set_split_mode" and r.rprt == 0 for r in records
            ), records
            assert not any(r.long_cmd == "set_mode" for r in records), records
            assert peer.modes[1] == code
            assert peer.modes[0] == "2"
            assert peer.split is True
            # The verified write's get_mode readback promotes the native
            # SUB mode into the canonical mirror; MAIN keeps its own mode
            # instead of being overwritten with the requested one.
            assert server._radio.radio_state.receiver("SUB").mode == native
            assert server._radio.radio_state.receiver("MAIN").mode == main_mode_before
            stdout, stderr = await _rigctl(server, True, "s", "VFOA", "x", "VFOA")
            assert stdout.splitlines() == [
                b"1",
                b"VFOA",
                mode.encode(),
                b"0",
            ], (stdout, stderr)
        assert not any(w.startswith(("VS", "TX")) for w in peer.writes), peer.writes


@pytest.mark.asyncio
async def test_installed_client_ignored_ftx_split_mode_write_reports_actual(caplog):
    """An ignored MD1 write is audited as an error and never reads back as the request."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        peer.ignore = "MD1C;"
        await _rigctl(server, True, "X", "VFOA", "PKTUSB", "0")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_mode" and r.rprt != 0 for r in records), (
            records
        )
        assert peer.writes.count("MD1C;") == 1, peer.writes
        # A fresh rigctl must prime its split cache with a read (never a
        # SET) before Hamlib will send the split-mode query at all.
        stdout, stderr = await _rigctl(server, True, "s", "VFOA", "x", "VFOA")
        assert stdout.splitlines() == [b"1", b"VFOA", b"LSB", b"0"], (
            stdout,
            stderr,
        )
        assert peer.modes[0] == "2"
        assert not any(w.startswith("MD0") for w in peer.writes)
        # The write path's verification readback promoted the actual SUB
        # mode; the requested DATA-U never lands in the canonical mirror.
        assert server._radio.radio_state.receiver("SUB").mode == "LSB"


@pytest.mark.asyncio
async def test_installed_client_refused_ftx_split_mode_write_reports_error(caplog):
    """A CAT NAK of the SUB mode write is reported, MAIN stays untouched."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        peer.reject = "MD1C;"
        await _rigctl(server, True, "X", "VFOA", "PKTUSB", "0")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_mode" and r.rprt != 0 for r in records), (
            records
        )
        stdout, stderr = await _rigctl(server, True, "s", "VFOA", "x", "VFOA")
        assert stdout.splitlines() == [b"1", b"VFOA", b"LSB", b"0"], (
            stdout,
            stderr,
        )
        assert peer.modes[0] == "2"
        assert peer.writes.count("MD1C;") == 1


@pytest.mark.asyncio
async def test_installed_client_unknown_rf_refuses_ftx_split_enable(caplog):
    """Unknown RF truth refuses the implicit split enable before the radio."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        _rf_state(server, None)
        modes_before = dict(peer.modes)
        await _rigctl(server, True, "X", "VFOA", "PKTUSB", "0")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_vfo" and r.rprt == -9 for r in records), (
            records
        )
        assert not any(r.long_cmd in ("set_split_mode", "set_mode") for r in records), (
            records
        )
        assert peer.modes == modes_before
        assert peer.split is False
        assert peer.writes == []


@pytest.mark.asyncio
async def test_installed_client_ftx_filter_passband_refuses_before_write(caplog):
    """A non-zero passband maps to an Icom filter bank this path refuses."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        await _rigctl(server, True, "X", "VFOA", "USB", "2400")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_mode" and r.rprt != 0 for r in records), (
            records
        )
        assert not any(w.startswith("MD") for w in peer.writes), peer.writes
        assert peer.modes == {0: "2", 1: "1"}


@pytest.mark.asyncio
async def test_installed_client_ftx_sub_selection_refuses_without_selector_write(
    caplog,
):
    """SUB RX selection refuses the relative write without moving the selector."""
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        peer.vs = 1
        await _rigctl(server, True, "X", "VFOA", "PKTUSB", "0")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_mode" and r.rprt != 0 for r in records), (
            records
        )
        assert peer.writes == ["ST1;"], peer.writes
        assert peer.vs == 1


@pytest.mark.asyncio
async def test_structural_native_without_routing_strategy_stays_enimpl(
    monkeypatch, caplog
):
    """Structural relative methods alone never admit the native escape.

    A radio that implements the relative read/write contract but has no
    routing strategy (``create_routing`` returns ``None`` — the Icom
    case) keeps the undeclared dual-RX target ENIMPL before any radio
    I/O. The Icom unknown-profile params of the split suite pin the same
    gate on a real backend; this stub covers the generic half.
    """
    monkeypatch.setattr(
        "rigplane.rigctld.handler.create_routing",
        lambda radio, max_power_w=100.0: None,
    )
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _ftx_server() as (peer, server):
        host, port = server._server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        try:
            for command in (b"i\n", b"x\n", b"I 14080100\n", b"X USB 0\n"):
                assert await _exchange(writer, reader, command) == b"RPRT -4\n"
        finally:
            writer.close()
            await writer.wait_closed()
        assert peer.writes == []
        assert peer.queries == []
