"""MOR-3750 failing-first regression: plain M/X USB/LSB must turn DATA off.

Failing-first: plain ``M``/``X`` ``USB``/``LSB`` currently preserve DATA —
``_cmd_set_mode`` only calls ``_apply_packet_data_mode`` for packet modes
(``handler.py``: "Only set DATA mode explicitly for packet modes") and
``_cmd_set_split_mode`` passes ``data=None`` for plain modes. These tests
drive the real TCP stack (``RigctldServer`` + real ``IcomRadio`` /
``YaesuCatRadio`` + ``SplitIcomTransport`` / recorded CAT) and assert the
intended contract: an explicit DATA OFF on the wire, physical peer state,
and a plain-mode readback. Packet modes keep DATA on (green today).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.types import Mode
from serial_stub import SplitIcomTransport
from test_rigctld_wsjtx_split_mk2 import (
    MODELS,
    _client,
    _exchange,
    _peer,
    _rf_state,
)


def _data_frames(requests, n_before, first_byte: bytes):
    """(0x1A, 0x06) writes after ``n_before`` whose DATA byte matches."""
    return [
        req
        for req in requests[n_before:]
        if req[0] == 0x1A and req[1] == 0x06 and req[2][:1] == first_byte
    ]


def _assert_plain_mode_wire(requests, start, mode, filt, *, via_selected, explicit):
    writes = requests[start:]
    data_writes = [
        data for cmd, sub, data in writes if (cmd, sub) == (0x1A, 0x06) and data
    ]
    assert all(data[0] == 0 for data in data_writes)
    assert len(data_writes) <= 1
    if via_selected:
        assert [data for cmd, _, data in writes if cmd == 0x26 and len(data) > 1] == [
            bytes([0, mode, 0, filt])
        ]
        assert not any(cmd == 0x06 for cmd, _, _ in writes)
    else:
        expected = bytes([mode, filt]) if explicit else bytes([mode])
        assert [data for cmd, _, data in writes if cmd == 0x06] == [expected]
        assert len(data_writes) == 1
        assert not any(cmd == 0x26 and len(data) > 1 for cmd, _, data in writes)
    assert not any(cmd in (0x05, 0x07) for cmd, _, _ in writes)


def _last_split_mode_write(requests):
    writes = [data for cmd, _, data in requests if cmd == 0x26 and len(data) > 1]
    assert writes, f"no 0x26 mode write in {requests!r}"
    return writes[-1]


@asynccontextmanager
async def _peer_with_config(model: str, config: RigctldConfig, *, selected: str = "A"):
    """Local ``_peer`` twin accepting a ``RigctldConfig`` (no stub changes)."""
    transport = SplitIcomTransport(selected=selected)
    radio = IcomRadio("127.0.0.1", model=model, timeout=0.5)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_min_interval = 0
    server = RigctldServer(radio, config, allow_not_ready=True)
    server._state_store = radio.state_store
    try:
        await server.start()
        _rf_state(server, False)
        async with _client(server) as (reader, writer):
            yield radio, transport, server, reader, writer
    finally:
        await server.stop()
        await radio._civ_runtime.stop_pump()
        radio._connected = False


# --- Case 1: TCP M PKTUSB then plain M clears DATA ---------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("initial", [0, 1])
@pytest.mark.parametrize("plain", ["USB", "LSB"])
async def test_tcp_m_pktusb_then_plain_clears_data(model, initial, plain):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        peer.slots["A"] = (14_074_000, Mode.USB, 2, initial)
        rx_b_before = peer.slots["B"]
        assert await _exchange(writer, reader, b"M PKTUSB 2400\n") == b"RPRT 0\n"
        assert peer.slots["A"][1] == Mode.USB
        assert peer.slots["A"][3] == 1
        pkt_readback = await _exchange(writer, reader, b"m\n", lines=2)
        assert pkt_readback.split(b"\n")[0] == b"PKTUSB"
        n_before = len(peer.requests)
        assert (
            await _exchange(writer, reader, f"M {plain} 2400\n".encode()) == b"RPRT 0\n"
        )
        assert peer.slots["A"] == (14_074_000, Mode[plain], 2, 0)
        _assert_plain_mode_wire(
            peer.requests,
            n_before,
            Mode[plain],
            2,
            via_selected=_radio.profile.set_mode_via_selected,
            explicit=True,
        )
        assert await _exchange(writer, reader, b"m\n", lines=2) == (
            plain.encode() + b"\n2400\n"
        )
        # Unaffected receiver/slot and selected context.
        assert peer.slots["B"] == rx_b_before
        assert peer.selected == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("plain", ["USB", "LSB"])
async def test_tcp_m_plain_passband_zero_preserves_filter(model, plain):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        peer.slots["A"] = (14_074_000, Mode.USB, 2, 1)
        assert await _exchange(writer, reader, b"M PKTUSB 2400\n") == b"RPRT 0\n"
        pkt_readback = await _exchange(writer, reader, b"m\n", lines=2)
        pkt_passband = pkt_readback.split(b"\n")[1]
        pkt_filter = peer.slots["A"][2]
        n_before = len(peer.requests)
        assert await _exchange(writer, reader, f"M {plain} 0\n".encode()) == b"RPRT 0\n"
        # Passband 0 preserves the packet step's filter; DATA still goes off.
        assert peer.slots["A"] == (14_074_000, Mode[plain], pkt_filter, 0)
        _assert_plain_mode_wire(
            peer.requests,
            n_before,
            Mode[plain],
            pkt_filter,
            via_selected=_radio.profile.set_mode_via_selected,
            explicit=False,
        )
        assert await _exchange(writer, reader, b"m\n", lines=2) == (
            plain.encode() + b"\n" + pkt_passband + b"\n"
        )
        assert peer.selected == "A"


# --- Case 2: single-RX TCP X clears TX DATA ----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("initial", [0, 1])
@pytest.mark.parametrize(
    "tail", ["USB 2400", "USB 0", "USB", "LSB 2400", "LSB 0", "LSB"]
)
async def test_tcp_x_pktusb_then_plain_clears_tx_data(model, initial, tail):
    plain = tail.split(" ")[0]
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        peer.slots["B"] = (7_076_000, Mode.LSB, 3, initial)
        rx_before = peer.slots["A"]
        assert await _exchange(writer, reader, b"X PKTUSB 2400\n") == b"RPRT 0\n"
        assert peer.slots["B"] == (7_076_000, Mode.USB, 2, 1)
        # Packet step keeps its DATA-on frame on the wire.
        assert (0x26, None, bytes([1, Mode.USB, 1, 2])) in peer.requests
        pkt_readback = await _exchange(writer, reader, b"x\n", lines=2)
        assert pkt_readback.split(b"\n")[0] == b"PKTUSB"
        pkt_passband = pkt_readback.split(b"\n")[1]
        assert await _exchange(writer, reader, f"X {tail}\n".encode()) == b"RPRT 0\n"
        # Actual 0x26 write carries DATA byte 0 with the preserved filter.
        assert peer.slots["B"] == (7_076_000, Mode[plain], 2, 0)
        assert _last_split_mode_write(peer.requests) == bytes([1, Mode[plain], 0, 2])
        assert await _exchange(writer, reader, b"x\n", lines=2) == (
            plain.encode() + b"\n" + pkt_passband + b"\n"
        )
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"


# --- Case 3: MAIN routing on dual-RX; dual-RX X stays ENIMPL ------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"M USB 2400\n", b"M VFOA USB 2400\n"])
async def test_tcp_m_plain_clears_data_on_main_dual_rx(command):
    async with _peer("IC-7610") as (_radio, peer, _server, reader, writer):
        peer.slots["A"] = (14_074_000, Mode.USB, 2, 1)
        assert await _exchange(writer, reader, b"M PKTUSB 2400\n") == b"RPRT 0\n"
        n_before = len(peer.requests)
        assert await _exchange(writer, reader, command) == b"RPRT 0\n"
        # MAIN path: DATA OFF is emitted and MAIN reads back plain USB.
        assert _data_frames(peer.requests, n_before, b"\x00")
        assert peer.slots["A"][1] == Mode.USB
        assert peer.slots["A"][3] == 0
        raw = await _exchange(writer, reader, b"m\n", lines=2)
        assert raw.split(b"\n")[0] == b"USB"
        assert peer.selected == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"X USB 2400\n", b"X LSB 0\n", b"X PKTUSB 0\n"])
async def test_tcp_x_dual_rx_stays_enimpl_without_writes(command):
    async with _peer("IC-7610") as (_radio, peer, _server, reader, writer):
        before = dict(peer.slots)
        requests_before = list(peer.requests)
        assert await _exchange(writer, reader, command) == b"RPRT -4\n"
        assert peer.slots == before
        assert peer.selected == "A"
        assert peer.requests == requests_before


# --- Case 4: real Yaesu backend through TCP -----------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("plain", ["USB", "LSB"])
async def test_tcp_m_plain_yaesu_sends_mode_only(plain):
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    radio._transport._connected = True
    writes: list[str] = []
    state = {"code": "2"}

    async def fake_write(frame, *args, **kwargs):
        writes.append(frame)
        if frame.startswith("MD0"):
            state["code"] = frame[3]
        return None

    async def fake_query(cmd, *args, **kwargs):
        if cmd == "ID;":
            return "ID0840"
        if cmd.startswith("MD"):
            return f"MD0{state['code']}"
        if cmd in ("FA;", "FB;"):
            return cmd[:2] + "014074000"
        if cmd == "TX;":
            return "TX0"
        return ""

    radio._transport.write = AsyncMock(side_effect=fake_write)
    radio._transport.query = AsyncMock(side_effect=fake_query)
    server = RigctldServer(radio, config, allow_not_ready=True)
    await server.start()
    try:
        async with _client(server) as (reader, writer):
            assert await _exchange(writer, reader, b"M USB 2400\n") == b"RPRT 0\n"
            assert (
                await _exchange(writer, reader, f"M {plain} 2400\n".encode())
                == b"RPRT 0\n"
            )
            # Real FTX-1 mapping: USB -> MD02, LSB -> MD01, main receiver,
            # exactly one CAT write per M (no DATA overlay command).
            codes = {"USB": "2", "LSB": "1"}
            assert writes == ["MD02;", f"MD0{codes[plain]};"]
            raw = await _exchange(writer, reader, b"m\n", lines=2)
            assert raw.split(b"\n")[0] == plain.encode()
            assert radio.supports_command("set_data_mode") is False
    finally:
        await server.stop()


# --- Case 5: configured integer packet DATA selection, real stack ------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "expected"), [("IC-7610", b"\x02"), ("IC-7300", b"\x01")]
)
async def test_tcp_m_packet_keeps_configured_integer_data_selection(model, expected):
    config = RigctldConfig(
        host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2, wsjtx_data_mode=2
    )
    async with _peer_with_config(model, config) as (
        _radio,
        peer,
        _server,
        reader,
        writer,
    ):
        peer.slots["A"] = (14_074_000, Mode.USB, 1, 0)
        assert await _exchange(writer, reader, b"M PKTUSB 2400\n") == b"RPRT 0\n"
        # Multi-DATA IC-7610 keeps integer 2; single-DATA IC-7300 falls back
        # to True (0x01) — same expectations as the handler unit tests.
        assert peer.slots["A"][3] == expected[0]
        assert any(
            cmd == 0x1A and sub == 0x06 and data[:1] == expected
            for cmd, sub, data in peer.requests
        )
        raw = await _exchange(writer, reader, b"m\n", lines=2)
        assert raw.split(b"\n")[0] == b"PKTUSB"
