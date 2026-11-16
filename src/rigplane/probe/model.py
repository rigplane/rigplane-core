"""``rigplane-probe-v1`` file model: typed contract with a validating parser.

The probe file is a paired, classified table: one row per read the run
sent, the radio's exact reply bytes, and a convenience class per row.
Raw bytes are always kept; the class never replaces them. Reads the
run deliberately did not send are listed under ``skipped`` with a
reason from a closed enum, so a silent omission is impossible.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

__all__ = [
    "SCHEMA",
    "OUTCOMES",
    "EXPECTED",
    "SKIP_REASONS",
    "TERMINATION_STATUSES",
    "MODES",
    "CHECK_STATUSES",
    "ToolInfo",
    "IdentityReport",
    "RadioInfo",
    "CommandRow",
    "SkippedRead",
    "TerminationInfo",
    "UnsolicitedRow",
    "Snapshot",
    "CheckStepRow",
    "CheckAnswerRow",
    "CheckWitness",
    "CheckRecord",
    "ProbeFile",
]

#: The one schema this tool reads and writes.
SCHEMA = "rigplane-probe-v1"

#: Major version this tool understands; any other major is refused.
SCHEMA_MAJOR = 1

#: Every outcome class both protocols share.
OUTCOMES = frozenset(
    {
        "data",
        "refused",
        "ack_only",
        "silent",
        "malformed",
        "unsolicited",
        "transport_error",
    }
)

#: What the profile declares about a sent read: the profile lists the
#: read (``present``) or marks it absent and the run borrowed the bytes
#: from sibling profiles (``absent``). Deliberately unknown calibration
#: requests carry ``absent``: the profile declares nothing about them.
EXPECTED = frozenset({"present", "absent"})

#: Why a declared read was not sent. ``unsafe_family`` is a refused or
#: action-shaped command family; ``private`` is stored memory contents
#: without ``--include-private``; ``response_code`` is the radio's own
#: NAK/ACK reply bytes, never requests; ``no_unambiguous_bytes`` is a
#: declared-absent read no sibling profile lends unanimous bytes for;
#: ``run_aborted`` is a read the run never attempted because an earlier
#: silence or transport break stopped it.
SKIP_REASONS = frozenset(
    {
        "unsafe_family",
        "private",
        "response_code",
        "no_unambiguous_bytes",
        "run_aborted",
    }
)

#: How a run ended. ``completed`` sent every planned read; ``silence_stop``
#: is five unanswered reads in a row; ``transport_error`` is a broken
#: serial link; ``cancelled`` is a signal or Ctrl-C during a write check
#: (its restore ran); ``restore_failed`` is a write check whose settings
#: could not be put back or verified (exit code 4). The parser refuses any
#: other status.
TERMINATION_STATUSES = frozenset(
    {"completed", "silence_stop", "transport_error", "cancelled", "restore_failed"}
)

#: ``read+checks``: the read pass ran and write checks were requested.
MODES = frozenset({"read", "read+checks"})

#: A check ``ran`` (wrote), was ``refused`` before any write (precondition,
#: unreadable original, original the check could not put back) or was
#: ``declined`` by the person.
CHECK_STATUSES = frozenset({"ran", "refused", "declined"})


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """The tool that recorded the file."""

    rigplane_version: str
    python: str
    os: str
    created_utc: str


@dataclass(frozen=True, slots=True)
class IdentityReport:
    """What the radio said it is: raw reply and extracted id."""

    raw: str
    parsed: str


@dataclass(frozen=True, slots=True)
class RadioInfo:
    """Which radio the run talked to.

    The tool records no port path, host, or user here — only profile,
    protocol, port kind, baud, and the radio's identity answer.
    Free text typed in ``--label`` is saved verbatim elsewhere.
    """

    profile: str
    profile_sha256: str
    protocol: str
    port_kind: str
    baud: int
    identity_reported: IdentityReport
    identity_matches_profile: bool


@dataclass(frozen=True, slots=True)
class CommandRow:
    """One sent read and what came back in its slot.

    ``outcome`` is the convenience class; the JSON key is ``class``.
    ``reply_hex``/``reply_text`` are ``None`` when nothing arrived.
    ``expected`` is what the profile declares: ``present`` for a listed
    read, ``absent`` for a declared-absent read sent with borrowed bytes
    (borrowed rows say so in ``note``).
    """

    name: str
    template: str
    request_hex: str
    request_text: str
    reply_hex: str | None
    reply_text: str | None
    outcome: str
    latency_ms: float
    expected: str
    note: str = ""


@dataclass(frozen=True, slots=True)
class SkippedRead:
    """One declared read the run deliberately did not send.

    ``request_hex`` is the withheld bytes, or ``None`` when no single
    bytes exist (no sibling lends unanimous bytes, or several expanded
    lines were withheld). ``reason`` is one of :data:`SKIP_REASONS`;
    ``detail`` is one short human sentence.
    """

    name: str
    request_hex: str | None
    reason: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class UnsolicitedRow:
    """One radio push that arrived outside any answer slot."""

    t_ms: float
    hex: str
    text: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Profile state reads before and after the pass."""

    before: dict[str, str | None]
    after: dict[str, str | None]
    changed: list[str]


@dataclass(frozen=True, slots=True)
class TerminationInfo:
    """How the run ended: one of :data:`TERMINATION_STATUSES` plus why.

    ``completed`` sent every planned read; ``silence_stop`` is five
    unanswered reads in a row; ``transport_error`` is a broken serial
    link, with the phase and the error in ``detail``.
    """

    status: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CheckStepRow:
    """One check frame on the wire: ``kind`` is ``read`` or ``write``,
    ``entry`` the table setting, ``value`` its hand-readable value (``None``
    when the reply did not decode), ``outcome`` one of :data:`OUTCOMES`
    (JSON key ``class``; a silent Yaesu write is the accepted one)."""

    kind: str
    entry: str
    request_hex: str
    reply_hex: str | None
    outcome: str
    value: str | None = None


@dataclass(frozen=True, slots=True)
class CheckAnswerRow:
    """One question put to the person: the answer, or skipped (timeout)."""

    question: str
    answer: str | None
    skipped: bool


@dataclass(frozen=True, slots=True)
class CheckWitness:
    """Witness reads before the check and after its restore."""

    before: dict[str, str | None]
    after: dict[str, str | None]
    changed: list[str]


@dataclass(frozen=True, slots=True)
class CheckRecord:
    """One write check: what it sent and read, the originals it put back
    (hand-settable text), and whether the restore was written (``restored``)
    and read back equal with an unchanged witness (``verified``)."""

    id: str
    title: str
    status: str
    steps: tuple[CheckStepRow, ...] = ()
    answers: tuple[CheckAnswerRow, ...] = ()
    originals: dict[str, str] = field(default_factory=dict)
    witness: CheckWitness = field(default_factory=lambda: CheckWitness({}, {}, []))
    restored: bool = True
    verified: bool = True
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeFile:
    """One ``rigplane-probe-v1`` document."""

    tool: ToolInfo
    mode: str
    radio: RadioInfo
    private_reads_included: bool
    label: str
    commands: tuple[CommandRow, ...] = ()
    skipped: tuple[SkippedRead, ...] = ()
    unsolicited: tuple[UnsolicitedRow, ...] = ()
    snapshot: Snapshot = field(default_factory=lambda: Snapshot({}, {}, []))
    termination: TerminationInfo = field(
        default_factory=lambda: TerminationInfo("completed")
    )
    checks: tuple[CheckRecord, ...] = ()
    schema: str = SCHEMA

    def to_json(self) -> str:
        """Pretty-printed JSON, one command block after another."""
        return json.dumps(_to_plain(self), indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ProbeFile:
        """Parse and validate; unknown major versions and bad fields refuse."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a probe file: invalid JSON ({error})") from error
        return _from_plain(document)


def _to_plain(file: ProbeFile) -> dict[str, Any]:
    return {
        "schema": file.schema,
        "tool": {
            "rigplane_version": file.tool.rigplane_version,
            "python": file.tool.python,
            "os": file.tool.os,
            "created_utc": file.tool.created_utc,
        },
        "mode": file.mode,
        "radio": {
            "profile": file.radio.profile,
            "profile_sha256": file.radio.profile_sha256,
            "protocol": file.radio.protocol,
            "port_kind": file.radio.port_kind,
            "baud": file.radio.baud,
            "identity_reported": {
                "raw": file.radio.identity_reported.raw,
                "parsed": file.radio.identity_reported.parsed,
            },
            "identity_matches_profile": file.radio.identity_matches_profile,
        },
        "private_reads_included": file.private_reads_included,
        "label": file.label,
        "commands": [
            {
                "name": row.name,
                "template": row.template,
                "request_hex": row.request_hex,
                "request_text": row.request_text,
                "reply_hex": row.reply_hex,
                "reply_text": row.reply_text,
                "class": row.outcome,
                "latency_ms": row.latency_ms,
                "expected": row.expected,
                "note": row.note,
            }
            for row in file.commands
        ],
        "skipped": [
            {
                "name": row.name,
                "request_hex": row.request_hex,
                "reason": row.reason,
                "detail": row.detail,
            }
            for row in file.skipped
        ],
        "unsolicited": [
            {"t_ms": row.t_ms, "hex": row.hex, "text": row.text}
            for row in file.unsolicited
        ],
        "snapshot": {
            "before": dict(file.snapshot.before),
            "after": dict(file.snapshot.after),
            "changed": list(file.snapshot.changed),
        },
        "termination": {
            "status": file.termination.status,
            "detail": file.termination.detail,
        },
        "checks": [_check_to_plain(record) for record in file.checks],
    }


def _check_to_plain(record: CheckRecord) -> dict[str, Any]:
    plain = asdict(record)
    for step in plain["steps"]:
        step["class"] = step.pop("outcome")
    return plain


def _need_str(document: Any, path: str) -> str:
    if not isinstance(document, str):
        raise ValueError(f"field {path} must be a string")
    return document


def _need_bool(document: Any, path: str) -> bool:
    if not isinstance(document, bool):
        raise ValueError(f"field {path} must be a boolean")
    return document


def _need_float(document: Any, path: str) -> float:
    if isinstance(document, bool) or not isinstance(document, (int, float)):
        raise ValueError(f"field {path} must be a number")
    return float(document)


def _need_hex(document: Any, path: str) -> str:
    value = _need_str(document, path)
    try:
        bytes.fromhex(value)
    except ValueError as error:
        raise ValueError(f"field {path} must be hex") from error
    return value


def _need_mapping(document: Any, path: str) -> dict[str, Any]:
    if not isinstance(document, dict):
        raise ValueError(f"field {path} must be an object")
    return document


def _missing(path: str) -> ValueError:
    return ValueError(f"missing field {path}")


def _check_schema(schema: Any) -> str:
    if not isinstance(schema, str):
        raise ValueError("field schema must be a string")
    head, sep, tail = schema.rpartition("-v")
    major: int | None = int(tail) if sep and tail.isdigit() else None
    if head != "rigplane-probe" or major != SCHEMA_MAJOR:
        raise ValueError(f"unsupported probe schema {schema!r}")
    return schema


def _tool_info(document: Any) -> ToolInfo:
    mapping = _need_mapping(document, "tool")
    for key in ("rigplane_version", "python", "os", "created_utc"):
        if key not in mapping:
            raise _missing(f"tool.{key}")
    return ToolInfo(
        rigplane_version=_need_str(
            mapping["rigplane_version"], "tool.rigplane_version"
        ),
        python=_need_str(mapping["python"], "tool.python"),
        os=_need_str(mapping["os"], "tool.os"),
        created_utc=_need_str(mapping["created_utc"], "tool.created_utc"),
    )


def _radio_info(document: Any) -> RadioInfo:
    mapping = _need_mapping(document, "radio")
    for key in (
        "profile",
        "profile_sha256",
        "protocol",
        "port_kind",
        "baud",
        "identity_reported",
        "identity_matches_profile",
    ):
        if key not in mapping:
            raise _missing(f"radio.{key}")
    identity = _need_mapping(mapping["identity_reported"], "radio.identity_reported")
    for key in ("raw", "parsed"):
        if key not in identity:
            raise _missing(f"radio.identity_reported.{key}")
    baud = mapping["baud"]
    if isinstance(baud, bool) or not isinstance(baud, int):
        raise ValueError("field radio.baud must be an integer")
    return RadioInfo(
        profile=_need_str(mapping["profile"], "radio.profile"),
        profile_sha256=_need_str(mapping["profile_sha256"], "radio.profile_sha256"),
        protocol=_need_str(mapping["protocol"], "radio.protocol"),
        port_kind=_need_str(mapping["port_kind"], "radio.port_kind"),
        baud=baud,
        identity_reported=IdentityReport(
            raw=_need_str(identity["raw"], "radio.identity_reported.raw"),
            parsed=_need_str(identity["parsed"], "radio.identity_reported.parsed"),
        ),
        identity_matches_profile=_need_bool(
            mapping["identity_matches_profile"], "radio.identity_matches_profile"
        ),
    )


def _command_row(document: Any, index: int) -> CommandRow:
    mapping = _need_mapping(document, f"commands[{index}]")
    for key in (
        "name",
        "template",
        "request_hex",
        "request_text",
        "reply_hex",
        "reply_text",
        "class",
        "latency_ms",
        "expected",
        "note",
    ):
        if key not in mapping:
            raise _missing(f"commands[{index}].{key}")
    outcome = _need_str(mapping["class"], f"commands[{index}].class")
    if outcome not in OUTCOMES:
        raise ValueError(
            f"field commands[{index}].class must be one of {sorted(OUTCOMES)}"
        )
    expected = _need_str(mapping["expected"], f"commands[{index}].expected")
    if expected not in EXPECTED:
        raise ValueError(
            f"field commands[{index}].expected must be one of {sorted(EXPECTED)}"
        )
    reply_hex = mapping["reply_hex"]
    reply_text = mapping["reply_text"]
    if reply_hex is not None:
        _need_hex(reply_hex, f"commands[{index}].reply_hex")
    if reply_text is not None and not isinstance(reply_text, str):
        raise ValueError(f"field commands[{index}].reply_text must be a string or null")
    return CommandRow(
        name=_need_str(mapping["name"], f"commands[{index}].name"),
        template=_need_str(mapping["template"], f"commands[{index}].template"),
        request_hex=_need_hex(mapping["request_hex"], f"commands[{index}].request_hex"),
        request_text=_need_str(
            mapping["request_text"], f"commands[{index}].request_text"
        ),
        reply_hex=reply_hex,
        reply_text=reply_text,
        outcome=outcome,
        latency_ms=_need_float(mapping["latency_ms"], f"commands[{index}].latency_ms"),
        expected=expected,
        note=_need_str(mapping["note"], f"commands[{index}].note"),
    )


def _skipped_row(document: Any, index: int) -> SkippedRead:
    mapping = _need_mapping(document, f"skipped[{index}]")
    for key in ("name", "request_hex", "reason", "detail"):
        if key not in mapping:
            raise _missing(f"skipped[{index}].{key}")
    reason = _need_str(mapping["reason"], f"skipped[{index}].reason")
    if reason not in SKIP_REASONS:
        raise ValueError(
            f"field skipped[{index}].reason must be one of {sorted(SKIP_REASONS)}"
        )
    request_hex = mapping["request_hex"]
    if request_hex is not None:
        _need_hex(request_hex, f"skipped[{index}].request_hex")
    return SkippedRead(
        name=_need_str(mapping["name"], f"skipped[{index}].name"),
        request_hex=request_hex,
        reason=reason,
        detail=_need_str(mapping["detail"], f"skipped[{index}].detail"),
    )


def _termination_info(document: Any) -> TerminationInfo:
    """The run's end state; files predating the key count as completed."""
    if document is None:
        return TerminationInfo("completed")
    mapping = _need_mapping(document, "termination")
    for key in ("status", "detail"):
        if key not in mapping:
            raise _missing(f"termination.{key}")
    status = _need_str(mapping["status"], "termination.status")
    if status not in TERMINATION_STATUSES:
        raise ValueError(
            f"field termination.status must be one of {sorted(TERMINATION_STATUSES)}"
        )
    return TerminationInfo(
        status=status, detail=_need_str(mapping["detail"], "termination.detail")
    )


def _unsolicited_row(document: Any, index: int) -> UnsolicitedRow:
    mapping = _need_mapping(document, f"unsolicited[{index}]")
    for key in ("t_ms", "hex", "text"):
        if key not in mapping:
            raise _missing(f"unsolicited[{index}].{key}")
    return UnsolicitedRow(
        t_ms=_need_float(mapping["t_ms"], f"unsolicited[{index}].t_ms"),
        hex=_need_hex(mapping["hex"], f"unsolicited[{index}].hex"),
        text=_need_str(mapping["text"], f"unsolicited[{index}].text"),
    )


def _from_plain(document: Any) -> ProbeFile:
    mapping = _need_mapping(document, "probe file")
    for key in (
        "schema",
        "tool",
        "mode",
        "radio",
        "private_reads_included",
        "label",
        "commands",
        "skipped",
        "unsolicited",
        "snapshot",
        "checks",
    ):
        if key not in mapping:
            raise _missing(key)
    commands = mapping["commands"]
    if not isinstance(commands, list):
        raise ValueError("field commands must be an array")
    skipped = mapping["skipped"]
    if not isinstance(skipped, list):
        raise ValueError("field skipped must be an array")
    stray = mapping["unsolicited"]
    if not isinstance(stray, list):
        raise ValueError("field unsolicited must be an array")
    snapshot = _need_mapping(mapping["snapshot"], "snapshot")
    for key in ("before", "after", "changed"):
        if key not in snapshot:
            raise _missing(f"snapshot.{key}")
    checks = mapping["checks"]
    if not isinstance(checks, list):
        raise ValueError("field checks must be an array")
    before = _need_mapping(snapshot["before"], "snapshot.before")
    after = _need_mapping(snapshot["after"], "snapshot.after")
    for label, side in (("before", before), ("after", after)):
        for key, value in side.items():
            if value is not None and not isinstance(value, str):
                raise ValueError(
                    f"field snapshot.{label}.{key} must be a string or null"
                )
    changed = snapshot["changed"]
    if not isinstance(changed, list) or not all(
        isinstance(key, str) for key in changed
    ):
        raise ValueError("field snapshot.changed must be an array of strings")
    termination = _termination_info(mapping.get("termination"))
    mode = _need_choice(mapping["mode"], "mode", MODES)
    return ProbeFile(
        schema=_check_schema(mapping["schema"]),
        tool=_tool_info(mapping["tool"]),
        mode=mode,
        radio=_radio_info(mapping["radio"]),
        private_reads_included=_need_bool(
            mapping["private_reads_included"], "private_reads_included"
        ),
        label=_need_str(mapping["label"], "label"),
        commands=tuple(_command_row(row, index) for index, row in enumerate(commands)),
        skipped=tuple(_skipped_row(row, index) for index, row in enumerate(skipped)),
        unsolicited=tuple(
            _unsolicited_row(row, index) for index, row in enumerate(stray)
        ),
        snapshot=Snapshot(
            before=dict(before), after=dict(after), changed=list(changed)
        ),
        termination=termination,
        checks=tuple(
            _check_record(row, f"checks[{i}]") for i, row in enumerate(checks)
        ),
    )


def _need_choice(value: Any, path: str, allowed: frozenset[str]) -> str:
    if _need_str(value, path) not in allowed:
        raise ValueError(f"field {path} must be one of {sorted(allowed)}")
    return str(value)


def _need_fields(document: Any, path: str, keys: tuple[str, ...]) -> dict[str, Any]:
    mapping = _need_mapping(document, path)
    if missing := [key for key in keys if key not in mapping]:
        raise _missing(f"{path}.{missing[0]}")
    if extra := sorted(set(mapping) - set(keys)):
        raise ValueError(f"field {path}.{extra[0]} is not part of the contract")
    return mapping


def _optional(value: Any, path: str, need: Any) -> Any:
    return None if value is None else need(value, path)


def _need_list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"field {path} must be an array")
    return value


def _text_map(value: Any, path: str, *, nullable: bool) -> dict[str, Any]:
    mapping = _need_mapping(value, path)
    for key, item in mapping.items():
        if not (nullable and item is None):
            _need_str(item, f"{path}.{key}")
    return dict(mapping)


def _check_step(document: Any, path: str) -> CheckStepRow:
    keys = ("kind", "entry", "request_hex", "reply_hex", "class", "value")
    row = _need_fields(document, path, keys)
    return CheckStepRow(
        kind=_need_choice(row["kind"], f"{path}.kind", frozenset({"read", "write"})),
        entry=_need_str(row["entry"], f"{path}.entry"),
        request_hex=_need_hex(row["request_hex"], f"{path}.request_hex"),
        reply_hex=_optional(row["reply_hex"], f"{path}.reply_hex", _need_hex),
        outcome=_need_choice(row["class"], f"{path}.class", OUTCOMES),
        value=_optional(row["value"], f"{path}.value", _need_str),
    )


def _check_answer(document: Any, path: str) -> CheckAnswerRow:
    row = _need_fields(document, path, ("question", "answer", "skipped"))
    return CheckAnswerRow(
        question=_need_str(row["question"], f"{path}.question"),
        answer=_optional(row["answer"], f"{path}.answer", _need_str),
        skipped=_need_bool(row["skipped"], f"{path}.skipped"),
    )


def _check_record(document: Any, path: str) -> CheckRecord:
    keys = tuple(name for name in CheckRecord.__dataclass_fields__)
    row = _need_fields(document, path, keys)
    witness = _need_fields(
        row["witness"], f"{path}.witness", ("before", "after", "changed")
    )
    changed = _need_list(witness["changed"], f"{path}.witness.changed")
    return CheckRecord(
        id=_need_str(row["id"], f"{path}.id"),
        title=_need_str(row["title"], f"{path}.title"),
        status=_need_choice(row["status"], f"{path}.status", CHECK_STATUSES),
        steps=tuple(
            _check_step(step, f"{path}.steps[{i}]")
            for i, step in enumerate(_need_list(row["steps"], f"{path}.steps"))
        ),
        answers=tuple(
            _check_answer(item, f"{path}.answers[{i}]")
            for i, item in enumerate(_need_list(row["answers"], f"{path}.answers"))
        ),
        originals=_text_map(row["originals"], f"{path}.originals", nullable=False),
        witness=CheckWitness(
            before=_text_map(
                witness["before"], f"{path}.witness.before", nullable=True
            ),
            after=_text_map(witness["after"], f"{path}.witness.after", nullable=True),
            changed=[_need_str(n, f"{path}.witness.changed") for n in changed],
        ),
        restored=_need_bool(row["restored"], f"{path}.restored"),
        verified=_need_bool(row["verified"], f"{path}.verified"),
        error=_optional(row["error"], f"{path}.error", _need_str),
    )
