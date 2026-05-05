"""Shared executable core for IC-9700 behavior.

IC-9700 inherits shared core command routing from CoreRadio.
Profile-driven routing based on ic9700.toml capabilities and CI-V address (0xA2).
Dual-receiver capable model.
"""

from ...radio import CoreRadio

__all__ = ["Ic9700CoreRadio"]


class Ic9700CoreRadio(CoreRadio):
    """IC-9700 model mapped to shared core with profile-driven routing.

    Inherits all command logic from CoreRadio;
    model="IC-9700" triggers ic9700.toml profile which defines:
    - CI-V address: 0xA2
    - Dual receivers (receiver_count=2)
    - LAN and serial backends supported
    - Capabilities: audio, RF/AF, dsp, filter, scope, meters, etc.
    - Frequency ranges and band stacking
    """

    pass
