"""Audio Scope passband grant, produced by the real server path (MOR-4604).

The browser's audio_fft surfaces all subscribe to the receiver passband plus
a small margin (``audioScopePassbandView`` in the frontend scope adapter).
This test sends that exact subscribe document through the real
``ScopeHandler`` and ``ScopeHub`` into the ``AudioFftSource`` the production
``WebServer`` builds for a PCM radio (its own FFT size, its own PCM tap), feeds
one 1 kHz USB tone through the broadcaster's RX tap, and records every message
the socket receives: the grant(s) and the binary frame.

``tests/fixtures/audio-scope-passband/usb-tone.json`` (text messages, case)
and ``usb-tone.bin`` (the binary frame) are what this test asserts byte for
byte; the browser replay
``frontend/src/components-v2/layout/__tests__/RadioLayout.audio-fft.component.test.ts``
(describe "MOR-4604 Audio Scope beside the audio SpectrumPanel") feeds only
these messages into the mounted panels. Regenerate with
``RIGPLANE_UPDATE_AUDIO_PASSBAND_REPLAY=1``.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from rigplane.audio import fft_scope
from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.testing.timing import ManualClock, await_until
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import decode_scope_frame_v2
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_TEXT
from test_audio_scope_dispatch import _seed_audio_facts
from test_handlers_coverage import _RelayRadio

FIXTURES = Path(__file__).parent / "fixtures" / "audio-scope-passband"
CASE = FIXTURES / "usb-tone.json"
FRAME = FIXTURES / "usb-tone.bin"

TUNED_HZ = 14_100_000
FILTER_HZ = 2400
TONE_HZ = 1000
SAMPLE_RATE = 48_000
# What the browser sends for USB 14.100 MHz, 2400 Hz filter: the passband
# 0..2400 Hz above the carrier plus a 300 Hz margin on each side, at the
# widest surface's backing width (an 800-px audio SpectrumPanel beside a
# 400-px Audio Scope).
SUBSCRIBE = {
    "type": "subscribe",
    "sourceId": "audio_fft",
    "receiver": None,
    "view": {"startHz": 14_099_700, "endHz": 14_102_700},
    "width": 800,
    "maxFps": 20,
}


class _Socket:
    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.text: list[str] = []
        self.binary: list[bytes] = []

    async def recv(self):
        message = await self.incoming.get()
        if message is None:
            raise EOFError
        return WS_OP_TEXT, message.encode()

    async def send_text(self, text: str) -> None:
        self.text.append(text)

    async def send_binary(self, data: bytes) -> None:
        self.binary.append(bytes(data))

    async def close(self) -> None:
        pass


async def _replay() -> tuple[list[dict], bytes]:
    server = WebServer(
        _RelayRadio(sample_rate=SAMPLE_RATE), WebConfig(radio_model="ic7300")
    )
    assert server._audio_scope_source is not None
    _seed_audio_facts(server, mode="USB", frequency=TUNED_HZ, width=FILTER_HZ)
    socket = _Socket()
    handler = ScopeHandler(socket, server)
    task = asyncio.create_task(handler.run())
    try:
        await await_until(lambda: handler in server._scope_handlers)
        await socket.incoming.put(json.dumps(SUBSCRIBE))
        await await_until(lambda: len(socket.text) == 1)
        # Twice the largest FFT window the source could use; the frozen clock
        # lets exactly the first window through the producer's fps limit.
        samples = np.arange(16384)
        pcm = (
            (8000 * np.sin(2 * np.pi * TONE_HZ * samples / SAMPLE_RATE))
            .astype("<i2")
            .tobytes()
        )
        with patch.object(fft_scope, "time", SimpleNamespace(monotonic=ManualClock(1))):
            server._audio_broadcaster.taps(STAGE_RX_POST_DSP).feed(pcm)
        await await_until(lambda: len(socket.binary) == 1)
        return [json.loads(text) for text in socket.text], socket.binary[0]
    finally:
        await socket.incoming.put(None)
        await task


async def test_passband_replay_fixture_is_what_the_real_server_sends() -> None:
    messages, frame = await _replay()
    document = {
        "case": {
            "tunedHz": TUNED_HZ,
            "mode": "USB",
            "filterWidthHz": FILTER_HZ,
            "toneHz": TONE_HZ,
            "sampleRateHz": SAMPLE_RATE,
            "subscribe": SUBSCRIBE,
        },
        "messages": messages,
    }
    if os.environ.get("RIGPLANE_UPDATE_AUDIO_PASSBAND_REPLAY") == "1":
        FIXTURES.mkdir(parents=True, exist_ok=True)
        CASE.write_text(json.dumps(document, indent=2) + "\n")
        FRAME.write_bytes(frame)
        return
    assert document == json.loads(CASE.read_text())
    assert frame == FRAME.read_bytes()


async def test_passband_grant_gives_the_trapezoid_native_resolution() -> None:
    """The acceptance the browser replay renders: enough bins, tone in place."""
    messages, wire = await _replay()
    grants = [message for message in messages if message["type"] == "granted"]
    final = grants[-1]
    assert final["view"] == {"startHz": 14_099_700.0, "endHz": 14_102_700.0}
    frame = decode_scope_frame_v2(wire)
    assert frame.pixels is not None and frame.width == final["width"] == len(
        frame.pixels
    )
    span = frame.view_end_hz - frame.view_start_hz
    passband_columns = frame.width * FILTER_HZ / span
    # The 400-px Audio Scope draws the 2400 Hz passband over about 226 CSS
    # columns; half of them must read distinct bins.
    assert passband_columns >= 113
    peak = int(np.argmax(np.frombuffer(frame.pixels, dtype=np.uint8)))
    peak_hz = frame.view_start_hz + (peak + 0.5) * span / frame.width - TUNED_HZ
    assert abs(peak_hz - TONE_HZ) <= 2 * span / frame.width
