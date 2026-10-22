"""Simulation profiles (``rigplane-sim-profile/1``): load, validate, expose.

``load_sim_profile`` checks one TOML data file against the capability
profile it names through ``rigplane.profiles`` (the only ``rigplane``
import this package makes) and returns a frozen ``SimProfile`` tree.
The first shipped profile is ``ic7610.toml`` (IC-7610, LAN, tier A).
"""

from rigplane_testkit.sim.profiles.loader import (
    SimProfile,
    SimProfileError,
    load_sim_profile,
)

__all__ = ["SimProfile", "SimProfileError", "load_sim_profile"]
