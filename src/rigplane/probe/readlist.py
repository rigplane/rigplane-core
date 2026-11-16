"""One read plan per protocol, from the loaded profile (MOR-4464).

Yaesu CAT: every ``CatCommandSpec.read`` template, fields expanded from
profile data where the profile declares a domain (``receiver`` from
``receiver_count``) and from the long-standing field knowledge otherwise
(receiver 0/1, meter type 1-8, one memory channel). Memory-content reads
(``MR``/``MT``) stay out unless ``include_private`` is set.

CI-V: every ``get_*`` entry the profile declares — listed reads use the
profile's own bytes, declared-absent reads borrow the bytes from the
same-name ``CivCommandSpec`` of the other shipped profiles of the same
protocol, but only when every sibling declaring that name agrees on the
bytes — plus declared cmd29 routes and the deliberately unknown
requests. The refused command bytes (send/set-only commands and the
power, PTT/tuner, VFO-selection and memory families) and the ``0x1A 0x00``
memory-contents pair never pass, with or without the private flag for the
families; the private flag only admits ``0x1A 0x00``.

``build_read_plan`` is the one entry point: the ``asked`` list is the
allow-list (the runner sends only what it holds, so a mistake here cannot
key anything on the radio) and ``skipped`` says what was not sent and why,
so no declared read silently disappears.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Collection, Iterable
from dataclasses import dataclass

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB
from rigplane.commands._frame import decode_wire_tuple, unwrap_receiver_prefix
from rigplane.commands.command_spec import (
    AbsentCommandSpec,
    CatCommandSpec,
    CivCommandSpec,
)
from rigplane.probe.model import SkippedRead
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigConfig

__all__ = [
    "ReadPlan",
    "ReadRequest",
    "build_allowed_bodies",
    "build_read_plan",
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

#: CI-V command bytes a borrowed read may use. A frame with no data is
#: a query for most commands but an ACTION for some (VFO/memory select
#: 0x07/0x08, scan 0x0E, memory write/clear 0x09/0x0B, tuner/TX 0x1C,
#: power 0x18, split/duplex 0x0F, speech 0x13), so a borrowed entry is
#: only sent when its command byte is on this positive list and every
#: lending profile declares the entry as a ``get_*`` read. Native entries
#: of the profile itself keep the older rule (the refused set above).
_BORROW_READ_SAFE_COMMANDS = frozenset(
    (0x03, 0x04, 0x14, 0x15, 0x16, 0x19, 0x1A, 0x1B, 0x1D, 0x1E, 0x21, 0x26, 0x27)
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
    ``expected`` is what the profile declares: ``present`` for a listed
    read, ``absent`` for a declared-absent read sent with borrowed bytes
    (and for the deliberately unknown calibration requests, which the
    profile declares nothing about).
    """

    name: str
    body: bytes
    kind: str
    note: str = ""
    expected: str = "present"


@dataclass(frozen=True, slots=True)
class ReadPlan:
    """What one probe pass asks, and what it deliberately does not.

    ``asked`` is the allow-list in send order; ``skipped`` holds one
    entry per declared read that was not sent, with a reason from
    :data:`rigplane.probe.model.SKIP_REASONS`. Every declared read of
    the profile is in exactly one of the two.
    """

    asked: tuple[ReadRequest, ...] = ()
    skipped: tuple[SkippedRead, ...] = ()


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


def _plan_cat_list(config: RigConfig, *, include_private: bool) -> ReadPlan:
    """Every CAT read of one profile, plus one skip entry per withheld read."""
    asked: list[ReadRequest] = []
    skipped: list[SkippedRead] = []
    for name, spec in config.commands.items():
        if isinstance(spec, CatCommandSpec) and spec.read:
            if spec.read[:2] in _PRIVATE_CAT_VERBS and not include_private:
                skipped.append(
                    SkippedRead(
                        name,
                        None,
                        "private",
                        "stored memory contents need --include-private",
                    )
                )
            else:
                asked.extend(
                    _expand_cat_read(
                        name, spec.read, config, include_private=include_private
                    )
                )
        elif isinstance(spec, AbsentCommandSpec) and name.startswith("get_"):
            skipped.append(
                SkippedRead(
                    name,
                    None,
                    "no_unambiguous_bytes",
                    f"profile declares no CAT read template for {name}",
                )
            )
    return ReadPlan(tuple(asked), tuple(skipped))


def _excluded_from_probes(body: bytes, *, include_private: bool) -> bool:
    """Whether a declared GET body is excluded from the probe list.

    The refused command bytes (their read forms included) are always
    out; the 0x1A 0x00 memory-contents pair — a (command, sub) pair
    rather than a whole command byte, because 0x1A carries legal
    scope/control reads — is out unless ``include_private`` is set.
    The bare 0xFA/0xFB bytes are the radio's NAK/ACK replies, not
    requests, so they never pass either.
    """
    if not body:
        return True
    if body[0] in _REFUSED_COMMANDS:
        return True
    if body[0] in (0xFA, 0xFB):
        return True
    if body[0] == 0x1A and body[1:2] == bytes((_MEMORY_CONTENTS_SUB,)):
        return not include_private
    return False


def _cmd29_body(base_body: bytes, receiver: int) -> bytes:
    """Wrap one command body for *receiver* through 0x29."""
    return bytes((_CMD29, receiver)) + base_body


def _skip_reason_for_body(body: bytes, *, include_private: bool) -> str | None:
    """Why a declared CI-V body must not be sent, or ``None`` to send it.

    The refused command families (their read forms included) are always
    out; the ``0xFA``/``0xFB`` reply codes are never requests; the
    ``0x1A 0x00`` memory-contents pair is out unless ``include_private``
    is set.
    """
    if _excluded_from_probes(body, include_private=include_private):
        if body and body[0] in (0xFA, 0xFB):
            return "response_code"
        if body and body[0] == 0x1A:
            return "private"
        return "unsafe_family"
    return None


def _lending_siblings(
    name: str, config: RigConfig, siblings: tuple[RigConfig, ...]
) -> tuple[list[tuple[str, tuple[int, ...]]], bool]:
    """Same-name CI-V lenders for a declared-absent read.

    Returns the ``(model, bytes)`` lenders and whether any sibling at
    all was considered: borrowing is same-name only, so a lender's
    ``set_*`` entry never feeds a ``get_*`` read.
    """
    lenders: list[tuple[str, tuple[int, ...]]] = []
    considered = False
    for sibling in siblings:
        if sibling.protocol_type != "civ" or sibling.model == config.model:
            continue
        considered = True
        spec = sibling.commands.get(name)
        if isinstance(spec, CivCommandSpec):
            lenders.append((sibling.model, tuple(spec.bytes)))
    return lenders, considered


def _borrowed_read(
    name: str,
    config: RigConfig,
    siblings: tuple[RigConfig, ...],
    *,
    include_private: bool,
) -> tuple[bytes, tuple[str, ...]] | SkippedRead:
    """Borrowed bytes for a declared-absent CI-V read, or why not.

    The bytes come from the same-name ``CivCommandSpec`` of the other
    shipped profiles, and only when every sibling declaring that name
    agrees on them. Borrowed bytes pass through the same filters as
    native ones, then through the borrow safeguard: the command byte
    must be on the positive safe list, and every lending profile must
    declare the entry as a ``get_*`` read.
    """
    lenders, considered = _lending_siblings(name, config, siblings)
    if not lenders:
        if considered:
            detail = f"no sibling profile declares {name}"
        else:
            detail = "no sibling profiles were provided"
        return SkippedRead(name, None, "no_unambiguous_bytes", detail)
    bodies = {body for _, body in lenders}
    if len(bodies) > 1:
        return SkippedRead(
            name, None, "no_unambiguous_bytes", f"siblings disagree on {name}"
        )
    body = bytes(next(iter(bodies)))
    models = tuple(sorted(model for model, _ in lenders))
    reason = _skip_reason_for_body(body, include_private=include_private)
    if reason is not None:
        return SkippedRead(name, body.hex(), reason, _skip_detail(body, reason))
    if body[0] not in _BORROW_READ_SAFE_COMMANDS:
        return SkippedRead(
            name,
            body.hex(),
            "unsafe_family",
            f"command byte 0x{body[0]:02X} is not borrow-safe",
        )
    if not name.startswith("get_"):
        return SkippedRead(
            name,
            body.hex(),
            "unsafe_family",
            f"{name} is not declared as a get_* read by every lending profile",
        )
    return body, models


def _skip_detail(body: bytes, reason: str) -> str:
    """One short human sentence for a withheld CI-V body."""
    if reason == "response_code":
        return f"0x{body[0]:02X} is the radio's reply code, never a request"
    if reason == "private":
        return "stored memory contents need --include-private"
    return f"command byte 0x{body[0]:02X} is never sent (refused family)"


def _plan_civ_list(
    config: RigConfig,
    profile: RadioProfile,
    *,
    include_private: bool,
    siblings: tuple[RigConfig, ...],
) -> ReadPlan:
    asked: list[ReadRequest] = []
    skipped: list[SkippedRead] = []
    routes = set(profile.cmd29_routes)
    command_map = profile.command_map
    if command_map is not None:
        names = sorted(
            name
            for name, spec in config.commands.items()
            if isinstance(spec, (CivCommandSpec, AbsentCommandSpec))
            and name.startswith("get_")
        )
        for name in names:
            spec = config.commands[name]
            if isinstance(spec, CivCommandSpec):
                body = bytes(spec.bytes)
                reason = _skip_reason_for_body(body, include_private=include_private)
                if reason is not None:
                    skipped.append(
                        SkippedRead(
                            name, body.hex(), reason, _skip_detail(body, reason)
                        )
                    )
                    continue
                asked.append(ReadRequest(name, body, "get"))
                expected = "present"
                note = ""
            else:
                borrowed = _borrowed_read(
                    name, config, siblings, include_private=include_private
                )
                if isinstance(borrowed, SkippedRead):
                    skipped.append(borrowed)
                    continue
                body, models = borrowed
                asked.append(
                    ReadRequest(
                        name,
                        body,
                        "get",
                        f"bytes from profile(s): {', '.join(models)}",
                        "absent",
                    )
                )
                expected = "absent"
                note = asked[-1].note
            _command, sub, _prefix = decode_wire_tuple(tuple(body))
            if (_command, sub) in routes:
                for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
                    asked.append(
                        ReadRequest(
                            f"cmd29:{name}:{receiver}",
                            _cmd29_body(body, receiver),
                            "cmd29",
                            note,
                            expected,
                        )
                    )

        if command_map.has("get_freq"):
            freq_body = bytes(command_map.get("get_freq"))
            _command, sub, _prefix = decode_wire_tuple(command_map.get("get_freq"))
            if (_command, sub) not in routes and config.receiver_count >= 2:
                for receiver in (RECEIVER_MAIN, RECEIVER_SUB):
                    asked.append(
                        ReadRequest(
                            f"cmd29:get_freq:{receiver}",
                            _cmd29_body(freq_body, receiver),
                            "cmd29",
                        )
                    )

    for body in _UNKNOWN_BODIES:
        asked.append(
            ReadRequest(f"unknown:{body.hex()}", body, "unknown", "", "absent")
        )

    return ReadPlan(tuple(asked), tuple(skipped))


def build_read_plan(
    config: RigConfig,
    *,
    include_private: bool = False,
    siblings: Iterable[RigConfig] | None = None,
) -> ReadPlan:
    """Every read of one profile, plus one skip entry per withheld read.

    ``siblings`` are the other shipped profiles of the same protocol
    (see ``rigplane.profiles.rig_loader.discover_rigs``); a
    declared-absent CI-V ``get_*`` read borrows their unanimous bytes.
    Without siblings every declared-absent read is skipped
    ``no_unambiguous_bytes``.

    The ``asked`` list is the allow-list: the runner sends only these
    bodies, so nothing here may key the transmitter, start the tuner,
    write memory, send CW, or switch power.
    """
    if config.protocol_type == "yaesu_cat":
        return _plan_cat_list(config, include_private=include_private)
    if config.protocol_type == "civ":
        return _plan_civ_list(
            config,
            config.to_profile(),
            include_private=include_private,
            siblings=tuple(siblings or ()),
        )
    raise ValueError(
        f"probe supports 'civ' and 'yaesu_cat', not {config.protocol_type!r}"
    )


def build_allowed_bodies(
    profile: RadioProfile,
    *,
    include_private: bool = False,
    borrowed_bodies: Collection[bytes] = (),
) -> frozenset[bytes]:
    """Unwrapped frame bodies the runner may send for *profile*.

    Every body :func:`build_read_plan` produces for the profile, with
    the leading ``0x29 <rx>`` wrapper removed from the wrapped ones,
    plus the ``borrowed_bodies`` the plan lent from sibling profiles.
    """
    command_map = profile.command_map
    if command_map is None:
        return frozenset() | frozenset(borrowed_bodies)
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
    return frozenset(bodies) | frozenset(borrowed_bodies) | frozenset(_UNKNOWN_BODIES)


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
    body = unwrap_receiver_prefix(frame_body)
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


def snapshot_names(config: RigConfig, produced: set[str]) -> list[str]:
    """Profile state reads for the before/after snapshot, in order.

    Only reads the pass would send anyway: a name is kept when it is in
    ``produced``, the asked names of the run's own read plan.
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
    return [name for name in candidates if name in produced]
