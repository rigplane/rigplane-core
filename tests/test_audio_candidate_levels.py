"""Unit tests for the pairing-candidate live RX level probe (MOR-3823).

FakeAudioBackend only (project rule): probe-level tests inject a driver
factory bound to the fake backend; web-handler tests monkeypatch
``usb_driver.PortAudioBackend`` like the pairing integration suite, so the
default factory's ``UsbAudioDriver`` opens the same fake streams.
"""

from __future__ import annotations

import asyncio
import json
import math
import struct
from collections.abc import Callable

from test_web_audio_session_rx import _SessionLanRadio
from test_web_auth_compare_digest import _MemoryWriter

from rigplane.audio import usb_driver
from rigplane.audio._usb_pairing import (
    AudioCandidate,
    PairRefused,
    WindowsAudioTopologyError,
)
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.candidate_levels import (
    CandidateLevelProbe,
    CandidateProbeBusyError,
)
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.web.server import WebConfig, WebServer

_PATH = "/api/v1/audio/candidate-levels"
_SENTINEL = "SENTINEL /private/device/path"


def _device(index: int, name: str) -> AudioDeviceInfo:
    return AudioDeviceInfo(
        id=AudioDeviceId(index), name=name, input_channels=1, output_channels=1
    )


def _capture(stable_id: str, index: int) -> AudioCandidate:
    return AudioCandidate(stable_id, stable_id, "capture", "Test API", index, True)


def _sine_frame(amplitude: float = 0.25, samples: int = 960) -> bytes:
    return struct.pack(
        f"<{samples}h",
        *(
            round(amplitude * 32767 * math.sin(2 * math.pi * 440 * i / 48_000))
            for i in range(samples)
        ),
    )


def _make_probe(backend: FakeAudioBackend, **kwargs: object) -> CandidateLevelProbe:
    def factory(stable_id: str, timeout: float) -> UsbAudioDriver:
        return UsbAudioDriver(
            backend=backend, rx_device=stable_id, capture_open_timeout=timeout
        )

    return CandidateLevelProbe(driver_factory=factory, **kwargs)  # type: ignore[arg-type]


async def _wait_for(predicate: Callable[[], object], timeout: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        assert asyncio.get_running_loop().time() < deadline, "condition not reached"
        await asyncio.sleep(0.005)


async def test_probe_reports_peak_dbfs_for_synthetic_sine() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    probe = _make_probe(backend, capture_window_s=0.2)
    task = asyncio.create_task(probe.probe((_capture("Codec A", 0),)))
    await _wait_for(lambda: backend.rx_streams and backend.rx_streams[0].running)
    backend.rx_streams[0].inject_frame(_sine_frame())
    (level,) = await task
    assert level.stable_id == "Codec A"
    assert level.state == "ok"
    assert level.peak_dbfs is not None
    # 0.25 full-scale sine peaks at 20*log10(0.25) ≈ -12.0 dBFS.
    assert -12.3 < level.peak_dbfs < -11.8


async def test_probe_reports_silent_for_zero_frames() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    probe = _make_probe(backend, capture_window_s=0.05)
    task = asyncio.create_task(probe.probe((_capture("Codec A", 0),)))
    await _wait_for(lambda: backend.rx_streams and backend.rx_streams[0].running)
    backend.rx_streams[0].inject_frame(b"\x00" * 1920)
    (level,) = await task
    assert level.state == "silent"
    assert level.peak_dbfs is None


async def test_probe_reports_busy_for_device_open_elsewhere() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")], strict_device_exclusive=True)
    blocker = backend.open_rx(AudioDeviceId(0))
    await blocker.start(lambda frame: None)
    try:
        probe = _make_probe(backend, capture_window_s=0.05)
        (level,) = await probe.probe((_capture("Codec A", 0),))
        assert level.state == "busy"
        assert level.peak_dbfs is None
    finally:
        await blocker.stop()


async def test_probe_reports_error_without_exception_text() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    backend.block_rx_open = lambda: (_ for _ in ()).throw(RuntimeError(_SENTINEL))
    probe = _make_probe(backend, capture_window_s=0.05)
    (level,) = await probe.probe((_capture("Codec A", 0),))
    assert level.state == "error"
    assert level.peak_dbfs is None
    assert "SENTINEL" not in json.dumps(level.to_dict())


async def test_probe_reports_error_when_nothing_arrives() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    probe = _make_probe(backend, capture_window_s=0.02)
    (level,) = await probe.probe((_capture("Codec A", 0),))
    assert level.state == "error"
    assert backend.rx_streams[0].stopped_count == 1


async def test_probe_skips_render_candidates() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    render = AudioCandidate("Codec A", "Codec A", "render", "Test API", 0, True)
    probe = _make_probe(backend, capture_window_s=0.05)
    assert await probe.probe((render,)) == ()
    assert not backend.rx_streams


async def test_probe_serializes_concurrent_runs() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    probe = _make_probe(backend, capture_window_s=0.2)
    first = asyncio.create_task(probe.probe((_capture("Codec A", 0),)))
    await _wait_for(lambda: probe._in_flight)
    try:
        await probe.probe((_capture("Codec A", 0),))
        raise AssertionError("concurrent probe must be refused")
    except CandidateProbeBusyError:
        pass
    await _wait_for(lambda: backend.rx_streams and backend.rx_streams[0].running)
    backend.rx_streams[0].inject_frame(b"\x01\x00" * 960)
    (level,) = await first
    assert level.state == "ok"


async def test_probe_reuses_a_fresh_result_for_one_second() -> None:
    backend = FakeAudioBackend([_device(0, "Codec A")])
    probe = _make_probe(backend, capture_window_s=0.02)
    first = await probe.probe((_capture("Codec A", 0),))
    opened = len(backend.rx_streams)
    again = await probe.probe((_capture("Codec A", 0),))
    assert again == first
    assert len(backend.rx_streams) == opened  # no new open: reused
    # A different latched set is a different question — never reused.
    await probe.probe(())
    assert len(backend.rx_streams) == opened


# ---------------------------------------------------------------------------
# Web route (POST /api/v1/audio/candidate-levels)
# ---------------------------------------------------------------------------


def _refusal(candidates: tuple[AudioCandidate, ...]) -> WindowsAudioTopologyError:
    return WindowsAudioTopologyError(
        PairRefused("serial_ambiguous", "usb_ambiguous", candidates, ())
    )


class _RefusingSessionRadio(_SessionLanRadio):
    def __init__(self, candidates: tuple[AudioCandidate, ...]) -> None:
        super().__init__()
        self._refusal_candidates = candidates

    async def start_rx(self, callback: object, *, jitter_depth: object = None) -> None:
        raise _refusal(self._refusal_candidates)


def _response(writer: _MemoryWriter) -> tuple[int, dict[str, object]]:
    raw = bytes(writer.buffer)
    status = int(raw.split(b" ", 2)[1])
    return status, json.loads(raw.split(b"\r\n\r\n", 1)[1])


async def _post(server: WebServer, writer: _MemoryWriter) -> None:
    await server._handle_http(writer, "POST", _PATH, headers={})


async def test_route_405_for_get() -> None:
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    writer = _MemoryWriter()
    await server._handle_http(writer, "GET", _PATH, headers={})
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 405 ")


async def test_route_409_without_latch() -> None:
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    writer = _MemoryWriter()
    await _post(server, writer)
    status, payload = _response(writer)
    assert status == 409
    assert payload["error"] == "candidate_levels_unavailable"


async def test_route_409_when_no_capture_candidate_is_latched() -> None:
    candidates = (AudioCandidate("Codec A", "Codec A", "render", "Test API", 0, True),)
    radio = _RefusingSessionRadio(candidates)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    broadcaster = server._audio_broadcaster
    try:
        await broadcaster.ensure_relay()
        assert broadcaster.rx_start_status()["failure"] is not None
        writer = _MemoryWriter()
        await _post(server, writer)
        status, payload = _response(writer)
        assert status == 409
        assert payload["error"] == "candidate_levels_unavailable"
    finally:
        await broadcaster._stop_relay()


async def test_route_409_while_rx_is_active() -> None:
    radio = _SessionLanRadio()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    broadcaster = server._audio_broadcaster
    try:
        await broadcaster.ensure_relay()
        candidates = (_capture("Codec A", 0),)
        broadcaster._rx_start_failure = (
            radio,
            radio.audio_session,
            "usb_ambiguous",
            candidates,
        )
        writer = _MemoryWriter()
        await _post(server, writer)
        status, payload = _response(writer)
        assert status == 409
        assert payload["error"] == "rx_active"
        # The refusal did not touch the latch and opened nothing.
        assert broadcaster.rx_start_status()["failure"] is not None
    finally:
        await broadcaster._stop_relay()


async def test_route_levels_cover_exactly_the_latched_capture_candidates(
    monkeypatch,
) -> None:
    candidates = (
        _capture("Codec A", 0),
        AudioCandidate("Codec A", "Codec A", "render", "Test API", 0, True),
        _capture("Codec B", 1),
        _capture("Ghost Codec", 9),
    )
    radio = _RefusingSessionRadio(candidates)
    backend = FakeAudioBackend([_device(0, "Codec A"), _device(1, "Codec B")])
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    server._candidate_level_probe = CandidateLevelProbe(capture_window_s=0.05)
    broadcaster = server._audio_broadcaster
    try:
        await broadcaster.ensure_relay()
        # A request body must not steer the probe: only the latch names devices.
        body = json.dumps({"stableIds": ["Evil Device"]}).encode()
        reader = asyncio.StreamReader()
        reader.feed_data(body)
        reader.feed_eof()
        writer = _MemoryWriter()
        task = asyncio.create_task(
            server._handle_http(
                writer,
                "POST",
                _PATH,
                headers={"content-length": str(len(body))},
                reader=reader,
            )
        )
        seen: set[int] = set()
        async with asyncio.timeout(5):
            while not task.done():
                for stream in backend.rx_streams:
                    if stream.running and id(stream) not in seen:
                        seen.add(id(stream))
                        stream.inject_frame(
                            _sine_frame() if len(seen) == 1 else b"\x00" * 1920
                        )
                await asyncio.sleep(0.005)
        await task
        status, payload = _response(writer)
        assert status == 200
        assert payload["schemaVersion"] == 1
        levels = payload["levels"]
        assert [level["stableId"] for level in levels] == [
            "Codec A",
            "Codec B",
            "Ghost Codec",
        ]
        assert [level["state"] for level in levels] == ["ok", "silent", "error"]
        assert -12.3 < levels[0]["peakDbfs"] < -11.8
        assert levels[1]["peakDbfs"] is None
        assert levels[2]["peakDbfs"] is None
        assert "Evil" not in json.dumps(payload)
        assert len(backend.rx_streams) == 2  # Ghost Codec never opened
        # The latch is untouched and the relay never started.
        assert broadcaster.rx_start_status()["failure"]["candidates"]
        assert radio.audio_session.rx_demand == 0
    finally:
        await broadcaster._stop_relay()


async def test_route_429_while_a_probe_is_in_flight(monkeypatch) -> None:
    candidates = (_capture("Codec A", 0),)
    radio = _RefusingSessionRadio(candidates)
    backend = FakeAudioBackend([_device(0, "Codec A")])
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    probe = CandidateLevelProbe(capture_window_s=0.2)
    server._candidate_level_probe = probe
    broadcaster = server._audio_broadcaster
    try:
        await broadcaster.ensure_relay()
        first = _MemoryWriter()
        task = asyncio.create_task(_post(server, first))
        await _wait_for(lambda: probe._in_flight)
        second = _MemoryWriter()
        await _post(server, second)
        status, payload = _response(second)
        assert status == 429
        assert payload["error"] == "probe_in_progress"
        await _wait_for(lambda: backend.rx_streams and backend.rx_streams[0].running)
        backend.rx_streams[0].inject_frame(b"\x01\x00" * 960)
        await task
        status, _ = _response(first)
        assert status == 200
    finally:
        await broadcaster._stop_relay()
