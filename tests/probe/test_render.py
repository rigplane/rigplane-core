"""Customer-facing probe text, pinned exactly (MOR-4464 PR A)."""

from __future__ import annotations

from rigplane.probe import render


def test_start_screen_names_profile_port_and_count() -> None:
    text = render.start_screen(
        profile_name="FT-991A", port_label="COM5 @ 38400", n_questions=140
    )
    assert text == (
        "RigPlane radio probe \u2014 READ ONLY\n"
        "Radio profile: FT-991A    Port: COM5 @ 38400\n"
        "This run asks about 140 read questions, one at a time, "
        "usually under a minute.\n"
        "It changes nothing and never transmits. "
        "Press Ctrl-C at any time to stop.\n"
        "Continue? [Y/n] "
    )


def test_progress_line() -> None:
    assert render.progress_line(done=20, total=140) == "answered 20 of 140"


def test_silent_stop_message() -> None:
    assert render.silent_stop_message() == (
        "The radio stopped answering. Check it is on, and the port and speed are right."
    )


def test_pre_save_summary_without_private() -> None:
    text = render.pre_save_summary(
        path="/tmp/ft991a-probe-20261114-173000.json",
        size_bytes=38912,
        n_commands=142,
        identity_text="model code 0670",
        settings_lines=["frequency 14.074 MHz", "mode USB"],
        private_reads_included=False,
    )
    assert text == (
        "Ready to save: /tmp/ft991a-probe-20261114-173000.json  (38 KB)\n"
        "It contains:\n"
        "  - the questions we asked and the radio's exact replies (142 of them)\n"
        "  - what the radio says it is (model code 0670) and the RigPlane version\n"
        "  - your radio's current settings as of this moment: "
        "frequency 14.074 MHz, mode USB\n"
        "  - not included: stored memory contents "
        "(you did not ask for them)\n"
        "It does NOT contain: your name, IP address, computer name or log files.\n"
        "Radio settings can show what you use the radio for (frequencies, mode).\n"
        "Nothing has been sent anywhere. "
        "To review it: open the file in any text editor.\n"
        "Save the file? [Y/n] "
    )


def test_pre_save_summary_with_private() -> None:
    text = render.pre_save_summary(
        path="/tmp/ft991a-probe-20261114-173000.json",
        size_bytes=38912,
        n_commands=144,
        identity_text="model code 0670",
        settings_lines=["frequency 14.074 MHz"],
        private_reads_included=True,
    )
    assert (
        "  - stored memory contents: these can contain callsigns "
        "and stored text. Look through the file before you send it.\n"
    ) in text
    assert "you did not ask for them" not in text


def test_closing_line() -> None:
    assert render.closing_line() == (
        "Done. Nothing was uploaded. Send the file to the person who asked you for it."
    )


def test_not_supported_message() -> None:
    assert render.not_supported_message("kenwood_cat") == (
        "Probe for protocol 'kenwood_cat' is not supported yet "
        "(USB CI-V and Yaesu CAT only)."
    )


def test_cannot_open_message() -> None:
    text = render.cannot_open_message("socket://127.0.0.1:1")
    assert text == (
        "Cannot open socket://127.0.0.1:1. "
        "Check the radio is on and the port and speed are right."
    )


def test_no_answers_message() -> None:
    assert render.no_answers_message() == (
        "The radio did not answer any question. "
        "Check it is on, and the port and speed are right."
    )


def test_probe_help_mentions_read_only() -> None:
    text = render.probe_help()
    assert "rigplane probe" in text
    assert "only READS" in text
    assert "does not transmit" in text
    assert "Nothing is uploaded" in text
    assert "--include-private" in text
