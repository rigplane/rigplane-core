"""Shared executable core for IC-705 behavior.

IC-705 inherits shared core command routing from CoreRadio.
Profile-driven routing based on ic705.toml capabilities and CI-V address (0xA4).
"""

from ...radio import CoreRadio

__all__ = ["Ic705CoreRadio"]


class Ic705CoreRadio(CoreRadio):
    """IC-705 model mapped to shared core with profile-driven routing.

    Inherits all command logic from CoreRadio;
    model="IC-705" triggers ic705.toml profile which defines:
    - CI-V address: 0xA4
    - Capabilities: audio, RF/AF, dsp, filter, scope, meters, etc.
    - Frequency ranges and band stacking
    """

    pass
