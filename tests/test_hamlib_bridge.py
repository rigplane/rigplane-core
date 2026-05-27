"""Tests for the local Hamlib A1 bridge runner (MOR-166 slice 1).

No real hardware and no real rigctld: the transparent CI-V pipe is exercised with
a fake raw-pipe radio and a direct TCP client; rigctld launch is checked via mock.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.hamlib_bridge import BridgeFrame, HamlibBridge


class _Sub:
    def __init__(self, registry: list[Any], cb: Any) -> None:
        self._registry = registry
        self._cb = cb
        self.closed = False

    def close(self) -> None:
        self.closed = True
        try:
            self._registry.remove(self._cb)
        except ValueError:
            pass


class FakeRawPipeRadio:
    """Implements the MOR-164 raw CI-V pipe surface for tests."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self._listeners: list[Any] = []

    async def send_civ_raw_fire_and_forget(self, frame: bytes) -> None:
        self.sent.append(frame)

    def add_raw_civ_listener(self, callback: Any) -> _Sub:
        self._listeners.append(callback)
        return _Sub(self._listeners, callback)

    def emit(self, frame: bytes) -> None:
        """Simulate an inbound frame from the radio."""
        for cb in list(self._listeners):
            cb(frame)


@pytest.fixture
def radio() -> FakeRawPipeRadio:
    return FakeRawPipeRadio()


async def test_open_transport_registers_and_stop_unsubscribes(
    radio: FakeRawPipeRadio,
) -> None:
    bridge = HamlibBridge(radio, model="3078")
    port = await bridge.open_transport()
    assert port > 0
    assert len(radio._listeners) == 1
    await bridge.stop()
    assert len(radio._listeners) == 0  # subscription closed on stop


async def test_hamlib_frames_forwarded_to_radio(radio: FakeRawPipeRadio) -> None:
    bridge = HamlibBridge(radio, model="3078")
    await bridge.open_transport()
    _, writer = await asyncio.open_connection("127.0.0.1", bridge.back_port)

    f1 = bytes.fromhex("fefe98e003fd")
    f2 = bytes.fromhex("fefe98e02500fd")
    f3 = bytes.fromhex("fefe98e01c0001fd")

    writer.write(f1 + f2)  # two frames in one write
    await writer.drain()
    writer.write(f3[:4])  # split a frame across two writes
    await writer.drain()
    await asyncio.sleep(0.02)
    writer.write(f3[4:])
    await writer.drain()
    await asyncio.sleep(0.05)

    assert radio.sent == [f1, f2, f3]

    writer.close()
    await bridge.stop()


async def test_radio_frames_forwarded_to_hamlib(radio: FakeRawPipeRadio) -> None:
    bridge = HamlibBridge(radio, model="3078")
    await bridge.open_transport()
    reader, writer = await asyncio.open_connection("127.0.0.1", bridge.back_port)
    await asyncio.sleep(0.02)  # let the server accept and register the writer

    ack = bytes.fromhex("fefee098fbfd")  # bare ACK from the radio
    radio.emit(ack)
    got = await asyncio.wait_for(reader.readexactly(len(ack)), timeout=1.0)
    assert got == ack

    writer.close()
    await bridge.stop()


async def test_trace_records_both_directions(radio: FakeRawPipeRadio) -> None:
    recs: list[BridgeFrame] = []
    bridge = HamlibBridge(radio, model="3078", on_frame=recs.append)
    await bridge.open_transport()
    _, writer = await asyncio.open_connection("127.0.0.1", bridge.back_port)
    await asyncio.sleep(0.02)

    writer.write(bytes.fromhex("fefe98e003fd"))
    await writer.drain()
    await asyncio.sleep(0.03)
    radio.emit(bytes.fromhex("fefee098fbfd"))
    await asyncio.sleep(0.02)

    directions = {rec.direction for rec in recs}
    assert directions == {"tx", "rx"}
    assert any(rec.hex == "fe fe 98 e0 03 fd" for rec in bridge.frames)

    writer.close()
    await bridge.stop()


def test_rigctld_argv(radio: FakeRawPipeRadio) -> None:
    bridge = HamlibBridge(
        radio,
        model="3078",
        civaddr=0x98,
        front_port=4599,
        rigctld_path="/usr/bin/rigctld",
        rigctld_conf="retry=0",
    )
    bridge._back_port = 50123  # simulate an open transport
    assert bridge.rigctld_argv() == [
        "/usr/bin/rigctld",
        "-m",
        "3078",
        "-r",
        "127.0.0.1:50123",
        "-t",
        "4599",
        "-c",
        "152",  # 0x98
        "-C",
        "retry=0",
    ]


async def test_spawn_rigctld_invokes_subprocess(radio: FakeRawPipeRadio) -> None:
    bridge = HamlibBridge(radio, model="3078", civaddr=0x98, rigctld_path="rigctld")
    await bridge.open_transport()

    fake_proc = AsyncMock()
    fake_proc.pid = 4242
    fake_proc.returncode = None
    with patch(
        "rigplane.hamlib_bridge.asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=fake_proc),
    ) as spawn:
        proc = await bridge.spawn_rigctld()

    assert proc is fake_proc
    argv = list(spawn.call_args.args)
    assert argv == bridge.rigctld_argv()

    bridge._proc = None  # avoid stop() awaiting the mock's terminate/wait
    await bridge.stop()
