"""Opt-in PortAudio smoke for OS audio routing used by the pipeline harness."""

from __future__ import annotations

import asyncio
import os
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from rigplane.audio.backend import AudioDeviceInfo, PortAudioBackend
from rigplane.audio_bridge import FRAME_BYTES, SAMPLES_PER_FRAME

from _audio_pipeline_helpers import PcmDiagnostics, sine_pcm16_mono


ENABLE_ENV = "RIGPLANE_OS_AUDIO_SMOKE"
TX_DEVICE_ENV = "RIGPLANE_OS_AUDIO_TX_DEVICE"
RX_DEVICE_ENV = "RIGPLANE_OS_AUDIO_RX_DEVICE"
FRAME_COUNT_ENV = "RIGPLANE_OS_AUDIO_SMOKE_FRAMES"
DEFAULT_FRAME_COUNT = 12


@dataclass(frozen=True)
class _SelectedDevices:
    rx: AudioDeviceInfo
    tx: AudioDeviceInfo


def _format_device(device: AudioDeviceInfo) -> str:
    flags = []
    if device.is_default_input:
        flags.append("default-input")
    if device.is_default_output:
        flags.append("default-output")
    suffix = f" ({', '.join(flags)})" if flags else ""
    return (
        f"{int(device.id)}:{device.name}"
        f" in={device.input_channels} out={device.output_channels}{suffix}"
    )


def _format_devices(devices: list[AudioDeviceInfo]) -> str:
    if not devices:
        return "no PortAudio devices reported"
    return "; ".join(_format_device(device) for device in devices)


def _select_device(
    devices: list[AudioDeviceInfo],
    selector: str,
    *,
    env_name: str,
) -> AudioDeviceInfo:
    try:
        wanted_id = int(selector)
    except ValueError:
        wanted_id = None

    if wanted_id is not None:
        matches = [device for device in devices if int(device.id) == wanted_id]
    else:
        needle = selector.casefold()
        matches = [device for device in devices if needle in device.name.casefold()]

    if not matches:
        pytest.skip(
            f"{env_name}={selector!r} did not match any PortAudio device; "
            f"available devices: {_format_devices(devices)}"
        )
    if len(matches) > 1:
        pytest.skip(
            f"{env_name}={selector!r} matched multiple PortAudio devices; "
            f"use an integer device id. Matches: {_format_devices(matches)}"
        )
    return matches[0]


def _require_enabled() -> None:
    if os.environ.get(ENABLE_ENV) != "1":
        pytest.skip(
            f"opt-in OS audio smoke disabled; set {ENABLE_ENV}=1, "
            f"{TX_DEVICE_ENV}=<input device>, and {RX_DEVICE_ENV}=<output device> "
            "to exercise PortAudio"
        )


def _select_devices(backend: PortAudioBackend) -> _SelectedDevices:
    try:
        devices = backend.list_devices()
    except ImportError as exc:
        pytest.skip(f"PortAudio smoke skipped: sounddevice/numpy unavailable ({exc})")
    except Exception as exc:
        pytest.skip(f"PortAudio smoke skipped: could not list devices ({exc})")

    tx_selector = os.environ.get(TX_DEVICE_ENV)
    rx_selector = os.environ.get(RX_DEVICE_ENV)
    if not tx_selector or not rx_selector:
        pytest.skip(
            f"PortAudio smoke requires explicit {TX_DEVICE_ENV} input/capture "
            f"and {RX_DEVICE_ENV} output/playback selectors; available devices: "
            f"{_format_devices(devices)}"
        )

    tx_device = _select_device(devices, tx_selector, env_name=TX_DEVICE_ENV)
    rx_device = _select_device(devices, rx_selector, env_name=RX_DEVICE_ENV)

    if tx_device.input_channels < 1:
        pytest.skip(
            f"{TX_DEVICE_ENV} selected non-input device: "
            f"{_format_device(tx_device)}; available devices: {_format_devices(devices)}"
        )
    if rx_device.output_channels < 1:
        pytest.skip(
            f"{RX_DEVICE_ENV} selected non-output device: "
            f"{_format_device(rx_device)}; available devices: {_format_devices(devices)}"
        )

    for device, direction, env_name in (
        (tx_device, "rx", TX_DEVICE_ENV),
        (rx_device, "tx", RX_DEVICE_ENV),
    ):
        if not backend.check_sample_rate(device.id, 48_000, direction=direction):
            pytest.skip(
                f"{env_name} device does not accept 48000 Hz for {direction}: "
                f"{_format_device(device)}"
            )

    return _SelectedDevices(rx=rx_device, tx=tx_device)


def _frame_count() -> int:
    raw = os.environ.get(FRAME_COUNT_ENV)
    if raw is None:
        return DEFAULT_FRAME_COUNT
    try:
        value = int(raw)
    except ValueError:
        pytest.skip(f"{FRAME_COUNT_ENV} must be an integer, got {raw!r}")
    if value < 3:
        pytest.skip(f"{FRAME_COUNT_ENV} must be >= 3, got {value}")
    return value


def _append_input_frame(
    captured: bytearray,
    lock: threading.Lock,
) -> Callable[[Any, int, Any, Any], None]:
    def _callback(indata: Any, _frames: int, _time: Any, _status: Any) -> None:
        with lock:
            captured.extend(bytes(indata))

    return _callback


async def test_os_audio_smoke_captures_nonzero_loopback_audio(
    record_property: Callable[[str, object], None],
) -> None:
    _require_enabled()
    backend = PortAudioBackend()
    selected = _select_devices(backend)
    frame_count = _frame_count()
    sd = backend.sounddevice_module
    if sd is None:
        pytest.skip("PortAudio smoke skipped: sounddevice unavailable after setup")

    captured = bytearray()
    captured_lock = threading.Lock()
    input_stream = sd.RawInputStream(
        samplerate=48_000,
        channels=1,
        dtype="int16",
        device=int(selected.tx.id),
        blocksize=SAMPLES_PER_FRAME,
        latency="low",
        callback=_append_input_frame(captured, captured_lock),
    )
    output_stream = backend.open_tx(
        selected.rx.id,
        sample_rate=48_000,
        channels=1,
        frame_ms=20,
    )

    tone_frame = sine_pcm16_mono(1000.0, samples=SAMPLES_PER_FRAME)
    assert len(tone_frame) == FRAME_BYTES

    input_stream.start()
    await output_stream.start()
    try:
        for _ in range(frame_count):
            await output_stream.write(tone_frame)
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.25)
    finally:
        await output_stream.stop()
        input_stream.stop()
        input_stream.close()

    with captured_lock:
        payload = bytes(captured)
    diagnostics = PcmDiagnostics.from_pcm(payload)

    record_property("rx_device", _format_device(selected.rx))
    record_property("tx_device", _format_device(selected.tx))
    record_property("requested_frames", frame_count)
    record_property("captured_frames", diagnostics.frame_count)
    record_property("captured_peak", diagnostics.peak)
    record_property("captured_rms", f"{diagnostics.rms:.2f}")

    assert diagnostics.byte_count >= FRAME_BYTES, (
        "PortAudio smoke captured less than one frame; "
        f"rx_device={_format_device(selected.rx)}; "
        f"tx_device={_format_device(selected.tx)}; "
        f"requested_frames={frame_count}; bytes={diagnostics.byte_count}"
    )
    assert diagnostics.peak > 0, (
        "PortAudio smoke captured all-zero TX input audio; "
        f"rx_device={_format_device(selected.rx)}; "
        f"tx_device={_format_device(selected.tx)}; "
        f"requested_frames={frame_count}; frames={diagnostics.frame_count}; "
        f"bytes={diagnostics.byte_count}; peak={diagnostics.peak}; "
        f"rms={diagnostics.rms:.2f}"
    )
    assert diagnostics.rms > 0.0, (
        "PortAudio smoke captured zero-RMS TX input audio; "
        f"rx_device={_format_device(selected.rx)}; "
        f"tx_device={_format_device(selected.tx)}; "
        f"requested_frames={frame_count}; frames={diagnostics.frame_count}; "
        f"bytes={diagnostics.byte_count}; peak={diagnostics.peak}; "
        f"rms={diagnostics.rms:.2f}"
    )
