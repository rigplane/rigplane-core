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
    assert any("stream stop" in w.getMessage() for w in _warnings(caplog)), (
        "timeout warning must name the stuck operation"
    )

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
    assert any("device enumeration" in w.getMessage() for w in _warnings(caplog)), (
        "timeout warning must name the stuck operation"
    )


@pytest.mark.timeout(10)
async def test_duplex_mode_cold_cache_never_touches_portaudio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cold ``duplex_mode`` read must not enumerate PortAudio on the loop.

    ``duplex_mode`` is reachable from the web server with a COLD cache
    (``radio_poller``, ``session.audio_setup_order``) — e.g. right after
    ``set_serial_port`` cleared the selection cache on rediscovery. The
    read is a pure cache hit (MOR-2892); any backend touch fails here.
    """
    driver, backend = _make_driver(capture_open_timeout=1.0)

    def _forbidden_list() -> list[AudioDeviceInfo]:
        raise AssertionError("duplex_mode touched PortAudio on the loop")

    monkeypatch.setattr(backend, "list_devices", _forbidden_list)

    assert driver.duplex_mode == "full"

    driver.set_serial_port("/dev/cu.usbserial-9931")
    assert driver.duplex_mode == "full"


@pytest.mark.timeout(10)
async def test_late_close_counted_against_pool_bound() -> None:
    """A wedged late-handle close must COUNT against the pool bound.

    ``_close_late_stream`` fires as a done-callback after an abandoned
    open belatedly completes (MOR-1438 F2). Its ``stream.stop()`` is the
    same blocking Pa_StopStream/Pa_CloseStream class as every other
    bounded operation — before MOR-2892 it went through a raw
    ``run_in_executor`` and a wedged close occupied a worker WITHOUT
    being counted, so eight of them could exhaust the pool while the
    saturation guard still reported it empty.
    """
    open_gate = threading.Event()
    stop_gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = open_gate.wait

    # Abandon an open; it keeps running on its worker.
    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_rx(lambda _frame: None)
    stream = backend.rx_streams[-1]
    # The LATE close (submitted once the open completes) hangs on this.
    stream.block_stop = stop_gate.wait

    loop = asyncio.get_running_loop()
    try:
        open_gate.set()  # the abandoned open completes -> late close fires

        # Deterministic wait: FakeRxStream.stop records the worker thread
        # BEFORE blocking, so once this is set the stuck close is in
        # flight on a worker.
        deadline = loop.time() + 5.0
        while loop.time() < deadline and stream.stop_thread_ident is None:
            await asyncio.sleep(0.005)
        assert stream.stop_thread_ident is not None, (
            "the late close never started — done-callback did not fire"
        )

        assert driver._inflight_opens == 1, (
            "a wedged late close must be counted like every other "
            "bounded operation; an uncounted close silently exhausts "
            "the pool behind the saturation guard's back"
        )
        assert stream.stop_thread_ident != threading.get_ident(), (
            "the late close must run off the event-loop thread"
        )
    finally:
        stop_gate.set()
        open_gate.set()

    deadline = loop.time() + 5.0
    while loop.time() < deadline and driver._inflight_opens != 0:
        await asyncio.sleep(0.005)
    assert driver._inflight_opens == 0, "released close must uncount itself"


@pytest.fixture
def windows_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
    monkeypatch.setattr("rigplane.audio.usb_driver._WINDOWS_PNP_TIMEOUT_S", 0.02)


@pytest.mark.timeout(10)
async def test_optional_pnp_timeout_falls_back_while_worker_stays_tracked(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    from rigplane.audio.usb_driver import bounded_portaudio_pool
    from rigplane.usb_audio_resolve import WindowsPnpDevice

    gate = threading.Event()
    entered = threading.Event()
    driver, backend = _make_driver(capture_open_timeout=0.5)
    driver.set_serial_port("COM3")
    before = bounded_portaudio_pool.inflight
    seen: list[list] = []
    select = driver._select_windows_devices

    def record_selection(devices, records, serial_port):
        seen.append(records)
        return select(devices, records, serial_port)

    monkeypatch.setattr(driver, "_select_windows_devices", record_selection)

    def slow_pnp():
        entered.set()
        gate.wait()
        return [
            WindowsPnpDevice("serial", "radio", "1234", "5678", "COM3", None),
            WindowsPnpDevice("audio", "radio", "1234", "5678", None, "USB Audio CODEC"),
        ]

    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", slow_pnp
    )
    started = time.monotonic()
    try:
        await driver.start_rx(lambda _frame: None)
        assert entered.is_set()
        assert time.monotonic() - started < 0.4
        assert driver.rx_running
        assert bounded_portaudio_pool.inflight == before + 1
        selected = driver.selected_rx_device
        driver.set_serial_port("COM7")
        gate.set()
        for _ in range(100):
            if bounded_portaudio_pool.inflight == before:
                break
            await asyncio.sleep(0.005)
        assert bounded_portaudio_pool.inflight == before
        assert seen == [[]], "late topology must never enter mapping or publish a cache"
        assert driver.selected_rx_device is None
        assert selected is not None
    finally:
        gate.set()
        await driver.stop_rx()


@pytest.mark.timeout(10)
async def test_optional_pnp_timeout_refuses_indistinguishable_radios(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    from rigplane.audio.usb_driver import AudioDeviceSelectionError

    gate = threading.Event()
    devices = _fake_devices() + [
        AudioDeviceInfo(
            id=AudioDeviceId(2),
            name="USB Audio CODEC",
            input_channels=1,
            output_channels=1,
        )
    ]
    backend = FakeAudioBackend(devices)
    driver = UsbAudioDriver(
        backend=backend,
        serial_port="COM3",
        capture_open_timeout=0.5,
    )
    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", lambda: gate.wait()
    )
    try:
        with pytest.raises(AudioDeviceSelectionError, match="ambiguous"):
            await driver.start_rx(lambda _frame: None)
        assert not backend.rx_streams
        assert driver.selected_rx_device is None
    finally:
        gate.set()
        await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_windows_selection_has_one_deadline_and_no_late_cache(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    gate = threading.Event()
    driver, backend = _make_driver(capture_open_timeout=0.08)
    driver.set_serial_port("COM3")
    devices = _fake_devices()

    def delayed_list():
        time.sleep(0.055)
        return devices

    monkeypatch.setattr(backend, "list_devices", delayed_list)
    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", lambda: []
    )
    original = driver._select_windows_devices

    def delayed_mapping(*args):
        gate.wait()
        return original(*args)

    monkeypatch.setattr(driver, "_select_windows_devices", delayed_mapping)
    started = time.monotonic()
    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
        assert time.monotonic() - started < 0.12
        assert not backend.rx_streams
        assert driver.selected_rx_device is None
    finally:
        gate.set()
        await asyncio.sleep(0.05)
    assert driver.selected_rx_device is None


@pytest.mark.timeout(10)
async def test_windows_cancelled_or_rebound_pnp_cannot_publish_selection(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    from rigplane.audio.usb_driver import AudioDriverLifecycleError

    for cancel in (False, True):
        gate = threading.Event()
        entered = threading.Event()
        driver, backend = _make_driver(capture_open_timeout=0.5)
        driver.set_serial_port("COM3")

        def slow_pnp():
            entered.set()
            gate.wait()
            return []

        monkeypatch.setattr(
            "rigplane.usb_audio_resolve._query_windows_pnp_devices", slow_pnp
        )
        task = asyncio.create_task(driver.start_rx(lambda _frame: None))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.001)
            assert entered.is_set()
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                driver.set_serial_port("COM7")
                with pytest.raises(AudioDriverLifecycleError):
                    await task
            assert not backend.rx_streams
        finally:
            gate.set()
            await asyncio.sleep(0.05)
        assert driver.selected_rx_device is None


@pytest.mark.timeout(10)
async def test_windows_override_bypasses_pnp_and_aliases_or_halves_are_one_pair(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    def forbidden():
        raise AssertionError("explicit override queried topology")

    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", forbidden
    )
    backend = FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend, serial_port="COM3", rx_device="1")
    await driver.start_rx(lambda _frame: None)
    await driver.stop_rx()

    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", lambda: []
    )
    for devices in (
        [
            AudioDeviceInfo(
                id=AudioDeviceId(i),
                name="USB Audio CODEC",
                input_channels=1,
                output_channels=1,
                platform_uid="same-physical-device",
            )
            for i in (1, 2)
        ],
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="USB Audio CODEC",
                input_channels=1,
            ),
            AudioDeviceInfo(
                id=AudioDeviceId(2),
                name="USB Audio CODEC",
                output_channels=1,
            ),
        ],
    ):
        backend = FakeAudioBackend(devices)
        driver = UsbAudioDriver(backend=backend, serial_port="COM3")
        await driver.start_rx(lambda _frame: None)
        assert driver.rx_running
        await driver.stop_rx()


@pytest.mark.timeout(10)
async def test_windows_valid_topology_and_wrong_parent_never_share_fallback(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from rigplane.audio.usb_driver import AudioDeviceSelectionError
    from rigplane.usb_audio_resolve import WindowsPnpDevice

    records = [
        WindowsPnpDevice("serial", "radio", "1234", "5678", "COM3", None),
        WindowsPnpDevice("audio", "radio", "1234", "5678", None, "USB Audio CODEC"),
    ]
    raw = [
        {"name": "Built-in", "max_input_channels": 0, "max_output_channels": 2},
        {"name": "USB Audio CODEC", "max_input_channels": 1, "max_output_channels": 1},
    ]
    thread_ids: list[int] = []

    def query_devices():
        thread_ids.append(threading.get_ident())
        return raw

    monkeypatch.setattr(
        "rigplane.audio.usb_driver._extract_sounddevice_module",
        lambda _backend: SimpleNamespace(query_devices=query_devices),
    )
    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", lambda: records
    )
    driver, backend = _make_driver(capture_open_timeout=0.5)
    driver.set_serial_port("COM3")
    await driver.start_rx(lambda _frame: None)
    assert driver.selected_rx_device.index == 1
    assert thread_ids and all(t != threading.get_ident() for t in thread_ids)
    await driver.stop_rx()

    records[1] = replace(records[1], parent_pnp_id="WRONG-RADIO")
    driver, backend = _make_driver(capture_open_timeout=0.5)
    driver.set_serial_port("COM3")
    with pytest.raises(AudioDeviceSelectionError, match="parents do not match"):
        await driver.start_rx(lambda _frame: None)
    assert not backend.rx_streams
    assert driver.selected_rx_device is None


@pytest.mark.timeout(10)
async def test_actual_windows_resolver_mapping_shares_selection_deadline(
    monkeypatch: pytest.MonkeyPatch, windows_selection: None
) -> None:
    from types import SimpleNamespace

    from rigplane.audio.usb_driver import bounded_portaudio_pool
    from rigplane.usb_audio_resolve import WindowsPnpDevice

    total_budget = 0.25
    tolerance = 0.01
    driver, backend = _make_driver(capture_open_timeout=total_budget)
    driver.set_serial_port("COM3")
    devices = _fake_devices()
    records = [
        WindowsPnpDevice("serial", "radio", "1234", "5678", "COM3", None),
        WindowsPnpDevice("audio", "radio", "1234", "5678", None, "USB Audio CODEC"),
    ]
    gate = threading.Event()
    entered = threading.Event()
    sdk_threads: list[int] = []
    submissions: list[tuple[str, float, float]] = []
    before = bounded_portaudio_pool.inflight
    run_bounded = bounded_portaudio_pool.run_bounded

    async def record_submission(
        fn, *, what, direction, timeout, warn_on_timeout=True
    ):
        submissions.append((what, time.monotonic(), timeout))
        return await run_bounded(
            fn,
            what=what,
            direction=direction,
            timeout=timeout,
            warn_on_timeout=warn_on_timeout,
        )

    def delayed_list():
        time.sleep(0.07)
        return devices

    def query_devices():
        sdk_threads.append(threading.get_ident())
        entered.set()
        gate.wait()
        return [
            {"name": "Built-in", "max_input_channels": 0, "max_output_channels": 2},
            {
                "name": "USB Audio CODEC",
                "max_input_channels": 1,
                "max_output_channels": 1,
            },
        ]

    monkeypatch.setattr(bounded_portaudio_pool, "run_bounded", record_submission)
    monkeypatch.setattr(backend, "list_devices", delayed_list)
    monkeypatch.setattr(
        "rigplane.usb_audio_resolve._query_windows_pnp_devices", lambda: records
    )
    monkeypatch.setattr(
        "rigplane.audio.usb_driver._extract_sounddevice_module",
        lambda _backend: SimpleNamespace(query_devices=query_devices),
    )
    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
        assert entered.is_set(), "actual resolver must reach the gated SDK call"
        assert sdk_threads and all(t != threading.get_ident() for t in sdk_threads)
        assert [what for what, _, _ in submissions] == [
            "device enumeration",
            "optional Windows topology",
            "device enumeration",
        ]
        _, enum_entry, enum_timeout = submissions[0]
        _, pnp_entry, pnp_timeout = submissions[1]
        _, mapping_entry, mapping_timeout = submissions[2]
        original_deadline = enum_entry + enum_timeout
        assert 0 < enum_timeout <= total_budget
        assert abs(mapping_entry + mapping_timeout - original_deadline) < tolerance
        assert mapping_timeout < enum_timeout - 0.05
        reserve = min(0.25, total_budget / 4)
        assert 0 < pnp_timeout <= 0.02
        assert pnp_entry + pnp_timeout <= original_deadline - reserve + tolerance
        assert bounded_portaudio_pool.inflight == before + 1
        assert not backend.rx_streams
        assert driver.selected_rx_device is None
        assert driver.selected_tx_device is None
    finally:
        gate.set()
        drain_deadline = time.monotonic() + 2
        while (
            bounded_portaudio_pool.inflight != before
            and time.monotonic() < drain_deadline
        ):
            await asyncio.sleep(0.005)
        assert bounded_portaudio_pool.inflight == before
    assert driver.selected_rx_device is None
    assert driver.selected_tx_device is None
    assert not backend.rx_streams
