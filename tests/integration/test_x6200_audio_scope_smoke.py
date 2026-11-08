"""Hardware-gated smoke test for MOR-241 — X6200 AUDIO SCOPE delivers frames.

Brings up an explicit ``audio_fft`` scope subscription against a physical Xiegu
X6200 (serial CI-V + USB RX audio) and asserts that at least one FFT
:class:`ScopeWireFrame` reaches the shared scope channel within a few seconds.

OFF BY DEFAULT. This test is double-gated and is skipped in a normal
``uv run pytest tests/`` run (CLAUDE.md's standard command collects
``tests/integration/`` too, but the markers below keep this one from
executing):

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
from rigplane.scope.models import Subscription, View
from rigplane.web.protocol import decode_scope_frame_v2
from rigplane.web.scope_sources import WebScopePcmTap
from test_audio_scope_dispatch import _seed_audio_facts
from rigplane.web.server import WebConfig, WebServer

pytestmark = [
    pytest.mark.integration,
    pytest.mark.serial_integration,
    pytest.mark.skipif(
        os.getenv("RIGPLANE_HW_SMOKE") != "1",
        reason="hardware smoke disabled (set RIGPLANE_HW_SMOKE=1 to run)",
    ),
]


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
        assert server._audio_scope_source is not None
        rx = radio.radio_state.main
        if not rx.freq or not rx.mode or not rx.filter_width:
            pytest.skip("radio has not reported audio RF passband geometry")
        _seed_audio_facts(
            server, mode=rx.mode, frequency=rx.freq, width=rx.filter_width
        )
        facts = WebScopePcmTap(server).facts()
        subscription = await server.scope_hub.subscribe(
            Subscription(
                "audio_fft",
                None,
                View(
                    facts.tuned_hz + facts.low_offset_hz,
                    facts.tuned_hz + facts.high_offset_hz,
                ),
                1024,
                20,
            )
        )
        try:
            wire = await asyncio.wait_for(subscription.receive(), timeout=5)
            assert decode_scope_frame_v2(wire).source == "audio_fft"
        finally:
            await subscription.close()
    finally:
        await radio.disconnect()
