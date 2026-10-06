"""Driver-level native close failures retain the handle and its COM lease."""

from __future__ import annotations

import asyncio
import ctypes
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from rigplane.audio import backend, usb_driver


@pytest.fixture
def fake_ownership_driver(monkeypatch: pytest.MonkeyPatch):
    pool = usb_driver._BoundedPortAudioPool()
    monkeypatch.setattr(usb_driver, "bounded_portaudio_pool", pool)
    monkeypatch.setattr(usb_driver, "_get_uid_map", lambda: {})
    monkeypatch.setattr(usb_driver, "resolve_usb_duplex_mode", lambda *_args: "full")
    devices = [
        backend.AudioDeviceInfo(
            id=backend.AudioDeviceId(index),
            name=f"USB Audio CODEC {index}",
            input_channels=2,
            output_channels=2,
            default_samplerate=48_000,
        )
        for index in (1, 2)
    ]
    fake = backend.FakeAudioBackend(devices)

    def make(device: int = 1):
        return usb_driver.UsbAudioDriver(
            backend=fake,
            rx_device=device,
            tx_device=device,
            capture_open_timeout=0.1,
        )

    yield pool, fake, make
    pool._executor.shutdown(wait=True)


async def _drain(pool: usb_driver._BoundedPortAudioPool) -> None:
    async with asyncio.timeout(5):
        while pool.inflight:
            await asyncio.sleep(0.01)


@pytest.mark.parametrize("kind", ["rx", "tx", "duplex"])
@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.parametrize("late_failure", [False, True])
@pytest.mark.timeout(10)
async def test_pending_open_blocks_same_endpoint_until_settled_and_closed(
    fake_ownership_driver, kind: str, cancel: bool, late_failure: bool
) -> None:
    pool, fake, make = fake_ownership_driver
    owner, replacement, unrelated = make(), make(), make(2)
    entered, release = threading.Event(), threading.Event()

    def blocked_open() -> None:
        entered.set()
        assert release.wait(5)
        if late_failure:
            raise RuntimeError("late start refused")

    hook = f"block_{kind}_open"
    setattr(fake, hook, blocked_open)
    attempt = asyncio.create_task(_start(owner, kind))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        if cancel:
            attempt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await attempt
        else:
            with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
                await attempt
        streams = getattr(fake, f"{kind}_streams")
        old = streams[0]
        setattr(fake, hook, None)
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await _start(replacement, kind)
        assert all(stream.started_count == 0 for stream in streams)
        assert old.stopped_count == 0
        await _start(unrelated, kind)
        assert getattr(unrelated, f"{kind}_running", unrelated.tx_running)
        await getattr(unrelated, f"stop_{kind}")()
        release.set()
        await _drain(pool)
        assert old.stopped_count == 1
        await _start(replacement, kind)
        await getattr(replacement, f"stop_{kind}")()
    finally:
        release.set()
        if not attempt.done():
            attempt.cancel()
        await asyncio.gather(attempt, return_exceptions=True)
        await _drain(pool)


@pytest.mark.parametrize("cancel", [False, True])
@pytest.mark.timeout(10)
async def test_retired_rx_callback_cannot_update_health_or_replacement(
    fake_ownership_driver, cancel: bool
) -> None:
    pool, fake, make = fake_ownership_driver
    driver = make()
    driver._capture_open_timeout = 0.1
    entered, release, closing, closed = (threading.Event() for _ in range(4))

    def blocked_open() -> None:
        entered.set()
        assert release.wait(5)

    def blocked_close() -> None:
        closing.set()
        assert closed.wait(5)

    fake.block_rx_open = blocked_open
    first: list[bytes] = []
    second: list[bytes] = []
    attempt = asyncio.create_task(driver.start_rx(first.append))
    try:
        async with asyncio.timeout(2):
            while not entered.is_set():
                await asyncio.sleep(0.005)
        old = fake.rx_streams[0]
        old.block_stop = blocked_close
        if cancel:
            attempt.cancel()
            with pytest.raises(asyncio.CancelledError):
                await attempt
        else:
            with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
                await attempt
        fake.block_rx_open = None
        retry = asyncio.create_task(driver.start_rx(second.append))
        await asyncio.sleep(0.01)
        release.set()
        async with asyncio.timeout(2):
            while not closing.is_set():
                await asyncio.sleep(0.005)
        assert old._callback is not None
        stale = old._callback
        for _ in range(500):
            stale(bytes(1920))
        assert first == second == []
        assert not driver.rx_silent
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await retry
        closed.set()
        await _drain(pool)
        await driver.start_rx(second.append)
        stale(bytes(1920))
        assert second == []
        fake.rx_streams[-1].inject_frame(b"\x01\x00" * 960)
        assert len(second) == 1
        await driver.stop_rx()
        stale(bytes(1920))
        assert len(second) == 1
    finally:
        release.set()
        closed.set()
        if not attempt.done():
            attempt.cancel()
        await asyncio.gather(attempt, return_exceptions=True)
        await _drain(pool)


@pytest.mark.timeout(10)
async def test_pending_start_is_not_stopped_concurrently_and_refused_close_is_owned(
    fake_ownership_driver,
) -> None:
    pool, fake, make = fake_ownership_driver
    driver, replacement = make(), make()
    entered, release = threading.Event(), threading.Event()
    closes = 0

    def blocked_open() -> None:
        entered.set()
        assert release.wait(5)

    def refuse_close() -> None:
        nonlocal closes
        closes += 1
        if closes < 3:
            raise RuntimeError("close refused")

    fake.block_rx_open = blocked_open
    try:
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
        old = fake.rx_streams[0]
        assert entered.is_set()
        old.block_stop = refuse_close
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await pool.stop_stream_bounded(old, direction="rx", timeout=0.1)
        assert closes == 0
        release.set()
        await _drain(pool)
        assert closes == 1
        fake.block_rx_open = None
        with pytest.raises(RuntimeError, match="close refused"):
            await replacement.start_rx(lambda _frame: None)
        assert all(stream.started_count == 0 for stream in fake.rx_streams[1:])
        await replacement.start_rx(lambda _frame: None)
        assert closes == 3
        await replacement.stop_rx()
    finally:
        release.set()
        await _drain(pool)


@pytest.fixture
def native_driver(monkeypatch: pytest.MonkeyPatch):
    events: list[str] = []
    handles: list[object] = []
    state = SimpleNamespace(fail_start=False, fail_close=False)

    def retain(pointer: object) -> int:
        ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p))[0] = 123
        events.append("retain")
        return 0

    def release(cookie: ctypes.c_void_p) -> int:
        assert cookie.value == 123
        events.append("release")
        return 0

    ole32 = SimpleNamespace(
        CoIncrementMTAUsage=Mock(side_effect=retain),
        CoDecrementMTAUsage=Mock(side_effect=release),
        CoInitializeEx=Mock(return_value=0),
        CoUninitialize=Mock(),
    )
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=ole32), raising=False)
    monkeypatch.setattr(backend, "sys", SimpleNamespace(platform="win32"))
    monkeypatch.setattr(usb_driver, "sys", SimpleNamespace(platform="win32"))

    class NativeStream:
        def __init__(self, **_kwargs: object) -> None:
            handles.append(self)
            events.append("open")

        def start(self) -> None:
            events.append("start")
            if state.fail_start:
                state.fail_start = False
                raise RuntimeError("start refused")

        def stop(self) -> None:
            events.append("stop")

        def close(self) -> None:
            events.append("close")
            if state.fail_close:
                state.fail_close = False
                raise RuntimeError("close refused")
            events.append("closed")

    sd = SimpleNamespace(
        InputStream=NativeStream,
        OutputStream=NativeStream,
        Stream=NativeStream,
        check_input_settings=lambda **_kwargs: None,
        check_output_settings=lambda **_kwargs: None,
    )
    driver = usb_driver.UsbAudioDriver(
        backend=backend.PortAudioBackend(dependency_loader=lambda: (sd, object())),
        channels=2,
        capture_open_timeout=1.0,
    )
    device = usb_driver.UsbAudioDevice(19, "USB Audio CODEC", 2, 2)

    async def select(**_kwargs: object):
        return device, device

    monkeypatch.setattr(driver, "_select_devices_bounded", select)
    return driver, state, events, handles


async def _start(driver: usb_driver.UsbAudioDriver, kind: str) -> None:
    if kind == "tx":
        await driver.start_tx()
    else:
        await getattr(driver, f"start_{kind}")(lambda _frame: None)


@pytest.mark.parametrize("kind", ["rx", "tx", "duplex"])
@pytest.mark.parametrize("retry", ["stop", "start"])
async def test_driver_retains_failed_close_until_explicit_cleanup(
    native_driver, kind: str, retry: str
) -> None:
    driver, state, events, handles = native_driver
    await _start(driver, kind)
    slot = f"_{kind}_stream"
    original = getattr(driver, slot)
    state.fail_close = True
    with pytest.raises(RuntimeError, match="close refused"):
        await getattr(driver, f"stop_{kind}")()
    assert getattr(driver, slot) is original
    assert not original.running
    assert events.count("retain") == 1
    assert "release" not in events

    if retry == "stop":
        await getattr(driver, f"stop_{kind}")()
        assert getattr(driver, slot) is None
        assert len(handles) == 1
    else:
        await _start(driver, kind)
        assert len(handles) == 2
        # The old handle must close before the replacement is constructed.
        assert events.index("closed") < events.index("open", events.index("open") + 1)
        await getattr(driver, f"stop_{kind}")()
    assert events.count("closed") == events.count("release") == len(handles)
    assert events.count("retain") == events.count("release")


@pytest.mark.parametrize("kind", ["rx", "tx", "duplex"])
async def test_failed_start_and_failed_cleanup_remain_owned_by_driver(
    native_driver, kind: str
) -> None:
    driver, state, events, handles = native_driver
    state.fail_start = state.fail_close = True
    with pytest.raises(RuntimeError, match="start refused"):
        await _start(driver, kind)
    original = getattr(driver, f"_{kind}_stream")
    assert original is not None
    assert not original.running
    assert "release" not in events
    await getattr(driver, f"stop_{kind}")()
    assert getattr(driver, f"_{kind}_stream") is None
    assert len(handles) == events.count("closed") == events.count("release") == 1


async def test_exclusive_handoff_refuses_replacement_after_failed_rx_close(
    native_driver, monkeypatch: pytest.MonkeyPatch
) -> None:
    driver, state, events, handles = native_driver
    monkeypatch.setattr(
        usb_driver, "resolve_usb_duplex_mode", lambda *_args: "exclusive"
    )
    await driver.start_rx(lambda _frame: None)
    original = driver._rx_stream
    state.fail_close = True
    with pytest.raises(RuntimeError, match="close refused"):
        await driver.start_tx()
    assert driver._rx_stream is original
    assert len(handles) == 1
    assert "release" not in events
    await driver.start_tx()
    assert events.count("closed") == events.count("release") == 1
    await driver.stop_rx()
    await driver.stop_tx()
    assert len(handles) == events.count("closed") == events.count("release") == 2


async def test_dead_duplex_cleanup_precedes_plain_rx_replacement(native_driver) -> None:
    driver, state, events, handles = native_driver
    await driver.start_duplex(lambda _frame: None)
    original = driver._duplex_stream
    state.fail_close = True
    with pytest.raises(RuntimeError, match="close refused"):
        await driver.stop_duplex()
    state.fail_close = True
    with pytest.raises(RuntimeError, match="close refused"):
        await driver.start_rx(lambda _frame: None)
    assert driver._duplex_stream is original
    assert len(handles) == 1
    await driver.start_rx(lambda _frame: None)
    await driver.stop_rx()
    assert events.count("closed") == events.count("release") == 2


@pytest.mark.timeout(10)
async def test_timed_out_close_is_joined_without_concurrent_second_stop() -> None:
    pool = usb_driver._BoundedPortAudioPool()
    entered = threading.Event()
    gate = threading.Event()
    calls = 0

    class Stream:
        async def stop(self) -> None:
            nonlocal calls
            calls += 1
            entered.set()
            gate.wait(5)

    stream = Stream()
    try:
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await pool.stop_stream_bounded(stream, direction="rx", timeout=0.05)
        assert entered.is_set()
        retry = asyncio.create_task(
            pool.stop_stream_bounded(stream, direction="rx", timeout=1.0)
        )
        await asyncio.sleep(0.05)
        assert calls == 1
        gate.set()
        await retry
    finally:
        gate.set()
        pool._executor.shutdown(wait=True)


@pytest.mark.parametrize("late_open_fails", [False, True])
@pytest.mark.timeout(10)
async def test_failed_late_cleanup_retained_until_next_explicit_open(
    late_open_fails: bool,
) -> None:
    pool = usb_driver._BoundedPortAudioPool()
    gate = threading.Event()
    cleanup_attempted = threading.Event()
    events: list[str] = []

    class Abandoned:
        async def start(self) -> None:
            gate.wait(5)
            if late_open_fails:
                raise RuntimeError("late start refused")

        async def stop(self) -> None:
            events.append("close")
            cleanup_attempted.set()
            if events.count("close") == 1:
                raise RuntimeError("late close refused")
            events.append("closed")

    class Replacement:
        async def start(self) -> None:
            events.append("new open")

    abandoned, replacement = Abandoned(), Replacement()
    try:
        with pytest.raises(usb_driver.AudioCaptureOpenTimeoutError):
            await pool.open_stream_bounded(
                abandoned, abandoned.start(), direction="rx", timeout=0.05
            )
        gate.set()
        for _ in range(100):
            if cleanup_attempted.is_set() and pool.inflight == 0:
                break
            await asyncio.sleep(0.01)
        assert events == ["close"]
        await pool.open_stream_bounded(
            replacement, replacement.start(), direction="rx", timeout=1.0
        )
        assert events == ["close", "close", "closed", "new open"]
    finally:
        gate.set()
        pool._executor.shutdown(wait=True)


async def test_pool_retains_failed_close_when_caller_drops_its_stream() -> None:
    """AudioBridge discards its slot after logging a close error."""
    pool = usb_driver._BoundedPortAudioPool()
    events: list[str] = []

    class Stream:
        async def stop(self) -> None:
            events.append("close")
            if events.count("close") < 3:
                raise RuntimeError("close refused")
            events.append("closed")

    class Replacement:
        async def start(self) -> None:
            events.append("new open")

    stream = Stream()
    replacement = Replacement()
    try:
        with pytest.raises(RuntimeError, match="close refused"):
            await pool.stop_stream_bounded(stream, direction="bridge", timeout=1.0)
        del stream  # The production caller no longer owns this handle.
        with pytest.raises(RuntimeError, match="close refused"):
            await pool.open_stream_bounded(
                replacement, replacement.start(), direction="bridge", timeout=1.0
            )
        assert events == ["close", "close"]
        await pool.open_stream_bounded(
            replacement, replacement.start(), direction="bridge", timeout=1.0
        )
        assert events == ["close", "close", "close", "closed", "new open"]
    finally:
        pool._executor.shutdown(wait=True)
