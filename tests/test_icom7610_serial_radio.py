"""Lifecycle and readiness tests for Icom7610SerialRadio."""

from __future__ import annotations

import asyncio

import pytest

from rigplane import IcomRadio, RadioConnectionState
from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_FREQ_GET,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.exceptions import CommandError, ConnectionError
from rigplane.types import AudioCodec
from rigplane.types import bcd_encode


def _freq_response_frame(freq_hz: int) -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_FREQ_GET,
        data=bcd_encode(freq_hz),
    )


def _bcd_byte(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def _scope_wave_frame(
    *,
    receiver: int = 0,
    mode: int = 1,
    start_hz: int = 14_000_000,
    end_hz: int = 14_350_000,
    pixels: bytes = b"\x10\x20\x30",
) -> bytes:
    payload = bytes(
        [
            receiver,
            _bcd_byte(1),
            _bcd_byte(1),
            mode,
            *bcd_encode(start_hz),
            *bcd_encode(end_hz),
            0x00,
            *pixels,
        ]
    )
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x27,
        sub=0x00,
        data=payload,
    )


async def _wait_until(predicate, *, timeout_s: float = 1.0) -> bool:  # type: ignore[no-untyped-def]
    deadline = asyncio.get_running_loop().time() + timeout_s
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return bool(predicate())


class _FakeSerialCivLink:
    def __init__(
        self,
        *,
        fail_connect: BaseException | None = None,
        fail_connect_calls: set[int] | None = None,
    ) -> None:
        self._fail_connect = fail_connect
        self._fail_connect_calls = set(fail_connect_calls or set())
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.connected = False
        self.ready = False
        self.healthy = False
        self.sent_frames: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}

    async def connect(self) -> None:
        self.connect_calls += 1
        if self.connect_calls in self._fail_connect_calls:
            raise OSError(f"connect failed on call {self.connect_calls}")
        if self._fail_connect is not None:
            raise self._fail_connect
        self.connected = True
        self.ready = True
        self.healthy = True

    async def disconnect(self) -> None:
        self.disconnect_calls += 1
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        if not self.connected:
            raise ConnectionError("Serial CI-V link is disconnected.")
        payload = bytes(frame)
        self.sent_frames.append(payload)
        send_no = len(self.sent_frames)
        for response in self._responses_by_send.pop(send_no, []):
            self._responses.put_nowait(response)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        if not self.connected:
            return None
        timeout_s = 0.05 if timeout is None else timeout
        try:
            return await asyncio.wait_for(self._responses.get(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None

    def queue_response_on_send(self, send_no: int, frame: bytes) -> None:
        self._responses_by_send.setdefault(send_no, []).append(frame)

    def queue_response(self, frame: bytes) -> None:
        self._responses.put_nowait(frame)


class _FakeUsbAudioDriver:
    def __init__(self) -> None:
        self.rx_running = False
        self.tx_running = False
        self._rx_callback = None
        self.tx_frames: list[bytes] = []
        self.rx_starts = 0
        self.tx_starts = 0

    async def start_rx(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        _ = kwargs
        if self.rx_running:
            raise RuntimeError("RX stream already started.")
        self.rx_running = True
        self.rx_starts += 1
        self._rx_callback = callback

    async def stop_rx(self) -> None:
        self.rx_running = False
        self._rx_callback = None

    async def start_tx(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
        if self.tx_running:
            raise RuntimeError("TX stream already started.")
        self.tx_running = True
        self.tx_starts += 1
        self.tx_start_kwargs: dict = dict(kwargs)

    async def stop_tx(self) -> None:
        self.tx_running = False

    async def _push_tx_pcm(self, frame: bytes) -> None:
        self.tx_frames.append(bytes(frame))

    def emit_rx_pcm(self, frame: bytes) -> None:
        if self._rx_callback is not None:
            self._rx_callback(frame)


@pytest.mark.asyncio
async def test_serial_radio_connect_disconnect_and_core_command_execution() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _freq_response_frame(14_074_000))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )

    await radio.connect()
    assert radio.connected is True
    assert radio.control_connected is True
    assert await radio.get_freq() == 14_074_000
    assert link.sent_frames
    assert radio.radio_ready is True

    await radio.disconnect()
    assert radio.connected is False
    assert radio.control_connected is False
    assert radio.radio_ready is False
    assert radio._civ_transport is None
    assert radio._civ_rx_task is None
    assert getattr(radio, "_civ_data_watchdog_task", None) is None


@pytest.mark.asyncio
async def test_serial_radio_connect_failure_sets_disconnected_state() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(fail_connect=OSError("permission denied")),
    )

    with pytest.raises(ConnectionError, match="Failed to connect serial session"):
        await radio.connect()

    assert radio.connected is False
    assert radio.control_connected is False
    assert radio.radio_ready is False


def test_serial_radio_rejects_unsupported_ptt_mode() -> None:
    with pytest.raises(ValueError, match="Unsupported serial PTT mode"):
        Icom7610SerialRadio(
            device="/dev/ttyUSB0",
            ptt_mode="rts",
        )


@pytest.mark.asyncio
async def test_serial_radio_ready_tracks_serial_link_health() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )

    await radio.connect()
    assert await _wait_until(lambda: radio.radio_ready)

    link.ready = False
    link.healthy = False
    assert await _wait_until(lambda: not radio.radio_ready)

    link.ready = True
    link.healthy = True
    assert await _wait_until(lambda: radio.radio_ready)

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_watchdog_retries_after_transient_soft_reconnect_failure() -> None:
    link = _FakeSerialCivLink(fail_connect_calls={2})
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    radio._SERIAL_WATCHDOG_INTERVAL_S = 0.05  # type: ignore[attr-defined]
    radio._SERIAL_WATCHDOG_RETRY_S = 0.01  # type: ignore[attr-defined]

    await radio.connect()
    assert link.connect_calls == 1
    assert radio.radio_ready is True

    link.ready = False
    link.healthy = False
    assert await _wait_until(lambda: link.connect_calls >= 3, timeout_s=2.0)
    assert await _wait_until(lambda: radio.radio_ready, timeout_s=2.0)
    assert radio.conn_state == RadioConnectionState.CONNECTED

    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_disconnect_cleans_watchdog_when_already_disconnected() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
    )
    radio._conn_state = RadioConnectionState.DISCONNECTED
    radio._civ_data_watchdog_task = asyncio.create_task(asyncio.sleep(10))
    await radio.disconnect()
    assert getattr(radio, "_civ_data_watchdog_task", None) is None


@pytest.mark.asyncio
async def test_serial_audio_opus_contract_uses_usb_driver_lifecycle() -> None:
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    received: list[bytes] = []
    await radio.start_audio_rx_opus(lambda packet: received.append(packet.data))
    usb_audio.emit_rx_pcm(b"\x01\x02" * 960)
    await asyncio.sleep(0.05)
    await radio.start_audio_tx_opus()
    await radio.push_audio_tx_opus(b"\x11\x22" * 960)
    await radio.stop_audio_tx_opus()
    await radio.stop_audio_rx_opus()
    await radio.disconnect()

    assert usb_audio.rx_starts == 1
    assert usb_audio.tx_starts == 1
    assert received
    assert received[0] == b"\x01\x02" * 960
    assert usb_audio.tx_frames[0] == b"\x11\x22" * 960


@pytest.mark.asyncio
async def test_serial_audio_pcm_contract_bridge_compatible() -> None:
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
        audio_codec=AudioCodec.OPUS_1CH,
    )
    await radio.connect()

    rx_pcm: list[bytes] = []
    await radio.start_audio_rx_pcm(lambda frame: rx_pcm.append(frame or b""))
    usb_audio.emit_rx_pcm(b"\x21\x43" * 960)
    await asyncio.sleep(0.05)

    await radio.start_audio_tx_pcm()
    await radio.push_audio_tx_pcm(b"\x10\x20" * 960)
    await radio.stop_audio_tx_pcm()
    await radio.stop_audio_rx_pcm()
    await radio.disconnect()

    assert rx_pcm
    assert rx_pcm[0] == b"\x21\x43" * 960
    assert usb_audio.tx_frames[0] == b"\x10\x20" * 960


@pytest.mark.asyncio
async def test_serial_audio_tx_requires_start() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=_FakeUsbAudioDriver(),
    )
    await radio.connect()
    with pytest.raises(RuntimeError, match="Audio TX not started"):
        await radio.push_audio_tx_opus(b"\x00" * 1920)
    await radio.disconnect()


def test_serial_scope_pacing_profile_is_separate_from_lan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ICOM_CIV_MIN_INTERVAL_MS", raising=False)
    monkeypatch.delenv("ICOM_SERIAL_CIV_MIN_INTERVAL_MS", raising=False)
    serial_radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
    )
    lan_radio = IcomRadio("192.168.55.40")
    assert serial_radio._civ_min_interval > lan_radio._civ_min_interval


@pytest.mark.asyncio
async def test_serial_scope_enable_disable_full_lifecycle_commands() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    await radio.enable_scope(policy="fast")
    await radio.disable_scope(policy="fast")
    await radio.disconnect()

    # Only parse full CI-V frames (min 6 bytes); skip short/control bytes
    signatures = []
    for frame in link.sent_frames:
        if len(frame) < 6:
            continue
        civ = parse_civ_frame(frame)
        signatures.append((civ.command, civ.sub, civ.data))

    assert len(signatures) >= 4, (
        f"Expected at least 4 scope CI-V frames, got {len(signatures)}"
    )
    assert signatures[0] == (0x27, 0x10, b"\x01")
    assert signatures[1] == (0x27, 0x11, b"\x01")
    assert signatures[2] == (0x27, 0x11, b"\x00")
    assert signatures[3] == (0x27, 0x10, b"\x00")


@pytest.mark.asyncio
async def test_serial_scope_capture_scope_frame() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_wave_frame(pixels=b"\x31\x32\x33"))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    frame = await radio.capture_scope_frame(timeout=1.0)
    await radio.disable_scope(policy="fast")
    await radio.disconnect()

    assert frame.receiver == 0
    assert frame.start_freq_hz == 14_000_000
    assert frame.end_freq_hz == 14_350_000
    assert frame.pixels == b"\x31\x32\x33"


@pytest.mark.asyncio
async def test_serial_scope_callback_streaming_path() -> None:
    link = _FakeSerialCivLink()
    link.queue_response_on_send(1, _scope_wave_frame(pixels=b"\x51\x52"))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    seen = []
    radio.on_scope_data(seen.append)
    await radio.enable_scope(policy="verify", timeout=1.0)
    assert await _wait_until(lambda: len(seen) == 1, timeout_s=1.0)
    await radio.disable_scope(policy="fast")
    await radio.disconnect()
    assert seen[0].pixels == b"\x51\x52"


@pytest.mark.asyncio
async def test_serial_scope_low_baud_guardrail_rejects_without_override() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        civ_link=_FakeSerialCivLink(),
    )
    await radio.connect()
    with pytest.raises(CommandError, match="baudrate"):
        await radio.enable_scope(policy="fast")
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_scope_enable_disconnected_low_baud_keeps_connection_error_contract() -> (
    None
):
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        civ_link=_FakeSerialCivLink(),
    )
    with pytest.raises(ConnectionError, match="Not connected"):
        await radio.enable_scope(policy="fast")


@pytest.mark.asyncio
async def test_serial_scope_low_baud_guardrail_override_allows_with_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        baudrate=19200,
        allow_low_baud_scope=True,
        civ_link=_FakeSerialCivLink(),
    )
    await radio.connect()
    with caplog.at_level("WARNING"):
        await radio.enable_scope(policy="fast")
    await radio.disable_scope(policy="fast")
    await radio.disconnect()
    assert "baudrate" in caplog.text.lower()
    assert "override" in caplog.text.lower()


@pytest.mark.asyncio
async def test_serial_scope_flood_does_not_starve_get_frequency() -> None:
    link = _FakeSerialCivLink()
    for _ in range(120):
        link.queue_response_on_send(3, _scope_wave_frame(pixels=b"\x11\x12\x13"))
    link.queue_response_on_send(3, _freq_response_frame(14_074_000))
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=link,
    )
    await radio.connect()
    await radio.enable_scope(policy="fast")
    assert await radio.get_freq() == 14_074_000
    await radio.disable_scope(policy="fast")
    await radio.disconnect()


# ---------------------------------------------------------------------------
# GH#1382 regression: TX always opens USB CODEC as mono (channels=1)
# IC-7610 USB CODEC mic input is mono-only; opening with channels=2 causes
# PortAudio to negotiate a 2-channel stream, producing 5-10s of TX artifacts
# while CoreAudio settles (regression introduced with stereo-first codec in
# PCM_2CH_16BIT becoming the global default, commit 8cc677df).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serial_tx_always_uses_mono_channels_regression_gh1382() -> None:
    """start_audio_tx_pcm always opens USB CODEC driver with channels=1 (GH#1382).

    Even when the global audio capabilities default to 2 channels (because
    PCM_2CH_16BIT is the preferred codec), the IC-7610 serial TX must open
    the USB CODEC with channels=1.  Callers that pass channels=2 (e.g. the
    CLI reading audio_caps.default_channels) must be clamped to mono.
    """
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
        audio_codec=AudioCodec.PCM_2CH_16BIT,  # stereo codec — reproduces regression
    )
    await radio.connect()

    # Simulate CLI passing channels=2 from audio_caps.default_channels
    await radio.start_audio_tx_pcm(sample_rate=48000, channels=2, frame_ms=20)

    # USB CODEC must be opened mono regardless of what caller requested
    assert usb_audio.tx_start_kwargs.get("channels") == 1, (
        "IC-7610 serial TX must open USB CODEC as mono (channels=1) "
        "regardless of the active audio codec or caller-supplied channels value"
    )
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_tx_default_uses_mono_channels() -> None:
    """Default call to start_audio_tx_pcm uses channels=1."""
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    await radio.start_audio_tx_pcm()
    assert usb_audio.tx_start_kwargs.get("channels") == 1
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_tx_accepts_none_args_resolving_to_defaults() -> None:
    """Explicit None args resolve to serial defaults (LSP parity with base).

    The base ``AudioRuntimeMixin.start_audio_tx_pcm`` accepts ``int | None``;
    the serial override must too, so a base-typed caller passing ``None`` does
    not hit a ``TypeError``.  ``None`` resolves to sample_rate=48000,
    frame_ms=20, channels=1 (USB CODEC mono clamp).
    """
    usb_audio = _FakeUsbAudioDriver()
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0",
        civ_link=_FakeSerialCivLink(),
        audio_driver=usb_audio,
    )
    await radio.connect()
    await radio.start_audio_tx_pcm(sample_rate=None, channels=None, frame_ms=None)
    assert usb_audio.tx_start_kwargs.get("sample_rate") == 48000
    assert usb_audio.tx_start_kwargs.get("frame_ms") == 20
    assert usb_audio.tx_start_kwargs.get("channels") == 1
    await radio.stop_audio_tx_pcm()
    await radio.disconnect()
