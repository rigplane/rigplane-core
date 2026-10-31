"""The fake IC-705 over USB and LAN: recorded answer tables mounted.

"Over USB" here means the personality the bench IC-705's USB CI-V port
showed in the ``transport: "usb"`` rows of
``tests/fixtures/ic705/civ_pairs.jsonl``, mounted as a
:class:`~rigplane_testkit.icom.replay.RecordedAnswerTable` through
``CivSerialFake(recorded_answers=...)``. ``rigs/ic705.toml`` declares no
``[identity].expected_ids``, so the model id the recorded ``19 00`` reply
carries (``A4``) is passed explicitly. The port echo defaults to the
table's ``echo`` fact, read from the rows (off for the recorded USB rows:
on this port the radio does not echo requests). The conformance suite
(``tests/testkit/test_ic705_conformance.py``) builds its serial fake
through :func:`ic705_usb_fake`.

"Over LAN" is the same table built from the ``transport: "lan"`` rows,
mounted on :class:`~rigplane_testkit.icom.MockIcomRadio` through its
``recorded_answers`` keyword, with ``radio_addr=0xA4`` and the instance
``model_id`` set to the recorded ``A4`` (the native ``19 00`` handler
answers it). A single-receiver radio: ``dual_receiver`` stays at its
default False. The LAN CI-V echo (``lan_civ_echo``, default True) matches
the recording: on LAN the radio echoes every request byte-identical
before its reply.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial import CivSerialFake

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "ic705"

RADIO_ADDR = 0xA4
MODEL_ID = b"\xa4"


def _answer_table(transport: str) -> RecordedAnswerTable:
    """The recorded answer table of one civ_pairs transport."""
    rows = [
        json.loads(line)
        for line in (FIXTURES / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]
    return RecordedAnswerTable.from_civ_pairs(rows, transport)


def ic705_usb_fake(**kw) -> CivSerialFake:
    """``CivSerialFake(model="IC-705")`` with the recorded USB table mounted."""
    return CivSerialFake(
        model="IC-705",
        model_id=MODEL_ID,
        recorded_answers=_answer_table("usb"),
        **kw,
    )


def ic705_lan_fake(*, cls: type[MockIcomRadio] = MockIcomRadio, **kw) -> MockIcomRadio:
    """``MockIcomRadio(radio_addr=0xA4)`` with the recorded LAN table mounted.

    ``model_id`` is an instance attribute (the constructor does not take
    it); it is set to the recorded ``A4`` so the native ``19 00`` identity
    answer matches the recording. ``cls`` lets a test mount the same
    personality on a subclass that adds a state-dependent behaviour.
    """
    kw.setdefault("radio_addr", RADIO_ADDR)
    server = cls(recorded_answers=_answer_table("lan"), **kw)
    server.model_id = MODEL_ID
    return server
