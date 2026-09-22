"""set_filter_width responses expose the profile-admitted width (MOR-2533)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object, **_metadata: object) -> None:
        self.items.append(item)

    def put_ordered(self, item: object, **_metadata: object) -> None:
        self.items.append(item)


class _Ws:
    def __init__(self) -> None:
        self.frames: list[dict[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.frames.append(json.loads(payload))


def _store_with_mode(mode: str, receiver_id: str = "main") -> StateStore:
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.active(receiver_id, "freq_mode", "mode"),
            value=mode,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
        )
    )
    return store


def _handler(
    *, state_store: StateStore | None
) -> tuple[ControlHandler, _Ws, _QueueRecorder]:
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue, command_state_store=state_store)
    ws = _Ws()
    profile = resolve_radio_profile(model="FTX-1")
    radio = SimpleNamespace(
        capabilities=set(profile.capabilities),
        profile=profile,
        model=profile.model,
        supports_command=MagicMock(return_value=True),
    )
    handler = ControlHandler(ws, radio, "test", profile.model, server=server)
    return handler, ws, queue


async def _dispatch_width(handler: ControlHandler, width: int) -> dict[str, object]:
    await handler._dispatch_command(  # noqa: SLF001
        "c1", "set_filter_width", {"width": width, "receiver": 0}
    )
    assert handler._ws.frames, "no response frame was sent"  # noqa: SLF001
    frame = handler._ws.frames[-1]  # noqa: SLF001
    assert frame["type"] == "response"
    assert frame["ok"] is True
    return frame


async def test_set_filter_width_response_exports_admitted_width() -> None:
    # FTX-1 SSB table (rigs/ftx1.toml [filters.width.SSB]): the entry nearest
    # to 2350 is 2400 (50 Hz away; 2250 is 100 Hz away).
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {
        "width": 2350,
        "receiver": 0,
        "admitted_width": 2400,
    }


async def test_set_filter_width_on_table_value_admits_the_request() -> None:
    handler, _ws, _queue = _handler(state_store=_store_with_mode("USB"))

    frame = await _dispatch_width(handler, 2400)

    assert frame["result"] == {"width": 2400, "receiver": 0, "admitted_width": 2400}


async def test_set_filter_width_omits_admitted_width_without_an_observed_mode() -> None:
    handler, _ws, _queue = _handler(state_store=None)

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_omits_admitted_width_for_a_table_less_mode() -> None:
    # C4FM-DN is a live FTX-1 mode with no [filters.width] entry, so
    # RadioProfile.resolve_filter_rule returns None for it.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("C4FM-DN"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_omits_admitted_width_for_a_fixed_width_mode() -> None:
    # AM is a fixed-width FTX-1 row (rigs/ftx1.toml [filters.width.AM]); the
    # backend refuses the write there, so there is no admission to export.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("AM"))

    frame = await _dispatch_width(handler, 2350)

    assert frame["result"] == {"width": 2350, "receiver": 0}


async def test_set_filter_width_admission_uses_the_requested_receiver_mode() -> None:
    # SUB observed in CW-L (table tops out at 4000 with 2400 present, but the
    # nearest entry to 2350 is 2400 with a 50 Hz gap either way on the CW-L
    # lattice: 2000 is 350 Hz away); receiver=1 must read the sub-mode row.
    handler, _ws, _queue = _handler(state_store=_store_with_mode("CW-L", "sub"))
    await handler._dispatch_command(  # noqa: SLF001
        "c1", "set_filter_width", {"width": 2350, "receiver": 1}
    )
    frame = handler._ws.frames[-1]  # noqa: SLF001

    assert frame["result"] == {
        "width": 2350,
        "receiver": 1,
        "admitted_width": 2400,
    }
