"""Integration tests for the SDR scope source in the web server (MOR-3157).

Drives :class:`WebServer` with a :class:`FakeIqSource` injected via
``WebConfig.sdr_source_factory`` through ``ensure_scope_enabled``.
MOR-3201 adds the public ``sdr`` status object (schema conformance,
lifecycle, broadcast) and the ``RIGPLANE_SDR_*`` CLI/env wiring.
"""

from __future__ import annotations

import asyncio
import io
import json
import os
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import numpy as np
import pytest

from rigplane.cli import _build_parser, _cmd_web

from rigplane.audio.bus import STAGE_RX_POST_DSP
from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_observation import ObservedPtt
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_protocol import AudioCapable, ScopeCapable
from rigplane.radio_state import RadioState
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.types import SdrConfig
from rigplane.types import AudioCodec
from rigplane.web.radio_poller import RadioPoller
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


class _HwScopeRadio(_AudioRadio, ScopeCapable):
    """IC-7610-class radio with a recordable hardware CI-V scope.

    ``enable_scope``/``restore_scope_session_state`` mirror the session
    the real backend runs: enable turns the scope on, restore (what
    ``DisableScope`` executes) turns it off. Scope-control getters are
    ``AsyncMock`` attributes because ``_fetch_scope_controls`` only
    awaits them after enable (values unused here).
    """

    radio_ready = True

    _CONTROL_GETTERS = (
        "get_scope_receiver",
        "get_scope_dual",
        "get_scope_during_tx",
        "get_scope_center_type",
        "get_scope_mode",
        "get_scope_span",
        "get_scope_edge",
        "get_scope_hold",
        "get_scope_ref",
        "get_scope_speed",
        "get_scope_vbw",
        "get_scope_fixed_edge",
        "get_scope_rbw",
    )

    def __init__(self) -> None:
        super().__init__(hardware=True)
        self.profile = resolve_radio_profile(model="IC-7610")
        self.scope_callback: Any = None
        self.enable_calls: list[dict[str, Any]] = []
        self.disable_calls: list[bool] = []
        self.restored_sessions: list[Any] = []
        self.scope_enabled = False
        for name in self._CONTROL_GETTERS:
            setattr(self, name, AsyncMock(return_value=0))

    def on_scope_data(self, callback: Any) -> None:
        self.scope_callback = callback

    async def enable_scope(self, **kwargs: Any) -> None:
        self.enable_calls.append(kwargs)
        self.scope_enabled = True

    async def disable_scope(self) -> None:
        self.disable_calls.append(True)
        self.scope_enabled = False

    async def get_scope_session_state(self) -> tuple[bool, bool]:
        return (False, False)

    async def restore_scope_session_state(self, state: Any) -> None:
        self.restored_sessions.append(state)
        self.scope_enabled = False

    async def set_scope_during_tx(self, on: bool) -> None:  # noqa: ARG002
        """ScopeCapable member; not exercised on this path."""


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


def _make_server(fake, *, scope_source: str = "auto", factory: Any = None) -> WebServer:
    return WebServer(
        _AudioRadio(),
        WebConfig(
            radio_model="IC-7300",
            scope_source=scope_source,
            sdr_config=SdrConfig(device_args="fake"),
            sdr_source_factory=factory or (lambda _config: fake),
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


async def _feed_audio_frames(server: WebServer, handler: _Handler) -> None:
    """Feed PCM through the RX tap until an audio-FFT frame reaches
    ``/api/v1/scope`` (rate-limited FFT: reset time, 9 chunks/attempt;
    spans ≤ 48 kHz mark audio frames)."""
    server._update_fft_scope_freq()  # production: state broadcasts sync
    registry = server._audio_broadcaster.taps(STAGE_RX_POST_DSP)
    rng = np.random.default_rng(3157)
    pcm = (rng.uniform(-1, 1, 960) * 5000).astype(np.int16).tobytes()
    for _ in range(10):
        server._audio_fft_scope._last_frame_time = 0.0
        for _ in range(9):
            registry.feed(pcm)
        await asyncio.sleep(0.05)
        if any(
            f.pixels and (f.end_freq_hz - f.start_freq_hz) <= 48_000
            for f in handler.frames
        ):
            return
    pytest.fail("audio FFT never reached /api/v1/scope")


@pytest.mark.asyncio
async def test_sdr_scope_follows_vfo_freezes_tx_falls_back_and_recovers() -> None:
    server = _make_server(_make_fake())
    handler = _Handler()
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
        await _feed_audio_frames(server, handler)

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


# ---------------------------------------------------------------------------
# Runtime SDR↔hardware-scope fallback (MOR-3202)
# ---------------------------------------------------------------------------


class _HwFrame:
    """Marker object standing in for one radio-delivered hardware frame."""

    pixels = b"\x01"


async def _drain_command_queue(server: WebServer) -> None:
    """Service the ordered command queue like the RadioPoller consumer
    does in production (the ``_drive_scope_pipeline`` pattern from
    tests/test_web_server.py, as a background loop for tests that wait
    on the server's own SDR tick task)."""
    poller = RadioPoller(
        server._radio,  # type: ignore[arg-type]
        server._command_queue,
        radio_state=server._radio_state,
    )
    server._radio_poller = poller
    while True:
        for entry in server._command_queue.drain_entries():
            try:
                await poller._execute(entry.command)
            except Exception as exc:
                if entry.future is not None and not entry.future.done():
                    entry.future.set_exception(exc)
            else:
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)
        await asyncio.sleep(0.01)


async def _wait_hardware_fallback(server: WebServer, radio: _HwScopeRadio) -> None:
    """Wait until the stale SDR handed /api/v1/scope to the hardware
    scope: CI-V enable executed, session confirmed, callback wired."""
    deadline = time.monotonic() + 6.0
    while not (radio.scope_enabled and server._scope_enabled):
        if time.monotonic() >= deadline:
            pytest.fail(
                f"hardware scope never enabled after SDR went stale "
                f"(scope_enabled={radio.scope_enabled}, "
                f"server_enabled={server._scope_enabled}, "
                f"enable_calls={radio.enable_calls})"
            )
        await asyncio.sleep(0.05)


async def test_sdr_stale_on_hardware_scope_radio_falls_back_and_recovers() -> None:
    """MOR-3202: with ``--sdr-device`` on a hardware-scope radio, the SDR
    owns ``/api/v1/scope`` while its frames flow; a stale SDR hands the
    channel to the hardware scope through the ordinary enable path (no
    client reconnect needed); on recovery hardware frames are gated out
    while the radio's scope stays enabled — no interleaving."""
    fake = _make_fake()
    radio = _HwScopeRadio()
    server = WebServer(
        radio,
        WebConfig(
            radio_model="IC-7610",
            sdr_config=SdrConfig(device_args="fake"),
            sdr_source_factory=lambda _config: fake,
        ),
    )
    handler = _Handler()
    server.command_state_store.apply_current(
        _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO1)
    )
    await server.ensure_scope_enabled(handler)
    drainer = asyncio.create_task(_drain_command_queue(server))
    pumper = asyncio.create_task(_pump(fake))
    try:
        # SDR frames flow: the SDR alone owns /api/v1/scope; the
        # hardware scope is never enabled nor wired.
        await _wait_frame(handler, 0, center_hz=_VFO1, min_span_hz=_SPAN)
        assert server._sdr_scope_active()
        assert radio.scope_callback is None
        assert radio.enable_calls == []

        # SDR silent past the stale window: the tick loop enables the
        # hardware scope; its frames reach /api/v1/scope.
        pumper.cancel()
        await _wait_hardware_fallback(server, radio)
        assert len(radio.enable_calls) == 1
        assert radio.scope_callback is not None
        hw1 = _HwFrame()
        radio.scope_callback(hw1)
        assert handler.frames[-1] is hw1

        # SDR recovers: SDR frames return, hardware frames are dropped
        # at the gate, and the radio's scope is NOT disabled over CI-V.
        pumper = asyncio.create_task(_pump(fake))
        await _wait_frame(
            handler, len(handler.frames), center_hz=_VFO1, min_span_hz=_SPAN
        )
        deadline = time.monotonic() + 3.0
        while not server._sdr_scope_active() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert server._sdr_scope_active()
        hw2 = _HwFrame()
        radio.scope_callback(hw2)
        await asyncio.sleep(0.1)
        assert hw2 not in handler.frames
        assert radio.disable_calls == []
        assert radio.restored_sessions == []
        await _wait_frame(
            handler, len(handler.frames), center_hz=_VFO1, min_span_hz=_SPAN
        )
    finally:
        pumper.cancel()
        drainer.cancel()
        server._stop_sdr_scope()


async def test_sdr_silent_from_start_falls_back_to_hardware_scope() -> None:
    """MOR-3202: a started SDR that never delivers a frame goes stale on
    the first tick transition — the hardware scope takes over without a
    client reconnect."""
    fake = _make_fake()
    radio = _HwScopeRadio()
    server = WebServer(
        radio,
        WebConfig(
            radio_model="IC-7610",
            sdr_config=SdrConfig(device_args="fake"),
            sdr_source_factory=lambda _config: fake,
        ),
    )
    handler = _Handler()
    server.command_state_store.apply_current(
        _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO1)
    )
    await server.ensure_scope_enabled(handler)
    drainer = asyncio.create_task(_drain_command_queue(server))
    try:
        # No pump: the source is open but silent. Right after start the
        # runtime still counts as active (seeded by started_at), so the
        # connect path must not touch the hardware scope either.
        assert server._sdr_scope_active()
        assert radio.enable_calls == []
        await _wait_hardware_fallback(server, radio)
        assert len(radio.enable_calls) == 1
        hw = _HwFrame()
        radio.scope_callback(hw)
        assert handler.frames[-1] is hw
    finally:
        drainer.cancel()
        server._stop_sdr_scope()


# ---------------------------------------------------------------------------
# Public ``sdr`` status object (MOR-3201)
# ---------------------------------------------------------------------------

_DISABLED_SDR = {
    "state": "disabled",
    "device": "",
    "sampleRateHz": 0,
    "spanHz": 0,
    "txFrozen": False,
    "overflowCount": 0,
    "lastError": None,
}


async def _wait_sdr(
    server: WebServer, want: Any, description: str, timeout_s: float = 4.0
) -> dict[str, Any]:
    """Poll the public payload until its ``sdr`` object satisfies *want*."""
    deadline = time.monotonic() + timeout_s
    sdr: dict[str, Any] = {}
    while time.monotonic() < deadline:
        sdr = server.build_public_state(updated_at="t")["sdr"]
        if want(sdr):
            return sdr
        await asyncio.sleep(0.05)
    pytest.fail(f"sdr status never {description}: {sdr}")


def _drained_sdr_states(queue: BoundedQueue[dict[str, Any]]) -> list[str]:
    """``sdr.state`` values carried by queued state_update events."""
    states: list[str] = []
    while True:
        try:
            event = queue.get_nowait()
        except asyncio.QueueEmpty:
            return states
        if event.get("type") != "state_update":
            continue
        data = event["data"].get("data") or event["data"].get("changed") or {}
        sdr = data.get("sdr")
        if isinstance(sdr, dict):
            states.append(sdr["state"])


async def test_sdr_status_object_disabled_without_sdr() -> None:
    """Without the SDR runtime the state carries ``sdr.state ==
    "disabled"`` and nothing else in the payload changes: dropping the
    runtime yields an otherwise identical payload modulo the seq bump
    the delivery-key change causes and the wall-clock ``sinceMs``."""
    pytest.importorskip("pydantic")
    from rigplane.web.state_schema import ServerStatePublic

    server = _make_server(_make_fake())
    with_runtime = server.build_public_state(updated_at="t")
    assert with_runtime["sdr"] == {
        **_DISABLED_SDR,
        "device": "fake",
        "sampleRateHz": _RATE,
    }
    server._sdr_runtime = None
    without_runtime = server.build_public_state(updated_at="t")
    assert without_runtime["sdr"] == _DISABLED_SDR

    assert sorted(with_runtime) == sorted(without_runtime)
    for key in ("sdr", "publicStateSeq"):
        with_runtime.pop(key)
        without_runtime.pop(key)
    with_runtime["radioHealth"].pop("sinceMs")
    without_runtime["radioHealth"].pop("sinceMs")
    assert with_runtime == without_runtime
    # The intact (un-mangled) payload conforms to the canonical schema.
    ServerStatePublic.model_validate(server.build_public_state())


async def test_sdr_status_object_lifecycle_and_broadcast() -> None:
    pytest.importorskip("pydantic")
    from rigplane.web.state_schema import ServerStatePublic

    fake = _make_fake()
    server = _make_server(fake)
    handler = _Handler()
    server.command_state_store.apply_current(
        _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO1)
    )
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(64)
    server._control_event_queues.add(queue)

    # Configured but not started: disabled with the configured device.
    assert server.build_public_state(updated_at="t")["sdr"]["device"] == "fake"
    await server.ensure_scope_enabled(handler)
    assert (await _wait_sdr(server, lambda s: s["state"] == "starting", "starting"))[
        "spanHz"
    ] == _SPAN

    pumper = asyncio.create_task(_pump(fake))
    try:
        await _wait_sdr(server, lambda s: s["state"] == "streaming", "streaming")
        # The TX freeze surfaces in the status object via the controller.
        server.command_state_store.apply_current(
            _observe(FieldPath.global_("tx_state", "observed_ptt"), ObservedPtt.ON)
        )
        await _wait_sdr(server, lambda s: s["txFrozen"] is True, "TX-frozen")
    finally:
        pumper.cancel()
        server._stop_sdr_scope()

    # Last viewer left: back to disabled, counters zeroed.
    stopped = await _wait_sdr(server, lambda s: s["state"] == "disabled", "disabled")
    assert stopped["device"] == "fake"
    assert stopped["overflowCount"] == 0

    # The transitions were broadcast to the control channel.
    states = _drained_sdr_states(queue)
    assert {"starting", "streaming", "disabled"} <= set(states)
    ServerStatePublic.model_validate(server.build_public_state())


async def test_sdr_start_failure_falls_back_not_breaks_scope(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failing source factory must not break ``/api/v1/scope``:
    audio-FFT frames still flow, status reads ``error``/``lastError``,
    and the next connect retries the start."""
    calls: list[int] = []

    def _boom(_config: SdrConfig) -> Any:
        calls.append(1)
        raise RuntimeError("boom")

    server = _make_server(_make_fake(), factory=_boom)
    handler = _Handler()
    server.command_state_store.apply_current(
        _observe(FieldPath.active("0", "freq_mode", "freq_hz"), _VFO1)
    )
    await server.ensure_scope_enabled(handler)
    await server.ensure_scope_enabled(handler)
    assert len(calls) == 2
    assert sum("start failed" in r.getMessage() for r in caplog.records) == 1
    sdr = server.build_public_state(updated_at="t")["sdr"]
    assert sdr["state"] == "error" and sdr["lastError"] == "boom"
    assert server._sdr_tick_task is None  # no tick loop after a failure
    await _feed_audio_frames(server, handler)


# ---------------------------------------------------------------------------
# RIGPLANE_SDR_* env fallback through the CLI (MOR-3201)
# ---------------------------------------------------------------------------


class _CaptureWebServer:
    def __init__(self, _radio: Any, cfg: Any) -> None:
        self.captured_config = cfg
        self._runtime_log_path = None

    async def serve_forever(self, *, on_started: Any = None) -> None:
        on_started()
        raise asyncio.CancelledError


async def _run_cmd_web(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], *cli_flags: str
) -> tuple[Any, dict[str, Any]]:
    """Run ``rigplane web`` with *env* and the WebServer class replaced
    by a config capturer; return ``(exit code, config)``."""
    for var in list(os.environ):
        if var.startswith("RIGPLANE_"):
            monkeypatch.delenv(var)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    with patch("sys.stderr", new_callable=io.StringIO):
        args = _build_parser().parse_args(["web", *cli_flags])
    args.web_rigctld = False
    captured: dict[str, Any] = {}

    def _capture(radio_arg: Any, cfg: Any) -> _CaptureWebServer:
        server = _CaptureWebServer(radio_arg, cfg)
        captured["config"] = cfg
        return server

    with patch("rigplane.web.server.WebServer", _capture):
        code = await _cmd_web(AsyncMock(), args)
    return code, captured.get("config")


async def test_cli_env_falls_back_and_flags_win(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Env alone configures the SDR; a given CLI flag wins over env."""
    code, cfg = await _run_cmd_web(
        monkeypatch,
        {
            "RIGPLANE_SCOPE_SOURCE": "hardware",
            "RIGPLANE_SDR_DEVICE": "driver=env",
            "RIGPLANE_SDR_SAMPLE_RATE": "1000000",
            "RIGPLANE_SDR_GAIN": "32.5",
            "RIGPLANE_SDR_PPM": "1.5",
            "RIGPLANE_SDR_SETTINGS": "direct_samp=2",
        },
        "--scope-source",
        "sdr",
        "--sdr-device",
        "driver=cli",
        "--sdr-sample-rate",
        "2400000",
    )
    assert code == 0
    assert cfg.sdr_config == SdrConfig(
        device_args="driver=cli",  # CLI wins over RIGPLANE_SDR_DEVICE
        sample_rate_hz=2_400_000,  # CLI wins over RIGPLANE_SDR_SAMPLE_RATE
        gain_db=32.5,  # env fallback (no flag given)
        ppm=1.5,
        extra_settings={"direct_samp": "2"},
    )
    assert cfg.scope_source == "sdr"  # CLI wins over RIGPLANE_SCOPE_SOURCE


async def test_cli_bad_env_value_fails_naming_the_variable(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _cfg = await _run_cmd_web(monkeypatch, {"RIGPLANE_SDR_PPM": "lots"})
    assert code == 1
    assert "RIGPLANE_SDR_PPM" in capsys.readouterr().err
