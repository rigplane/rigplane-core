"""Typed backend configuration models for radio assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from ..profiles import RadioProfile
from ..types import AudioCodec, get_audio_capabilities

_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_AUDIO_CODEC = _AUDIO_CAPABILITIES.default_codec
_DEFAULT_AUDIO_SAMPLE_RATE = _AUDIO_CAPABILITIES.default_sample_rate_hz


@dataclass(frozen=True, slots=True)
class LanBackendConfig:
    """Configuration for LAN backend assembly."""

    backend: Literal["lan"] = "lan"
    host: str = ""
    port: int = 50001
    username: str = ""
    password: str = ""
    radio_addr: int | None = None
    timeout: float = 5.0
    audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC
    audio_sample_rate: int = _DEFAULT_AUDIO_SAMPLE_RATE
    auto_reconnect: bool = False
    reconnect_delay: float = 2.0
    reconnect_max_delay: float = 60.0
    watchdog_timeout: float = 30.0
    auto_recover_audio: bool = True
    cache_ttl_s: dict[str, float] | None = None
    profile: RadioProfile | str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.host.strip():
            raise ValueError("LAN backend requires non-empty host.")
        if not (1 <= self.port <= 65535):
            raise ValueError("LAN backend port must be in range 1..65535.")
        if self.radio_addr is not None and not (0 <= self.radio_addr <= 0xFF):
            raise ValueError("radio_addr must be a single byte (0..255).")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0.")
        if self.audio_sample_rate <= 0:
            raise ValueError("audio_sample_rate must be > 0.")
        if self.reconnect_delay <= 0:
            raise ValueError("reconnect_delay must be > 0.")
        if self.reconnect_max_delay <= 0:
            raise ValueError("reconnect_max_delay must be > 0.")
        if self.watchdog_timeout <= 0:
            raise ValueError("watchdog_timeout must be > 0.")


@dataclass(frozen=True, slots=True)
class SerialBackendConfig:
    """Configuration for serial backend assembly (stub-ready)."""

    backend: Literal["serial"] = "serial"
    device: str = ""
    baudrate: int = 115200
    radio_addr: int | None = None
    timeout: float = 5.0
    audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC
    audio_sample_rate: int = _DEFAULT_AUDIO_SAMPLE_RATE
    rx_device: str | None = None
    tx_device: str | None = None
    ptt_mode: Literal["civ"] = "civ"
    allow_low_baud_scope: bool = False
    profile: RadioProfile | str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("Serial backend requires non-empty device path.")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be > 0.")
        if self.radio_addr is not None and not (0 <= self.radio_addr <= 0xFF):
            raise ValueError("radio_addr must be a single byte (0..255).")
        if self.timeout <= 0:
            raise ValueError("timeout must be > 0.")
        if self.audio_sample_rate <= 0:
            raise ValueError("audio_sample_rate must be > 0.")
        if self.rx_device is not None and not self.rx_device.strip():
            raise ValueError("rx_device override must be a non-empty string.")
        if self.tx_device is not None and not self.tx_device.strip():
            raise ValueError("tx_device override must be a non-empty string.")
        if self.ptt_mode != "civ":
            raise ValueError("ptt_mode must be 'civ'.")
        if not isinstance(self.allow_low_baud_scope, bool):
            raise ValueError("allow_low_baud_scope must be a bool.")


@dataclass(frozen=True, slots=True)
class YaesuCatBackendConfig:
    """Configuration for Yaesu CAT serial backend."""

    backend: Literal["yaesu-cat"] = "yaesu-cat"
    device: str = ""
    baudrate: int = 38400
    audio_sample_rate: int = 48000
    rx_device: str | None = None
    tx_device: str | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        if not self.device.strip():
            raise ValueError("Yaesu CAT backend requires non-empty device path.")
        if self.baudrate <= 0:
            raise ValueError("baudrate must be > 0.")
        if self.audio_sample_rate <= 0:
            raise ValueError("audio_sample_rate must be > 0.")
        if self.rx_device is not None and not self.rx_device.strip():
            raise ValueError("rx_device override must be a non-empty string.")
        if self.tx_device is not None and not self.tx_device.strip():
            raise ValueError("tx_device override must be a non-empty string.")


BackendConfig = LanBackendConfig | SerialBackendConfig | YaesuCatBackendConfig

__all__ = [
    "BackendConfig",
    "LanBackendConfig",
    "SerialBackendConfig",
    "YaesuCatBackendConfig",
]
