"""Customer-facing probe text as pure functions (MOR-4464).

Every string the customer sees lives here so exact-string tests pin it;
the CLI only wires prompts and ports.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from rigplane.commands import parse_civ_frame
from rigplane.probe.model import TerminationInfo

#: The display name of this command in every customer-facing hint.
#: The Pro sidecar calls the probe under its own command name, so every
#: hint takes a ``prog`` parameter defaulting to this instead of spelling
#: the name inline. This is the one place the default is spelled.
DEFAULT_PROBE_PROG = "rigplane probe"


def termination_line(termination: TerminationInfo | None) -> str:
    """The plain-words stop line, or ``""`` when the run completed."""
    if termination is None or termination.status == "completed":
        return ""
    return f"  - stopped early: {termination.detail}\n"


__all__ = [
    "DEFAULT_PROBE_PROG",
    "AbsentAnswer",
    "absent_answered_line",
    "ask_question_prompt",
    "bad_choice_message",
    "cancelled_message",
    "check_consent_prompt",
    "check_failed_message",
    "check_restore_warning",
    "check_running_line",
    "originals_unreadable_message",
    "check_result_line",
    "checks_consent_prompt",
    "checks_intro",
    "checks_no_checks_message",
    "checks_no_tty_message",
    "checks_summary_line",
    "closing_line",
    "cannot_open_message",
    "confirm_radio_prompt",
    "discovery_announcement",
    "found_line",
    "human_read_name",
    "journal_pending_message",
    "journal_unreadable_message",
    "no_answers_message",
    "no_profile_for_radio_message",
    "no_radio_found_message",
    "not_supported_message",
    "opening_folder_line",
    "port_without_profile_message",
    "pre_save_summary",
    "precondition_abort_message",
    "read_failed_message",
    "readback_mismatch_message",
    "rehearsal_failed_message",
    "present_missed_line",
    "probe_help",
    "progress_line",
    "radio_choice_list",
    "radio_choice_prompt",
    "several_no_tty_message",
    "silent_stop_message",
    "single_no_tty_message",
    "skip_reason_text",
    "skipped_line",
    "start_screen",
    "termination_line",
    "transport_error_message",
    "witness_changed_message",
    "write_failed_message",
]

#: Skip reasons in the order the summary counts them.
_REASON_ORDER = (
    "unsafe_family",
    "private",
    "response_code",
    "no_unambiguous_bytes",
    "run_aborted",
)

#: Plain-words meaning of each skip reason enum in the file contract.
#: The file keeps the enum; the screen shows the sentence.
_SKIP_REASON_TEXT = {
    "unsafe_family": "never asked: asking could change something on the radio",
    "response_code": "not a question: this entry is an answer the radio gives",
    "private": "stored memory, only read if you ask for it",
    "no_unambiguous_bytes": "no safe way to ask it for this radio",
    "run_aborted": "the run stopped before this question",
}


def skip_reason_text(reason: str) -> str:
    """The plain-words meaning of one skip reason enum."""
    return _SKIP_REASON_TEXT[reason]


def human_read_name(name: str) -> str:
    """The human name of one profile read (``get_auto_notch`` -> ``auto notch``)."""
    short = name[4:] if name.startswith("get_") else name
    return short.replace("_", " ")


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


def probe_help(prog: str = DEFAULT_PROBE_PROG) -> str:
    """The probe help text, naming the command the user actually ran."""
    return f"""{prog} - record how your radio answers, into one file you can send to us.

By default this only READS. It asks the radio the questions its profile lists
(frequency, mode, settings...) and writes the replies to a file. It does not
change any setting, does not transmit, does not start the tuner and does not
write to radio memory. Nothing is uploaded: you decide whether to send the file.

With no arguments it scans USB serial ports for your radio and asks which
one is yours. You can also name the model and port yourself:

  {prog}
  {prog} FT-991A
  {prog} FT-991A --port /dev/ttyUSB0

  --include-private   also read stored memory contents (may hold callsigns/text)
"""


def start_screen(profile_name: str, port_label: str, n_questions: int) -> str:
    """The screen shown before the read pass starts."""
    return (
        "RigPlane radio probe - READ ONLY\n"
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
    parts = "; ".join(
        f"{counts[reason]} {skip_reason_text(reason)}"
        for reason in _REASON_ORDER
        if counts.get(reason)
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
        f"{human_read_name(entry.name)}"
        f" ({_answer_value(entry.request_hex, entry.reply_hex)})"
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
    shown = [human_read_name(name) for name in names]
    return (
        f"  - notable: the radio did not answer {len(names)} {reads} "
        f"the profile declares present: {_short_names(shown)}\n"
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
    checks_line: str | None = None,
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
        + (checks_line + "\n" if checks_line else "")
        "The tool itself does not add your name, computer name, internet "
        "address, or any file from your computer.\n"
        "But the --label text is saved exactly as you typed it, and stored "
        "memory contents (only with --include-private) can hold text you "
        "stored. Look through the file before you send it.\n"
        "Radio settings can show what you use the radio for (frequencies, mode).\n"
        "Nothing has been sent anywhere. "
        "To review it: open the file in any text editor.\n"
        "Save the file? [Y/n] "
    )


def closing_line(path: str) -> str:
    """The line printed after the file is saved; it names the full path."""
    return (
        f"Saved to {path}. "
        "Nothing was uploaded. Send the file to the person who asked you for it."
    )


def discovery_announcement() -> str:
    """The lines printed before scanning USB serial ports for the radio."""
    return (
        "Looking for your radio on USB serial ports...\n"
        "This only asks each port a read-only identity question; "
        "it changes nothing.\n"
    )


def found_line(model: str, port: str) -> str:
    """The one-radio discovery result."""
    return f"Found: {model} on {port}"


def discovery_failed_message() -> str:
    """The sentence when the USB scan itself fails (exit 3)."""
    return "Could not scan the USB ports, so no radio was checked."


def confirm_radio_prompt() -> str:
    """The question after a one-radio discovery result."""
    return "Is this your radio? [Y/n] "


def radio_choice_list(entries: Sequence[tuple[str, str]]) -> str:
    """The numbered list for a multi-radio discovery result."""
    lines = [f"Found {len(entries)} radios:"]
    lines.extend(
        f"  {index}. {model} on {port}"
        for index, (model, port) in enumerate(entries, 1)
    )
    return "\n".join(lines)


def radio_choice_prompt(n: int) -> str:
    """The question after a multi-radio discovery result."""
    return f"Type the number of your radio (1-{n}), or press Enter to quit: "


def bad_choice_message() -> str:
    """The line for a number that is not on the discovery list."""
    return "That is not one of the numbers above."


def no_radio_found_message(profile: str | None, prog: str = DEFAULT_PROBE_PROG) -> str:
    """The checklist when discovery found no usable radio (exit 3)."""
    header = f"No {profile} found." if profile else "No radio found."
    hand = profile if profile else "<model>"
    return (
        f"{header} Check that:\n"
        "  - the radio is on\n"
        "  - the USB cable is firmly connected\n"
        "  - the USB driver is installed\n"
        "  - RigPlane Pro is closed, if you use it\n"
        "  - other programs that use the radio are closed\n"
        "If you know the port, name the model and port by hand: "
        f"{prog} {hand} --port <device>"
    )


def port_without_profile_message(prog: str = DEFAULT_PROBE_PROG) -> str:
    """The sentence for ``--port`` without a profile (exit 2)."""
    return (
        "A port alone is not enough: name your radio model too, for example: "
        f"{prog} FT-991A --port COM5. "
        "Or drop --port to scan for it automatically."
    )


def single_no_tty_message(model: str, port: str, prog: str = DEFAULT_PROBE_PROG) -> str:
    """The sentence for one radio with no terminal to confirm in (exit 2)."""
    return (
        f"Found {model} on {port}, but there is no terminal to confirm in. "
        "Rerun with --yes to take this radio, or name it by hand: "
        f"{prog} {model} --port {port}."
    )


def several_no_tty_message(prog: str = DEFAULT_PROBE_PROG) -> str:
    """The sentence for several radios with no terminal to choose in (exit 2)."""
    return (
        "More than one radio found and there is no terminal to choose in. "
        "Rerun naming your radio, for example: "
        f"{prog} <model> --port <device>."
    )


def no_profile_for_radio_message(model: str) -> str:
    """The sentence for a discovered radio with no shipped profile (exit 2)."""
    return f"No shipped profile matches {model}, so the probe cannot run for it."


def opening_folder_line(directory: str) -> str:
    """The line printed when the save folder is opened after saving."""
    return f"Opening the folder: {directory}"


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


def checks_no_tty_message() -> str:
    """The sentence for ``--checks`` with no terminal to ask in (exit 2)."""
    return (
        "--checks changes settings on your radio, so it needs a terminal "
        "to ask before each change. Rerun in a terminal."
    )


def checks_no_checks_message(model: str) -> str:
    """The sentence for ``--checks`` on a profile without checks."""
    return (
        f"{model} has no write checks. "
        "The read-only part is the whole run; nothing was changed."
    )


def checks_intro(n: int) -> str:
    """The block starting the optional write checks (ends asking)."""
    return (
        "The read-only part is done.\n"
        f"Next there are {n} optional checks. Each one CHANGES one setting "
        "on your radio for a moment, asks the radio what it now says, and "
        "puts the setting back. None of them transmits, starts the antenna "
        "tuner or writes to the radio's memory. You can say no to any check.\n"
        'Do not touch the knobs or buttons of the radio until this program says "done".\n'
        "Nothing may key the radio while a check runs: no PTT, no CW key, "
        "no VOX or microphone PTT, no digital program.\n"
        'If the program stops or the cable comes out, set the values listed under "Right now" back by hand.\n'
        "Run the checks? [y/N]"
    )


def checks_consent_prompt() -> str:
    """The question starting the optional write checks."""
    return "Run the checks? [y/N]"


def check_consent_prompt(
    i: int,
    n: int,
    title: str,
    explain_what: str,
    explain_after: str,
    right_now: list[str],
    warnings: tuple[str, ...] = (),
    raw_verbs: tuple[str, ...] = (),
) -> str:
    """The per-check question: what changes, what is now, what never."""
    lines = [
        f"Check {i} of {n}: {title}",
        f"  What it does: {explain_what}",
        f"  Afterwards:   {explain_after}",
        f"  Right now:    {', '.join(right_now)} (these will be restored).",
        "  Never:        no transmit, no tuner, no memory write.",
    ]
    lines.extend(f"  {warning}" for warning in warnings)
    if raw_verbs:
        lines.append(
            f"  This check sends the command(s) {', '.join(raw_verbs)} "
            "to your radio, which RigPlane does not normally use."
        )
    lines.append("  Ctrl-C at any time puts the settings back first.")
    lines.append("Run this check? [y/N]")
    return "\n".join(lines)


def ask_question_prompt(question: str) -> str:
    """The question put to the person mid-check (60 s to answer)."""
    return (
        f"Look at the radio. {question} Type your answer, "
        "or press Enter to skip (it is skipped after 60 seconds):"
    )


def check_running_line(i: int, n: int, title: str) -> str:
    """The line opening one consented check."""
    return f"Running check {i} of {n}: {title}"


def check_result_line(i: int, verified: bool) -> str:
    """The per-check result: verified, or put back but different."""
    if verified:
        return f"Check {i} done: settings put back and verified."
    return (
        f"Check {i} done: settings put back, but the radio now reports "
        "a different value than before (see the file)."
    )


def check_restore_warning(name: str, value: str) -> str:
    """The warning when one original could not be put back."""
    return (
        f"WARNING: could not put {name} back to {value}. "
        "Please set it by hand. This check never transmits."
    )


def precondition_abort_message() -> str:
    """The abort when the radio is not in VFO mode."""
    return (
        "Switch the radio to VFO mode (not a memory channel) "
        "and run the check again. Nothing was changed."
    )


def originals_unreadable_message(name: str) -> str:
    """The abort when an original cannot be read before the first write."""
    return f"could not read {name} before the first write."


def rehearsal_failed_message(name: str) -> str:
    """The abort when the no-op rehearsal does not read back."""
    return f"check not run: {name} refused the rehearsal; nothing was changed."


def write_failed_message(target: str, outcome: str) -> str:
    """The record when a check write is refused, silent or malformed."""
    return f"write {target} was {outcome}."


def read_failed_message(target: str, outcome: str) -> str:
    """The record when a check read is refused, silent or malformed."""
    return f"read {target} was {outcome}."


def readback_mismatch_message(name: str) -> str:
    """The record when a read does not show the value just written."""
    return (
        f"readback for {name} differs: "
        "the radio does not report the written value."
    )


def witness_changed_message(name: str) -> str:
    """The record when the witness moves outside the restore list."""
    return f"witness changed outside the restore list: {name}."


def transport_error_message(detail: str) -> str:
    """The record when the link breaks mid-check."""
    return f"transport error: {detail}."


def cancelled_message(detail: str) -> str:
    """The record when the person or a signal cancels a check."""
    return f"cancelled: {detail}."


def check_failed_message(detail: str) -> str:
    """The record when a check fails without changing anything."""
    return f"check failed: {detail}."


def checks_summary_line(
    run: int, verified: int, skipped: int, changed: list[str]
) -> str:
    """The pre-save one-liner over the checks that ran."""
    names = ", ".join(changed) if changed else "none"
    return (
        f"checks: {run} run ({verified} verified, {skipped} skipped by you); "
        f"settings changed and put back: {names}"
    )


def journal_pending_message(items: list[str], path: str) -> str:
    """The next-start message for a journal an unfinished check left."""
    return (
        "A check did not finish last time. Before it the radio had: "
        f"{', '.join(items)}. Please set these by hand. "
        f"The journal file is left in place until you delete it: {path}"
    )


def journal_unreadable_message(path: str) -> str:
    """The next-start message for a journal that does not parse."""
    return (
        f"A previous check journal {path} could not be read. "
        "Delete it once the radio is back the way it was."
    )


def no_answers_message() -> str:
    """The message when nothing answered at all."""
    return (
        "The radio did not answer any question. "
        "Check it is on, and the port and speed are right."
    )
