"""Universal USB audio driver for all serial-connected radios (macOS-first).

Supports automatic device resolution when multiple USB Audio devices
are present (e.g. IC-7300 + FTX-1 both connected via USB).
Works with any radio that exposes a standard USB Audio Class device:
Icom (IC-7300, IC-705, IC-9700), Yaesu (FTX-1, FT-710, FT-991A),
Kenwood (TS-890S, TS-590SG), etc.

See :mod:`icom_lan.usb_audio_resolve` for the topology-based resolution logic.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from typing import Any, Callable

from .backend import (
    AudioBackend,
    AudioDeviceId,
    AudioDeviceInfo,
    PortAudioBackend,
    RxStream,
    TxStream,
)

logger = logging.getLogger(__name__)

_DEPENDENCY_HINT = (
    "USB audio backend requires optional dependencies sounddevice and numpy. "
    "Install with: pip install icom-lan[bridge]"
)
_USB_NAME_PATTERNS: tuple[str, ...] = (
    "usb audio codec",
    "usb audio",
    "icom",
    "yaesu",
    "kenwood",
    "ftdi",
)


class AudioDeviceSelectionError(RuntimeError):
    """Raised when no suitable USB audio device can be selected."""


class AudioDriverLifecycleError(RuntimeError):
    """Raised on invalid USB audio lifecycle operations."""


@dataclass(frozen=True, slots=True)
class UsbAudioDevice:
    """Normalized USB audio device descriptor."""

    index: int
    name: str
    input_channels: int
    output_channels: int
    default_samplerate: int = 48_000
    is_default_input: bool = False
    is_default_output: bool = False
    platform_uid: str = ""

    @property
    def supports_rx(self) -> bool:
        """Whether the device can capture RX audio from radio (input channels)."""
        return self.input_channels > 0

    @property
    def supports_tx(self) -> bool:
        """Whether the device can play TX audio to radio (output channels)."""
        return self.output_channels > 0

    @property
    def duplex(self) -> bool:
        """Whether the device supports both capture and playback."""
        return self.supports_rx and self.supports_tx


def _safe_int(value: object, default: int = 0) -> int:
    try:
        if isinstance(value, (int, float, str, bytes, bytearray)):
            return int(value)
        return default
    except (TypeError, ValueError):
        return default


def _name_score(name: str) -> int:
    lowered = name.lower()
    for idx, pattern in enumerate(_USB_NAME_PATTERNS):
        if pattern in lowered:
            return idx
    return 99


def _find_by_override(
    devices: list[UsbAudioDevice],
    *,
    override: str,
    direction: str,
) -> UsbAudioDevice:
    if not override.strip():
        raise AudioDeviceSelectionError(
            f"{direction.upper()} device override must be a non-empty string."
        )
    exact = [dev for dev in devices if dev.name == override]
    if len(exact) == 1:
        return exact[0]
    exact_ci = [dev for dev in devices if dev.name.lower() == override.lower()]
    if len(exact_ci) == 1:
        return exact_ci[0]
    partial = [dev for dev in devices if override.lower() in dev.name.lower()]
    if len(partial) == 1:
        return partial[0]
    available = ", ".join(sorted(dev.name for dev in devices)) or "<none>"
    raise AudioDeviceSelectionError(
        f"Unknown {direction.upper()} device override {override!r}. "
        f"Available {direction.upper()} devices: {available}"
    )


def _auto_pick(
    devices: list[UsbAudioDevice],
    *,
    direction: str,
) -> UsbAudioDevice:
    if direction == "rx":
        default_attr = "is_default_input"
    else:
        default_attr = "is_default_output"
    ranked = sorted(
        devices,
        key=lambda dev: (
            _name_score(dev.name),
            0 if getattr(dev, default_attr) else 1,
            dev.index,
            dev.name.lower(),
        ),
    )
    return ranked[0]


def select_usb_audio_devices(
    devices: list[UsbAudioDevice],
    *,
    rx_device: str | None = None,
    tx_device: str | None = None,
) -> tuple[UsbAudioDevice, UsbAudioDevice]:
    """Select RX/TX devices deterministically with override precedence."""
    rx_candidates = [dev for dev in devices if dev.supports_rx]
    tx_candidates = [dev for dev in devices if dev.supports_tx]
    duplex_candidates = [dev for dev in devices if dev.duplex]

    if not rx_candidates:
        raise AudioDeviceSelectionError("No suitable RX USB audio device was found.")
    if not tx_candidates:
        raise AudioDeviceSelectionError("No suitable TX USB audio device was found.")

    if rx_device is None and tx_device is None and duplex_candidates:
        selected = _auto_pick(duplex_candidates, direction="rx")
        return selected, selected

    selected_rx: UsbAudioDevice
    selected_tx: UsbAudioDevice

    if rx_device is not None:
        selected_rx = _find_by_override(
            rx_candidates, override=rx_device, direction="rx"
        )
    elif tx_device is not None:
        selected_tx = _find_by_override(
            tx_candidates, override=tx_device, direction="tx"
        )
        selected_rx = (
            selected_tx
            if selected_tx.supports_rx
            else _auto_pick(rx_candidates, direction="rx")
        )
    else:
        selected_rx = _auto_pick(rx_candidates, direction="rx")

    if tx_device is not None:
        selected_tx = _find_by_override(
            tx_candidates, override=tx_device, direction="tx"
        )
    elif rx_device is not None and selected_rx.supports_tx:
        selected_tx = selected_rx
    else:
        selected_tx = (
            selected_rx
            if selected_rx.supports_tx
            else _auto_pick(tx_candidates, direction="tx")
        )

    return selected_rx, selected_tx


def _get_uid_map() -> dict[str, str]:
    """Return CoreAudio name→UID map on macOS, empty dict elsewhere."""
    if sys.platform != "darwin":
        return {}
    try:
        from icom_lan.audio._macos_uid import get_device_uid_map

        return get_device_uid_map()
    except Exception:
        logger.debug("CoreAudio UID lookup unavailable", exc_info=True)
        return {}


def _device_info_to_usb(
    info: AudioDeviceInfo,
    uid_map: dict[str, str],
) -> UsbAudioDevice:
    """Convert an :class:`AudioDeviceInfo` to a :class:`UsbAudioDevice`."""
    return UsbAudioDevice(
        index=int(info.id),
        name=info.name,
        input_channels=info.input_channels,
        output_channels=info.output_channels,
        default_samplerate=info.default_samplerate,
        is_default_input=info.is_default_input,
        is_default_output=info.is_default_output,
        platform_uid=uid_map.get(info.name, ""),
    )


def _devices_from_backend(backend: AudioBackend) -> list[UsbAudioDevice]:
    """List devices from a backend, enriching with platform UIDs."""
    uid_map = _get_uid_map()
    return [_device_info_to_usb(info, uid_map) for info in backend.list_devices()]


def list_usb_audio_devices(sounddevice_module: Any) -> list[UsbAudioDevice]:
    """Return normalized system audio devices.

    .. deprecated::
        Prefer :func:`_devices_from_backend` with an :class:`AudioBackend`.
        This function is kept for backward compatibility with CLI code.
    """
    raw_devices = list(sounddevice_module.query_devices())
    default_input_idx: int | None = None
    default_output_idx: int | None = None
    default_raw = getattr(getattr(sounddevice_module, "default", None), "device", None)
    if isinstance(default_raw, (list, tuple)) and len(default_raw) >= 2:
        default_input_idx = _safe_int(default_raw[0], default=-1)
        default_output_idx = _safe_int(default_raw[1], default=-1)

    uid_map = _get_uid_map()

    normalized: list[UsbAudioDevice] = []
    for idx, raw in enumerate(raw_devices):
        index = _safe_int(raw.get("index", idx), default=idx)
        name = str(raw.get("name", f"device-{index}"))
        normalized.append(
            UsbAudioDevice(
                index=index,
                name=name,
                input_channels=_safe_int(raw.get("max_input_channels")),
                output_channels=_safe_int(raw.get("max_output_channels")),
                default_samplerate=_safe_int(
                    raw.get("default_samplerate"), default=48_000
                ),
                is_default_input=(
                    default_input_idx is not None and index == default_input_idx
                ),
                is_default_output=(
                    default_output_idx is not None and index == default_output_idx
                ),
                platform_uid=uid_map.get(name, ""),
            )
        )
    return normalized


def _extract_sounddevice_module(backend: AudioBackend) -> Any | None:
    """Extract the underlying sounddevice module from a PortAudioBackend."""
    if isinstance(backend, PortAudioBackend):
        return backend.sounddevice_module
    return None


class UsbAudioDriver:
    """Stateful USB audio driver with deterministic device selection.

    Delegates stream I/O to an :class:`AudioBackend` while retaining
    the USB-specific device selection heuristics and topology resolution.
    """

    _BYTES_PER_SAMPLE = 2  # s16le

    def __init__(
        self,
        *,
        rx_device: str | None = None,
        tx_device: str | None = None,
        serial_port: str | None = None,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        backend: AudioBackend | None = None,
    ) -> None:
        self._rx_device_override = rx_device
        self._tx_device_override = tx_device
        self._serial_port = serial_port
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_ms = frame_ms
        self._backend: AudioBackend = backend or PortAudioBackend()

        self._selected_rx: UsbAudioDevice | None = None
        self._selected_tx: UsbAudioDevice | None = None

        self._rx_stream: RxStream | None = None
        self._tx_stream: TxStream | None = None

        self._rx_lock = asyncio.Lock()
        self._tx_lock = asyncio.Lock()

    @property
    def rx_running(self) -> bool:
        return self._rx_stream is not None and self._rx_stream.running

    @property
    def tx_running(self) -> bool:
        return self._tx_stream is not None and self._tx_stream.running

    @property
    def selected_rx_device(self) -> UsbAudioDevice | None:
        return self._selected_rx

    @property
    def selected_tx_device(self) -> UsbAudioDevice | None:
        return self._selected_tx

    def _ensure_selected_devices(self) -> tuple[UsbAudioDevice, UsbAudioDevice]:
        devices = _devices_from_backend(self._backend)

        # If serial_port is set and no explicit rx/tx overrides, try
        # topology-based resolution to find the correct audio pair.
        if (
            self._serial_port
            and self._rx_device_override is None
            and self._tx_device_override is None
        ):
            resolved = self._try_resolve_from_serial(devices)
            if resolved is not None:
                self._selected_rx, self._selected_tx = resolved
                return resolved

        selected_rx, selected_tx = select_usb_audio_devices(
            devices,
            rx_device=self._rx_device_override,
            tx_device=self._tx_device_override,
        )
        self._selected_rx = selected_rx
        self._selected_tx = selected_tx
        return selected_rx, selected_tx

    def _try_resolve_from_serial(
        self,
        devices: list[UsbAudioDevice],
    ) -> tuple[UsbAudioDevice, UsbAudioDevice] | None:
        """Attempt topology-based audio device resolution from serial port."""
        sd_module = _extract_sounddevice_module(self._backend)
        if sd_module is None:
            logger.debug(
                "usb-audio: topology resolution skipped — backend %s "
                "is not PortAudioBackend; falling back to name-based selection",
                type(self._backend).__name__,
            )
            return None

        from ..usb_audio_resolve import resolve_audio_for_serial_port

        mapping = resolve_audio_for_serial_port(
            self._serial_port,  # type: ignore[arg-type]
            sounddevice_module=sd_module,
        )
        if mapping is None:
            return None

        rx_dev = next((d for d in devices if d.index == mapping.rx_device_index), None)
        tx_dev = next((d for d in devices if d.index == mapping.tx_device_index), None)
        if rx_dev is None or tx_dev is None:
            logger.warning(
                "usb-audio: topology resolved indices [%d, %d] but devices "
                "not found in normalized list",
                mapping.rx_device_index,
                mapping.tx_device_index,
            )
            return None

        logger.info(
            "usb-audio: topology-resolved devices for %s: RX=[%d] %s, TX=[%d] %s",
            self._serial_port,
            rx_dev.index,
            rx_dev.name,
            tx_dev.index,
            tx_dev.name,
        )
        return rx_dev, tx_dev

    def list_devices(self) -> list[UsbAudioDevice]:
        """List normalized devices from the active audio backend."""
        return _devices_from_backend(self._backend)

    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        """Start capture loop and deliver PCM frames to callback."""
        if callback is None:
            raise AudioDriverLifecycleError("Audio RX callback is required.")
        if not callable(callback):
            raise TypeError("Audio RX callback must be callable.")

        async with self._rx_lock:
            if self.rx_running:
                raise AudioDriverLifecycleError("RX stream already started.")

            selected_rx, _ = self._ensure_selected_devices()
            sr = self._sample_rate if sample_rate is None else sample_rate
            ch = self._channels if channels is None else channels
            fm = self._frame_ms if frame_ms is None else frame_ms
            if (sr * fm) % 1000 != 0:
                raise AudioDriverLifecycleError(
                    "Invalid RX frame format: sample_rate * frame_ms must be divisible by 1000."
                )
            self._rx_stream = self._backend.open_rx(
                AudioDeviceId(selected_rx.index),
                sample_rate=sr,
                channels=ch,
                frame_ms=fm,
            )
            await self._rx_stream.start(callback)

    async def stop_rx(self) -> None:
        """Stop capture loop and close RX stream."""
        async with self._rx_lock:
            stream = self._rx_stream
            self._rx_stream = None
            if stream is not None and stream.running:
                await stream.stop()

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        """Start playback loop for outgoing PCM frames."""
        async with self._tx_lock:
            if self.tx_running:
                raise AudioDriverLifecycleError("TX stream already started.")

            _, selected_tx = self._ensure_selected_devices()
            sr = self._sample_rate if sample_rate is None else sample_rate
            ch = self._channels if channels is None else channels
            fm = self._frame_ms if frame_ms is None else frame_ms
            if (sr * fm) % 1000 != 0:
                raise AudioDriverLifecycleError(
                    "Invalid TX frame format: sample_rate * frame_ms must be divisible by 1000."
                )
            self._tx_stream = self._backend.open_tx(
                AudioDeviceId(selected_tx.index),
                sample_rate=sr,
                channels=ch,
                frame_ms=fm,
            )
            await self._tx_stream.start()

    async def _push_tx_pcm(self, frame: bytes) -> None:
        """Queue one PCM frame for playback."""
        if not self.tx_running:
            raise AudioDriverLifecycleError("Audio TX stream is not started.")
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("PCM TX frame must be bytes-like.")
        assert self._tx_stream is not None
        await self._tx_stream.write(bytes(frame))

    async def stop_tx(self) -> None:
        """Stop playback loop and close TX stream."""
        async with self._tx_lock:
            stream = self._tx_stream
            self._tx_stream = None
            if stream is not None and stream.running:
                await stream.stop()


__all__ = [
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "UsbAudioDevice",
    "UsbAudioDriver",
    "list_usb_audio_devices",
    "select_usb_audio_devices",
]
