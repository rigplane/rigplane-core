"""Memory recall over USB, end to end into the fake radio (MOR-4219).

The owner's IC-705 over USB: RECALL on the memory list sent
``set_memory_mode {"group": 0, "channel": 4}``, the radio acknowledged both
frames and stayed where it was. The recall sent ``08 A0 0000`` (select the
memory group) and ``08 0004`` (select the memory channel) and never the bare
``08``, "Select the Memory mode" -- selecting a channel does not leave the VFO
mode (the IC-7300 manual selects the channel to store into while in the VFO
mode, and the fake below follows that).

Each test enters where a client does: an HTTP ``POST /api/v1/commands`` or a
WebSocket ``cmd`` reaches the real ``WebServer`` handler, the command queue,
the ``RadioPoller`` executor and the serial radio Core builds from the CLI
options (``--model``/``--serial-port``), whose link writes to the testkit
``CivSerialFake``. Only the OS open step is replaced; no real port is opened
and no real radio is touched.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from rigplane.backends.factory import create_radio
from rigplane.cli import _build_backend_config, _build_parser
from rigplane.commands import build_civ_frame
from rigplane.core import serial_open
from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.serial import CivSerialFake
from support.command_consumer import run_command_consumer
from test_web_split_writes import _request

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened
_ANSWER_WINDOW_S = 0.2  # compressed CI-V answer window (production: 2.0 s)
_MEMORY_FREQ_HZ = 145_500_000
_MEMORY_MODE_FM = 0x05  # 04/06 mode byte FM (CI-V guide mode table)
_VFO_FREQ_HZ = 14_074_000

# model -> (fake keywords, recall params, the 08 frames on the wire)
_CASES: dict[str, tuple[dict[str, Any], dict[str, int], list[str]]] = {
    "IC-705": (
        {"model_id": b"\xa4"},  # rigs/ic705.toml declares no expected_ids
        {"group": 0, "channel": 4},
        ["fefea4e008a00000fd", "fefea4e0080004fd", "fefea4e008fd"],
    ),
    "IC-7300": (
        {},
        {"group": 0, "channel": 4},
        ["fefe94e0080004fd", "fefe94e008fd"],
    ),
}


def _memory_frames(fake: CivSerialFake) -> list[str]:
    """Every ``08`` request the fake heard, rebuilt as wire hex."""
    return [
        build_civ_frame(c.to_addr, c.from_addr, c.command, data=c.data).hex()
        for c in fake.commands
        if c.command == 0x08
    ]


@contextlib.asynccontextmanager
async def _station(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[WebServer, Any, CivSerialFake, list[Any], asyncio.Task[None]]]:
    fake_kw, params, _frames = _CASES[model]
    fake = CivSerialFake(model=model, frequency_hz=_VFO_FREQ_HZ, **fake_kw)
    fake.store_memory(
        params["group"], params["channel"], _MEMORY_FREQ_HZ, _MEMORY_MODE_FM
    )

    async def opener(*, url: str, baudrate: int, **kw: Any) -> Any:
        return await fake.opener(url=url, baudrate=baudrate, **kw)

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", opener)
    args = _build_parser().parse_args(
        ["--model", model, "--serial-port", _DEVICE, "status"]
    )
    radio = create_radio(await _build_backend_config(args))
    radio._civ_get_timeout = _ANSWER_WINDOW_S
    radio._enumerate_serial_ports_fn = lambda: []  # no OS port enumeration
    await asyncio.wait_for(radio.connect(), 30)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    entries: list[Any] = []

    consumer = asyncio.create_task(
        run_command_consumer(
            web.command_queue, poller._execute_queued_entry, entries=entries, count=1
        )
    )
    try:
        yield web, radio, fake, entries, consumer
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await radio.disconnect()


@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize("model", sorted(_CASES))
async def test_recall_puts_the_radio_on_the_memory_channel(
    model: str, protocol: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kw, params, frames = _CASES[model]
    async with _station(model, monkeypatch) as (web, radio, fake, entries, consumer):
        assert fake.memory_mode is False
        assert _memory_frames(fake) == []  # connect sends no memory frame

        status, result = await _request(web, protocol, "set_memory_mode", params)
        await asyncio.wait_for(consumer, 5)

        assert status == (200 if protocol == "http" else None)
        assert result["ok"] is True
        assert len(entries) == 1
        # The exact bytes, in order: selects first, the Memory mode last.
        assert _memory_frames(fake) == frames
        # The radio ends in the Memory mode on the requested channel ...
        assert fake.memory_mode is True
        assert fake.memory_slot == (params["group"], params["channel"])
        # ... and the next poll reads the memory's frequency and mode.
        assert fake.frequency_hz == _MEMORY_FREQ_HZ
        assert await radio.get_freq() == _MEMORY_FREQ_HZ
        mode, _filter = await radio.get_mode()
        assert mode == "FM"


@pytest.mark.parametrize("model", sorted(_CASES))
async def test_a_refused_recall_reaches_the_client_as_an_error(
    model: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_kw, params, frames = _CASES[model]
    async with _station(model, monkeypatch) as (web, _radio, fake, entries, consumer):
        fake.refuse_next(0x08)  # the radio NAKs the first memory frame

        status, result = await _request(web, "http", "set_memory_mode", params)
        with pytest.raises(CommandRejectedError, match="rejected memory recall"):
            await asyncio.wait_for(consumer, 5)

        assert status == 409
        assert len(entries) == 1
        assert result["ok"] is False
        assert result["error"] == "radio_nak"
        assert result["message"] == (
            f"Radio rejected memory recall {params['group']}/{params['channel']}"
        )
        assert _memory_frames(fake) == frames[:1]  # nothing after the NAK
        assert fake.memory_mode is False
        assert fake.frequency_hz == _VFO_FREQ_HZ
