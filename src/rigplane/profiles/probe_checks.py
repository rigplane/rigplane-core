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
from dataclasses import dataclass, replace
from collections.abc import Callable, Collection, Mapping, Sequence
from typing import Any, TypeVar

from rigplane.commands import command_carries_sub
from rigplane.commands._frame import unwrap_receiver_prefix
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec, CommandSpec
from rigplane.core.types import Mode

__all__ = [
    "CIV_DIGITAL_VOICE",
    "CIV_READ_REFUSED",
    "CIV_WRITE_ALLOW",
    "YAESU_NEVER",
    "YAESU_WRITE_ALLOW",
    "ProbeCheck",
    "ProbeCheckStep",
    "ProbePrecondition",
    "ProbeRestoreItem",
    "check_send_allowed",
    "command_key",
    "declared_body",
    "digital_voice_codes",
    "excluded_from_probes",
    "parse_probe_checks",
    "policy_refusal",
    "restored_settings",
    "setting_of",
]

#: What one write changes and one read returns: CAT, the template text before
#: the first field (``MD0`` of ``MD0{mode};``, or a whole raw line); CI-V,
#: ``(receiver, command, sub)`` with the read and write forms of one setting
#: folded together (``_CIV_SAME``).
Setting = bytes | tuple[int | None, int, int | None]


def _civ_pairs(spec: str) -> frozenset[tuple[int, int | None]]:
    """``"28 16:46"``: hex command, optionally ``:`` and a hex sub."""
    parts = (item.partition(":") for item in spec.split())
    return frozenset(
        (int(command, 16), int(sub, 16) if sub else None) for command, _, sub in parts
    )


#: Yaesu CAT verbs a check may write: receive-side settings only.
YAESU_WRITE_ALLOW = frozenset("FA FB MD FT PC BS PR".split())

#: Yaesu verbs never sent by a check, not even as reads: transmit/PTT keys,
#: memory writes, keyer/CW, voice memory, scan, tuner-adjacent writes.
YAESU_NEVER = frozenset(
    "TX MX AC KY KM PS MW MC EX PB LM AM MT QI MA QR VM CH SC VX BI DT".split()
)

#: CI-V settings a check may write (after ``_CIV_SAME``): selected/unselected
#: frequency and mode, split on/off, VFO equalize/exchange, and the receive
#: levels AF, RF gain, SQL, NR, PBT, CW pitch, RF power, notch, NB. Mic gain,
#: compressor, break-in delay, monitor, VOX and anti-VOX stay off until a
#: check needs them; duplex (``0F 10..12``) and VFO/receiver select are off.
CIV_WRITE_ALLOW = _civ_pairs(
    "25:00 25:01 26:00 26:01 0F 07:A0 07:B0 07:B1 "
    "14:01 14:02 14:03 14:06 14:07 14:08 14:09 14:0A 14:0D 14:12"
)

#: Read and write forms of one CI-V setting: operating freq/mode (03/05,
#: 04/06) are the selected VFO's (25 00, 26 00); split off/on are values of
#: the one split setting read by a bare ``0F``.
_CIV_SAME = {
    (0x03, None): (0x25, 0x00),
    (0x05, None): (0x25, 0x00),
    (0x04, None): (0x26, 0x00),
    (0x06, None): (0x26, 0x00),
    (0x0F, 0x00): (0x0F, None),
    (0x0F, 0x01): (0x0F, None),
}

#: Writes that change settings other than their own, and the settings the
#: restore list must then hold: VFO equalize/exchange moves frequency and
#: mode of both VFOs; Yaesu band select moves frequency and mode of its
#: receiver (CAT keys match the longest prefix, so a raw ``BS1x`` takes the
#: stricter sub-receiver entry).
_BOTH_VFOS = ((0x25, 0x00), (0x25, 0x01), (0x26, 0x00), (0x26, 0x01))
_ALSO_RESTORE: dict[object, tuple[Any, ...]] = {
    (0x07, 0xA0): _BOTH_VFOS,
    (0x07, 0xB0): _BOTH_VFOS,
    (0x07, 0xB1): _BOTH_VFOS,
    b"BS": (b"FA", b"MD0"),
    b"BS0": (b"FA", b"MD0"),
    b"BS1": (b"FB", b"MD1"),
}

#: CI-V mode bytes with unattended transmit (auto reply, GPS beacon): DV and
#: DD (0x22, the IC-905/IC-9700 mode notes).
CIV_DIGITAL_VOICE = frozenset((bytes((Mode.DV,)), b"\x22"))

#: CI-V command bytes no probe read sends, whatever follows (checked after
#: unwrapping cmd29): send/set-only commands (send freq/mode 0x00/0x01, set
#: freq/mode 0x05/0x06, MEM select/write/clear 0x08/0x09/0x0B, scan 0x0E, CW
#: send 0x17) plus the power, PTT/tuner and VFO-selection families
#: (0x18/0x1C/0x07). 0x07 is not read-only on Icom radios: a bare ``07``
#: selects VFO mode, ``07 D0/D1`` select MAIN/SUB, ``07 A0/B0/B1`` exchange or
#: equalise VFOs. Shared with ``rigplane.probe.readlist``: one read rule.
CIV_READ_REFUSED = frozenset(
    (0x00, 0x01, 0x05, 0x06, 0x07, 0x08, 0x09, 0x0B, 0x0E, 0x17, 0x18, 0x1C)
)


def excluded_from_probes(body: bytes, *, include_private: bool) -> bool:
    """Whether a declared CI-V GET body is excluded from every probe read.

    The refused command bytes (their read forms included) are always out;
    the 0x1A 0x00 memory-contents pair, a (command, sub) pair because 0x1A
    carries legal scope/control reads, is out unless ``include_private``.
    The bare 0xFA/0xFB bytes are the radio's NAK/ACK replies, never requests.
    """
    if not body or body[0] in CIV_READ_REFUSED or body[0] in (0xFA, 0xFB):
        return True
    if body[0] == 0x1A and body[1:2] == b"\x00":
        return not include_private
    return False


def digital_voice_codes(
    protocol: str, modes: Sequence[str], mode_codes: Sequence[str] | None
) -> frozenset[bytes] | None:
    """Mode values a check must not write: CI-V DV/DD, Yaesu the profile's
    C4FM codes; ``None`` when a CAT profile declares no mode codes."""
    if protocol != "yaesu_cat":
        return CIV_DIGITAL_VOICE
    if mode_codes is None:
        return None
    pairs = zip(modes, mode_codes, strict=True)
    return frozenset(code.encode("ascii") for name, code in pairs if "C4FM" in name)


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

#: One template field: ``{name}`` or ``{name:09d}``.
_TEMPLATE_FIELD = re.compile(r"\{([^{}]*)\}")


@dataclass(frozen=True, slots=True)
class ProbeCheckStep:
    """One engine step. ``write``/``read`` name a profile command (a CAT
    write fills ``values`` into the profile's template; CI-V appends
    ``data_hex``); ``raw_write`` carries a literal CAT ``line`` or CI-V
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


def setting_of(protocol: str, body: bytes) -> Setting:
    """The setting a declared entry, template or wire body reads or writes."""
    if protocol == "yaesu_cat":
        return body.split(b"{")[0].removesuffix(b";").upper()
    inner = unwrap_receiver_prefix(body)
    rx = body[1] if inner is not body else None
    has_sub = inner[0] in (0x07, 0x0F, 0x25, 0x26) or command_carries_sub(inner[0])
    key = (inner[0], inner[1] if has_sub and len(inner) >= 2 else None)
    command, sub = _CIV_SAME.get(key, key)
    return rx, command, sub


def _restore_missing(setting: Setting, restored: Collection[Setting]) -> bool:
    """Whether the restore list lacks a setting the write changes."""
    if isinstance(setting, bytes):
        keys = [
            k for k in _ALSO_RESTORE if isinstance(k, bytes) and setting.startswith(k)
        ]
        if keys:
            return not set(_ALSO_RESTORE[max(keys, key=len)]) <= set(restored)
        return not any(isinstance(r, bytes) and setting.startswith(r) for r in restored)
    rx, command, sub = setting
    pairs = _ALSO_RESTORE.get((command, sub), ((command, sub),))
    return not {(rx, *pair) for pair in pairs} <= set(restored)


def restored_settings(
    restore: Sequence[ProbeRestoreItem],
    commands: Mapping[str, CommandSpec],
    protocol: str,
) -> frozenset[Setting]:
    """The settings a check's restore writes put back."""
    bodies = (
        declared_body(commands.get(item.write), protocol, "write") for item in restore
    )
    return frozenset(setting_of(protocol, body) for body in bodies if body)


def command_key(protocol: str, body: bytes) -> str | int:
    """The verb (CAT) or command byte (CI-V) a wire body addresses."""
    return (
        body[:2].decode("ascii", "replace").upper()
        if protocol == "yaesu_cat"
        else body[0]
    )


def policy_refusal(
    protocol: str,
    body: bytes,
    *,
    restored: Collection[Setting],
    voice_codes: Collection[bytes] | None,
    setting: Setting | None = None,
) -> str | None:
    """Why a write must not be sent, naming the verb; ``None`` when allowed.

    ``restored`` holds what the check's restore list puts back: the write's
    ``setting`` (default: of ``body``; a CAT profile write passes its
    template's) and every setting in ``_ALSO_RESTORE`` must be in it.
    ``voice_codes`` are the refused digital-voice mode values (``None``:
    unknown, so every mode write is refused).
    """
    if not body or protocol not in _PROTOCOLS:
        return "empty body" if not body else _NEED_PROTOCOL
    setting = setting_of(protocol, body) if setting is None else setting
    if protocol == "yaesu_cat":
        if not body.endswith(b";") or b";" in body[:-1]:
            return "a CAT line must be exactly one command ending in ';'"
        key = command_key(protocol, body)
        if key in YAESU_NEVER or key not in YAESU_WRITE_ALLOW:
            return f"CAT verb {key!r} is not on the allow-list"
        mode = body[3:4] if key == "MD" else b""
    else:
        inner = unwrap_receiver_prefix(body)
        if not inner or inner[0] == 0x29 or 0xFD in inner or b"\xfe\xfe" in inner:
            return "CI-V body must be one command without frame delimiters"
        assert not isinstance(setting, bytes)
        if setting[1:] not in CIV_WRITE_ALLOW:
            return f"CI-V 0x{inner[0]:02X} {inner[1:2].hex()} is not on the allow-list"
        mode = inner[1 + (inner[0] == 0x26) :][:1] if setting[1] == 0x26 else b""
    if mode and (voice_codes is None or mode in voice_codes):
        return f"mode {mode!r} is digital voice or unknown (may transmit unattended)"
    if _restore_missing(setting, restored):
        return f"write {body!r} needs a restore item for the setting it changes"
    return None


def check_send_allowed(
    protocol: str,
    body: bytes,
    *,
    restored: Collection[Setting],
    voice_codes: Collection[bytes] | None,
) -> None:
    """Refuse a write the engine must never send (``ValueError`` names it)."""
    why = policy_refusal(protocol, body, restored=restored, voice_codes=voice_codes)
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
    voice_codes: frozenset[bytes] | None
    restored: frozenset[Setting] = frozenset()

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

    def declared(self, where: str, name: str, part: str) -> bytes | None:
        try:
            return declared_body(self.commands.get(name), self.protocol, part)
        except UnicodeEncodeError:
            raise self.fail(f"{where} {name!r} template must be ASCII") from None

    def read_capable(self, where: str, name: str) -> None:
        """Only what the shipped read pass sends: a CI-V ``get_*`` entry outside
        the refused read families, or a concrete CAT read template off the
        never-list and off the private memory reads."""
        spec, why = self.commands.get(name), ""
        body = self.declared(where, name, "read")
        if body is None or (self.cat and b"{" in body):
            why = "declares no concrete read"
        elif self.cat and command_key(self.protocol, body) in YAESU_NEVER | {"MR"}:
            why = f"never-listed CAT verb {command_key(self.protocol, body)!r}"
        elif not self.cat and excluded_from_probes(body, include_private=False):
            why = f"never-listed CI-V read 0x{unwrap_receiver_prefix(body)[0]:02X}"
        elif isinstance(spec, CivCommandSpec) and not name.startswith("get_"):
            why = "is not a get_* read"
        if why:
            raise self.fail(f"{where} {name!r} {why}")

    def refuse(self, where: str, body: bytes, setting: Setting | None = None) -> None:
        why = policy_refusal(
            self.protocol,
            body,
            restored=self.restored,
            voice_codes=self.voice_codes,
            setting=setting,
        )
        if why:
            raise self.fail(f"{where}: {why}")

    def write_step(
        self, where: str, mapping: dict[str, Any], name: str
    ) -> ProbeCheckStep:
        template = self.declared(f"{where}.command", name, "write")
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
        own = setting_of(self.protocol, template) if self.cat else None
        self.refuse(f"{where}.command {name!r}", body, own)
        if self.cat:
            return ProbeCheckStep(
                "write", command=name, values=tuple(sorted(values.items()))
            )
        return ProbeCheckStep(
            "write", command=name, data_hex=values["data_hex"].lower()
        )

    def step(self, where: str, raw: object) -> ProbeCheckStep:
        raw_key = "line" if self.cat else "body_hex"
        keys = frozenset({"kind", "question", "command", "values", "line", "body_hex"})
        mapping = self.table(where, raw, keys)
        kind = mapping.get("kind")
        if kind not in (*_STEP_KEYS, "raw_write"):
            raise self.fail(
                f"{where}.kind must be one of {sorted((*_STEP_KEYS, 'raw_write'))}"
            )
        if extra := sorted(set(mapping) - {"kind", *_STEP_KEYS.get(kind, (raw_key,))}):
            raise self.fail(f"{where} must not carry {extra!r}")
        if kind == "ask":
            return ProbeCheckStep(
                kind, question=self.text(f"{where}.question", mapping.get("question"))
            )
        if kind == "raw_write":
            text = self.text(f"{where}.{raw_key}", mapping.get(raw_key))
            self.refuse(f"{where}.{raw_key}", self.wire(f"{where}.{raw_key}", text))
            if self.cat:
                return ProbeCheckStep(kind, line=text)
            return ProbeCheckStep(kind, body_hex=text.lower())
        name = self.text(f"{where}.command", mapping.get("command"))
        if kind == "write":
            return self.write_step(where, mapping, name)
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
        read = self.text(f"{where}.read", mapping.get("read"))
        self.read_capable(f"{where}.read", read)
        return ProbePrecondition(
            read, self.text(f"{where}.equals", mapping.get("equals"))
        )

    def validate(self, restore: tuple[ProbeRestoreItem, ...]) -> _Loader:
        """Each restore item reads and writes one setting the policy allows;
        returns the loader that judges writes against these restores."""
        writes = []
        for item in restore:
            where = f"restore {item.read!r} -> {item.write!r}"
            self.read_capable(where, item.read)
            write = self.declared(where, item.write, "write")
            read = self.declared(where, item.read, "parse")
            if write is None or read is None:
                raise self.fail(f"{where} declares no write or no parse")
            if setting_of(self.protocol, read) != setting_of(self.protocol, write):
                raise self.fail(f"{where} must read and write the same setting")
            writes.append((where, write))
        loader = replace(
            self, restored=restored_settings(restore, self.commands, self.protocol)
        )
        for where, write in writes:
            loader.refuse(where, write)
        return loader

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
        loader = self.validate(restore)
        steps = self.each(
            f"{where}.steps", mapping.get("steps", []), loader.step, nonempty=True
        )
        if not restore and any(step.kind in ("write", "raw_write") for step in steps):
            raise self.fail(f"{where} has write steps but no restore items")
        text = self.text
        return ProbeCheck(
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


def parse_probe_checks(
    filename: str,
    raw: object,
    *,
    commands: Mapping[str, CommandSpec],
    protocol_type: str,
    voice_codes: frozenset[bytes] | None,
) -> tuple[ProbeCheck, ...]:
    """Parse ``[probe]`` into typed checks; ``()`` when absent. Structure,
    unknown keys, non-ASCII customer text and write-policy violations fail
    here with the verb or key named."""
    if raw is None:
        return ()
    loader = _Loader(filename, commands, protocol_type, voice_codes)
    section = loader.table("[probe]", raw, frozenset({"checks"}))
    if section.get("checks") and protocol_type not in _PROTOCOLS:
        raise loader.fail(f"[probe]: {_NEED_PROTOCOL}")
    checks = loader.each("[probe].checks", section.get("checks", []), loader.check)
    ids = [check.id for check in checks]
    if repeated := next((name for name in ids if ids.count(name) > 1), None):
        raise loader.fail(f"[probe.checks] id {repeated!r} repeats a check id")
    return checks
