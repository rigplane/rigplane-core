"""Installed Hamlib client -> real TCP server -> runtime -> fake CI-V.

No radio is opened. A fresh rigctl process reads back to exclude its local
mode cache. Without an installed binary these compatibility tests are skipped;
raw TCP contract coverage remains in the adjacent split suites.
"""

from __future__ import annotations

import asyncio
import logging
import shutil

import pytest

from rigplane.rigctld.audit import AUDIT_LOGGER_NAME, AuditRecord
from rigplane.types import Mode
from test_rigctld_dual_rx_split import _dual_peer
from test_rigctld_wsjtx_split_mk2 import _peer


async def _rigctl(server, dual: bool, *command: str):
    binary = shutil.which("rigctl")
    if binary is None:
        pytest.skip("installed Hamlib rigctl binary required")
    host, port = server._server.sockets[0].getsockname()[:2]
    argv = [binary, "-#", "-m", "2", "-r", f"{host}:{port}", "-P", "NONE"]
    if dual:
        argv.append("-o")
    process = await asyncio.create_subprocess_exec(
        *argv, *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    assert process.returncode == 0, (stdout, stderr)
    return stdout, stderr


def _audit(caplog):
    return [
        record.msg for record in caplog.records if isinstance(record.msg, AuditRecord)
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model,selected",
    [("IC-705", "A"), ("IC-7300", "A"), ("IC-7300MK2", "A")]
    + [
        (model, selected) for model in ("IC-7610", "IC-9700") for selected in ("A", "B")
    ],
)
async def test_installed_client_split_mode_reads_physical_tx(model, selected, caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    dual = model in ("IC-7610", "IC-9700")
    harness = _dual_peer if dual else _peer
    async with harness(model, selected=selected) as (radio, peer, server, _, _writer):
        rx = "A" if model == "IC-7610" else selected
        tx = "B" if rx == "A" else "A"
        rx_before = peer.slots[rx]
        prefix = ("VFOA",) if dual else ()
        for mode, data in (("PKTUSB", 1), ("USB", 0)):
            caplog.clear()
            # X alone deliberately exercises Hamlib's implicit S enable path.
            await _rigctl(server, dual, "X", *prefix, mode, "2400")
            writes = _audit(caplog)
            assert any(
                r.long_cmd == "set_split_mode" and r.rprt == 0 for r in writes
            ), writes
            assert not any(r.long_cmd == "set_mode" for r in writes), writes
            assert peer.slots[tx][1:] == (Mode.USB, 2, data)
            # Initialize the fresh client's split cache using a read, never a SET.
            stdout, stderr = await _rigctl(server, dual, "s", *prefix, "x", *prefix)
            assert stdout.splitlines() == [b"1", b"VFOA", mode.encode(), b"2400"], (
                stdout,
                stderr,
            )
            assert any(
                r.long_cmd == "get_split_mode" and r.rprt == 0 for r in _audit(caplog)
            )
            assert peer.slots[rx] == rx_before
            assert peer.selected == selected
            assert not peer.ptt_writes


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7610", "IC-9700"])
async def test_installed_client_wrong_mode_readback_reports_error(model, caplog):
    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _dual_peer(model) as (_radio, peer, server, _, _writer):
        peer.mode_write_mismatch = "mode"
        rx_before = peer.slots["A"]
        stdout, stderr = await _rigctl(server, True, "X", "VFOA", "USB", "2400")
        assert any(
            r.long_cmd == "set_split_mode" and r.rprt == -11 for r in _audit(caplog)
        )
        assert b"error" in (stdout + stderr).lower(), (stdout, stderr)
        observed, _ = await _rigctl(server, True, "s", "VFOA", "x", "VFOA")
        assert observed.splitlines() == [b"1", b"VFOA", b"LSB", b"2400"]
        assert peer.slots["B"][1:] == (Mode.LSB, 2, 0)
        assert peer.slots["A"] == rx_before
        assert not peer.ptt_writes


@pytest.mark.asyncio
async def test_installed_client_unknown_rf_refuses_implicit_split_enable(caplog):
    from test_rigctld_wsjtx_split_mk2 import _rf_state

    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _peer("IC-705") as (_radio, peer, server, _, _writer):
        _rf_state(server, None)
        before = dict(peer.slots)
        await _rigctl(server, False, "X", "PKTUSB", "2400")
        records = _audit(caplog)
        assert any(r.long_cmd == "set_split_vfo" and r.rprt == -9 for r in records)
        assert not any(r.long_cmd in ("set_split_mode", "set_mode") for r in records)
        assert peer.slots == before
        assert not peer.split
        assert not peer.ptt_writes


@pytest.mark.asyncio
async def test_installed_client_empty_split_readback_is_not_cached_success(
    caplog, monkeypatch
):
    from _helpers import wrap_civ_in_udp
    from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame

    caplog.set_level(logging.INFO, logger=AUDIT_LOGGER_NAME)
    async with _peer("IC-705") as (radio, peer, server, _, _writer):
        await _rigctl(server, False, "X", "USB", "2400")
        assert radio._last_split is True
        original = peer.send_tracked

        async def empty_split_reply(packet, *, is_current=None):
            frame = parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
            if frame.command == 0x0F and not frame.data:
                peer._responses.put_nowait(
                    wrap_civ_in_udp(
                        build_civ_frame(
                            CONTROLLER_ADDR,
                            frame.to_addr,
                            0x0F,
                        )
                    )
                )
                return
            await original(packet, is_current=is_current)

        monkeypatch.setattr(peer, "send_tracked", empty_split_reply)
        caplog.clear()
        stdout, stderr = await _rigctl(server, False, "s", "x")
        assert any(
            r.long_cmd == "get_split_vfo" and r.rprt == -11 for r in _audit(caplog)
        )
        assert b"error" in (stdout + stderr).lower(), (stdout, stderr)
        assert not any(r.long_cmd == "get_split_mode" for r in _audit(caplog))
        assert not peer.ptt_writes
