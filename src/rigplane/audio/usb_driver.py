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
from typing import Any, Callable, Literal

from .backend import (
    AudioBackend,
    AudioDeviceConfig,
    AudioDeviceId,
    AudioDeviceInfo,
    DuplexStream,
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


class AudioAlreadyStartedError(AudioDriverLifecycleError):
    """Raised when starting an audio stream that is already running.

    Shared by the USB driver and the Icom LAN stream (MOR-563) so
    consumers can match the benign double-start case by type instead of
    message text. Subclasses :class:`AudioDriverLifecycleError` (and thus
    ``RuntimeError``) so existing ``except`` clauses keep catching it.
    """


class AudioNotStartedError(AudioDriverLifecycleError):
    """Raised when using an audio stream that has not been started.

    Shared by the USB driver and the Icom LAN stream (MOR-563).
    Subclasses :class:`AudioDriverLifecycleError` (and thus
    ``RuntimeError``) so existing ``except`` clauses keep catching it.
    """


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
    open_channels: int | None = None
    """Device-native channel count the OS stream is opened at.

    ``None`` means the stream opens at ``channels`` (open == deliver). It is set
    only when the device is opened at MORE channels than downstream consumes —
    i.e. a mono (deliver=1) request on a stereo-native device (open=2): the
    stream opens at the native count and software-downmixes back to ``channels``
    (MOR-504). ``channels`` always stays the DELIVERED count (downstream DSP is
    48 kHz mono), so diagnostics and the fixed-frame contract see the mono path.
    """

    @property
    def effective_open_channels(self) -> int:
        """Channel count to open the OS stream at (defaults to ``channels``)."""
        return self.channels if self.open_channels is None else self.open_channels

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
        if self.open_channels is not None:
            payload["open_channels"] = self.open_channels
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


def resolve_usb_duplex_mode(
    rx_dev: UsbAudioDevice,
    tx_dev: UsbAudioDevice,
) -> Literal["full", "exclusive"]:
    """Resolve the USB duplex policy for a selected RX/TX device pair.

    Returns ``"exclusive"`` iff the platform is macOS AND RX and TX resolve
    to the same device index AND the device is a real CODEC (not a virtual
    loopback such as BlackHole/VB-Cable). On macOS CoreAudio, two separate
    streams on one real C-Media USB CODEC fail with AUHAL -50 (MOR-531), so
    such a device must be owned exclusively by ONE full-duplex stream.
    Everything else — separate devices, virtual loopbacks, non-macOS
    platforms — supports the two-stream path: ``"full"``.

    Pure read-only policy (MOR-534, AudioTransport 1/12). Consumed via
    :attr:`UsbAudioDriver.duplex_mode`, which the backends expose as
    ``audio_duplex_mode`` for the AudioSession's setup-order sequencing.
    """
    if sys.platform != "darwin":
        return "full"
    if rx_dev.index != tx_dev.index:
        return "full"
    # Single source of truth with the bridge path: reuse (not move) the
    # virtual-loopback predicate. Imported lazily and called via the module
    # attribute so existing monkeypatch targets on ``rigplane.audio.bridge``
    # keep steering both consumers.
    from rigplane.audio import bridge

    info = AudioDeviceInfo(
        id=AudioDeviceId(rx_dev.index),
        name=rx_dev.name,
        input_channels=rx_dev.input_channels,
        output_channels=rx_dev.output_channels,
    )
    if bridge._is_virtual_loopback_device(info):
        return "full"
    return "exclusive"


class UsbAudioDriver:
    """Stateful USB audio driver with deterministic device selection.

    Delegates stream I/O to an :class:`AudioBackend` while retaining
    the USB-specific device selection heuristics and topology resolution.
    """

    _BYTES_PER_SAMPLE = 2  # s16le

    def __init__(
        self,
        config: AudioDeviceConfig | None = None,
        *,
        rx_device: str | None = None,
        tx_device: str | None = None,
        serial_port: str | None = None,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        backend: AudioBackend | None = None,
        rx_audio_channel: str = "mix",
    ) -> None:
        # ONE per-device config carrier (MOR-578). Callers either hand a
        # ready-made :class:`AudioDeviceConfig` or use the historical keyword
        # parameters, from which an identical carrier is built (back-compat:
        # when ``config`` is provided it is authoritative and the per-keyword
        # equivalents are ignored). ``serial_port`` (topology-resolution hint)
        # and ``backend`` (stream implementation) are not device config and
        # stay separate keywords.
        #
        # ``rx_audio_channel`` — stereo→mono downmix selection (MOR-508):
        # "mix" = (L+R)//2 (default, unchanged for every rig), "left"/"right"
        # = that channel at full level. Only consulted when a mono request
        # opens a stereo-native device (MOR-504 under-request downmix). The
        # FTX-1 sets "left" because its USB RX audio is on the LEFT channel
        # only.
        self._config = (
            config
            if config is not None
            else AudioDeviceConfig(
                rx_device=rx_device,
                tx_device=tx_device,
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
                rx_audio_channel=rx_audio_channel,
            )
        )
        self._serial_port = serial_port
        self._backend: AudioBackend = backend or PortAudioBackend()

        self._selected_rx: UsbAudioDevice | None = None
        self._selected_tx: UsbAudioDevice | None = None

        self._rx_stream: RxStream | None = None
        self._tx_stream: TxStream | None = None
        # Single full-duplex stream for the same-device RX+TX case (MOR-531):
        # opening separate InputStream + OutputStream on one C-Media CODEC fails
        # with macOS CoreAudio AUHAL -50. When set, it drives BOTH directions.
        self._duplex_stream: DuplexStream | None = None
        self._usb_audio_contract = UsbAudioContract()

        self._rx_lock = asyncio.Lock()
        self._tx_lock = asyncio.Lock()

    @property
    def rx_running(self) -> bool:
        if self._duplex_stream is not None:
            return self._duplex_stream.running
        return self._rx_stream is not None and self._rx_stream.running

    @property
    def tx_running(self) -> bool:
        if self._duplex_stream is not None:
            return self._duplex_stream.running
        return self._tx_stream is not None and self._tx_stream.running

    @property
    def selected_rx_device(self) -> UsbAudioDevice | None:
        return self._selected_rx

    @property
    def selected_tx_device(self) -> UsbAudioDevice | None:
        return self._selected_tx

    @property
    def duplex_mode(self) -> Literal["full", "exclusive"]:
        """USB duplex policy for the resolved RX/TX pair (lazy, read-only).

        Resolves devices via the normal selection path on first access; see
        :func:`resolve_usb_duplex_mode` for the policy itself.
        """
        rx, tx = self._selected_rx, self._selected_tx
        if rx is None or tx is None:
            rx, tx = self._ensure_selected_devices()
        return resolve_usb_duplex_mode(rx, tx)

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
            and self._config.rx_device is None
            and self._config.tx_device is None
        ):
            resolved = self._try_resolve_from_serial(devices)
            if resolved is not None:
                self._selected_rx, self._selected_tx = resolved
                return resolved

        selected_rx, selected_tx = select_usb_audio_devices(
            devices,
            rx_device=self._config.rx_device,
            tx_device=self._config.tx_device,
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
    ) -> tuple[int, int, str, str | None]:
        """Reconcile requested channels with the device's real capability.

        Returns ``(deliver_channels, open_channels, channel_source,
        fallback_reason)``:

        - ``deliver_channels`` — the count delivered downstream (the broadcaster
          / FFT scope contract is 48 kHz mono).
        - ``open_channels`` — the count the OS stream is opened at.

        Two reconciliation directions, both healing a codec/device mismatch that
        PortAudio otherwise rejects with PaErrorCode -9998 ("Invalid number of
        channels") on Windows, or AUHAL -10863 on macOS CoreAudio:

        - **Over-request (MOR-238)** — a profile / codec asks for MORE channels
          than the device exposes (stereo request on a mono capture endpoint).
          Both open and deliver clamp DOWN to ``device_max``; downstream is mono,
          so a 1-channel capture feeds the broadcaster cleanly.
        - **Under-request (MOR-504)** — a mono (deliver=1) request on a
          stereo-NATIVE device. macOS AUHAL refuses to open a 2-channel device at
          1 channel (err -10863), so the stream opens at the device-native count
          and the RX stream software-downmixes back to mono before delivery. RX
          path only (TX never up-opens — there is no mix-up direction for
          playback).
        """

        device_max = (
            device.input_channels if direction == "rx" else device.output_channels
        )
        # A device advertising zero channels for this direction is a selection
        # bug, not a reconcile target — leave the request untouched and let the
        # open surface the real error.
        if device_max < 1:
            return requested_channels, requested_channels, "requested", None
        if requested_channels > device_max:
            reason = f"channels-{requested_channels}-clamped-to-device-{device_max}"
            return device_max, device_max, "device-clamp", reason
        if direction == "rx" and requested_channels < device_max:
            # Open native, deliver the (mono) request via software downmix.
            reason = (
                f"channels-{requested_channels}-opened-as-device-{device_max}-downmix"
            )
            return requested_channels, device_max, "device-native", reason
        return requested_channels, requested_channels, "requested", None

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
        (
            deliver_channels,
            open_channels,
            channel_source,
            channel_reason,
        ) = self._clamp_channels(
            direction=direction,
            device=device,
            requested_channels=channels,
        )
        # Only carry ``open_channels`` when it differs from the delivered count
        # (under-request downmix); otherwise the OS stream opens at ``channels``.
        open_field = open_channels if open_channels != deliver_channels else None
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
                channels=deliver_channels,
                frame_ms=frame_ms,
                sample_rate_source=source,
                channel_source=channel_source,
                fallback_reason=channel_reason,
                open_channels=open_field,
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
                    channels=deliver_channels,
                    frame_ms=frame_ms,
                    sample_rate_source="fallback",
                    channel_source=channel_source,
                    fallback_reason=fallback_reason,
                    open_channels=open_field,
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
                raise AudioAlreadyStartedError("RX stream already started.")

            selected_rx, _ = self._ensure_selected_devices()
            sr = self._config.sample_rate if sample_rate is None else sample_rate
            ch = self._config.channels if channels is None else channels
            fm = self._config.frame_ms if frame_ms is None else frame_ms
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
                "open %d ch / deliver %d ch (requested %d, source=%s), %d ms "
                "(device input_channels=%d)",
                selected_rx.index,
                selected_rx.name,
                contract.sample_rate_hz,
                contract.effective_open_channels,
                contract.channels,
                ch,
                contract.channel_source,
                fm,
                selected_rx.input_channels,
            )
            self._rx_stream = self._backend.open_rx(
                AudioDeviceId(selected_rx.index),
                sample_rate=contract.sample_rate_hz,
                channels=contract.effective_open_channels,
                frame_ms=fm,
                deliver_channels=contract.channels,
                rx_audio_channel=self._config.rx_audio_channel,
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
                raise AudioAlreadyStartedError("TX stream already started.")

            _, selected_tx = self._ensure_selected_devices()
            sr = self._config.sample_rate if sample_rate is None else sample_rate
            ch = self._config.channels if channels is None else channels
            fm = self._config.frame_ms if frame_ms is None else frame_ms
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

    async def start_duplex(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
        allow_sample_rate_fallback: bool = True,
    ) -> None:
        """Start a single full-duplex RX+TX stream on the SAME USB CODEC.

        Opens ONE ``sd.Stream`` via :meth:`AudioBackend.open_duplex` when RX and
        TX resolve to the same device, so a USB-CODEC radio can transmit while RX
        capture keeps running — avoiding the macOS CoreAudio AUHAL -50 that two
        separate streams cause on one C-Media device (MOR-531). RX frames go to
        *callback*; TX frames are pushed via :meth:`_push_tx_pcm` (routed through
        the duplex stream's TX queue). The two-stream :meth:`start_rx` /
        :meth:`start_tx` path is unchanged for separate-device use.
        """
        if callback is None:
            raise AudioDriverLifecycleError("Audio RX callback is required.")
        if not callable(callback):
            raise TypeError("Audio RX callback must be callable.")

        async with self._rx_lock, self._tx_lock:
            if self.rx_running or self.tx_running:
                raise AudioDriverLifecycleError(
                    "Duplex stream requires both RX and TX idle."
                )

            selected_rx, selected_tx = self._ensure_selected_devices()
            if selected_rx.index != selected_tx.index:
                raise AudioDriverLifecycleError(
                    "Duplex stream requires RX and TX on the SAME device "
                    f"(got RX=[{selected_rx.index}] {selected_rx.name}, "
                    f"TX=[{selected_tx.index}] {selected_tx.name}); "
                    "use start_rx/start_tx for separate devices."
                )

            sr = self._config.sample_rate if sample_rate is None else sample_rate
            ch = self._config.channels if channels is None else channels
            fm = self._config.frame_ms if frame_ms is None else frame_ms
            if (sr * fm) % 1000 != 0:
                raise AudioDriverLifecycleError(
                    "Invalid duplex frame format: sample_rate * frame_ms must "
                    "be divisible by 1000."
                )

            rx_contract = self._resolve_stream_contract(
                direction="rx",
                device=selected_rx,
                requested_sample_rate=sr,
                channels=ch,
                frame_ms=fm,
                allow_sample_rate_fallback=allow_sample_rate_fallback,
            )
            tx_contract = self._resolve_stream_contract(
                direction="tx",
                device=selected_tx,
                requested_sample_rate=rx_contract.sample_rate_hz,
                channels=ch,
                frame_ms=fm,
                allow_sample_rate_fallback=False,
            )
            logger.info(
                "usb-audio: opening DUPLEX — device=[%d] %s, %d Hz, RX open %d ch "
                "/ deliver %d ch, TX %d ch, %d ms",
                selected_rx.index,
                selected_rx.name,
                rx_contract.sample_rate_hz,
                rx_contract.effective_open_channels,
                rx_contract.channels,
                tx_contract.channels,
                fm,
            )
            self._duplex_stream = self._backend.open_duplex(
                AudioDeviceId(selected_rx.index),
                sample_rate=rx_contract.sample_rate_hz,
                channels=rx_contract.effective_open_channels,
                frame_ms=fm,
                deliver_channels=rx_contract.channels,
                rx_audio_channel=self._config.rx_audio_channel,
                tx_channels=tx_contract.channels,
            )
            await self._duplex_stream.start(callback)
            self._store_stream_contract(rx_contract)
            self._store_stream_contract(tx_contract)
            logger.info(
                "usb-audio: DUPLEX running — device=[%d] %s",
                selected_rx.index,
                selected_rx.name,
            )

    async def stop_duplex(self) -> None:
        """Stop and close the full-duplex stream."""
        async with self._rx_lock, self._tx_lock:
            stream = self._duplex_stream
            self._duplex_stream = None
            if stream is not None and stream.running:
                await stream.stop()

    async def _push_tx_pcm(self, frame: bytes) -> None:
        """Queue one PCM frame for playback."""
        if not self.tx_running:
            raise AudioNotStartedError("Audio TX stream is not started.")
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("PCM TX frame must be bytes-like.")
        # Same-device duplex: TX rides the single stream's TX queue.
        if self._duplex_stream is not None:
            await self._duplex_stream.write(bytes(frame))
            return
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
    "AudioAlreadyStartedError",
    "AudioDeviceConfig",
    "AudioDeviceSelectionError",
    "AudioDriverLifecycleError",
    "AudioNotStartedError",
    "UsbAudioDevice",
    "UsbAudioContract",
    "UsbAudioDriver",
    "UsbAudioStreamContract",
    "list_usb_audio_devices",
    "resolve_usb_duplex_mode",
    "select_usb_audio_devices",
]
