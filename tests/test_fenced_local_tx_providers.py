from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.rig_loader import load_rig


_RIGS_DIR = Path(__file__).parents[1] / "rigs"


class FakeStreamReader:
    async def readuntil(self, separator: bytes) -> bytes:
        del separator
        raise asyncio.TimeoutError("no responses")


class FakeStreamWriter:
    def __init__(self) -> None:
        self.written: list[bytes] = []

    def write(self, data: bytes) -> None:
        self.written.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


class BlockingFirstDrainWriter(FakeStreamWriter):
    def __init__(self) -> None:
        super().__init__()
        self.first_drain_entered = asyncio.Event()
        self.release_first_drain = asyncio.Event()
        self._drain_count = 0

    async def drain(self) -> None:
        self._drain_count += 1
        if self._drain_count == 1:
            self.first_drain_entered.set()
            await self.release_first_drain.wait()


async def _connected_radio(
    monkeypatch: pytest.MonkeyPatch, writer: FakeStreamWriter
) -> YaesuCatRadio:
    serial_asyncio = MagicMock()
    serial_asyncio.open_serial_connection = AsyncMock(
        return_value=(FakeStreamReader(), writer)
    )
    monkeypatch.setitem(sys.modules, "serial_asyncio", serial_asyncio)
    config = load_rig(_RIGS_DIR / "ftx1.toml")
    radio = YaesuCatRadio("/dev/test", profile=config, audio_driver=MagicMock())
    await radio._transport.connect()
    radio._transport._drain_responses = AsyncMock(return_value=0)
    return radio


@pytest.mark.asyncio
async def test_yaesu_cw_suppresses_second_chunk_after_fence_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeStreamWriter()
    radio = await _connected_radio(monkeypatch, writer)
    fence = TxAbortFence()
    token = fence.issue()
    drains = 0

    async def poison_after_first_write(command: str) -> int:
        nonlocal drains
        del command
        drains += 1
        if drains == 1:
            await fence.force_off()
        return 0

    radio._transport._drain_responses = poison_after_first_write  # type: ignore[method-assign]

    with pytest.raises(CatTransportError, match="no longer current"):
        await radio.send_cw_text("A" * 25, is_current=lambda: fence.is_current(token))

    assert writer.written == [b"KY " + b"A" * 24 + b";"]


@pytest.mark.asyncio
async def test_yaesu_tuner_start_is_suppressed_after_queued_fence_poison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = BlockingFirstDrainWriter()
    radio = await _connected_radio(monkeypatch, writer)
    fence = TxAbortFence()
    token = fence.issue()

    blocker = asyncio.create_task(radio._transport.write("FA000000001;"))
    await writer.first_drain_entered.wait()
    tuner = asyncio.create_task(
        radio.set_tuner_status(1, is_current=lambda: fence.is_current(token))
    )
    await asyncio.sleep(0)
    await fence.force_off()
    writer.release_first_drain.set()

    await blocker
    with pytest.raises(CatTransportError, match="no longer current"):
        await tuner
    assert writer.written == [b"FA000000001;"]


@pytest.mark.asyncio
async def test_yaesu_direct_cw_and_tuner_calls_remain_unguarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writer = FakeStreamWriter()
    radio = await _connected_radio(monkeypatch, writer)

    await radio.send_cw_text("B" * 25)
    await radio.set_tuner_status(1)

    assert writer.written == [
        b"KY " + b"B" * 24 + b";",
        b"KY B;",
        b"AC001;",
    ]
