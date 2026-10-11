"""Serial-side CI-V fakes for the rigplane test suites (MOR-3218).

The implementation lives in :mod:`rigplane_testkit.serial.civ_serial`; this
module re-exports its public surface so callers can write
``from rigplane_testkit.serial import CivSerialFake``.
"""

from rigplane_testkit.serial.civ_serial import CivSerialFake

__all__ = ["CivSerialFake"]
