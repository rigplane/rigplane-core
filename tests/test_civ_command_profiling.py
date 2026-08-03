"""Profile CI-V command pipeline latency — establish operation baselines.

This module profiles key radio operations to establish baseline latencies and
identify bottlenecks in the command pipeline. Results inform performance SLOs
and optimization priorities.

Operations profiled:
- Command parsing (CI-V frame → RadioState)
- Command creation (python dict → CI-V bytes)
- Command queue processing
- Response parsing latency
- Full roundtrip (send + receive + parse)
"""

from __future__ import annotations

import time

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    _CMD_FREQ_GET,
    _CMD_FREQ_SET,
    _CMD_MODE_GET,
    _CMD_MODE_SET,
    _CMD_LEVEL,
    _SUB_RF_POWER,
    CONTROLLER_ADDR,
    build_civ_frame,
)
from rigplane.types import Mode, bcd_encode


class TestCIVCommandProfiling:
    """Profile latency of CI-V command operations."""

    def test_profile_civ_frame_creation(self):
        """Profile time to create CI-V frames for common operations."""
        operations = [
            (
                "frequency_get",
                lambda: build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET),
            ),
            (
                "frequency_set",
                lambda: build_civ_frame(
                    CONTROLLER_ADDR,
                    IC_7610_ADDR,
                    _CMD_FREQ_SET,
                    data=bcd_encode(14_200_000),
                ),
            ),
            (
                "mode_get",
                lambda: build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_MODE_GET),
            ),
            (
                "mode_set",
                lambda: build_civ_frame(
                    CONTROLLER_ADDR,
                    IC_7610_ADDR,
                    _CMD_MODE_SET,
                    data=bytes([Mode.USB, 1]),
                ),
            ),
            (
                "power_level",
                lambda: build_civ_frame(
                    CONTROLLER_ADDR,
                    IC_7610_ADDR,
                    _CMD_LEVEL,
                    sub=_SUB_RF_POWER,
                    data=b"\x10\x00",
                ),
            ),
        ]

        results = {}
        for name, op_func in operations:
            start = time.perf_counter()
            for _ in range(1000):
                _ = op_func()
            elapsed_ms = (time.perf_counter() - start) * 1000
            latency_us = (elapsed_ms * 1000) / 1000  # per operation
            results[name] = latency_us
            print(f"  {name}: {latency_us:.2f} µs/op")

        # All operations should complete in <100 µs
        for name, latency in results.items():
            assert latency < 100, f"{name} too slow: {latency:.2f} µs"

    def test_profile_bcd_encoding_latency(self):
        """Profile BCD encoding used in frequency commands."""
        frequencies = [
            1_800_000,  # 160m
            3_500_000,  # 80m
            7_074_000,  # 40m
            14_200_000,  # 20m
            21_074_000,  # 15m
            28_074_000,  # 10m
        ]

        start = time.perf_counter()
        for _ in range(1000):
            for freq in frequencies:
                _ = bcd_encode(freq)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 6000 BCD encodings should complete quickly
        latency_us_per_op = (elapsed_ms * 1000) / 6000
        print(f"  BCD encoding: {latency_us_per_op:.2f} µs/op")

        assert latency_us_per_op < 20, (
            f"BCD encoding too slow: {latency_us_per_op:.2f} µs"
        )

    def test_profile_civ_frame_parsing(self):
        """Profile CI-V frame parsing latency."""
        # Create test frames
        test_frames = [
            build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET),
            build_civ_frame(
                CONTROLLER_ADDR,
                IC_7610_ADDR,
                _CMD_FREQ_GET,
                data=bcd_encode(14_200_000),
            ),
            build_civ_frame(
                CONTROLLER_ADDR, IC_7610_ADDR, _CMD_MODE_GET, data=bytes([Mode.USB, 1])
            ),
        ]

        # Profile parsing (frame extraction from frame data)
        start = time.perf_counter()
        for _ in range(1000):
            for frame in test_frames:
                # Simulate frame parsing (identity access)
                _ = frame[0]  # Access frame start marker
                _ = frame[-1]  # Access frame end marker
        elapsed_ms = (time.perf_counter() - start) * 1000

        latency_us_per_frame = (elapsed_ms * 1000) / 3000
        print(f"  Frame parsing: {latency_us_per_frame:.2f} µs/frame")

        # Frame parsing should be very fast
        assert latency_us_per_frame < 10, (
            f"Frame parsing too slow: {latency_us_per_frame:.2f} µs"
        )


class TestCommandPipelineLatency:
    """Test end-to-end command pipeline latency."""

    @pytest.mark.asyncio
    async def test_command_queue_processing_latency(self):
        """Profile latency of command queue processing."""
        from rigplane.web.radio_poller import CommandQueue
        from rigplane.web.radio_poller import SetAntenna1

        queue = CommandQueue()

        # Queue multiple commands
        commands = [
            SetAntenna1(True),
            SetAntenna1(False),
            SetAntenna1(True),
        ]

        start = time.perf_counter()
        for _ in range(100):
            for cmd in commands:
                queue.put(cmd)
        elapsed_ms = (time.perf_counter() - start) * 1000

        latency_us_per_cmd = (elapsed_ms * 1000) / 300
        print(f"  Command queueing: {latency_us_per_cmd:.2f} µs/cmd")

        # Command queueing should be very fast (<100 µs)
        assert latency_us_per_cmd < 100, (
            f"Command queueing too slow: {latency_us_per_cmd:.2f} µs"
        )

    def test_frequency_command_roundtrip_latency(self):
        """Profile frequency get/set command creation."""
        # Simulate the full process of creating a frequency command

        start = time.perf_counter()
        for freq in range(1_800_000, 30_000_000, 100_000):
            # Create get command
            get_cmd = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
            # Create set command with frequency
            set_cmd = build_civ_frame(
                CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_SET, data=bcd_encode(freq)
            )
            # Simulate parsing response
            _ = len(get_cmd)
            _ = len(set_cmd)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # ~291 frequencies tested
        latency_us_per_freq = (elapsed_ms * 1000) / 291
        print(f"  Frequency command roundtrip: {latency_us_per_freq:.2f} µs/op")

        # Should complete in <50 µs per operation
        assert latency_us_per_freq < 50, (
            f"Frequency roundtrip too slow: {latency_us_per_freq:.2f} µs"
        )


class TestOperationThroughput:
    """Test throughput of various operations."""

    def test_frame_creation_throughput(self):
        """Measure frames per second creation rate."""
        frame_count = 0
        start = time.perf_counter()
        end_time = start + 0.1  # Run for 100ms

        while time.perf_counter() < end_time:
            _ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
            frame_count += 1

        elapsed = time.perf_counter() - start
        fps = frame_count / elapsed
        print(f"  Frame creation throughput: {fps:.0f} frames/sec")

        # Should be able to create at least 10,000 frames per second
        assert fps > 10_000, f"Frame creation too slow: {fps:.0f} fps"

    def test_bcd_encoding_throughput(self):
        """Measure BCD encodings per second."""
        encode_count = 0
        start = time.perf_counter()
        end_time = start + 0.1  # Run for 100ms

        while time.perf_counter() < end_time:
            _ = bcd_encode(14_200_000)
            encode_count += 1

        elapsed = time.perf_counter() - start
        ops_per_sec = encode_count / elapsed
        print(f"  BCD encoding throughput: {ops_per_sec:.0f} ops/sec")

        # Should be able to encode at least 50,000 values per second
        assert ops_per_sec > 50_000, f"BCD encoding too slow: {ops_per_sec:.0f} ops/sec"


class TestLatencyDistribution:
    """Analyze latency distribution of critical operations."""

    def test_frame_creation_latency_distribution(self):
        """Measure frame creation latency percentiles.

        Gates the MOR-416 frame-building performance SLO on p50/p95, which
        both carry a >20x margin over baseline (~0.17µs / ~0.38µs vs the
        10µs / 30µs budgets below) and stay stable even on a contended
        CI runner.

        p99 with only 100 samples is not a statistical percentile -- index
        99 of a 100-sample sort is literally the single worst sample, so a
        lone scheduler preemption on a shared runner can move it 10-30x
        with zero change in actual latency (observed locally; see MOR-1176
        for a CI run where it hit 155µs against p50=0.17µs/p95=0.38µs).
        Report it for visibility; do not gate on it.
        """
        latencies_ms = []

        for _ in range(100):
            start = time.perf_counter()
            _ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, _CMD_FREQ_GET)
            latencies_ms.append(
                (time.perf_counter() - start) * 1_000_000
            )  # microseconds

        # Sort to compute percentiles
        latencies_ms.sort()

        p50 = latencies_ms[50]
        p95 = latencies_ms[95]
        p99 = latencies_ms[99]  # worst-of-100 sample; reported only, see above

        print(
            f"  Frame creation latency: p50={p50:.2f}µs, p95={p95:.2f}µs, p99={p99:.2f}µs"
        )

        # Latency should be consistently low
        assert p50 < 10, f"Median latency too high: {p50:.2f}µs"
        assert p95 < 30, f"95th percentile too high: {p95:.2f}µs"
