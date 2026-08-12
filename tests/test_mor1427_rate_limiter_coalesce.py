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
import random
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rigplane.web.handlers import ControlHandler
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import PttOff, SetFilterWidth, SetFreq, SetMode

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


def _default_key(name: str) -> str:
    """Coalescing key for a non-selector, receiver-less-in-params command.

    MOR-1499: ``_cmd_pending``/``_cmd_flush_tasks``/``_cmd_coalesced`` are
    keyed by ``ControlHandler._coalesce_key(name, params)``, not the bare
    name. None of the commands this suite exercises (``set_freq``,
    ``set_filter_width``) are selector-type or pass an explicit
    ``receiver``, so their key is always ``f"{name}:0"`` (the same default
    every receiver-scoped command already applies).
    """
    return f"{name}:0"


async def _await_flush(handler: ControlHandler, name: str) -> None:
    """Wait for the deferred coalesced flush (if any) for *name* to complete."""
    task = handler._cmd_flush_tasks.get(_default_key(name))  # noqa: SLF001
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
        assert handler._cmd_coalesced.get(_default_key("set_freq")) == 8  # noqa: SLF001
        assert any("set_freq" in rec.message for rec in caplog.records)

        await _await_flush(handler, "set_freq")

    # Counter resets once the burst actually flushes, so the next burst's
    # count is not inflated by a prior one.
    assert handler._cmd_coalesced.get(_default_key("set_freq"), 0) == 0  # noqa: SLF001


# ---------------------------------------------------------------------------
# (f) MOR-1427 review B1 regression pin: a pending frame must always win the
#     race, even once the raw pacing window has elapsed, if the deferred
#     flush task has not actually run yet (event loop stalled past its
#     deadline). Consulting only elapsed time let a newer frame bypass the
#     gate and dispatch immediately while an OLDER frame was still queued
#     for flush -- the flush then overwrote the newer value with the stale
#     one. See src/rigplane/web/handlers/control.py `_handle_command`.
# ---------------------------------------------------------------------------


async def test_late_flush_gate_diverts_to_coalescing_when_frame_pending() -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )

    base_freq = 14_000_000

    # F1: dispatched immediately -- opens the pacing window.
    await handler._handle_command(
        {"id": "f1", "name": "set_freq", "params": {"freq": base_freq}}
    )
    assert len(queue.items) == 1

    # F2: arrives inside the window -> coalesced, schedules the deferred
    # flush task for "set_freq".
    await handler._handle_command(
        {"id": "f2", "name": "set_freq", "params": {"freq": base_freq + 1}}
    )
    assert _default_key("set_freq") in handler._cmd_pending  # noqa: SLF001
    assert _default_key("set_freq") in handler._cmd_flush_tasks  # noqa: SLF001

    # Block the event loop SYNCHRONOUSLY past the flush deadline. Real
    # wall-clock time now exceeds _CMD_MIN_INTERVAL since F1, but the
    # sleeping flush task has not been resumed by the loop yet -- this is
    # exactly the window the fix must close.
    time.sleep(handler._CMD_MIN_INTERVAL + 0.02)  # noqa: SLF001

    # F3: arrives after the raw window has elapsed by wall-clock time, while
    # F2 is still the pending frame (flush task has not run). Must still be
    # coalesced -- not dispatched immediately ahead of F2's queued flush.
    await handler._handle_command(
        {"id": "f3", "name": "set_freq", "params": {"freq": base_freq + 2}}
    )

    await _await_flush(handler, "set_freq")

    # The LAST physical enqueue carries F3's value -- never the stale F2.
    assert len(queue.items) == 2
    assert isinstance(queue.items[1], SetFreq)
    assert queue.items[1].freq == base_freq + 2

    by_id = _messages_by_id(ws)
    assert set(by_id) == {"f1", "f2", "f3"}
    assert by_id["f1"]["ok"] is True
    assert by_id["f1"]["result"] == {"freq": base_freq, "receiver": 0}
    assert by_id["f2"]["ok"] is True
    assert by_id["f2"]["result"] == {"superseded": True}
    assert by_id["f3"]["ok"] is True
    assert by_id["f3"]["result"] == {"freq": base_freq + 2, "receiver": 0}

    # Every id got exactly one reply.
    assert ws.send_text.await_count == 3


# ---------------------------------------------------------------------------
# (g) Battery/property test (MOR-1427 review evidence-tier requirement):
#     many randomized bursts, with synchronous loop stalls injected at
#     randomized offsets around the flush boundary, occasional cross-name
#     interleaving, and occasional mid-burst teardown. Fixed seeds keep this
#     deterministic in CI. Runtime budget: well under ~20s.
#
#     Invariants asserted per iteration:
#       (a) the last physical write for a name always carries the value of
#           the last frame dispatched for that name (unless that frame was
#           still pending at a mid-burst teardown, per N4);
#       (b) every frame id receives at most one reply, and exactly one
#           unless it was cancelled by mid-burst teardown;
#       (c) after a mid-burst teardown, no set_* command reaches the queue
#           after the teardown's PTT OFF marker.
# ---------------------------------------------------------------------------

_BATTERY_PRIMARY_NAMES = ("set_freq", "set_filter_width")


def _battery_value_for(name: str, n: int) -> int:
    return 14_000_000 + n if name == "set_freq" else 500 + n


def _battery_params_for(name: str, value: int) -> dict[str, Any]:
    return {"freq": value} if name == "set_freq" else {"width": value}


def _battery_dataclass_for(name: str) -> type:
    return SetFreq if name == "set_freq" else SetFilterWidth


def _reply_id_counts(ws: SimpleNamespace) -> dict[str, int]:
    counts: dict[str, int] = {}
    for msg in _sent_messages(ws):
        counts[msg["id"]] = counts.get(msg["id"], 0) + 1
    return counts


async def _run_one_battery_iteration(rng: random.Random) -> None:
    ws = SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock())
    queue = _QueueRecorder()
    handler = _control_handler(
        ws=ws,
        radio=SimpleNamespace(connected=True),
        server=SimpleNamespace(command_queue=queue),
    )
    # Shrink the pacing window so stalls stay small (same code path, faster
    # wall-clock) -- _CMD_MIN_INTERVAL is a plain instance attribute.
    handler._CMD_MIN_INTERVAL = 0.01  # noqa: SLF001

    primary = rng.choice(_BATTERY_PRIMARY_NAMES)
    secondary = _BATTERY_PRIMARY_NAMES[1 - _BATTERY_PRIMARY_NAMES.index(primary)]

    burst_len = rng.randint(1, 8)
    dispatched: list[tuple[str, int]] = []  # (id, value) for `primary`, in order
    teardown_index: int | None = None  # index into queue.items of the PTT OFF marker
    cancelled_id: str | None = None
    teardown_fired = False

    for step in range(burst_len):
        # Occasionally stall the loop synchronously, at a randomized offset
        # around the flush boundary (sometimes short of it, sometimes past
        # it -- exercising the exact B1 race window).
        if rng.random() < 0.35:
            time.sleep(rng.uniform(0.0, handler._CMD_MIN_INTERVAL * 1.6))  # noqa: SLF001

        # Occasionally interleave a one-shot secondary-name command. It is
        # never itself rate-limited within this iteration (fresh handler,
        # first frame for that name), so it must not perturb the primary
        # burst's coalescing state.
        if rng.random() < 0.25:
            sec_id = f"sec-{step}"
            await handler._handle_command(
                {
                    "id": sec_id,
                    "name": secondary,
                    "params": _battery_params_for(
                        secondary, _battery_value_for(secondary, step)
                    ),
                }
            )

        # Occasionally tear down mid-burst instead of dispatching further.
        if rng.random() < 0.2:
            pending = handler._cmd_pending.get(_default_key(primary))  # noqa: SLF001
            if pending is not None:
                cancelled_id = str(pending[0])
            queue.put(PttOff())
            teardown_index = len(queue.items) - 1
            handler._cancel_pending_command_flushes()  # noqa: SLF001
            teardown_fired = True
            break

        cmd_id = str(step)
        value = _battery_value_for(primary, step)
        dispatched.append((cmd_id, value))
        await handler._handle_command(
            {
                "id": cmd_id,
                "name": primary,
                "params": _battery_params_for(primary, value),
            }
        )

    if not teardown_fired:
        # Let any still-pending flush for either name settle physically
        # before asserting.
        await _await_flush(handler, primary)
        await _await_flush(handler, secondary)
    else:
        # Give any already-cancelled callback a chance to unwind; nothing
        # further should ever append to the queue after this point.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    reply_counts = _reply_id_counts(ws)

    # (b) every id gets at most one reply; exactly one unless cancelled.
    for cmd_id, _ in dispatched:
        if cmd_id == cancelled_id:
            assert reply_counts.get(cmd_id, 0) == 0, (
                f"cancelled id {cmd_id!r} unexpectedly got a reply"
            )
        else:
            assert reply_counts.get(cmd_id, 0) == 1, (
                f"id {cmd_id!r} got {reply_counts.get(cmd_id, 0)} replies, expected 1"
            )
    assert all(count <= 1 for count in reply_counts.values())

    # (a) the last physical write for `primary` carries the value of the
    # last frame that actually completed (real, non-superseded ack).
    by_id = _messages_by_id(ws)
    real_ack_dispatched = [
        (cmd_id, value)
        for cmd_id, value in dispatched
        if cmd_id in by_id and by_id[cmd_id].get("result") != {"superseded": True}
    ]
    primary_cls = _battery_dataclass_for(primary)
    primary_writes = [item for item in queue.items if isinstance(item, primary_cls)]
    if real_ack_dispatched:
        last_id, last_value = real_ack_dispatched[-1]
        assert primary_writes, f"expected a physical write for {primary!r}"
        last_write = primary_writes[-1]
        written_value = last_write.freq if primary == "set_freq" else last_write.width
        assert written_value == last_value, (
            f"last physical write for {primary!r} carries {written_value!r}, "
            f"expected {last_value!r} (id {last_id!r})"
        )

    # If nothing was cancelled, the burst fully drained: no pending state,
    # no outstanding flush tasks, and (if any frames were sent) the very
    # last dispatched frame must be among the real (non-superseded) acks --
    # i.e. it always eventually wins, never gets silently stuck as
    # "superseded" with nothing superseding it.
    if not teardown_fired:
        assert _default_key(primary) not in handler._cmd_pending  # noqa: SLF001
        assert _default_key(primary) not in handler._cmd_flush_tasks  # noqa: SLF001
        if dispatched:
            last_dispatched_id = dispatched[-1][0]
            assert by_id[last_dispatched_id].get("result") != {"superseded": True}

    # (c) after a mid-burst teardown, no set_* command reaches the queue
    # after the teardown's PTT OFF marker.
    if teardown_fired:
        assert teardown_index is not None
        for item in queue.items[teardown_index + 1 :]:
            assert not isinstance(item, (SetFreq, SetFilterWidth, SetMode)), (
                f"set_* command {item!r} reached the queue after teardown"
            )
        assert not handler._cmd_pending  # noqa: SLF001
        assert not handler._cmd_flush_tasks  # noqa: SLF001


async def test_battery_coalescing_survives_stalls_interleaving_and_teardown() -> None:
    """MOR-1427 review evidence-tier requirement: property/burst battery.

    200 iterations across 4 fixed seeds, each with a randomized burst,
    randomized synchronous loop stalls around the flush boundary, occasional
    cross-name interleaving, and occasional mid-burst teardown.
    """
    started = time.monotonic()
    for seed in (1427, 1405, 2377, 42):
        rng = random.Random(seed)
        for _ in range(50):
            await _run_one_battery_iteration(rng)
    elapsed = time.monotonic() - started
    assert elapsed < 20.0, f"battery took {elapsed:.1f}s, budget is 20s"
