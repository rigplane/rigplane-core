"""MOR-3096 managed PTT ingress delivery discriminators.

A real ``ManagedTxAuthority`` over a literal Icom command backend and a
recorded wire, driven through the real session/control ingress seams:
``ManagedTxAuthority.start_ptt_submission`` plus the poller's
``execute_positive_tx_queue_entry``. The actuator is honest about the
unkey: the dekey settles only when the wire confirms, so an accepted OFF
admission can never report a clean release without a real dekey
(MOR-2860 conservative ACK semantics).

Group 1 (``test_compat_*``) pins the pure admission contract. Group 2
(``test_invariant_*``) pins the new delivery diagnostics: a pending
release or visible fault/risk persists while the dekey is unconfirmed,
and the release cleans only through a confirmed dekey. The three frontend
cases are matched where the Python seam can represent them: momentary
ON then regular OFF, session teardown mid-TX, and an accepted ON whose
settlement response is never delivered.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.command_map import CommandMap
from rigplane.commands.bound import BoundCommands
from rigplane.core.civ import CivRequestTracker
from rigplane.core.types import CivFrame
from rigplane.runtime._poller_types import (
    CommandQueueEntry,
    execute_positive_tx_queue_entry,
)
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ActuationResult,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ReleasePlan,
)
from rigplane.runtime.radio import CoreRadio
from test_managed_tx_authority import FakeClock, FakeConfigStore, FakeWakeup


class _HonestLiteralIcomActuator:
    """Literal Icom commands over a recorded wire; the unkey settles on it.

    ``actuate`` is the real ``CoreRadio.actuate``. The unkey's answer is
    delivered only when ``dekey_confirmed`` is set; otherwise the send
    returns no answer and the actuator honestly reports UNCERTAIN, so no
    fake ever unkeys on its own.
    """

    actuate = CoreRadio.actuate

    def __init__(self) -> None:
        self._commands = BoundCommands(
            CommandMap(
                {
                    "ptt_on": (0x31, 0x01, 0xA1),
                    "ptt_off": (0x31, 0x01, 0xA0),
                    "stop_cw": (0x32, 0x02),
                    "set_tuner_status": (0x33, 0x03),
                }
            )
        )
        self._radio_addr = 0x94
        # ``CoreRadio.actuate`` distrusts an unkey answer while the
        # tracker holds another write's unclaimed answer (MOR-2860); no
        # write sink is registered on this recorded wire.
        self._civ_request_tracker = CivRequestTracker()
        self._civ_get_timeout = 2.0
        self.wire: list[bytes] = []
        self.dekey_confirmed = False
        self.on_wire_gate: asyncio.Event | None = None

    async def _send_civ_raw(
        self,
        frame: bytes,
        *,
        is_current: Any,
        wait_response: bool,
        **_kwargs: Any,
    ) -> CivFrame | None:
        del is_current
        if self.on_wire_gate is not None and not wait_response:
            await self.on_wire_gate.wait()
        self.wire.append(bytes(frame))
        if not wait_response:
            return None
        if self.dekey_confirmed:
            return CivFrame(to_addr=0xE0, from_addr=self._radio_addr, command=0xFB)
        return None

    def wire_counts(self) -> dict[str, int]:
        addr = self._radio_addr
        keys = {
            "on": bytes(self._commands.ptt_on(to_addr=addr)),
            "off": bytes(self._commands.ptt_off(to_addr=addr)),
            "stop_cw": bytes(self._commands.stop_cw(to_addr=addr)),
            "tune_off": bytes(self._commands.set_tuner_status(0, to_addr=addr)),
        }
        counts = dict.fromkeys(keys, 0)
        for frame in self.wire:
            for name, key in keys.items():
                if frame == key:
                    counts[name] += 1
        return counts


@pytest.fixture
async def rig():
    clock = FakeClock()
    actuator = _HonestLiteralIcomActuator()
    lane = ManagedTxEffectLane(actuator, clock=clock)
    managed = ManagedTxAuthority(
        lane,
        FakeConfigStore(3600),
        TxAbortFence(),
        provider_generation=7,
        clock=clock,
        wakeup=FakeWakeup(),
        attempt_timeout_seconds=30,
        retry_delay_seconds=2,
    )
    await managed._stop_scheduler(managed._scheduler_task)
    fixture = SimpleNamespace(managed=managed, actuator=actuator, clock=clock)
    try:
        yield fixture
    finally:
        actuator.on_wire_gate = None
        actuator.dekey_confirmed = True
        state = managed.snapshot_nowait().state
        if state.release_required or state.intent.kind is not ManagedTxIntentKind.RX:
            with contextlib.suppress(RuntimeError):
                await asyncio.wait_for(managed.force_off(), 3)
        with contextlib.suppress(RuntimeError):
            await asyncio.wait_for(managed.close(), 3)


async def _admit_on(rig, owner: str) -> None:
    """One momentary ON through the real session/control ingress seams."""
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    pending = rig.managed.start_ptt_submission(
        True,
        owner,
        ready=ready,
        expires_at_monotonic=rig.clock() + 10.0,
    )
    entry = CommandQueueEntry(
        command=None,
        positive_tx_ready=ready,
        positive_tx_submission=pending,
    )
    await execute_positive_tx_queue_entry(entry)


async def _drive_retry(rig) -> None:
    assert rig.managed._retry_due is not None
    rig.clock.now = rig.managed._retry_due
    await rig.managed._process_due()


# ------------------------------------------------------------------
# Group 1: pure admission compatibility through the real seams
# ------------------------------------------------------------------


async def test_compat_momentary_on_admits_via_the_real_queue_entry(rig):
    await _admit_on(rig, "ws-session")

    state = (await rig.managed.snapshot()).state
    assert state.intent == ManagedTxIntent.ptt("ws-session")
    assert state.release_required
    assert state.pending_effect is None
    assert state.last_actuation is not None
    assert state.last_actuation.operation is ActuationOperation.PTT_ON
    assert state.last_actuation.result is ActuationResult.ACCEPTED
    assert rig.actuator.wire_counts() == {
        "on": 1,
        "off": 0,
        "stop_cw": 0,
        "tune_off": 0,
    }


async def test_compat_off_from_a_wrong_owner_is_rejected_without_a_wire_write(rig):
    await _admit_on(rig, "ws-a")
    before = (await rig.managed.snapshot()).state

    off = await rig.managed.submit_ptt(False, "ws-b")

    assert off.outcome is ManagedTxOutcome.REJECTED
    assert (await rig.managed.snapshot()).state == before
    assert rig.actuator.wire_counts()["off"] == 0


# ------------------------------------------------------------------
# Group 2: delivery diagnostics — no accepted clean OFF without a dekey
# ------------------------------------------------------------------


async def test_invariant_unconfirmed_dekey_keeps_pending_release_until_confirm(rig):
    await _admit_on(rig, "ws-session")

    off = await rig.managed.submit_ptt(False, "ws-session")
    assert off.outcome is ManagedTxOutcome.ACCEPTED
    await off.wait_settlement()

    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.release_required
    assert state.release_plan is ReleasePlan.PTT_RELEASE
    assert state.pending_effect is None
    assert state.last_actuation is not None
    assert state.last_actuation.operation is ActuationOperation.FORCE_RECEIVE
    assert state.last_actuation.result is ActuationResult.UNCERTAIN
    assert state.last_error == ActuationResult.UNCERTAIN.value
    counts = rig.actuator.wire_counts()
    assert counts["on"] == 1 and counts["off"] == 1 and counts["stop_cw"] == 0
    first_epoch = state.effect_epoch

    # One ordinary retry while the wire stays silent: still owed.
    await _drive_retry(rig)
    retry = (await rig.managed.snapshot()).state
    assert retry.release_required
    assert retry.last_actuation is not None
    assert retry.last_actuation.result is ActuationResult.UNCERTAIN
    assert retry.effect_epoch > first_epoch
    assert rig.actuator.wire_counts()["off"] == 2

    # The confirmed dekey is the only path to a clean release.
    rig.actuator.dekey_confirmed = True
    await _drive_retry(rig)
    clean = (await rig.managed.snapshot()).state
    assert clean.intent.kind is ManagedTxIntentKind.RX
    assert not clean.release_required
    assert clean.pending_effect is None
    assert clean.last_error is None
    assert clean.last_actuation is not None
    assert clean.last_actuation.result is ActuationResult.ACCEPTED
    counts = rig.actuator.wire_counts()
    assert counts["on"] == 1 and counts["off"] == 3 and counts["stop_cw"] == 0


async def test_invariant_session_teardown_runs_one_stop_cw_and_keeps_debt(rig):
    await _admit_on(rig, "ws-session")

    outcome = await rig.managed.owner_disconnect("ws-session")

    assert outcome is ManagedTxOutcome.ACCEPTED
    state = (await rig.managed.snapshot()).state
    assert state.intent.kind is ManagedTxIntentKind.RX
    assert state.release_required
    assert state.release_plan is ReleasePlan.FORCE_RELEASE
    assert state.pending_effect is None
    assert state.last_actuation is not None
    assert state.last_actuation.operation is ActuationOperation.FORCE_RECEIVE
    assert state.last_actuation.result is ActuationResult.UNCERTAIN
    assert state.last_error == ActuationResult.UNCERTAIN.value
    counts = rig.actuator.wire_counts()
    assert counts["on"] == 1 and counts["off"] == 1 and counts["stop_cw"] == 1

    # The ordinary retry dekeys without re-running the abort family, and
    # the debt clears only through the confirmed dekey.
    rig.actuator.dekey_confirmed = True
    await _drive_retry(rig)
    clean = (await rig.managed.snapshot()).state
    assert not clean.release_required
    assert clean.last_actuation is not None
    assert clean.last_actuation.result is ActuationResult.ACCEPTED
    counts = rig.actuator.wire_counts()
    assert counts["on"] == 1 and counts["off"] == 2 and counts["stop_cw"] == 1


async def test_invariant_lost_on_response_admits_without_delivering_settlement(rig):
    loop = asyncio.get_running_loop()
    ready: asyncio.Future[None] = loop.create_future()
    gate = asyncio.Event()
    rig.actuator.on_wire_gate = gate
    pending = rig.managed.start_ptt_submission(
        True,
        "ws-session",
        ready=ready,
        expires_at_monotonic=rig.clock() + 10.0,
    )

    ready.set_result(None)
    submission = await asyncio.shield(pending)

    # The server-side admission latched the ON while the provider
    # settlement was still in flight and undelivered.
    assert submission.outcome is ManagedTxOutcome.ACCEPTED
    assert not submission.settlement_done
    state = (await rig.managed.snapshot()).state
    assert state.intent == ManagedTxIntent.ptt("ws-session")
    assert state.pending_effect is not None

    gate.set()
    settled = await submission.wait_settlement()
    assert settled is not None
    assert settled.result is ActuationResult.ACCEPTED
    assert rig.actuator.wire_counts()["on"] == 1


async def test_invariant_unavailable_provider_retains_debt_until_replacement_dekeys(
    rig,
):
    await _admit_on(rig, "ws-session")

    await rig.managed.provider_unavailable()

    debt = await rig.managed.snapshot()
    assert debt.provider_generation is None
    assert debt.state.intent.kind is ManagedTxIntentKind.RX
    assert debt.state.release_required
    assert debt.state.pending_effect is None
    assert rig.actuator.wire_counts()["off"] == 0

    await rig.managed.provider_available(8)
    arrival = await rig.managed.snapshot()
    assert arrival.provider_generation == 8
    assert arrival.state.release_required
    assert rig.actuator.wire_counts()["off"] == 1

    await _drive_retry(rig)
    assert (await rig.managed.snapshot()).state.release_required
    assert rig.actuator.wire_counts()["off"] == 2

    rig.actuator.dekey_confirmed = True
    await _drive_retry(rig)
    clean = await rig.managed.snapshot()
    assert clean.provider_generation == 8
    assert not clean.state.release_required
    counts = rig.actuator.wire_counts()
    assert counts["on"] == 1 and counts["off"] == 3 and counts["stop_cw"] == 0
