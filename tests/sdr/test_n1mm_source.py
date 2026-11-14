"""Receiver + passive source tests for the N1MM+ spectrum feed (MOR-4394)."""

from __future__ import annotations

import asyncio
import random
import socket
from collections.abc import Callable

import pytest

from rigplane.scope.models import Coverage, FollowView, NativeFrame, View
from rigplane.scope.n1mm_spectrum import (
    N1mmSpectrumPacket,
    emit_n1mm_spectrum,
    parse_n1mm_spectrum,
)
from rigplane.sdr.n1mm_source import N1mmSpectrumReceiver, N1mmSpectrumSource
from rigplane.testing.timing import VirtualClock, await_until


def _packet(index: int = 0, size: int = 4) -> N1mmSpectrumPacket:
    return N1mmSpectrumPacket(
        app="Thetis",
        name="Thetis_1",
        low_hz=14_000_000.0,
        high_hz=14_000_000.0 + (size - 1) * 1000.0,
        scaling_factor=1.0,
        levels=tuple((index * 7 + i) % 161 for i in range(size)),
    )


def _send(port: int, payload: bytes) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(payload, ("127.0.0.1", port))


@pytest.mark.timeout(30)
async def test_receiver_delivers_emitted_packet() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    await receiver.start()
    try:
        assert receiver.port != 0
        packet = _packet()
        _send(receiver.port, emit_n1mm_spectrum(packet))
        await await_until(lambda: received, description="packet delivery")
        assert received == [packet]
    finally:
        await receiver.stop()


@pytest.mark.timeout(30)
async def test_receiver_name_filter_and_rejection_counts() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(
        port=0, source_name="Thetis_1", on_packet=received.append
    )
    await receiver.start()
    try:
        other = _packet()
        other = N1mmSpectrumPacket(
            other.app,
            "Other",
            other.low_hz,
            other.high_hz,
            other.scaling_factor,
            other.levels,
        )
        _send(receiver.port, emit_n1mm_spectrum(other))
        _send(receiver.port, b"not xml at all")
        await await_until(
            lambda: receiver.filtered_count == 1 and receiver.rejected_count == 1,
            description="filter and rejection counts",
        )
        assert received == []
        assert receiver.last_rejection is not None
        assert receiver.last_rejection.reason == "malformed_xml"
        wanted = _packet()
        _send(receiver.port, emit_n1mm_spectrum(wanted))
        await await_until(lambda: received, description="wanted packet")
        assert received == [wanted]
    finally:
        await receiver.stop()


@pytest.mark.timeout(30)
async def test_receiver_latest_wins_on_burst() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    await receiver.start()
    try:
        packets = [_packet(index) for index in range(5)]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for packet in packets:
                sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", receiver.port))
        await await_until(lambda: received, description="burst delivery")
        for _ in range(20):
            await asyncio.sleep(0)
        assert received == [packets[-1]]
    finally:
        await receiver.stop()


@pytest.mark.timeout(30)
async def test_receiver_stop_idempotent_and_port_busy() -> None:
    first = N1mmSpectrumReceiver(port=0, on_packet=lambda packet: None)
    await first.start()
    try:
        busy = N1mmSpectrumReceiver(port=first.port, on_packet=lambda packet: None)
        with pytest.raises(OSError, match=str(first.port)):
            await busy.start()
        await first.stop()
        await first.stop()
    finally:
        await first.stop()


class _FakeReceiver:
    def __init__(self, on_packet: Callable[[N1mmSpectrumPacket], None]) -> None:
        self.on_packet = on_packet
        self.starts = 0
        self.stops = 0

    @property
    def port(self) -> int:
        return 0

    async def start(self) -> None:
        self.starts += 1

    async def stop(self) -> None:
        self.stops += 1


def _source(
    fps_limit: float = 10.0,
) -> tuple[N1mmSpectrumSource, list[_FakeReceiver]]:
    fakes: list[_FakeReceiver] = []

    def factory(on_packet: Callable[[N1mmSpectrumPacket], None]) -> _FakeReceiver:
        fakes.append(_FakeReceiver(on_packet))
        return fakes[-1]

    return N1mmSpectrumSource(factory, fps_limit=fps_limit), fakes


@pytest.mark.timeout(30)
async def test_source_start_stop_lifecycle(virtual_clock: VirtualClock) -> None:
    source, fakes = _source()
    await source.start()
    await source.start()
    assert len(fakes) == 1 and fakes[0].starts == 1
    await source.stop()
    await source.stop()
    assert fakes[0].stops == 1


@pytest.mark.timeout(30)
async def test_source_frames_cover_packet_edges(
    virtual_clock: VirtualClock,
) -> None:
    for size in (475, 1024, 16384):
        source, _ = _source(fps_limit=1000.0)
        frames: list[NativeFrame] = []
        source.on_frame(frames.append)
        packet = _packet(size=size)
        source.handle_packet(packet)
        await virtual_clock.elapse(0.01)
        assert len(frames) == 1
        frame = frames[0]
        assert frame.receiver is None
        assert frame.coverage == Coverage(packet.low_hz, packet.high_hz)
        assert frame.native_width == size == len(frame.pixels or b"")
        assert frame.pixels is not None
        assert all(byte <= 160 for byte in frame.pixels)
        assert len(set(frames[0].pixels or b"")) > 1


@pytest.mark.timeout(30)
async def test_source_epoch_tracks_geometry_only(
    virtual_clock: VirtualClock,
) -> None:
    source, _ = _source(fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    base = _packet(size=475)
    source.handle_packet(base)
    await virtual_clock.elapse(0.01)
    narrower = N1mmSpectrumPacket(
        base.app,
        base.name,
        base.low_hz,
        base.high_hz,
        base.scaling_factor,
        base.levels[:256],
    )
    source.handle_packet(narrower)
    await virtual_clock.elapse(0.01)
    shifted = N1mmSpectrumPacket(
        base.app,
        base.name,
        base.low_hz + 1000.0,
        base.high_hz + 1000.0,
        base.scaling_factor,
        base.levels[:256],
    )
    source.handle_packet(shifted)
    await virtual_clock.elapse(0.01)
    source.handle_packet(shifted)
    await virtual_clock.elapse(0.01)
    assert [frame.epoch for frame in frames] == [0, 1, 2, 2]


@pytest.mark.timeout(30)
async def test_source_fps_limit_drops_not_queues(
    virtual_clock: VirtualClock,
) -> None:
    source, _ = _source(fps_limit=10.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    packet = _packet(size=475)
    source.handle_packet(packet)
    source.handle_packet(packet)
    assert len(frames) == 1
    await virtual_clock.elapse(0.05)
    source.handle_packet(packet)
    assert len(frames) == 1
    await virtual_clock.elapse(0.06)
    source.handle_packet(packet)
    assert len(frames) == 2


@pytest.mark.timeout(30)
async def test_source_views_and_receiver_guard(
    virtual_clock: VirtualClock,
) -> None:
    source, _ = _source()
    caps = source.capabilities()
    assert caps.receivers == (None,)
    assert caps.view_modes == ("free",)
    assert caps.native_width is None
    assert caps.max_fps == 10.0
    assert "N1MM" in caps.coverage_rule
    with pytest.raises(ValueError, match="receiver"):
        source.resolve_view(View(1.0, 2.0), 0)
    with pytest.raises(ValueError, match="receiver"):
        source.coverage_for(View(1.0, 2.0), 0)
    with pytest.raises(ValueError, match="receiver"):
        source.resolve_follow(FollowView("vfo", 1000.0), Coverage(1.0, 2.0), 0)
    with pytest.raises(ValueError, match="no spectrum"):
        source.resolve_view(FollowView("vfo", 2000.0))
    assert source.coverage_for(View(1.0, 2.0)) is None
    packet = _packet(size=475)
    source.handle_packet(packet)
    await virtual_clock.elapse(0)
    assert source.capabilities().native_width == 475
    assert source.coverage_for(View(1.0, 2.0)) == Coverage(
        packet.low_hz, packet.high_hz
    )
    view = source.resolve_view(FollowView("vfo", 2000.0))
    mid = (packet.low_hz + packet.high_hz) / 2
    assert (view.start_hz, view.end_hz) == (mid - 1000.0, mid + 1000.0)
    passthrough = View(1.0, 2.0)
    assert source.resolve_view(passthrough) is passthrough
    kept = source.resolve_follow(
        FollowView("vfo", 2000.0), Coverage(1_000_000.0, 1_002_000.0)
    )
    assert (kept.start_hz, kept.end_hz) == (1_000_000.0, 1_002_000.0)
    granted = await source.set_view(FollowView("vfo", 2000.0))
    assert granted.view == view
    assert granted.coverage == Coverage(packet.low_hz, packet.high_hz)


@pytest.mark.timeout(60)
async def test_replay_moving_tone_over_noise(virtual_clock: VirtualClock) -> None:
    rng = random.Random(4394)
    size, count = 512, 20
    packets = []
    for step in range(count):
        levels = [10 + rng.randrange(20) for _ in range(size)]
        tone = 20 + step * 20
        levels[tone] = 160
        packets.append(
            N1mmSpectrumPacket(
                "replay", "tone", 14_000_000.0, 14_511_000.0, 1.0, tuple(levels)
            )
        )
    source = N1mmSpectrumSource(port=0, fps_limit=10.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    receiver = N1mmSpectrumReceiver(port=0, on_packet=source.handle_packet)
    await receiver.start()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for index, packet in enumerate(packets):
                sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", receiver.port))
                await await_until(
                    lambda: len(frames) == index + 1,
                    description=f"replay frame {index}",
                )
                await virtual_clock.elapse(0.2)
    finally:
        await receiver.stop()
        await source.stop()
    assert len(frames) == count
    for frame, step in zip(frames, range(count)):
        pixels = frame.pixels
        assert pixels is not None and len(pixels) == size
        assert pixels.index(max(pixels)) == 20 + step * 20
    assert parse_n1mm_spectrum(emit_n1mm_spectrum(packets[-1])) == packets[-1]
