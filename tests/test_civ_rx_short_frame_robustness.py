"""Regression tests for CI-V RX robustness against short/partial/echo frames.

MOR-237: the Xiegu X6200 (and a flaky serial line generally) can deliver
truncated frequency frames — e.g. ``FE FE E0 98 00 FD`` (cmd 0x00, zero data
bytes) or ``FE FE E0 98 00 00 00 FD`` (two data bytes). These reached the
5-byte BCD decoder and raised ``ValueError: BCD data must be exactly 5 bytes``,
which was caught but logged on every poll cycle (thousands of debug lines).

These tests assert that such frames are skipped gracefully:
- no exception escapes the dispatch path,
- no state corruption (frequency cache/state untouched),
- a valid 5-byte frame that follows still decodes correctly.
"""

from __future__ import annotations

import logging

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState
from rigplane.types import CivFrame


def _make_radio() -> IcomRadio:
    transport = MockTransport()
    radio = IcomRadio("192.168.1.100")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    radio._radio_state = RadioState()
    return radio


def _freq_frame(data: bytes, *, command: int = 0x00) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=command,
        sub=None,
        data=data,
        receiver=None,
    )


@pytest.mark.parametrize(
    "data",
    [
        b"",  # "got 0" — FE FE E0 98 00 FD
        b"\x00\x00",  # "got 2" — FE FE E0 98 00 00 00 FD
        b"\x00\x00\x00",  # 3 bytes
        b"\x00\x40\x07\x14",  # 4 bytes (alternate-shape probe, still skipped)
        b"\x00\x40\x07\x14\x00\x00",  # 6 bytes (over-long)
    ],
)
@pytest.mark.parametrize("command", [0x00, 0x03])
def test_short_freq_frame_does_not_raise_or_corrupt_state(
    caplog: pytest.LogCaptureFixture, data: bytes, command: int
) -> None:
    radio = _make_radio()
    radio._radio_state.receiver("MAIN").freq = 14_074_000
    radio._last_freq_hz = 14_074_000

    frame = _freq_frame(data, command=command)

    with caplog.at_level(logging.DEBUG, logger="rigplane.runtime._civ_rx"):
        # Both update paths must tolerate the malformed frame.
        radio._civ_runtime._update_state_cache_from_frame(frame)
        radio._civ_runtime._update_radio_state_from_frame(frame)

    # State must be untouched by the malformed frame.
    assert radio._radio_state.receiver("MAIN").freq == 14_074_000
    assert radio._last_freq_hz == 14_074_000

    # No error-level spam (the old code emitted "BCD data must be exactly..."
    # exceptions caught at debug; now we skip without ever invoking the decoder).
    #
    # Scope the assertion to the logger under test (``rigplane.runtime._civ_rx``
    # and its children) only. Capturing global/root logging would let unrelated
    # warnings fail the test — e.g. the ``Radio`` finalizer's "Radio collected
    # with active connection/tasks" warning, which the GC can emit inside this
    # capture window (timing differs across 3.11/3.12/3.13) from a different
    # component entirely. The test's intent is purely about ``_civ_rx``.
    civ_rx_logger = "rigplane.runtime._civ_rx"
    civ_rx_records = [
        record
        for record in caplog.records
        if record.name == civ_rx_logger or record.name.startswith(civ_rx_logger + ".")
    ]
    for record in civ_rx_records:
        assert record.levelno < logging.WARNING, (
            f"unexpected non-debug log: {record.levelname} {record.getMessage()}"
        )
        assert "BCD data must be exactly" not in record.getMessage(), (
            "short freq frame should be skipped before the BCD decoder"
        )


@pytest.mark.parametrize("command", [0x00, 0x03])
def test_valid_freq_frame_still_decodes_after_short_frame(command: int) -> None:
    from rigplane.types import bcd_encode

    radio = _make_radio()

    # A truncated frame first, then a valid 5-byte frame.
    radio._civ_runtime._update_radio_state_from_frame(_freq_frame(b"", command=command))
    radio._civ_runtime._update_state_cache_from_frame(_freq_frame(b"", command=command))

    valid = _freq_frame(bcd_encode(14_074_000), command=command)
    radio._civ_runtime._update_radio_state_from_frame(valid)
    radio._civ_runtime._update_state_cache_from_frame(valid)

    assert radio._radio_state.receiver("MAIN").freq == 14_074_000
    assert radio._last_freq_hz == 14_074_000
