"""Audio route resolution for radio DATA-mode policy."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rigplane.radio_protocol import Radio

__all__ = [
    "AudioRoute",
    "DataModePolicy",
    "RadioTransport",
    "RxAudioSource",
    "TxAudioSource",
    "resolve_audio_route",
    "rigctld_wsjtx_policy",
]


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


@dataclass(frozen=True)
class AudioRoute:
    """Resolved radio audio route and the DATA policy derived from it."""

    radio_transport: RadioTransport
    tx_audio_source: TxAudioSource
    rx_audio_source: RxAudioSource
    data_mode_policy: DataModePolicy
    bridge_required: bool


def _profile_data_mode_count(radio: "Radio") -> int:
    profile = getattr(radio, "profile", None)
    count = getattr(profile, "data_mode_count", 1)
    return count if isinstance(count, int) and count > 0 else 1


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
