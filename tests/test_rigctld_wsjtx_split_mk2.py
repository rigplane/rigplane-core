"""WSJT-X split acceptance through TCP, CoreRadio, and a CI-V transport peer."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from serial_stub import SplitIcomTransport
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.types import Mode, bcd_encode

from _helpers import observe_selected_receiver

MODELS = ["IC-7300", "IC-7300MK2", "IC-705"]


def _rf_state(server: RigctldServer, value: bool | None) -> None:
    server._state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=value,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )


async def _exchange(writer, reader, payload: bytes, *, lines: int = 1) -> bytes:
    writer.write(payload)
    await writer.drain()
    return b"".join(
        [await asyncio.wait_for(reader.readline(), 2) for _ in range(lines)]
    )


@asynccontextmanager
async def _client(server):
    host, port = server._server.sockets[0].getsockname()[:2]
    reader, writer = await asyncio.open_connection(host, port)
    try:
        yield reader, writer
    finally:
        writer.close()
        await writer.wait_closed()


@asynccontextmanager
async def _peer(model: str, *, selected: str = "A", profile=None, configure=None):
    transport = SplitIcomTransport(selected=selected)
    radio = IcomRadio("127.0.0.1", model=model, profile=profile, timeout=0.5)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    # The connect flow's 0x07 0xD2 answer: MAIN is selected (MOR-3591).
    observe_selected_receiver(radio, 0)
    radio._civ_min_interval = 0
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    kwargs = configure(radio) if configure is not None else {}
    server = RigctldServer(radio, config, allow_not_ready=True, **kwargs)
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


def _assert_no_relative_overlays(server):
    paths = {
        FieldPath.unselected("main", "freq_mode", name)
        for name in ("freq_hz", "mode", "filter_num", "data_mode")
    }
    assert not any(
        overlay.path in paths
        for overlay in (
            *server._rig_handler._command_service._overlays,
            *server._rig_handler._command_service._readback_expectations,
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_wsjtx_rig_split_sequence_preserves_physical_rx(model, initial):
    async with _peer(model, selected=initial) as (_radio, peer, server, reader, writer):
        assert server._rig_handler._command_queue is None
        rx_before = peer.slots[initial]
        tx_slot = "B" if initial == "A" else "A"
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
        assert peer.requests[-2:] == [
            (0x25, None, b"\x01" + bcd_encode(14_080_100)),
            (0x25, None, b"\x01"),
        ]
        assert await _exchange(writer, reader, b"X PKTUSB 2400\n") == b"RPRT 0\n"
        assert peer.slots[tx_slot] == (14_080_100, Mode.USB, 2, 1)
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        assert peer.split
        assert (0x25, None, b"\x01" + bcd_encode(14_080_100)) in peer.requests
        assert (0x25, None, b"\x01") in peer.requests
        assert (0x26, None, bytes([1, Mode.USB, 1, 2])) in peer.requests
        assert (0x26, None, b"\x01") in peer.requests
        assert not any(cmd in (0x05, 0x06, 0x07, 0x1A) for cmd, _, _ in peer.requests)
        async with _client(server) as (other_reader, other_writer):
            assert await _exchange(other_writer, other_reader, b"i\n") == b"14080100\n"
            assert (
                await _exchange(other_writer, other_reader, b"x\n", lines=2)
                == b"PKTUSB\n2400\n"
            )
        assert await _exchange(writer, reader, b"T 1\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"T 0\n") == b"RPRT 0\n"
        assert peer.ptt_writes == [True, False]
        assert peer.ptt_key_tuples == [(14_080_100, Mode.USB, 2, 1)]
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        _assert_no_relative_overlays(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("data_mode", [0, 1])
async def test_split_explicit_passband_preserves_tx_data_on_wire(model, data_mode):
    # MOR-3750 intended contract: plain X USB clears TX DATA (DATA byte 0),
    # it does not preserve the incoming data_mode on the wire.
    async with _peer(model) as (_radio, peer, server, reader, writer):
        peer.slots["B"] = (7_076_000, Mode.LSB, 3, data_mode)
        rx_before = peer.slots["A"]
        assert await _exchange(writer, reader, b"X USB 2400\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"x\n", lines=2) == b"USB\n2400\n"
        assert peer.requests == [
            (0x26, None, bytes([1, Mode.USB, 0, 2])),
            (0x25, None, b"\x01"),
            (0x26, None, b"\x01"),
            (0x25, None, b"\x01"),
            (0x26, None, b"\x01"),
        ]
        assert peer.slots["B"] == (7_076_000, Mode.USB, 2, 0)
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
        _assert_no_relative_overlays(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_split_omitted_passband_preserves_tx_data_and_filter(model, command):
    # MOR-3750 intended contract: plain X USB clears TX DATA (DATA byte 0)
    # while still preserving the TX filter; x reads back plain USB.
    async with _peer(model) as (radio, peer, server, reader, writer):
        radio._filter_width = 1
        rx_before = peer.slots["A"]
        assert await _exchange(writer, reader, command) == b"RPRT 0\n"
        assert peer.slots["B"] == (7_076_000, Mode.USB, 3, 0)
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
        assert await _exchange(writer, reader, b"x\n", lines=2) == b"USB\n1800\n"
        mode_requests = [data for cmd, _, data in peer.requests if cmd == 0x26]
        assert mode_requests == [
            b"\x01",
            bytes([1, Mode.USB, 0, 3]),
            b"\x01",
            b"\x01",
        ]
        _assert_no_relative_overlays(server)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize(
    ("name", "mode"),
    [("PKTUSB", Mode.USB), ("PKTLSB", Mode.LSB), ("PKTRTTY", Mode.RTTY)],
)
@pytest.mark.parametrize("initial_data", [0, 1])
@pytest.mark.parametrize(
    ("passband", "expected_filter", "expected_passband"),
    [(0, 3, 1800), (2400, 2, 2400)],
)
async def test_split_packet_mode_sets_data_in_same_frame(
    model, name, mode, initial_data, passband, expected_filter, expected_passband
):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        peer.slots["B"] = (7_076_000, Mode.AM, 3, initial_data)
        rx_before = peer.slots["A"]
        assert (
            await _exchange(writer, reader, f"X {name} {passband}\n".encode())
            == b"RPRT 0\n"
        )
        assert peer.slots["B"] == (7_076_000, mode, expected_filter, 1)
        assert (0x26, None, bytes([1, mode, 1, expected_filter])) in peer.requests
        assert not any(cmd in (0x06, 0x07, 0x1A) for cmd, _, _ in peer.requests)
        assert await _exchange(writer, reader, b"x\n", lines=2) == (
            f"{name}\n{expected_passband}\n".encode()
        )
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
