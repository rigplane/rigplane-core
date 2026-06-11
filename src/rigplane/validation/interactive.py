"""Interactive operator prompting for the ``rigplane validate`` harness.

The validation harness has a class of *manual-perception* checks — ``audio.rx``,
``scope.capture``, ``bsr.select`` — that no software readback can confirm: only
a human watching the rig can say whether RX audio is audible, the scope is
sweeping, or the band display changed. Without an operator these resolve to
``MANUAL_REQUIRED`` (an honest "not verified") and never PASS/FAIL.

:class:`InteractivePrompter` turns those into a real PASS/FAIL by asking the
operator a yes/no question on the terminal and recording the answer. It is a
thin, dependency-free wrapper around an injectable ``input`` callable so tests
can feed canned answers without touching real stdin.

Two methods, deliberately:

* :meth:`InteractivePrompter.ask` — the general perception prompt. It honours
  ``assume_yes`` (auto-answer YES for unattended perception runs).
* :meth:`InteractivePrompter.confirm` — a stricter yes/no *gate* primitive that
  ALWAYS reads a real answer and IGNORES ``assume_yes``. MOR-666 reuses this for
  the pre-TX "type YES to transmit" gate, where an unattended auto-yes must
  never be able to key the transmitter. Keeping ``confirm`` immune to
  ``assume_yes`` is the safety boundary between perception checks and actuation.

This module imports only the standard library — it lives in the ``validation``
leaf layer and must not reach into the CLI, backends, or runtime.
"""

from __future__ import annotations

from collections.abc import Callable

__all__ = ["InteractivePrompter", "is_affirmative"]

# Inputs that count as an explicit "yes". Everything else (including an empty
# line) is "no": the prompts are phrased ``[y/N]`` so the safe default is no.
_AFFIRMATIVE = frozenset({"y", "yes"})


def is_affirmative(answer: str) -> bool:
    """Return ``True`` only for an explicit yes (``y``/``yes``, any case).

    Empty input, ``n``/``no``, or anything else is ``False`` — the perception
    prompts default to "no" so an ambiguous or blank answer never falsely PASSes
    a check or (via :meth:`InteractivePrompter.confirm`) authorizes an action.
    """
    return answer.strip().lower() in _AFFIRMATIVE


class InteractivePrompter:
    """Ask the operator yes/no questions on the terminal.

    Parameters
    ----------
    input_fn:
        The line-reader used to collect an answer. Defaults to the builtin
        :func:`input`; tests inject a callable returning canned strings so no
        real stdin is consumed.
    output_fn:
        Where the prompt text is written. Defaults to :func:`print`.
    assume_yes:
        When ``True``, :meth:`ask` returns ``True`` WITHOUT reading input —
        for unattended perception runs (``--assume-yes``). :meth:`confirm`
        ignores this flag and always reads a real answer, so it can never
        auto-authorize a TX/actuation gate.
    """

    def __init__(
        self,
        *,
        input_fn: Callable[[str], str] = input,
        output_fn: Callable[[str], None] = print,
        assume_yes: bool = False,
    ) -> None:
        self._input = input_fn
        self._output = output_fn
        self.assume_yes = assume_yes

    def ask(self, prompt: str) -> bool:
        """Print *prompt*, read one line, return ``True`` on an affirmative.

        With ``assume_yes`` set, returns ``True`` immediately without reading
        input (the prompt is still echoed so the run log shows what was
        auto-confirmed). Use this for perception checks only.
        """
        if self.assume_yes:
            self._output(prompt + " [auto-yes]")
            return True
        return is_affirmative(self._read(prompt))

    def confirm(self, prompt: str) -> bool:
        """Yes/no gate that ALWAYS reads a real answer (ignores ``assume_yes``).

        This is the reusable primitive for explicit-affirmative gates such as
        the MOR-666 pre-TX "type YES to transmit" confirmation: it returns
        ``True`` only when the operator types an affirmative, and ``--assume-yes``
        can never satisfy it.
        """
        return is_affirmative(self._read(prompt))

    def _read(self, prompt: str) -> str:
        """Echo *prompt* then read a line via the injected input function."""
        try:
            return self._input(prompt)
        except EOFError:
            # No more input (e.g. closed pipe): treat as a declined answer
            # rather than letting EOFError abort the whole validation run.
            return ""
