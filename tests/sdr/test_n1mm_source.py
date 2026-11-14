"""Receiver + passive source tests for the N1MM+ spectrum feed (MOR-4394)."""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import replace
import socket

import pytest

from rigplane.scope.models import Coverage, FollowView, NativeFrame, View
from rigplane.scope.n1mm_spectrum import (
    N1mmSpectrumPacket,
    emit_n1mm_spectrum,
    parse_n1mm_spectrum,
)
from rigplane.sdr.n1mm_source import (
    _MAX_COALESCE_TURNS,
    N1mmSpectrumReceiver,
    N1mmSpectrumSource,
)
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
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 17)
        sock.sendto(payload, ("127.0.0.1", port))


async def _turns(n: int) -> None:
    for _ in range(n):
        await asyncio.sleep(0)


@asynccontextmanager
async def _running(node):
    await node.start()
    try:
        yield node
    finally:
        await node.stop()


def _blast(port: int, packets: list[N1mmSpectrumPacket]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 17)
        for packet in packets:
            sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", port))


@pytest.mark.timeout(30)
async def test_receiver_delivers_emitted_packet() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    async with _running(receiver):
        assert receiver.port != 0
        packet = _packet()
        _send(receiver.port, emit_n1mm_spectrum(packet))
        await await_until(lambda: received, description="packet delivery")
        assert received == [packet]


@pytest.mark.timeout(30)
async def test_receiver_name_filter_and_rejection_counts() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(
        port=0, source_name="Thetis_1", on_packet=received.append
    )
    async with _running(receiver):
        other = replace(_packet(), name="Other")
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


@pytest.mark.timeout(30)
async def test_receiver_latest_wins_on_burst() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    async with _running(receiver):
        packets = [_packet(index) for index in range(5)]
        _blast(receiver.port, packets)
        await await_until(
            lambda: received and received[-1] == packets[-1],
            description="burst delivery",
        )
        await _turns(_MAX_COALESCE_TURNS * 3)
        assert received == [packets[-1]]


@pytest.mark.parametrize("middle", ["filtered", "malformed"])
@pytest.mark.timeout(30)
async def test_burst_coalesces_across_unaccepted_datagram(
    virtual_clock: VirtualClock, middle: str
) -> None:
    source = N1mmSpectrumSource(fps_limit=10.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    first = replace(_packet(0), name="wanted")
    last = replace(_packet(1), name="wanted", low_hz=14_010_000.0, high_hz=14_013_000.0)
    other = replace(_packet(9), name="Other")
    gap = b"<bogus" if middle == "malformed" else emit_n1mm_spectrum(other)
    receiver = N1mmSpectrumReceiver(
        port=0, source_name="wanted", on_packet=source.handle_packet
    )
    async with _running(receiver):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(emit_n1mm_spectrum(first), ("127.0.0.1", receiver.port))
            sock.sendto(gap, ("127.0.0.1", receiver.port))
            sock.sendto(emit_n1mm_spectrum(last), ("127.0.0.1", receiver.port))
        await await_until(lambda: frames, description="coalesced frame")
        await _turns(_MAX_COALESCE_TURNS * 2)
        assert len(frames) == 1
        assert frames[0].coverage == Coverage(last.low_hz, last.high_hz)


@pytest.mark.timeout(60)
async def test_receiver_sustained_backlog_makes_progress() -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    async with _running(receiver):
        packets = [
            replace(
                _packet(index),
                low_hz=14_000_000.0 + index * 1_000.0,
                high_hz=14_000_000.0 + index * 1_000.0 + 3_000.0,
            )
            for index in range(40)
        ]
        _blast(receiver.port, packets)
        await await_until(
            lambda: received and received[-1] == packets[-1],
            description="backlog drain",
        )
        await _turns(_MAX_COALESCE_TURNS * 3)
        assert 2 <= len(received) < len(packets)
        order = [packets.index(packet) for packet in received]
        assert order == sorted(order) and order[-1] == len(packets) - 1


@pytest.mark.timeout(60)
async def test_receiver_stream_no_starvation(virtual_clock: VirtualClock) -> None:
    received: list[N1mmSpectrumPacket] = []
    receiver = N1mmSpectrumReceiver(port=0, on_packet=received.append)
    async with _running(receiver):
        packets = [_packet(index) for index in range(20)]
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for step, packet in enumerate(packets):
                sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", receiver.port))
                await await_until(lambda: len(received) > step, description="delivery")
                await virtual_clock.elapse(0.1)
        assert received == packets


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.mark.timeout(30)
async def test_receiver_restart_same_port_after_stop() -> None:
    received: list[N1mmSpectrumPacket] = []
    port = _free_port()
    receiver = N1mmSpectrumReceiver(port=port, on_packet=received.append)
    await receiver.start()
    await receiver.stop()
    await receiver.start()
    async with _running(receiver):
        packet = _packet()
        _send(port, emit_n1mm_spectrum(packet))
        await await_until(lambda: received, description="post-restart delivery")
        assert received == [packet]


@pytest.mark.timeout(30)
async def test_source_restart_same_port_after_stop() -> None:
    port = _free_port()
    source = N1mmSpectrumSource(port=port, fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    await source.start()
    await source.stop()
    await source.start()
    async with _running(source):
        packet = _packet()
        _send(port, emit_n1mm_spectrum(packet))
        await await_until(lambda: frames, description="post-restart frame")
        assert len(frames) == 1
        assert frames[0].coverage == Coverage(packet.low_hz, packet.high_hz)


@pytest.mark.parametrize("target", ["receiver", "source"])
@pytest.mark.timeout(30)
async def test_cancelled_late_start_keeps_shared_startup(target: str) -> None:
    port = _free_port()
    packets: list[N1mmSpectrumPacket] = []
    frames: list[NativeFrame] = []
    if target == "receiver":
        node = N1mmSpectrumReceiver(port=port, on_packet=packets.append)
    else:
        node = N1mmSpectrumSource(port=port, fps_limit=1000.0)
        node.on_frame(frames.append)
    loop = asyncio.get_running_loop()
    real_endpoint = loop.create_datagram_endpoint
    entered, release = asyncio.Event(), asyncio.Event()

    async def gated(*args, **kwargs):
        entered.set()
        await release.wait()
        return await real_endpoint(*args, **kwargs)

    loop.create_datagram_endpoint = gated
    try:
        owner = asyncio.create_task(node.start())
        await await_until(entered.is_set, description="start entered bind")
        waiter = asyncio.create_task(node.start())
        await _turns(2)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await owner
        busy = N1mmSpectrumReceiver(port=port, on_packet=lambda packet: None)
        with pytest.raises(OSError):
            await busy.start()
        _send(port, emit_n1mm_spectrum(_packet()))
        await await_until(lambda: packets or frames, description="delivery")
        assert len(packets) + len(frames) == 1
    finally:
        loop.create_datagram_endpoint = real_endpoint
        await node.stop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", port))


@pytest.mark.timeout(30)
async def test_source_concurrent_and_cancelled_stop_close_fully() -> None:
    port = _free_port()
    source = N1mmSpectrumSource(port=port, fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    await source.start()
    first = asyncio.create_task(source.stop())
    await _turns(1)
    second = asyncio.create_task(source.stop())
    await _turns(2)
    assert not second.done()
    await asyncio.gather(first, second)
    await source.start()
    _send(port, emit_n1mm_spectrum(_packet()))
    await await_until(lambda: frames, description="post-restart frame")
    doomed = asyncio.create_task(source.stop())
    await _turns(2)
    doomed.cancel()
    with pytest.raises(asyncio.CancelledError):
        await doomed
    await source.stop()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", port))


@pytest.mark.timeout(30)
async def test_source_stop_during_start_no_leak() -> None:
    port = _free_port()
    source = N1mmSpectrumSource(port=port, fps_limit=1000.0)
    task = asyncio.create_task(source.start())
    await asyncio.sleep(0)
    await source.stop()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert not source._active
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
        probe.bind(("127.0.0.1", port))


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


def _source(fps_limit: float = 10.0) -> tuple[N1mmSpectrumSource, list[_FakeReceiver]]:
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
async def test_source_frames_cover_packet_edges(virtual_clock: VirtualClock) -> None:
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
async def test_source_epoch_tracks_geometry_only(virtual_clock: VirtualClock) -> None:
    source, _ = _source(fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    base = _packet(size=475)
    source.handle_packet(base)
    await virtual_clock.elapse(0.01)
    narrower = replace(base, levels=base.levels[:256])
    source.handle_packet(narrower)
    await virtual_clock.elapse(0.01)
    shifted = replace(narrower, low_hz=base.low_hz + 1e3, high_hz=base.high_hz + 1e3)
    source.handle_packet(shifted)
    await virtual_clock.elapse(0.01)
    source.handle_packet(shifted)
    await virtual_clock.elapse(0.01)
    assert [frame.epoch for frame in frames] == [0, 1, 2, 2]


@pytest.mark.timeout(30)
async def test_source_fps_limit_drops_not_queues(virtual_clock: VirtualClock) -> None:
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
async def test_source_views_and_receiver_guard(virtual_clock: VirtualClock) -> None:
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


@pytest.mark.parametrize(
    "noise_lo, noise_hi, tone, scale",
    [(10, 29, 160, 1.0), (0, 20, 160, 0.3125), (1000, 1200, 60000, 0.001)],
)
@pytest.mark.timeout(30)
async def test_source_relative_db_tone_not_saturated(
    virtual_clock: VirtualClock, noise_lo: int, noise_hi: int, tone: int, scale: float
) -> None:
    size, tone_pos, span = 256, 200, noise_hi - noise_lo + 1
    levels = [noise_lo + (i * 37) % span for i in range(size)]
    levels[tone_pos] = tone
    packet = N1mmSpectrumPacket(
        "table", "tone", 14e6, 14_000_000.0 + (size - 1) * 1000.0, scale, tuple(levels)
    )
    source, _ = _source(fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    source.handle_packet(packet)
    await virtual_clock.elapse(0.01)
    assert len(frames) == 1
    pixels = frames[0].pixels
    assert pixels is not None and len(pixels) == size
    peak = max(pixels)
    assert pixels.index(peak) == tone_pos
    assert pixels.count(peak) == 1
    below_half = sum(v < peak // 2 for i, v in enumerate(pixels) if i != tone_pos)
    assert below_half >= 3


@pytest.mark.timeout(30)
async def test_source_drops_nonfinite_packet_before_mapper(
    virtual_clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="rigplane.sdr.n1mm_source")
    source, _ = _source(fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    size, tone_pos = 256, 200
    overflowing = N1mmSpectrumPacket(
        "app", "name", 14e6, 14e6 + (size - 1) * 1000.0, 1e308, tuple([10] * size)
    )
    source.handle_packet(overflowing)
    await virtual_clock.elapse(0.01)
    assert frames == []
    assert source.dropped_nonfinite == 1
    assert "n1mm.spectrum.nonfinite" in caplog.text
    levels = [10] * size
    levels[tone_pos] = 160
    tone = replace(overflowing, scaling_factor=1.0, levels=tuple(levels))
    source.handle_packet(tone)
    await virtual_clock.elapse(0.01)
    assert len(frames) == 1
    pixels = frames[0].pixels
    assert pixels is not None and len(pixels) == size
    assert pixels.index(max(pixels)) == tone_pos
    assert pixels.count(max(pixels)) == 1


@pytest.mark.timeout(60)
async def test_replay_moving_tone_over_noise(virtual_clock: VirtualClock) -> None:
    rng = random.Random(4394)
    size, count = 512, 20
    packets = []
    for step in range(count):
        levels = [10 + rng.randrange(20) for _ in range(size)]
        tone = 20 + step * 20
        levels[tone] = 160
        pkt = N1mmSpectrumPacket(
            "replay", "tone", 14e6, 14_511_000.0, 1.0, tuple(levels)
        )
        packets.append(pkt)
    source = N1mmSpectrumSource(port=0, fps_limit=10.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    receiver = N1mmSpectrumReceiver(port=0, on_packet=source.handle_packet)
    async with _running(receiver), _running(source):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for index, packet in enumerate(packets):
                sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", receiver.port))
                await await_until(lambda: len(frames) == index + 1, description="frame")
                await virtual_clock.elapse(0.2)
    assert len(frames) == count
    for frame, step in zip(frames, range(count)):
        pixels = frame.pixels
        assert pixels is not None and len(pixels) == size
        assert pixels.index(max(pixels)) == 20 + step * 20
    assert parse_n1mm_spectrum(emit_n1mm_spectrum(packets[-1])) == packets[-1]


_LOW_HZ = 7_012_345.0
_HIGH_HZ = 7_113_579.0
_CARRIER_A_HZ = 7_040_000.0
_CARRIER_B_HZ = 7_090_123.0


def _expected_index(freq_hz: float, size: int) -> int:
    return round((freq_hz - _LOW_HZ) / (_HIGH_HZ - _LOW_HZ) * (size - 1))


def _known_signal_packet(
    size: int, carrier_a: int = 60, carrier_b: int = 40
) -> N1mmSpectrumPacket:
    rng = random.Random(4394)
    levels = [1 + rng.randrange(9) for _ in range(size)]
    levels[_expected_index(_CARRIER_A_HZ, size)] = carrier_a
    levels[_expected_index(_CARRIER_B_HZ, size)] = carrier_b
    return N1mmSpectrumPacket("known", "signal", _LOW_HZ, _HIGH_HZ, 1.0, tuple(levels))


@pytest.mark.parametrize("size", [475, 1024, 16384])
@pytest.mark.timeout(60)
async def test_known_signal_end_to_end(virtual_clock: VirtualClock, size: int) -> None:
    packet = _known_signal_packet(size)
    raw = emit_n1mm_spectrum(packet)
    if size == 16384:
        # Single-digit levels fit one datagram; real multi-digit senders would
        # NOT fit 16384 points (open question for Win4Yaesu, needs a capture).
        assert len(raw) <= 65_507
    source = N1mmSpectrumSource(port=0, fps_limit=1000.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    receiver = N1mmSpectrumReceiver(port=0, on_packet=source.handle_packet)
    async with _running(receiver), _running(source):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 17)
            sock.sendto(raw, ("127.0.0.1", receiver.port))
        await await_until(lambda: frames, description="known-signal frame")
        assert len(frames) == 1
    frame = frames[0]
    assert frame.coverage == Coverage(_LOW_HZ, _HIGH_HZ)
    pixels = frame.pixels
    assert pixels is not None and len(pixels) == size == frame.native_width
    expected_a = _expected_index(_CARRIER_A_HZ, size)
    expected_b = _expected_index(_CARRIER_B_HZ, size)
    brightest = sorted(range(size), key=lambda i: pixels[i], reverse=True)[:2]
    assert brightest == [expected_a, expected_b]
    others = [v for i, v in enumerate(pixels) if i != expected_a and i != expected_b]
    assert pixels[expected_a] > pixels[expected_b] > max(others)
    spacing = (_HIGH_HZ - _LOW_HZ) / (size - 1)
    assert abs(_LOW_HZ + expected_a * spacing - _CARRIER_A_HZ) <= spacing / 2
    assert abs(_LOW_HZ + expected_b * spacing - _CARRIER_B_HZ) <= spacing / 2


@pytest.mark.timeout(60)
async def test_known_signal_level_steps(virtual_clock: VirtualClock) -> None:
    size = 1024
    stepped_at = _expected_index(_CARRIER_A_HZ, size)
    steady_at = _expected_index(_CARRIER_B_HZ, size)
    source = N1mmSpectrumSource(port=0, fps_limit=10.0)
    frames: list[NativeFrame] = []
    source.on_frame(frames.append)
    receiver = N1mmSpectrumReceiver(port=0, on_packet=source.handle_packet)
    async with _running(receiver), _running(source):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            for step, level in enumerate((15, 25, 35, 45)):
                packet = _known_signal_packet(size, carrier_a=level)
                sock.sendto(emit_n1mm_spectrum(packet), ("127.0.0.1", receiver.port))
                await await_until(lambda: len(frames) == step + 1, description="step")
                await virtual_clock.elapse(0.2)
    assert len(frames) == 4
    stepped = [f.pixels[stepped_at] for f in frames if f.pixels is not None]
    assert len(stepped) == 4
    assert all(later > earlier for earlier, later in zip(stepped, stepped[1:]))
    for frame in frames:
        pixels = frame.pixels
        assert pixels is not None
        noise = [v for i, v in enumerate(pixels) if i != stepped_at and i != steady_at]
        assert pixels[stepped_at] > max(noise)
