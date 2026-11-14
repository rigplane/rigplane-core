"""One read enumeration per protocol, from the loaded profile (MOR-4464).

Yaesu CAT: every ``CatCommandSpec.read`` template, fields expanded from
profile data where the profile declares a domain (``receiver`` from
``receiver_count``) and from the long-standing field knowledge otherwise
(receiver 0/1, meter type 1-8, one memory channel). Memory-content reads
(``MR``/``MT``) stay out unless ``include_private`` is set.

CI-V: every ``get_*`` entry of the profile ``CommandMap`` plus declared
cmd29 routes and the deliberately unknown requests. The refused command
bytes (send/set-only commands and the power, PTT/tuner, VFO-selection
and memory families) and the ``0x1A 0x00`` memory-contents pair never
pass, with or without the private flag for the families; the private flag
only admits ``0x1A 0x00``.

``build_read_list`` is the allow-list: the runner sends only what it
produces, so a mistake here cannot key anything on the radio.
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB
from rigplane.commands._frame import decode_wire_tuple
from rigplane.commands.command_spec import CatCommandSpec
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigConfig

__all__ = [
    "ReadRequest",
    "build_allowed_bodies",
    "build_read_list",
    "compile_cat_template",
    "identity_requests",
    "is_forbidden",
    "snapshot_names",
]

#: Values a Yaesu template field is expanded over. ``receiver`` is the
#: main/sub side digit; ``type`` is the RM meter selector; ``chan`` is
#: one memory slot (MOR-3628). ``receiver`` is overridden per profile
#: from ``receiver_count``; the rest have no profile domain and stay here.
FIELD_VALUES: dict[str, tuple[str, ...]] = {
    "receiver": ("0", "1"),
    "type": tuple("12345678"),
    "chan": ("00001",),
}

#: Yaesu read verbs holding memory contents: opt-in via ``include_private``.
_PRIVATE_CAT_VERBS = ("MR", "MT")

#: Yaesu state reads, in order, for the before/after snapshot.
_YAESU_SNAPSHOT_CANDIDATES = (
    "get_if_status",
    "get_freq",
    "get_mode",
    "get_split",
    "get_ptt",
    "get_powerstat",
    "get_auto_info",
)

#: CI-V state reads, in order, for the before/after snapshot. The PTT
#: and tuner families are never sent (policy: no TX-adjacent traffic at
#: all, even reads), so they have no snapshot slot either.
_CIV_SNAPSHOT_CANDIDATES = (
    "get_freq",
    "get_mode",
    "get_split",
    "get_rf_power",
)

#: CI-V command bytes refused whatever follows them, checked on the body
#: after unwrapping cmd29 so the wrapped forms are refused too: the
#: send/set-only commands (send freq/mode 0x00/0x01, set freq/mode
#: 0x05/0x06, MEM select/write/clear 0x08/0x09/0x0B, scan 0x0E, CW send
#: 0x17) plus the power, PTT/tuner and VFO-selection families
#: (0x18/0x1C/0x07) whose read forms the probe also never sends. 0x07 is
#: in the set because the family is not read-only on Icom radios: a bare
#: ``07`` selects VFO mode (leaving memory mode), ``07 D0/D1`` select
#: MAIN/SUB, and ``07 A0/B0/B1`` exchange or equalise VFOs.
_REFUSED_COMMANDS = frozenset(
    (0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x17, 0x18, 0x1C)
)

#: Memory contents read, forbidden by its (command, sub) pair.
_MEMORY_CONTENTS_SUB = 0x00

#: Deliberately unknown requests for NAK-vs-silence classification.
_UNKNOWN_BODIES = (
    bytes((0x1A, 0x05, 0x09, 0x99)),
    bytes((0x7F,)),
    bytes((0x14, 0x7E)),
    bytes((0x27, 0x7F)),
)

#: Command29 receiver prefix (the CI-V dual-receiver wrapper itself).
_CMD29 = 0x29

#: A template field: ``{name}`` is one letter or digit. Read templates
#: never carry a width spec; any other shape gets one literal ``0``.
_FIELD = re.compile(r"\{([^{}]*)\}")

#: A template field: ``{name}`` is one letter or digit, ``{name:09d}``
#: is nine digits. Any other format spec does not match and so never
#: allows a line.
_MATCH_FIELD = re.compile(r"\{[^{}:]*(?::0?(\d*)d)?\}")


@dataclass(frozen=True, slots=True)
class ReadRequest:
    """One read to send once.

    ``body`` is the exact wire bytes: the CAT line with its ``;`` or the
    CI-V frame body between the address bytes and the terminator.
    ``kind`` is ``cat``, ``get``, ``cmd29``, ``unknown`` or ``identity``.
    """

    name: str
    body: bytes
    kind: str
    note: str = ""


def compile_cat_template(template: str) -> re.Pattern[str]:
    """The regex matching exactly the wire lines one CAT template makes."""
    parts: list[str] = []
    pos = 0
    for field in _MATCH_FIELD.finditer(template):
        parts.append(re.escape(template[pos : field.start()]))
        width = field.group(1)
        if field.group(0) == "{chan}":  # MR/MT memory slot: five digits (MOR-3628)
            parts.append("[0-9]{5}")
        else:
            parts.append(f"[0-9]{{{width}}}" if width else "[0-9A-Za-z]")
        pos = field.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts))


def _receiver_values(config: RigConfig) -> tuple[str, ...]:
    """The receiver side digits one profile's receiver count declares."""
    if config.receiver_count >= 2:
        return FIELD_VALUES["receiver"]
    return ("0",)


def _field_values(name: str, config: RigConfig) -> tuple[tuple[str, ...], str]:
    """Expansion values for one template field plus a provenance note."""
    if name == "receiver":
        return _receiver_values(config), ""
    if name in FIELD_VALUES:
        return FIELD_VALUES[name], f"default domain for {{{name}}}"
    return ("0",), f"profile declares no domain for {{{name}}}; sent 0 only"


def _expand_cat_read(
    name: str, template: str, config: RigConfig, *, include_private: bool
) -> list[ReadRequest]:
    """Every wire line one CAT read template makes, in profile order."""
    if template[:2] in _PRIVATE_CAT_VERBS and not include_private:
        return []
    names = _FIELD.findall(template)
    probes: list[ReadRequest] = []
    seen: set[bytes] = set()
    domains_notes = [_field_values(field_name, config) for field_name in names]
    domains = [domain for domain, _ in domains_notes]
    notes = [note for _, note in domains_notes]
    for combo in itertools.product(*domains):
        line = template
        for field_name, value in zip(names, combo, strict=True):
            line = line.replace("{" + field_name + "}", value, 1)
        body = line.encode("ascii")
        if body not in seen:
            seen.add(body)
            note = "; ".join(note for note in notes if note)
            probes.append(ReadRequest(name, body, "cat", note))
    return probes


def _build_cat_list(config: RigConfig, *, include_private: bool) -> list[ReadRequest]:
    probes: list[ReadRequest] = []
    for name, spec in config.commands.items():
        if not isinstance(spec, CatCommandSpec) or not spec.read:
            continue
        probes.extend(
            _expand_cat_read(name, spec.read, config, include_private=include_private)
        )
    return probes


def _unwrap_cmd29(frame_body: bytes) -> bytes:
    """Strip one leading ``0x29 <rx>`` prefix (rx = MAIN 0x00 / SUB 0x01)."""
    if (
        len(frame_body) >= 3
        and frame_body[0] == _CMD29
        and frame_body[1] in (RECEIVER_MAIN, RECEIVER_SUB)
    ):
        return frame_body[2:]
    return frame_body


def _excluded_from_probes(body: bytes, *, include_private: bool) -> bool:
    """Whether a declared GET body is excluded from the probe list.

    The refused command bytes (their read forms included) are always
    out; the 0x1A 0x00 memory-contents pair — a (command, sub) pair
    rather than a whole command byte, because 0x1A carries legal
    scope/control reads — is out unless ``include_private`` is set.
    """
    if not body:
        return True
    if body[0] in _REFUSED_COMMANDS:
        return True
    if body[0] == 0x1A and body[1:2] == bytes((_MEMORY_CONTENTS_SUB,)):
        return not include_private
    return False


def _cmd29_body(base_body: bytes, receiver: int) -> bytes:
    """Wrap one command body for *receiver* through 0x29."""
    return bytes((_CMD29, receiver)) + base_body


def _build_civ_list(
    config: RigConfig, profile: RadioProfile, *, include_private: bool
) -> list[ReadRequest]:
    probes: list[ReadRequest] = []
    routes = set(profile.cmd29_routes)
    command_map = profile.command_map
    if command_map is None:
        return probes

    for name in sorted(command_map):
        if not name.startswith("get_"):
            continue
        body = bytes(command_map.get(name))
        if _excluded_from_probes(body, include_private=include_private):
            continue
        probes.append(ReadRequest(name, body, "get"))
        _command, sub, _prefix = decode_wire_tuple(command_map.get(name))
        if (_command, sub) in routes:
            for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
                probes.append(
                    ReadRequest(
                        f"cmd29:{name}:{receiver}",
                        _cmd29_body(body, receiver),
                        "cmd29",
                    )
                )

    if command_map.has("get_freq"):
        freq_body = bytes(command_map.get("get_freq"))
        for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
            probes.append(
                ReadRequest(
                    f"cmd29:get_freq:{receiver}",
                    _cmd29_body(freq_body, receiver),
                    "cmd29",
                )
            )

    for body in _UNKNOWN_BODIES:
        probes.append(ReadRequest(f"unknown:{body.hex()}", body, "unknown"))

    return probes


def build_read_list(
    config: RigConfig, *, include_private: bool = False
) -> list[ReadRequest]:
    """Every read of one profile, concretely expanded, in profile order.

    The runner sends only these bodies, so this list is the allow-list:
    nothing here may key the transmitter, start the tuner, write memory,
    send CW, or switch power.
    """
    if config.protocol_type == "yaesu_cat":
        return _build_cat_list(config, include_private=include_private)
    if config.protocol_type == "civ":
        return _build_civ_list(
            config, config.to_profile(), include_private=include_private
        )
    raise ValueError(
        f"probe supports 'civ' and 'yaesu_cat', not {config.protocol_type!r}"
    )


def build_allowed_bodies(
    profile: RadioProfile, *, include_private: bool = False
) -> frozenset[bytes]:
    """Unwrapped frame bodies the runner may send for *profile*.

    Every body :func:`build_read_list` produces for the profile, with
    the leading ``0x29 <rx>`` wrapper removed from the wrapped ones.
    """
    command_map = profile.command_map
    if command_map is None:
        return frozenset()
    bodies: set[bytes] = set()
    for name in command_map:
        if not name.startswith("get_"):
            continue
        body = bytes(command_map.get(name))
        if _excluded_from_probes(body, include_private=include_private):
            continue
        bodies.add(body)
    if command_map.has("get_freq"):
        bodies.add(bytes(command_map.get("get_freq")))
    return frozenset(bodies) | frozenset(_UNKNOWN_BODIES)


def is_forbidden(frame_body: bytes, allowed: frozenset[bytes] | set[bytes]) -> bool:
    """Whether *frame_body* may not be sent by the runner.

    Two independent refusals, both evaluated on the body after unwrapping
    one leading ``0x29 <rx>``:

    1. the command byte is in :data:`_REFUSED_COMMANDS` — power, PTT/
       tuner, memory write/clear, CW send, scan, and the send/set-only
       commands — so a mistake in *allowed* cannot key anything;
    2. the body is not byte-for-byte in *allowed*. Every SET is refused
       here: a SET carries a value, so its body is never equal to a
       declared GET body.
    """
    if not frame_body:
        return True
    body = _unwrap_cmd29(frame_body)
    if body[0] in _REFUSED_COMMANDS:
        return True
    return body not in allowed


def identity_requests(config: RigConfig) -> list[ReadRequest]:
    """The identity reads for one profile: Yaesu ``ID;``, CI-V ``19 00``.

    The Xiegu ``1D 19`` model-id read follows when the profile declares
    it. Recorded as reported plus a match flag; a mismatch never stops
    the run.
    """
    if config.protocol_type == "yaesu_cat":
        spec = config.commands.get("get_id")
        if not isinstance(spec, CatCommandSpec) or not spec.read:
            return []
        return [ReadRequest("get_id", spec.read.encode("ascii"), "identity")]
    if config.protocol_type == "civ":
        profile = config.to_profile()
        command_map = profile.command_map
        if command_map is None:
            return []
        requests: list[ReadRequest] = []
        if command_map.has("get_transceiver_id"):
            requests.append(
                ReadRequest(
                    "get_transceiver_id",
                    bytes(command_map.get("get_transceiver_id")),
                    "identity",
                )
            )
        if command_map.has("get_xiegu_model_id"):
            requests.append(
                ReadRequest(
                    "get_xiegu_model_id",
                    bytes(command_map.get("get_xiegu_model_id")),
                    "identity",
                )
            )
        return requests
    raise ValueError(
        f"probe supports 'civ' and 'yaesu_cat', not {config.protocol_type!r}"
    )


def snapshot_names(config: RigConfig) -> list[str]:
    """Profile state reads for the before/after snapshot, in order.

    Only reads the pass would send anyway: a name is kept when it is in
    the produced read list.
    """
    candidates: tuple[str, ...]
    if config.protocol_type == "yaesu_cat":
        candidates = _YAESU_SNAPSHOT_CANDIDATES
    elif config.protocol_type == "civ":
        candidates = _CIV_SNAPSHOT_CANDIDATES
    else:
        raise ValueError(
            f"probe supports 'civ' and 'yaesu_cat', not {config.protocol_type!r}"
        )
    produced = {item.name for item in build_read_list(config)}
    return [name for name in candidates if name in produced]
