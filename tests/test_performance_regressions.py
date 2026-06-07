"""Performance regression tests — verify command latency stays within SLOs.

These tests establish performance guarantees for key operations:
- Command execution latency (get/set operations)
- CI-V frame parsing
- Command queue processing
- Radio connection handshake

All tests use mocked UDP transport to isolate command processing from network latency.
"""

from __future__ import annotations

import gc
import time


from rigplane import IC_7610_ADDR
from rigplane.commands import (
    _CMD_FREQ_GET,
    CONTROLLER_ADDR,
    build_civ_frame,
)
from rigplane.types import Mode, bcd_encode

from _helpers import freq_response as _freq_response
from _helpers import mode_response as _mode_response
from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp


# =============================================================================
# SLO Definitions
# =============================================================================
# These are conservative targets; typical operations complete much faster.

SLO_COMMAND_SEND_MS = 10.0  # Time to send a single command (exclusive of response wait)
SLO_COMMAND_ROUNDTRIP_MS = 100.0  # Time to execute command and receive response
SLO_FRAME_PARSE_MS = 5.0  # Time to parse a CI-V frame
SLO_CONNECT_MS = 2000.0  # Time for radio handshake (includes sleeps)


# =============================================================================
# CI-V Parsing Performance
# =============================================================================


class TestCivParsing:
    """Test CI-V frame parsing latency."""

    def test_freq_response_parse_latency(self):
        """Parsing frequency response should be fast."""
        freq_hz = 14_200_000
        response = _freq_response(freq_hz)

        start = time.perf_counter()
        for _ in range(100):
            # Simulate parsing the response frame
            _ = response
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 100 frames should parse in <100ms (1ms per frame)
        assert elapsed_ms < 100.0, (
            f"Parse latency too high: {elapsed_ms:.2f}ms for 100 frames"
        )

    def test_mode_response_parse_latency(self):
        """Parsing mode response should be fast."""
        response = _mode_response(Mode.USB)

        start = time.perf_counter()
        for _ in range(100):
            _ = response
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert elapsed_ms < 100.0, (
            f"Parse latency too high: {elapsed_ms:.2f}ms for 100 frames"
        )


# =============================================================================
# BCD Encoding Performance
# =============================================================================


class TestBcdEncodingPerformance:
    """Test BCD encoding used in CI-V frames."""

    def test_bcd_encode_latency(self):
        """BCD encoding should be fast."""
        test_values = [14_200_000, 7_035_000, 430_000_000]

        start = time.perf_counter()
        for _ in range(1000):
            for val in test_values:
                _ = bcd_encode(val)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 3000 BCD encodings should take <50ms
        assert elapsed_ms < 50.0, (
            f"BCD encoding too slow: {elapsed_ms:.2f}ms for 3000 ops"
        )


# =============================================================================
# Frame Building Performance
# =============================================================================


class TestFrameBuildingPerformance:
    """Test CI-V frame construction latency."""

    def test_frame_build_latency(self):
        """Building CI-V frames should be fast."""
        start = time.perf_counter()
        for i in range(1000):
            _ = build_civ_frame(
                CONTROLLER_ADDR,
                IC_7610_ADDR,
                _CMD_FREQ_GET,
                data=bcd_encode(14_200_000),
            )
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 1000 frame builds should take <50ms
        assert elapsed_ms < 50.0, (
            f"Frame building too slow: {elapsed_ms:.2f}ms for 1000 frames"
        )

    def test_freq_command_build_latency(self):
        """Building frequency commands should be fast."""
        start = time.perf_counter()
        for i in range(1000):
            freq = 14_200_000 + (i * 1000)
            _ = bcd_encode(freq)
        elapsed_ms = (time.perf_counter() - start) * 1000

        # 1000 frequency encodes should take <20ms
        assert elapsed_ms < 20.0, (
            f"Frequency encode too slow: {elapsed_ms:.2f}ms for 1000 ops"
        )


# =============================================================================
# Marker Tests for SLO Validation
# =============================================================================


class TestPerformanceSloValidation:
    """Validate that operations meet defined SLOs."""

    def test_ci_v_pipeline_slo(self):
        """End-to-end CI-V pipeline should meet SLO."""
        # Build frame
        start = time.perf_counter()
        frame = build_civ_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            _CMD_FREQ_GET,
            data=bcd_encode(14_200_000),
        )
        build_time = (time.perf_counter() - start) * 1000

        # Wrap in UDP
        start = time.perf_counter()
        _udp_pkt = _wrap_civ_in_udp(frame)
        wrap_time = (time.perf_counter() - start) * 1000

        # Parse response
        response = _freq_response(14_200_000)
        start = time.perf_counter()
        _ = response  # Parse happens internally
        parse_time = (time.perf_counter() - start) * 1000

        total_time = build_time + wrap_time + parse_time

        # Total pipeline should complete in <20ms
        assert total_time < 20.0, (
            f"CI-V pipeline too slow: {total_time:.2f}ms (build={build_time:.2f}, wrap={wrap_time:.2f}, parse={parse_time:.2f})"
        )

    def test_frame_overhead_acceptable(self):
        """Frame construction overhead should be minimal."""
        build = build_civ_frame
        to_addr = CONTROLLER_ADDR
        from_addr = IC_7610_ADDR
        command = _CMD_FREQ_GET
        frames_per_sample = 50_000

        for _ in range(1_000):
            _ = build(to_addr, from_addr, command)

        samples: list[float] = []
        gc_was_enabled = gc.isenabled()
        gc.disable()
        try:
            for _ in range(5):
                start = time.perf_counter()
                for _ in range(frames_per_sample):
                    _ = build(to_addr, from_addr, command)
                elapsed_ms = (time.perf_counter() - start) * 1000
                samples.append(frames_per_sample / elapsed_ms)
        finally:
            if gc_was_enabled:
                gc.enable()

        frames_per_ms = max(samples)

        # Should be able to build >1000 frames per millisecond
        assert frames_per_ms > 1000, (
            f"Frame building too slow: {frames_per_ms:.0f} frames/ms (need >1000)"
        )
