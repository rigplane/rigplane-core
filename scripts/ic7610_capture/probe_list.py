"""Profile-derived read-only CI-V probe list for the IC-7610 capture session.

``build_probe_list`` turns a ``RadioProfile`` into the ordered list of
read-only CI-V request bodies the capture driver sends once each, paced,
so the recording holds the radio's answer class per command — data /
NAK / silence, with latency. Every wire byte for a declared command
comes from the profile's ``CommandMap``; the only literal bytes here are
the deliberately unknown requests and the 0x29 receiver prefix, both of
which exist to be classified by the radio's answer.
"""

from __future__ import annotations

from dataclasses import dataclass

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB
from rigplane.commands._frame import decode_wire_tuple
from rigplane.profiles import RadioProfile

__all__ = ["Probe", "build_probe_list", "is_forbidden"]

#: Command29 receiver prefix (the CI-V dual-receiver wrapper itself).
_CMD29 = 0x29

#: CI-V commands the capture must never send: power (0x18), PTT/tuner
#: (0x1C), memory write/clear (0x09/0x0B), CW send (0x17), scan (0x0E).
_FORBIDDEN_COMMANDS = frozenset((0x18, 0x1C, 0x09, 0x0B, 0x17, 0x0E))

#: Memory contents read/write, forbidden by its (command, sub) pair.
_MEMORY_CONTENTS_SUB = 0x00

#: Deliberately unknown requests for NAK-vs-silence classification.
_UNKNOWN_BODIES = (
    bytes((0x1A, 0x05, 0x09, 0x99)),
    bytes((0x7F,)),
    bytes((0x14, 0x7E)),
    bytes((0x27, 0x7F)),
)


@dataclass(frozen=True)
class Probe:
    """One read-only CI-V request: the frame body between the address
    bytes and the 0xFD terminator (``FE FE <to> <from> <frame_body> FD``).
    """

    name: str
    frame_body: bytes
    kind: str


def is_forbidden(frame_body: bytes) -> bool:
    """Whether *frame_body* may never be sent by the capture driver.

    Byte-level exclusion rules enforced whatever the probe list says:
    power, PTT/tuner, memory write/clear, CW send, scan, and the 0x1A 0x00
    memory-contents pair. SET commands are excluded at list construction
    by name (only ``get_*`` entries are ever taken).
    """
    if not frame_body:
        return True
    if frame_body[0] in _FORBIDDEN_COMMANDS:
        return True
    if frame_body[0] == 0x1A and frame_body[1:2] == bytes((_MEMORY_CONTENTS_SUB,)):
        return True
    return False


def _cmd29_body(base_body: bytes, receiver: int) -> bytes:
    """Wrap one command body for *receiver*.

    Same layout ``rigplane.commands.build_cmd29_frame`` emits: the 0x29
    command byte, the receiver byte, then the wrapped command's body.
    """
    return bytes((_CMD29, receiver)) + base_body


def build_probe_list(profile: RadioProfile) -> list[Probe]:
    """Build the read-only probe list for *profile*.

    Covers every ``get_*`` command declared in the profile's command map
    (both ``[commands]`` and ``[commands.overrides]`` — the map holds
    both and excludes declared-absent entries), the cmd29-wrapped variant
    of each GET whose (command, sub) the profile routes through 0x29,
    the documented cmd29-freq refusal pair, and the deliberately unknown
    requests. Frames failing :func:`is_forbidden` are dropped.
    """
    probes: list[Probe] = []
    routes = set(profile.cmd29_routes)
    command_map = profile.command_map

    for name in sorted(command_map):
        if not name.startswith("get_"):
            continue
        body = bytes(command_map.get(name))
        if is_forbidden(body):
            continue
        probes.append(Probe(name, body, "get"))
        command, sub, _prefix = decode_wire_tuple(command_map.get(name))
        if (command, sub) in routes:
            for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
                probes.append(
                    Probe(
                        f"cmd29:{name}:{receiver}", _cmd29_body(body, receiver), "cmd29"
                    )
                )

    if command_map.has("get_freq"):
        # cmd29 does NOT work for freq on IC-7610 (CLAUDE.md hard protocol
        # rule); probing it records the documented refusal itself.
        freq_body = bytes(command_map.get("get_freq"))
        for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
            probes.append(
                Probe(
                    f"cmd29:get_freq:{receiver}",
                    _cmd29_body(freq_body, receiver),
                    "refusal",
                )
            )

    for body in _UNKNOWN_BODIES:
        probes.append(Probe(f"unknown:{body.hex()}", body, "unknown"))

    return probes
