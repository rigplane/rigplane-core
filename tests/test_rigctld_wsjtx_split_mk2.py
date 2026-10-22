"""WSJT-X split wire acceptance through real CoreRadio and a CI-V peer."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

import pytest

from serial_stub import SplitIcomTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.types import Mode


def _known_rx(server: RigctldServer) -> None:
    server._state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
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
async def _peer(model: str, *, selected: str = "A"):
    transport = SplitIcomTransport(selected=selected)
    radio = IcomRadio("127.0.0.1", model=model, timeout=0.5)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_min_interval = 0
    cfg = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    server = RigctldServer(radio, cfg, allow_not_ready=True)
    server._state_store = radio.state_store
    writer = None
    try:
        await server.start()
        _known_rx(server)
        host, port = server._server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        yield radio, transport, server, reader, writer
    finally:
        if writer is not None:
            writer.close()
            await writer.wait_closed()
        await server.stop()
        await radio._civ_runtime.stop_pump()
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("initial", ["A", "B"])
@pytest.mark.parametrize("selector", [0, 1])
@pytest.mark.parametrize("data_mode", [0, 1])
async def test_split_peer_mode_write_updates_tuple_and_acks(
    initial: str,
    selector: int,
    data_mode: int,
) -> None:
    peer = SplitIcomTransport(selected=initial)
    target = initial if selector == 0 else ("B" if initial == "A" else "A")
    before = dict(peer.slots)
    await peer.send_tracked(
        build_civ_frame(
            0xA4,
            CONTROLLER_ADDR,
            0x26,
            data=bytes([selector, Mode.RTTY, data_mode, 3]),
        )
    )
    assert peer.slots == {
        **before,
        target: (before[target][0], Mode.RTTY, 3, data_mode),
    }
    assert peer.selected == initial
    packet = await peer.receive_packet()
    assert parse_civ_frame(packet[packet.index(b"\xfe\xfe") :]).command == 0xFB
    assert not peer.relative_mode_read.is_set()
    await peer.send_tracked(
        build_civ_frame(
            0xA4,
            CONTROLLER_ADDR,
            0x26,
            data=bytes([selector]),
        )
    )
    packet = await peer.receive_packet()
    reply = parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
    assert reply.command == 0x26
    assert reply.data == bytes([selector, Mode.RTTY, data_mode, 3])
    assert peer.relative_mode_read.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-7300", "IC-705"])
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_wsjtx_rig_split_sequence_preserves_physical_rx(
    model: str,
    initial: str,
) -> None:
    async with _peer(model, selected=initial) as (radio, peer, server, reader, writer):
        # The unobserved mirror still says A, including physical-B startup.
        assert radio.radio_state.main.active_slot == "A"
        rx_before = peer.slots[initial]
        tx_slot = "B" if initial == "A" else "A"
        assert await _exchange(writer, reader, b"\\chk_vfo\n") == b"0\n"
        if initial == "A":
            assert await _exchange(writer, reader, b"v\n") == b"VFOA\n"
        assert await _exchange(writer, reader, b"s\n", lines=2) == b"0\nVFOA\n"
        assert await _exchange(writer, reader, b"f\n") == f"{rx_before[0]}\n".encode()
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
        assert peer.slots[tx_slot][0] == 14_080_100
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT 0\n"
        assert peer.slots[tx_slot][1:3] == (Mode.RTTY, 3)
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert peer.split
        # A separate client has no write overlays: both answers are live CI-V.
        host, port = server._server.sockets[0].getsockname()[:2]
        live_reader, live_writer = await asyncio.open_connection(host, port)
        try:
            assert await _exchange(live_writer, live_reader, b"i\n") == b"14080100\n"
            expected_mode = (
                b"PKTRTTY\n1800\n" if peer.slots[tx_slot][3] else b"RTTY\n1800\n"
            )
            assert (
                await _exchange(live_writer, live_reader, b"x\n", lines=2)
                == expected_mode
            )
            assert peer.relative_freq_read.is_set()
            assert peer.relative_mode_read.is_set()
            assert peer.slots[initial] == rx_before
            assert peer.selected == initial
        finally:
            live_writer.close()
            await live_writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-7300", "IC-705"])
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_wsjtx_split_after_transport_replacement_ignores_previous_slot_cache(
    model: str,
    initial: str,
) -> None:
    async with _peer(model, selected=initial) as (radio, peer, server, reader, writer):
        opposite = "B" if initial == "A" else "A"
        for selected in (initial, opposite):
            if selected != initial:
                assert radio.radio_state.main.active_slot == initial
                await radio._civ_runtime.stop_pump()
                radio._connected = False
                peer = SplitIcomTransport(selected=selected)
                radio._civ_transport = peer
                radio._ctrl_transport = peer
                radio._connected = True
                assert radio.radio_state.main.active_slot == initial
            before = peer.slots[selected]
            target = "B" if selected == "A" else "A"
            assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
            assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
            assert peer.slots[target][0] == 14_080_100
            assert peer.slots[selected] == before
            assert peer.selected == selected
            assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT 0\n"
            assert peer.slots[target][1:3] == (Mode.RTTY, 3)
            assert peer.slots[selected] == before
            assert peer.selected == selected
            assert radio.radio_state.main.active_slot == selected


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
async def test_split_wire_nak_returns_rejected_without_payload(
    model: str,
    command: bytes,
) -> None:
    async with _peer(model) as (radio, peer, server, reader, writer):
        peer.reject_slot = "B"
        before = dict(peer.slots)
        assert await _exchange(writer, reader, command) == b"RPRT -9\n"
        assert peer.slots == before
        assert peer.selected == "A"
        assert peer.restore_attempted.is_set()
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        (b"\xfa", b"RPRT -9\n"),
        (b"\x07", b"RPRT -6\n"),
        (b"\x07\x02", b"RPRT -6\n"),
        (b"\x07\x00\x01", b"RPRT -6\n"),
    ],
    ids=["NAK", "empty", "unknown", "trailing"],
)
async def test_split_wire_unproven_identity_never_switches_or_writes(
    command: bytes,
    reply: bytes,
    expected: bytes,
) -> None:
    async with _peer("IC-7300MK2", selected="B") as (
        radio,
        peer,
        server,
        reader,
        writer,
    ):
        peer.query_reply = reply
        before = dict(peer.slots)
        assert await _exchange(writer, reader, command) == expected
        assert peer.selected == "B"
        assert peer.slots == before
        assert not peer.restore_attempted.is_set()


@pytest.mark.asyncio
async def test_split_wire_timeout_joins_restore_before_next_client_write() -> None:
    async with _peer("IC-7300MK2") as (radio, peer, server, reader, writer):
        peer.restore_ack = asyncio.Event()
        server._config.command_timeout = 0.05
        first = asyncio.create_task(_exchange(writer, reader, b"I 14080100\n"))
        second = None
        second_writer = None
        try:
            await asyncio.wait_for(peer.restore_attempted.wait(), 2)
            # The server cancels the command on timeout while its restore
            # is already awaiting an ACK; cleanup must remain joined.
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(first), 0.15)
            server._config.command_timeout = 2
            host, port = server._server.sockets[0].getsockname()[:2]
            second_reader, second_writer = await asyncio.open_connection(host, port)
            second = asyncio.create_task(
                _exchange(second_writer, second_reader, b"I 14090000\n")
            )
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(peer.second_payload.wait(), 0.02)
            peer.restore_ack.set()
            assert await first == b"RPRT -5\n"
            assert await second == b"RPRT 0\n"
            assert peer.selected == "A"
            assert peer.slots["A"] == (14_074_000, Mode.USB, 1, 0)
            assert peer.slots["B"][0] == 14_090_000
        finally:
            peer.restore_ack.set()
            for task in (first, second):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (first, second) if task is not None),
                return_exceptions=True,
            )
            if second_writer is not None:
                second_writer.close()
                await second_writer.wait_closed()
