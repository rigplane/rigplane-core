"""Replay side of the Yaesu CAT recorder: a recorded request -> reply table.

The CAT sibling of :class:`rigplane_testkit.icom.replay.RecordedAnswerTable`
(which is keyed on CI-V bodies and cannot hold a text line). Pure and
socket-free: :meth:`RecordedCatAnswerTable.from_cat_pairs` pairs the
``c2r`` / ``r2c`` rows of a fixture and answers each recorded request the
way the real radio did — a data line, the ``?;`` refusal, or silence.

A Yaesu radio is strictly request/reply and the Core transport keeps one
query outstanding, so a reply belongs to the latest unanswered request. A
reply is attributed to it when it is ``?;`` or carries the request's two
letter verb (``FA;`` -> ``FA014074000;``); any other line is unsolicited
(an auto-information push) and is not an answer. A request that the next
request overtakes without a reply is recorded as silence — the recorded
fact "this read timed out".
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

REFUSAL = "?;"


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
                if pending is not None and (line == REFUSAL or line[:2] == pending[:2]):
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


__all__ = ["REFUSAL", "RecordedCatAnswer", "RecordedCatAnswerTable"]
