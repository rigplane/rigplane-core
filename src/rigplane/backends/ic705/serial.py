"""Serial adaptation layer for the IC-705 backend.

IC-705 serial backend using USB CI-V + exported USB audio devices.
Profile-driven routing with ic705.toml configuration.
"""

from __future__ import annotations

from .._icom_serial_base import (
    _IcomSerialRadioBase,
    _NON_ICOM_SERIAL_CIV_MIN_INTERVAL_MS,
)

__all__ = ["Ic705SerialRadio", "XieguSerialRadio"]


class XieguSerialRadio(_IcomSerialRadioBase):
    """X6200 on the IC-705 serial transport, at the unmeasured gap.

    Same CI-V session as the IC-705; the gap stays 50 ms because that radio
    was not on the bench that set the Icom serial gap (MOR-2595). X6100 has
    no serial backend and is not routed here.
    """

    _serial_civ_min_interval_ms = _NON_ICOM_SERIAL_CIV_MIN_INTERVAL_MS


class Ic705SerialRadio(_IcomSerialRadioBase):
    """IC-705 backend wired to shared core over serial CI-V session driver.

    Uses USB CI-V interface + exported USB audio devices (RX/TX).
    Profile-driven routing via ic705.toml (CI-V addr 0xA4).
    """

    _DEFAULT_MODEL = "IC-705"
