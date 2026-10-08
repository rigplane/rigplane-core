"""Integration tests for the SDR scope source in the web server (MOR-3157).

Drives :class:`WebServer` with a :class:`FakeIqSource` injected via
``WebConfig.sdr_source_factory`` through ``ensure_scope_enabled``.
"""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_observation import ObservedPtt
from rigplane.radio_protocol import AudioCapable
from rigplane.radio_state import RadioState
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.types import SdrConfig
from rigplane.types import AudioCodec
from rigplane.web.server import WebConfig, WebServer

_RATE = 2_400_000
_VFO1 = 14_074_000
_VFO2 = 14_124_000  # +50 kHz: view shift, no retune
_SPAN = _RATE // 4  # controller default span: 600 kHz


class _AudioRadio(AudioCapable):
    """Audio-capable radio without a hardware scope (audio-FFT fallback)."""

    # Class default shadows the protocol's read-only property
    # (tests/test_tap_surface pattern).
    audio_bus: object = None

    def __init__(self, *, hardware: bool = False) -> None:
        sub = SimpleNamespace(start=lambda: None, stop=lambda: None)
        self.audio_bus = SimpleNamespace(
            subscribe=lambda name="": sub, subscribe_rx=lambda name="": sub
        )
        self.capabilities = {CAP_AUDIO, CAP_SCOPE} if hardware else {CAP_AUDIO}
        self.radio_state = RadioState()

    @property
    def audio_codec(self):
        return AudioCodec.PCM_1CH_16BIT

    @property
    def audio_sample_rate(self):
        return 48_000


class _Handler:
    def __init__(self) -> None:
        self.frames: list[Any] = []

    def enqueue_frame(self, frame) -> None:
        self.frames.append(frame)


def _observe(path: FieldPath, value: Any) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(
            source="poll_response", provider="test", transport="fake"
        ),
        timestamp_monotonic=time.monotonic(),
        max_age=30.0,
        quality=("confirmed",),
    )


def _make_server(fake: FakeIqSource, *, scope_source: str = "auto") -> WebServer:
    return WebServer(
        _AudioRadio(),
        WebConfig(
            radio_model="IC-7300",
            scope_source=scope_source,
            sdr_config=SdrConfig(device_args="fake"),
            sdr_source_factory=lambda _config: fake,
        ),
    )


def _make_fake() -> FakeIqSource:
    return FakeIqSource(
        sample_rate_hz=_RATE,
        block_size=4096,
        tones=((600_000.0, -6.0),),  # +1/4 rate: lands on the tuned VFO
    )


async def _pump(fake: FakeIqSource) -> None:
    while True:
        fake.pump(2)
        await asyncio.sleep(0.02)


async def _wait_frame(handler: _Handler, since: int, **wish: Any) -> Any:
    """Wait for a frame after ``since`` matching the wish kwargs."""
    deadline = time.monotonic() + wish.pop("timeout_s", 4.0)
    while time.monotonic() < deadline:
        for frame in handler.frames[since:]:
            if not frame.pixels:
                continue
            if "out_of_range" in wish and frame.out_of_range != wish["out_of_range"]:
                continue
            center = (frame.start_freq_hz + frame.end_freq_hz) // 2
            if "center_hz" in wish and abs(center - wish["center_hz"]) > 1_000:
                continue
            span = frame.end_freq_hz - frame.start_freq_hz
            if "min_span_hz" in wish and span < wish["min_span_hz"]:
                continue
            return frame
        await asyncio.sleep(0.05)
    pytest.fail(f"no matching frame after index {since}")


async def _capabilities(server: WebServer) -> dict:
    buf = bytearray()

    async def drain() -> None:
        pass

    writer = SimpleNamespace(buffer=buf, write=buf.extend, drain=drain)
    await server._serve_capabilities(writer)
    payload = buf.decode("ascii", errors="replace")
    return json.loads(payload[payload.index("\r\n\r\n") + 4 :])


@pytest.mark.asyncio
async def test_sdr_scope_follows_vfo_freezes_tx_falls_back_and_recovers() -> None:
    server = _make_server(_make_fake())
    handler = _Handler()
    # Prime the VFO before connecting so the runtime seeds from it.
    server.command_state_store.apply_current(
        _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO1)
    )
    await server.ensure_scope_enabled(handler)
    assert server._sdr_scope_active()
    assert server._sdr_runtime is not None and server._sdr_runtime.started
    fake = server._sdr_runtime._source
    # Controller tuned to VFO − 1/4 bandwidth before open (centre-0
    # blocks carry no tuning and are dropped).
    assert fake.center_freq_hz == 13_474_000
    pumper = asyncio.create_task(_pump(fake))
    try:
        # The tone sits at the absolute VFO1 frequency; a +50 kHz VFO
        # move shifts the window, so the peak moves by 50 kHz of pixels.
        frame1 = await _wait_frame(handler, 0, center_hz=_VFO1, min_span_hz=_SPAN)
        peak1 = max(range(689), key=frame1.pixels.__getitem__)
        server.command_state_store.apply_current(
            _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO2)
        )
        frame2 = await _wait_frame(handler, 0, center_hz=_VFO2, min_span_hz=_SPAN)
        peak2 = max(range(689), key=frame2.pixels.__getitem__)
        assert abs((peak1 - peak2) - round(50_000 / _SPAN * 689)) <= 15, (peak1, peak2)
        caps = await _capabilities(server)
        assert caps["scopeSource"] == "sdr"
        assert caps["sdrAvailable"] is True
        assert caps["scopeConfig"]["defaultSpan"] == _SPAN

        # TX freezes: out-of-range frames repeat the last window pixels.
        server.command_state_store.apply_current(
            _observe(FieldPath.global_("tx_state", "observed_ptt"), ObservedPtt.ON)
        )
        i = len(handler.frames)
        first = (await _wait_frame(handler, i, timeout_s=3, out_of_range=True)).pixels
        second = (await _wait_frame(handler, i, timeout_s=3, out_of_range=True)).pixels
        assert second == first
        server.command_state_store.apply_current(
            _observe(FieldPath.global_("tx_state", "observed_ptt"), ObservedPtt.OFF)
        )
        i = len(handler.frames)
        await _wait_frame(
            handler, i, timeout_s=3, out_of_range=False, min_span_hz=_SPAN
        )

        # SDR drops: the audio FFT takes the channel after the stale window.
        pumper.cancel()
        await asyncio.sleep(2.3)
        assert not server._sdr_scope_active()
        caps = await _capabilities(server)
        assert caps["scopeSource"] == "audio_fft"
        assert caps["sdrAvailable"] is True  # still retrying in the background
        server._update_fft_scope_freq()  # production: state broadcasts sync
        registry = server._audio_broadcaster.taps(STAGE_RX_POST_DSP)
        rng = np.random.default_rng(3157)
        n = len(handler.frames)
        pcm = (rng.uniform(-1, 1, 960) * 5000).astype(np.int16).tobytes()
        for _ in range(10):
            server._audio_fft_scope._last_frame_time = 0.0
            for _ in range(9):
                registry.feed(pcm)
            await asyncio.sleep(0.05)
            if any(
                f.pixels and (f.end_freq_hz - f.start_freq_hz) <= 48_000
                for f in handler.frames[n:]
            ):
                break
        else:
            pytest.fail("audio FFT fallback never reached /api/v1/scope")

        # SDR recovers: frames flow again and the SDR takes over.
        pumper = asyncio.create_task(_pump(fake))
        await _wait_frame(
            handler, len(handler.frames), center_hz=_VFO2, min_span_hz=_SPAN
        )
        deadline = time.monotonic() + 3.0
        while not server._sdr_scope_active() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert server._sdr_scope_active()
        assert (await _capabilities(server))["scopeSource"] == "sdr"

        # The sink protocol has no out-of-range flag: when the VFO leaves
        # the source's tunable range the runtime marks the frames.
        fake._frequency_range = (0, 6_000_000)
        await _wait_frame(handler, len(handler.frames), timeout_s=3, out_of_range=True)
    finally:
        pumper.cancel()
        server._stop_sdr_scope()


@pytest.mark.asyncio
async def test_sdr_scope_lifecycle_and_selection_guards() -> None:
    fake = _make_fake()
    server = _make_server(fake)
    handler = _Handler()
    await server.ensure_scope_enabled(handler)
    runtime = server._sdr_runtime
    assert runtime is not None and runtime.started
    # Last client leaves: the pipeline stops and the source closes.
    server.unregister_scope_handler(handler)
    assert runtime.started is False and fake.is_open is False

    # Explicit hardware override keeps the SDR unselected; without an
    # SDR config the default behaviour is unchanged.
    skipped = _make_server(_make_fake(), scope_source="hardware")
    assert skipped._sdr_runtime is None
    assert (await _capabilities(skipped))["sdrAvailable"] is False
    plain = WebServer(_AudioRadio(), WebConfig(radio_model="IC-7300"))
    assert plain._sdr_runtime is None
    plain_handler = _Handler()
    await plain.ensure_scope_enabled(plain_handler)
    frame = object()
    plain._dispatch_audio_fft_frame(frame)
    assert plain_handler.frames == [frame]  # audio FFT still feeds the channel
    assert (await _capabilities(plain))["scopeSource"] == "audio_fft"

    with pytest.raises(ValueError, match="scope_source"):
        WebConfig(scope_source="matrix")
    with pytest.raises(ValueError, match="--sdr-device"):
        WebConfig(scope_source="sdr")
