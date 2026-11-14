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
    "ToolInfo",
    "IdentityReport",
    "RadioInfo",
    "CommandRow",
    "SkippedRead",
    "UnsolicitedRow",
    "Snapshot",
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
#: declared-absent read no sibling profile lends unanimous bytes for.
SKIP_REASONS = frozenset(
    {"unsafe_family", "private", "response_code", "no_unambiguous_bytes"}
)


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
    """Which radio the run talked to. Never a port path, host, or user."""

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
class ProbeFile:
    """One ``rigplane-probe-v1`` document. ``checks`` stays empty in PR A."""

    tool: ToolInfo
    mode: str
    radio: RadioInfo
    private_reads_included: bool
    label: str
    commands: tuple[CommandRow, ...] = ()
    skipped: tuple[SkippedRead, ...] = ()
    unsolicited: tuple[UnsolicitedRow, ...] = ()
    snapshot: Snapshot = field(default_factory=lambda: Snapshot({}, {}, []))
    checks: tuple[Any, ...] = ()
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
        "checks": list(file.checks),
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
    return ProbeFile(
        schema=_check_schema(mapping["schema"]),
        tool=_tool_info(mapping["tool"]),
        mode=_need_str(mapping["mode"], "mode"),
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
        checks=tuple(checks),
    )
