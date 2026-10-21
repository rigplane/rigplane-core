"""FTX-1 capture probe list: every profile READ line, concretely expanded.

Pure. The list comes from the rig profile's read templates, never from a
hand-typed table: a template without fields is one probe; a field is
expanded over the values in ``FIELD_VALUES`` (receiver 0/1; meter types 1-8).
``CatReadOnlyGuard`` is the second, independent gate: the driver's bridge
drops anything that is not a profile read, so a mistake here cannot reach
the radio.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles.rig_loader import RigConfig

# Values a template field is expanded over. ``receiver`` is the main/sub
# side digit; ``type`` is the RM meter selector (the Core poller reads 7 and
# 8; the others are probed to learn which the radio answers).
FIELD_VALUES: dict[str, tuple[str, ...]] = {
    "receiver": ("0", "1"),
    "type": tuple("12345678"),
}

_FIELD = re.compile(r"\{([^{}:]*)\}")


@dataclass(frozen=True, slots=True)
class Probe:
    """One read line to send once; ``command`` is the profile command name."""

    command: str
    line: str


def build_probe_list(config: RigConfig) -> list[Probe]:
    """Every distinct READ line of the profile, templates expanded, in profile order."""
    probes: list[Probe] = []
    seen: set[str] = set()
    for name, spec in config.commands.items():
        if not isinstance(spec, CatCommandSpec) or not spec.read:
            continue
        names = _FIELD.findall(spec.read)
        for combo in itertools.product(*(FIELD_VALUES[n] for n in names)):
            line = spec.read
            for field, value in zip(names, combo, strict=True):
                line = line.replace("{" + field + "}", value, 1)
            if line not in seen:
                seen.add(line)
                probes.append(Probe(name, line))
    return probes
