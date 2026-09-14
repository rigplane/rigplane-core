"""MOR-1687 F2: AF/RF command responses export the server-admitted target.

Pins the additive wire contract: the optional ``admitted_level`` float in
``set_af_level``/``set_rf_power``/``set_power`` results, untouched legacy
keys, and its honest absence when no valid target exists.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.command_service import (
    admitted_level_for_intent,
    command_intent_from_request,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler

AF_QUANTIZED = 128 / 255
RAW_73 = 73 / 255
WATTS = {"power_native_unit": "watts", "power_max_watts": 100}


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


def _radio(*, model: str, native_power_unit: str | None = None) -> SimpleNamespace:
    profile = resolve_radio_profile(model=model)
    return SimpleNamespace(
        capabilities=set(profile.capabilities),
        profile=profile,
        model=profile.model,
        native_power_unit=native_power_unit or "raw_255",
        supports_command=MagicMock(return_value=True),
        set_rf_power=AsyncMock(),
        get_rf_power=AsyncMock(return_value=0),
        get_powerstat=AsyncMock(return_value=True),
        set_powerstat=AsyncMock(),
    )


def _handler(
    *, model: str, native_power_unit: str | None = None
) -> tuple[ControlHandler, _Ws, _QueueRecorder]:
    queue = _QueueRecorder()
    server = SimpleNamespace(command_queue=queue, command_state_store=None)
    ws = _Ws()
    handler = ControlHandler(
        ws,
        _radio(model=model, native_power_unit=native_power_unit),
        "test",
        model,
        server=server,
    )
    return handler, ws, queue


async def _dispatch(
    handler: ControlHandler, name: str, params: dict[str, object], cmd_id: str = "c1"
) -> dict[str, object]:
    await handler._dispatch_command(cmd_id, name, params)  # noqa: SLF001
    assert handler._ws.frames, "no response frame was sent"  # noqa: SLF001
    frame = handler._ws.frames[-1]  # noqa: SLF001
    assert frame["type"] == "response"
    return frame


async def test_set_af_level_response_exports_quantized_admitted_target() -> None:
    handler, _ws, _queue = _handler(model="IC-7300")
    frame = await _dispatch(handler, "set_af_level", {"level": 0.5, "receiver": 0})

    assert frame["ok"] is True
    assert frame["result"] == {
        "level": 128,
        "receiver": 0,
        "admitted_level": AF_QUANTIZED,
    }


async def test_set_af_level_raw_int_response_exports_admitted_target() -> None:
    handler, _ws, _queue = _handler(model="IC-7300")
    frame = await _dispatch(handler, "set_af_level", {"level": 73, "receiver": 1})

    assert frame["result"] == {"level": 73, "receiver": 1, "admitted_level": RAW_73}


async def test_set_power_raw255_alias_exports_admitted_target() -> None:
    handler, _ws, _queue = _handler(model="IC-7300", native_power_unit="raw_255")
    frame = await _dispatch(handler, "set_power", {"level": 128})

    assert frame["result"] == {"level": 128, "admitted_level": AF_QUANTIZED}


async def test_set_rf_power_watts_exports_admitted_target() -> None:
    handler, _ws, _queue = _handler(model="FTX-1", native_power_unit="watts")
    frame = await _dispatch(handler, "set_rf_power", {"level": 0.5})

    assert frame["result"] == {"level": 50, "admitted_level": 0.5}


async def test_set_rf_power_watts_raw_int_exports_admitted_target() -> None:
    handler, _ws, _queue = _handler(model="FTX-1", native_power_unit="watts")
    frame = await _dispatch(handler, "set_rf_power", {"level": 25})

    assert frame["result"] == {"level": 25, "admitted_level": 0.25}


async def test_error_and_unrelated_commands_keep_the_exact_legacy_shape() -> None:
    handler, _ws, _queue = _handler(model="IC-7300")
    failed = await _dispatch(handler, "set_af_level", {"level": 2.5, "receiver": 0})

    assert failed["ok"] is False
    assert set(failed) == {"type", "id", "ok", "error", "message"}

    rf_gain = await _dispatch(
        handler, "set_rf_gain", {"level": 73, "receiver": 0}, "c2"
    )
    assert rf_gain["result"] == {"level": 73, "receiver": 0}


@pytest.mark.parametrize(
    ("name", "params", "kwargs", "expected"),
    [
        ("set_af_level", {"level": 0.5, "receiver": 0}, {}, AF_QUANTIZED),
        ("set_af_level", {"level": 73, "receiver": 0}, {}, RAW_73),
        ("set_rf_power", {"level": 0.5}, WATTS, 0.5),
        ("set_rf_power", {"level": 25}, WATTS, 0.25),
        ("set_power", {"level": 128}, {}, AF_QUANTIZED),
    ],
)
def test_admitted_level_for_intent_exports_existing_normalization(
    name: str, params: dict[str, object], kwargs: dict[str, object], expected: float
) -> None:
    intent = command_intent_from_request(name, params, source="http", **kwargs)
    assert admitted_level_for_intent(intent) == expected


def test_admitted_level_for_intent_is_absent_without_a_valid_target() -> None:
    out_of_domain = command_intent_from_request(
        "set_rf_power", {"level": 300}, source="http"
    )
    assert admitted_level_for_intent(out_of_domain) is None
