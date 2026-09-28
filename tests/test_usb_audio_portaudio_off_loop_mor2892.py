"""MOR-2892 — PortAudio calls reachable from the web server stay off the loop.

Stand incident (2026-09-28, IC-7300 on mini .77): a pending macOS
microphone-permission prompt (TCC) blocked ``Pa_IsFormatSupported`` — the RX
relay's format probe — ON the event-loop thread. The whole server froze
(HTTP, WebSockets, PTT release, watchdogs) and only SIGKILL ended it.
MOR-1438 had already moved the stream OPEN off the loop; the format probe
and the stop/close paths were still synchronous on it.

``UsbAudioDriver`` now runs the format probe (``check_sample_rate``), device
enumeration, and stream stop/close off the loop through the same driver-owned
worker pool, bounded by ``capture_open_timeout``. On exceeding the bound the
audio request fails with an operator-readable warning (a microphone
permission prompt may be pending on the computer running RigPlane) and the
server keeps answering HTTP and WebSockets.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import AudioCaptureOpenTimeoutError, UsbAudioDriver

_LOGGER_NAME = "rigplane.audio.usb_driver"
# Tiny relative to the production default — keeps this suite fast while
# still exercising the real asyncio.wait()-based bound.
_TEST_TIMEOUT_S = 0.05


def _fake_devices() -> list[AudioDeviceInfo]:
    return [
        AudioDeviceInfo(
            id=AudioDeviceId(1),
            name="USB Audio CODEC",
            input_channels=1,
            output_channels=1,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        ),
    ]


def _make_driver(
    capture_open_timeout: float = _TEST_TIMEOUT_S,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    backend = FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend, capture_open_timeout=capture_open_timeout)
    return driver, backend


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno >= logging.WARNING
    ]


@pytest.fixture(autouse=True)
def _no_coreaudio_uid_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the fake-backend paths off real CoreAudio (macOS test hosts).

    ``_devices_from_backend`` enriches devices via ``_get_uid_map()`` — on
    a macOS test host that is a REAL CoreAudio mach call whose latency is
    unrelated to what these tests measure, and since MOR-2892 it runs
    inside the bounded (here 0.05 s) enumeration window, turning host
    jitter into flaky "device enumeration" timeouts. Neutralize it.
    """
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})


class _Ticker:
    """Counts event-loop ticks while the PortAudio call under test is stuck."""

    def __init__(self) -> None:
        self.progressed = 0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            self.progressed += 1
            await asyncio.sleep(0.005)

    def start(self) -> asyncio.Task[None]:
        return asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        # Give the just-started task a chance to have ticked at least once
        # even on a loaded runner, so the ``progressed`` read is stable.
        await asyncio.sleep(0)


@pytest.mark.timeout(10)
async def test_probe_timeout_keeps_loop_free_and_fails_request(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck format probe must not stall other coroutines on the loop."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_probe = gate.wait  # blocks a WORKER thread, never the loop

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        started = time.monotonic()
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
        elapsed = time.monotonic() - started
    finally:
        await ticker.stop()
        await ticker_task
        gate.set()  # release the stuck background probe so the worker exits
        await asyncio.sleep(0.05)

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while the probe is stuck"
    )
    assert elapsed < 0.5, "audio request must fail within the bound (plus margin)"
    assert driver.rx_running is False

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    message = warnings[0].getMessage()
    assert "format probe" in message
    assert "microphone" in message.lower(), (
        f"operator-readable reason expected, got: {message!r}"
    )
    assert "RigPlane" in message


@pytest.mark.timeout(10)
async def test_probe_runs_off_loop_thread() -> None:
    """The format probe itself must execute on a non-loop thread.

    Direct thread-identity proof — moving the probe back onto the loop
    (the MOR-2892 mutation) turns this red immediately.
    """
    driver, backend = _make_driver(capture_open_timeout=1.0)
    ident: dict[str, int] = {}
    released = threading.Event()

    def _record_thread() -> None:
        ident["thread"] = threading.get_ident()
        released.set()

    backend.block_probe = _record_thread
    await driver.start_rx(lambda _frame: None)

    assert released.wait(timeout=1.0)
    assert ident["thread"] != threading.get_ident(), (
        "format probe must run off the event-loop thread"
    )
    assert driver.rx_running is True
    await driver.stop_rx()


@pytest.mark.timeout(10)
async def test_probe_timeout_keeps_http_and_ws_answering(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Acceptance: while the probe is stuck, HTTP and a WebSocket ping still
    get answers on the same event loop, and the audio request fails within
    the bound."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_probe = gate.wait

    async def _http_ping(_request: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def _ws_holder(request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        # Ping/pong is handled by the protocol layer; just hold the socket.
        async for _msg in ws:
            pass
        return ws

    app = web.Application()
    app.router.add_get("/api/v1/ping", _http_ping)
    app.router.add_get("/api/v1/ws", _ws_holder)
    server = TestServer(app)
    await server.start_server()
    session = ClientSession()
    ws = await session.ws_connect(str(server.make_url("/api/v1/ws")))
    try:
        start_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
        await asyncio.sleep(0.01)  # let it reach the stuck probe

        resp = await asyncio.wait_for(
            session.get(str(server.make_url("/api/v1/ping"))), timeout=1.0
        )
        assert resp.status == 200
        await resp.release()

        # aiohttp's ping() is synchronous: it sends PING and returns the
        # future that resolves when the PONG arrives — await THAT, bounded.
        pong = ws.ping()
        await asyncio.wait_for(pong, timeout=1.0)

        with pytest.raises(AudioCaptureOpenTimeoutError):
            await asyncio.wait_for(start_task, timeout=1.0)
        assert driver.rx_running is False
    finally:
        gate.set()  # release the stuck background probe so the worker exits
        await ws.close()
        await session.close()
        await server.close()
        await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_probe_recovers_after_permission_granted() -> None:
    """A later start (e.g. the prompt answered) must open RX again."""
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_probe = gate.wait

    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is False

    gate.set()
    await asyncio.sleep(0.05)  # let the abandoned probe settle
    backend.block_probe = None  # simulate the permission granted

    await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is True
    await driver.stop_rx()


@pytest.mark.timeout(10)
async def test_stop_rx_off_loop_and_bounded(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck stream stop must run off the loop and fail within the bound.

    ``stream.stop()`` is the same synchronous Pa_StopStream/Pa_CloseStream
    call class as the open (MOR-1438 F2 made the ABANDONED-handle close
    off-loop; this covers the ordinary stop_rx path).
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    await driver.start_rx(lambda _frame: None)
    stream = backend.rx_streams[-1]
    stream.block_stop = gate.wait

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.stop_rx()
    finally:
        await ticker.stop()
        await ticker_task

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while the stop is stuck"
    )
    assert driver.rx_running is False
    assert stream.stop_thread_ident is not None
    assert stream.stop_thread_ident != threading.get_ident(), (
        "stream stop must run off the event-loop thread"
    )
    assert any(
        "stream stop" in w.getMessage() for w in _warnings(caplog)
    ), "timeout warning must name the stuck operation"

    gate.set()  # let the abandoned background stop finally complete
    for _ in range(50):
        await asyncio.sleep(0.01)
        if stream.stopped_count:
            break
    assert stream.stopped_count == 1, "background stop must still complete"


@pytest.mark.timeout(10)
async def test_enumeration_timeout_bounded(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device enumeration (sd.query_devices + topology resolve) is also a
    PortAudio call on the web-server start path — bounded the same way."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    devices = _fake_devices()

    def _blocked_list() -> list[AudioDeviceInfo]:
        gate.wait()
        return devices

    monkeypatch.setattr(backend, "list_devices", _blocked_list)

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
    finally:
        await ticker.stop()
        await ticker_task
        gate.set()
        await asyncio.sleep(0.05)

    assert ticker.progressed >= 3
    assert driver.rx_running is False
    assert any(
        "device enumeration" in w.getMessage() for w in _warnings(caplog)
    ), "timeout warning must name the stuck operation"
