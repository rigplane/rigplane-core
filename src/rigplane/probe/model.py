"""``rigplane-probe-v1`` file model: typed contract with a validating parser.

The probe file is a paired, classified table: one row per read the run
sent, the radio's exact reply bytes, and a convenience class per row.
Raw bytes are always kept; the class never replaces them. Reads the
run deliberately did not send are listed under ``skipped`` with a
reason from a closed enum, so a silent omission is impossible.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "SCHEMA",
    "OUTCOMES",
    "EXPECTED",
    "SKIP_REASONS",
    "TERMINATION_STATUSES",
    "MODES",
    "STEP_KINDS",
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
        "sent",
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
#: serial link. The parser refuses any other status.
TERMINATION_STATUSES = frozenset({"completed", "silence_stop", "transport_error"})

#: How a probe run ran: ``read`` is the read-only pass, ``read+checks``
#: ran at least one consented write check after it. The parser refuses
#: any other mode.
MODES = frozenset({"read", "read+checks"})

#: What a check step did: a profile ``write``/``read`` by command name,
#: a literal ``raw_read``/``raw_write`` outside the declared commands,
#: or an ``ask`` put to the person (recorded in ``answers``, not here).
STEP_KINDS = frozenset({"write", "read", "raw_read", "raw_write"})


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
    """One wire step of a write check and the radio's exact reply.

    ``outcome`` is the convenience class; the JSON key is ``class``.
    ``reply_hex``/``reply_text`` are ``None`` when nothing arrived.
    """

    kind: str
    name: str
    request_hex: str
    request_text: str
    reply_hex: str | None
    reply_text: str | None
    outcome: str


@dataclass(frozen=True, slots=True)
class CheckAnswerRow:
    """One question put to the person: the answer, or a skip."""

    question: str
    answer: str | None
    skipped: bool


@dataclass(frozen=True, slots=True)
class CheckWitness:
    """Profile reads snapshotted before and after one check."""

    before: dict[str, str | None]
    after: dict[str, str | None]
    changed: list[str]


@dataclass(frozen=True, slots=True)
class CheckRecord:
    """One consented write check: what changed, what was asked, and
    whether the originals were put back and verified."""

    id: str
    title: str
    preconditions_ok: bool
    steps: tuple[CheckStepRow, ...]
    answers: tuple[CheckAnswerRow, ...]
    originals: dict[str, str]
    witness: CheckWitness
    restored: bool
    verified: bool
    error: str | None


@dataclass(frozen=True, slots=True)
class ProbeFile:
    """One ``rigplane-probe-v1`` document. ``checks`` holds one typed
    record per write check that ran (empty for a read-only run)."""

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
        "checks": [_check_record_to_plain(record) for record in file.checks],
    }


def _check_record_to_plain(record: CheckRecord) -> dict[str, Any]:
    return {
        "id": record.id,
        "title": record.title,
        "preconditions_ok": record.preconditions_ok,
        "steps": [
            {
                "kind": row.kind,
                "name": row.name,
                "request_hex": row.request_hex,
                "request_text": row.request_text,
                "reply_hex": row.reply_hex,
                "reply_text": row.reply_text,
                "class": row.outcome,
            }
            for row in record.steps
        ],
        "answers": [
            {
                "question": row.question,
                "answer": row.answer,
                "skipped": row.skipped,
            }
            for row in record.answers
        ],
        "originals": dict(record.originals),
        "witness": {
            "before": dict(record.witness.before),
            "after": dict(record.witness.after),
            "changed": list(record.witness.changed),
        },
        "restored": record.restored,
        "verified": record.verified,
        "error": record.error,
    }


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
    records = tuple(
        _check_record(row, index) for index, row in enumerate(checks)
    )
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
    mode = _need_str(mapping["mode"], "mode")
    if mode not in MODES:
        raise ValueError(f"field mode must be one of {sorted(MODES)}")
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
        checks=records,
    )


def _check_step_row(document: Any, check: int, index: int) -> CheckStepRow:
    mapping = _need_mapping(document, f"checks[{check}].steps[{index}]")
    for key in (
        "kind", "name", "request_hex", "request_text",
        "reply_hex", "reply_text", "class",
    ):
        if key not in mapping:
            raise _missing(f"checks[{check}].steps[{index}].{key}")
    kind = _need_str(mapping["kind"], f"checks[{check}].steps[{index}].kind")
    if kind not in STEP_KINDS:
        raise ValueError(
            f"field checks[{check}].steps[{index}].kind "
            f"must be one of {sorted(STEP_KINDS)}"
        )
    outcome = _need_str(mapping["class"], f"checks[{check}].steps[{index}].class")
    if outcome not in OUTCOMES:
        raise ValueError(
            f"field checks[{check}].steps[{index}].class "
            f"must be one of {sorted(OUTCOMES)}"
        )
    reply_hex = mapping["reply_hex"]
    reply_text = mapping["reply_text"]
    if reply_hex is not None:
        _need_hex(reply_hex, f"checks[{check}].steps[{index}].reply_hex")
    if reply_text is not None and not isinstance(reply_text, str):
        raise ValueError(
            f"field checks[{check}].steps[{index}].reply_text "
            "must be a string or null"
        )
    return CheckStepRow(
        kind=kind,
        name=_need_str(mapping["name"], f"checks[{check}].steps[{index}].name"),
        request_hex=_need_hex(
            mapping["request_hex"], f"checks[{check}].steps[{index}].request_hex"
        ),
        request_text=_need_str(
            mapping["request_text"], f"checks[{check}].steps[{index}].request_text"
        ),
        reply_hex=reply_hex,
        reply_text=reply_text,
        outcome=outcome,
    )


def _check_answer_row(document: Any, check: int, index: int) -> CheckAnswerRow:
    mapping = _need_mapping(document, f"checks[{check}].answers[{index}]")
    for key in ("question", "answer", "skipped"):
        if key not in mapping:
            raise _missing(f"checks[{check}].answers[{index}].{key}")
    answer = mapping["answer"]
    if answer is not None and not isinstance(answer, str):
        raise ValueError(
            f"field checks[{check}].answers[{index}].answer must be a string or null"
        )
    return CheckAnswerRow(
        question=_need_str(
            mapping["question"], f"checks[{check}].answers[{index}].question"
        ),
        answer=answer,
        skipped=_need_bool(
            mapping["skipped"], f"checks[{check}].answers[{index}].skipped"
        ),
    )


def _check_record(document: Any, index: int) -> CheckRecord:
    mapping = _need_mapping(document, f"checks[{index}]")
    for key in (
        "id", "title", "preconditions_ok", "steps", "answers", "originals",
        "witness", "restored", "verified", "error",
    ):
        if key not in mapping:
            raise _missing(f"checks[{index}].{key}")
    steps = mapping["steps"]
    answers = mapping["answers"]
    if not isinstance(steps, list):
        raise ValueError(f"field checks[{index}].steps must be an array")
    if not isinstance(answers, list):
        raise ValueError(f"field checks[{index}].answers must be an array")
    originals = mapping["originals"]
    if not isinstance(originals, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in originals.items()
    ):
        raise ValueError("field checks[{index}].originals must be an object of strings")
    witness = _need_mapping(mapping["witness"], f"checks[{index}].witness")
    for key in ("before", "after", "changed"):
        if key not in witness:
            raise _missing(f"checks[{index}].witness.{key}")
    for label in ("before", "after"):
        side = witness[label]
        if not isinstance(side, dict) or not all(
            isinstance(name, str) and (value is None or isinstance(value, str))
            for name, value in side.items()
        ):
            raise ValueError(
                f"field checks[{index}].witness.{label} "
                "must be an object of strings or null"
            )
    changed = witness["changed"]
    if not isinstance(changed, list) or not all(
        isinstance(name, str) for name in changed
    ):
        raise ValueError(f"field checks[{index}].witness.changed must be an array")
    error = mapping["error"]
    if error is not None and not isinstance(error, str):
        raise ValueError(f"field checks[{index}].error must be a string or null")
    return CheckRecord(
        id=_need_str(mapping["id"], f"checks[{index}].id"),
        title=_need_str(mapping["title"], f"checks[{index}].title"),
        preconditions_ok=_need_bool(
            mapping["preconditions_ok"], f"checks[{index}].preconditions_ok"
        ),
        steps=tuple(
            _check_step_row(row, index, step) for step, row in enumerate(steps)
        ),
        answers=tuple(
            _check_answer_row(row, index, answer)
            for answer, row in enumerate(answers)
        ),
        originals=dict(originals),
        witness=CheckWitness(
            before=dict(witness["before"]),
            after=dict(witness["after"]),
            changed=list(changed),
        ),
        restored=_need_bool(mapping["restored"], f"checks[{index}].restored"),
        verified=_need_bool(mapping["verified"], f"checks[{index}].verified"),
        error=error,
    )
