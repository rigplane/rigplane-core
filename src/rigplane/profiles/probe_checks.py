"""Typed ``[[probe.checks]]`` profile data and the closed write-check table (MOR-4464 B1).

A check names entries of a closed table of settings, and values, never bytes.
Frames are BUILT from the table (``build_frame``); ``parse_frame`` accepts only
what the table's encoders produce; ``sendable`` adds that a write's restore list
holds every setting it changes. ``judged_frame`` runs the three on every step at
load, and the write-check engine (B1b) runs the same function on the bytes it is
about to write. Per-radio facts come from the profile: each entry is compiled
against the profile's own templates, and a template of another shape is a load
error. What is not in the table (band select, duplex, VFO equalize or select,
transmit levels) is absent. The adversary is a wrong profile entry written by a
developer, never the radio or its owner.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, replace
from typing import Any, TypeVar

from rigplane.commands import RECEIVER_MAIN, RECEIVER_SUB
from rigplane.commands._frame import unwrap_receiver_prefix
from rigplane.commands.command_spec import CatCommandSpec, CivCommandSpec, CommandSpec
from rigplane.core.types import Mode

__all__ = [
    "TABLE",
    "Entry",
    "Parsed",
    "ProbeCheck",
    "ProbeCheckStep",
    "ProbePrecondition",
    "ProbeRestoreItem",
    "RadioFacts",
    "Table",
    "TableEntry",
    "build_frame",
    "compile_table",
    "judged_frame",
    "parse_frame",
    "parse_probe_checks",
    "sendable",
]

#: ``(entry id, receiver)``: what one restore item puts back.
RestoreKey = tuple[str, int | None]


@dataclass(frozen=True, slots=True)
class RadioFacts:
    """The profile facts the table compiles against."""

    protocol: str
    commands: Mapping[str, CommandSpec]
    modes: tuple[str, ...]
    mode_codes: tuple[str, ...] | None
    cmd29_routes: frozenset[tuple[int, int | None]]
    vfo_swap: tuple[int, ...] | None
    freq_ranges: tuple[tuple[int, int], ...]


# --- value codecs: fixed width; what decodes is the domain ---------------------------


@dataclass(frozen=True, slots=True)
class _Choice:
    options: tuple[tuple[object, bytes], ...]

    @property
    def width(self) -> int:
        return len(self.options[0][1])

    def encode(self, value: object) -> bytes | None:
        same = (b for v, b in self.options if (v, type(v)) == (value, type(value)))
        return next(same, None)

    def decode(self, data: bytes) -> object | None:
        return next((v for v, b in self.options if b == data), None)


@dataclass(frozen=True, slots=True)
class _Number:
    """An integer inside ``ranges``, as ASCII digits or BCD (``le``: low byte
    first)."""

    width: int
    kind: str  # "ascii" | "bcd" | "le"
    ranges: tuple[tuple[int, int], ...]

    def encode(self, value: object) -> bytes | None:
        if type(value) is not int or not any(a <= value <= b for a, b in self.ranges):
            return None
        if self.kind == "ascii":
            return f"{value:0{self.width}d}".encode("ascii")
        data = bytes.fromhex(f"{value:0{2 * self.width}d}")
        return data[::-1] if self.kind == "le" else data

    def decode(self, data: bytes) -> object | None:
        raw = data[::-1] if self.kind == "le" else data
        text = raw.decode("latin-1") if self.kind == "ascii" else raw.hex()
        number = int(text) if re.fullmatch("[0-9]+", text) else -1
        return number if any(a <= number <= b for a, b in self.ranges) else None


@dataclass(frozen=True, slots=True)
class _Fields:
    """A tuple of fixed-width parts (CI-V 26: mode, data flag, filter)."""

    parts: tuple[_Choice, ...]

    @property
    def width(self) -> int:
        return sum(part.width for part in self.parts)

    def encode(self, value: object) -> bytes | None:
        if not isinstance(value, tuple) or len(value) != len(self.parts):
            return None
        pieces = [
            part.encode(item) for part, item in zip(self.parts, value, strict=True)
        ]
        return (
            b"".join(p for p in pieces if p is not None) if None not in pieces else None
        )

    def decode(self, data: bytes) -> object | None:
        starts = [sum(p.width for p in self.parts[:i]) for i in range(len(self.parts))]
        cut = (
            p.decode(data[s : s + p.width])
            for p, s in zip(self.parts, starts, strict=True)
        )
        values = tuple(cut)
        return None if None in values else values


Codec = _Choice | _Number | _Fields


def _voice(name: str) -> bool:
    """Digital-voice modes may transmit unattended (auto reply, beacon)."""
    return name in ("DV", "DD") or "C4FM" in name


def _cat_modes(facts: RadioFacts) -> Codec:
    if facts.mode_codes is None:
        raise ValueError("the mode entry needs [modes].codes")
    codes = [code.encode("ascii") for code in facts.mode_codes]
    pairs = zip(facts.modes, codes, strict=True)
    return _Choice(tuple((n, c) for n, c in pairs if not _voice(n)))


def _civ_modes(facts: RadioFacts) -> _Choice:
    found = ((n, Mode.__members__.get(n.replace("-", "_"))) for n in facts.modes)
    return _Choice(tuple((n, bytes((m,))) for n, m in found if m and not _voice(n)))


_DATA = _Choice(((0, b"\x00"), (1, b"\x01")))
_FILTER = _Choice(tuple((i, bytes((i,))) for i in (1, 2, 3)))


def _mode_data_filter(facts: RadioFacts) -> Codec:
    return _Fields((_civ_modes(facts), _DATA, _FILTER))


def _mode_filter(facts: RadioFacts) -> Codec:
    return _Fields((_civ_modes(facts), _FILTER))


# --- the closed table ------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Form:
    """One spelling of an entry: ``(profile command, template)`` per side
    (CAT text with ``{}`` fields, CI-V hex; ``vfo_swap`` is ``[vfo]`` swap)."""

    read: tuple[str, str] | None
    write: tuple[str, str] | None
    codec: Codec | Callable[[RadioFacts], Codec] | None = None


@dataclass(frozen=True, slots=True)
class TableEntry:
    """A setting: its forms in order of preference, what a write changes
    (default: itself) and whether a ``29 <rx>`` wrapper may carry it."""

    id: str
    forms: tuple[_Form, ...]
    changes: tuple[str, ...] = ()
    wrapper_ok: bool = False


def _cat(name: str, key: str, value: str | None, codec: Any = None) -> tuple[_Form]:
    write = None if value is None else (f"set_{name}", key + value)
    return (_Form((f"get_{name}", key + ";"), write, codec),)


def _civ(name: str, key: str, codec: Any) -> _Form:
    return _Form((f"get_{name}", key), (f"set_{name}", key), codec)


def _freq_cat(facts: RadioFacts) -> Codec:
    """Frequencies inside the profile's own ``[freq_ranges]``."""
    return _Number(9, "ascii", facts.freq_ranges)


def _freq_civ(facts: RadioFacts) -> Codec:
    return _Number(5, "le", facts.freq_ranges)


_LEVEL = _Number(2, "bcd", ((0, 255),))

#: Only what the first checks need: the B2 FT-991A checks (power, cross-band
#: split, speech processor, IF witness) and a synthetic CI-V check (VFO
#: exchange, RF power, a receiver-wrapped AF level).
TABLE: dict[str, tuple[TableEntry, ...]] = {
    "yaesu_cat": (
        TableEntry("freq_a", _cat("freq", "FA", "{:09d};", _freq_cat)),
        TableEntry("freq_b", _cat("freq_sub", "FB", "{:09d};", _freq_cat)),
        TableEntry("mode_main", _cat("mode", "MD0", "{};", _cat_modes)),
        TableEntry(
            "split", _cat("split", "FT", "{};", _Choice((("off", b"2"), ("on", b"3"))))
        ),
        TableEntry(
            "power", _cat("power", "PC", "{:03d};", _Number(3, "ascii", ((0, 999),)))
        ),
        TableEntry(
            "speech_processor",
            _cat(
                "processor",
                "PR0",
                "{};",
                _Choice(tuple((v, v.encode()) for v in "012")),
            ),
        ),
        TableEntry("info", _cat("if_status", "IF", None)),
    ),
    "civ": (
        TableEntry(
            "freq_selected",
            (
                _civ("selected_freq", "25 00", _freq_civ),
                _Form(("get_freq", "03"), ("set_freq", "05"), _freq_civ),
            ),
        ),
        TableEntry("freq_unselected", (_civ("unselected_freq", "25 01", _freq_civ),)),
        TableEntry(
            "mode_selected",
            (
                _civ("selected_mode", "26 00", _mode_data_filter),
                _Form(("get_mode", "04"), ("set_mode", "06"), _mode_filter),
            ),
        ),
        TableEntry(
            "mode_unselected", (_civ("unselected_mode", "26 01", _mode_data_filter),)
        ),
        TableEntry(
            "vfo_exchange",
            (_Form(None, ("vfo_swap", "07 B0"), _Fields(())),),
            ("freq_selected", "freq_unselected", "mode_selected", "mode_unselected"),
        ),
        TableEntry("rf_power", (_civ("rf_power", "14 0A", _LEVEL),)),
        TableEntry("af_level", (_civ("af_level", "14 01", _LEVEL),), wrapper_ok=True),
    ),
}


@dataclass(frozen=True, slots=True)
class Entry:
    """An entry compiled for one profile: read and write keys (CAT text before
    the value, CI-V command bytes), ``None`` where absent."""

    id: str
    read: bytes | None
    write: bytes | None
    codec: Codec | None
    changes: tuple[str, ...]
    wrapper_ok: bool


@dataclass(frozen=True, slots=True)
class Table:
    protocol: str
    entries: tuple[Entry, ...]

    def entry(self, entry_id: str) -> Entry:
        found = next((e for e in self.entries if e.id == entry_id), None)
        if found is None:
            raise KeyError(f"entry {entry_id!r} is not in the compiled table")
        return found


@dataclass(frozen=True, slots=True)
class Parsed:
    entry: Entry
    form: str  # "read" | "write"
    receiver: int | None
    value: object | None


def _declared(facts: RadioFacts, name: str, write: bool) -> str | None:
    """The profile's template for one side, in the table's notation."""
    spec = facts.commands.get(name)
    if name == "vfo_swap" and facts.vfo_swap:
        return "07 " + bytes(facts.vfo_swap).hex(" ").upper()
    if isinstance(spec, CivCommandSpec) and facts.protocol == "civ":
        return bytes(spec.bytes).hex(" ").upper()
    text = getattr(spec, "write" if write else "read", None)
    if isinstance(spec, CatCommandSpec) and facts.protocol == "yaesu_cat" and text:
        return re.sub(r"\{[A-Za-z_]\w*", "{", text)
    return None


def _compile(spec: TableEntry, facts: RadioFacts) -> Entry | None:
    for form in spec.forms:
        sides = ((form.read, False), (form.write, True))
        found = [(s, _declared(facts, s[0], w)) for s, w in sides if s is not None]
        if any(declared is None for _, declared in found):
            continue
        if wrong := next(((s, d) for s, d in found if d != s[1]), None):
            (name, need), declared = wrong
            raise ValueError(
                f"{name} is {declared!r}; entry {spec.id!r} needs {need!r}"
            )
        cat = facts.protocol == "yaesu_cat"
        keys = [
            None
            if s is None
            else s[1].split("{")[0].removesuffix(";").encode()
            if cat
            else bytes.fromhex(s[1])
            for s in (form.read, form.write)
        ]
        key = keys[0] or keys[1] or b""
        codec = form.codec(facts) if callable(form.codec) else form.codec
        routed = (key[0], key[1] if len(key) > 1 else None) in facts.cmd29_routes
        changes = spec.changes or (spec.id,)
        return Entry(
            spec.id, keys[0], keys[1], codec, changes, spec.wrapper_ok and routed
        )
    return None


def compile_table(facts: RadioFacts, ids: Collection[str] | None = None) -> Table:
    """The table for one profile: ``ids`` must all be available; without
    ``ids``, entries the profile does not declare are left out."""
    specs = {spec.id: spec for spec in TABLE.get(facts.protocol, ())}
    if unknown := sorted(set(ids or ()) - set(specs)):
        raise ValueError(f"entry {unknown[0]!r} is not in the table")
    entries = []
    for entry_id in specs if ids is None else sorted(ids, key=list(specs).index):
        entry = _compile(specs[entry_id], facts)
        if entry is None and ids is not None:
            raise ValueError(f"entry {entry_id!r} is not available on this radio")
        entries += [] if entry is None else [entry]
    return Table(facts.protocol, tuple(entries))


# --- the one parser, the one predicate, the one builder ------------------------------


def parse_frame(table: Table, body: bytes) -> Parsed | None:
    """The entry and form ``body`` is, or ``None``. CAT: one whole line whose
    only ``;`` ends it. CI-V: at most one ``29 <rx>`` wrapper, a key, then a
    value the codec consumes entirely; no value is the read form."""
    receiver, inner = None, body
    if table.protocol == "yaesu_cat":
        if not re.fullmatch(rb"[A-Z]{2}[^;]*;", body):
            return None
        inner = body[:-1]
    elif (unwrapped := unwrap_receiver_prefix(body)) is not body:
        receiver, inner = body[1], unwrapped
    found = []
    for entry in table.entries:
        if receiver is not None and not entry.wrapper_ok:
            continue
        if inner == entry.read:
            found.append(Parsed(entry, "read", receiver, None))
        if entry.write is not None and entry.codec and inner.startswith(entry.write):
            rest = inner[len(entry.write) :]
            value = entry.codec.decode(rest) if len(rest) == entry.codec.width else None
            if value is not None:
                found.append(Parsed(entry, "write", receiver, value))
    return found[0] if len(found) == 1 else None


def sendable(parsed: Parsed, restored: Collection[RestoreKey]) -> bool:
    """A read always; a write when the restore list holds every setting it
    changes on its receiver (``parse_frame`` already enforced the domain)."""
    if parsed.form == "read":
        return True
    return {(c, parsed.receiver) for c in parsed.entry.changes} <= set(restored)


def build_frame(
    table: Table, entry_id: str, form: str, value: object, receiver: int | None
) -> bytes:
    """Encode one entry's read or write form (``ValueError`` outside it)."""
    entry = table.entry(entry_id)
    key = {"read": entry.read, "write": entry.write}.get(form)
    if key is None:
        raise ValueError(f"{entry_id!r} has no {form} form")
    data = b"" if form == "read" else entry.codec.encode(value) if entry.codec else None
    if data is None:
        raise ValueError(f"value {value!r} is outside the domain of {entry_id!r}")
    if table.protocol == "yaesu_cat":
        if receiver is not None:
            raise ValueError("a receiver is CI-V only")
        return key + data + b";"
    return (b"" if receiver is None else bytes((0x29, receiver))) + key + data


def judged_frame(
    table: Table,
    entry_id: str,
    form: str,
    value: object,
    receiver: int | None,
    restored: Collection[RestoreKey],
) -> bytes:
    """Build a frame and judge those exact bytes: the only judging path."""
    body = build_frame(table, entry_id, form, value, receiver)
    parsed = parse_frame(table, body)
    if parsed is None or (parsed.entry.id, parsed.form) != (entry_id, form):
        raise ValueError(f"{body!r} does not parse as {entry_id!r} (receiver routed?)")
    if not sendable(parsed, restored):
        need = sorted(c for c in parsed.entry.changes if (c, receiver) not in restored)
        raise ValueError(f"write {entry_id!r} needs restore items for {need!r}")
    return body


# --- check data --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ProbeCheckStep:
    """``write``/``read`` name an entry (a write with its value) and carry the
    judged ``frame``; ``ask`` carries the question put to the person."""

    kind: str
    entry: str = ""
    value: object = None
    receiver: int | None = None
    question: str = ""
    frame: bytes = b""


@dataclass(frozen=True, slots=True)
class ProbeRestoreItem:
    """An entry read before the first write and written back afterwards;
    ``rehearse = False`` skips the no-op rehearsal."""

    entry: str
    receiver: int | None = None
    rehearse: bool = True


@dataclass(frozen=True, slots=True)
class ProbePrecondition:
    """A read before the first write; ``equals`` is the reply that allows it."""

    entry: str
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

    @property
    def restored(self) -> frozenset[RestoreKey]:
        return frozenset((item.entry, item.receiver) for item in self.restore)


_T = TypeVar("_T")
_TEXT = ("title", "explain_what", "explain_after")
_CHECK_KEYS = {"id", *_TEXT, "warnings", "preconditions", "witness", "steps", "restore"}
_KEYS = {
    "ask": {"kind", "question"},
    "read": {"kind", "entry", "receiver"},
    "write": {"kind", "entry", "value", "receiver"},
    "restore": {"entry", "receiver", "rehearse"},
    "precondition": {"entry", "equals"},
}
_RECEIVERS = {"main": RECEIVER_MAIN, "sub": RECEIVER_SUB}


@dataclass(frozen=True, slots=True)
class _Loader:
    """The profile being loaded: the context every parse helper shares."""

    filename: str
    facts: RadioFacts
    table: Table = Table("", ())
    restored: frozenset[RestoreKey] = frozenset()

    def fail(self, message: str) -> Exception:
        from rigplane.profiles.rig_loader import RigLoadError

        error: Exception = RigLoadError(f"{self.filename}: {message}")
        return error

    def map(self, where: str, raw: object, keys: Collection[str]) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise self.fail(f"{where} must be a table")
        if unknown := sorted(set(raw) - set(keys)):
            raise self.fail(f"{where} unknown key(s): {unknown!r}")
        return dict(raw)

    def text(self, where: str, value: object) -> str:
        if not isinstance(value, str) or not value or not value.isascii():
            raise self.fail(f"{where} must be a non-empty ASCII string")
        return value

    def each(
        self, where: str, raw: object, parse: Callable[[str, Any], _T]
    ) -> tuple[_T, ...]:
        if not isinstance(raw, list):
            raise self.fail(f"{where} must be an array")
        return tuple(parse(f"{where}[{i}]", item) for i, item in enumerate(raw))

    def named(
        self, where: str, raw: object, kind: str
    ) -> tuple[dict[str, Any], str, int | None]:
        """A table of ``kind`` naming an entry read at load: (table, entry, receiver)."""
        mapping = self.map(where, raw, _KEYS[kind])
        entry = self.text(f"{where}.entry", mapping.get("entry"))
        receiver = mapping.get("receiver")
        if receiver is not None and receiver not in _RECEIVERS:
            raise self.fail(f"{where}.receiver must be 'main' or 'sub'")
        return mapping, entry, None if receiver is None else _RECEIVERS[receiver]

    def judge(
        self,
        where: str,
        entry: str,
        form: str = "read",
        value: object = None,
        receiver: int | None = None,
    ) -> bytes:
        try:
            if form == "restore" and self.table.entry(entry).write is None:
                raise ValueError(f"restore entry {entry!r} has no write form")
            form = "read" if form == "restore" else form
            return judged_frame(self.table, entry, form, value, receiver, self.restored)
        except (KeyError, ValueError) as error:
            raise self.fail(f"{where}: {error}") from None

    def step(self, where: str, raw: object) -> ProbeCheckStep:
        kind = raw.get("kind") if isinstance(raw, dict) else None
        if kind not in ("ask", "read", "write"):
            raise self.fail(f"{where}.kind must be one of ['ask', 'read', 'write']")
        if kind == "ask":
            question = self.map(where, raw, _KEYS[kind]).get("question")
            return ProbeCheckStep(
                kind, question=self.text(f"{where}.question", question)
            )
        mapping, entry, receiver = self.named(where, raw, kind)
        value = mapping.get("value", () if kind == "write" else None)
        value = tuple(value) if isinstance(value, list) else value
        frame = self.judge(where, entry, kind, value, receiver)
        return ProbeCheckStep(kind, entry, value, receiver, frame=frame)

    def restore(self, where: str, raw: object) -> ProbeRestoreItem:
        mapping, entry, receiver = self.named(where, raw, "restore")
        if not isinstance(rehearse := mapping.get("rehearse", True), bool):
            raise self.fail(f"{where}.rehearse must be a boolean")
        self.judge(where, entry, "restore", None, receiver)
        return ProbeRestoreItem(entry, receiver, rehearse)

    def precondition(self, where: str, raw: object) -> ProbePrecondition:
        mapping, entry, _ = self.named(where, raw, "precondition")
        self.judge(where, entry)
        return ProbePrecondition(
            entry, self.text(f"{where}.equals", mapping.get("equals"))
        )

    def witness(self, where: str, raw: object) -> str:
        self.judge(where, entry := self.text(where, raw))
        return entry

    def check(self, where: str, raw: object) -> ProbeCheck:
        mapping = self.map(where, raw, _CHECK_KEYS)
        slug = mapping.get("id")
        if not isinstance(slug, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug):
            raise self.fail(f"{where}.id must be a slug like 'power-2m'")
        named = [*mapping.get("witness", [])]
        for key in ("steps", "restore", "preconditions"):
            named += [
                i.get("entry") for i in mapping.get(key, []) if isinstance(i, dict)
            ]
        try:
            table = compile_table(self.facts, {str(n) for n in named if n is not None})
        except UnicodeEncodeError:
            raise self.fail(
                f"{where}: [modes].codes must be ASCII for probe checks"
            ) from None
        except ValueError as error:
            raise self.fail(f"{where}: {error}") from None
        loader = replace(self, table=table)
        restore = loader.each(
            f"{where}.restore", mapping.get("restore", []), loader.restore
        )
        loader = replace(
            loader, restored=frozenset((r.entry, r.receiver) for r in restore)
        )
        steps = loader.each(f"{where}.steps", mapping.get("steps", []), loader.step)
        if not steps:
            raise self.fail(f"{where}.steps must be a non-empty array")
        text = {key: self.text(f"{where}.{key}", mapping.get(key)) for key in _TEXT}
        return ProbeCheck(
            slug,
            text["title"],
            text["explain_what"],
            text["explain_after"],
            warnings=self.each(
                f"{where}.warnings", mapping.get("warnings", []), self.text
            ),
            preconditions=loader.each(
                f"{where}.preconditions",
                mapping.get("preconditions", []),
                loader.precondition,
            ),
            witness=loader.each(
                f"{where}.witness", mapping.get("witness", []), loader.witness
            ),
            steps=steps,
            restore=restore,
        )


def parse_probe_checks(
    filename: str, raw: object, *, facts: RadioFacts
) -> tuple[ProbeCheck, ...]:
    """Parse ``[probe]`` into typed checks; ``()`` when absent. The table is
    compiled only here, so a profile without checks never meets its rules."""
    if raw is None:
        return ()
    loader = _Loader(filename, facts)
    section = loader.map("[probe]", raw, {"checks"})
    if not section.get("checks"):
        return ()
    if facts.protocol not in TABLE:
        raise loader.fail("[probe]: probe checks need USB CI-V or Yaesu CAT")
    checks = loader.each("[probe].checks", section["checks"], loader.check)
    ids = [check.id for check in checks]
    if repeated := next((name for name in ids if ids.count(name) > 1), None):
        raise loader.fail(f"[probe.checks] id {repeated!r} repeats a check id")
    return checks
