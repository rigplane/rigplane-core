"""Serial adaptation layer for the IC-705 backend.

IC-705 serial backend using USB CI-V + exported USB audio devices.
Profile-driven routing with ic705.toml configuration.
"""

from __future__ import annotations

from .._icom_serial_base import _IcomSerialRadioBase

__all__ = ["Ic705SerialRadio"]


class Ic705SerialRadio(_IcomSerialRadioBase):
    """IC-705 backend wired to shared core over serial CI-V session driver.

    Uses USB CI-V interface + exported USB audio devices (RX/TX).
    Profile-driven routing via ic705.toml (CI-V addr 0xA4).
    """

    _DEFAULT_MODEL = "IC-705"
