"""The fake IC-7300 over USB with the recorded answer table mounted.

``CivSerialFake(model="IC-7300")`` carries the profile-derived identity and the
native frequency/mode/PTT/S-meter/identity handlers; the recorded ``usb`` rows
of ``tests/fixtures/ic7300/civ_pairs.jsonl`` (bench IC-7300, 2026-10-21) answer
every other recorded request. The port echo comes from the table: the recorded
radio does not echo (menu "USB Echo Back" off).

``ic7300_profile_fake`` is the fake as ``serve --radio ic7300`` builds it, from
``rigplane_testkit/sim/profiles/ic7300.toml`` (MOR-3597): the same answer table
plus the profile's latency, baud gate and scope.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane_testkit.icom.replay import RecordedAnswerTable
from rigplane_testkit.serial import CivSerialFake

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "ic7300"


def ic7300_answer_table(fixtures: Path = FIXTURES) -> RecordedAnswerTable:
    rows = [
        json.loads(line)
        for line in (fixtures / "civ_pairs.jsonl").read_text().splitlines()
        if line
    ]
    return RecordedAnswerTable.from_civ_pairs(rows, "usb")


def ic7300_usb_fake(**kw) -> CivSerialFake:
    """``CivSerialFake(model="IC-7300")`` with the recorded USB table mounted."""
    return CivSerialFake(model="IC-7300", recorded_answers=ic7300_answer_table(), **kw)


def ic7300_profile_fake(**kw) -> CivSerialFake:
    """``CivSerialFake.from_profile`` over the repo's ``ic7300.toml``."""
    return CivSerialFake.from_profile(REPO_ROOT, **kw)
