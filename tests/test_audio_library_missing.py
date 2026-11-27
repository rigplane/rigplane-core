"""Missing native audio libraries are actionable permanent RX-start failures."""

from __future__ import annotations

import asyncio
import gc
import json
import logging

import pytest
from test_web_audio_rx_start_status import info
from test_web_audio_session_rx import _SessionLanRadio, _make_ws

from rigplane.audio.backend import PortAudioBackend
from rigplane.audio.usb_driver import UsbAudioDriver, audio_start_failure_reason
from rigplane.runtime.controller_authority import ControllerError
from rigplane.web.handlers.audio import AudioHandler
from rigplane.web.server import WebConfig, WebServer


class MissingLibraryRadio(_SessionLanRadio):
    def __init__(self):
        super().__init__()
        self.available = False
        self.load_attempts = 0
        self.backend = PortAudioBackend(dependency_loader=self.load)

    def load(self):
        self.load_attempts += 1
        if not self.available:
            raise OSError("PortAudio library not found")
        return object(), object()

    async def start_rx(self, callback, *, jitter_depth=None):
        self.backend._ensure_deps()
        await super().start_rx(callback, jitter_depth=jitter_depth)


class MissingUsbLibraryRadio(MissingLibraryRadio):
    async def start_rx(self, callback, *, jitter_depth=None):
        await UsbAudioDriver(backend=self.backend).start_rx(callback)


@pytest.mark.parametrize("injected", [True, False])
def test_loader_oserror_is_typed_and_actionable(monkeypatch, injected):
    import builtins

    def missing():
        raise OSError("PortAudio library not found")

    if injected:
        backend = PortAudioBackend(dependency_loader=missing)
    else:
        original = builtins.__import__

        def importing(name, *args, **kwargs):
            if name == "sounddevice":
                return missing()
            return original(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", importing)
        backend = PortAudioBackend()
    with pytest.raises(OSError) as raised:
        backend.list_devices()
    assert type(raised.value).__name__ == "AudioLibraryMissingError"
    assert audio_start_failure_reason(raised.value) == "audio_library_missing"
    assert "libportaudio2" in str(raised.value)
    assert "restart" in str(raised.value).lower()


def test_other_loader_oserror_keeps_its_category():
    error = OSError("some other initialization failure")

    def broken():
        raise error

    with pytest.raises(OSError) as raised:
        PortAudioBackend(dependency_loader=broken).list_devices()
    assert raised.value is error
    assert audio_start_failure_reason(error) == "other"


async def test_usb_driver_loader_failure_keeps_typed_category(caplog):
    radio = MissingLibraryRadio()
    driver = UsbAudioDriver(backend=radio.backend)
    with pytest.raises(OSError) as raised:
        await driver.start_rx(lambda _frame: None)
    assert audio_start_failure_reason(raised.value) == "audio_library_missing"
    assert radio.load_attempts == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("radio_type", [MissingLibraryRadio, MissingUsbLibraryRadio])
async def test_bus_subscribers_do_not_retry_missing_library(caplog, radio_type):
    radio = radio_type()
    with caplog.at_level(logging.WARNING):
        for _ in range(10):
            sub = radio.audio_bus.subscribe("consumer")
            with pytest.raises(RuntimeError) as raised:
                await sub.start()
            assert audio_start_failure_reason(raised.value) == "audio_library_missing"
            assert not sub.active
            assert radio.audio_bus.subscriber_count == 0
    assert radio.load_attempts == 1
    assert len([r for r in caplog.records if r.levelno >= logging.WARNING]) == 1


async def test_missing_library_latches_one_payload_and_log_then_explicit_retry(
    caplog,
):
    radio = MissingLibraryRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    broadcaster = server._audio_broadcaster
    ws = _make_ws()
    queue = None
    try:
        with caplog.at_level(logging.WARNING):
            queue = await broadcaster.subscribe(ws=ws)
            failure = (await info(server))["failure"]
            assert failure["reason"] == "audio_library_missing"
            assert "Audio library missing" in failure["message"]
            assert "libportaudio2" in failure["message"]
            for _ in range(10):
                await broadcaster.ensure_relay()
            assert radio.load_attempts == 1
            assert radio.audio_session.rx_demand == 0
            assert not radio.audio_bus.rx_active
            assert (await info(server))["failure"] == failure
            errors = [r for r in caplog.records if r.levelno >= logging.WARNING]
            assert len(errors) == 1
            assert "libportaudio2" in errors[0].getMessage()
            ws.send_text.assert_awaited_once()
            wire = json.loads(ws.send_text.call_args.args[0])
            assert wire["reason"] == "audio_library_missing"
            assert "libportaudio2" in wire["message"]

        radio.available = True
        await broadcaster.ensure_relay()
        assert radio.load_attempts == 1
        await broadcaster.unsubscribe(queue)
        queue = await broadcaster.subscribe(ws=ws, retry_start=True)
        assert radio.load_attempts == 2
        assert radio.audio_bus.rx_active
        assert radio.audio_session.rx_demand == 1
        assert (await info(server))["failure"] is None
    finally:
        if queue is not None:
            await broadcaster.unsubscribe(queue)
        await broadcaster._stop_relay()


async def test_new_client_gets_latched_failure_without_reloading():
    radio = MissingLibraryRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    broadcaster = server._audio_broadcaster
    queues = []
    try:
        await broadcaster.ensure_relay()
        for _ in range(3):
            ws = _make_ws()
            queues.append(await broadcaster.subscribe(ws=ws))
            ws.send_text.assert_awaited_once()
            assert json.loads(ws.send_text.call_args.args[0])["reason"] == (
                "audio_library_missing"
            )
        assert radio.load_attempts == 1
    finally:
        for queue in queues:
            await broadcaster.unsubscribe(queue)


async def test_explicit_audio_start_retries_after_installation():
    radio = MissingLibraryRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    broadcaster = server._audio_broadcaster
    handler = AudioHandler(_make_ws(), radio, broadcaster)
    try:
        await handler._start_rx()
        assert radio.load_attempts == 1
        await handler._stop_rx()
        radio.available = True
        await handler._start_rx()
        assert radio.load_attempts == 2
        assert radio.audio_bus.rx_active
        assert (await info(server))["failure"] is None
    finally:
        await handler._stop_rx()
        await broadcaster._stop_relay()


async def test_replaced_source_is_not_blocked_by_old_library_failure():
    old = MissingLibraryRadio()
    server = WebServer(old, WebConfig(radio_model="IC-7610"))
    broadcaster = server._audio_broadcaster
    await broadcaster.ensure_relay()
    current = MissingLibraryRadio()
    current.available = True
    broadcaster._radio = current
    server._radio = current
    try:
        await broadcaster.ensure_relay()
        assert current.load_attempts == 1
        assert (await info(server))["failure"] is None
    finally:
        await broadcaster._stop_relay()


@pytest.mark.parametrize(
    "failure", [ControllerError("controller_invalid"), RuntimeError("RX task failed")]
)
async def test_background_failure_is_retrieved(failure, caplog):
    server = WebServer(None, WebConfig())
    loop = asyncio.get_running_loop()
    contexts = []
    previous = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: contexts.append(context))
    settled = asyncio.Event()

    async def failing():
        raise failure

    try:
        with caplog.at_level(logging.DEBUG):
            task = server._spawn(failing())
            task.add_done_callback(lambda _done: settled.set())
            await asyncio.wait_for(settled.wait(), timeout=1)
            del task
            gc.collect()
        assert not server._bg_tasks
        assert not contexts
        assert any("background task failed" in r.getMessage() for r in caplog.records)
    finally:
        loop.set_exception_handler(previous)
