"""WSJT-X split acceptance through TCP, CoreRadio, and an ACK/NAK CI-V peer."""

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
from rigplane.types import Mode


def _known_rx(server: RigctldServer) -> None:
    _rf_state(server, False)


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
async def _peer(model: str, *, selected: str = "A"):
    transport = SplitIcomTransport(selected=selected)
    radio = IcomRadio("127.0.0.1", model=model, timeout=0.5)
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._civ_min_interval = 0
    config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
    server = RigctldServer(radio, config, allow_not_ready=True)
    server._state_store = radio.state_store
    try:
        await server.start()
        _known_rx(server)
        async with _client(server) as (reader, writer):
            yield radio, transport, server, reader, writer
    finally:
        await server.stop()
        await radio._civ_runtime.stop_pump()
        radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-7300", "IC-705"])
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_wsjtx_rig_split_sequence_preserves_physical_rx(model, initial):
    async with _peer(model, selected=initial) as (radio, peer, server, reader, writer):
        assert radio.radio_state.main.active_slot == "A"
        rx_before = peer.slots[initial]
        tx_slot = "B" if initial == "A" else "A"
        assert await _exchange(writer, reader, b"\\chk_vfo\n") == b"0\n"
        assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
        assert peer.slots[tx_slot][0] == 14_080_100
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        # Different from either seeded mode, so no-op mode implementations fail.
        assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT 0\n"
        assert peer.slots[tx_slot][1:3] == (Mode.RTTY, 3)
        assert peer.slots[initial] == rx_before
        assert peer.selected == initial
        assert peer.exchanges == 4
        assert peer.split
        async with _client(server) as (live_reader, live_writer):
            assert await _exchange(live_writer, live_reader, b"i\n") == b"14080100\n"
            expected = b"PKTRTTY\n1800\n" if peer.slots[tx_slot][3] else b"RTTY\n1800\n"
            assert (
                await _exchange(live_writer, live_reader, b"x\n", lines=2) == expected
            )
        assert peer.relative_freq_read.is_set()
        assert peer.relative_mode_read.is_set()
        assert peer.slots[initial] == rx_before
        unselected_paths = {
            FieldPath.unselected("main", "freq_mode", name)
            for name in ("freq_hz", "mode", "filter_num", "data_mode")
        }
        assert not any(
            overlay.path in unselected_paths
            for overlay in server._rig_handler._command_service._overlays
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-7300", "IC-705"])
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_wsjtx_split_after_transport_replacement_ignores_previous_slot_cache(
    model, initial
):
    async with _peer(model, selected=initial) as (radio, peer, server, reader, writer):
        for selected in (initial, "B" if initial == "A" else "A"):
            if selected != initial:
                await radio._civ_runtime.stop_pump()
                radio._connected = False
                peer = SplitIcomTransport(selected=selected)
                radio._civ_transport = peer
                radio._ctrl_transport = peer
                radio._connected = True
            rx_before = peer.slots[selected]
            other = "B" if selected == "A" else "A"
            assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
            assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT 0\n"
            assert peer.slots[other][:3] == (14_080_100, Mode.RTTY, 3)
            assert peer.slots[selected] == rx_before
            assert peer.selected == selected


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
@pytest.mark.parametrize("context", ["memory", "vfo"])
async def test_split_live_getters_preserve_memory_context(model, context):
    async with _peer(model, selected="B") as (_radio, peer, _server, reader, writer):
        peer.context = context
        before = dict(peer.slots)
        assert await _exchange(writer, reader, b"i\n") == b"14074000\n"
        assert await _exchange(writer, reader, b"x\n", lines=2) == b"USB\n3000\n"
        assert peer.context == context
        assert peer.slots == before
        assert peer.selected == "B"
        assert not any(command == 0x07 for command, _sub, _data in peer.requests)


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
async def test_split_wire_nak_returns_rejected_without_payload(model, command):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        peer.reject_exchange = 1
        before = dict(peer.slots)
        assert await _exchange(writer, reader, command) == b"RPRT -9\n"
        assert peer.slots == before
        assert peer.exchanges == 1
        assert not peer.payload_writes
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
async def test_split_wire_restore_nak_fails_successful_payload(command):
    async with _peer("IC-7300MK2") as (_radio, peer, server, reader, writer):
        peer.reject_exchange = 2
        assert await _exchange(writer, reader, command) == b"RPRT -9\n"
        assert peer.exchanges == 2
        assert peer.payload_writes == 1
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
@pytest.mark.parametrize("command", [b"X RTTY 1800\n", b"X PKTUSB 2400\n"])
async def test_split_mode_and_data_nak_restore_rx_without_success(model, command):
    async with _peer(model) as (_radio, peer, server, reader, writer):
        rx_before = peer.slots["A"]
        peer.reject_mode = command == b"X RTTY 1800\n"
        peer.reject_data = not peer.reject_mode
        assert await _exchange(writer, reader, command) == b"RPRT -9\n"
        assert peer.exchanges == 2
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
@pytest.mark.parametrize("command", [b"X USB\n", b"X USB 0\n"])
async def test_split_omitted_passband_preserves_tx_filter(model, command):
    async with _peer(model) as (radio, peer, server, reader, writer):
        # Keep the complete physical TX tuple fresh: a pending None filter
        # must not override filter 3 and turn x's passband into zero.
        server._config.cache_ttl = 60
        for name, value in (("mode", "LSB"), ("filter_num", 3), ("data_mode", 1)):
            server._state_store.apply_current(
                Observation(
                    path=FieldPath.vfo_slot("main", "B", "freq_mode", name),
                    value=value,
                    source=SourceMetadata(source="test", provider="tests"),
                    timestamp_monotonic=asyncio.get_running_loop().time(),
                    max_age=60,
                )
            )
        radio._filter_width = 1  # RX cache disagrees with physical TX filter 3.
        rx_before = peer.slots["A"]
        assert await _exchange(writer, reader, command) == b"RPRT 0\n"
        assert peer.slots["B"][1:3] == (Mode.USB, 3)
        assert peer.slots["A"] == rx_before
        assert await _exchange(writer, reader, b"x\n", lines=2) == b"PKTUSB\n1800\n"
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-705"])
@pytest.mark.parametrize("initial", ["A", "B"])
async def test_restored_main_reads_ignore_temporary_tx_cache_and_projection(
    model, initial
):
    async with _peer(model, selected=initial) as (radio, peer, server, reader, writer):
        original = peer.slots[initial]
        assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"
        assert await _exchange(writer, reader, b"X RTTY 1800\n") == b"RPRT 0\n"
        radio._state_cache.update_freq(14_080_100)
        radio._state_cache.update_mode("AM", 2)
        radio.radio_state.main.freq = 14_080_100
        radio.radio_state.main.mode = "AM"
        radio.radio_state.main.filter = 2
        for name, value in (
            ("freq_hz", 14_080_100),
            ("mode", "AM"),
            ("filter_num", 2),
            ("data_mode", 0),
        ):
            server._state_store.apply_current(
                Observation(
                    path=FieldPath.active("main", "freq_mode", name),
                    value=value,
                    source=SourceMetadata(source="test", provider="tests"),
                    timestamp_monotonic=asyncio.get_running_loop().time(),
                    max_age=60,
                )
            )
        peer.context = "memory"
        assert await _exchange(writer, reader, b"f\n") == f"{original[0]}\n".encode()
        expected = b"USB\n3000\n" if initial == "A" else b"PKTLSB\n1800\n"
        assert await _exchange(writer, reader, b"m\n", lines=2) == expected
        assert peer.context == "memory"
        assert peer.slots[initial] == original


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I inf\n", b"I -inf\n", b"I 1e999\n"])
async def test_split_wire_nonfinite_frequency_returns_einval(command):
    async with _peer("IC-7300MK2") as (_radio, peer, _server, reader, writer):
        assert await _exchange(writer, reader, command) == b"RPRT -1\n"
        assert not peer.requests


@asynccontextmanager
async def _paused_split(radio, reader, writer, monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    original = radio.set_freq

    async def paused(freq, receiver=0):
        if freq == 14_080_100:
            entered.set()
            await release.wait()
        await original(freq, receiver=receiver)

    monkeypatch.setattr(radio, "set_freq", paused)
    task = asyncio.create_task(_exchange(writer, reader, b"I 14080100\n"))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        yield release, task
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "lines", "expected"),
    [
        (b"f\n", 1, b"14074000\n"),
        (b"m\n", 2, b"USB\n3000\n"),
        (b"v\n", 1, b"VFOA\n"),
        (b"i\n", 1, b"14080100\n"),
        (b"x\n", 2, b"PKTLSB\n1800\n"),
    ],
)
async def test_split_transaction_blocks_all_getter_paths(
    monkeypatch, payload, lines, expected
):
    async with _peer("IC-7300MK2") as (radio, peer, server, reader, writer):
        # Exercise both projection and mirror branches while the lock is held.
        radio.radio_state.main.freq = 14_074_000
        radio.radio_state.main.mode = "USB"
        radio.radio_state.main.filter = 1
        async with _paused_split(radio, reader, writer, monkeypatch) as (
            release,
            first,
        ):
            assert peer.exchanges == 1
            async with _client(server) as (other_reader, other_writer):
                other = asyncio.create_task(
                    _exchange(other_writer, other_reader, payload, lines=lines)
                )
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(other), 0.03)
                    release.set()
                    assert await first == b"RPRT 0\n"
                    assert await other == expected
                finally:
                    release.set()
                    await asyncio.gather(other, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("dekey", [False, True])
async def test_split_ptt_waits_and_immediate_dekey_invalidates_queued_key(
    monkeypatch, dekey
):
    async with _peer("IC-7300MK2") as (radio, peer, server, reader, writer):
        async with _paused_split(radio, reader, writer, monkeypatch) as (
            release,
            first,
        ):
            async with _client(server) as (key_reader, key_writer):
                key = asyncio.create_task(_exchange(key_writer, key_reader, b"T 1\n"))
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(key), 0.03)
                    assert not peer.ptt_writes
                    if dekey:
                        async with _client(server) as (off_reader, off_writer):
                            assert (
                                await _exchange(off_writer, off_reader, b"T 0\n")
                                == b"RPRT 0\n"
                            )
                        assert peer.ptt_writes == [False]
                    release.set()
                    assert await first == b"RPRT 0\n"
                    if dekey:
                        assert await key == b"RPRT -9\n"
                        assert peer.ptt_writes == [False]
                    else:
                        assert await key == b"RPRT 0\n"
                        assert peer.ptt_writes == [True]
                        assert peer.ptt_key_tuples == [peer.slots["A"]]
                finally:
                    release.set()
                    await asyncio.gather(key, return_exceptions=True)


@pytest.mark.asyncio
async def test_split_wire_timeout_joins_restore_before_next_client_write():
    async with _peer("IC-7300MK2") as (_radio, peer, server, reader, writer):
        peer.restore_ack = asyncio.Event()
        server._config.command_timeout = 0.05
        first = asyncio.create_task(_exchange(writer, reader, b"I 14080100\n"))
        second = None
        try:
            await asyncio.wait_for(peer.restore_attempted.wait(), 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(first), 0.15)
            server._config.command_timeout = 2
            async with _client(server) as (other_reader, other_writer):
                second = asyncio.create_task(
                    _exchange(other_writer, other_reader, b"I 14090000\n")
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


@asynccontextmanager
async def _paused_exchange(peer, reader, writer):
    peer.pause_exchange = 1
    task = asyncio.create_task(_exchange(writer, reader, b"I 14080100\n"))
    try:
        await asyncio.wait_for(peer.exchange_paused.wait(), 2)
        yield task
    finally:
        peer.exchange_release.set()
        await asyncio.gather(task, return_exceptions=True)


async def _slot_waiters(server, count):
    async def wait():
        while len(server._rig_handler._vfo_slot_lock._waiters or ()) < count:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 2)


_RAW_KEY = b"w FE FE 94 E0 1C 00 01 FD\n"
_RAW_OFF = b"w FE FE 94 E0 1C 00 00 FD\n"
_RAW_ACK = b"FE FE E0 94 FB FD\n"


@pytest.mark.asyncio
@pytest.mark.parametrize("dekey", [False, True])
async def test_split_raw_ptt_fence_and_raw_off_invalidates_typed_key(dekey):
    async with _peer("IC-7300") as (_radio, peer, server, reader, writer):
        rx_before = peer.slots["A"]
        async with _paused_exchange(peer, reader, writer) as split:
            async with _client(server) as (key_reader, key_writer):
                key = asyncio.create_task(
                    _exchange(key_writer, key_reader, b"T 1\n" if dekey else _RAW_KEY)
                )
                try:
                    with pytest.raises(TimeoutError):
                        await asyncio.wait_for(asyncio.shield(key), 0.03)
                    assert not peer.ptt_writes
                    if dekey:
                        await _slot_waiters(server, 1)
                        async with _client(server) as (off_reader, off_writer):
                            assert (
                                await _exchange(off_writer, off_reader, _RAW_OFF)
                                == _RAW_ACK
                            )
                        assert peer.ptt_writes == [False]
                    peer.exchange_release.set()
                    assert await split == b"RPRT 0\n"
                    assert await key == (b"RPRT -9\n" if dekey else _RAW_ACK)
                    assert peer.ptt_writes == ([False] if dekey else [True])
                    assert peer.ptt_key_tuples == ([] if dekey else [rx_before])
                    assert peer.slots["A"] == rx_before
                    assert peer.exchanges == 2
                finally:
                    peer.exchange_release.set()
                    await asyncio.gather(key, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("command", [b"I 14080100\n", b"X RTTY 1800\n"])
@pytest.mark.parametrize("rf_after_key", [True, None, False])
async def test_split_waiting_behind_key_revalidates_rf_and_key_generation(
    command, rf_after_key
):
    async with _peer("IC-7300MK2") as (_radio, peer, server, reader, writer):
        peer.pause_frequency = 14_250_000
        peer.on_key = lambda: _rf_state(server, rf_after_key)
        ordinary = asyncio.create_task(_exchange(writer, reader, b"F 14250000\n"))
        key = split = None
        try:
            await asyncio.wait_for(peer.frequency_paused.wait(), 2)
            async with (
                _client(server) as (key_reader, key_writer),
                _client(server) as (
                    split_reader,
                    split_writer,
                ),
            ):
                key = asyncio.create_task(_exchange(key_writer, key_reader, b"T 1\n"))
                await _slot_waiters(server, 1)
                split = asyncio.create_task(
                    _exchange(split_writer, split_reader, command)
                )
                await _slot_waiters(server, 2)
                peer.frequency_release.set()
                assert await ordinary == b"RPRT 0\n"
                assert await key == b"RPRT 0\n"
                expected = b"RPRT -9\n" if rf_after_key is None else b"RPRT 0\n"
                assert await split == expected
                assert peer.ptt_writes == [True]
                assert peer.ptt_key_tuples == [(14_250_000, Mode.USB, 1, 0)]
                assert peer.exchanges == 0
                assert peer.payload_writes == 1
                assert peer.slots["B"] == (7_076_000, Mode.LSB, 3, 1)
        finally:
            peer.frequency_release.set()
            await asyncio.gather(
                *(task for task in (ordinary, key, split) if task is not None),
                return_exceptions=True,
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300MK2", "IC-7300"])
@pytest.mark.parametrize(
    ("command", "initial_filter", "expected_filter", "passband"),
    [
        (b"X PKTUSB 2400\n", 3, 2, b"2400"),
        (b"X PKTUSB\n", 2, 2, b"2400"),
        (b"X PKTUSB 0\n", 3, 3, b"1800"),
    ],
)
async def test_split_packet_data_preserves_physical_filter(
    model, command, initial_filter, expected_filter, passband
):
    async with _peer(model) as (_radio, peer, _server, reader, writer):
        peer.slots["B"] = (7_076_000, Mode.LSB, initial_filter, 0)
        rx_before = peer.slots["A"]
        assert await _exchange(writer, reader, command) == b"RPRT 0\n"
        assert peer.slots["B"] == (7_076_000, Mode.USB, expected_filter, 1)
        assert (
            await _exchange(writer, reader, b"x\n", lines=2)
            == b"PKTUSB\n" + passband + b"\n"
        )
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
        assert peer.exchanges == 2


@pytest.mark.asyncio
async def test_split_packet_filter_reapply_nak_restores_rx_and_fails():
    async with _peer("IC-7300") as (_radio, peer, server, reader, writer):
        rx_before = peer.slots["A"]
        peer.reject_mode_write = 2
        assert await _exchange(writer, reader, b"X PKTUSB 2400\n") == b"RPRT -9\n"
        assert peer.mode_writes == 2
        assert peer.slots["A"] == rx_before
        assert peer.selected == "A"
        assert peer.exchanges == 2
        assert not server._rig_handler._command_service._overlays


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
async def test_split_pipelined_off_invalidates_key_waiting_on_slot(raw):
    async with _peer("IC-7300") as (_radio, peer, server, reader, writer):
        async with _paused_exchange(peer, reader, writer) as split:
            async with _client(server) as (other_reader, other_writer):
                other_writer.write(_RAW_KEY + _RAW_OFF if raw else b"T 1\nT 0\n")
                await other_writer.drain()
                await asyncio.wait_for(peer.dekeyed.wait(), 1)
                assert peer.ptt_writes == [False]
                with pytest.raises(TimeoutError):
                    await asyncio.wait_for(other_reader.readline(), 0.03)
                peer.exchange_release.set()
                assert await split == b"RPRT 0\n"
                assert (
                    await asyncio.wait_for(other_reader.readline(), 2) == b"RPRT -9\n"
                )
                assert await asyncio.wait_for(other_reader.readline(), 2) == (
                    _RAW_ACK if raw else b"RPRT 0\n"
                )
                assert peer.ptt_writes == [False]
                assert not peer.ptt_key_tuples


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [False, True])
async def test_pipelined_off_waits_for_key_already_writing_radio(raw):
    async with _peer("IC-7300") as (_radio, peer, _server, reader, writer):
        peer.key_release = asyncio.Event()
        writer.write(_RAW_KEY + _RAW_OFF if raw else b"T 1\nT 0\n")
        await writer.drain()
        try:
            await asyncio.wait_for(peer.key_started.wait(), 2)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(peer.dekeyed.wait(), 0.03)
            assert not peer.ptt_writes
            peer.key_release.set()
            expected = _RAW_ACK if raw else b"RPRT 0\n"
            assert await asyncio.wait_for(reader.readline(), 2) == expected
            assert await asyncio.wait_for(reader.readline(), 2) == expected
            assert peer.ptt_writes == [True, False]
            assert peer.ptt_key_tuples == [(14_074_000, Mode.USB, 1, 0)]
        finally:
            peer.key_release.set()
