"""Typed ``[[probe.checks]]`` profile data (MOR-4464 B1).

A check is profile DATA: named receive-side settings the write-check
engine changes one at a time, reads back, and puts back. This module
holds the frozen dataclasses, the ``[probe]`` TOML parse, and the
write-policy lists with the load-time enforcement.

The policy lists live HERE, not in ``rigplane.probe.write_policy``,
because the loader must enforce them at load and the import layers
forbid ``profiles`` importing from ``probe`` (see ``.importlinter``:
``probe`` sits above ``profiles``). ``write_policy`` re-exports these
lists and adds the send-time check; a verb joins or leaves an
allow-list by editing the frozenset below, in this one place.

The adversary is a wrong or careless profile entry written by a
developer or a future profile author, never the radio or its owner:
every write the engine sends must be on its protocol's allow-list,
and every never-listed verb is refused at load (naming the verb) and
again at send time.

Source categories, one line each:

- Yaesu write allow-list: the receive-side CAT verbs a check may
  change (frequency, mode, split state, power level, band select,
  speech processor). Confirmed against the FTX-1 CAT manual: these
  verbs are receive-side settings there.
- Yaesu never-list: transmit/PTT keys, memory writes, keyer/CW and
  voice-memory playback, scan, tuner-adjacent and date/time writes.
  Family scope confirmed against the FTX-1 CAT manual; the meaning of
  each individual verb is to be confirmed by a reviewer holding the
  FT-991A CAT manual.
- CI-V write allow-list: frequency/mode set, VFO select, split,
  levels, and selected-VFO reads. The starting set a real check
  needs; nothing else until one does.
- CI-V never-list: voice TX memory (0x28, confirmed against the
  IC-705/IC-9700 CI-V references), VOX/BK-IN (0x16 46/47, confirmed
  against the same references), DV TX data (0x22, confirmed), TX band
  (0x1E writes, confirmed), memory and scan families from the read
  pass, plus the read pass's refused families. Every other entry is
  to be confirmed by a reviewer holding the manual.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from rigplane.commands import command_carries_sub
from rigplane.commands.command_spec import (
    AbsentCommandSpec,
    CatCommandSpec,
    CivCommandSpec,
    CommandSpec,
)

__all__ = [
    "CIV_CONDITIONAL_VFO",
    "CIV_NEVER_ANY",
    "CIV_NEVER_WRITE",
    "CIV_WRITE_ALLOW",
    "YAESU_NEVER",
    "YAESU_WRITE_ALLOW",
    "ProbeCheck",
    "ProbeCheckStep",
    "ProbePrecondition",
    "ProbeRestoreItem",
    "parse_probe_checks",
    "validate_probe_check",
]

#: Yaesu CAT verbs a check may write: receive-side settings only.
YAESU_WRITE_ALLOW = frozenset({"FA", "FB", "MD", "FT", "PC", "BS", "PR"})

#: CI-V commands a check may write: freq/mode set, VFO select, split,
#: levels, and the selected-VFO pairs.
CIV_WRITE_ALLOW = frozenset({0x05, 0x06, 0x07, 0x0F, 0x14, 0x25, 0x26})

#: Yaesu CAT verbs that are never sent by a check: transmit/PTT keys,
#: memory writes, keyer/CW, voice memory, scan, tuner-adjacent writes.
YAESU_NEVER = frozenset(
    {
        "TX", "MX", "AC", "KY", "KM", "PS", "MW", "MC",
        "EX", "PB", "LM", "AM", "MT", "QI", "MA", "QR",
        "VM", "CH", "SC", "VX", "BI", "DT",
    }
)

#: CI-V (command, sub) pairs never sent in any form (``None`` sub means
#: the whole command): voice TX memory, VOX/BK-IN, and the read pass's
#: refused memory/scan/power/PTT/VFO families plus memory contents.
CIV_NEVER_ANY: frozenset[tuple[int, int | None]] = frozenset(
    {
        (0x28, None),
        (0x16, 0x46),
        (0x16, 0x47),
        (0x22, None),
        (0x12, None),
        (0x08, None),
        (0x09, None),
        (0x0A, None),
        (0x0B, None),
        (0x0E, None),
        (0x17, None),
        (0x18, None),
        (0x1C, None),
        (0x1A, 0x00),
    }
)

#: CI-V (command, sub) pairs refused only as writes (a write carries
#: data past the command prefix): menu-configuration writes, TX-band
#: and filter writes.
CIV_NEVER_WRITE: frozenset[tuple[int, int | None]] = frozenset(
    {
        (0x1A, 0x05),
        (0x1A, 0x01),
        (0x1A, 0x02),
        (0x1E, None),
        (0x1F, None),
    }
)

#: CI-V VFO exchange/equalize writes: allowed only when the check
#: restores both VFOs (two restore items on distinct reads), because
#: they touch the VFO the check did not target.
CIV_CONDITIONAL_VFO: frozenset[tuple[int, int | None]] = frozenset(
    {(0x07, 0xA0), (0x07, 0xB1)}
)

#: Check-level TOML keys: everything else under ``[[probe.checks]]`` is
#: rejected like the rest of the loader.
_CHECK_KEYS = frozenset(
    {
        "id", "title", "explain_what", "explain_after", "warnings",
        "preconditions", "witness", "steps", "restore",
    }
)

#: Step-level TOML keys, per kind below; unknown keys are rejected.
_STEP_KEYS = frozenset(
    {"kind", "command", "values", "line", "body_hex", "question"}
)

_STEP_KINDS = frozenset({"write", "read", "raw_read", "raw_write", "ask"})

#: Restore-item TOML keys; unknown keys are rejected.
_RESTORE_KEYS = frozenset({"read", "write", "map", "rehearse"})

#: Precondition TOML keys; unknown keys are rejected.
_PRECONDITION_KEYS = frozenset({"read", "equals"})

#: One template field: ``{name}`` or ``{name:09d}``.
_TEMPLATE_FIELD = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True, slots=True)
class ProbeCheckStep:
    """One engine step: ``write``/``read`` name a profile command.

    ``write`` carries ``values`` (CAT: formatted into the profile's own
    write template; CI-V: exactly ``{"data_hex": ...}`` appended to the
    profile's own command bytes). ``raw_read``/``raw_write`` carry a
    literal CAT ``line`` or CI-V ``body_hex``. ``ask`` carries the
    question put to the person.
    """

    kind: str
    command: str = ""
    values: tuple[tuple[str, object], ...] = ()
    data_hex: str = ""
    line: str = ""
    body_hex: str = ""
    question: str = ""


@dataclass(frozen=True, slots=True)
class ProbeRestoreItem:
    """One original to put back: the read taken before the first write
    plus the write that restores it.

    ``map`` renames read-form values to write-form values where the two
    differ (``FT;`` answers 0/1 and writes 2/3). ``rehearse = False``
    skips the no-op rehearsal for an item whose rehearsal was shown to
    have a side effect on a supported radio.
    """

    read: str
    write: str
    map: tuple[tuple[str, str], ...] = ()
    rehearse: bool = True


@dataclass(frozen=True, slots=True)
class ProbePrecondition:
    """One gate read before the first write: ``read`` names a profile
    read command, ``equals`` the reply that means VFO mode."""

    read: str
    equals: str


@dataclass(frozen=True, slots=True)
class ProbeCheck:
    """One named write check, profile data for the engine."""

    id: str
    title: str
    explain_what: str
    explain_after: str
    warnings: tuple[str, ...] = ()
    preconditions: tuple[ProbePrecondition, ...] = ()
    witness: tuple[str, ...] = ()
    steps: tuple[ProbeCheckStep, ...] = ()
    restore: tuple[ProbeRestoreItem, ...] = ()


def _fail(filename: str, message: str) -> ValueError:
    from rigplane.profiles.rig_loader import RigLoadError

    return RigLoadError(f"{filename}: {message}")


def _need_table(filename: str, where: str, raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _fail(filename, f"{where} must be a table")
    return dict(raw)


def _need_ascii(filename: str, where: str, value: object) -> str:
    if not isinstance(value, str) or not value:
        raise _fail(filename, f"{where} must be a non-empty string")
    if not value.isascii():
        raise _fail(filename, f"{where} must be ASCII")
    return value


def _cat_read_of(spec: CommandSpec | None) -> str | None:
    if isinstance(spec, CatCommandSpec) and spec.read is not None:
        return spec.read
    return None


def _cat_write_of(spec: CommandSpec | None) -> str | None:
    if isinstance(spec, CatCommandSpec) and spec.write is not None:
        return spec.write
    return None


def _cat_parse_of(spec: CommandSpec | None) -> str | None:
    if isinstance(spec, CatCommandSpec):
        if spec.parse is not None:
            return spec.parse
        return spec.read
    return None


def _civ_bytes_of(spec: CommandSpec | None) -> tuple[int, ...] | None:
    if isinstance(spec, CivCommandSpec):
        return tuple(spec.bytes)
    return None


def _check_cat_line(filename: str, where: str, line: str) -> str:
    """A literal CAT wire line; returns its two-letter verb."""
    if (
        not line.isascii()
        or not line.endswith(";")
        or len(line) < 3
        or not line[:2].isalpha()
    ):
        raise _fail(filename, f"{where} must be a CAT line like 'FA;'")
    return line[:2].upper()


def _check_civ_body(filename: str, where: str, body_hex: str) -> bytes:
    try:
        body = bytes.fromhex(body_hex)
    except ValueError:
        raise _fail(filename, f"{where} must be hex") from None
    if not body:
        raise _fail(filename, f"{where} must not be empty")
    return body


def _civ_sub_of(body: bytes) -> int | None:
    if len(body) >= 2 and command_carries_sub(body[0]):
        return body[1]
    return None


def _civ_never_hit(body: bytes) -> str | None:
    """Why a CI-V body is never sent in any form, or ``None``."""
    command, sub = body[0], _civ_sub_of(body)
    if (command, sub) in CIV_NEVER_ANY or (command, None) in CIV_NEVER_ANY:
        return f"0x{command:02X}"
    if (command, sub) in CIV_CONDITIONAL_VFO:
        return f"0x{command:02X} {sub:02X}"
    return None


def _civ_write_hit(body: bytes) -> str | None:
    """Why a CI-V body is refused as a write, or ``None``."""
    never = _civ_never_hit(body)
    if never is not None:
        return never
    command, sub = body[0], _civ_sub_of(body)
    prefix = 2 if sub is not None else 1
    is_write = len(body) > prefix
    if is_write and (
        (command, sub) in CIV_NEVER_WRITE or (command, None) in CIV_NEVER_WRITE
    ):
        return f"0x{command:02X}"
    return None


def _parse_step(
    filename: str,
    where: str,
    raw: object,
    *,
    commands: Mapping[str, CommandSpec],
    protocol_type: str,
    check_id: str,
) -> ProbeCheckStep:
    mapping = _need_table(filename, where, raw)
    unknown = sorted(set(mapping) - _STEP_KEYS)
    if unknown:
        raise _fail(filename, f"{where} unknown key(s): {unknown!r}")
    kind = mapping.get("kind")
    if kind not in _STEP_KINDS:
        raise _fail(
            filename, f"{where}.kind must be one of {sorted(_STEP_KINDS)}"
        )
    if protocol_type == "yaesu_cat":
        return _parse_cat_step(filename, where, mapping, kind, commands, check_id)
    if protocol_type == "civ":
        return _parse_civ_step(filename, where, mapping, kind, commands, check_id)
    raise _fail(filename, f"{where}: probe checks need USB CI-V or Yaesu CAT")


def _forbid(
    filename: str, where: str, mapping: dict[str, Any], *keys: str
) -> None:
    present = [key for key in keys if key in mapping]
    if present:
        raise _fail(filename, f"{where} must not carry {present!r}")


def _template_fields(template: str) -> tuple[str, ...]:
    return tuple(
        field.split(":")[0].split("!")[0] for field in _TEMPLATE_FIELD.findall(template)
    )


def _parse_cat_step(
    filename: str,
    where: str,
    mapping: dict[str, Any],
    kind: str,
    commands: Mapping[str, CommandSpec],
    check_id: str,
) -> ProbeCheckStep:
    if kind == "ask":
        _forbid(filename, where, mapping, "command", "values", "line", "body_hex")
        return ProbeCheckStep(
            kind=kind, question=_need_ascii(filename, f"{where}.question", mapping.get("question"))
        )
    if kind in ("raw_read", "raw_write"):
        _forbid(filename, where, mapping, "command", "values", "body_hex", "question")
        line = _need_ascii(filename, f"{where}.line", mapping.get("line"))
        verb = _check_cat_line(filename, f"{where}.line", line)
        if verb in YAESU_NEVER:
            raise _fail(
                filename, f"{where}.line verb {verb!r} is never sent by a check"
            )
        return ProbeCheckStep(kind=kind, line=line)
    _forbid(filename, where, mapping, "line", "body_hex", "question")
    name = mapping.get("command")
    if not isinstance(name, str) or not name:
        raise _fail(filename, f"{where}.command must be a non-empty string")
    spec = commands.get(name)
    if kind == "write":
        template = _cat_write_of(spec)
        if template is None:
            raise _fail(
                filename, f"{where}.command {name!r} declares no CAT write"
            )
        raw_values = mapping.get("values", {})
        if not isinstance(raw_values, dict):
            raise _fail(filename, f"{where}.values must be a table")
        for key, value in raw_values.items():
            if not isinstance(value, (str, int)) or isinstance(value, bool):
                raise _fail(
                    filename, f"{where}.values.{key} must be a string or integer"
                )
        fields = _template_fields(template)
        missing = [field for field in fields if field not in raw_values]
        if missing:
            raise _fail(
                filename, f"{where}.values misses {missing!r} for {name!r}"
            )
        extra = sorted(set(raw_values) - set(fields))
        if extra:
            raise _fail(filename, f"{where}.values has no slot for {extra!r}")
        try:
            body = template.format(
                **{key: value for key, value in raw_values.items()}
            )
        except (KeyError, ValueError) as error:
            raise _fail(
                filename, f"{where}.values do not fit {name!r} ({error})"
            ) from error
        verb = _check_cat_line(filename, f"{where}.command", body)
        if verb in YAESU_NEVER:
            raise _fail(
                filename,
                f"{where}.command {name!r} writes never-listed verb {verb!r}",
            )
        if verb not in YAESU_WRITE_ALLOW:
            raise _fail(
                filename,
                f"{where}.command {name!r} writes verb {verb!r} "
                "which is not on the check allow-list",
            )
        return ProbeCheckStep(
            kind=kind,
            command=name,
            values=tuple(sorted(raw_values.items())),
        )
    read = _cat_read_of(spec)
    if read is None:
        raise _fail(filename, f"{where}.command {name!r} declares no CAT read")
    if "{" in read:
        raise _fail(
            filename, f"{where}.command {name!r} read is not a concrete line"
        )
    return ProbeCheckStep(kind=kind, command=name)


def _parse_civ_step(
    filename: str,
    where: str,
    mapping: dict[str, Any],
    kind: str,
    commands: Mapping[str, CommandSpec],
    check_id: str,
) -> ProbeCheckStep:
    if kind == "ask":
        _forbid(filename, where, mapping, "command", "values", "line", "body_hex")
        return ProbeCheckStep(
            kind=kind, question=_need_ascii(filename, f"{where}.question", mapping.get("question"))
        )
    if kind in ("raw_read", "raw_write"):
        _forbid(filename, where, mapping, "command", "values", "line", "question")
        body_hex = _need_ascii(
            filename, f"{where}.body_hex", mapping.get("body_hex")
        )
        _check_civ_body(filename, f"{where}.body_hex", body_hex)
        return ProbeCheckStep(kind=kind, body_hex=body_hex.lower())
    _forbid(filename, where, mapping, "line", "body_hex", "question")
    name = mapping.get("command")
    if not isinstance(name, str) or not name:
        raise _fail(filename, f"{where}.command must be a non-empty string")
    spec = commands.get(name)
    declared = _civ_bytes_of(spec)
    if kind == "write":
        if declared is None:
            raise _fail(
                filename, f"{where}.command {name!r} declares no CI-V bytes"
            )
        raw_values = mapping.get("values", {})
        if not isinstance(raw_values, dict) or set(raw_values) != {"data_hex"}:
            raise _fail(filename, f"{where}.values must be exactly data_hex")
        data_hex = raw_values["data_hex"]
        if not isinstance(data_hex, str):
            raise _fail(filename, f"{where}.values.data_hex must be hex")
        try:
            data = bytes.fromhex(data_hex)
        except ValueError:
            raise _fail(
                filename, f"{where}.values.data_hex must be hex"
            ) from None
        body = bytes(declared) + data
        hit = _civ_write_hit(body)
        if hit is not None:
            raise _fail(
                filename, f"{where}.command {name!r} writes never-listed {hit}"
            )
        if declared[0] not in CIV_WRITE_ALLOW:
            raise _fail(
                filename,
                f"{where}.command {name!r} writes 0x{declared[0]:02X} "
                "which is not on the check allow-list",
            )
        return ProbeCheckStep(
            kind=kind, command=name, data_hex=data_hex.lower()
        )
    if declared is None:
        raise _fail(filename, f"{where}.command {name!r} declares no CI-V bytes")
    return ProbeCheckStep(kind=kind, command=name)


def _cat_verbs_of_restore(
    commands: Mapping[str, CommandSpec], item: ProbeRestoreItem
) -> set[str]:
    """The CAT verbs one restore item touches (read and write sides)."""
    verbs: set[str] = set()
    read = _cat_read_of(commands.get(item.read))
    if read is not None and len(read) >= 2:
        verbs.add(read[:2].upper())
    write = _cat_write_of(commands.get(item.write))
    if write is not None and len(write) >= 2:
        verbs.add(write[:2].upper())
    return verbs


def _civ_commands_of_restore(
    commands: Mapping[str, CommandSpec], item: ProbeRestoreItem
) -> set[int]:
    found: set[int] = set()
    for name in (item.read, item.write):
        declared = _civ_bytes_of(commands.get(name))
        if declared:
            found.add(declared[0])
    return found


def _parse_restore(
    filename: str, where: str, raw: object
) -> ProbeRestoreItem:
    mapping = _need_table(filename, where, raw)
    unknown = sorted(set(mapping) - _RESTORE_KEYS)
    if unknown:
        raise _fail(filename, f"{where} unknown key(s): {unknown!r}")
    read = mapping.get("read")
    write = mapping.get("write")
    if not isinstance(read, str) or not read:
        raise _fail(filename, f"{where}.read must be a non-empty string")
    if not isinstance(write, str) or not write:
        raise _fail(filename, f"{where}.write must be a non-empty string")
    raw_map = mapping.get("map", {})
    if not isinstance(raw_map, dict):
        raise _fail(filename, f"{where}.map must be a table")
    table: list[tuple[str, str]] = []
    for key, value in raw_map.items():
        if not isinstance(key, str) or not key:
            raise _fail(filename, f"{where}.map keys must be non-empty strings")
        if not isinstance(value, str) or not value:
            raise _fail(filename, f"{where}.map values must be non-empty strings")
        table.append((key, value))
    rehearse = mapping.get("rehearse", True)
    if not isinstance(rehearse, bool):
        raise _fail(filename, f"{where}.rehearse must be a boolean")
    return ProbeRestoreItem(
        read=read, write=write, map=tuple(table), rehearse=rehearse
    )


def _parse_precondition(filename: str, where: str, raw: object) -> ProbePrecondition:
    mapping = _need_table(filename, where, raw)
    unknown = sorted(set(mapping) - _PRECONDITION_KEYS)
    if unknown:
        raise _fail(filename, f"{where} unknown key(s): {unknown!r}")
    read = mapping.get("read")
    if not isinstance(read, str) or not read:
        raise _fail(filename, f"{where}.read must be a non-empty string")
    return ProbePrecondition(
        read=read,
        equals=_need_ascii(filename, f"{where}.equals", mapping.get("equals")),
    )


def _read_capable(
    filename: str, where: str, name: str, commands: Mapping[str, CommandSpec]
) -> None:
    spec = commands.get(name)
    if isinstance(spec, CatCommandSpec):
        if spec.read is None or "{" in spec.read:
            raise _fail(
                filename, f"{where} {name!r} declares no concrete CAT read"
            )
        return
    if isinstance(spec, CivCommandSpec):
        return
    raise _fail(filename, f"{where} {name!r} declares no read")


def validate_probe_check(
    filename: str,
    check: ProbeCheck,
    *,
    commands: Mapping[str, CommandSpec],
    protocol_type: str,
) -> None:
    """Enforce the write policy for one parsed check; never-list verbs
    fail naming the verb."""
    if protocol_type == "yaesu_cat":
        restore_verbs: set[str] = set()
        for item in check.restore:
            _read_capable(filename, f"check {check.id!r} restore", item.read, commands)
            write = _cat_write_of(commands.get(item.write))
            if write is None or len(write) < 2:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} declares no CAT write",
                )
            verb = write[:2].upper()
            if verb in YAESU_NEVER:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} "
                    f"writes never-listed verb {verb!r}",
                )
            if verb not in YAESU_WRITE_ALLOW:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} "
                    f"writes verb {verb!r} which is not on the check allow-list",
                )
            parse = _cat_parse_of(commands.get(item.read))
            if parse is None:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.read!r} declares no CAT parse",
                )
            restore_verbs |= _cat_verbs_of_restore(commands, item)
        for step in check.steps:
            if step.kind == "raw_write" and step.line[:2].upper() not in restore_verbs:
                raise _fail(
                    filename,
                    f"check {check.id!r} raw_write {step.line!r} "
                    "needs its own restore item in the same check",
                )
        return
    if protocol_type == "civ":
        both_vfos = len({item.read for item in check.restore}) >= 2
        restore_commands: set[int] = set()
        for item in check.restore:
            _read_capable(filename, f"check {check.id!r} restore", item.read, commands)
            declared = _civ_bytes_of(commands.get(item.write))
            if declared is None:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} declares no CI-V bytes",
                )
            body = bytes(declared)
            hit = _civ_write_hit(body) or _civ_write_hit(body + b"\x00")
            if hit is not None:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} "
                    f"writes never-listed {hit}",
                )
            if body[0] not in CIV_WRITE_ALLOW:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} "
                    f"writes 0x{body[0]:02X} which is not on the check allow-list",
                )
            pair = (body[0], _civ_sub_of(body))
            if pair in CIV_CONDITIONAL_VFO and not both_vfos:
                raise _fail(
                    filename,
                    f"check {check.id!r} restore {item.write!r} "
                    "needs both VFOs in the restore list",
                )
            restore_commands |= _civ_commands_of_restore(commands, item)
        for step in check.steps:
            if step.kind != "raw_write":
                continue
            body = bytes.fromhex(step.body_hex)
            if _civ_never_hit(body) or _civ_write_hit(body):
                raise _fail(
                    filename,
                    f"check {check.id!r} raw_write {step.body_hex!r} "
                    "names a never-listed command",
                )
            if body[0] not in CIV_WRITE_ALLOW:
                raise _fail(
                    filename,
                    f"check {check.id!r} raw_write {step.body_hex!r} "
                    f"writes 0x{body[0]:02X} which is not on the check allow-list",
                )
            pair = (body[0], _civ_sub_of(body))
            both_vfos = len({item.read for item in check.restore}) >= 2
            if pair in CIV_CONDITIONAL_VFO and not both_vfos:
                raise _fail(
                    filename,
                    f"check {check.id!r} raw_write {step.body_hex!r} "
                    "needs both VFOs in the restore list",
                )
            if body[0] not in restore_commands:
                raise _fail(
                    filename,
                    f"check {check.id!r} raw_write {step.body_hex!r} "
                    "needs its own restore item in the same check",
                )
        return
    raise _fail(
        filename, f"check {check.id!r}: probe checks need USB CI-V or Yaesu CAT"
    )


def parse_probe_checks(
    filename: str,
    raw: object,
    *,
    commands: Mapping[str, CommandSpec],
    protocol_type: str,
) -> tuple[ProbeCheck, ...]:
    """Parse ``[probe]`` into typed checks; ``()`` when absent.

    Structural mistakes, unknown keys, non-ASCII customer text, and
    write-policy violations all fail here with the verb or key named.
    """
    if raw is None:
        return ()
    section = _need_table(filename, "[probe]", raw)
    unknown = sorted(set(section) - {"checks"})
    if unknown:
        raise _fail(filename, f"[probe] unknown key(s): {unknown!r}")
    raw_checks = section.get("checks", [])
    if not isinstance(raw_checks, list):
        raise _fail(filename, "[probe].checks must be an array")
    checks: list[ProbeCheck] = []
    seen: set[str] = set()
    for index, raw_check in enumerate(raw_checks):
        where = f"[probe.checks#{index}]"
        mapping = _need_table(filename, where, raw_check)
        unknown_check = sorted(set(mapping) - _CHECK_KEYS)
        if unknown_check:
            raise _fail(filename, f"{where} unknown key(s): {unknown_check!r}")
        check_id = mapping.get("id")
        if (
            not isinstance(check_id, str)
            or not check_id.isascii()
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", check_id)
        ):
            raise _fail(filename, f"{where}.id must be a slug like 'power-2m'")
        if check_id in seen:
            raise _fail(filename, f"{where}.id {check_id!r} repeats a check id")
        seen.add(check_id)
        title = _need_ascii(filename, f"{where}.title", mapping.get("title"))
        explain_what = _need_ascii(
            filename, f"{where}.explain_what", mapping.get("explain_what")
        )
        explain_after = _need_ascii(
            filename, f"{where}.explain_after", mapping.get("explain_after")
        )
        raw_warnings = mapping.get("warnings", ())
        if not isinstance(raw_warnings, (list, tuple)):
            raise _fail(filename, f"{where}.warnings must be an array")
        warnings = tuple(
            _need_ascii(filename, f"{where}.warnings[{i}]", warning)
            for i, warning in enumerate(raw_warnings)
        )
        raw_preconditions = mapping.get("preconditions", [])
        if not isinstance(raw_preconditions, list):
            raise _fail(filename, f"{where}.preconditions must be an array")
        preconditions = tuple(
            _parse_precondition(filename, f"{where}.preconditions[{i}]", raw_item)
            for i, raw_item in enumerate(raw_preconditions)
        )
        raw_witness = mapping.get("witness", [])
        if not isinstance(raw_witness, list) or not all(
            isinstance(name, str) and name for name in raw_witness
        ):
            raise _fail(filename, f"{where}.witness must be an array of names")
        for name in raw_witness:
            _read_capable(filename, f"{where}.witness", name, commands)
        raw_steps = mapping.get("steps", [])
        if not isinstance(raw_steps, list) or not raw_steps:
            raise _fail(filename, f"{where}.steps must be a non-empty array")
        steps = tuple(
            _parse_step(
                filename,
                f"{where}.steps[{i}]",
                raw_item,
                commands=commands,
                protocol_type=protocol_type,
                check_id=check_id,
            )
            for i, raw_item in enumerate(raw_steps)
        )
        raw_restore = mapping.get("restore", [])
        if not isinstance(raw_restore, list):
            raise _fail(filename, f"{where}.restore must be an array")
        restore = tuple(
            _parse_restore(filename, f"{where}.restore[{i}]", raw_item)
            for i, raw_item in enumerate(raw_restore)
        )
        if any(step.kind in ("write", "raw_write") for step in steps) and not restore:
            raise _fail(
                filename, f"{where} has write steps but no restore items"
            )
        check = ProbeCheck(
            id=check_id,
            title=title,
            explain_what=explain_what,
            explain_after=explain_after,
            warnings=warnings,
            preconditions=preconditions,
            witness=tuple(raw_witness),
            steps=steps,
            restore=restore,
        )
        validate_probe_check(
            filename, check, commands=commands, protocol_type=protocol_type
        )
        checks.append(check)
    return tuple(checks)
