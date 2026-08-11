"""RED/GREEN tests for MOR-1427: rate-limited SET commands must coalesce
(last-value-wins) instead of being silently dropped.

Bug (live-measured on the RC stand): ``ControlHandler._handle_command``'s
per-command rate limiter hard-drops any ``set_*`` command arriving within
``_CMD_MIN_INTERVAL`` (50ms) of the previous same-name command on the same
session, while still ACKing the dropped frame with ``ok: true``. A 10-frame
incrementing ``set_freq`` burst applied only 1 value and silently lost 9.

Fix under test: a command arriving inside the pacing window is coalesced
(last-value-wins) rather than dropped. The pacing itself (one physical
enqueue per ``_CMD_MIN_INTERVAL``) is preserved. A frame that gets replaced
before it is flushed receives an honest ``{ok: true, result: {superseded:
true}}`` reply instead of the old ``{throttled: true}`` fiction; the frame
that is actually flushed gets the real enqueue ack.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rigplane.web.handlers import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import PttOff, SetFreq

pytestmark = pytest.mark.asyncio


class _QueueRecorder:
    """Mirrors the recorder used in tests/test_handlers_coverage.py."""

    def __init__(self) -> None:
        self.items: list[object] = []

    def put(self, item: object) -> None:
        self.items.append(item)


def _control_handler(
    ws: object | None = None,
    radio: object | None = None,
    server: object | None = None,
    session_id: str | None = None,
) -> ControlHandler:
    if ws is None:
        ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    return ControlHandler(
        ws,
        radio,
        "9.9.9",
        "IC-7610",
        server=server,
        session_id=session_id,
    )


def _sent_messages(ws: SimpleNamespace) -> list[dict[str, Any]]:
    return [decode_json(c.args[0]) for c in ws.send_text.await_args_list]


def _messages_by_id(ws: SimpleNamespace) -> dict[str, dict[str, Any]]:
    """Last message observed for each command id (each id should get exactly one)."""
    out: dict[str, dict[str, Any]] = {}
    for msg in _sent_messages(ws):
        out[msg["id"]] = msg
    return out


async def _await_flush(handler: ControlHandler, name: str) -> None:
    """Wait for the deferred coalesced flush (if any) for *name* to complete."""
    task = handler._cmd_flush_tasks.get(name)  # noqa: SLF001
    if task is not None:
        await task


# ---------------------------------------------------------------------------
# (a) Burst of N incrementing targets -> pacing-allowed enqueue count,
#     final enqueued value is the LAST target.
# ---------------------------------------------------------------------------


async def test_burst_coalesces_to_last_value_not_dropped() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    n = 10
    base_freq = 14_000_000
    for i in range(n):
        await handler._handle_command(
            {"id": str(i), "name": "set_freq", "params": {"freq": base_freq + i}}
        )

    # Only the first frame is enqueued synchronously (paces the window open).
    assert len(queue.items) == 1
    assert isinstance(queue.items[0], SetFreq)
    assert queue.items[0].freq == base_freq

    await _await_flush(handler, "set_freq")

    # Exactly one more physical enqueue happens at the paced flush, carrying
    # the LAST target in the burst — never lost, never any of the middle ones.
    assert len(queue.items) == 2
    assert isinstance(queue.items[1], SetFreq)
    assert queue.items[1].freq == base_freq + n - 1


# ---------------------------------------------------------------------------
# (b) Superseded frames get the honest reply shape; applied frames get the
#     real ack. Every frame gets exactly one reply.
# ---------------------------------------------------------------------------


async def test_superseded_frames_get_honest_reply_flushed_frame_gets_real_ack() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    n = 10
    base_freq = 14_000_000
    for i in range(n):
        await handler._handle_command(
            {"id": str(i), "name": "set_freq", "params": {"freq": base_freq + i}}
        )
    await _await_flush(handler, "set_freq")

    by_id = _messages_by_id(ws)
    assert set(by_id) == {str(i) for i in range(n)}

    # id "0" was dispatched immediately — real ack, no superseded marker.
    assert by_id["0"]["ok"] is True
    assert by_id["0"]["result"] == {"freq": base_freq, "receiver": 0}

    # ids "1".."8" were each replaced by a newer frame before they could
    # flush — honest supersede reply, never claims unqualified success.
    for i in range(1, n - 1):
        msg = by_id[str(i)]
        assert msg["ok"] is True
        assert msg["result"] == {"superseded": True}

    # id "9" (the last target) is the one actually flushed — real ack.
    last_id = str(n - 1)
    assert by_id[last_id]["ok"] is True
    assert by_id[last_id]["result"] == {"freq": base_freq + n - 1, "receiver": 0}


# ---------------------------------------------------------------------------
# (c) Commands slower than 50ms apart behave byte-identically to today:
#     both dispatch immediately, no coalescing, no supersede replies.
# ---------------------------------------------------------------------------


async def test_slower_than_window_commands_are_unaffected() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    await handler._handle_command(
        {"id": "x", "name": "set_freq", "params": {"freq": 14_074_000}}
    )
    await asyncio.sleep(handler._CMD_MIN_INTERVAL + 0.02)  # noqa: SLF001
    await handler._handle_command(
        {"id": "y", "name": "set_freq", "params": {"freq": 14_075_000}}
    )

    assert len(queue.items) == 2
    assert [item.freq for item in queue.items] == [14_074_000, 14_075_000]

    by_id = _messages_by_id(ws)
    assert by_id["x"]["result"] == {"freq": 14_074_000, "receiver": 0}
    assert by_id["y"]["result"] == {"freq": 14_075_000, "receiver": 0}
    assert not handler._cmd_pending  # noqa: SLF001
    assert not handler._cmd_flush_tasks  # noqa: SLF001


# ---------------------------------------------------------------------------
# (d) PTT / TX-off paths are NOT subject to this limiter at all — pin it.
# ---------------------------------------------------------------------------


async def test_ptt_off_bursts_are_never_rate_limited_or_coalesced() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    n = 5
    for i in range(n):
        await handler._handle_command(
            {"id": f"ptt-{i}", "name": "ptt_off", "params": {}}
        )

    # Every single PTT OFF physically enqueues immediately — no coalescing,
    # no pending state, regardless of how tightly they are spaced.
    assert len(queue.items) == n
    assert all(isinstance(item, PttOff) for item in queue.items)
    assert "ptt_off" not in handler._cmd_pending  # noqa: SLF001
    assert "ptt_off" not in handler._cmd_flush_tasks  # noqa: SLF001

    by_id = _messages_by_id(ws)
    for i in range(n):
        msg = by_id[f"ptt-{i}"]
        assert msg["ok"] is True
        assert msg["result"] == {}


# ---------------------------------------------------------------------------
# (e) Counter accuracy: every coalesce/supersede event is counted exactly.
# ---------------------------------------------------------------------------


async def test_coalesce_counter_is_accurate_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    n = 10
    with caplog.at_level(logging.WARNING, logger="rigplane.web.handlers.control"):
        for i in range(n):
            await handler._handle_command(
                {"id": str(i), "name": "set_freq", "params": {"freq": 14_000_000 + i}}
            )

        # 10 frames: 1 dispatched immediately, 9 coalesced-in, 8 of those
        # superseded before flush (the 9th survives to flush).
        assert handler._cmd_coalesced.get("set_freq") == 8  # noqa: SLF001
        assert any("set_freq" in rec.message for rec in caplog.records)

        await _await_flush(handler, "set_freq")

    # Counter resets once the burst actually flushes, so the next burst's
    # count is not inflated by a prior one.
    assert handler._cmd_coalesced.get("set_freq", 0) == 0  # noqa: SLF001
