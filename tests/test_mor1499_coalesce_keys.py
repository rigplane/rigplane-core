"""RED/GREEN tests for MOR-1499: the MOR-1427 coalescing key must include the
receiver and (for selector-type commands) the selected target, not just the
bare command NAME.

Bugs (both verifier-proven by inspection of
``ControlHandler._coalesce_command`` / ``_cmd_pending``, keyed by
``command_name`` alone):

1. Frontend preset flow (``panel-commands.ts`` ``onFilterPresetChange``)
   dispatches ``set_filter(A)``, ``set_filter_width(W)``, ``set_filter(B)``
   in a single tick. Because both ``set_filter`` frames share the same
   NAME-only key, the second silently supersedes the first inside the
   pacing window -- "switch filter -> write width -> switch back" can
   collapse to just the final switch, with the width written against
   whichever filter was actually active on the radio at that moment.

2. Cross-receiver: ``set_nb`` on MAIN followed by ``set_nb`` on SUB inside
   the pacing window superseded MAIN's frame with SUB's -- MAIN's toggle
   never reached the radio.

Fix under test: the coalescing key (``ControlHandler._coalesce_key``) always
includes the receiver, and for discrete-target SELECTOR commands
(``set_filter``, ``set_vfo``/``select_vfo``, ``set_band`` --
``_COALESCE_TARGET_PARAM``) also includes the selected target. Continuous
value-carrying commands (levels, offsets, gains, ...) deliberately do NOT
key off their value -- last-value-wins across DIFFERENT values remains the
whole point of coalescing them (MOR-1427's original purpose), pinned here
too so the fix does not regress it.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rigplane.web.handlers import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import SetFilter, SetIfShift, SetNB

pytestmark = pytest.mark.asyncio


def _control_handler(
    ws: object | None = None,
    radio: object | None = None,
    server: object | None = None,
) -> ControlHandler:
    if ws is None:
        ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(ws, radio, "9.9.9", "IC-7610", server=server)


class _QueueRecorder:
    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _sent_messages(ws: SimpleNamespace) -> list[dict[str, Any]]:
    return [decode_json(c.args[0]) for c in ws.send_text.await_args_list]


def _messages_by_id(ws: SimpleNamespace) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for msg in _sent_messages(ws):
        out[msg["id"]] = msg
    return out


async def _await_flush(handler: ControlHandler, key: str) -> None:
    task = handler._cmd_flush_tasks.get(key)  # noqa: SLF001
    if task is not None:
        await task


# ---------------------------------------------------------------------------
# (a) set_filter with DIFFERENT targets in the pacing window: both switches
#     must reach the radio -- neither supersedes the other.
# ---------------------------------------------------------------------------


async def test_set_filter_distinct_targets_both_reach_radio() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    # Mirrors onFilterPresetChange: switch to FIL1, then switch back to FIL2,
    # dispatched back-to-back in the same pacing window.
    await handler._handle_command(
        {
            "id": "switch-to-1",
            "name": "set_filter",
            "params": {"filter": "FIL1", "receiver": 0},
        }
    )
    await handler._handle_command(
        {
            "id": "switch-to-2",
            "name": "set_filter",
            "params": {"filter": "FIL2", "receiver": 0},
        }
    )

    # Distinct targets -> distinct coalescing keys -> both dispatch
    # immediately, neither is superseded.
    filter_writes = [item for item in queue.items if isinstance(item, SetFilter)]
    assert [w.filter_num for w in filter_writes] == [1, 2]

    by_id = _messages_by_id(ws)
    assert by_id["switch-to-1"]["ok"] is True
    assert by_id["switch-to-1"]["result"] == {"filter": "FIL1", "receiver": 0}
    assert by_id["switch-to-2"]["ok"] is True
    assert by_id["switch-to-2"]["result"] == {"filter": "FIL2", "receiver": 0}
    assert by_id["switch-to-1"]["result"] != {"superseded": True}
    assert by_id["switch-to-2"]["result"] != {"superseded": True}


# ---------------------------------------------------------------------------
# (b) set_filter with the SAME target repeated rapidly still coalesces --
#     the per-target discrimination must not defeat MOR-1427's original
#     purpose for repeated identical selections.
# ---------------------------------------------------------------------------


async def test_set_filter_same_target_still_coalesces() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    for i in range(3):
        await handler._handle_command(
            {
                "id": f"click-{i}",
                "name": "set_filter",
                "params": {"filter": "FIL1", "receiver": 0},
            }
        )

    # First click dispatches immediately (opens the window); the 2nd is
    # superseded by the 3rd, which is the one that flushes.
    filter_writes = [item for item in queue.items if isinstance(item, SetFilter)]
    assert len(filter_writes) == 1
    key = "set_filter:0:'FIL1'"
    await _await_flush(handler, key)
    filter_writes = [item for item in queue.items if isinstance(item, SetFilter)]
    assert len(filter_writes) == 2
    assert all(w.filter_num == 1 for w in filter_writes)

    by_id = _messages_by_id(ws)
    assert by_id["click-0"]["result"] == {"filter": "FIL1", "receiver": 0}
    assert by_id["click-1"]["result"] == {"superseded": True}
    assert by_id["click-2"]["result"] == {"filter": "FIL1", "receiver": 0}


# ---------------------------------------------------------------------------
# (c) Cross-receiver: set_nb on MAIN then SUB inside the pacing window --
#     both must reach the radio, MAIN's toggle must not be superseded by
#     SUB's.
# ---------------------------------------------------------------------------


async def test_set_nb_cross_receiver_both_reach_radio() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    # Canonical dual-RX VFO methods (DualReceiverCapable) so the "dual_rx"
    # capability tag isn't stripped by runtime_capabilities()'s isinstance
    # check, and receiver=1 (SUB) passes _ensure_receiver_supported.
    radio = SimpleNamespace(
        connected=True,
        capabilities={"nb", "dual_rx"},
        swap_main_sub=AsyncMock(),
        equalize_main_sub=AsyncMock(),
        set_main_sub_tracking=AsyncMock(),
        get_main_sub_tracking=AsyncMock(return_value=False),
    )
    handler = _control_handler(
        ws=ws, radio=radio, server=SimpleNamespace(command_queue=queue)
    )

    await handler._handle_command(
        {"id": "main-on", "name": "set_nb", "params": {"on": True, "receiver": 0}}
    )
    await handler._handle_command(
        {"id": "sub-on", "name": "set_nb", "params": {"on": True, "receiver": 1}}
    )

    nb_writes = [item for item in queue.items if isinstance(item, SetNB)]
    assert len(nb_writes) == 2
    assert {(w.on, w.receiver) for w in nb_writes} == {(True, 0), (True, 1)}

    by_id = _messages_by_id(ws)
    assert by_id["main-on"]["result"] != {"superseded": True}
    assert by_id["sub-on"]["result"] != {"superseded": True}


# ---------------------------------------------------------------------------
# (d) Regression pin: value-carrying commands on the SAME receiver still
#     coalesce across DIFFERENT values -- last-value-wins, MOR-1427's
#     original purpose, unaffected by the MOR-1499 key-shape change.
# ---------------------------------------------------------------------------


async def test_slider_command_still_coalesces_across_different_values() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    radio = SimpleNamespace(connected=True, capabilities={"if_shift"})
    handler = _control_handler(
        ws=ws, radio=radio, server=SimpleNamespace(command_queue=queue)
    )

    n = 5
    for i in range(n):
        await handler._handle_command(
            {
                "id": str(i),
                "name": "set_if_shift",
                "params": {"offset": i * 10, "receiver": 0},
            }
        )

    # Only the first frame enqueues synchronously.
    assert len([item for item in queue.items if isinstance(item, SetIfShift)]) == 1
    key = "set_if_shift:0"
    await _await_flush(handler, key)

    writes = [item for item in queue.items if isinstance(item, SetIfShift)]
    assert len(writes) == 2
    assert writes[0].offset == 0
    assert writes[-1].offset == (n - 1) * 10

    by_id = _messages_by_id(ws)
    for i in range(1, n - 1):
        assert by_id[str(i)]["result"] == {"superseded": True}
