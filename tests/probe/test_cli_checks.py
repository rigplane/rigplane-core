"""``rigplane probe --checks``: the CLI wiring of the write checks (MOR-4502).

Runs drive the testkit ``FakeCatRadio`` (FT-991A) through the production
serial exchange. Most tests call ``execute()`` with a scripted person; the
reader tests type on a real pseudo-terminal (and press a real Ctrl-C); the
entry tests go through ``probe_main`` with stdin/stdout on that terminal.
The synthetic checks come from the engine tests' loader helper; the journal
lives in a temporary state folder (``state_dir``).
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import os
import pty
import re
import select
import signal
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import platformdirs
import pytest

import rigplane.cli as cli_module
from rigplane.backends.discovery import RadioDiscoveryResult
from rigplane.cli import _probe, probe_main
from rigplane.cli._probe import _TerminalLines, build_probe_parser, execute
from rigplane.probe import render
from rigplane.probe.journal import journal_path, leftover_journal_line
from rigplane.probe.model import CheckRecord, CheckWitness, ProbeFile
from rigplane.profiles.rig_loader import discover_rigs
from tests.probe.test_checks_engine import (
    SPLIT,
    cat_exchange,
    cat_radio,
    check_toml,
    load,
)

REPO = Path(__file__).resolve().parents[2]
RIGS = discover_rigs(REPO / "rigs")
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
WITNESSED = check_toml(
    [
        'kind = "write"\nentry = "power"\nvalue = 100',
        'kind = "ask"\nquestion = "Did the meter move?"',
    ],
    ["power"],
    'witness = ["freq_a"]',
)
SECOND_POWER = """
[[probe.checks]]
id = "second"
title = "Second check"
explain_what = "Raises the RF power for a moment."
explain_after = "Puts it back."
[[probe.checks.steps]]
kind = "write"
entry = "power"
value = 100
[[probe.checks.restore]]
entry = "power"
"""
CAT_WRITE = re.compile(r"(FA|FB)\d{9};|FT[23];|PC\d{3};|MD0\w;")
PINNED = (
    "A check could not put the radio back: RF power is now 100 W, it was 20 W. "
    "Set it back by hand before transmitting."
)


@pytest.fixture(autouse=True)
def state_dir(tmp_path_factory, monkeypatch) -> Path:
    """The checks journal lives in a temporary folder, never the user's."""
    state = tmp_path_factory.mktemp("state")
    monkeypatch.setattr(platformdirs, "user_state_path", lambda app: state / app)
    return state / "rigplane"


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

    async def lines(self, prompt: str) -> str | None:
        return self.ask(prompt)

    def emit(self, text: str) -> None:
        self.log.append(("emit", text))

    def asked(self, prompt: str) -> bool:
        return ("ask", prompt) in self.log

    def said(self, text: str) -> bool:
        return any(text in t for k, t in self.log if k == "emit")

    def index(self, kind: str, start: str) -> int:
        return next(
            i for i, (k, t) in enumerate(self.log) if k == kind and t.startswith(start)
        )


class Terminal:
    """A real pseudo-terminal. The program uses the slave side; a thread
    reads the screen and, for each new appearance of a rule's prompt, types
    the rule's answer (a callable runs first; ``None`` types nothing)."""

    def __init__(self, rules: dict[str, str | Callable[[], str | None]]):
        self.master, self.slave = pty.openpty()
        self.rules, self.seen = rules, Counter[str]()
        self.screen = ""
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def _watch(self) -> None:
        while not self._stop.is_set():
            if not select.select([self.master], [], [], 0.02)[0]:
                continue
            try:
                self.screen += os.read(self.master, 4096).decode(errors="replace")
            except OSError:
                return
            for prompt, action in self.rules.items():
                while self.screen.count(prompt) > self.seen[prompt]:
                    self.seen[prompt] += 1
                    text = action() if callable(action) else action
                    if text is not None:
                        os.write(self.master, text.encode())

    def write(self, text: str) -> None:
        os.write(self.slave, text.encode())

    def lines(self) -> _TerminalLines:
        return _TerminalLines(self.slave, self.write)

    def close(self) -> None:
        self._stop.set()
        self._thread.join()
        os.close(self.master)
        os.close(self.slave)


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
    lines=None,
    gap_s: float = 0.0,
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
        gap_s=gap_s,
        timeout_s=0.2,
        emit=person.emit,
        checks=checks,
        check_lines=lines or person.lines,
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


async def test_checks_on_windows_run_the_read_probe_only(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(_probe, "_HAS_TERMINAL_READER", False)  # as on Windows
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person()
    try:
        out = tmp_path / "probe.json"
        code = await execute(
            config,
            b"profile bytes",
            device_label="COM5",
            baud=38400,
            out=str(out),
            include_private=False,
            label="",
            yes=True,
            is_tty=True,
            ask=person.ask,
            exchange_factory=lambda: cat_exchange(radio),
            gap_s=0.0,
            timeout_s=0.2,
            emit=person.emit,
            checks=True,
        )
    finally:
        await radio.stop()
    assert code == 0 and person.said(render.checks_not_on_windows_message())
    assert saved(out).mode == "read" and cat_writes(radio) == []


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


async def test_a_leftover_journal_refuses_the_checks_without_a_write(tmp_path) -> None:
    body = write_leftover(journal_path())
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()
    person = Person({render.checks_prompt(): "y", render.check_prompt(): "y"})
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    assert code == 0
    assert not person.asked(render.check_prompt())
    assert cat_writes(radio) == [] and journal_path().read_bytes() == body
    (record,) = saved(out).checks
    assert record.status == "refused"
    assert ("emit", render.check_result_line(1, record)) in person.log


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


# --- what the person is told after a check: only what the engine confirmed ------------


def _summary(person: Person) -> str:
    return next(
        t for k, t in person.log if k == "emit" and t.startswith("Ready to save")
    )


async def test_a_rejected_restore_is_exit_4_with_the_pinned_line_first(
    tmp_path,
) -> None:
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
    summary = _summary(person)
    assert "NOT put back, set by hand: RF power" in summary
    assert "put back (confirmed)" not in summary and "current settings" not in summary
    assert not person.said("put back and verified")


async def test_a_differing_restore_readback_is_not_called_put_back(tmp_path) -> None:
    config = load(tmp_path, "ft991a", POWER_ASK)
    radio = await cat_radio()
    radio.hold("PC", "020")

    def drift() -> str:
        # the restore write of 020 is accepted but stores 050: the read-back differs
        radio.hold("PC", "100", {"020": "050"})
        return ""

    person = Person(
        {render.checks_prompt(): "y", render.check_prompt(): "y", "Look at": drift}
    )
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    (record,) = saved(out).checks
    assert code == 4 and (record.restored, record.verified) == (True, False)
    assert person.said(render.check_result_line(1, record))
    assert "did not confirm" in render.check_result_line(1, record)
    summary = _summary(person)
    assert "sent back but not confirmed, check them on the radio: RF power" in summary
    assert "put back (confirmed)" not in summary
    assert "settings at the start of this run" in summary
    assert not person.said("put back and verified") and not person.said(PINNED)
    assert person.said(render.journal_instruction_line(str(journal_path())))


async def test_missing_witness_data_never_claims_a_different_value(tmp_path) -> None:
    config = load(tmp_path, "ft991a", WITNESSED)
    radio = await cat_radio()
    radio.hold("PC", "020")

    def mute_witness() -> str:
        radio.reject("FA")  # the witness read after the restore gets no data
        return ""

    person = Person(
        {
            render.checks_prompt(): "y",
            render.check_prompt(): "y",
            "Look at": mute_witness,
        }
    )
    try:
        code, out = await drive(tmp_path, config, radio, person)
    finally:
        await radio.stop()
    (record,) = saved(out).checks
    assert code == 4 and (record.restored, record.verified) == (True, False)
    assert record.witness.after.get("freq_a") is None
    assert person.said(render.check_result_line(1, record))
    assert not person.said("different value") and not person.said("put back and")


# --- the terminal reader: one answer per prompt, Ctrl-C reaches the engine -------------


@pytest.mark.realtime(reason="a real pseudo-terminal and a real question time-out")
async def test_a_late_answer_to_a_timed_out_question_never_answers_the_next() -> None:
    term = Terminal({})
    try:
        lines = term.lines()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(lines("Did the meter move? "), 0.1)
        os.write(term.master, b"yes\n")  # typed after the question expired
        await asyncio.sleep(0.1)
        consent = asyncio.ensure_future(lines(render.check_prompt()))
        await asyncio.sleep(0.2)
        assert not consent.done()
        os.write(term.master, b"n\n")
        assert await asyncio.wait_for(consent, 5) == "n"
    finally:
        term.close()


@pytest.mark.realtime(
    reason="a real pseudo-terminal, a real question time-out and a late answer"
)
async def test_two_checks_a_late_yes_does_not_authorize_the_second(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(_probe, "ASK_TIMEOUT_S", 0.3)
    config = load(tmp_path, "ft991a", SPLIT + SECOND_POWER)
    radio = await cat_radio()
    radio.hold("PC", "020")
    consents = iter(["y\n", "n\n"])

    def too_late() -> str:
        time.sleep(0.4)  # past the 0.3 s limit, while check 1 is restoring
        return "yes\n"

    term = Terminal(
        {
            render.checks_prompt(): "y\n",
            render.check_prompt(): lambda: next(consents),
            "Look at the radio.": too_late,
        }
    )
    try:
        code, out = await drive(
            tmp_path, config, radio, Person(), lines=term.lines(), gap_s=0.05
        )
    finally:
        term.close()
        await radio.stop()
    first, second = saved(out).checks
    assert code == 0 and first.answers[0].skipped
    assert (first.restored, first.verified) == (True, True)
    assert second.status == "declined"
    assert term.seen[render.check_prompt()] == 2
    assert "PC100;" not in radio.commands


@pytest.mark.skipif(os.name != "posix", reason="POSIX signals (Windows: MOR-4497)")
@pytest.mark.realtime(reason="a real SIGINT on a real pseudo-terminal, bounded in time")
async def test_ctrl_c_at_the_check_question_ends_with_no_write(tmp_path) -> None:
    config = load(tmp_path, "ft991a", SPLIT)
    radio = await cat_radio()

    def ctrl_c() -> None:
        os.kill(os.getpid(), signal.SIGINT)  # no newline typed
        return None

    term = Terminal({render.checks_prompt(): "y\n", render.check_prompt(): ctrl_c})
    started = time.monotonic()
    try:
        code, out = await asyncio.wait_for(
            drive(tmp_path, config, radio, Person(), yes=True, lines=term.lines()), 60
        )
    finally:
        term.close()
        await radio.stop()
    # the Ctrl-C itself ends the wait, not the 60 s safety limit above
    assert time.monotonic() - started < 30
    (record,) = saved(out).checks
    assert code == 0
    assert (record.status, record.error) == ("refused", "cancelled before any write")
    assert cat_writes(radio) == [] and not journal_path().exists()


# --- through the real entry point (probe_main) --------------------------------------------


def rigs_with(tmp_path: Path, toml: str, monkeypatch) -> None:
    """The shipped FT-991A plus *toml*, as the only profile the CLI finds."""
    rigs = tmp_path / "rigs"
    rigs.mkdir()
    load(rigs, "ft991a", toml)
    monkeypatch.setattr(cli_module, "_rigs_dir", lambda: rigs)


async def on_terminal(term: Terminal, monkeypatch, argv: list[str]) -> int:
    """``probe_main`` with stdin and stdout on the pseudo-terminal."""
    monkeypatch.setattr("sys.stdin", open(term.slave, closefd=False))
    monkeypatch.setattr("sys.stdout", open(term.slave, "w", closefd=False))
    # the fake radio answers on this loop; probe_main runs its own
    return await asyncio.wait_for(asyncio.to_thread(probe_main, argv), 120)


async def test_entry_named_port_checks_run_write_restore_and_save(
    tmp_path, monkeypatch
) -> None:
    rigs_with(tmp_path, SPLIT, monkeypatch)
    radio = await cat_radio()
    out = tmp_path / "probe.json"
    term = Terminal(
        {
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
            "Look at the radio.": "yes\n",
        }
    )
    argv = ["FT-991A", "--port", radio.device_url, "--checks", "--yes"]
    try:
        code = await on_terminal(term, monkeypatch, [*argv, "--out", str(out)])
    finally:
        term.close()
        await radio.stop()
    (record,) = saved(out).checks
    assert code == 0 and (record.status, record.restored, record.verified) == (
        "ran",
        True,
        True,
    )
    assert record.answers[0].answer == "yes"
    assert "FT3;" in radio.commands and "FT2;" in radio.commands  # write, restore


async def test_entry_named_port_failed_restore_is_exit_4(tmp_path, monkeypatch) -> None:
    rigs_with(tmp_path, POWER_ASK, monkeypatch)
    radio = await cat_radio()
    radio.hold("PC", "020")
    out = tmp_path / "probe.json"

    def refuse_power() -> str:
        radio.reject("PC")
        return "\n"

    term = Terminal(
        {
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
            "Look at the radio.": refuse_power,
        }
    )
    argv = ["FT-991A", "--port", radio.device_url, "--checks", "--yes"]
    try:
        code = await on_terminal(term, monkeypatch, [*argv, "--out", str(out)])
    finally:
        term.close()
        await radio.stop()
    (record,) = saved(out).checks
    assert code == 4 and record.restored is False
    assert "PC100;" in radio.commands and PINNED in term.screen


async def _failed_restore_on_terminal(tmp_path, monkeypatch, out: Path, save: str):
    """A check whose restore is refused, run through probe_main on a terminal
    without --yes; *save* answers the save question. Returns the exit code."""
    rigs_with(tmp_path, POWER_ASK, monkeypatch)
    # No opener stub: the suite guard fails any test that reaches the real
    # opener, and neither save answer below reaches it (declined / unwritable).
    radio = await cat_radio()
    radio.hold("PC", "020")

    def refuse_power() -> str:
        radio.reject("PC")
        return "\n"

    term = Terminal(
        {
            "Continue? [Y/n] ": "\n",
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
            "Look at the radio.": refuse_power,
            "Save the file? [Y/n] ": save,
        }
    )
    argv = ["FT-991A", "--port", radio.device_url, "--checks", "--out", str(out)]
    try:
        return await on_terminal(term, monkeypatch, argv)
    finally:
        term.close()
        await radio.stop()


async def test_entry_failed_restore_then_save_declined_is_still_exit_4(
    tmp_path, monkeypatch
) -> None:
    out = tmp_path / "probe.json"
    code = await _failed_restore_on_terminal(tmp_path, monkeypatch, out, "n\n")
    assert code == 4 and not out.exists()
    assert leftover_journal_line(journal_path()) == PINNED


async def test_entry_failed_restore_then_save_fails_is_still_exit_4(
    tmp_path, monkeypatch
) -> None:
    out = tmp_path / "no-such-folder" / "probe.json"
    code = await _failed_restore_on_terminal(tmp_path, monkeypatch, out, "y\n")
    assert code == 4 and not out.exists()
    assert leftover_journal_line(journal_path()) == PINNED


async def test_entry_unreadable_journal_never_reaches_the_saved_file(
    tmp_path, monkeypatch
) -> None:
    import getpass

    rigs_with(tmp_path, SPLIT, monkeypatch)
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"not json at all")
    radio = await cat_radio()
    out = tmp_path / "probe.json"
    term = Terminal(
        {
            render.journal_set_back_prompt(): "\n",  # keep it
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
        }
    )
    argv = ["FT-991A", "--port", radio.device_url, "--checks", "--yes"]
    try:
        code = await on_terminal(term, monkeypatch, [*argv, "--out", str(out)])
    finally:
        term.close()
        await radio.stop()
    text = out.read_text(encoding="utf-8")
    (record,) = saved(out).checks
    assert code == 0 and record.status == "refused"
    assert record.error == render.journal_unreadable_line(None)
    assert str(path) in term.screen  # the console still names the file
    for private in (str(path), str(path.parent), str(Path.home()), getpass.getuser()):
        assert private not in text
    assert cat_writes(radio) == []


async def test_entry_discovery_checks_run_write_restore_and_save(
    tmp_path, monkeypatch
) -> None:
    rigs_with(tmp_path, SPLIT, monkeypatch)
    radio = await cat_radio()
    out = tmp_path / "probe.json"

    async def found():
        return [
            RadioDiscoveryResult(
                port=radio.device_url,
                protocol="yaesu_cat",
                model="FT-991A",
                profile_id="yaesu_ft991a",
                baudrate=38400,
                address="0670",
            )
        ]

    monkeypatch.setattr(_probe, "discover_serial_radios", found)
    term = Terminal(
        {
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
            "Look at the radio.": "yes\n",
        }
    )
    try:
        code = await on_terminal(
            term, monkeypatch, ["--checks", "--yes", "--out", str(out)]
        )
    finally:
        term.close()
        await radio.stop()
    (record,) = saved(out).checks
    assert code == 0 and (record.status, record.restored) == ("ran", True)
    assert "FT3;" in radio.commands and "FT2;" in radio.commands


async def _no_radio():
    return []


async def _scan_fails():
    raise OSError("device vanished")


@pytest.mark.parametrize(
    ("argv", "scan", "code"),
    [
        ([], _no_radio, 3),
        ([], _scan_fails, 3),
        (["--port", "COM5"], _no_radio, 2),
    ],
    ids=["no-radio", "scan-fails", "usage-error"],
)
def test_entry_prints_the_leftover_journal_first_whatever_stops_it(
    argv, scan, code, monkeypatch, capsys
) -> None:
    body = write_leftover(journal_path())
    monkeypatch.setattr(_probe, "discover_serial_radios", scan)
    assert probe_main(argv) == code
    lines = capsys.readouterr().out.splitlines()
    assert lines[:2] == [
        PINNED,
        render.journal_instruction_line(str(journal_path())),
    ]
    assert journal_path().read_bytes() == body


async def _leftover_on_terminal(tmp_path, monkeypatch, set_back: str):
    """A leftover journal and a --checks run on a terminal; *set_back* answers
    the question about it. Returns (code, record, radio, screen, body)."""
    rigs_with(tmp_path, SPLIT, monkeypatch)
    body = write_leftover(journal_path())
    radio = await cat_radio()
    out = tmp_path / "probe.json"
    term = Terminal(
        {
            render.journal_set_back_prompt(): set_back,
            render.checks_prompt(): "y\n",
            render.check_prompt(): "y\n",
            "Look at the radio.": "yes\n",
        }
    )
    argv = ["FT-991A", "--port", radio.device_url, "--checks", "--yes"]
    try:
        code = await on_terminal(term, monkeypatch, [*argv, "--out", str(out)])
    finally:
        term.close()
        await radio.stop()
    (record,) = saved(out).checks
    return code, record, radio, term.screen, body


async def test_entry_yes_to_set_back_removes_the_journal_and_checks_run(
    tmp_path, monkeypatch
) -> None:
    code, record, radio, screen, _ = await _leftover_on_terminal(
        tmp_path, monkeypatch, "y\n"
    )
    assert screen.index(PINNED) < screen.index(render.journal_set_back_prompt())
    assert code == 0 and record.status == "ran" and "FT3;" in radio.commands
    assert not journal_path().exists()


async def test_entry_enter_at_set_back_keeps_the_journal_and_no_check_writes(
    tmp_path, monkeypatch
) -> None:
    code, record, radio, screen, body = await _leftover_on_terminal(
        tmp_path, monkeypatch, "\n"
    )
    assert render.journal_set_back_prompt() in screen
    assert code == 0 and record.status == "refused" and cat_writes(radio) == []
    assert journal_path().read_bytes() == body


async def test_entry_failing_to_remove_a_leftover_journal_is_a_readable_error(
    monkeypatch, capsys
) -> None:
    """MOR-4542: a yes to the set-back prompt whose unlink fails (a
    directory in the journal's place, a read-only state folder) must end
    with a one-line error naming the file and exit code 1 -- not a
    traceback -- and the journal must stay intact."""

    def undeletable(path):
        raise PermissionError(13, "Permission denied")

    body = write_leftover(journal_path())
    monkeypatch.setattr(_probe, "remove_journal", undeletable)
    term = Terminal({render.journal_set_back_prompt(): "y\n"})
    try:
        code = await on_terminal(term, monkeypatch, [])
    finally:
        term.close()
    assert code == 1
    assert journal_path().read_bytes() == body
    err = capsys.readouterr().err
    assert "could not remove the check journal" in err
    assert str(journal_path()) in err


@pytest.mark.parametrize("kind", ["garbage", "directory"])
def test_entry_unreadable_journal_prints_a_plain_line(
    kind, monkeypatch, capsys
) -> None:
    path = journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "garbage":
        path.write_bytes(b"not json at all")
    else:
        path.mkdir()
    monkeypatch.setattr(_probe, "discover_serial_radios", _no_radio)
    assert probe_main([]) == 3
    assert capsys.readouterr().out.splitlines()[:2] == [
        render.journal_unreadable_line(str(path)),
        render.journal_instruction_line(str(path)),
    ]


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
        "on your radio for a moment, asks the radio what it now says, then sets "
        "them back and reads them again to confirm. If the radio does not take a "
        "setting back, this program tells you what to set by hand. None of them "
        "transmits, starts the antenna tuner or writes to the radio's memory "
        "channels. You can say no to any check.\n"
        'Do not touch the knobs or buttons of the radio until this program says "done".\n'
        "Do not transmit while a check runs: no PTT, no CW key, no VOX, "
        "no digital-mode program.\n"
        "Do not close this window while a check is running. Press Ctrl-C instead; "
        "the program then tries to put the settings back before it stops.\n"
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
        "(the check will try to set these back).\n"
        "  Never:        no transmit, no tuner, no memory channel write.\n"
        "  Switch off an amplifier first.\n"
        "  Ctrl-C at any time: the program tries to put the settings back, then stops."
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
    assert render.checks_not_on_windows_message() == (
        "--checks does not work on Windows yet. Running the read-only probe only."
    )
    assert render.no_checks_message() == (
        "This radio has no checks yet. Running the read-only probe only."
    )
    assert render.journal_instruction_line("/s/j.json") == (
        "When the radio is set back, delete this file and run the program "
        "again: /s/j.json"
    )
    assert render.journal_unreadable_line("/s/j.json") == (
        "The file with the values an earlier check should have set back cannot "
        "be read: /s/j.json. Check the radio's settings by hand."
    )
    assert render.journal_unreadable_line(None) == (
        "The file with the values an earlier check should have set back cannot "
        "be read. Check the radio's settings by hand."
    )
    assert render.journal_set_back_prompt() == (
        "Are these set back on the radio now? [y/N] "
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
        "Check 2 done: the settings were sent back, but the radio did not confirm "
        "them (see the file). Check them on the radio before transmitting."
    )
    assert line(
        2,
        _record(
            "ran",
            restored=False,
            verified=False,
            error="could not put VFO-B back to 14.25 MHz",
        ),
    ) == (
        "WARNING: could not put VFO-B back to 14.25 MHz. "
        "Please set it by hand before you transmit."
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
        "  - checks: 5 offered (1 verified, 1 sent back but not confirmed, "
        "1 NOT put back, 1 skipped by you, 1 not run); settings changed and put "
        "back (confirmed): VFO-A, split; sent back but not confirmed, check them "
        "on the radio: RF power; NOT put back, set by hand: VFO-B\n"
    )
    assert render.checks_summary_line(()) == ""
    assert render.checks_summary_line((_record("declined"),)) == (
        "  - checks: 1 offered (1 skipped by you); nothing was changed\n"
    )
    text = render.pre_save_summary(
        "/p", 10, 1, "unknown model", ["mode USB"], False, checks=records
    )
    assert render.checks_summary_line(records) in text
    assert "  - your radio's settings at the start of this run: mode USB\n" in text
    assert "current settings" not in text


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
