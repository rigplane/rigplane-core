"""Serial adaptation layer for the IC-7300 backend.

IC-7300 serial backend using USB CI-V + exported USB audio devices.
Profile-driven routing with ic7300.toml configuration.
"""

from __future__ import annotations

from .._icom_serial_base import _IcomSerialRadioBase

__all__ = ["Ic7300SerialRadio"]


class Ic7300SerialRadio(_IcomSerialRadioBase):
    """IC-7300 backend wired to shared core over serial CI-V session driver.

    Uses USB CI-V interface + exported USB audio devices (RX/TX).
    Profile-driven routing via ic7300.toml (CI-V addr 0x94).
    """

    _DEFAULT_MODEL = "IC-7300"
