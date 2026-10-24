"""Immutable normalized inputs to USB audio pairing."""

from dataclasses import dataclass
from typing import Literal

from rigplane.audio._portaudio_enum import PortAudioEnumeration

NodeKind = Literal[
    "controller", "root_hub", "hub", "device", "interface", "media", "endpoint", "other"
]
Flow = Literal["capture", "render"]


@dataclass(frozen=True, slots=True)
class UsbNode:
    id: str
    parent: str | None
    kind: NodeKind
    vid: str | None
    pid: str | None


@dataclass(frozen=True, slots=True)
class SerialPortRef:
    port: str
    node: str | None


@dataclass(frozen=True, slots=True)
class AudioEndpoint:
    os_id: str
    owner: str
    flow: Flow
    name: str


@dataclass(frozen=True, slots=True)
class Unavailable:
    part: str
    reason: str


@dataclass(frozen=True, slots=True)
class UsbSnapshot:
    os: Literal["windows", "macos", "linux"]
    usb: tuple[UsbNode, ...]
    serial_ports: tuple[SerialPortRef, ...]
    endpoints: tuple[AudioEndpoint, ...]
    portaudio: PortAudioEnumeration
    unavailable: tuple[Unavailable, ...]
