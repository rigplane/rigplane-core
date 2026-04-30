"""Scope/waterfall frame assembly for Icom transceivers.

Wave data arrives as CI-V 0x27/0x00 packets in sequence bursts.
ScopeAssembler reconstructs complete frames from multi-packet sequences.

Reference: wfview icomcommander.cpp parseSpectrum() line 1921.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from icom_lan.types import bcd_decode

__all__ = ["ScopeFrame", "ScopeAssembler"]

_log = logging.getLogger(__name__)

_DEFAULT_ASSEMBLY_TIMEOUT: float = 5.0


@dataclass
class ScopeFrame:
    """Complete spectrum scope frame.

    Attributes:
        receiver: Receiver index (0=main, 1=sub).
        mode: Scope mode (0=center, 1=fixed, 2=scroll-C, 3=scroll-F).
        start_freq_hz: Start frequency in Hz.
        end_freq_hz: End frequency in Hz.
        pixels: Amplitude values as bytes, each 0x00–0xA0 (0–160).
        out_of_range: True if signal is outside the scope display range.
    """

    receiver: int
    mode: int
    start_freq_hz: int
    end_freq_hz: int
    pixels: bytes
    out_of_range: bool


def _bcd_byte_decode(b: int) -> int:
    """Decode a single BCD byte to decimal integer.

    Args:
        b: BCD byte (e.g. 0x11 → 11, 0x15 → 15).

    Returns:
        Decimal value 0–99.
    """
    return ((b >> 4) & 0x0F) * 10 + (b & 0x0F)


class _ReceiverState:
    """Assembly state for one receiver channel (main or sub)."""

    __slots__ = (
        "_mode",
        "_start_freq",
        "_end_freq",
        "_oor",
        "_chunks",
        "_timeout",
        "_start_time",
    )

    def __init__(self, timeout: float = _DEFAULT_ASSEMBLY_TIMEOUT) -> None:
        self._mode: int = 0
        self._start_freq: int = 0
        self._end_freq: int = 0
        self._oor: bool = False
        self._chunks: list[bytes] = []
        self._timeout: float = timeout
        self._start_time: float | None = None

    @property
    def has_incomplete(self) -> bool:
        """True when a multi-packet frame is being assembled (seq 1 received, not yet complete)."""
        return self._start_time is not None

    def _reset(self) -> None:
        self._chunks = []
        self._start_time = None

    def feed(self, raw_payload: bytes, receiver: int) -> ScopeFrame | None:
        """Process one sequence packet.

        Args:
            raw_payload: Bytes starting with [seq_bcd, seqMax_bcd, data...].
                Sequence 1: data = [mode, 5-byte start BCD, 5-byte end BCD, oor, pixels...].
                Sequences 2..seqMax: data = pixel bytes (amplitude 0–160).
            receiver: Receiver index (0=main, 1=sub).

        Returns:
            Complete ScopeFrame when final sequence is received, else None.
        """
        if len(raw_payload) < 2:
            return None

        seq = _bcd_byte_decode(raw_payload[0])
        seq_max = _bcd_byte_decode(raw_payload[1])

        # Discard stale partial assembly if it has exceeded the timeout.
        if self._start_time is not None and seq != 1:
            elapsed = time.monotonic() - self._start_time
            if elapsed > self._timeout:
                _log.warning(
                    "Scope assembly timeout (%.1fs > %.1fs) for receiver %d"
                    " — discarding %d partial chunk(s)",
                    elapsed,
                    self._timeout,
                    receiver,
                    len(self._chunks),
                )
                self._reset()
                return None

        if seq == 1:
            self._reset()
            self._start_time = time.monotonic()
            # Sequence 1 carries metadata: mode, start/end freq, OOR flag.
            # Minimum: 2 (seq/seqMax) + 1 (mode) + 5 (start) + 5 (end) + 1 (oor) = 14
            if len(raw_payload) < 14:
                self._start_time = None
                return None

            self._mode = raw_payload[2]
            self._start_freq = bcd_decode(bytes(raw_payload[3:8]))
            self._end_freq = bcd_decode(bytes(raw_payload[8:13]))
            self._oor = bool(raw_payload[13])

            if self._oor:
                self._start_time = None
                return ScopeFrame(
                    receiver=receiver,
                    mode=self._mode,
                    start_freq_hz=self._start_freq,
                    end_freq_hz=self._end_freq,
                    pixels=b"",
                    out_of_range=True,
                )

            # Center mode: start=center_freq, end=bandwidth.
            # Adjust to real edge frequencies per wfview parseSpectrum().
            if self._mode == 0:
                center = self._start_freq
                bw = self._end_freq
                self._start_freq = center - bw
                self._end_freq = center + bw

            # LAN single-packet mode: seq == seqMax, pixels follow OOR flag.
            if seq == seq_max:
                self._chunks.append(bytes(raw_payload[14:]))
                return self._build_frame(receiver)

            return None

        elif 1 < seq < seq_max:
            # Middle sequences: bytes [2:] are pixel amplitude data.
            self._chunks.append(bytes(raw_payload[2:]))
            return None

        elif seq == seq_max:
            # Last sequence: append remaining pixels and emit complete frame.
            self._chunks.append(bytes(raw_payload[2:]))
            return self._build_frame(receiver)

        return None

    def _build_frame(self, receiver: int) -> ScopeFrame:
        frame = ScopeFrame(
            receiver=receiver,
            mode=self._mode,
            start_freq_hz=self._start_freq,
            end_freq_hz=self._end_freq,
            pixels=b"".join(self._chunks),
            out_of_range=self._oor,
        )
        self._reset()
        return frame


class ScopeAssembler:
    """Assembles multi-sequence scope frames for main and sub receivers.

    Wave data arrives as a burst of CI-V 0x27/0x00 packets numbered
    seq=1..seqMax. This class reassembles those into complete ScopeFrame
    objects, maintaining independent state for each receiver channel.

    IC-7610 parameters: SpectrumSeqMax=15, SpectrumAmpMax=200, SpectrumLenMax=689.

    Usage::

        asm = ScopeAssembler()
        # raw_payload: CI-V frame data after the receiver byte
        # i.e. starting with [seq_bcd, seqMax_bcd, ...]
        frame = asm.feed(raw_payload, receiver=0)
        if frame is not None:
            process_frame(frame)
    """

    def __init__(self, assembly_timeout: float = _DEFAULT_ASSEMBLY_TIMEOUT) -> None:
        """Create a ScopeAssembler.

        Args:
            assembly_timeout: Seconds before an incomplete frame is discarded.
                Defaults to 5.0 seconds.
        """
        self._main = _ReceiverState(timeout=assembly_timeout)
        self._sub = _ReceiverState(timeout=assembly_timeout)

    def feed(self, raw_payload: bytes, receiver: int) -> ScopeFrame | None:
        """Feed one scope sequence packet.

        Args:
            raw_payload: Bytes starting with [seq_bcd, seqMax_bcd, data...].
            receiver: Receiver index (0=main, 1=sub).

        Returns:
            Complete ScopeFrame when final sequence received, else None.
        """
        state = self._sub if receiver else self._main
        return state.feed(raw_payload, receiver)

    def shed_incomplete(self) -> int:
        """Discard incomplete multi-packet scope frames for both receivers.

        Only drops frames that are mid-assembly (seq 1 received but final
        sequence not yet arrived). Complete single-packet frames are never
        in-progress, so they are unaffected.

        Returns:
            Number of receiver channels whose partial frame was discarded
            (0, 1, or 2).
        """
        shed_count = 0
        for state in (self._main, self._sub):
            if state.has_incomplete:
                state._reset()
                shed_count += 1
        if shed_count:
            _log.debug(
                "Scope assembler: shed %d incomplete frame(s) under pressure",
                shed_count,
            )
        return shed_count
