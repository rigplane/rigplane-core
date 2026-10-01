"""MOR-3096 managed TX ACK-recovery discriminators over a literal serial CI-V wire.

A real ``ManagedTxAuthority`` drives a real ``Icom7610SerialRadio`` over
the recorded fake serial CI-V link. A fire-and-forget radio write the
link never answers leaves an ACK sink whose drop stamps the
unclaimed-answer mark (``core/civ.py: CivRequestTracker.ack_unclaimed_at``),
so the release unkey's prompt FB is unattributable and the release stays
owed (MOR-2860 conservative ACK semantics) until the mark ages past one
``_civ_get_timeout``. Ordinary retries (``_process_due`` with
``full_force=False``) re-send only the unkey: the STOP_CW abort runs
exactly once, inside the full-force release.

The authority clock and wakeup are the FakeClock/FakeWakeup seams; the
radio's answer window (``_civ_get_timeout``) is real time, so the only
sleeps are bounded by that window.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntentKind,
    ManagedTxOutcome,
)
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_managed_tx_authority import FakeClock, FakeConfigStore, FakeWakeup


class _StopCwSilentLink(_FakeSerialCivLink):
    """Fake serial link that records the STOP_CW abort but never answers it."""

    async def send(self, frame: bytes) -> None:
        payload = bytes(frame)
        if payload[4:-1] == b"\x17\xff":
            if not self.connected:
                raise ConnectionError("Serial CI-V link is disconnected.")
            if self.lifecycle_events is not None:
                self.lifecycle_events.append(("send", payload))
            self.sent_frames.append(payload)
            return
        await super().send(payload)


async def _new_rig(link: _FakeSerialCivLink) -> SimpleNamespace:
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    radio._civ_get_timeout = 0.2
    await radio.connect()
    clock, wakeup = FakeClock(), FakeWakeup()
    managed = ManagedTxAuthority(
        ManagedTxEffectLane(radio, clock=clock),
        FakeConfigStore(3600),
        TxAbortFence(),
        provider_generation=7,
        clock=clock,
        wakeup=wakeup,
        attempt_timeout_seconds=30,
        retry_delay_seconds=2,
    )
    return SimpleNamespace(
        link=link,
        radio=radio,
        managed=managed,
        clock=clock,
        wakeup=wakeup,
    )


async def _teardown_rig(rig) -> None:
    state = rig.managed.snapshot_nowait().state
    if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
        with contextlib.suppress(RuntimeError):
            await asyncio.wait_for(rig.managed.force_off(), 3)
    with contextlib.suppress(RuntimeError):
        await asyncio.wait_for(rig.managed.close(), 3)
    if not rig.managed._scheduler_task.done():
        await rig.managed._stop_scheduler(rig.managed._scheduler_task)
    with contextlib.suppress(Exception):
        await rig.radio.disconnect()


@pytest.fixture
async def rig():
    built = await _new_rig(_FakeSerialCivLink())
    try:
        yield built
    finally:
        await _teardown_rig(built)


@pytest.fixture
async def stop_cw_silent_rig():
    built = await _new_rig(_StopCwSilentLink())
    try:
        yield built
    finally:
        await _teardown_rig(built)


def _frame_counts(rig) -> dict[str, int]:
    counts = {"identity": 0, "ptt_on": 0, "unkey": 0, "stop_cw": 0, "noise": 0}
    for frame in rig.link.sent_frames:
        key = frame[4:-1]
        if key == b"\x19\x00":
            counts["identity"] += 1
        elif key == b"\x1c\x00\x01":
            counts["ptt_on"] += 1
        elif key == b"\x1c\x00\x00":
            counts["unkey"] += 1
        elif key == b"\x17\xff":
            counts["stop_cw"] += 1
        elif key == b"\x0f\x01":
            counts["noise"] += 1
    return counts


async def _unanswered_write(rig) -> None:
    """One real fire-and-forget radio write the fake link never answers."""
    await rig.radio._send_civ_raw(
        build_civ_frame(rig.radio._radio_addr, CONTROLLER_ADDR, 0x0F, data=b"\x01"),
        wait_response=False,
    )


async def _drive_retry(rig) -> None:
    """Fire one scheduled retry through the real scheduler wakeup path."""
    assert rig.managed._retry_due is not None
    revision = rig.wakeup.revision
    rig.clock.now = rig.managed._retry_due
    rig.wakeup.wake()
    await rig.wakeup.wait_after(revision + 1)


def _last_result(rig) -> ActuationResult:
    state = rig.managed.snapshot_nowait().state
    assert state.last_actuation is not None
    return state.last_actuation.result


async def test_answered_release_settles_the_first_unkey_without_retry_debt(rig):
    assert await rig.managed.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
    assert rig.radio._civ_request_tracker.ack_unclaimed_at is None

    assert await rig.managed.force_off() is ManagedTxOutcome.ACCEPTED

    projection = await rig.managed.snapshot()
    assert projection.provider_generation == 7
    state = projection.state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert not state.release_required
    assert state.pending_effect is None
    assert state.last_error is None
    assert state.last_actuation is not None
    assert state.last_actuation.result is ActuationResult.ACCEPTED
    assert rig.managed._retry_due is None
    assert rig.radio._civ_request_tracker.ack_unclaimed_at is None
    counts = _frame_counts(rig)
    assert counts["ptt_on"] == 1 and counts["unkey"] == 1 and counts["stop_cw"] == 1
    await rig.managed.close()


async def test_full_force_release_retry_stays_owed_until_the_mark_ages_out(rig):
    assert await rig.managed.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
    await _unanswered_write(rig)
    assert rig.radio._civ_request_tracker.ack_unclaimed_at is None

    assert await rig.managed.force_off() is ManagedTxOutcome.ACCEPTED

    release = (await rig.managed.snapshot()).state
    assert release.intent.kind is ManagedTxIntentKind.RX
    assert release.release_required
    assert release.pending_effect is None
    assert release.last_actuation is not None
    assert release.last_actuation.operation is ActuationOperation.FORCE_RECEIVE
    assert release.last_actuation.result is ActuationResult.UNCERTAIN
    assert release.abort_errors == ()
    mark = rig.radio._civ_request_tracker.ack_unclaimed_at
    assert mark is not None
    counts = _frame_counts(rig)
    assert counts["ptt_on"] == 1 and counts["stop_cw"] == 1 and counts["unkey"] == 1
    attempts = [release.last_actuation.attempt_id]

    # Ordinary retry inside the answer window: prompt FB, still
    # unattributable, so the release stays owed and no abort re-runs.
    await _drive_retry(rig)
    first = (await rig.managed.snapshot()).state
    assert first.release_required
    assert first.last_actuation is not None
    assert first.last_actuation.result is ActuationResult.UNCERTAIN
    attempts.append(first.last_actuation.attempt_id)
    assert attempts[1] > attempts[0]
    counts = _frame_counts(rig)
    assert counts["unkey"] == 2 and counts["stop_cw"] == 1
    # Finite mark: no fresh unanswered write, so nothing re-stamped it.
    assert rig.radio._civ_request_tracker.ack_unclaimed_at == mark

    # Quiescence past one answer window: the retry's FB is attributable
    # again and the release cleans through the ordinary retry.
    await asyncio.sleep(rig.radio._civ_get_timeout + 0.05)
    await _drive_retry(rig)
    projection = await rig.managed.snapshot()
    assert projection.provider_generation == 7
    clean = projection.state
    assert clean.intent.kind is ManagedTxIntentKind.RX
    assert not clean.release_required
    assert clean.pending_effect is None
    assert clean.last_error is None
    assert clean.last_actuation is not None
    assert clean.last_actuation.result is ActuationResult.ACCEPTED
    attempts.append(clean.last_actuation.attempt_id)
    assert attempts[2] > attempts[1]
    counts = _frame_counts(rig)
    assert counts["ptt_on"] == 1 and counts["unkey"] == 3 and counts["stop_cw"] == 1
    assert rig.radio._civ_request_tracker.ack_unclaimed_at == mark
    await rig.managed.close()


async def test_refreshed_unanswered_writes_keep_retries_uncertain_until_noise_stops(
    rig,
):
    assert await rig.managed.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
    await _unanswered_write(rig)
    assert await rig.managed.force_off() is ManagedTxOutcome.ACCEPTED
    marks = [rig.radio._civ_request_tracker.ack_unclaimed_at]
    assert marks[0] is not None
    assert _last_result(rig) is ActuationResult.UNCERTAIN

    for _ in range(2):
        # A fresh unanswered write inside the window renews the mark.
        await _unanswered_write(rig)
        await _drive_retry(rig)
        state = (await rig.managed.snapshot()).state
        assert state.release_required
        assert state.last_actuation is not None
        assert state.last_actuation.result is ActuationResult.UNCERTAIN
        marks.append(rig.radio._civ_request_tracker.ack_unclaimed_at)
    assert marks[1] > marks[0] and marks[2] > marks[1]
    counts = _frame_counts(rig)
    assert counts["noise"] == 3 and counts["stop_cw"] == 1 and counts["unkey"] == 3

    # Noise stops: the last mark ages past the window and the retry cleans.
    await asyncio.sleep(rig.radio._civ_get_timeout + 0.05)
    await _drive_retry(rig)
    clean = (await rig.managed.snapshot()).state
    assert clean.intent.kind is ManagedTxIntentKind.RX
    assert not clean.release_required
    assert clean.last_actuation is not None
    assert clean.last_actuation.result is ActuationResult.ACCEPTED
    counts = _frame_counts(rig)
    assert counts["ptt_on"] == 1 and counts["unkey"] == 4 and counts["stop_cw"] == 1
    assert rig.radio._civ_request_tracker.ack_unclaimed_at == marks[2]
    await rig.managed.close()


async def test_unanswered_stop_cw_refreshes_the_mark_once_not_every_retry(
    stop_cw_silent_rig,
):
    rig = stop_cw_silent_rig
    assert await rig.managed.ptt_down("owner") is ManagedTxOutcome.ACCEPTED
    await _unanswered_write(rig)
    assert await rig.managed.force_off() is ManagedTxOutcome.ACCEPTED
    release_mark = rig.radio._civ_request_tracker.ack_unclaimed_at
    assert release_mark is not None
    assert _last_result(rig) is ActuationResult.UNCERTAIN

    # The unanswered STOP_CW abort's own sink is dropped inside the first
    # retry's drain, renewing the mark once.
    await _drive_retry(rig)
    first = (await rig.managed.snapshot()).state
    assert first.release_required
    assert first.last_actuation is not None
    assert first.last_actuation.result is ActuationResult.UNCERTAIN
    stop_cw_mark = rig.radio._civ_request_tracker.ack_unclaimed_at
    assert stop_cw_mark is not None and stop_cw_mark > release_mark
    counts = _frame_counts(rig)
    assert counts["noise"] == 1 and counts["stop_cw"] == 1 and counts["unkey"] == 2

    # The mark is finite: the retry does not re-abort, so nothing re-stamps.
    await _drive_retry(rig)
    second = (await rig.managed.snapshot()).state
    assert second.release_required
    assert second.last_actuation is not None
    assert second.last_actuation.result is ActuationResult.UNCERTAIN
    assert rig.radio._civ_request_tracker.ack_unclaimed_at == stop_cw_mark
    counts = _frame_counts(rig)
    assert counts["unkey"] == 3 and counts["stop_cw"] == 1

    await asyncio.sleep(rig.radio._civ_get_timeout + 0.05)
    await _drive_retry(rig)
    clean = (await rig.managed.snapshot()).state
    assert clean.intent.kind is ManagedTxIntentKind.RX
    assert not clean.release_required
    assert clean.last_actuation is not None
    assert clean.last_actuation.result is ActuationResult.ACCEPTED
    counts = _frame_counts(rig)
    assert counts["ptt_on"] == 1 and counts["unkey"] == 4 and counts["stop_cw"] == 1
    assert rig.radio._civ_request_tracker.ack_unclaimed_at == stop_cw_mark
    await rig.managed.close()
