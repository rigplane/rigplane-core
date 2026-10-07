"""Controller capabilities fence transport groups before retained TX cleanup."""

from __future__ import annotations

import asyncio

import pytest

from test_managed_tx_authority import authority as managed_authority

from rigplane.runtime._poller_types import (
    CommandQueue,
    PttOff,
    SetFreq,
    validate_command_queue_entry_currency,
)
from rigplane.runtime.controller_authority import ControllerAuthority, ControllerError
from rigplane.runtime.managed_tx_state import ActuationOperation, ManagedTxOutcome


class Safety:
    def __init__(self) -> None:
        self.now = 10.0
        self.safe = True
        self.cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()
        self.cleanups = 0

    async def cleanup(self) -> None:
        self.cleanups += 1
        self.cleanup_started.set()
        await self.cleanup_release.wait()

    def authority(self) -> ControllerAuthority:
        return ControllerAuthority(
            ready=lambda: self.safe,
            cleanup=self.cleanup,
            clock=lambda: self.now,
        )


@pytest.mark.parametrize(
    "kinds", [("browser", "browser"), ("browser", "pro"), ("pro", "pro")]
)
async def test_atomic_acquire_and_no_client_selected_identity(kinds) -> None:
    safety = Safety()
    authority = safety.authority()
    with pytest.raises(ControllerError, match="remote_disabled"):
        authority.acquire(kinds[0])
    await authority.set_mode("remote")

    async def acquire(kind):
        try:
            return authority.acquire(kind)
        except ControllerError as exc:
            return exc.code

    results = await asyncio.gather(*(acquire(kind) for kind in kinds))
    winner = next(item for item in results if isinstance(item, dict))
    assert results.count("controller_busy") == 1
    assert len(winner["controller_key"]) == 64
    assert winner["ttl_ms"] == 6000
    assert winner["heartbeat_ms"] == 2000
    assert winner["controller_key"] not in repr(authority.status())
    await authority.release(winner["controller_key"])


async def test_only_primary_renews_and_duplicate_primary_is_refused() -> None:
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("pro")
    key = grant["controller_key"]
    primary = authority.attach(key, "primary", "native")
    auxiliary = authority.attach(key, "auxiliary", "frontend")
    with pytest.raises(ControllerError, match="controller_busy"):
        authority.attach(key, "primary", "second")
    for invalid in (None, "wrong", key + ",duplicate", "я" * 64):
        with pytest.raises(ControllerError, match="controller_invalid"):
            authority.attach(invalid, "auxiliary", "bad")
    safety.now += 4
    with pytest.raises(ControllerError, match="controller_primary_required"):
        authority.heartbeat(auxiliary)
    assert authority.heartbeat(primary)["type"] == "controller_heartbeat"
    safety.now += 4
    authority.validate(auxiliary)
    await authority.release(key)


async def test_expiry_fences_immediately_before_cleanup_and_takeback_waits() -> None:
    safety = Safety()
    authority = safety.authority()
    local = authority.capture()
    await authority.set_mode("remote")
    with pytest.raises(ControllerError):
        authority.validate(local)
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    safety.cleanup_release.clear()
    safety.now += 6
    with pytest.raises(ControllerError, match="controller_invalid"):
        authority.validate(ticket)
    await asyncio.wait_for(safety.cleanup_started.wait(), 2)
    assert authority.status()["state"] == "revoking"
    with pytest.raises(ControllerError, match="controller_busy"):
        authority.acquire("browser")
    takeback = asyncio.create_task(authority.set_mode("local"))
    await asyncio.sleep(0)
    assert not takeback.done()
    assert authority.status()["mode"] == "remote"
    safety.safe = False
    safety.cleanup_release.set()
    with pytest.raises(ControllerError, match="controller_not_ready"):
        await takeback
    assert authority.status()["state"] == "blocked"
    assert authority.status()["mode"] == "remote"
    safety.safe = True
    await authority.set_mode("local")
    assert authority.status()["mode"] == "local"
    assert safety.cleanups == 1


async def test_old_detach_cannot_revoke_new_generation_and_no_replay() -> None:
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    old = authority.acquire("browser")
    ticket = authority.attach(old["controller_key"], "primary", "old-tab")
    await authority.release(old["controller_key"])
    fresh = authority.acquire("browser")
    new_ticket = authority.attach(fresh["controller_key"], "primary", "new-tab")
    assert fresh["generation"] > old["generation"]
    assert fresh["controller_key"] != old["controller_key"]
    authority.detach(ticket)
    authority.validate(new_ticket)
    for operation in (
        lambda: authority.validate(ticket),
        lambda: authority.heartbeat(ticket),
    ):
        with pytest.raises(ControllerError):
            operation()
    await authority.release(fresh["controller_key"])


async def test_primary_loss_closes_group_and_provider_revocation_is_retained() -> None:
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("pro")
    primary = authority.attach(grant["controller_key"], "primary", "native")
    auxiliary = authority.attach(grant["controller_key"], "auxiliary", "frontend")
    closed = []
    authority.add_channel(auxiliary, lambda: closed.append("frontend"))
    authority.detach(primary)
    with pytest.raises(ControllerError):
        authority.validate(auxiliary)
    await authority.settle()
    assert closed == ["frontend"]
    assert safety.cleanups == 1
    grant = authority.acquire("browser")
    authority.revoke()
    await authority.settle()
    with pytest.raises(ControllerError):
        authority.attach(grant["controller_key"], "primary", "late")


async def test_release_cancellation_retains_cleanup_and_auxiliary_cannot_claim_primary() -> (
    None
):
    safety = Safety()
    authority = safety.authority()
    await authority.set_mode("remote")
    grant = authority.acquire("pro")
    with pytest.raises(ControllerError, match="controller_primary_required"):
        authority.attach(grant["controller_key"], "auxiliary", "early")
    safety.cleanup_release.clear()
    request = asyncio.create_task(authority.release(grant["controller_key"]))
    await asyncio.wait_for(safety.cleanup_started.wait(), 2)
    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request
    assert authority.status()["state"] == "revoking"
    safety.cleanup_release.set()
    await authority.settle()
    assert authority.status()["state"] == "idle"
    assert safety.cleanups == 1


async def test_arm_and_acquire_fail_closed_with_unsafe_radio() -> None:
    safety = Safety()
    authority = safety.authority()
    safety.safe = False
    with pytest.raises(ControllerError, match="controller_not_ready"):
        await authority.set_mode("remote")
    assert authority.status()["mode"] == "local"
    safety.safe = True
    await authority.set_mode("remote")
    safety.safe = False
    with pytest.raises(ControllerError, match="controller_not_ready"):
        authority.acquire("browser")


async def test_queue_captures_currency_even_when_local_and_safe_off_survives() -> None:
    safety = Safety()
    authority = safety.authority()
    queue = CommandQueue()
    queue.bind_controller(authority)
    local = queue.put_ordered(SetFreq(100))
    await authority.set_mode("remote")
    grant = authority.acquire("browser")
    ticket = authority.attach(grant["controller_key"], "primary", "tab")
    with pytest.raises(ControllerError):
        queue.put(SetFreq(200))
    with authority.bind(ticket):
        remote = queue.put_ordered(SetFreq(300))
    off = queue.put_ordered(PttOff())
    currency = dict(
        now=safety.now,
        provider_generation=None,
        connection_generation=None,
        session_is_live=lambda _: True,
    )
    with pytest.raises(ControllerError):
        validate_command_queue_entry_currency(local, **currency)
    validate_command_queue_entry_currency(remote, **currency)
    authority.revoke()
    with pytest.raises(ControllerError):
        validate_command_queue_entry_currency(remote, **currency)
    validate_command_queue_entry_currency(off, **currency)
    await authority.settle()


async def test_managed_positive_admission_requires_current_matching_owner() -> None:
    safety = Safety()
    authority = safety.authority()
    managed, _, _, _, _, lane = managed_authority()
    managed.bind_controller(authority)
    try:
        await authority.set_mode("remote")
        grant = authority.acquire("pro")
        ticket = authority.attach(grant["controller_key"], "primary", "native")
        with pytest.raises(ControllerError):
            managed.start_ptt_submission(True, "host-local")
        with authority.bind(ticket):
            with pytest.raises(ControllerError, match="controller_tx_unsupported"):
                managed.start_transmit_on_submission()
            with pytest.raises(ControllerError, match="controller_tx_unsupported"):
                managed.start_ptt_submission(True, "other-owner")
            ready = asyncio.get_running_loop().create_future()
            pending = managed.start_ptt_submission(True, "native", ready=ready)
        authority.revoke()
        ready.set_result(None)
        with pytest.raises(ControllerError):
            await pending
        assert lane.effects == []
        receipt = await managed.submit_force_off()
        await receipt.wait_settlement()
        assert lane.effects[-1].operation is ActuationOperation.FORCE_RECEIVE
    finally:
        await authority.settle()
        await managed.close()


async def test_revocation_invalidates_positive_provider_write_guard_before_cleanup() -> (
    None
):
    safety = Safety()
    authority = safety.authority()
    managed, _, _, _, _, lane = managed_authority()
    managed.bind_controller(authority)
    gate = lane.block_next()
    try:
        await authority.set_mode("remote")
        grant = authority.acquire("browser")
        ticket = authority.attach(grant["controller_key"], "primary", "tab")
        with authority.bind(ticket):
            pending = managed.start_ptt_submission(True, "tab")
        receipt = await pending
        await asyncio.wait_for(lane.started.get(), 2)
        assert lane.guards[0]() is True
        auxiliary = authority.attach(grant["controller_key"], "auxiliary", "other")
        with authority.bind(auxiliary):
            rejected = await managed.start_ptt_submission(True, "other")
        assert rejected.outcome is ManagedTxOutcome.REJECTED
        await rejected.wait_settlement()
        assert lane.guards[0]() is True
        safety.cleanup_release.clear()
        authority.revoke()
        assert lane.guards[0]() is False
        gate.set()
        await receipt.wait_settlement()
    finally:
        gate.set()
        safety.cleanup_release.set()
        await authority.settle()
        off = await managed.submit_force_off()
        await off.wait_settlement()
        await managed.close()
