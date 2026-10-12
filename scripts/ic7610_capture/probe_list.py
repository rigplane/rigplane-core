"""Profile-derived read-only CI-V probe list for the IC-7610 capture session.

``build_probe_list`` turns a ``RadioProfile`` into the ordered list of
read-only CI-V request bodies the capture driver sends once each, paced,
so the recording holds the radio's answer class per command — data /
ack / nak / silence, with latency. Every wire byte for a declared command
comes from the profile's ``CommandMap``; the only literal bytes here are
the deliberately unknown requests and the 0x29 receiver prefix, both of
which exist to be classified by the radio's answer.

Sending is gated by an allow-list, not a deny-list: ``run_probes`` sends
a frame only when ``is_forbidden`` accepts its body — after unwrapping
one leading ``0x29 <rx>`` — as byte-for-byte one of the bodies
``build_probe_list`` itself produces (``build_allowed_bodies`` collects
them once per profile). A SET therefore never passes: a SET carries a
value, so its body is never equal to a declared GET body. Command 0x07
is excluded from both the probe list and the allow-list because its
bare and sub-command forms change VFO/receiver selection.
"""

from __future__ import annotations

from dataclasses import dataclass

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB
from rigplane.commands._frame import decode_wire_tuple
from rigplane.profiles import RadioProfile

__all__ = ["Probe", "build_allowed_bodies", "build_probe_list", "is_forbidden"]

#: Command29 receiver prefix (the CI-V dual-receiver wrapper itself).
_CMD29 = 0x29

#: CI-V command bytes refused outright, whatever follows them, checked on
#: the body after unwrapping cmd29 so the wrapped forms are refused too:
#: the send/set-only commands (send freq/mode 0x00/0x01, set freq/mode
#: 0x05/0x06, MEM select/write/clear 0x08/0x09/0x0B, scan 0x0E, CW send
#: 0x17) plus the power, PTT/tuner and VFO-selection families
#: (0x18/0x1C/0x07) whose read forms the capture also never sends. This
#: is the second, independent refusal behind the allow-list: a mistake
#: there cannot key anything on these commands. 0x07 is in the set
#: because the family is not read-only on Icom radios: a bare ``07``
#: selects VFO mode (leaving memory mode), ``07 D0/D1`` select MAIN/SUB,
#: and ``07 A0/B0/B1`` exchange or equalise VFOs.
_REFUSED_COMMANDS = frozenset(
    (0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x17, 0x18, 0x1C)
)

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


def _unwrap_cmd29(frame_body: bytes) -> bytes:
    """Strip one leading ``0x29 <rx>`` prefix (rx = MAIN 0x00 / SUB 0x01)."""
    if (
        len(frame_body) >= 3
        and frame_body[0] == _CMD29
        and frame_body[1] in (RECEIVER_MAIN, RECEIVER_SUB)
    ):
        return frame_body[2:]
    return frame_body


def _excluded_from_probes(body: bytes) -> bool:
    """Whether a declared GET body is excluded from the probe list.

    The refused command bytes (their read forms included) and the
    0x1A 0x00 memory-contents pair — a (command, sub) pair rather than a
    whole command byte, because 0x1A carries legal scope/control reads.
    """
    if not body:
        return True
    if body[0] in _REFUSED_COMMANDS:
        return True
    return body[0] == 0x1A and body[1:2] == bytes((_MEMORY_CONTENTS_SUB,))


def build_allowed_bodies(profile: RadioProfile) -> frozenset[bytes]:
    """Unwrapped frame bodies the capture driver may send.

    Every body :func:`build_probe_list` produces for *profile*, with the
    leading ``0x29 <rx>`` wrapper removed from the wrapped ones: the
    declared GET bodies and the deliberately unknown requests.
    ``run_probes`` sends a frame only when its unwrapped body is
    byte-for-byte in this set.
    """
    return frozenset(
        _unwrap_cmd29(probe_.frame_body) for probe_ in build_probe_list(profile)
    )


def is_forbidden(frame_body: bytes, allowed: frozenset[bytes] | set[bytes]) -> bool:
    """Whether *frame_body* may not be sent by the capture driver.

    Two independent refusals, both evaluated on the body after unwrapping
    one leading ``0x29 <rx>``:

    1. the command byte is in :data:`_REFUSED_COMMANDS` — power, PTT/
       tuner, memory write/clear, CW send, scan, and the send/set-only
       commands — so a mistake in *allowed* cannot key anything;
    2. the body is not byte-for-byte in *allowed*, built once per profile
       by :func:`build_allowed_bodies`. Every SET is refused here: a SET
       carries a value, so its body is never equal to a declared GET
       body.
    """
    if not frame_body:
        return True
    body = _unwrap_cmd29(frame_body)
    if body[0] in _REFUSED_COMMANDS:
        return True
    return body not in allowed


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
    both and excludes declared-absent entries) except the refused
    command bytes and the 0x1A 0x00 memory-contents pair, the
    cmd29-wrapped variant of each GET whose (command, sub) the profile
    routes through 0x29, the cmd29-wrapped frequency read for both
    receivers, and the deliberately unknown requests.
    """
    probes: list[Probe] = []
    routes = set(profile.cmd29_routes)
    command_map = profile.command_map

    for name in sorted(command_map):
        if not name.startswith("get_"):
            continue
        body = bytes(command_map.get(name))
        if _excluded_from_probes(body):
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
        # cmd29-wrapped frequency read for both receivers.
        freq_body = bytes(command_map.get("get_freq"))
        for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
            probes.append(
                Probe(
                    f"cmd29:get_freq:{receiver}",
                    _cmd29_body(freq_body, receiver),
                    "cmd29",
                )
            )

    for body in _UNKNOWN_BODIES:
        probes.append(Probe(f"unknown:{body.hex()}", body, "unknown"))

    return probes
