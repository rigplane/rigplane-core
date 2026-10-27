"""PortAudio enumeration records for USB captures."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class HostApiRecord:
    index: int
    name: str
    device_count: int
    default_input: int
    default_output: int


@dataclass(frozen=True, slots=True)
class PortAudioDeviceRecord:
    index: int
    name: str
    hostapi: int
    max_input_channels: int
    max_output_channels: int
    default_samplerate: float
    default_low_input_latency: float
    default_low_output_latency: float
    default_high_input_latency: float
    default_high_output_latency: float


@dataclass(frozen=True, slots=True)
class PortAudioEnumeration:
    hostapis: tuple[HostApiRecord, ...]
    devices: tuple[PortAudioDeviceRecord, ...]
    generation: int
    fresh: bool

    def to_json_dict(self) -> dict[str, Any]:
        return {
            "hostapis": [asdict(api) for api in self.hostapis],
            "devices": [asdict(device) for device in self.devices],
            "generation": self.generation,
            "fresh": self.fresh,
        }

    @classmethod
    def from_json_dict(cls, value: dict[str, Any]) -> PortAudioEnumeration:
        return cls(
            hostapis=tuple(HostApiRecord(**api) for api in value["hostapis"]),
            devices=tuple(
                PortAudioDeviceRecord(**device) for device in value["devices"]
            ),
            generation=value["generation"],
            fresh=value["fresh"],
        )


def read_portaudio_enumeration(sd: Any) -> PortAudioEnumeration:
    return PortAudioEnumeration(
        hostapis=tuple(
            HostApiRecord(
                index=index,
                name=api["name"],
                device_count=len(api["devices"]),
                default_input=api["default_input_device"],
                default_output=api["default_output_device"],
            )
            for index, api in enumerate(sd.query_hostapis())
        ),
        devices=tuple(
            PortAudioDeviceRecord(
                index=index,
                name=device["name"],
                hostapi=device["hostapi"],
                max_input_channels=device["max_input_channels"],
                max_output_channels=device["max_output_channels"],
                default_samplerate=device["default_samplerate"],
                default_low_input_latency=device["default_low_input_latency"],
                default_low_output_latency=device["default_low_output_latency"],
                default_high_input_latency=device["default_high_input_latency"],
                default_high_output_latency=device["default_high_output_latency"],
            )
            for index, device in enumerate(sd.query_devices())
        ),
        generation=0,
        fresh=True,
    )
