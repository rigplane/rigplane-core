"""Serial-side CI-V fakes and the serial bridge recorder for the rigplane
test suites (MOR-3218, MOR-3306).

The implementations live in :mod:`rigplane_testkit.serial.civ_serial` and
:mod:`rigplane_testkit.serial.recorder`; this module re-exports their public
surface so callers can write ``from rigplane_testkit.serial import
CivSerialFake``.
"""

from rigplane_testkit.serial.civ_filter import CivFrameFilter, CivFrameGuard
from rigplane_testkit.serial.civ_serial import CivSerialFake
from rigplane_testkit.serial.recorder import SerialCivRecorder, civ_frames

__all__ = [
    "CivFrameFilter",
    "CivFrameGuard",
    "CivSerialFake",
    "SerialCivRecorder",
    "civ_frames",
]
