"""Integration: a ``WavSource`` drives the simulated audio port's RX stream.

In-process, real UDP on loopback, reusing the client harness of
``tests/testkit/test_simulator_audio.py``. With a source set, the RX
payload is the FILE's bytes; with ``rx_source=None`` it stays on the
recorded byte ramp (regression pin). Red mutation: point
``_audio_rx_packet`` back at the ramp while a source is set.
"""

from __future__ import annotations

import asyncio
import heapq
import struct
import wave
from collections.abc import AsyncIterator, Callable, Coroutine
from pathlib import Path
from types import SimpleNamespace

import pytest

import rigplane_testkit.icom.simulator_audio as audio_module
from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.rx_source import RX_BURST_BYTES, WavSource
from rigplane_testkit.icom.simulator_audio import (
    _RX_IDENT_FIRST_FRAGMENT,
    AUDIO_HEADER_SIZE,
)
from testkit.test_simulator_audio import (
    _assert_burst_cadence,
    _collect_audio,
    _complete_bursts,
    _open_audio,
    _recorded_burst_shape,
    _udp,
)
from testkit.test_simulator_faults import _CLIENT_ID, _HOST, _UdpClient


class _AudioClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.loop = asyncio.get_running_loop()
        self.deadlines: list[float] = []
        self.sleepers: list[tuple[float, asyncio.Future[None]]] = []
        self.calls: list[tuple[float, Callable[..., None], tuple[object, ...]]] = []
        self.timers: list[
            tuple[float, int, Callable[..., None], tuple[object, ...]]
        ] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            audio_module,
            "asyncio",
            SimpleNamespace(
                get_running_loop=lambda: self,
                sleep=self.sleep,
                CancelledError=asyncio.CancelledError,
            ),
        )

    def time(self) -> float:
        return self.now

    def create_task(self, coro: Coroutine[object, object, None]) -> asyncio.Task[None]:
        return self.loop.create_task(coro)

    async def sleep(self, delay: float) -> None:
        deadline = self.now + delay
        future = self.loop.create_future()
        self.deadlines.append(deadline)
        self.sleepers.append((deadline, future))
        await future

    def call_at(
        self, when: float, callback: Callable[..., None], *args: object
    ) -> None:
        heapq.heappush(self.timers, (when, len(self.calls), callback, args))
        self.calls.append((when, callback, args))

    def call_later(
        self, delay: float, callback: Callable[..., None], *args: object
    ) -> None:
        self.call_at(self.now + delay, callback, *args)

    def advance_to(self, now: float) -> None:
        assert now >= self.now
        self.now = now
        while self.timers and self.timers[0][0] <= now:
            _, _, callback, args = heapq.heappop(self.timers)
            callback(*args)
        pending = []
        for deadline, future in self.sleepers:
            if not future.done():
                if deadline <= now:
                    future.set_result(None)
                else:
                    pending.append((deadline, future))
        self.sleepers = pending

    async def tick(self, now: float) -> None:
        self.advance_to(now)
        await asyncio.sleep(0)


@pytest.fixture
async def clocked_rx(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes]]:
    wav = tmp_path / "rx.wav"
    raw = _stereo_wav(wav, bursts=50)
    server = MockIcomRadio(enable_audio=True, rx_source=WavSource(wav))
    await server.start()
    client = await _udp()
    clock = _AudioClock()
    clock.install(monkeypatch)
    try:
        yield server, client, clock, raw
    finally:
        task = server._audio_rx_task
        await server.stop()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        client.close()


def _stereo_wav(path: Path, bursts: int) -> bytes:
    """A 48 kHz stereo s16 WAV (full-range pattern); returns the raw frames."""
    n = bursts * (RX_BURST_BYTES // 4)
    samples = struct.pack(
        f"<{n * 2}h", *(((i * 37) % 65536) - 32768 for i in range(n * 2))
    )
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(48000)
        w.writeframes(samples)
    return samples


async def test_rx_stream_carries_the_wav_payload(tmp_path: Path) -> None:
    wav = tmp_path / "rx.wav"
    raw = _stereo_wav(wav, bursts=50)
    server = MockIcomRadio(enable_audio=True, rx_source=WavSource(wav))
    await server.start()
    client = await _udp()
    try:
        await _open_audio(client, server)
        packets = await asyncio.wait_for(_collect_audio(client, 0.4), 5.0)
        bursts = _complete_bursts(packets, _recorded_burst_shape())
        assert len(bursts) >= 4, f"only {len(bursts)} complete bursts captured"

        for burst in bursts:
            first = burst[0][1]  # burst start: ident 0x8126 at 0x10
            assert struct.unpack_from(">H", first, 0x10)[0] == _RX_IDENT_FIRST_FRAGMENT
            payload = b"".join(p[AUDIO_HEADER_SIZE:] for _, p in burst)
            # Mid-file capture start: the burst index follows from the
            # audio-level seq at 0x12 (3 packets per burst, from 0).
            index = struct.unpack_from(">H", first, 0x12)[0] // 3
            assert (
                payload == raw[index * RX_BURST_BYTES : (index + 1) * RX_BURST_BYTES]
            ), f"burst {index} does not match the file"

        # Header sequences still step by 1 across the captured packets.
        flat = [p for burst in bursts for _, p in burst]
        seqs = [struct.unpack_from("<H", p, 6)[0] for p in flat]
        assert all((c - p) & 0xFFFF == 1 for p, c in zip(seqs, seqs[1:]))
    finally:
        client.close()
        await server.stop()


async def test_catchup_bursts_keep_file_order(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes],
) -> None:
    server, client, clock, raw = clocked_rx
    assert client.transport is not None
    addr = (_HOST, client.transport.get_extra_info("sockname")[1])
    server._audio_start_rx(addr, _CLIENT_ID)
    await asyncio.sleep(0)
    for _ in range(6):
        await clock.tick(0.121)
    assert clock.deadlines[-1] == pytest.approx(0.14)
    assert len(clock.calls) == 18
    clock.advance_to(0.13)
    frames = [await client.recv() for _ in range(18)]
    assert [struct.unpack_from("<H", p, 6)[0] for p in frames] == list(range(18))
    assert [struct.unpack_from(">H", p, 0x12)[0] for p in frames] == list(range(18))
    assert b"".join(p[AUDIO_HEADER_SIZE:] for p in frames) == raw[: 6 * RX_BURST_BYTES]


async def test_delayed_task_start_keeps_the_slot_origin(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes], tmp_path: Path
) -> None:
    server, client, clock, raw = clocked_rx
    server._rx_source = WavSource(
        tmp_path / "rx.wav", start="slot", utc=lambda: 14.9 + clock.time()
    )
    assert client.transport is not None
    server._audio_start_rx(
        (_HOST, client.transport.get_extra_info("sockname")[1]), _CLIENT_ID
    )
    clock.advance_to(0.05)
    await asyncio.sleep(0)
    for now in (0.05, 0.05, 0.06, 0.08, 0.10):
        await clock.tick(now)
    file_calls = [
        (when, args[0])
        for when, _, args in clock.calls
        if isinstance(args[0], bytes) and any(args[0][AUDIO_HEADER_SIZE:])
    ]
    assert len(file_calls) == 3
    assert file_calls[0][0] == pytest.approx(0.10)
    file_pcm = b"".join(p[AUDIO_HEADER_SIZE:] for _, p in file_calls)
    assert file_pcm == raw[:RX_BURST_BYTES]
    clock.advance_to(0.101)
    frames = [await client.recv() for _ in range(15)]
    assert b"".join(p[AUDIO_HEADER_SIZE:] for p in frames[:12]) == bytes(
        4 * RX_BURST_BYTES
    )
    assert b"".join(p[AUDIO_HEADER_SIZE:] for p in frames[12:]) == raw[:RX_BURST_BYTES]


@pytest.mark.parametrize("startup_delay", [0.200, 0.205])
async def test_resynchronized_task_start_keeps_the_future_slot_boundary(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes],
    tmp_path: Path,
    startup_delay: float,
) -> None:
    server, client, clock, raw = clocked_rx
    server._rx_source = WavSource(
        tmp_path / "rx.wav", start="slot", utc=lambda: 14.5 + clock.time()
    )
    assert client.transport is not None
    server._audio_start_rx(
        (_HOST, client.transport.get_extra_info("sockname")[1]), _CLIENT_ID
    )
    clock.advance_to(startup_delay)
    await asyncio.sleep(0)
    for _ in range(30):
        await clock.tick(clock.deadlines[-1])
        if any(any(args[0][AUDIO_HEADER_SIZE:]) for _, _, args in clock.calls[-3:]):
            break
    file_calls = [
        (when, args[0])
        for when, _, args in clock.calls
        if any(args[0][AUDIO_HEADER_SIZE:])
    ]
    assert len(file_calls) == 3
    assert file_calls[0][0] == pytest.approx(0.5)
    assert (
        b"".join(p[AUDIO_HEADER_SIZE:] for _, p in file_calls) == raw[:RX_BURST_BYTES]
    )


@pytest.mark.parametrize(
    ("first_wake", "second_burst"), [(0.035, 0.040), (0.135, 0.1358)]
)
async def test_ramp_cadence_allows_a_delayed_wake_then_catchup(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes],
    first_wake: float,
    second_burst: float,
) -> None:
    server, client, clock, _ = clocked_rx
    server._rx_source = None
    assert client.transport is not None
    server._audio_start_rx(
        (_HOST, client.transport.get_extra_info("sockname")[1]), _CLIENT_ID
    )
    await asyncio.sleep(0)
    await clock.tick(first_wake)
    for _ in range(24):
        await clock.tick(clock.deadlines[-1])
    starts = [when for when, _, _ in clock.calls[::3]]
    assert len(starts) == 25
    assert starts[:2] == pytest.approx([first_wake, second_burst])
    assert starts[-1] == pytest.approx(0.5)
    _assert_burst_cadence(starts)


async def test_rx_loop_uses_absolute_deadlines_after_sleep_overshoot(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes],
) -> None:
    server, client, clock, _ = clocked_rx
    assert client.transport is not None
    server._audio_start_rx(
        (_HOST, client.transport.get_extra_info("sockname")[1]), _CLIENT_ID
    )
    await asyncio.sleep(0)
    for now in (0.027, 0.047, 0.067):
        await clock.tick(now)
    assert len(clock.calls) == 9
    assert clock.deadlines == pytest.approx([0.02, 0.04, 0.06, 0.08])


@pytest.mark.parametrize(
    ("resume_at", "next_deadline"), [(0.139, 0.139), (0.141, 0.160)]
)
async def test_rx_loop_resynchronizes_only_beyond_five_periods(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes],
    resume_at: float,
    next_deadline: float,
) -> None:
    server, client, clock, _ = clocked_rx
    assert client.transport is not None
    server._audio_start_rx(
        (_HOST, client.transport.get_extra_info("sockname")[1]), _CLIENT_ID
    )
    await asyncio.sleep(0)
    await clock.tick(resume_at)
    await clock.tick(resume_at)
    assert len(clock.calls) == 6
    assert clock.deadlines == pytest.approx([0.02, resume_at, next_deadline])


@pytest.mark.parametrize("stop_first", [True, False])
async def test_pending_pcm_is_invalidated_on_reopen_or_retarget(
    clocked_rx: tuple[MockIcomRadio, _UdpClient, _AudioClock, bytes], stop_first: bool
) -> None:
    server, old_client, clock, raw = clocked_rx
    new_client = await _udp()
    try:
        assert old_client.transport is not None
        assert new_client.transport is not None
        old_addr = (_HOST, old_client.transport.get_extra_info("sockname")[1])
        new_addr = (_HOST, new_client.transport.get_extra_info("sockname")[1])
        server._audio_start_rx(old_addr, _CLIENT_ID)
        assert server._rx_source is not None
        server._rx_source.read_burst()
        server._audio_queue_rx_burst(old_addr, _CLIENT_ID)
        if stop_first:
            task = server._audio_rx_task
            server._audio_stop_rx("test_restart")
            assert task is not None
            await asyncio.gather(task, return_exceptions=True)
        server._audio_start_rx(new_addr, _CLIENT_ID + 1)
        await asyncio.sleep(0)
        await clock.tick(0.001)
        assert server.audio_rx_frames_sent == 0
        await clock.tick(0.02)
        clock.advance_to(0.021)
        frames = [await new_client.recv() for _ in range(3)]
        assert all(
            struct.unpack_from("<I", p, 0x0C)[0] == _CLIENT_ID + 1 for p in frames
        )
        index = 0 if stop_first else 2
        assert (
            b"".join(p[AUDIO_HEADER_SIZE:] for p in frames)
            == raw[index * RX_BURST_BYTES : (index + 1) * RX_BURST_BYTES]
        )
        await old_client.expect_silence(0.01)
    finally:
        new_client.close()


async def test_no_rx_source_keeps_the_byte_ramp() -> None:
    server = MockIcomRadio(enable_audio=True)
    await server.start()
    client = await _udp()
    try:
        await _open_audio(client, server)
        packets = await asyncio.wait_for(_collect_audio(client, 0.15), 5.0)
        frames = [p for _, p in packets if len(p) > AUDIO_HEADER_SIZE]
        assert len(frames) >= 6
        for p in frames:  # the ramp is seeded by the inner sequence
            inner = struct.unpack_from(">H", p, 0x12)[0]
            assert p[AUDIO_HEADER_SIZE] == inner & 0xFF
            assert p[AUDIO_HEADER_SIZE + 1] == (inner + 1) & 0xFF
    finally:
        client.close()
        await server.stop()
