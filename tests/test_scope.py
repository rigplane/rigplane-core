"""Tests for scope/waterfall data parsing and assembly.

Tests cover:
- ScopeAssembler (scope.py) → ScopeFrame
- Scope command builders (commands.py)
- IcomRadio._execute_civ_raw integration (callback via mock transport)
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
import rigplane.commands as raw_commands

from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    get_scope_center_type,
    get_scope_during_tx,
    get_scope_edge,
    get_scope_fixed_edge,
    get_scope_hold,
    get_scope_main_sub,
    get_scope_mode,
    get_scope_rbw,
    get_scope_ref,
    get_scope_single_dual,
    get_scope_span,
    get_scope_speed,
    get_scope_vbw,
    scope_data_output_off,
    scope_data_output_on,
    scope_main_sub,
    scope_off,
    scope_on,
    scope_set_center_type,
    scope_set_during_tx,
    scope_set_edge,
    scope_set_fixed_edge,
    scope_set_hold,
    scope_set_mode,
    scope_set_rbw,
    scope_set_ref,
    scope_set_span,
    scope_set_speed,
    scope_set_vbw,
    scope_single_dual,
)
from rigplane.scope import ScopeAssembler, ScopeFrame
from rigplane.types import bcd_encode
from rigplane.radio import IcomRadio
from _command_test_helpers import bind_default_addr_globals, bind_default_addr_module
from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp

bind_default_addr_module(raw_commands, to_addr=IC_7610_ADDR)
bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)


# ---------------------------------------------------------------------------
# Helpers — build raw payload bytes
# ---------------------------------------------------------------------------


def _bcd_byte(value: int) -> int:
    """Encode a decimal 0-99 as BCD byte."""
    return ((value // 10) << 4) | (value % 10)


def _seq1_payload(
    receiver: int,
    seq: int,
    seq_max: int,
    mode: int,
    start_hz: int,
    end_hz: int,
    oor: bool,
    extra_pixels: bytes = b"",
) -> bytes:
    """Build a sequence-1 payload (after the receiver byte)."""
    return bytes(
        [
            _bcd_byte(seq),
            _bcd_byte(seq_max),
            mode,
            *bcd_encode(start_hz),
            *bcd_encode(end_hz),
            0x01 if oor else 0x00,
            *extra_pixels,
        ]
    )


def _seq_n_payload(
    seq: int,
    seq_max: int,
    pixels: bytes,
) -> bytes:
    """Build a sequence-N (N>1) payload (after the receiver byte)."""
    return bytes([_bcd_byte(seq), _bcd_byte(seq_max), *pixels])


def _scope_civ_frame(
    receiver: int,
    payload_after_receiver: bytes,
    from_addr: int = IC_7610_ADDR,
    to_addr: int = CONTROLLER_ADDR,
) -> bytes:
    """Wrap scope wave data in a full CI-V frame (FE FE to from 27 00 ...)."""
    return build_civ_frame(
        to_addr,
        from_addr,
        0x27,
        sub=0x00,
        data=bytes([receiver]) + payload_after_receiver,
    )


# ---------------------------------------------------------------------------
# Tests for ScopeAssembler → ScopeFrame
# ---------------------------------------------------------------------------


class TestScopeAssemblerSequence1:
    """Sequence 1 (metadata) parsing tests."""

    def test_seq1_fixed_mode_returns_none(self) -> None:
        """Seq 1 with seqMax > 1 does not complete the frame."""
        asm = ScopeAssembler()
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        result = asm.feed(payload, 0)
        assert result is None

    def test_seq1_too_short_returns_none(self) -> None:
        """Truncated sequence 1 payload returns None."""
        asm = ScopeAssembler()
        result = asm.feed(bytes([0x01, 0x03, 0x01]), 0)
        assert result is None

    def test_seq1_empty_returns_none(self) -> None:
        asm = ScopeAssembler()
        result = asm.feed(b"", 0)
        assert result is None

    def test_seq1_oor_returns_immediately(self) -> None:
        """OOR=True on seq 1 returns ScopeFrame immediately with empty pixels."""
        asm = ScopeAssembler()
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=True,
        )
        result = asm.feed(payload, 0)
        assert result is not None
        assert isinstance(result, ScopeFrame)
        assert result.out_of_range is True
        assert result.pixels == b""
        assert result.receiver == 0

    def test_seq1_lan_single_packet(self) -> None:
        """When seq == seqMax == 1, pixels follow immediately (LAN mode)."""
        pixels = bytes([10, 20, 30, 40, 50])
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=pixels,
        )
        asm = ScopeAssembler()
        result = asm.feed(payload, 0)
        assert result is not None
        assert result.pixels == pixels
        assert result.start_freq_hz == 14_000_000
        assert result.end_freq_hz == 14_350_000
        assert result.mode == 1
        assert result.out_of_range is False


class TestScopeAssemblerMultiSequence:
    """Multi-sequence frame assembly tests."""

    def _build_3seq_frame(self, receiver: int = 0, mode: int = 1) -> list[bytes]:
        """Build a 3-sequence scope burst (seq_max=3)."""
        seq1 = _seq1_payload(
            receiver=receiver,
            seq=1,
            seq_max=3,
            mode=mode,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        seq2 = _seq_n_payload(seq=2, seq_max=3, pixels=bytes(range(50)))
        seq3 = _seq_n_payload(seq=3, seq_max=3, pixels=bytes(range(10)))
        return [seq1, seq2, seq3]

    def test_assembles_complete_frame(self) -> None:
        asm = ScopeAssembler()
        payloads = self._build_3seq_frame()
        results = [asm.feed(p, 0) for p in payloads]
        assert results[0] is None
        assert results[1] is None
        final = results[2]
        assert final is not None
        assert isinstance(final, ScopeFrame)

    def test_pixel_data_concatenated(self) -> None:
        asm = ScopeAssembler()
        pixels2 = bytes(range(50))
        pixels3 = bytes(range(10))
        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        seq2 = _seq_n_payload(2, 3, pixels2)
        seq3 = _seq_n_payload(3, 3, pixels3)
        asm.feed(seq1, 0)
        asm.feed(seq2, 0)
        result = asm.feed(seq3, 0)
        assert result is not None
        assert result.pixels == pixels2 + pixels3

    def test_metadata_in_result(self) -> None:
        asm = ScopeAssembler()
        payloads = self._build_3seq_frame(receiver=0, mode=1)
        for p in payloads[:-1]:
            asm.feed(p, 0)
        result = asm.feed(payloads[-1], 0)
        assert result is not None
        assert result.receiver == 0
        assert result.mode == 1
        assert result.start_freq_hz == 14_000_000
        assert result.end_freq_hz == 14_350_000
        assert result.out_of_range is False


class TestScopeAssemblerCenterMode:
    """Center mode frequency calculation tests."""

    def test_center_mode_expands_to_edges(self) -> None:
        """Mode 0 (center): start=center, end=half_span → actual edges."""
        center_hz = 14_175_000
        half_span_hz = 175_000
        asm = ScopeAssembler()
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=0,
            start_hz=center_hz,
            end_hz=half_span_hz,
            oor=False,
            extra_pixels=bytes([50, 60, 70]),
        )
        result = asm.feed(payload, 0)
        assert result is not None
        assert result.mode == 0
        assert result.start_freq_hz == center_hz - half_span_hz
        assert result.end_freq_hz == center_hz + half_span_hz

    def test_fixed_mode_preserves_freqs(self) -> None:
        """Mode 1 (fixed): start/end passed through unchanged."""
        asm = ScopeAssembler()
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=b"\x20",
        )
        result = asm.feed(payload, 0)
        assert result is not None
        assert result.start_freq_hz == 14_000_000
        assert result.end_freq_hz == 14_350_000


class TestScopeAssemblerReceiverIsolation:
    """Main and sub receiver channels are independent."""

    def test_main_sub_independent(self) -> None:
        """Sub receiver activity does not affect main receiver assembly."""
        asm = ScopeAssembler()
        pixels = bytes(range(5))

        seq1_main = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        asm.feed(seq1_main, 0)

        seq1_sub = _seq1_payload(
            receiver=1,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
        )
        asm.feed(seq1_sub, 1)

        seq2_main = _seq_n_payload(2, 2, pixels)
        result_main = asm.feed(seq2_main, 0)
        assert result_main is not None
        assert result_main.receiver == 0
        assert result_main.start_freq_hz == 14_000_000

        seq2_sub = _seq_n_payload(2, 2, pixels)
        result_sub = asm.feed(seq2_sub, 1)
        assert result_sub is not None
        assert result_sub.receiver == 1
        assert result_sub.start_freq_hz == 7_000_000

    def test_sub_oor_does_not_affect_main(self) -> None:
        asm = ScopeAssembler()

        seq1_main = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        asm.feed(seq1_main, 0)

        seq1_sub_oor = _seq1_payload(
            receiver=1,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=0,
            end_hz=0,
            oor=True,
        )
        oor_result = asm.feed(seq1_sub_oor, 1)
        assert oor_result is not None
        assert oor_result.out_of_range is True

        seq2_main = _seq_n_payload(2, 2, bytes([10, 20, 30]))
        result = asm.feed(seq2_main, 0)
        assert result is not None
        assert result.out_of_range is False


class TestScopeAssemblerTimeout:
    """Incomplete frame assembly is discarded after the configured timeout."""

    def test_timeout_discards_partial_frame(self) -> None:
        """seq>1 arriving after timeout returns None and logs a warning."""
        asm = ScopeAssembler(assembly_timeout=5.0)

        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )

        t0 = 1000.0
        with patch("rigplane.scope.time.monotonic", return_value=t0):
            result = asm.feed(seq1, 0)
        assert result is None

        # Feed a middle packet well past the timeout.
        import logging

        t_expired = t0 + 6.0
        with (
            patch("rigplane.scope.time.monotonic", return_value=t_expired),
            patch.object(logging.getLogger("rigplane.scope"), "warning") as mock_warn,
        ):
            seq2 = _seq_n_payload(2, 3, bytes(range(10)))
            result2 = asm.feed(seq2, 0)

        assert result2 is None
        assert mock_warn.called
        call_args = mock_warn.call_args[0]
        assert "6.0" in call_args[0] % call_args[1:]
        assert "5.0" in call_args[0] % call_args[1:]

    def test_timeout_new_frame_starts_fresh(self) -> None:
        """After a timeout discard, seq=1 starts a new assembly normally."""
        asm = ScopeAssembler(assembly_timeout=5.0)

        seq1_old = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        t0 = 1000.0
        with patch("rigplane.scope.time.monotonic", return_value=t0):
            asm.feed(seq1_old, 0)

        # Trigger timeout by sending a middle packet late.
        t_expired = t0 + 10.0
        seq2_old = _seq_n_payload(2, 3, bytes([0xAA] * 5))
        with patch("rigplane.scope.time.monotonic", return_value=t_expired):
            asm.feed(seq2_old, 0)  # discarded by timeout

        # New single-packet frame should assemble correctly.
        pixels = bytes([0x11, 0x22, 0x33])
        seq1_new = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
            extra_pixels=pixels,
        )
        with patch("rigplane.scope.time.monotonic", return_value=t_expired + 0.1):
            result = asm.feed(seq1_new, 0)

        assert result is not None
        assert result.start_freq_hz == 7_000_000
        assert result.pixels == pixels

    def test_custom_timeout_respected(self) -> None:
        """assembly_timeout parameter is used instead of default."""
        asm = ScopeAssembler(assembly_timeout=1.0)

        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        t0 = 0.0
        with patch("rigplane.scope.time.monotonic", return_value=t0):
            asm.feed(seq1, 0)

        # 1.5s > 1.0s timeout → discard.
        seq2 = _seq_n_payload(2, 2, bytes([0x10] * 3))
        with patch("rigplane.scope.time.monotonic", return_value=t0 + 1.5):
            result = asm.feed(seq2, 0)
        assert result is None

    def test_within_timeout_completes_normally(self) -> None:
        """Packets arriving before the timeout assemble into a complete frame."""
        asm = ScopeAssembler(assembly_timeout=5.0)

        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        t0 = 1000.0
        with patch("rigplane.scope.time.monotonic", return_value=t0):
            asm.feed(seq1, 0)

        pixels = bytes([0x10, 0x20, 0x30])
        seq2 = _seq_n_payload(2, 2, pixels)
        # 4.9s < 5.0s timeout → should complete.
        with patch("rigplane.scope.time.monotonic", return_value=t0 + 4.9):
            result = asm.feed(seq2, 0)

        assert result is not None
        assert result.pixels == pixels


class TestScopeAssemblerReset:
    """A new sequence 1 resets the accumulator."""

    def test_new_seq1_resets_state(self) -> None:
        """Starting a new frame (seq=1) discards the previous partial frame."""
        asm = ScopeAssembler()

        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        seq2 = _seq_n_payload(2, 3, bytes([0xAA] * 10))
        asm.feed(seq1, 0)
        asm.feed(seq2, 0)

        seq1_new = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
        )
        asm.feed(seq1_new, 0)

        seq2_new = _seq_n_payload(2, 2, bytes([0x55] * 5))
        result = asm.feed(seq2_new, 0)
        assert result is not None
        assert result.start_freq_hz == 7_000_000
        assert result.pixels == bytes([0x55] * 5)


# ---------------------------------------------------------------------------
# Tests for ScopeAssembler.shed_incomplete
# ---------------------------------------------------------------------------


class TestScopeAssemblerShedIncomplete:
    """shed_incomplete() discards in-progress multi-packet frames."""

    def test_no_incomplete_returns_zero(self) -> None:
        """Fresh assembler has nothing to shed."""
        asm = ScopeAssembler()
        assert asm.shed_incomplete() == 0

    def test_complete_single_packet_not_shed(self) -> None:
        """A single-packet frame completes immediately and is not shed."""
        asm = ScopeAssembler()
        payload = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=bytes([0x80] * 10),
        )
        frame = asm.feed(payload, 0)
        assert frame is not None  # completed
        assert asm.shed_incomplete() == 0  # nothing left to shed

    def test_incomplete_multi_packet_is_shed(self) -> None:
        """An incomplete multi-packet frame is discarded by shed."""
        asm = ScopeAssembler()
        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        asm.feed(seq1, 0)  # starts assembly, not complete
        assert asm.shed_incomplete() == 1

    def test_shed_both_receivers(self) -> None:
        """Incomplete frames on both receivers are shed independently."""
        asm = ScopeAssembler()
        seq1_main = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        seq1_sub = _seq1_payload(
            receiver=1,
            seq=1,
            seq_max=5,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
        )
        asm.feed(seq1_main, 0)
        asm.feed(seq1_sub, 1)
        assert asm.shed_incomplete() == 2

    def test_shed_count_only_incomplete(self) -> None:
        """Only the receiver with an incomplete frame is counted."""
        asm = ScopeAssembler()
        # Main: complete single-packet
        complete = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=bytes([0x80] * 10),
        )
        asm.feed(complete, 0)
        # Sub: incomplete multi-packet
        incomplete = _seq1_payload(
            receiver=1,
            seq=1,
            seq_max=3,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
        )
        asm.feed(incomplete, 1)
        assert asm.shed_incomplete() == 1

    def test_after_shed_new_frame_assembles(self) -> None:
        """After shedding, assembler accepts new frames normally."""
        asm = ScopeAssembler()
        seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
        )
        asm.feed(seq1, 0)
        asm.shed_incomplete()
        # Start a new frame and complete it
        new_seq1 = _seq1_payload(
            receiver=0,
            seq=1,
            seq_max=2,
            mode=1,
            start_hz=7_000_000,
            end_hz=7_300_000,
            oor=False,
        )
        asm.feed(new_seq1, 0)
        seq2 = _seq_n_payload(2, 2, bytes([0x42] * 5))
        frame = asm.feed(seq2, 0)
        assert frame is not None
        assert frame.start_freq_hz == 7_000_000


# ---------------------------------------------------------------------------
# Tests for scope command builders
# ---------------------------------------------------------------------------


class TestScopeCommandBuilders:
    """Verify scope CI-V command bytes."""

    def test_scope_on(self) -> None:
        frame = scope_on()
        assert frame[:2] == b"\xfe\xfe"
        assert frame[4] == 0x27
        assert frame[5] == 0x10
        assert frame[6] == 0x01
        assert frame[-1] == 0xFD

    def test_scope_off(self) -> None:
        frame = scope_off()
        assert frame[4] == 0x27
        assert frame[5] == 0x10
        assert frame[6] == 0x00

    def test_scope_data_output_on(self) -> None:
        frame = scope_data_output_on()
        assert frame[4] == 0x27
        assert frame[5] == 0x11
        assert frame[6] == 0x01

    def test_scope_data_output_off(self) -> None:
        frame = scope_data_output_off()
        assert frame[4] == 0x27
        assert frame[5] == 0x11
        assert frame[6] == 0x00

    def test_scope_main_sub_main(self) -> None:
        frame = scope_main_sub(0)
        assert frame[4] == 0x27
        assert frame[5] == 0x12
        assert frame[6] == 0x00

    def test_scope_main_sub_sub(self) -> None:
        frame = scope_main_sub(1)
        assert frame[4] == 0x27
        assert frame[5] == 0x12
        assert frame[6] == 0x01

    def test_scope_single_dual_single(self) -> None:
        frame = scope_single_dual(False)
        assert frame[4] == 0x27
        assert frame[5] == 0x13
        assert frame[6] == 0x00

    def test_scope_single_dual_dual(self) -> None:
        frame = scope_single_dual(True)
        assert frame[4] == 0x27
        assert frame[5] == 0x13
        assert frame[6] == 0x01

    def test_scope_set_mode(self) -> None:
        frame = scope_set_mode(2)
        assert frame[4] == 0x27
        assert frame[5] == 0x14
        assert frame[6] == 0x02

    def test_scope_set_span(self) -> None:
        frame = scope_set_span(3)  # index 3 = 25000 Hz
        assert frame[4] == 0x27
        assert frame[5] == 0x15
        # BCD-encoded 25000 Hz (little-endian): 00 50 02 00 00
        assert frame[6:11] == bytes([0x00, 0x50, 0x02, 0x00, 0x00])

    def test_scope_set_edge(self) -> None:
        frame = scope_set_edge(4)
        assert frame[4] == 0x27
        assert frame[5] == 0x16
        assert frame[6] == 0x04

    def test_scope_set_hold(self) -> None:
        frame = scope_set_hold(True)
        assert frame[4] == 0x27
        assert frame[5] == 0x17
        assert frame[6] == 0x01

    def test_scope_set_ref(self) -> None:
        # -10.5 dB → [10dB=1, 1dB=0] [0.1dB=5, 0] [sign=minus]
        frame = scope_set_ref(-10.5)
        assert frame[4] == 0x27
        assert frame[5] == 0x19
        assert frame[6:9] == b"\x10\x50\x01"

    def test_scope_set_speed(self) -> None:
        frame = scope_set_speed(1)
        assert frame[4] == 0x27
        assert frame[5] == 0x1A
        assert frame[6] == 0x01

    def test_scope_set_vbw(self) -> None:
        frame = scope_set_vbw(True)
        assert frame[4] == 0x27
        assert frame[5] == 0x1D
        assert frame[6] == 0x01

    def test_scope_set_rbw(self) -> None:
        frame = scope_set_rbw(2)
        assert frame[4] == 0x27
        assert frame[5] == 0x1F
        assert frame[6] == 0x02

    def test_get_scope_main_sub(self) -> None:
        frame = get_scope_main_sub()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x12\xfd"

    def test_get_scope_single_dual(self) -> None:
        frame = get_scope_single_dual()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x13\xfd"

    def test_get_scope_mode(self) -> None:
        frame = get_scope_mode()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x14\xfd"

    def test_get_scope_span(self) -> None:
        frame = get_scope_span()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x15\xfd"

    def test_get_scope_edge(self) -> None:
        frame = get_scope_edge()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x16\xfd"

    def test_get_scope_hold(self) -> None:
        frame = get_scope_hold()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x17\xfd"

    def test_get_scope_ref(self) -> None:
        frame = get_scope_ref()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x19\xfd"

    def test_get_scope_speed(self) -> None:
        frame = get_scope_speed()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1a\xfd"

    def test_get_scope_during_tx(self) -> None:
        frame = get_scope_during_tx()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1b\xfd"

    def test_scope_set_during_tx(self) -> None:
        frame = scope_set_during_tx(True)
        assert frame[4] == 0x27
        assert frame[5] == 0x1B
        assert frame[6] == 0x01

    def test_get_scope_center_type(self) -> None:
        frame = get_scope_center_type()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1c\xfd"

    def test_scope_set_center_type(self) -> None:
        frame = scope_set_center_type(2)
        assert frame[4] == 0x27
        assert frame[5] == 0x1C
        assert frame[6] == 0x02

    def test_get_scope_vbw(self) -> None:
        frame = get_scope_vbw()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1d\xfd"

    def test_get_scope_fixed_edge(self) -> None:
        # IC-7610 NAKs a bare 0x27 0x1E query; it requires a <range><edge>
        # selector. Defaults are range=1, edge=1 (MOR-662).
        frame = get_scope_fixed_edge()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1e\x01\x01\xfd"

    def test_get_scope_fixed_edge_selector(self) -> None:
        frame = get_scope_fixed_edge(range_index=6, edge=4)
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1e\x06\x04\xfd"

    def test_get_scope_fixed_edge_rejects_bad_edge(self) -> None:
        with pytest.raises(ValueError):
            get_scope_fixed_edge(edge=5)
        with pytest.raises(ValueError):
            get_scope_fixed_edge(edge=0)

    def test_scope_set_fixed_edge(self) -> None:
        frame = scope_set_fixed_edge(edge=4, start_hz=14_000_000, end_hz=14_350_000)
        assert frame[4] == 0x27
        assert frame[5] == 0x1E
        assert frame[6] == 0x06
        assert frame[7] == 0x04
        assert frame[8:13] == bcd_encode(14_000_000)
        assert frame[13:18] == bcd_encode(14_350_000)

    def test_get_scope_rbw(self) -> None:
        frame = get_scope_rbw()
        assert frame == b"\xfe\xfe\x98\xe0\x27\x1f\xfd"

    def test_custom_addrs(self) -> None:
        frame = scope_on(to_addr=0x70, from_addr=0xE1)
        assert frame[2] == 0x70
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Integration: IcomRadio._execute_civ_raw with scope callback
# ---------------------------------------------------------------------------


class MockTransport:
    """Minimal mock transport for radio integration tests."""

    def __init__(self) -> None:
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent_packets: list[bytes] = []
        self.my_id: int = 0x00010001
        self.remote_id: int = 0xDEADBEEF
        self.rx_packet_count: int = 0

    async def connect(self, host: str, port: int) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    async def send_tracked(self, data: bytes) -> None:
        self.sent_packets.append(data)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response(self, data: bytes) -> None:
        self._responses.put_nowait(data)


def _ack_udp() -> bytes:
    """Build an ACK CI-V frame wrapped in UDP."""
    civ = build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)
    return _wrap_civ_in_udp(civ)


@pytest.fixture
def radio_with_mock() -> tuple[IcomRadio, MockTransport]:
    t = MockTransport()
    r = IcomRadio("192.168.1.1")
    r._civ_transport = t  # type: ignore[assignment]
    r._ctrl_transport = t  # type: ignore[assignment]
    r._connected = True
    return r, t


class TestRadioScopeCallback:
    """Integration tests: scope frames trigger the callback."""

    @pytest.mark.asyncio
    async def test_scope_frame_triggers_callback(
        self, radio_with_mock: tuple[IcomRadio, MockTransport]
    ) -> None:
        """Scope wave data mixed with ACK does not prevent command response."""
        radio, transport = radio_with_mock
        received: list[ScopeFrame] = []
        radio.on_scope_data(received.append)

        pixels1 = bytes([0x10, 0x20, 0x30])
        pixels2 = bytes([0x40, 0x50])
        seq1_payload = _seq1_payload(
            0, 1, 2, mode=1, start_hz=14_000_000, end_hz=14_350_000, oor=False
        )
        seq2_payload = _seq_n_payload(2, 2, pixels1 + pixels2)

        scope_seq1 = _scope_civ_frame(0, seq1_payload)
        scope_seq2 = _scope_civ_frame(0, seq2_payload)

        transport.queue_response(_wrap_civ_in_udp(scope_seq1))
        transport.queue_response(_wrap_civ_in_udp(scope_seq2))
        transport.queue_response(_ack_udp())

        # Use SET frequency (0x05): this path expects ACK/NAK, unlike GET (0x03)
        # which expects a data response and would not match queued ACK packets here.
        civ_cmd = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(14_074_000)
        )
        response = await radio._execute_civ_raw(civ_cmd)

        assert response.command == 0xFB

        assert len(received) == 1
        frame = received[0]
        assert isinstance(frame, ScopeFrame)
        assert frame.receiver == 0
        assert frame.mode == 1
        assert frame.start_freq_hz == 14_000_000
        assert frame.end_freq_hz == 14_350_000
        assert frame.out_of_range is False
        assert frame.pixels == pixels1 + pixels2

    @pytest.mark.asyncio
    async def test_scope_frame_with_no_callback_does_not_crash(
        self, radio_with_mock: tuple[IcomRadio, MockTransport]
    ) -> None:
        """Scope frames are silently discarded when no callback is registered."""
        radio, transport = radio_with_mock
        assert radio._scope_callback is None

        seq1_payload = _seq1_payload(
            0,
            1,
            1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=bytes([10]),
        )
        scope_frame = _scope_civ_frame(0, seq1_payload)
        transport.queue_response(_wrap_civ_in_udp(scope_frame))
        transport.queue_response(_ack_udp())

        civ_cmd = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(14_074_000)
        )
        response = await radio._execute_civ_raw(civ_cmd)
        assert response.command == 0xFB

    @pytest.mark.asyncio
    async def test_on_scope_data_unregister(
        self, radio_with_mock: tuple[IcomRadio, MockTransport]
    ) -> None:
        """Passing None unregisters the callback."""
        radio, transport = radio_with_mock
        received: list[ScopeFrame] = []
        radio.on_scope_data(received.append)
        radio.on_scope_data(None)
        assert radio._scope_callback is None

        seq1_payload = _seq1_payload(
            0,
            1,
            1,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            oor=False,
            extra_pixels=bytes([10]),
        )
        scope_frame = _scope_civ_frame(0, seq1_payload)
        transport.queue_response(_wrap_civ_in_udp(scope_frame))
        transport.queue_response(_ack_udp())

        civ_cmd = build_civ_frame(
            IC_7610_ADDR, CONTROLLER_ADDR, 0x05, data=bcd_encode(14_074_000)
        )
        await radio._execute_civ_raw(civ_cmd)
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_scope_cmd_0x27_non_wave_not_treated_as_scope_data(
        self, radio_with_mock: tuple[IcomRadio, MockTransport]
    ) -> None:
        """0x27 with sub != 0x00 (e.g. ACK for scope_on) is returned as response."""
        radio, transport = radio_with_mock
        received: list[ScopeFrame] = []
        radio.on_scope_data(received.append)

        transport.queue_response(_ack_udp())

        civ_cmd = scope_on(to_addr=IC_7610_ADDR)
        response = await radio._execute_civ_raw(civ_cmd)
        assert response.command == 0xFB
        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_callback_survives_disconnect(
        self, radio_with_mock: tuple[IcomRadio, MockTransport]
    ) -> None:
        """Callback persists through disconnect (user manages lifecycle)."""
        radio, _transport = radio_with_mock
        received: list[ScopeFrame] = []
        radio.on_scope_data(received.append)
        # Simulate partial disconnect (without real transport teardown)
        radio._connected = False
        assert radio._scope_callback is not None
