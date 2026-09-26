"""MOR-2636: set_narrow web command, capability gate, and FTX-1 CAT bytes."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import SetNarrow
from rigplane.web.handlers import ControlHandler


def _handler(radio: object, server: object) -> ControlHandler:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "FTX-1", server=server)


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object, **_metadata: object) -> None:
        self.items.append(item)


def _server() -> tuple[SimpleNamespace, _QueueRecorder]:
    queue = _QueueRecorder()
    return SimpleNamespace(command_queue=queue), queue


def _ftx1_radio() -> YaesuCatRadio:
    radio = YaesuCatRadio("/dev/null", profile="ftx1")
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    return radio


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("on", "receiver", "expected"),
    [
        (True, 0, "NA01;"),
        (False, 0, "NA00;"),
        (True, 1, "NA11;"),
    ],
)
async def test_set_narrow_writes_the_ftx1_cat_bytes(
    on: bool, receiver: int, expected: str
) -> None:
    """The web command reaches YaesuCatRadio.set_narrow and the CAT write.

    Templates are rigs/ftx1.toml set_narrow (NA0{state};) and
    set_narrow_sub (NA1{state};).
    """
    radio = _ftx1_radio()
    poller = YaesuCatPoller(radio, callback=lambda _state: None, fast_interval=10.0)

    await poller._execute_command(SetNarrow(on, receiver=receiver))

    radio._transport.write.assert_awaited_once_with(expected)


@pytest.mark.asyncio
async def test_set_narrow_refused_without_the_narrow_capability() -> None:
    """A profile that does not declare ``narrow`` raises the same ValueError
    as the other capability gates."""
    server, _queue = _server()
    radio = SimpleNamespace(
        capabilities=set(),
        profile=resolve_radio_profile(model="IC-7300"),
        supports_command=MagicMock(return_value=False),
    )
    handler = _handler(radio, server)

    with pytest.raises(ValueError, match="missing capability: narrow"):
        await handler._enqueue_command("set_narrow", {"on": True, "receiver": 0})


@pytest.mark.asyncio
async def test_set_narrow_enqueues_the_typed_intent() -> None:
    server, queue = _server()
    radio = SimpleNamespace(
        capabilities={"narrow"},
        profile=resolve_radio_profile(model="FTX-1"),
        supports_command=MagicMock(return_value=True),
    )
    handler = _handler(radio, server)

    result = await handler._enqueue_command("set_narrow", {"on": False, "receiver": 1})

    assert result == {"on": False, "receiver": 1}
    assert queue.items == [SetNarrow(False, receiver=1)]


def test_enqueue_helpers_are_awaitable() -> None:
    assert asyncio.iscoroutinefunction(ControlHandler._enqueue_command)
