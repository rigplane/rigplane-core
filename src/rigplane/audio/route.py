"""Audio route and LAN stream request resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from rigplane.types import AudioCodec, get_audio_capabilities

if TYPE_CHECKING:
    from rigplane.radio_protocol import Radio

__all__ = [
    "AudioConfigSource",
    "AudioRoute",
    "AudioStreamContract",
    "AudioStreamRequest",
    "DataModePolicy",
    "RadioTransport",
    "RxAudioSource",
    "TxAudioSource",
    "audio_stream_contract_from_request",
    "resolve_lan_audio_stream_request",
    "resolve_audio_route",
    "rigctld_wsjtx_policy",
]

_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_RX_CODEC = _AUDIO_CAPABILITIES.default_codec
_CHANNELS_BY_CODEC: dict[AudioCodec, int] = {
    AudioCodec.ULAW_1CH: 1,
    AudioCodec.PCM_1CH_8BIT: 1,
    AudioCodec.PCM_1CH_16BIT: 1,
    AudioCodec.PCM_2CH_8BIT: 2,
    AudioCodec.PCM_2CH_16BIT: 2,
    AudioCodec.ULAW_2CH: 2,
    AudioCodec.OPUS_1CH: 1,
    AudioCodec.OPUS_2CH: 2,
}


class RadioTransport(StrEnum):
    """Control transport family used by the active radio backend."""

    LAN = "lan"
    SERIAL = "serial"
    REMOTE = "remote"
    UNKNOWN = "unknown"


class TxAudioSource(StrEnum):
    """How TX audio enters the radio hardware."""

    LAN = "lan"
    USB = "usb"
    ACC = "acc"
    UNAVAILABLE = "unavailable"


class RxAudioSource(StrEnum):
    """How RX audio leaves the radio hardware."""

    LAN = "lan"
    USB = "usb"
    ACC = "acc"
    UNAVAILABLE = "unavailable"


class DataModePolicy(StrEnum):
    """WSJT-X packet-mode DATA policy implied by an audio route."""

    DATA2_LAN = "data2_lan"
    DATA1_USB = "data1_usb"
    LEGACY = "legacy"


class AudioConfigSource(StrEnum):
    """Where an effective LAN audio request value came from."""

    EXPLICIT = "explicit"
    PROFILE_DEFAULT = "profile-default"
    PROFILE_CODEC_DEFAULT = "profile-codec-default"
    GLOBAL_DEFAULT = "global-default"
    FALLBACK = "fallback"


@dataclass(frozen=True)
class AudioRoute:
    """Resolved radio audio route and the DATA policy derived from it."""

    radio_transport: RadioTransport
    tx_audio_source: TxAudioSource
    rx_audio_source: RxAudioSource
    data_mode_policy: DataModePolicy
    bridge_required: bool


@dataclass(frozen=True, slots=True)
class AudioStreamRequest:
    """Concrete Icom LAN audio values to write into conninfo."""

    rx_codec: AudioCodec
    tx_codec: AudioCodec
    rx_sample_rate_hz: int
    tx_sample_rate_hz: int
    rx_channels: int
    tx_channels: int
    rx_codec_source: AudioConfigSource
    tx_codec_source: AudioConfigSource
    rx_sample_rate_source: AudioConfigSource
    tx_sample_rate_source: AudioConfigSource


@dataclass(frozen=True, slots=True)
class AudioStreamContract:
    """Accepted/effective radio-native Icom LAN audio values."""

    rx_codec: AudioCodec
    tx_codec: AudioCodec
    rx_sample_rate_hz: int
    tx_sample_rate_hz: int
    rx_channels: int
    tx_channels: int
    rx_codec_source: AudioConfigSource
    tx_codec_source: AudioConfigSource
    rx_sample_rate_source: AudioConfigSource
    tx_sample_rate_source: AudioConfigSource
    fallback_reason: str | None = None


def audio_stream_contract_from_request(
    request: AudioStreamRequest,
) -> AudioStreamContract:
    """Create the initial effective contract from a pre-conninfo request."""

    return AudioStreamContract(
        rx_codec=request.rx_codec,
        tx_codec=request.tx_codec,
        rx_sample_rate_hz=request.rx_sample_rate_hz,
        tx_sample_rate_hz=request.tx_sample_rate_hz,
        rx_channels=request.rx_channels,
        tx_channels=request.tx_channels,
        rx_codec_source=request.rx_codec_source,
        tx_codec_source=request.tx_codec_source,
        rx_sample_rate_source=request.rx_sample_rate_source,
        tx_sample_rate_source=request.tx_sample_rate_source,
    )


def _profile_data_mode_count(radio: "Radio") -> int:
    profile = getattr(radio, "profile", None)
    count = getattr(profile, "data_mode_count", 1)
    return count if isinstance(count, int) and count > 0 else 1


def _profile_codec(name: str | None) -> AudioCodec | None:
    if not name:
        return None
    try:
        return AudioCodec[name]
    except KeyError:
        return None


def _profile_rx_codec(profile: Any) -> AudioCodec | None:
    preference = getattr(profile, "codec_preference", None)
    if not preference:
        return None
    supported = _AUDIO_CAPABILITIES.supported_codecs
    for name in preference:
        codec = _profile_codec(name)
        if codec in supported:
            return codec
    return None


def _sample_rate_for_codec(
    profile: Any,
    codec: AudioCodec,
) -> tuple[int | None, AudioConfigSource | None]:
    by_codec = getattr(profile, "sample_rate_by_codec", None) or {}
    if codec.name in by_codec:
        return int(by_codec[codec.name]), AudioConfigSource.PROFILE_CODEC_DEFAULT
    default_rate = getattr(profile, "default_sample_rate_hz", None)
    if default_rate is not None:
        return int(default_rate), AudioConfigSource.PROFILE_DEFAULT
    return None, None


def resolve_lan_audio_stream_request(
    *,
    profile: Any,
    requested_rx_codec: AudioCodec | int,
    requested_sample_rate_hz: int,
    rx_codec_explicit: bool = False,
    sample_rate_explicit: bool = False,
) -> AudioStreamRequest:
    """Resolve concrete direct Icom LAN audio values before conninfo.

    The resolver is intentionally small: profile policy may replace only caller
    defaults, while explicit constructor/CLI/env choices remain authoritative.
    """

    requested_codec = AudioCodec(requested_rx_codec)
    if rx_codec_explicit:
        rx_codec = requested_codec
        rx_codec_source = AudioConfigSource.EXPLICIT
    else:
        profile_rx_codec = _profile_rx_codec(profile)
        if profile_rx_codec is not None:
            rx_codec = profile_rx_codec
            rx_codec_source = AudioConfigSource.PROFILE_DEFAULT
        else:
            rx_codec = requested_codec
            rx_codec_source = (
                AudioConfigSource.GLOBAL_DEFAULT
                if requested_codec == _DEFAULT_RX_CODEC
                else AudioConfigSource.EXPLICIT
            )

    profile_tx_codec = _profile_codec(getattr(profile, "tx_codec", None))
    if profile_tx_codec is not None:
        tx_codec = profile_tx_codec
        tx_codec_source = AudioConfigSource.PROFILE_DEFAULT
    else:
        tx_codec = AudioCodec.PCM_1CH_16BIT
        tx_codec_source = AudioConfigSource.GLOBAL_DEFAULT

    if sample_rate_explicit:
        rx_sample_rate_hz = requested_sample_rate_hz
        tx_sample_rate_hz = requested_sample_rate_hz
        rx_sample_rate_source = AudioConfigSource.EXPLICIT
        tx_sample_rate_source = AudioConfigSource.EXPLICIT
    else:
        rx_rate, rx_rate_source = _sample_rate_for_codec(profile, rx_codec)
        tx_rate, tx_rate_source = _sample_rate_for_codec(profile, tx_codec)
        rx_sample_rate_hz = rx_rate or requested_sample_rate_hz
        tx_sample_rate_hz = tx_rate or rx_sample_rate_hz
        rx_sample_rate_source = rx_rate_source or AudioConfigSource.GLOBAL_DEFAULT
        tx_sample_rate_source = tx_rate_source or rx_sample_rate_source

    return AudioStreamRequest(
        rx_codec=rx_codec,
        tx_codec=tx_codec,
        rx_sample_rate_hz=rx_sample_rate_hz,
        tx_sample_rate_hz=tx_sample_rate_hz,
        rx_channels=_CHANNELS_BY_CODEC[rx_codec],
        tx_channels=_CHANNELS_BY_CODEC[tx_codec],
        rx_codec_source=rx_codec_source,
        tx_codec_source=tx_codec_source,
        rx_sample_rate_source=rx_sample_rate_source,
        tx_sample_rate_source=tx_sample_rate_source,
    )


def resolve_audio_route(radio: "Radio") -> AudioRoute:
    """Resolve the active radio audio route without looking at CLI bridge flags."""

    backend_id = getattr(radio, "backend_id", None)
    if backend_id == "rigplane":
        data_policy = (
            DataModePolicy.DATA2_LAN
            if _profile_data_mode_count(radio) >= 2
            else DataModePolicy.LEGACY
        )
        return AudioRoute(
            radio_transport=RadioTransport.LAN,
            tx_audio_source=TxAudioSource.LAN,
            rx_audio_source=RxAudioSource.LAN,
            data_mode_policy=data_policy,
            bridge_required=False,
        )

    if (
        backend_id in {"icom_serial", "yaesu_cat"}
        or getattr(radio, "has_usb_audio", False) is True
    ):
        return AudioRoute(
            radio_transport=RadioTransport.SERIAL,
            tx_audio_source=TxAudioSource.USB,
            rx_audio_source=RxAudioSource.USB,
            data_mode_policy=DataModePolicy.DATA1_USB,
            bridge_required=True,
        )

    return AudioRoute(
        radio_transport=RadioTransport.UNKNOWN,
        tx_audio_source=TxAudioSource.UNAVAILABLE,
        rx_audio_source=RxAudioSource.UNAVAILABLE,
        data_mode_policy=DataModePolicy.LEGACY,
        bridge_required=False,
    )


def rigctld_wsjtx_policy(route: AudioRoute) -> tuple[int | None, int | None]:
    """Return ``RigctldConfig`` WSJT-X DATA fields for a resolved route."""

    if route.data_mode_policy == DataModePolicy.DATA2_LAN:
        return 2, 5
    return None, None
