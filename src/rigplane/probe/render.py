"""Customer-facing probe text as pure functions (MOR-4464).

Every string the customer sees lives here so exact-string tests pin it;
the CLI only wires prompts and ports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rigplane.commands import parse_civ_frame
from rigplane.probe.model import TerminationInfo


def termination_line(termination: TerminationInfo | None) -> str:
    """The plain-words stop line, or ``""`` when the run completed."""
    if termination is None or termination.status == "completed":
        return ""
    return f"  - stopped early: {termination.detail}\n"

__all__ = [
    "AbsentAnswer",
    "absent_answered_line",
    "closing_line",
    "cannot_open_message",
    "no_answers_message",
    "not_supported_message",
    "pre_save_summary",
    "present_missed_line",
    "probe_help",
    "progress_line",
    "silent_stop_message",
    "skipped_line",
    "start_screen",
    "termination_line",
]

#: Skip reasons in the order the summary counts them.
_REASON_ORDER = (
    "unsafe_family",
    "private",
    "response_code",
    "no_unambiguous_bytes",
    "run_aborted",
)

#: How many finding names print before the rest folds into "and N more".
_MAX_FINDING_NAMES = 10


@dataclass(frozen=True, slots=True)
class AbsentAnswer:
    """One declared-absent read the radio answered with data.

    ``request_hex`` is the sent body; ``reply_hex`` is the raw reply chunk
    (the full CI-V frame on CI-V profiles), or ``None`` when nothing arrived.
    """

    name: str
    request_hex: str
    reply_hex: str | None


def probe_help() -> str:
    """The ``rigplane probe`` help text."""
    return """rigplane probe — record how your radio answers, into one file you can send to us.

By default this only READS. It asks the radio the questions its profile lists
(frequency, mode, settings...) and writes the replies to a file. It does not
change any setting, does not transmit, does not start the tuner and does not
write to radio memory. Nothing is uploaded: you decide whether to send the file.

  --include-private   also read stored memory contents (may hold callsigns/text)
"""


def start_screen(profile_name: str, port_label: str, n_questions: int) -> str:
    """The screen shown before the read pass starts."""
    return (
        "RigPlane radio probe \u2014 READ ONLY\n"
        f"Radio profile: {profile_name}    Port: {port_label}\n"
        f"This run asks about {n_questions} read questions, one at a time, "
        "usually under a minute.\n"
        "It changes nothing and never transmits. "
        "Press Ctrl-C at any time to stop.\n"
        "Continue? [Y/n] "
    )


def progress_line(done: int, total: int) -> str:
    """One mid-run progress line."""
    return f"answered {done} of {total}"


def silent_stop_message() -> str:
    """The message when the radio stops answering mid-run."""
    return (
        "The radio stopped answering. Check it is on, and the port and speed are right."
    )


def _format_kb(size_bytes: int) -> str:
    return f"{max(size_bytes, 1024) // 1024} KB"


def _short_names(names: list[str]) -> str:
    """Up to ten names, then ``and N more``."""
    shown = names[:_MAX_FINDING_NAMES]
    text = ", ".join(shown)
    rest = len(names) - len(shown)
    if rest:
        text += f" and {rest} more"
    return text


def skipped_line(counts: dict[str, int]) -> str:
    """The ``N reads were not sent`` line, or ``""`` when nothing was skipped."""
    total = sum(counts.values())
    if not total:
        return ""
    parts = ", ".join(
        f"{counts[reason]} {reason}" for reason in _REASON_ORDER if counts.get(reason)
    )
    return (
        f"  - {total} reads were not sent ({parts}); "
        "each is listed under 'skipped' in the file with why\n"
    )


def _civ_payload(reply_hex: str) -> bytes | None:
    """Frame data of one CI-V reply frame, or ``None`` when not a frame."""
    try:
        reply = bytes.fromhex(reply_hex)
    except ValueError:
        return None
    try:
        parse_civ_frame(reply)
    except ValueError:
        return None
    return reply[4:-1]


def _answer_value(request_hex: str, reply_hex: str | None) -> str:
    """The value text for one answered absent read.

    The reply payload after the echoed request body: for a CI-V reply
    ``FE FE <ctrl> <radio> <request body> <value bytes> FD`` that is the
    ``<value bytes>`` hex. When the reply does not start with the request
    body, the full reply hex in brackets instead.
    """
    try:
        request = bytes.fromhex(request_hex)
    except ValueError:
        request = b""
    payload = _civ_payload(reply_hex or "")
    if payload is not None and request and payload.startswith(request):
        return payload[len(request) :].hex()
    return f"[{reply_hex or ''}]"


def absent_answered_line(entries: Sequence[AbsentAnswer]) -> str:
    """Reads the profile calls absent that the radio answered with data."""
    if not entries:
        return ""
    reads = "read" if len(entries) == 1 else "reads"
    shown = [
        f"{entry.name} ({_answer_value(entry.request_hex, entry.reply_hex)})"
        for entry in entries
    ]
    return (
        f"  - notable: the radio answered {len(entries)} {reads} "
        f"the profile calls absent: {_short_names(shown)}\n"
    )


def present_missed_line(names: list[str]) -> str:
    """Reads the profile declares present that were refused or silent."""
    if not names:
        return ""
    reads = "read" if len(names) == 1 else "reads"
    return (
        f"  - notable: the radio did not answer {len(names)} {reads} "
        f"the profile declares present: {_short_names(names)}\n"
    )


def pre_save_summary(
    path: str,
    size_bytes: int,
    n_commands: int,
    identity_text: str,
    settings_lines: list[str],
    private_reads_included: bool,
    *,
    skipped_counts: dict[str, int] | None = None,
    absent_answered: Sequence[AbsentAnswer] | None = None,
    present_missed: list[str] | None = None,
    termination: TerminationInfo | None = None,
) -> str:
    """The summary shown before the file exists on disk."""
    settings = ", ".join(settings_lines)
    if private_reads_included:
        memory_line = (
            "  - stored memory contents: these can contain callsigns "
            "and stored text. Look through the file before you send it.\n"
        )
    else:
        memory_line = (
            "  - not included: stored memory contents (you did not ask for them)\n"
        )
    return (
        f"Ready to save: {path}  ({_format_kb(size_bytes)})\n"
        "It contains:\n"
        f"  - the questions we asked and the radio's exact replies ({n_commands} of them)\n"
        f"  - what the radio says it is ({identity_text}) and the RigPlane version\n"
        f"  - your radio's current settings as of this moment: {settings}\n"
        f"{memory_line}"
        f"{skipped_line(skipped_counts or {})}"
        f"{absent_answered_line(absent_answered or [])}"
        f"{present_missed_line(present_missed or [])}"
        f"{termination_line(termination)}"
        "It does NOT contain: your name, IP address, computer name or log files.\n"
        "Radio settings can show what you use the radio for (frequencies, mode).\n"
        "Nothing has been sent anywhere. "
        "To review it: open the file in any text editor.\n"
        "Save the file? [Y/n] "
    )


def closing_line() -> str:
    """The line printed after the file is saved."""
    return (
        "Done. Nothing was uploaded. Send the file to the person who asked you for it."
    )


def not_supported_message(protocol: str) -> str:
    """The message for a profile protocol the probe does not speak."""
    return (
        f"Probe for protocol {protocol!r} is not supported yet "
        "(USB CI-V and Yaesu CAT only)."
    )


def cannot_open_message(device: str) -> str:
    """The message when the serial port cannot be opened."""
    return (
        f"Cannot open {device}. Check the radio is on and the port and speed are right."
    )


def no_answers_message() -> str:
    """The message when nothing answered at all."""
    return (
        "The radio did not answer any question. "
        "Check it is on, and the port and speed are right."
    )
