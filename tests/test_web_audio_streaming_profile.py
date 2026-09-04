"""Performance profiling for web audio streaming pipeline (M6.P2.3).

Measures latency and throughput of:
- Audio codec operations (ulaw decode, PCM16 encode)
- Frame building and serialization
- End-to-end relay loop processing

The measuring tests here are marked ``benchmark`` and so deselected from the
default suite: they compare wall-clock measurements against fixed thresholds
that idle hardware clears many times over -- the narrowest,
``TestAudioCodecPerformance.test_frame_encode_throughput``, by about eight
times -- or assert nothing at all and exist to print a number. Either way
they are measurements rather than gates: a slowdown small enough to matter
passes, while a busy host fails.
``TestAudioStreamingEndToEnd.test_client_queue_saturation`` is deliberately
NOT marked and stays in the default suite: it counts ``QueueFull``
rejections against a bounded queue, a behaviour that does not depend on how
fast the host is, so none of the reasoning above reaches it. Run the marked
ones deliberately with ``uv run pytest -m benchmark tests/ -s`` and read the
printed numbers. Tightening a threshold here means revisiting the marker, because a threshold
tight enough to catch a regression is also tight enough to catch a busy
neighbour.
"""

from __future__ import annotations

import asyncio
import statistics
import time
from unittest.mock import MagicMock

import pytest

from rigplane.audio._codecs import decode_ulaw_to_pcm16
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.protocol import (
    encode_audio_frame,
    MSG_TYPE_AUDIO_RX,
    AUDIO_CODEC_PCM16,
)


@pytest.mark.benchmark
class TestAudioCodecPerformance:
    """Profile audio codec operations."""

    def test_ulaw_decode_latency(self) -> None:
        """Measure ulaw to PCM16 decode latency."""
        # Create 20ms of ulaw data at 8kHz mono (160 samples = 160 bytes)
        ulaw_data = bytes([0x80] * 160)  # Mid-range ulaw values

        # Warmup
        decode_ulaw_to_pcm16(ulaw_data)

        # Measure
        latencies: list[float] = []
        for _ in range(1000):
            start = time.perf_counter()
            decode_ulaw_to_pcm16(ulaw_data)
            latencies.append((time.perf_counter() - start) * 1e6)  # Convert to µs

        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]
        p99 = sorted(latencies)[int(0.99 * len(latencies))]

        print(
            f"\nulaw decode (160 bytes): p50={p50:.2f}µs, p95={p95:.2f}µs, p99={p99:.2f}µs"
        )
        assert p50 < 1000, "ulaw decode should be <1ms"

    def test_ulaw_decode_throughput(self) -> None:
        """Measure ulaw decode throughput (samples/sec).

        Was marked ``slow`` for a reason that was never about duration —
        "runner allocation is variable enough that the throughput floor
        flakes on slow nodes" — which is the ``benchmark`` rationale in the
        module docstring, reached before that marker existed. It carries
        ``benchmark`` from the class now, so one marker covers the whole
        shape and ``slow`` goes back to meaning long-running.
        """
        # 20ms audio @ 16kHz = 320 samples = 320 bytes ulaw
        ulaw_frame = bytes([0x80] * 320)
        sample_rate = 16000
        frame_ms = 20

        # Warmup
        for _ in range(10):
            decode_ulaw_to_pcm16(ulaw_frame)

        # Measure
        start = time.perf_counter()
        iterations = 1000
        for _ in range(iterations):
            decode_ulaw_to_pcm16(ulaw_frame)
        elapsed = time.perf_counter() - start

        # Calculate throughput in samples/second
        samples_per_frame = (sample_rate * frame_ms) // 1000
        total_samples = iterations * samples_per_frame
        throughput = total_samples / elapsed

        print(f"ulaw decode throughput: {throughput / 1e6:.2f}M samples/sec")
        # Loose floor — bench output is the value we care about, not a hard SLO.
        # 1M samples/sec is ~16x the real-time decode rate of one stream;
        # below this we'd see catastrophic regression worth investigating.
        assert throughput > 1e6, "Should decode >1M samples/sec"

    def test_pcm_encode_frame_latency(self) -> None:
        """Measure audio frame encoding latency."""
        audio_data = bytes(640)  # 20ms @ 16kHz stereo

        # Warmup
        encode_audio_frame(
            MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, 0, 480, 2, 20, audio_data
        )

        # Measure
        latencies: list[float] = []
        for seq in range(1000):
            start = time.perf_counter()
            encode_audio_frame(
                MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, seq, 480, 2, 20, audio_data
            )
            latencies.append((time.perf_counter() - start) * 1e6)

        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]

        print(f"frame encode latency: p50={p50:.2f}µs, p95={p95:.2f}µs")
        assert p50 < 100, "Frame encoding should be <100µs"

    def test_frame_encode_throughput(self) -> None:
        """Measure frame encoding throughput (frames/sec)."""
        audio_data = bytes(640)

        # Warmup
        for _ in range(10):
            encode_audio_frame(
                MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, 0, 480, 2, 20, audio_data
            )

        # Measure
        start = time.perf_counter()
        iterations = 10000
        for seq in range(iterations):
            encode_audio_frame(
                MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, seq, 480, 2, 20, audio_data
            )
        elapsed = time.perf_counter() - start

        throughput = iterations / elapsed
        print(f"frame encode throughput: {throughput:.0f} frames/sec")
        assert throughput > 1e6, "Should encode >1M frames/sec"


@pytest.mark.benchmark
class TestAudioBroadcasterPerformance:
    """Profile AudioBroadcaster relay loop processing."""

    @pytest.mark.asyncio
    async def test_relay_loop_ulaw_decode_latency(self) -> None:
        """Measure end-to-end relay loop with ulaw decode."""
        broadcaster = AudioBroadcaster(None)
        broadcaster._radio = MagicMock()
        broadcaster._radio_codec = AudioCodec.ULAW_1CH
        broadcaster._web_codec = AUDIO_CODEC_PCM16
        broadcaster._sample_rate = 8000
        broadcaster._channels = 1

        # Create mock audio packets (20ms @ 8kHz = 160 bytes ulaw)
        ulaw_frame = bytes([0x80] * 160)

        # Mock subscription
        _packets_sent = []

        async def mock_subscription():
            for i in range(100):
                yield MagicMock(data=ulaw_frame)
                await asyncio.sleep(0)

        broadcaster._subscription = mock_subscription()

        # Mock clients
        client_queue = asyncio.Queue()
        broadcaster._clients = {1: client_queue}
        broadcaster._client_ws = {}

        # Measure relay processing
        start = time.perf_counter()

        # Run relay loop
        relay_task = asyncio.create_task(broadcaster._relay_loop())
        # Let it process packets
        await asyncio.sleep(0.1)
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass

        elapsed = time.perf_counter() - start

        # Count frames processed
        frames_processed = broadcaster._seq
        if frames_processed > 0:
            avg_latency = (elapsed * 1e6) / frames_processed
            print(f"relay loop avg latency (with ulaw): {avg_latency:.2f}µs/frame")

    @pytest.mark.asyncio
    async def test_relay_loop_throughput(self) -> None:
        """Measure relay loop throughput (packets/sec)."""
        broadcaster = AudioBroadcaster(None)
        broadcaster._radio = MagicMock()
        broadcaster._radio_codec = AudioCodec.PCM_1CH_16BIT  # No decode needed
        broadcaster._web_codec = AUDIO_CODEC_PCM16
        broadcaster._sample_rate = 16000
        broadcaster._channels = 1

        # Mock audio packets
        pcm_frame = bytes(320)  # 20ms @ 16kHz

        async def mock_subscription():
            for i in range(1000):
                yield MagicMock(data=pcm_frame)

        broadcaster._subscription = mock_subscription()

        # Mock clients
        client_queue = asyncio.Queue(maxsize=100)
        broadcaster._clients = {1: client_queue}
        broadcaster._client_ws = {}

        # Run and measure
        start = time.perf_counter()
        relay_task = asyncio.create_task(broadcaster._relay_loop())

        # Wait for processing
        await asyncio.sleep(0.5)
        relay_task.cancel()
        try:
            await relay_task
        except asyncio.CancelledError:
            pass

        elapsed = time.perf_counter() - start
        throughput = broadcaster._seq / elapsed

        print(f"relay loop throughput: {throughput:.0f} frames/sec")


class TestAudioStreamingEndToEnd:
    """End-to-end latency measurements."""

    @pytest.mark.benchmark
    def test_full_pipeline_latency(self) -> None:
        """Measure full pipeline: decode → encode → frame."""
        ulaw_frame = bytes([0x80] * 160)
        iterations = 100

        latencies: list[float] = []
        for i in range(iterations):
            start = time.perf_counter()

            # Decode
            pcm_data = decode_ulaw_to_pcm16(ulaw_frame)

            # Encode frame
            _frame = encode_audio_frame(
                MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, i, 160, 1, 20, pcm_data
            )

            latencies.append((time.perf_counter() - start) * 1e6)

        p50 = statistics.median(latencies)
        p95 = sorted(latencies)[int(0.95 * len(latencies))]
        p99 = sorted(latencies)[int(0.99 * len(latencies))]

        print(
            f"full pipeline latency: p50={p50:.2f}µs, p95={p95:.2f}µs, p99={p99:.2f}µs"
        )
        assert p50 < 2000, "Full pipeline should be <2ms"

    @pytest.mark.benchmark
    def test_frame_size_impact(self) -> None:
        """Measure impact of different frame sizes."""
        sizes = [160, 320, 640, 1280]  # 8kHz/16kHz/stereo variations

        for size in sizes:
            audio_data = bytes(size)
            start = time.perf_counter()

            for i in range(1000):
                encode_audio_frame(
                    MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, i, 480, 1, 20, audio_data
                )

            elapsed = time.perf_counter() - start
            avg_latency = (elapsed * 1e6) / 1000

            print(f"frame {size}B: {avg_latency:.2f}µs/frame")

    def test_client_queue_saturation(self) -> None:
        """Measure queue backpressure under load."""
        queue = asyncio.Queue(maxsize=10)
        frame = bytes(648)  # Typical audio frame size

        # Fill queue
        for i in range(10):
            queue.put_nowait(frame)

        # Try to add more
        full_count = 0
        for i in range(100):
            try:
                queue.put_nowait(frame)
            except asyncio.QueueFull:
                full_count += 1

        print(f"queue saturation: {full_count}/100 puts rejected")
        assert full_count > 0, "Queue should experience backpressure"
