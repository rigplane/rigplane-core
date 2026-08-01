"""Test-tree import seam for the deterministic serial test doubles.

Transitional re-export shim: tests import ``SerialMockRadio`` (and the
serial framing helpers) from here instead of from the shipping package,
so the double can move out of ``src/`` in a follow-up mechanical-move PR
without touching the test modules again. Once the move lands, this file
holds the actual implementation.
"""

from __future__ import annotations

from rigplane.backends.icom7610.drivers.serial_stub import (
    DeterministicSerialCivLink,
    SerialFrameCodec,
    SerialFrameError,
    SerialFrameOverflowError,
    SerialFrameTimeoutError,
    SerialMockRadio,
)

__all__ = [
    "DeterministicSerialCivLink",
    "SerialFrameCodec",
    "SerialFrameError",
    "SerialFrameOverflowError",
    "SerialFrameTimeoutError",
    "SerialMockRadio",
]
