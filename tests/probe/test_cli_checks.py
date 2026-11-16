"""``rigplane probe --checks``: the CLI wiring of the write checks (MOR-4502).

Every run drives the real ``execute()`` against the testkit ``FakeCatRadio``
(FT-991A) through the production serial exchange, with a scripted person at
the keyboard. The synthetic check comes from the engine tests' loader helper;
the journal lives in the temporary state folder (``conftest.py``).
"""

from __future__ import annotations

import argparse
import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

from rigplane.cli import _probe
from rigplane.cli._probe import build_probe_parser, execute
from rigplane.probe import render
from rigplane.probe.journal import journal_path, leftover_journal_line
from rigplane.probe.model import CheckRecord, CheckWitness, ProbeFile
from tests.probe.test_checks_engine import (
    RIGS,
    SPLIT,
    cat_exchange,
    cat_radio,
    check_toml,
    load,
)

REPO = Path(__file__).resolve().parents[2]
QUESTION = "Does the display show TX on VFO-B?"
POWER_SENTENCE = (
    "This check briefly sets the radio's RF power to 100 W on the 2 m band and "
    "reads back what the radio holds."
)
WARNED = check_toml(
    [
        'kind = "write"\nentry = "freq_a"\nvalue = 7074000',
        'kind = "write"\nentry = "freq_b"\nvalue = 145000000',
        'kind = "write"\nentry = "split"\nvalue = "on"',
    ],
    ["freq_a", "freq_b", "split", "mode_main"],
    f'warnings = ["{POWER_SENTENCE}"]',
)
POWER_ASK = check_toml(
    [
        'kind = "write"\nentry = "power"\nvalue = 100',
        'kind = "ask"\nquestion = "Did the meter move?"',
    ],
    ["power"],
)
CAT_WRITE = re.compile(r"(FA|FB)\d{9};|FT[23];|PC\d{3};|MD0\w;")
PINNED = (
    "A check could not put the radio back: RF power is now 100 W, it was 20 W. "
    "Set it back by hand before transmitting."
)


class Person:
    """The scripted keyboard: the first matching prompt prefix answers (a
    callable answer runs first); everything else gets Enter."""

    def __init__(self, answers: dict[str, str | Callable[[], str]] | None = None):
        self.answers = answers or {}
        self.log: list[tuple[str, str]] = []

    def ask(self, prompt: str) -> str:
        self.log.append(("ask", prompt))
        for prefix, answer in self.answers.items():
            if prompt.startswith(prefix):
                return answer() if callable(answer) else answer
        return ""

    def emit(self, text: str) -> None:
        self.log.append(("emit", text))

    def asked(self, prompt: str) -> bool:
        return ("ask", prompt) in self.log

    def index(self, kind: str, start: str) -> int:
        return next(
            i for i, (k, t) in enumerate(self.log) if k == kind and t.startswith(start)
        )


def cat_writes(radio) -> list[str]:
    return [line for line in radio.commands if CAT_WRITE.fullmatch(line)]


async def drive(
    tmp_path: Path,
    config,
    radio,
    person: Person,
    *,
    checks: bool = True,
    yes: bool = False,
    is_tty: bool = True,
) -> tuple[int, Path]:
    out = tmp_path / "probe.json"

    async def factory():
        return await cat_exchange(radio)

    code = await execute(
        config,
        b"profile bytes",
        device_label="COM5",
        baud=38400,
        out=str(out),
        include_private=False,
        label="",
        yes=yes,
        is_tty=is_tty,
        ask=person.ask,
        exchange_factory=factory,
        gap_s=0.0,
        timeout_s=0.2,
        emit=person.emit,
        checks=checks,
    )
    return code, out


def saved(out: Path) -> ProbeFile:
    return ProbeFile.from_json(out.read_text(encoding="utf-8"))


def write_leftover(path: Path) -> bytes:
    body = (
        b'{"check_id": "x", "check_title": "X", "profile": "FT-991A", "originals":'
        b' {"RF power": "20 W"}, "saved_utc": "t", "now": {"RF power": "100 W"}}\n'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return body


# --- refusals before anything is sent -------------------------------------------------


async def test_checks_without_a_terminal_exit_2_before_the_port(tmp_path) -> None:
    person = Person()

    async def factory():
        raise AssertionError("must not open the port")

    code = await execute(
        load(tmp_path, "ft991a", SPLIT),
        b"profile bytes",
        device_label="COM5",
        baud=38400,
        out=str(tmp_path / "probe.json"),
        include_private=False,
        label="",
        yes=True,
        is_tty=False,
        ask=person.ask,
        exchange_factory=factory,
        emit=person.emit,
        checks=True,
    )
    assert code == 2
    assert person.log == [("emit", render.checks_need_terminal_message())]
    assert not (tmp_path / "probe.json").exists()


def test_discovery_path_refuses_checks_without_a_terminal_before_scanning(
    monkeypatch, capsys
) -> None:
    import asyncio

    class NoTerminal:
        def isatty(self) -> bool:
            return False

    async def no_scan(**_kwargs):
        raise AssertionError("must not scan the ports")

    monkeypatch.setattr(_probe, "select_radio", no_scan)
    monkeypatch.setattr("sys.stdin", NoTerminal())
    args = build_probe_parser().parse_args(["--checks", "--yes"])
    assert asyncio.run(_probe._run_with_selection(args, {})) == 2
    assert capsys.readouterr().out == render.checks_need_terminal_message() + "\n"


def test_checks_flag_is_one_definition_for_both_entry_points() -> None:
    assert build_probe_parser().parse_args(["--checks"]).checks is True
    assert build_probe_parser().parse_args([]).checks is False
    parser = argparse.ArgumentParser()
    _probe.add_subparser(parser.add_subparsers())
    assert parser.parse_args(["probe", "--checks"]).checks is True


# --- consent ------------------------------------------------------------------------------


async def test_yes_never_answers_the_checks_question(tmp_path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person()
    try:
        code, out = await drive(tmp_path, config, radio, person, yes=True)
    finally:
        await radio.stop()
    assert code == 0
    assert person.asked(render.checks_prompt())
    assert not person.asked("Continue? [Y/n] ") and not person.asked(
        "Save the file? [Y/n] "
    )
    assert cat_writes(radio) == []
    assert saved(out).mode == "read" and saved(out).checks == ()


async def test_the_checks_question_defaults_to_no(tmp_path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person()  # Enter everywhere: start yes, checks NO, save yes
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 0 and person.asked(render.checks_prompt())
    assert not person.asked(render.check_prompt())
    assert cat_writes(radio) == []
    assert saved(out).mode == "read"


async def test_no_to_one_check_sends_no_write_and_shows_the_warning_first(
    tmp_path,
) -> None:
    config = load(tmp_path, "ft991a", WARNED)
    radio = await cat_radio()
    person = Person({render.checks_prompt(): "y"})
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 0 and cat_writes(radio) == []
    screen = render.check_screen(
        1,
        1,
        "Synthetic check",
        "Changes settings for a moment.",
        "Puts them back.",
        {"VFO-A": "14.074 MHz", "VFO-B": "14.25 MHz", "split": "off", "mode": "USB"},
        (POWER_SENTENCE,),
    )
    assert person.index("emit", screen) < person.index("ask", render.check_prompt())
    assert POWER_SENTENCE in screen
    (record,) = saved(out).checks
    assert record.status == "declined"
    assert ("emit", render.check_result_line(1, record)) in person.log


async def test_yes_to_a_check_restores_and_saves_the_record(tmp_path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person(
        {render.checks_prompt(): "y", render.check_prompt(): "y", "Look at": "yes"}
    )
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 0
    assert person.asked(render.check_question_prompt(QUESTION, 60.0))
    block = person.index("emit", "The read-only part is done.")
    last_progress = max(
        i for i, (k, t) in enumerate(person.log) if t.startswith("answered ")
    )
    assert last_progress < block < person.index("emit", "Check 1 of 1: ")
    assert person.log[block] == ("emit", render.checks_block_start(1))
    file = saved(out)
    (record,) = file.checks
    assert file.mode == "read+checks"
    assert (record.status, record.restored, record.verified) == ("ran", True, True)
    assert record.answers[0].answer == "yes"
    assert ("emit", "Check 1 done: settings put back and verified.") in person.log
    assert radio.frequency_hz == 14_074_000
    assert not journal_path().exists()


async def test_failed_restore_is_exit_4_with_the_pinned_line_first(tmp_path) -> None:
    config = load(tmp_path, "ft991a", POWER_ASK)
    radio = await cat_radio()
    radio.hold("PC", "020")

    def refuse_power() -> str:
        radio.reject("PC")  # the restore write is refused twice
        return ""

    person = Person(
        {
            render.checks_prompt(): "y",
            render.check_prompt(): "y",
            "Look at": refuse_power,
        }
    )
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 4
    after_question = person.index("ask", "Look at") + 1
    assert person.log[after_question] == ("emit", PINNED)
    (record,) = saved(out).checks
    assert record.restored is False
    assert ("emit", render.check_result_line(1, record)) in person.log
    assert leftover_journal_line(journal_path()) == PINNED


# --- leftover journal ---------------------------------------------------------------------


async def test_a_leftover_journal_is_printed_first_on_a_plain_probe(tmp_path) -> None:
    from rigplane.cli._probe import resolve_config

    body = write_leftover(journal_path())
    radio = await cat_radio()
    person = Person()
    try:
        code, _ = await drive(
            tmp_path, resolve_config("FT-991A", RIGS), radio, person, checks=False
        )
    finally:
        await radio.stop()
    assert code == 0
    assert person.log[:2] == [
        ("emit", PINNED),
        ("emit", render.journal_instruction_line(str(journal_path()))),
    ]
    assert cat_writes(radio) == [] and journal_path().read_bytes() == body


async def test_a_leftover_journal_refuses_the_checks_without_a_write(tmp_path) -> None:
    body = write_leftover(journal_path())
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person({render.checks_prompt(): "y", render.check_prompt(): "y"})
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 0 and person.log[0] == ("emit", PINNED)
    assert not person.asked(render.check_prompt())
    assert cat_writes(radio) == [] and journal_path().read_bytes() == body
    (record,) = saved(out).checks
    assert record.status == "refused"
    assert ("emit", render.check_result_line(1, record)) in person.log


@pytest.mark.parametrize("kind", ["garbage", "directory"])
async def test_an_unreadable_journal_prints_a_plain_line(tmp_path, kind) -> None:
    from rigplane.cli._probe import resolve_config

    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "garbage":
        path.write_bytes(b"not json at all")
    else:
        path.mkdir()
    radio = await cat_radio()
    person = Person()
    try:
        code, _ = await drive(
            tmp_path, resolve_config("FT-991A", RIGS), radio, person, checks=False
        )
    finally:
        await radio.stop()
    assert code == 0
    assert person.log[:2] == [
        ("emit", render.journal_unreadable_line(str(path))),
        ("emit", render.journal_instruction_line(str(path))),
    ]


async def test_a_profile_without_checks_runs_the_read_probe_only(tmp_path) -> None:
    from rigplane.cli._probe import resolve_config

    radio = await cat_radio()
    person = Person()
    try:
        code, out = await drive(
            tmp_path, resolve_config("FT-991A", RIGS), radio, person
        )
    finally:
        await radio.stop()
    assert code == 0
    assert ("emit", render.no_checks_message()) in person.log
    assert not person.asked(render.checks_prompt())
    assert saved(out).mode == "read"


# --- the CLI never builds a frame ---------------------------------------------------------


def test_the_cli_never_builds_or_judges_a_check_frame() -> None:
    tree = ast.parse((REPO / "src/rigplane/cli/_probe.py").read_text(encoding="utf-8"))
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names |= {alias.name for alias in node.names}
    assert not names & {"judged_frame", "build_frame", "compile_table", "parse_frame"}


# --- customer strings, exact --------------------------------------------------------------


def test_checks_block_start_text() -> None:
    assert render.checks_block_start(3) == (
        "The read-only part is done.\n"
        "Next there are 3 optional checks. Each one CHANGES one or more settings "
        "on your radio for a moment, asks the radio what it now says, and puts "
        "the settings back. None of them transmits, starts the antenna tuner or "
        "writes to the radio's memory channels. You can say no to any check.\n"
        'Do not touch the knobs or buttons of the radio until this program says "done".\n'
        "Nothing may key the radio while a check runs: no PTT, no CW key, no VOX "
        "or microphone PTT, no digital program.\n"
        "Do not close this window while a check is running: press Ctrl-C instead, "
        "the program then puts the settings back first.\n"
        "If you close this window anyway, the radio may be left changed. Run the "
        "program again: it will show the values to set back.\n"
        'If the cable comes out, set the values listed under "Right now" back by hand.'
    )
    assert "Next there is 1 optional check. Each" in render.checks_block_start(1)


def test_check_screen_text() -> None:
    assert render.check_screen(
        2,
        3,
        "cross-band split",
        "sets VFO-A to 7.074 MHz and VFO-B to 145.000 MHz, switches split on.",
        "puts VFO-A, VFO-B and split back exactly as they are now.",
        {"VFO-A": "14.2 MHz", "VFO-B": "14.25 MHz", "split": "off"},
        ("Switch off an amplifier first.",),
    ) == (
        "Check 2 of 3: cross-band split\n"
        "  What it does: sets VFO-A to 7.074 MHz and VFO-B to 145.000 MHz, "
        "switches split on.\n"
        "  Afterwards:   puts VFO-A, VFO-B and split back exactly as they are now.\n"
        "  Right now:    VFO-A 14.2 MHz, VFO-B 14.25 MHz, split off "
        "(these will be restored).\n"
        "  Never:        no transmit, no tuner, no memory channel write.\n"
        "  Switch off an amplifier first.\n"
        "  Ctrl-C at any time puts the settings back first."
    )


def test_prompts_and_one_line_messages() -> None:
    assert render.checks_prompt() == "Run the checks? [y/N] "
    assert render.check_prompt() == "Run this check? [y/N] "
    assert render.check_question_prompt("Is TX on VFO-B?", 60.0) == (
        "Look at the radio. Is TX on VFO-B? Type your answer, or press Enter to "
        "skip (it is skipped after 60 seconds): "
    )
    assert render.checks_need_terminal_message() == (
        "--checks asks you questions, so it needs a terminal window: "
        "run it there, not from a script or a pipe."
    )
    assert render.no_checks_message() == (
        "This radio has no checks yet. Running the read-only probe only."
    )
    assert render.journal_instruction_line("/s/j.json") == (
        "When the radio is set back, delete this file and run the program "
        "again: /s/j.json"
    )
    assert render.journal_unreadable_line("/s/j.json") == (
        "A check journal from an earlier run cannot be read: /s/j.json. "
        "Check the radio's settings by hand."
    )
    assert render.checks_done_line() == "The checks are done."


def _record(status: str, **kw) -> CheckRecord:
    return CheckRecord("c", "C", status, **kw)


def test_check_result_lines() -> None:
    line = render.check_result_line
    assert line(2, _record("ran")) == "Check 2 done: settings put back and verified."
    assert line(2, _record("ran", error="the radio did not accept split = 'on'")) == (
        "Check 2 done: settings put back and verified. It stopped early: "
        "the radio did not accept split = 'on'."
    )
    assert line(2, _record("ran", verified=False)) == (
        "Check 2 done: settings put back, but the radio now reports a different "
        "value than before (see the file)."
    )
    assert line(
        2,
        _record(
            "ran",
            restored=False,
            verified=False,
            error="could not put VFO-B back to 14.25 MHz; please set it by hand",
        ),
    ) == (
        "WARNING: could not put VFO-B back to 14.25 MHz; please set it by hand. "
        "This check never transmits."
    )
    assert line(1, _record("declined")) == "Check 1 skipped by you."
    assert line(1, _record("refused", error="precondition vfo not met.")) == (
        "Check 1 not run: precondition vfo not met. Nothing was changed."
    )


def test_pre_save_summary_carries_the_checks_line() -> None:
    records = (
        _record("ran", originals={"VFO-A": "14.2 MHz", "split": "off"}),
        _record(
            "ran",
            originals={"RF power": "20 W"},
            witness=CheckWitness({}, {}, []),
            verified=False,
        ),
        _record("declined"),
        _record("refused", error="x"),
        _record("ran", originals={"VFO-B": "1 MHz"}, restored=False, verified=False),
    )
    assert render.checks_summary_line(records) == (
        "  - checks: 5 offered (1 verified, 1 put back but read differently, "
        "1 NOT put back, 1 skipped by you, 1 not run); settings changed and put "
        "back: VFO-A, split, RF power; NOT put back, set by hand: VFO-B\n"
    )
    assert render.checks_summary_line(()) == ""
    assert render.checks_summary_line((_record("declined"),)) == (
        "  - checks: 1 offered (1 skipped by you); nothing was changed\n"
    )
    text = render.pre_save_summary(
        "/p", 10, 1, "unknown model", [], False, checks=records
    )
    assert render.checks_summary_line(records) in text


def test_start_screen_with_checks_does_not_claim_read_only() -> None:
    assert render.start_screen("FT-991A", "COM5 @ 38400", 140, checks=True) == (
        "RigPlane radio probe - read, then optional checks\n"
        "Radio profile: FT-991A    Port: COM5 @ 38400\n"
        "This run asks about 140 read questions, one at a time, "
        "usually under a minute.\n"
        "The read part changes nothing and never transmits; each optional check "
        "after it asks you first. Press Ctrl-C at any time to stop.\n"
        "Continue? [Y/n] "
    )
