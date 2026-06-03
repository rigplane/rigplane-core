"""Universal USB audio driver for all serial-connected radios (macOS-first).

Supports automatic device resolution when multiple USB Audio devices
are present (e.g. IC-7300 + FTX-1 both connected via USB).
Works with any radio that exposes a standard USB Audio Class device:
Icom (IC-7300, IC-705, IC-9700), Yaesu (FTX-1, FT-710, FT-991A),
Kenwood (TS-890S, TS-590SG), etc.

See :mod:`rigplane.usb_audio_resolve` for the topology-based resolution logic.
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
    _platform_uid_from_device_name,
)

logger = logging.getLogger(__name__)

_DEPENDENCY_HINT = (
    "USB audio backend requires optional dependencies sounddevice and numpy. "
    "Install with: pip install rigplane[bridge]"
)
# Ordered by selection preference (lower index = stronger match). The
# C-Media identity ranks the Xiegu X6200's audio codec ahead of an unknown
# commodity device on platforms without topology resolution (MOR-219). It is
# placed after the explicit "usb audio" names so a vendor CODEC still wins
# when both are present.
_USB_NAME_PATTERNS: tuple[str, ...] = (
    "usb audio codec",
    "usb audio",
    "icom",
    "yaesu",
    "kenwood",
    "c-media",
    "cmedia",
    "ftdi",
)


class AudioDeviceSelectionError(RuntimeError):
    """Raised when no suitable USB audio device can be selected."""


class AudioDriverLifecycleError(RuntimeError):
    """Raised on invalid USB audio lifecycle operations."""


_DEFAULT_SAMPLE_RATE_CANDIDATES: tuple[int, ...] = (48_000, 24_000, 16_000, 8_000)


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

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-friendly device descriptor for diagnostics."""

        return {
            "index": self.index,
            "name": self.name,
            "input_channels": self.input_channels,
            "output_channels": self.output_channels,
            "default_samplerate": self.default_samplerate,
            "platform_uid": self.platform_uid,
        }


@dataclass(frozen=True, slots=True)
class UsbAudioStreamContract:
    """Effective OS audio-device stream contract for one direction."""

    direction: str
    device: UsbAudioDevice
    sample_rate_hz: int
    channels: int
    frame_ms: int
    sample_rate_source: str
    channel_source: str = "requested"
    fallback_reason: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "direction": self.direction,
            "device": self.device.to_dict(),
            "sample_rate_hz": self.sample_rate_hz,
            "channels": self.channels,
            "frame_ms": self.frame_ms,
            "sample_rate_source": self.sample_rate_source,
            "channel_source": self.channel_source,
        }
        if self.fallback_reason:
            payload["fallback_reason"] = self.fallback_reason
        return payload


@dataclass(frozen=True, slots=True)
class UsbAudioContract:
    """Effective RX/TX OS audio contract for a USB-audio radio path."""

    rx: UsbAudioStreamContract | None = None
    tx: UsbAudioStreamContract | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "rx": self.rx.to_dict() if self.rx is not None else None,
            "tx": self.tx.to_dict() if self.tx is not None else None,
        }


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


def _format_device_choices(devices: list[UsbAudioDevice]) -> str:
    choices: list[str] = []
    for dev in sorted(devices, key=lambda item: (item.index, item.name.lower())):
        label = f"[{dev.index}] {dev.name}"
        if dev.platform_uid:
            label = f"{label} ({dev.platform_uid})"
        choices.append(label)
    return ", ".join(choices) or "<none>"


def _unique_override_match(
    matches: list[UsbAudioDevice],
    *,
    override: str,
    direction: str,
) -> UsbAudioDevice | None:
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        available = _format_device_choices(matches)
        raise AudioDeviceSelectionError(
            f"Ambiguous {direction.upper()} device override {override!r}. "
            f"Matching {direction.upper()} devices: {available}"
        )
    return None


def _find_by_override(
    devices: list[UsbAudioDevice],
    *,
    override: str,
    direction: str,
) -> UsbAudioDevice:
    selector = override.strip()
    if not selector:
        raise AudioDeviceSelectionError(
            f"{direction.upper()} device override must be a non-empty string."
        )

    index_matches = [
        dev
        for dev in devices
        if selector in {str(dev.index), f"[{dev.index}]", f"index:{dev.index}"}
    ]
    match = _unique_override_match(
        index_matches,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    exact = [
        dev
        for dev in devices
        if dev.name == selector or (dev.platform_uid and dev.platform_uid == selector)
    ]
    match = _unique_override_match(
        exact,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    selector_ci = selector.lower()
    exact_ci = [
        dev
        for dev in devices
        if dev.name.lower() == selector_ci
        or (dev.platform_uid and dev.platform_uid.lower() == selector_ci)
    ]
    match = _unique_override_match(
        exact_ci,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    partial = [
        dev
        for dev in devices
        if selector_ci in dev.name.lower()
        or (dev.platform_uid and selector_ci in dev.platform_uid.lower())
    ]
    match = _unique_override_match(
        partial,
        override=selector,
        direction=direction,
    )
    if match is not None:
        return match

    available = _format_device_choices(devices)
    raise AudioDeviceSelectionError(
        f"Unknown {direction.upper()} device override {selector!r}. "
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
        from rigplane.audio._macos_uid import get_device_uid_map

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
        platform_uid=(
            info.platform_uid
            or uid_map.get(info.name, "")
            or _platform_uid_from_device_name(info.name)
        ),
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
                platform_uid=uid_map.get(name, "")
                or _platform_uid_from_device_name(name),
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
        self._usb_audio_contract = UsbAudioContract()

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

    @property
    def usb_audio_contract(self) -> UsbAudioContract | None:
        if self._usb_audio_contract.rx is None and self._usb_audio_contract.tx is None:
            return None
        return self._usb_audio_contract

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

    def _sample_rate_candidates(self, requested: int) -> tuple[int, ...]:
        return tuple(dict.fromkeys((requested, *_DEFAULT_SAMPLE_RATE_CANDIDATES)))

    def _clamp_channels(
        self,
        *,
        direction: str,
        device: UsbAudioDevice,
        requested_channels: int,
    ) -> tuple[int, str, str | None]:
        """Clamp requested channels to the device's real capability (MOR-238).

        A profile / codec may request stereo from a device that only exposes a
        mono capture (or playback) endpoint. PortAudio rejects that open with
        PaErrorCode -9998 ("Invalid number of channels"). Mirroring the
        sample-rate negotiation, the effective channel count is clamped down to
        what the device advertises so any mono/stereo USB codec self-heals
        without a per-profile ``codec_preference`` entry. Downstream DSP is
        48 kHz mono, so a 1-channel capture feeds the broadcaster cleanly.
        """

        device_max = (
            device.input_channels if direction == "rx" else device.output_channels
        )
        # A device advertising zero channels for this direction is a selection
        # bug, not a clamp target — leave the request untouched and let the
        # open surface the real error.
        if device_max >= 1 and requested_channels > device_max:
            reason = f"channels-{requested_channels}-clamped-to-device-{device_max}"
            return device_max, "device-clamp", reason
        return requested_channels, "requested", None

    def _resolve_stream_contract(
        self,
        *,
        direction: str,
        device: UsbAudioDevice,
        requested_sample_rate: int,
        channels: int,
        frame_ms: int,
        allow_sample_rate_fallback: bool,
    ) -> UsbAudioStreamContract:
        device_id = AudioDeviceId(device.index)
        effective_channels, channel_source, channel_reason = self._clamp_channels(
            direction=direction,
            device=device,
            requested_channels=channels,
        )
        if self._backend.check_sample_rate(
            device_id,
            requested_sample_rate,
            direction=direction,
        ):
            source = "default" if allow_sample_rate_fallback else "explicit"
            return UsbAudioStreamContract(
                direction=direction,
                device=device,
                sample_rate_hz=requested_sample_rate,
                channels=effective_channels,
                frame_ms=frame_ms,
                sample_rate_source=source,
                channel_source=channel_source,
                fallback_reason=channel_reason,
            )

        if not allow_sample_rate_fallback:
            raise AudioDriverLifecycleError(
                f"Explicit {direction.upper()} sample rate "
                f"{requested_sample_rate} Hz is not supported by [{device.index}] "
                f"{device.name}."
            )

        for candidate in self._sample_rate_candidates(requested_sample_rate):
            if candidate == requested_sample_rate:
                continue
            if self._backend.check_sample_rate(
                device_id,
                candidate,
                direction=direction,
            ):
                sample_reason = f"sample-rate-{requested_sample_rate}-unsupported"
                fallback_reason = (
                    f"{sample_reason}; {channel_reason}"
                    if channel_reason
                    else sample_reason
                )
                return UsbAudioStreamContract(
                    direction=direction,
                    device=device,
                    sample_rate_hz=candidate,
                    channels=effective_channels,
                    frame_ms=frame_ms,
                    sample_rate_source="fallback",
                    channel_source=channel_source,
                    fallback_reason=fallback_reason,
                )

        raise AudioDriverLifecycleError(
            f"No supported {direction.upper()} sample rate found for [{device.index}] "
            f"{device.name}; tried {list(self._sample_rate_candidates(requested_sample_rate))}."
        )

    def _store_stream_contract(self, contract: UsbAudioStreamContract) -> None:
        if contract.direction == "rx":
            self._usb_audio_contract = UsbAudioContract(
                rx=contract,
                tx=self._usb_audio_contract.tx,
            )
        else:
            self._usb_audio_contract = UsbAudioContract(
                rx=self._usb_audio_contract.rx,
                tx=contract,
            )

    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
        allow_sample_rate_fallback: bool = True,
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
            contract = self._resolve_stream_contract(
                direction="rx",
                device=selected_rx,
                requested_sample_rate=sr,
                channels=ch,
                frame_ms=fm,
                allow_sample_rate_fallback=allow_sample_rate_fallback,
            )
            # Log the effective capture request before opening the
            # InputStream. ``input_channels`` is included because a codec /
            # device channel-count mismatch is the failure mode behind
            # MOR-236: requesting more channels than the mono USB CODEC
            # exposes makes PortAudio reject the stream with "Invalid number
            # of channels" (PaErrorCode -9998), which previously surfaced
            # only as an opaque "audio-bus: failed to start RX" with zero
            # RX frames reaching the browser.
            logger.info(
                "usb-audio: opening RX capture — device=[%d] %s, %d Hz, "
                "%d ch (requested %d, source=%s), %d ms "
                "(device input_channels=%d)",
                selected_rx.index,
                selected_rx.name,
                contract.sample_rate_hz,
                contract.channels,
                ch,
                contract.channel_source,
                fm,
                selected_rx.input_channels,
            )
            self._rx_stream = self._backend.open_rx(
                AudioDeviceId(selected_rx.index),
                sample_rate=contract.sample_rate_hz,
                channels=contract.channels,
                frame_ms=fm,
            )
            await self._rx_stream.start(callback)
            self._store_stream_contract(contract)
            logger.info(
                "usb-audio: RX capture running — device=[%d] %s",
                selected_rx.index,
                selected_rx.name,
            )

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
        allow_sample_rate_fallback: bool = True,
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
            contract = self._resolve_stream_contract(
                direction="tx",
                device=selected_tx,
                requested_sample_rate=sr,
                channels=ch,
                frame_ms=fm,
                allow_sample_rate_fallback=allow_sample_rate_fallback,
            )
            self._tx_stream = self._backend.open_tx(
                AudioDeviceId(selected_tx.index),
                sample_rate=contract.sample_rate_hz,
                channels=contract.channels,
                frame_ms=fm,
            )
            await self._tx_stream.start()
            self._store_stream_contract(contract)

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
    "UsbAudioContract",
    "UsbAudioDriver",
    "UsbAudioStreamContract",
    "list_usb_audio_devices",
    "select_usb_audio_devices",
]
