"""Tests for the interactive operator-prompt primitive (MOR-667).

``InteractivePrompter`` wraps an injectable ``input`` callable so these tests
feed canned answers — no real stdin is ever consumed.
"""

from __future__ import annotations

from rigplane.validation.interactive import InteractivePrompter, is_affirmative


def _prompter(answers: list[str], *, assume_yes: bool = False):
    """Build a prompter that returns *answers* in order and records prompts."""
    seen: list[str] = []

    def _input(prompt: str) -> str:
        seen.append(prompt)
        return answers.pop(0)

    return (
        InteractivePrompter(
            input_fn=_input, output_fn=lambda _msg: None, assume_yes=assume_yes
        ),
        seen,
    )


def test_is_affirmative_only_on_yes() -> None:
    for yes in ("y", "Y", "yes", "YES", " Yes ", "yEs"):
        assert is_affirmative(yes) is True
    for no in ("", " ", "n", "no", "NO", "nope", "maybe", "1", "0"):
        assert is_affirmative(no) is False


def test_ask_true_on_yes() -> None:
    prompter, seen = _prompter(["y"])
    assert prompter.ask("hear audio? [y/N] ") is True
    assert seen == ["hear audio? [y/N] "]


def test_ask_false_on_no_and_empty() -> None:
    prompter, _ = _prompter(["n"])
    assert prompter.ask("hear audio? [y/N] ") is False
    prompter, _ = _prompter([""])
    assert prompter.ask("hear audio? [y/N] ") is False


def test_assume_yes_auto_answers_ask_without_reading_input() -> None:
    answers: list[str] = []  # popping would IndexError if input were read

    def _input(_prompt: str) -> str:  # pragma: no cover - must never run
        raise AssertionError("input must not be read under assume_yes")

    prompter = InteractivePrompter(
        input_fn=_input, output_fn=lambda _msg: None, assume_yes=True
    )
    assert prompter.ask("hear audio? [y/N] ") is True
    assert answers == []


def test_confirm_true_only_on_affirmative() -> None:
    prompter, _ = _prompter(["yes"])
    assert prompter.confirm("type yes to transmit: ") is True
    prompter, _ = _prompter(["n"])
    assert prompter.confirm("type yes to transmit: ") is False
    prompter, _ = _prompter([""])
    assert prompter.confirm("type yes to transmit: ") is False


def test_confirm_ignores_assume_yes_and_reads_real_answer() -> None:
    """The TX gate primitive (MOR-666) must never be satisfied by --assume-yes."""
    prompter, _ = _prompter(["n"], assume_yes=True)
    # assume_yes is set, but confirm still reads the canned "n" → False.
    assert prompter.confirm("type yes to transmit: ") is False
    prompter, _ = _prompter(["yes"], assume_yes=True)
    assert prompter.confirm("type yes to transmit: ") is True


def test_read_returns_no_on_eof() -> None:
    def _input(_prompt: str) -> str:
        raise EOFError

    prompter = InteractivePrompter(input_fn=_input, output_fn=lambda _msg: None)
    assert prompter.ask("hear audio? [y/N] ") is False
