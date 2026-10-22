"""MOR-3678: FTX-1 send_cw_text writes KM then plays it with KY.

FTX-1_CAT_OM_ENG_2508-C:
- KM KEYING MEMORY (printed p.17): ``KM P1 P2..P2;``, P1 keyer memory
  channel 1-5, P2 up to 50 characters.
- KY CW KEYING MEMORY PLAY (printed p.18): ``KY P1 P2;``, P1 0 CW TEXT /
  1 CW MESSAGE memory, P2 0 STOP / 1-5 playback; KY has no Read and no
  free-text form (command list, printed p.5).
The old ``KY CQ TEST;`` / ``KY ;`` frames (24-character keyboard chunks)
are invalid on the wire and must never be written.

Transport is mocked: nothing reaches a port, nothing is keyed.
"""

from __future__ import annotations

import asyncio
import dataclasses
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.rig_loader import load_rig

_RIG = Path(__file__).resolve().parents[1] / "rigs" / "ftx1.toml"
# Every KY frame the backend may emit, per printed p.18.
_VALID_KY = re.compile(r"KY[01][0-5];")


def _radio(*, supported: bool, memory_channel: int = 5) -> YaesuCatRadio:
    rig = load_rig(_RIG)
    rig = dataclasses.replace(
        rig,
        cw_text=dataclasses.replace(
            rig.cw_text, supported=supported, memory_channel=memory_channel
        ),
    )
    r = YaesuCatRadio("/dev/null", profile=rig)
    r._transport._connected = True  # noqa: SLF001
    r._transport.write = AsyncMock()  # noqa: SLF001
    r._transport.query = AsyncMock(return_value="")  # noqa: SLF001
    return r


def _writes(radio: YaesuCatRadio) -> list[str]:
    return [str(c.args[0]) for c in radio._transport.write.call_args_list]  # noqa: SLF001


@pytest.mark.asyncio
@pytest.mark.parametrize("supported", [True, False])
@pytest.mark.parametrize("text", ["CQ TEST", "CQ", ""])
async def test_send_cw_text_never_writes_a_free_text_ky_frame(
    text: str, supported: bool
) -> None:
    radio = _radio(supported=supported)
    try:
        await radio.send_cw_text(text)
    except (RuntimeError, ValueError):
        pass
    ky = [w for w in _writes(radio) if w.upper().startswith("KY")]
    assert [frame for frame in ky if not _VALID_KY.fullmatch(frame)] == []


@pytest.mark.asyncio
async def test_send_cw_text_writes_keyer_memory_then_plays_it() -> None:
    radio = _radio(supported=True)
    await radio.send_cw_text("CQ")
    # memory_type "text" -> KY P1 0; channel 5 from the profile.
    assert _writes(radio) == ["KM5CQ;", "KY05;"]


@pytest.mark.asyncio
async def test_stop_cw_text_stops_keyer_playback() -> None:
    radio = _radio(supported=True)
    await radio.stop_cw_text()
    assert _writes(radio) == ["KY00;"]


@pytest.mark.asyncio
async def test_send_cw_text_over_fifty_characters_is_rejected_before_any_write() -> (
    None
):
    radio = _radio(supported=True)
    with pytest.raises(
        ValueError,
        match=r"^CW text too long for FTX-1: got 51 characters, limit is 50$",
    ):
        await radio.send_cw_text("A" * 51)
    assert _writes(radio) == []


@pytest.mark.asyncio
async def test_disabled_send_cw_text_refuses_before_any_write() -> None:
    radio = _radio(supported=False)
    with pytest.raises(
        RuntimeError, match=r"^FTX-1 does not support sending CW text yet$"
    ):
        await radio.send_cw_text("CQ")
    assert _writes(radio) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["CQ;", "CQ\x00", "CQ\x1f", "C\nQ", "CQ\x7f"],
    ids=["semicolon", "nul", "us", "newline", "del"],
)
async def test_text_a_cat_parameter_cannot_carry_is_rejected_before_any_write(
    text: str,
) -> None:
    # CAT 2508-C general rules (printed p.4): no ASCII control codes and no
    # ";" inside a parameter. DEL (7Fh) is a control code too.
    radio = _radio(supported=True)
    with pytest.raises(ValueError, match="control characters"):
        await radio.send_cw_text(text)
    assert _writes(radio) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [1, 2, 3, 4, 5])
async def test_each_keyer_memory_channel_is_written_then_played(channel: int) -> None:
    radio = _radio(supported=True, memory_channel=channel)
    await radio.send_cw_text("CQ")
    assert _writes(radio) == [f"KM{channel}CQ;", f"KY0{channel};"]


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", [6, 10])
async def test_a_channel_km_cannot_address_is_refused_before_any_write(
    channel: int,
) -> None:
    # KM P1 is a single digit 1-5 (printed p.17): "KM10CQ;" would write
    # "0CQ" into channel 1.
    radio = _radio(supported=True, memory_channel=channel)
    with pytest.raises(RuntimeError, match=f"memory_channel {channel}"):
        await radio.send_cw_text("CQ")
    assert _writes(radio) == []


@pytest.mark.asyncio
async def test_concurrent_sends_keep_each_memory_write_with_its_playback() -> None:
    radio = _radio(supported=True)

    async def slow_write(command: str, **_kwargs: object) -> None:
        # Yield on every write so two sends would interleave without a lock.
        await asyncio.sleep(0)

    radio._transport.write.side_effect = slow_write  # noqa: SLF001
    await asyncio.gather(radio.send_cw_text("FIRST"), radio.send_cw_text("SECOND"))
    assert _writes(radio) == ["KM5FIRST;", "KY05;", "KM5SECOND;", "KY05;"]


@pytest.mark.asyncio
async def test_message_memory_type_plays_and_stops_the_message_memory() -> None:
    # KY P1 1 = CW MESSAGE memory (printed p.18).
    rig = load_rig(_RIG)
    rig = dataclasses.replace(
        rig,
        cw_text=dataclasses.replace(rig.cw_text, supported=True, memory_type="message"),
    )
    radio = YaesuCatRadio("/dev/null", profile=rig)
    radio._transport._connected = True  # noqa: SLF001
    radio._transport.write = AsyncMock()  # noqa: SLF001
    radio._transport.query = AsyncMock(return_value="")  # noqa: SLF001
    await radio.send_cw_text("CQ")
    await radio.stop_cw_text()
    assert _writes(radio) == ["KM5CQ;", "KY15;", "KY10;"]


@pytest.mark.asyncio
async def test_empty_text_is_rejected_before_any_write() -> None:
    radio = _radio(supported=True)
    with pytest.raises(ValueError, match="^CW text is empty$"):
        await radio.send_cw_text("")
    assert _writes(radio) == []
