"""The fake IC-7610 over USB: ``CivSerialFake`` with the recorded answer table.

"Over USB" here means the personality the bench IC-7610's USB CI-V port
showed in the recorded ``usb_session`` window of
``tests/fixtures/ic7610/civ_pairs.jsonl``: every recorded request is answered
with the recorded reply class through
:class:`~rigplane_testkit.icom.replay.RecordedAnswerTable`, and the port echo
is off (the table's ``echo`` fact, read from the rows). The conformance suite
(``tests/testkit/test_ic7610_conformance.py``) builds its serial fake through
:func:`ic7610_usb_fake`, and the row set is loaded fresh on each call so a
monkeypatched fixture path in a mutation test cannot leak between tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial import CivSerialFake

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"


def _usb_answer_table() -> RecordedAnswerTable:
    rows = [
        json.loads(line)
        for line in (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]
    return RecordedAnswerTable.from_civ_pairs(rows, "usb")


def ic7610_usb_fake(**kw) -> CivSerialFake:
    """``CivSerialFake(model="IC-7610")`` with the recorded USB table mounted."""
    return CivSerialFake(model="IC-7610", recorded_answers=_usb_answer_table(), **kw)
