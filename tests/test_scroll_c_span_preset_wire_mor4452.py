"""MOR-4452: a trimmed SCROLL-C wave reaches ``/api/v1/scope`` as the span preset's window.

At the ±50 kHz span preset an Icom radio's SCROLL-C (mode 2) waveform
reports the extent of its sweep points — ±49.25 kHz — a few trimmed bins
inside the ±50 kHz display width the preset selects. The source expands
that sweep extent to the preset width (``RadioScopeSource._preset_window``),
the same "bins span the preset width" rule CENTER mode already gets, so the
wire view and coverage — and the panel's SPAN readout and edge legends —
name the preset, never the trimmed extent.

This is the real journey: the simulated radio streams the trimmed CI-V
``27 00`` wave, the real ``IcomRadio`` assembles it, the real ``WebServer``
serves it, and a real WebSocket client of ``/api/v1/scope`` decodes what
the handler sends. The delivered frame is asserted equal to the shared
golden documents ``tests/fixtures/scope-scroll-c/<profile>.bin|.json``
(the per-delivery sequence counter is pinned; the source's epoch and sent
time are already the deterministic 0/None). Regenerate with
``RIGPLANE_UPDATE_SCROLL_C_SCOPE_WIRE=1``.

The paired frontend test replays those same documents into a mounted
SpectrumPanel and asserts the labels:
``frontend/src/components/spectrum/__tests__/SpectrumPanel.component.test.ts``
(``reads ±50k at the span preset in SCROLL-C on %s (MOR-4452)``). Reverting
``_preset_window`` turns this test red (the wire carries the trimmed
98.5 kHz window, never the 100 kHz preset window this journey waits for);
dropping the expansion's effect from the document turns the frontend test
red.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict, replace
from pathlib import Path

import aiohttp
import pytest
from test_web_server import _addr

from rigplane.core.types import bcd_encode
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeAssembler
from rigplane.scope.models import FollowView, Subscription
from rigplane.testing.timing import await_until
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    decode_scope_granted,
    encode_scope_frame_v2,
    encode_scope_subscription,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio

_FIXTURES = Path(__file__).parent / "fixtures" / "scope-scroll-c"
_UPDATE = "RIGPLANE_UPDATE_SCROLL_C_SCOPE_WIRE"
_DIAL_HZ = 14_050_000
#: The sweep extent the radio reports at the ±50 kHz preset (MOR-4452).
_TRIMMED_HALF_HZ = 49_250
_PRESET_HALF_HZ = 50_000
_CONTROLLER_ADDR = 0xE0

#: (profile model, CI-V address, recorded 19 00 model id) per Icom profile.
_PROFILES = [
    ("IC-705", 0xA4, b"\xa4"),
    ("IC-7300", 0x94, b"\x94"),
]


def _scroll_c_wave(radio_addr: int) -> bytes:
    """A mode-2 (SCROLL-C) wave at the ±50 kHz preset: the reported window
    is the trimmed sweep extent ±49.25 kHz, not the preset's ±50 kHz.

    Same byte shape as the recorded single-packet templates the simulator
    replays: ``FE FE to from 27 00`` then receiver, seq, seqmax, mode, the
    two 5-byte little-endian BCD edges, the out-of-range byte and the bins
    (there is no production wave builder; the simulator replays bytes).
    """
    start = _DIAL_HZ - _TRIMMED_HALF_HZ
    end = _DIAL_HZ + _TRIMMED_HALF_HZ
    return (
        b"\xfe\xfe"
        + bytes((_CONTROLLER_ADDR, radio_addr))
        + b"\x27\x00"  # scope waveform data
        + b"\x00"  # receiver 0 (MAIN)
        + b"\x01\x01"  # single-packet wave (seq 1 of 1)
        + b"\x02"  # mode 2: SCROLL-C
        + bcd_encode(start)
        + bcd_encode(end)
        + b"\x00"  # in range
        + bytes(475)
        + b"\xfd"
    )


def test_scroll_c_wave_reports_the_trimmed_sweep_extent() -> None:
    """The injected hardware wave really is trimmed: ±49.25 kHz of sweep
    points at the ±50 kHz preset, through the production assembler."""
    for radio_addr in (0xA4, 0x94):
        wave = _scroll_c_wave(radio_addr)
        frame = ScopeAssembler().feed(wave[7:-1], wave[6])
        assert frame is not None and frame.mode == 2
        assert not frame.out_of_range
        assert (frame.start_freq_hz, frame.end_freq_hz) == (
            _DIAL_HZ - _TRIMMED_HALF_HZ,
            _DIAL_HZ + _TRIMMED_HALF_HZ,
        )
        assert len(frame.pixels) == 475


class _ScrollCScopeFake(MockIcomRadio):
    """An Icom LAN personality streaming the trimmed SCROLL-C wave.

    Every fact this journey needs is a base-simulator knob: the radio
    address, the five-byte half-width ``27 15`` span register, and
    ``27 00`` wave replay. The radio boots on the ±25 kHz preset, so the
    subscription's ±50 kHz request must arrive as a span SET. Two
    registers the base does not model are answered here so the session's
    receiver capture reads real values instead of degrading (the shape
    the G7250 journey's fake uses): mode ``27 14`` and edge ``27 16``.
    The scope templates are replaced by the trimmed wave — the recorded
    IC-7610 templates are all centre-mode.
    """

    def __init__(self, radio_addr: int, model_id: bytes) -> None:
        super().__init__(
            radio_addr=radio_addr,
            span_preset_semantics="half_width",
            scope_stream=True,
            capture_civ_frames=True,
        )
        self.model_id = model_id
        self.scope_span_wire_hz = 25_000  # boots at ±25 kHz
        self.scope_mode = 2  # SCROLL-C, matching the streamed wave
        self.scope_edge = 1
        self._scope_templates[0] = [_scroll_c_wave(radio_addr)]

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        if not payload:
            return super()._dispatch_scope(payload, from_addr)
        sub, rest = payload[0], payload[1:]
        if sub in (0x14, 0x16) and rest[:1] == b"\x00" and len(rest) in (1, 2):
            if len(rest) == 1:  # GET: the radio answers sub + receiver + value
                value = self.scope_mode if sub == 0x14 else self.scope_edge
                return self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x27,
                    data=payload[:2] + bytes([value]),
                )
            if sub == 0x14:  # SET: kept, and confirmed like every write
                self.scope_mode = rest[1]
            else:
                self.scope_edge = rest[1]
            return self._civ_ack(from_addr, self._radio_addr)
        return super()._dispatch_scope(payload, from_addr)


@pytest.mark.realtime(reason="real LAN simulator, radio and scope websocket")
@pytest.mark.timeout(60)
@pytest.mark.parametrize(
    ("model", "radio_addr", "model_id"), _PROFILES, ids=[p[0] for p in _PROFILES]
)
async def test_trimmed_scroll_c_wave_streams_the_span_preset_window(
    model: str, radio_addr: int, model_id: bytes
) -> None:
    """A trimmed SCROLL-C wave through the real ``/api/v1/scope`` WebSocket
    comes out as the ±50 kHz preset's full 100 kHz window.

    Without the MOR-4452 expansion the wire carries the trimmed 98.5 kHz
    sweep extent and no 100 kHz frame ever arrives: this test fails on the
    old code. The delivered document is the shared golden the paired
    frontend test replays.
    """
    sim = _ScrollCScopeFake(radio_addr, model_id)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    received = None
    try:
        await radio.connect()
        radio._radio_state.main.freq = _DIAL_HZ  # the simulator's dial
        await server.start()
        host, port = _addr(server)
        async with aiohttp.ClientSession() as client:
            async with client.ws_connect(f"http://{host}:{port}/api/v1/scope") as ws:
                await ws.send_str(
                    encode_scope_subscription(
                        Subscription(
                            "radio", 0, FollowView("vfo", 2 * _PRESET_HALF_HZ), 475, 20
                        )
                    )
                )
                message = await ws.receive(timeout=10)
                assert message.type is aiohttp.WSMsgType.TEXT, message.type
                granted = decode_scope_granted(message.data)
                assert (granted.view.start_hz, granted.view.end_hz) == (
                    _DIAL_HZ - _PRESET_HALF_HZ,
                    _DIAL_HZ + _PRESET_HALF_HZ,
                )
                # The ±50 kHz request reaches the radio as a span SET.
                assert await await_until(
                    lambda: sim.scope_span_wire_hz == _PRESET_HALF_HZ,
                    timeout=10,
                    description="the ±50 kHz span SET reaching the radio",
                )
                async with asyncio.timeout(10):
                    while True:  # a trimmed 98.5 kHz frame is not the window
                        message = await ws.receive(timeout=10)
                        if message.type is aiohttp.WSMsgType.TEXT:
                            assert '"type":"error"' not in message.data
                            continue
                        assert message.type is aiohttp.WSMsgType.BINARY
                        frame = decode_scope_frame_v2(message.data)
                        if (
                            frame.coverage_end_hz - frame.coverage_start_hz
                            == 2 * _PRESET_HALF_HZ
                        ):
                            received = frame
                            break
    finally:
        await server.stop()
        await radio.disconnect()
        await sim.stop()

    assert received is not None
    # The preset-width geometry the panel labels from.
    assert (received.view_start_hz, received.view_end_hz) == (
        _DIAL_HZ - _PRESET_HALF_HZ,
        _DIAL_HZ + _PRESET_HALF_HZ,
    )
    assert (received.coverage_start_hz, received.coverage_end_hz) == (
        received.view_start_hz,
        received.view_end_hz,
    )
    assert received.pixels == bytes(475)
    # The per-delivery sequence counter depends on arrival timing; pin it
    # like the paired goldens pin their volatile fields.
    delivered = replace(received, seq=0)
    name = model.lower().replace("-", "")
    bin_path = _FIXTURES / f"{name}.bin"
    json_path = _FIXTURES / f"{name}.json"
    wire = encode_scope_frame_v2(delivered)
    document = asdict(delivered)
    assert delivered.pixels is not None
    document["pixels"] = list(delivered.pixels)
    if os.environ.get(_UPDATE) == "1":
        _FIXTURES.mkdir(exist_ok=True)
        bin_path.write_bytes(wire)
        # One line per sidecar: pixels stay a JSON array (same contract as
        # the scope-wire goldens), without one-number-per-line printing.
        json_path.write_text(json.dumps(document, separators=(",", ":")) + "\n")
        return
    assert wire == bin_path.read_bytes()
    assert json.loads(json_path.read_text()) == document
