"""The fake IC-7610 over USB and LAN: recorded answer tables mounted.

"Over USB" here means the personality the bench IC-7610's USB CI-V port
showed in the ``transport: "usb"`` rows of
``tests/fixtures/ic7610/civ_pairs.jsonl``, mounted as a
:class:`~rigplane_testkit.icom.replay.RecordedAnswerTable` through
``CivSerialFake(recorded_answers=...)``. The native handlers still answer the
requests they handle (the recorded rows include unwrapped ``03``, ``15 02``
and ``19 00`` reads), so not every recorded request goes through the table;
the order in which the handlers and the table are consulted is in the module
docstring of ``rigplane_testkit/serial/civ_serial.py``. The port echo
defaults to the table's ``echo`` fact, read from the rows (off for the
recorded USB rows). The conformance suite
(``tests/testkit/test_ic7610_conformance.py``) builds its serial fake through
:func:`ic7610_usb_fake`.

"Over LAN" is the same table built from the ``transport: "lan"`` rows,
mounted on :class:`~rigplane_testkit.icom.MockIcomRadio` through its
``recorded_answers`` keyword: the LAN simulator answers the recorded
requests its native CI-V handlers NAK, while the native answers, the LAN
CI-V echo and the ``refused_civ_commands`` hook keep precedence (the mount's
answer order is pinned by ``tests/testkit/test_simulator_recorded_answers.py``
and measured at scale by the conformance answer-class replay through
:func:`ic7610_lan_fake`).
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial import CivSerialFake

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic7610"


def _answer_table(transport: str) -> RecordedAnswerTable:
    """The recorded answer table of one civ_pairs transport."""
    rows = [
        json.loads(line)
        for line in (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]
    return RecordedAnswerTable.from_civ_pairs(rows, transport)


def ic7610_usb_fake(**kw) -> CivSerialFake:
    """``CivSerialFake(model="IC-7610")`` with the recorded USB table mounted."""
    return CivSerialFake(model="IC-7610", recorded_answers=_answer_table("usb"), **kw)


def ic7610_lan_fake(**kw) -> MockIcomRadio:
    """``MockIcomRadio()`` with the recorded LAN table mounted."""
    return MockIcomRadio(recorded_answers=_answer_table("lan"), **kw)
