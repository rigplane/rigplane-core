"""Capture-backed USB function identifiers shared by profiles and audio."""

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class UsbFunctionId:
    vid: str
    pid: str

    def __post_init__(self) -> None:
        for value in (self.vid, self.pid):
            if (
                not isinstance(value, str)
                or re.fullmatch(r"[0-9A-F]{4}", value) is None
            ):
                raise ValueError("USB VID and PID must be four uppercase hex digits")


@dataclass(frozen=True, slots=True)
class UsbAudioIdentity:
    audio_codec: frozenset[UsbFunctionId]
    serial_functions: tuple[UsbFunctionId, ...] = ()
