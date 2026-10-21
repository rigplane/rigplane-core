"""Replay side of the Yaesu CAT recorder: a recorded request -> reply table.

The CAT sibling of :class:`rigplane_testkit.icom.replay.RecordedAnswerTable`
(which is keyed on CI-V bodies and cannot hold a text line). Pure and
socket-free: :meth:`RecordedCatAnswerTable.from_cat_pairs` pairs the
``c2r`` / ``r2c`` rows of a fixture and answers each recorded request the
way the real radio did — a data line, the ``?;`` refusal, or silence.

A Yaesu radio is strictly request/reply and the Core transport keeps one
query outstanding, so a reply belongs to the latest unanswered request. A
reply is attributed to it when :func:`reply_closes` says so (``?;`` or a
line that starts with the whole request body); any other line is unsolicited
(an auto-information push) and is not an answer. A request that the next
request overtakes without a reply is recorded as silence — the recorded
fact "this read timed out".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

REFUSAL = "?;"

# The one recorded exception to "a data reply starts with the whole request
# body": the radio answers the SUB S-meter read ``SM1;`` with the side digit
# echoed as 0 (rigs/ftx1.toml: get_s_meter_sub).
_ECHO_EXCEPTIONS = {"SM1;": "SM0"}


def reply_closes(request: str, reply: str) -> bool:
    """True when *reply* (with its ``;``) can be the radio's answer to *request*.

    A data reply starts with the whole request body (``NA1;`` -> ``NA1x;``) or
    is the ``?;`` refusal. A late reply to an earlier request that merely
    shares the two-letter verb (``NA00;`` for ``NA1;``) is not one.
    """
    return (
        reply == REFUSAL
        or reply.startswith(request[:-1])
        or reply.startswith(_ECHO_EXCEPTIONS.get(request, "\0"))
    )


@dataclass(frozen=True, slots=True)
class RecordedCatAnswer:
    """How the real radio answered one request line."""

    kind: str  # "data" | "refusal" | "silence"
    reply: str | None  # the reply line, ';' included; None for silence


class RecordedCatAnswerTable:
    """Recorded CAT request line -> :class:`RecordedCatAnswer`.

    The latest recording of a request wins, like the CI-V table. Requests
    are keyed on the full wire line (``MD0;``), so a read selector with a
    parameter (``RM7;`` versus ``RM8;``) is its own key.
    """

    def __init__(self) -> None:
        self._answers: dict[str, RecordedCatAnswer] = {}
        self.unsolicited: list[str] = []

    @classmethod
    def from_cat_pairs(
        cls, rows: Iterable[Mapping[str, Any]], window: str | None = None
    ) -> RecordedCatAnswerTable:
        """Build the table from fixture rows (``window``, ``dir``, ``line``).

        Only rows of *window* are used when it is given. Row order is
        recording order; ``t`` is not read here.
        """
        table = cls()
        pending: str | None = None
        for row in rows:
            if window is not None and row.get("window") != window:
                continue
            line = str(row["line"])
            if row["dir"] == "c2r":
                if pending is not None:
                    table._answers[pending] = RecordedCatAnswer("silence", None)
                pending = line
            elif row["dir"] == "r2c":
                if pending is not None and reply_closes(pending, line):
                    kind = "refusal" if line == REFUSAL else "data"
                    table._answers[pending] = RecordedCatAnswer(kind, line)
                    pending = None
                else:
                    table.unsolicited.append(line)
        if pending is not None:
            table._answers[pending] = RecordedCatAnswer("silence", None)
        return table

    def lookup(self, request: str) -> RecordedCatAnswer | None:
        """The recorded answer to *request* (with its ``;``), else None."""
        return self._answers.get(request)

    def requests(self) -> tuple[str, ...]:
        """Every recorded request line, in first-recorded order."""
        return tuple(self._answers)


__all__ = ["REFUSAL", "RecordedCatAnswer", "RecordedCatAnswerTable", "reply_closes"]
