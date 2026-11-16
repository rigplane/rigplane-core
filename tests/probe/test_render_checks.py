"""Exact customer strings for the write checks (MOR-4464 B1).

These sentences are with the owner for review and may change: they
live only in ``render.py`` and are pinned here word for word.
"""

from __future__ import annotations

from rigplane.probe import render


def test_intro_block() -> None:
    assert render.checks_intro(3) == (
        "The read-only part is done.\n"
        "Next there are 3 optional checks. Each one CHANGES one setting "
        "on your radio for a moment, asks the radio what it now says, and "
        "puts the setting back. None of them transmits, starts the antenna "
        "tuner or writes to the radio's memory. You can say no to any check.\n"
        'Do not touch the knobs or buttons of the radio until this program says "done".\n'
        "Nothing may key the radio while a check runs: no PTT, no CW key, "
        "no VOX or microphone PTT, no digital program.\n"
        'If the program stops or the cable comes out, set the values listed under "Right now" back by hand.\n'
        "Run the checks? [y/N]"
    )


def test_per_check_consent() -> None:
    assert render.check_consent_prompt(
        2, 3,
        "cross-band split",
        "sets VFO-A to 7.074 MHz and VFO-B to 145.000 MHz.",
        "puts VFO-A, VFO-B and split back exactly as they are now.",
        ["VFO-A 14.200 MHz", "split off"],
    ) == (
        "Check 2 of 3: cross-band split\n"
        "  What it does: sets VFO-A to 7.074 MHz and VFO-B to 145.000 MHz.\n"
        "  Afterwards:   puts VFO-A, VFO-B and split back exactly as they are now.\n"
        "  Right now:    VFO-A 14.200 MHz, split off (these will be restored).\n"
        "  Never:        no transmit, no tuner, no memory write.\n"
        "  Ctrl-C at any time puts the settings back first.\n"
        "Run this check? [y/N]"
    )


def test_per_check_warnings_and_raw_verbs() -> None:
    text = render.check_consent_prompt(
        1, 1, "power", "sets 100 W.", "puts power back.", ["power 5 W"],
        warnings=("Switch the amplifier off first.",),
        raw_verbs=("PR",),
    )
    assert "  Switch the amplifier off first.\n" in text
    assert (
        "  This check sends the command(s) PR to your radio, "
        "which RigPlane does not normally use.\n" in text
    )


def test_question_results_and_warning() -> None:
    assert render.ask_question_prompt("Does the display show it?") == (
        "Look at the radio. Does the display show it? Type your answer, "
        "or press Enter to skip (it is skipped after 60 seconds):"
    )
    assert render.check_result_line(2, True) == (
        "Check 2 done: settings put back and verified."
    )
    assert render.check_result_line(2, False) == (
        "Check 2 done: settings put back, but the radio now reports "
        "a different value than before (see the file)."
    )
    assert render.check_restore_warning("VFO-B", "14.250 MHz") == (
        "WARNING: could not put VFO-B back to 14.250 MHz. "
        "Please set it by hand. This check never transmits."
    )


def test_precondition_summary_journal_and_plain_lines() -> None:
    assert render.precondition_abort_message() == (
        "Switch the radio to VFO mode (not a memory channel) "
        "and run the check again. Nothing was changed."
    )
    assert render.checks_summary_line(3, 2, 1, ["freq", "mode"]) == (
        "checks: 3 run (2 verified, 1 skipped by you); "
        "settings changed and put back: freq, mode"
    )
    assert render.journal_pending_message(["freq FA1;"], "p.json") == (
        "A check did not finish last time. Before it the radio had: "
        "freq FA1;. Please set these by hand. "
        "The journal file is left in place until you delete it: p.json"
    )
    assert "has no write checks" in render.checks_no_checks_message("FT-991A")
    assert render.checks_no_tty_message().isascii()
    assert render.checks_consent_prompt() == "Run the checks? [y/N]"
