"""FTX-1 stored memory channels, read-only (MOR-3628): MR/MT without selecting.

The reply shapes are the real FTX-1's (MOR-3650 and the 2026-10-21 sweep) with
frequency and tag replaced: an unprogrammed channel answers ``?;``.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import (
    CatCommandRejected,
    CatGarbledFrameError,
    CatTimeoutError,
    YaesuCatTransport,
)
from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import MemoryReadCapable
from rigplane.core.types import (
    MemoryEntry,
    MemoryProfile,
    RepeaterShiftDirection,
    ToneSquelchType,
)
from rigplane.profiles import get_radio_profile
from rigplane.rig_loader import load_rig
from test_memory_list_api import _get

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_MR_25 = "MR00025146940000+000000412000"  # FM, memory, tone code 2, simplex
_MT_25 = "MT00025TEST RPT    "


def _radio(answers: dict[str, str]) -> YaesuCatRadio:
    """A connected FTX-1; a command missing from ``answers`` answers ``?;``."""
    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True

    async def query(command: str, **_: object) -> str:
        if command not in answers:
            raise CatCommandRejected("?;", command=command)
        return answers[command]

    radio._transport.query = AsyncMock(spec=YaesuCatTransport.query, side_effect=query)
    return radio


def test_the_shipped_profile_declares_a_numbered_channel_list() -> None:
    profile = get_radio_profile("FTX-1")
    assert profile.memory == MemoryProfile("channel", 0, 0, 1, 99)
    assert "memory_read" in profile.capabilities
    assert isinstance(_radio({}), MemoryReadCapable)


@pytest.mark.asyncio
async def test_reads_a_stored_channel_and_marks_an_unprogrammed_one_empty() -> None:
    radio = _radio({"MR00025;": _MR_25, "MT00025;": _MT_25})
    entries = await radio.read_memory_entries(0, [25, 26])
    assert entries == [
        MemoryEntry(
            group=0,
            channel=25,
            frequency_hz=146_940_000,
            mode="FM",
            duplex=RepeaterShiftDirection.SIMPLEX,
            tone_mode=ToneSquelchType.TONE,
            name="TEST RPT",
        ),
        MemoryEntry(group=0, channel=26, empty=True),
    ]
    sent = [call.args[0] for call in radio._transport.query.await_args_list]
    assert sent == ["MR00025;", "MT00025;", "MR00026;"]  # reads only, nothing selected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tone_code", "tone_mode"),
    [
        ("0", ToneSquelchType.OFF),
        ("1", ToneSquelchType.TSQL),
        ("2", ToneSquelchType.TONE),
        ("3", ToneSquelchType.DTCS),
        ("4", ToneSquelchType.PR_FREQ),
        ("5", ToneSquelchType.REV_TONE),
    ],
)
async def test_mr_tone_codes_are_decoded_with_the_mr_table(
    tone_code: str, tone_mode: ToneSquelchType
) -> None:
    mr = f"MR00025146940000+00000041{tone_code}000"
    entry = (
        await _radio({"MR00025;": mr, "MT00025;": _MT_25}).read_memory_entries(0, [25])
    )[0]
    assert entry.tone_mode == tone_mode


@pytest.mark.asyncio
async def test_shift_codes_and_a_missing_tag() -> None:
    plus = "MR00025146940000+000000412001"
    ars = "MR00025146940000+000000412003"  # the live IF answer for ARS; no stored direction
    radio = _radio({"MR00025;": plus})  # MT answers ?;
    entry = (await radio.read_memory_entries(0, [25]))[0]
    assert (entry.duplex, entry.name) == (RepeaterShiftDirection.PLUS, "")
    radio = _radio({"MR00025;": ars})
    assert (await radio.read_memory_entries(0, [25]))[0].duplex is None


def _mr(chan: str = "00025", mode: str = "4", tone: str = "2", shift: str = "0") -> str:
    return f"MR{chan}146940000+000000{mode}1{tone}00{shift}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "answer",
    [
        _mr(chan="00026"),  # another channel
        _mr(tone="9"),  # unknown MR tone code
        _mr(mode="J"),  # a mode letter the profile does not assign
    ],
)
async def test_an_unreadable_answer_is_a_command_error(answer: str) -> None:
    radio = _radio({"MR00025;": answer, "MT00025;": _MT_25})
    with pytest.raises(CommandError, match="unreadable memory channel 25"):
        await radio.read_memory_entries(0, [25])


@pytest.mark.asyncio
async def test_an_unknown_shift_digit_has_no_stored_direction() -> None:
    radio = _radio({"MR00025;": _mr(shift="9"), "MT00025;": _MT_25})
    assert (await radio.read_memory_entries(0, [25]))[0].duplex is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("group", "channel"), [(1, 25), (0, 0), (0, 100)])
async def test_a_slot_outside_the_profile_is_refused_before_the_wire(
    group: int, channel: int
) -> None:
    radio = _radio({})
    with pytest.raises(ValueError):
        await radio.read_memory_entries(group, [channel])
    radio._transport.query.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode_code", "mode_name", "data_mode"),
    [
        ("C", "DATA-U", True),
        ("8", "DATA-L", True),
        ("A", "DATA-FM", True),
        ("F", "DATA-FM-N", True),
        ("1", "LSB", False),
        ("2", "USB", False),
        ("3", "CW-U", False),
        ("4", "FM", False),
        ("5", "AM", False),
        ("6", "RTTY-L", False),
        ("7", "CW-L", False),
        ("9", "RTTY-U", False),
        ("B", "FM-N", False),
        ("D", "AM-N", False),
        ("E", "PSK", False),
        ("H", "C4FM-DN", False),
        ("I", "C4FM-VW", False),
    ],
)
async def test_the_memories_endpoint_lists_an_ftx1(
    mode_code: str, mode_name: str, data_mode: bool
) -> None:
    radio = _radio({"MR00025;": _mr(mode=mode_code), "MT00025;": _MT_25})
    status, body = await _get(radio, "/api/v1/memories?group=0&start=25&count=2")
    assert status == 200
    assert body["addressing"] == "channel"
    assert body["recall"] is False
    entry, empty = body["entries"]
    assert (entry["mode"], entry["dataMode"]) == (mode_name, data_mode)
    assert (entry["name"], entry["frequencyHz"], entry["toneMode"]) == (
        "TEST RPT",
        146_940_000,
        "tone",
    )
    assert empty == {"group": 0, "channel": 26, "empty": True}


@pytest.mark.asyncio
async def test_the_tag_must_belong_to_the_channel_that_was_asked_for() -> None:
    radio = _radio({"MR00025;": _MR_25, "MT00025;": "MT00026OTHER RPT   "})
    with pytest.raises(CommandError, match="MT answered channel '00026' for 25"):
        await radio.read_memory_entries(0, [25])


@pytest.mark.asyncio
@pytest.mark.parametrize("failing", ["MR00025;", "MT00025;"])
@pytest.mark.parametrize(
    "failure",
    [CatTimeoutError("no reply"), CatGarbledFrameError("garbled"), "truncated"],
)
async def test_transport_and_parse_failures_become_command_errors(
    failing: str, failure: Exception | str
) -> None:
    radio = _radio({"MR00025;": _MR_25, "MT00025;": _MT_25})
    answers = {"MR00025;": _MR_25, "MT00025;": _MT_25}

    async def query(command: str, **_: object) -> str:
        if command == failing:
            if isinstance(failure, str):
                return failure  # a truncated answer: the parser refuses it
            raise failure
        return answers[command]

    radio._transport.query = AsyncMock(spec=YaesuCatTransport.query, side_effect=query)
    with pytest.raises(CommandError, match="unreadable memory"):
        await radio.read_memory_entries(0, [25])


@pytest.mark.asyncio
async def test_the_endpoint_answers_a_radio_failure_with_a_read_error() -> None:
    radio = _radio({"MR00025;": "MR00025", "MT00025;": _MT_25})
    status, body = await _get(radio, "/api/v1/memories?group=0&start=25&count=1")
    assert (status, body["error"]) == (502, "memory_read_failed")
