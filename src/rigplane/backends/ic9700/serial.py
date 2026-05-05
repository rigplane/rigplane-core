"""Serial adaptation layer for the IC-9700 backend.

IC-9700 serial backend using USB CI-V + exported USB audio devices.
Profile-driven routing with ic9700.toml configuration.
Dual-receiver capable transceiver with LAN + serial support.
"""

from __future__ import annotations

from .._icom_serial_base import _IcomSerialRadioBase

__all__ = ["Ic9700SerialRadio"]


class Ic9700SerialRadio(_IcomSerialRadioBase):
    """IC-9700 backend wired to shared core over serial CI-V session driver.

    Uses USB CI-V interface + exported USB audio devices (RX/TX).
    Profile-driven routing via ic9700.toml (CI-V addr 0xA2).
    Dual-receiver capable with shared command core inheritance.
    """

    _DEFAULT_MODEL = "IC-9700"
