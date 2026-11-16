"""Typed ``[[probe.checks]]`` profile data and the write policy (MOR-4464 B1).

A check is profile DATA: named receive-side settings the write-check engine
changes one at a time, reads back and puts back. The policy lists and
``policy_refusal`` live here, not in ``rigplane.probe``, because the loader
enforces them at load and ``probe`` sits above ``profiles`` in the import
layers; the engine enforces them again at send time. The adversary is a wrong
profile entry written by a developer, never the radio or its owner.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any, TypeVar

from rigplane.commands import command_carries_sub
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec, CommandSpec

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
    "both_vfos",
    "check_send_allowed",
    "command_key",
    "declared_body",
    "parse_probe_checks",
    "policy_refusal",
]


def _civ_pairs(spec: str) -> frozenset[tuple[int, int | None]]:
    """``"28 16:46"``: hex command, optionally ``:`` and a hex sub."""
    parts = (item.partition(":") for item in spec.split())
    return frozenset(
        (int(command, 16), int(sub, 16) if sub else None) for command, _, sub in parts
    )


#: Yaesu CAT verbs a check may write: receive-side settings only.
YAESU_WRITE_ALLOW = frozenset("FA FB MD FT PC BS PR".split())

#: CI-V commands a check may write: freq/mode set, VFO select, levels.
CIV_WRITE_ALLOW = frozenset(c for c, _ in _civ_pairs("05 06 07 0F 14 25 26"))

#: Yaesu verbs never sent by a check: transmit/PTT keys, memory writes,
#: keyer/CW, voice memory, scan, tuner-adjacent writes.
YAESU_NEVER = frozenset(
    "TX MX AC KY KM PS MW MC EX PB LM AM MT QI MA QR VM CH SC VX BI DT".split()
)

#: CI-V pairs never sent in any form (no sub: the whole command): voice TX
#: memory, VOX/BK-IN, DV TX data, and the read pass's refused memory, scan,
#: power, PTT and VFO families.
CIV_NEVER_ANY = _civ_pairs("28 16:46 16:47 22 12 08 09 0A 0B 0E 17 18 1C 1A:00")

#: CI-V pairs refused only as writes: menu configuration, TX band, filters.
CIV_NEVER_WRITE = _civ_pairs("1A:05 1A:01 1A:02 1E 1F")

#: VFO exchange/equalize writes: allowed only when the check restores both
#: VFOs (two restore items on distinct reads).
CIV_CONDITIONAL_VFO = _civ_pairs("07:A0 07:B1")

_T = TypeVar("_T")
_PROTOCOLS = ("yaesu_cat", "civ")
_NEED_PROTOCOL = "probe checks need USB CI-V or Yaesu CAT"

#: Keys per TOML table; anything else is rejected.
_CHECK_KEYS = frozenset(
    "id title explain_what explain_after warnings preconditions witness steps restore".split()
)
_RESTORE_KEYS = frozenset("read write map rehearse".split())
_STEP_KEYS = {
    "ask": ("question",),
    "write": ("command", "values"),
    "read": ("command",),
}
_RAW_KINDS = ("raw_read", "raw_write")

#: One template field: ``{name}`` or ``{name:09d}``.
_TEMPLATE_FIELD = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True, slots=True)
class ProbeCheckStep:
    """One engine step. ``write``/``read`` name a profile command (a CAT
    write fills ``values`` into the profile's template; CI-V appends
    ``data_hex``); ``raw_*`` carry a literal CAT ``line`` or CI-V
    ``body_hex``; ``ask`` carries the question put to the person."""

    kind: str
    command: str = ""
    values: tuple[tuple[str, object], ...] = ()
    data_hex: str = ""
    line: str = ""
    body_hex: str = ""
    question: str = ""


@dataclass(frozen=True, slots=True)
class ProbeRestoreItem:
    """One original to put back: the read taken before the first write plus
    the write that restores it. ``map`` renames read-form values to
    write-form values; ``rehearse = False`` skips the no-op rehearsal."""

    read: str
    write: str
    map: tuple[tuple[str, str], ...] = ()
    rehearse: bool = True


@dataclass(frozen=True, slots=True)
class ProbePrecondition:
    """A gate read before the first write; ``equals`` is the reply that
    means VFO mode."""

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


def _civ_sub_of(body: bytes) -> int | None:
    return body[1] if len(body) >= 2 and command_carries_sub(body[0]) else None


def both_vfos(restore: tuple[ProbeRestoreItem, ...]) -> bool:
    """Whether a check restores both VFOs (two distinct restore reads)."""
    return len({item.read for item in restore}) >= 2


def command_key(protocol: str, body: bytes) -> str | int:
    """The verb (CAT) or command byte (CI-V) a wire body addresses."""
    return (
        body[:2].decode("ascii", "replace").upper()
        if protocol == "yaesu_cat"
        else body[0]
    )


def policy_refusal(
    protocol: str, body: bytes, *, write: bool, both_vfos: bool = False
) -> str | None:
    """Why a body must not be sent, naming the verb; ``None`` when allowed.

    ``write`` adds the allow-list, the write-only never-list and the
    both-VFOs rule; a read only has to avoid the never-list.
    """
    if not body or protocol not in _PROTOCOLS:
        return "empty body" if not body else _NEED_PROTOCOL
    key = command_key(protocol, body)
    if protocol == "yaesu_cat":
        if key in YAESU_NEVER:
            return f"never-listed CAT verb {key!r}"
        if write and key not in YAESU_WRITE_ALLOW:
            return f"CAT verb {key!r} is not on the allow-list"
        return None
    sub = _civ_sub_of(body)
    keys = {(body[0], sub), (body[0], None)}
    name = f"0x{body[0]:02X}"
    if keys & CIV_NEVER_ANY:
        return f"never-listed CI-V command {name}"
    if (body[0], sub) in CIV_CONDITIONAL_VFO and not (write and both_vfos):
        return f"CI-V {name} {sub:02X} needs both VFOs in the restore list"
    if write and keys & CIV_NEVER_WRITE:
        return f"never-listed CI-V write {name}"
    if write and body[0] not in CIV_WRITE_ALLOW:
        return f"CI-V {name} is not on the allow-list"
    return None


def check_send_allowed(
    protocol: str, body: bytes, *, write: bool = True, both_vfos: bool = False
) -> None:
    """Refuse a body the engine must never send (``ValueError`` names it)."""
    why = policy_refusal(protocol, body, write=write, both_vfos=both_vfos)
    if why is not None:
        raise ValueError(f"refusing to send: {why}")


def declared_body(
    spec: CommandSpec | None, protocol: str, part: str = "read"
) -> bytes | None:
    """A profile command's declared wire bytes: the CAT ``read``/``write``/
    ``parse`` template text, or the CI-V command bytes; ``None`` if absent."""
    if isinstance(spec, CivCommandSpec) and protocol == "civ":
        return bytes(spec.bytes)
    if isinstance(spec, CatCommandSpec) and protocol == "yaesu_cat":
        text = getattr(spec, part)
        if text is None and part == "parse":
            text = spec.read
        return None if text is None else str(text).encode("ascii")
    return None


@dataclass(frozen=True, slots=True)
class _Loader:
    """The profile being loaded: the context every parse helper shares."""

    filename: str
    commands: Mapping[str, CommandSpec]
    protocol: str

    @property
    def cat(self) -> bool:
        return self.protocol == "yaesu_cat"

    def fail(self, message: str) -> ValueError:
        from rigplane.profiles.rig_loader import RigLoadError

        error: ValueError = RigLoadError(f"{self.filename}: {message}")
        return error

    def table(self, where: str, raw: object, keys: frozenset[str]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise self.fail(f"{where} must be a table")
        if unknown := sorted(set(raw) - keys):
            raise self.fail(f"{where} unknown key(s): {unknown!r}")
        return dict(raw)

    def text(self, where: str, value: object) -> str:
        """A non-empty ASCII string (customer text and names)."""
        if not isinstance(value, str) or not value:
            raise self.fail(f"{where} must be a non-empty string")
        if not value.isascii():
            raise self.fail(f"{where} must be ASCII")
        return value

    def each(
        self,
        where: str,
        raw: object,
        parse: Callable[[str, Any], _T],
        *,
        nonempty: bool = False,
    ) -> tuple[_T, ...]:
        """Parse every element of an array with ``parse(where[i], element)``."""
        if not isinstance(raw, list) or (nonempty and not raw):
            raise self.fail(f"{where} must be a {'non-empty ' * nonempty}array")
        return tuple(parse(f"{where}[{i}]", item) for i, item in enumerate(raw))

    def wire(self, where: str, text: str) -> bytes:
        """A literal wire body: a CAT line like ``FA;`` or CI-V hex."""
        if self.cat:
            line = text.isascii() and text.endswith(";") and len(text) >= 3
            if not (line and text[:2].isalpha()):
                raise self.fail(f"{where} must be a CAT line like 'FA;'")
            return text.encode("ascii")
        try:
            body = bytes.fromhex(text)
        except ValueError:
            raise self.fail(f"{where} must be hex") from None
        if not body:
            raise self.fail(f"{where} must not be empty")
        return body

    def read_capable(self, where: str, name: str) -> None:
        body = declared_body(self.commands.get(name), self.protocol)
        if body is None or (self.cat and b"{" in body):
            raise self.fail(f"{where} {name!r} declares no concrete read")

    def refuse(self, where: str, body: bytes, *, write: bool, vfos: bool) -> None:
        if why := policy_refusal(self.protocol, body, write=write, both_vfos=vfos):
            raise self.fail(f"{where}: {why}")

    def write_step(
        self, where: str, mapping: dict[str, Any], name: str, vfos: bool
    ) -> ProbeCheckStep:
        template = declared_body(self.commands.get(name), self.protocol, "write")
        if template is None:
            raise self.fail(f"{where}.command {name!r} declares no write")
        values = mapping.get("values", {})
        fields = {"data_hex"}
        if self.cat:
            found = _TEMPLATE_FIELD.findall(template.decode("ascii"))
            fields = {f.split(":")[0].split("!")[0] for f in found}
        if not isinstance(values, dict) or set(values) != fields:
            raise self.fail(f"{where}.values must be exactly {sorted(fields)!r}")
        for key, value in values.items():
            number = self.cat and isinstance(value, int) and not isinstance(value, bool)
            if not (isinstance(value, str) or number):
                raise self.fail(f"{where}.values.{key} must be a string or integer")
        try:
            if self.cat:
                line = template.decode("ascii").format(**values)
            else:
                line = (template + bytes.fromhex(values["data_hex"])).hex()
        except (KeyError, ValueError) as error:
            raise self.fail(f"{where}.values do not fit {name!r} ({error})") from error
        body = self.wire(f"{where}.command", line)
        self.refuse(f"{where}.command {name!r}", body, write=True, vfos=vfos)
        if self.cat:
            return ProbeCheckStep(
                "write", command=name, values=tuple(sorted(values.items()))
            )
        return ProbeCheckStep(
            "write", command=name, data_hex=values["data_hex"].lower()
        )

    def step(self, where: str, raw: object, vfos: bool) -> ProbeCheckStep:
        raw_key = "line" if self.cat else "body_hex"
        keys = frozenset({"kind", "question", "command", "values", "line", "body_hex"})
        mapping = self.table(where, raw, keys)
        kind = mapping.get("kind")
        if kind not in (*_STEP_KEYS, *_RAW_KINDS):
            raise self.fail(
                f"{where}.kind must be one of {sorted((*_STEP_KEYS, *_RAW_KINDS))}"
            )
        if extra := sorted(set(mapping) - {"kind", *_STEP_KEYS.get(kind, (raw_key,))}):
            raise self.fail(f"{where} must not carry {extra!r}")
        if kind == "ask":
            return ProbeCheckStep(
                kind, question=self.text(f"{where}.question", mapping.get("question"))
            )
        if kind in _RAW_KINDS:
            text = self.text(f"{where}.{raw_key}", mapping.get(raw_key))
            body = self.wire(f"{where}.{raw_key}", text)
            self.refuse(
                f"{where}.{raw_key}", body, write=kind == "raw_write", vfos=vfos
            )
            return (
                ProbeCheckStep(kind, line=text)
                if self.cat
                else ProbeCheckStep(kind, body_hex=text.lower())
            )
        name = self.text(f"{where}.command", mapping.get("command"))
        if kind == "write":
            return self.write_step(where, mapping, name, vfos)
        self.read_capable(f"{where}.command", name)
        return ProbeCheckStep("read", command=name)

    def restore(self, where: str, raw: object) -> ProbeRestoreItem:
        mapping = self.table(where, raw, _RESTORE_KEYS)
        pairs = mapping.get("map", {})
        if not isinstance(pairs, dict):
            raise self.fail(f"{where}.map must be a table")
        rehearse = mapping.get("rehearse", True)
        if not isinstance(rehearse, bool):
            raise self.fail(f"{where}.rehearse must be a boolean")
        return ProbeRestoreItem(
            read=self.text(f"{where}.read", mapping.get("read")),
            write=self.text(f"{where}.write", mapping.get("write")),
            map=tuple(
                (
                    self.text(f"{where}.map key", key),
                    self.text(f"{where}.map value", value),
                )
                for key, value in pairs.items()
            ),
            rehearse=rehearse,
        )

    def precondition(self, where: str, raw: object) -> ProbePrecondition:
        mapping = self.table(where, raw, frozenset({"read", "equals"}))
        return ProbePrecondition(
            self.text(f"{where}.read", mapping.get("read")),
            self.text(f"{where}.equals", mapping.get("equals")),
        )

    def validate(self, check: ProbeCheck) -> None:
        """Restore items obey the write policy; every raw write has its own
        restore item (same verb or command) in the check."""
        vfos = both_vfos(check.restore)
        own: set[str | int] = set()
        for item in check.restore:
            where = f"check {check.id!r} restore {item.write!r}"
            self.read_capable(f"check {check.id!r} restore", item.read)
            write = declared_body(self.commands.get(item.write), self.protocol, "write")
            if write is None:
                raise self.fail(f"{where} declares no write")
            self.refuse(where, write, write=True, vfos=vfos)
            read = declared_body(self.commands.get(item.read), self.protocol, "parse")
            if read is None:
                raise self.fail(
                    f"check {check.id!r} restore {item.read!r} declares no parse"
                )
            own |= {command_key(self.protocol, write), command_key(self.protocol, read)}
        for step in (s for s in check.steps if s.kind == "raw_write"):
            raw = (
                step.line.encode("ascii") if self.cat else bytes.fromhex(step.body_hex)
            )
            if command_key(self.protocol, raw) not in own:
                raise self.fail(
                    f"check {check.id!r} raw_write needs its own restore item"
                )

    def check(self, where: str, raw: object) -> ProbeCheck:
        mapping = self.table(where, raw, _CHECK_KEYS)
        if not isinstance(mapping.get("id"), str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9-]*", mapping["id"]
        ):
            raise self.fail(f"{where}.id must be a slug like 'power-2m'")
        witness = self.each(f"{where}.witness", mapping.get("witness", []), self.text)
        for name in witness:
            self.read_capable(f"{where}.witness", name)
        restore = self.each(
            f"{where}.restore", mapping.get("restore", []), self.restore
        )
        steps = self.each(
            f"{where}.steps",
            mapping.get("steps", []),
            lambda path, step: self.step(path, step, both_vfos(restore)),
            nonempty=True,
        )
        if not restore and any(step.kind in ("write", "raw_write") for step in steps):
            raise self.fail(f"{where} has write steps but no restore items")
        text = self.text
        check = ProbeCheck(
            id=mapping["id"],
            title=text(f"{where}.title", mapping.get("title")),
            explain_what=text(f"{where}.explain_what", mapping.get("explain_what")),
            explain_after=text(f"{where}.explain_after", mapping.get("explain_after")),
            warnings=self.each(f"{where}.warnings", mapping.get("warnings", []), text),
            preconditions=self.each(
                f"{where}.preconditions",
                mapping.get("preconditions", []),
                self.precondition,
            ),
            witness=witness,
            steps=steps,
            restore=restore,
        )
        self.validate(check)
        return check


def parse_probe_checks(
    filename: str,
    raw: object,
    *,
    commands: Mapping[str, CommandSpec],
    protocol_type: str,
) -> tuple[ProbeCheck, ...]:
    """Parse ``[probe]`` into typed checks; ``()`` when absent. Structure,
    unknown keys, non-ASCII customer text and write-policy violations fail
    here with the verb or key named."""
    if raw is None:
        return ()
    loader = _Loader(filename, commands, protocol_type)
    section = loader.table("[probe]", raw, frozenset({"checks"}))
    if section.get("checks") and protocol_type not in _PROTOCOLS:
        raise loader.fail(f"[probe]: {_NEED_PROTOCOL}")
    checks = loader.each("[probe].checks", section.get("checks", []), loader.check)
    ids = [check.id for check in checks]
    if repeated := next((name for name in ids if ids.count(name) > 1), None):
        raise loader.fail(f"[probe.checks] id {repeated!r} repeats a check id")
    return checks
