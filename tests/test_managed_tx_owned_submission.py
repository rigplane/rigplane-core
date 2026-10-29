"""MOR-3821 P0b capability: owner-specific admission and keying preparation.

``ManagedTxAuthority.owned_tx_submission`` admits one owner's TX audio push
only while the held intent is ``PTT(owner)`` (spec 8.3 item 5), modelled on
``outside_tx_submission`` (MOR-3764). ``before_on`` on
``start_ptt_submission`` (spec 7.3a) prepares the route inside the admitted
key — after the intent commits to ``PTT(owner)``, before the PTT_ON effect
is dispatched, bounded by the attempt timeout. A hook that raises, times
out or reports failure takes the existing "ON rejected before effect" path:
intent back to RX, release debt, scheduled ``force_receive``, no RF effect,
and the requester gets a rejection.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from test_controller_sessions import radio_tx_authority

from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    submission_refused_for_release_debt,
)

ACCEPTED = ManagedTxOutcome.ACCEPTED
REJECTED = ManagedTxOutcome.REJECTED


async def _enter_owned(
    managed: ManagedTxAuthority, owner: str, out: list[bool]
) -> None:
    async with managed.owned_tx_submission(owner) as admitted:
        out.append(admitted)


async def test_owned_submission_admits_while_the_owner_holds_ptt() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        out: list[bool] = []
        await _enter_owned(managed, "a", out)
        assert out == [True]
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_owned_submission_refuses_another_owner() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        out: list[bool] = []
        await _enter_owned(managed, "b", out)
        assert out == [False]
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_owned_submission_refuses_after_unkey() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        assert await managed.ptt_up("a") is ACCEPTED
        out: list[bool] = []
        await _enter_owned(managed, "a", out)
        assert out == [False]
    finally:
        await managed.close()


async def test_owned_submission_handoff_before_admission_verdict() -> None:
    """A frame suspended before admission loses the handoff (spec 8.3.5).

    The test takes the exclusion lock before the frame exists, so the
    frame cannot pass admission until the handoff below is complete —
    and then it resumes refused, so nothing of A reaches B's over.
    """
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        out: list[bool] = []
        await managed._outside_tx_lock.acquire()
        try:
            frame_task = asyncio.ensure_future(_enter_owned(managed, "a", out))
            assert await managed.ptt_up("a") is ACCEPTED
        finally:
            managed._outside_tx_lock.release()
        assert await managed.ptt_down("b") is ACCEPTED
        await frame_task
        assert out == [False]
        state = (await managed.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.PTT
        assert state.intent.owner_token == "b"
        assert await managed.ptt_up("b") is ACCEPTED
    finally:
        await managed.close()


async def test_owned_push_inside_excludes_the_rival_key() -> None:
    """A push suspended inside owns the exclusion until it returns."""
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        pushed: list[bytes] = []
        push_started = asyncio.Event()
        release_push = asyncio.Event()

        async def frame() -> None:
            async with managed.owned_tx_submission("a") as admitted:
                assert admitted is True
                push_started.set()
                await release_push.wait()
                pushed.append(b"a-frame")

        frame_task = asyncio.ensure_future(frame())
        await asyncio.wait_for(push_started.wait(), 1.0)
        rival = asyncio.ensure_future(managed.submit_ptt(True, "b"))
        done, _ = await asyncio.wait({rival}, timeout=0.2)
        assert rival not in done
        release_push.set()
        await frame_task
        assert pushed == [b"a-frame"]
        # B's key is decided only after A's frame finished: A still holds
        # the intent, so the rival is refused — the push was never
        # interleaved with B's keying.
        assert (await rival).outcome is REJECTED
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_owned_submission_refuses_when_closed_or_faulted() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        managed._state = replace(managed._state, last_error="release failed")
        out: list[bool] = []
        await _enter_owned(managed, "a", out)
        assert out == [False]
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()
    out_closed: list[bool] = []
    await _enter_owned(managed, "a", out_closed)
    assert out_closed == [False]


async def test_before_on_never_runs_while_another_owner_holds_tx() -> None:
    managed, _clock, lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("b") is ACCEPTED
        effects_before = list(lane.effects)
        calls: list[str] = []

        async def hook() -> bool:
            calls.append("ran")
            return True

        submission = managed.start_ptt_submission(
            True,
            "a",
            expires_at_monotonic=100.0 + 3.0,
            before_on=hook,
        )
        receipt = await submission
        assert receipt.outcome is REJECTED
        assert calls == []
        assert lane.effects == effects_before
        assert await managed.ptt_up("b") is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_blocks_a_rival_key_until_it_returns() -> None:
    managed, _clock, lane, _probe = radio_tx_authority()
    try:
        entered = asyncio.Event()
        release_hook = asyncio.Event()

        async def hook() -> bool:
            entered.set()
            await release_hook.wait()
            return True

        submission_a = managed.start_ptt_submission(
            True,
            "a",
            expires_at_monotonic=100.0 + 3.0,
            before_on=hook,
        )
        await asyncio.wait_for(entered.wait(), 1.0)
        rival = asyncio.ensure_future(managed.submit_ptt(True, "b"))
        done, _ = await asyncio.wait({rival}, timeout=0.2)
        assert rival not in done
        release_hook.set()
        assert (await submission_a).outcome is ACCEPTED
        assert (await rival).outcome is REJECTED
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_raise_leaves_rx_with_debt_and_no_ptt_on() -> None:
    managed, _clock, lane, _probe = radio_tx_authority()
    try:

        async def hook() -> bool:
            raise RuntimeError("route refused")

        submission = managed.start_ptt_submission(
            True,
            "a",
            expires_at_monotonic=100.0 + 3.0,
            before_on=hook,
        )
        receipt = await submission
        assert receipt.outcome is REJECTED
        assert submission_refused_for_release_debt(receipt) is True
        state = (await managed.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required is True
        assert state.last_error is not None
        assert [
            effect.operation
            for effect in lane.effects
            if effect.operation is ActuationOperation.PTT_ON
        ] == []
        assert await managed.force_off() is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_timeout_leaves_rx_with_debt_and_no_ptt_on() -> None:
    managed, _clock, lane, _probe = radio_tx_authority()
    try:

        async def hook() -> bool:
            await asyncio.sleep(30.0)
            return True

        submission = managed.start_ptt_submission(
            True,
            "a",
            expires_at_monotonic=100.0 + 30.0,
            before_on=hook,
        )
        receipt = await submission
        assert receipt.outcome is REJECTED
        assert submission_refused_for_release_debt(receipt) is True
        state = (await managed.snapshot()).state
        assert state.intent.kind is ManagedTxIntentKind.RX
        assert state.release_required is True
        assert [
            effect.operation
            for effect in lane.effects
            if effect.operation is ActuationOperation.PTT_ON
        ] == []
        assert await managed.force_off() is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_defaults_to_no_preparation() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:
        assert await managed.ptt_down("a") is ACCEPTED
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_may_read_the_authority_while_routing() -> None:
    """A hook awaiting an authority read must not deadlock (spec 7.3a).

    TCI prepares the route through the normal CommandIntent path, whose
    admission and projection reads take the authority lock. The hook runs
    after the intent commits, so those reads must proceed while it runs:
    the key admits and PTT_ON is dispatched.
    """
    managed, _clock, lane, _probe = radio_tx_authority()
    try:
        seen: list[str] = []

        async def hook() -> bool:
            projection = await managed.snapshot()
            seen.append(projection.state.intent.owner_token)
            return True

        submission = managed.start_ptt_submission(
            True,
            "a",
            expires_at_monotonic=100.0 + 3.0,
            before_on=hook,
        )
        receipt = await submission
        assert receipt.outcome is ACCEPTED
        assert seen == ["a"]
        assert [
            effect.operation
            for effect in lane.effects
            if effect.operation is ActuationOperation.PTT_ON
        ] != []
        assert await managed.ptt_up("a") is ACCEPTED
    finally:
        await managed.close()


async def test_before_on_is_rejected_for_an_unkey() -> None:
    managed, _clock, _lane, _probe = radio_tx_authority()
    try:

        async def hook() -> bool:
            return True

        with pytest.raises(ValueError, match="before_on"):
            managed.start_ptt_submission(
                False, "a", expires_at_monotonic=None, before_on=hook
            )
    finally:
        await managed.close()
