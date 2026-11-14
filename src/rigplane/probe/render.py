"""Customer-facing probe text as pure functions (MOR-4464).

Every string the customer sees lives here so exact-string tests pin it;
the CLI only wires prompts and ports.
"""

from __future__ import annotations

__all__ = [
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
]

#: Skip reasons in the order the summary counts them.
_REASON_ORDER = ("unsafe_family", "private", "response_code", "no_unambiguous_bytes")

#: How many finding names print before the rest folds into "and N more".
_MAX_FINDING_NAMES = 10


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


def absent_answered_line(names: list[str]) -> str:
    """Reads the profile calls absent that the radio answered with data."""
    if not names:
        return ""
    reads = "read" if len(names) == 1 else "reads"
    return (
        f"  - notable: the radio answered {len(names)} {reads} "
        f"the profile calls absent: {_short_names(names)}\n"
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
    absent_answered: list[str] | None = None,
    present_missed: list[str] | None = None,
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
