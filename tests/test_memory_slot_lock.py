"""Memory-slot operations share one lock across backends (MOR-3768)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatCommandRejected, YaesuCatTransport
from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.types import CivFrame
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from test_memory_recall import _radio as _icom_recall_radio
from test_radio import MockTransport

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


def _icom_radio(model: str = "IC-705") -> IcomRadio:
    return _icom_recall_radio(model, MockTransport())


@pytest.mark.asyncio
async def test_grouped_recall_never_interleaves_memory_write() -> None:
    radio = _icom_radio()
    order: list[str] = []

    async def fake_expect(civ: bytes, **_: object) -> CivFrame:
        payload = civ[4:-1]
        if len(payload) >= 2 and payload[0] == 0x08 and payload[1] == 0xA0:
            order.append("group")
            await asyncio.sleep(0)
        elif payload and payload[0] == 0x08:
            order.append("channel")
        return CivFrame(CONTROLLER_ADDR, 0xA4, 0x00, None, b"", 0x00)

    async def fake_fire_and_forget(_civ: bytes) -> None:
        order.append("memory_write")

    radio._send_civ_expect = fake_expect  # type: ignore[method-assign]
    radio._send_fire_and_forget = fake_fire_and_forget  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            radio.set_memory_mode(4, group=12),
            radio.memory_write(),
        )
    finally:
        radio._connected = False
    assert order == ["group", "channel", "memory_write"]


@pytest.mark.asyncio
async def test_grouped_recall_never_interleaves_channel_only_select() -> None:
    radio = _icom_radio()
    order: list[str] = []

    async def fake_expect(civ: bytes, **_: object) -> CivFrame:
        order.append("group")
        await asyncio.sleep(0)
        return CivFrame(CONTROLLER_ADDR, 0xA4, 0x00, None, b"", 0x00)

    async def fake_fire_and_forget(_civ: bytes) -> None:
        order.append("channel_select")

    radio._send_civ_expect = fake_expect  # type: ignore[method-assign]
    radio._send_fire_and_forget = fake_fire_and_forget  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            radio.set_memory_mode(4, group=12),
            radio.set_memory_mode(7),
        )
    finally:
        radio._connected = False
    assert order == ["group", "channel", "channel_select"]


@pytest.mark.asyncio
async def test_ftx1_memory_reads_do_not_interleave_across_callers() -> None:
    from test_ftx1_memory_read import _MR_25, _MT_25

    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True
    order: list[str] = []
    mr_steps = 0

    async def query(read_cmd: str, **_: object) -> str:
        nonlocal mr_steps
        order.append(read_cmd)
        if read_cmd.startswith("MR"):
            mr_steps += 1
            if mr_steps == 1:
                await asyncio.sleep(0)
        if read_cmd == "MR00025;":
            return _MR_25
        if read_cmd == "MT00025;":
            return _MT_25
        raise CatCommandRejected("?;", command=read_cmd)

    radio._transport.query = AsyncMock(spec=YaesuCatTransport.query, side_effect=query)
    try:
        await asyncio.gather(
            radio.read_memory_entries(0, [25]),
            radio.read_memory_entries(0, [26]),
        )
    finally:
        radio._transport._connected = False
    assert order == ["MR00025;", "MT00025;", "MR00026;"]
