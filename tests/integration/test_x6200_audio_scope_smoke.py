"""Hardware-gated smoke test for MOR-241 — X6200 AUDIO SCOPE delivers frames.

Brings up the real ``/api/v1/audio-scope`` wiring against a physical Xiegu
X6200 (serial CI-V + USB RX audio) and asserts that at least one FFT
:class:`ScopeFrame` reaches the audio-scope channel within a few seconds.

OFF BY DEFAULT. This test is double-gated and is skipped in a normal
``uv run pytest tests/`` run:

* it lives in ``tests/integration/`` (CLAUDE.md runs with
  ``--ignore=tests/integration``);
* it carries the ``serial_integration`` marker, so the integration conftest
  skips it unless ``ICOM_SERIAL_DEVICE`` is configured;
* it additionally requires ``RIGPLANE_HW_SMOKE=1`` (mirroring the
  ``RIGPLANE_VALIDATION_ALLOW_HARDWARE`` opt-in style) so it never runs by
  accident even inside a configured integration session.

To run against attached hardware::

    RIGPLANE_HW_SMOKE=1 \
    ICOM_SERIAL_DEVICE=/dev/tty.usbmodemXXXX \
    uv run pytest tests/integration/test_x6200_audio_scope_smoke.py \
        -m serial_integration -o addopts=""
"""

from __future__ import annotations

import asyncio
import os

import pytest

from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.scope import ScopeFrame
from rigplane.web.server import WebConfig, WebServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.serial_integration,
    pytest.mark.skipif(
        os.getenv("RIGPLANE_HW_SMOKE") != "1",
        reason="hardware smoke disabled (set RIGPLANE_HW_SMOKE=1 to run)",
    ),
]


class _RecordingScopeHandler:
    """Minimal audio-scope handler that records frames it is handed."""

    def __init__(self) -> None:
        self.frames: list[ScopeFrame] = []

    def enqueue_frame(self, frame: ScopeFrame) -> None:
        self.frames.append(frame)


@pytest.mark.asyncio
async def test_x6200_audio_scope_delivers_fft_frames_on_real_hardware(
    serial_radio_config: dict,
) -> None:
    """A real X6200 with USB RX audio must relay >=1 FFT frame in a few seconds."""
    radio = create_radio(
        SerialBackendConfig(
            device=serial_radio_config["device"],
            baudrate=serial_radio_config["baudrate"],
            radio_addr=serial_radio_config["radio_addr"],
            model="X6200",
        )
    )
    await radio.connect()
    try:
        assert CAP_AUDIO in radio.capabilities
        assert CAP_SCOPE not in radio.capabilities, "X6200 must have no hardware scope"

        server = WebServer(radio=radio, config=WebConfig())
        assert server._audio_fft_scope is not None

        # Make sure the FFT scope has a valid center frequency so feed_audio's
        # center_freq>0 gate passes.
        freq = await radio.get_frequency()
        assert isinstance(freq, int) and freq > 0
        server._audio_fft_scope.set_center_freq(freq)

        handler = _RecordingScopeHandler()
        # ensure_audio_scope_enabled wires the PCM tap + starts the relay so
        # real RX audio drives the FFT, exactly like a connected browser would.
        await server.ensure_audio_scope_enabled(handler)

        deadline = asyncio.get_event_loop().time() + 5.0
        while not handler.frames and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)

        assert len(handler.frames) >= 1, (
            "no audio-scope FFT frames arrived from real X6200 within 5s"
        )
        assert isinstance(handler.frames[0], ScopeFrame)
    finally:
        await radio.disconnect()
