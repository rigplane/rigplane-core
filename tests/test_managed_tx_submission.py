"""Managed-key submission choreography shared by every keying ingress.

``rigplane.runtime.managed_tx_submission`` is the one extracted implementation
of the machinery that used to live in ``rigctld`` only (MOR-3821 P0c): the
per-connection key ordering gate (``ManagedPttReady``), the key/unkey
submission start, the managed-PTT / managed-tuner execution through the
ordered command queue, and the fail-soft session release. ``rigctld`` calls
this module; a future TCI adapter uses the same entry points with
``source="tci"``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import get_args

import pytest

from test_managed_tx_web_projection import _gate_authority

from rigplane.core.state_pipeline_contracts import CommandSource
from rigplane.core.tx_safety import TxSource
from rigplane.runtime.managed_tx_ingress import (
    _STABLE_OWNER_SOURCES,
    bind_managed_tx,
)
from rigplane.runtime.managed_tx_submission import (
    ManagedKeyRefused,
    ManagedKeySetupError,
    ManagedPttReady,
    ManagedPttTracker,
    execute_managed_ptt,
    release_managed_session_tx,
)


async def _failing_release(session_id: str) -> None:
    raise RuntimeError(f"release of {session_id} failed")


async def test_key_ready_is_superseded_by_a_later_unkey() -> None:
    revocation = asyncio.Event()
    ready = ManagedPttReady(
        revocation,
        expires_at_monotonic=asyncio.get_running_loop().time() + 3.0,
        connection_generation=object(),
    )
    assert ready.superseded is False
    revocation.set()
    assert ready.superseded is True
    # A superseded ready never resolves: the stale key must not admit.
    ready.set_result(None)
    assert ready.done() is False


async def test_key_ready_resolves_when_validation_and_queue_release_meet() -> None:
    revocation = asyncio.Event()
    ready = ManagedPttReady(
        revocation,
        expires_at_monotonic=asyncio.get_running_loop().time() + 3.0,
        connection_generation=object(),
    )
    ready.set_result(None)
    assert ready.done() is False
    ready.release_queue()
    assert ready.done() is True


async def test_tracker_starts_a_key_with_a_live_ready() -> None:
    managed = _gate_authority()
    try:
        tracker = ManagedPttTracker()
        generation = object()
        submission, ready = tracker.begin_key(
            managed,
            owner="rigctld-client-1",
            on=True,
            expires_at_monotonic=asyncio.get_running_loop().time() + 3.0,
            connection_generation=generation,
        )
        try:
            assert ready is not None
            assert ready.connection_generation is generation
            assert ready.superseded is False
            assert ready.expires_at_monotonic > 0.0
            # The admission block waits on both halves of this ready: the
            # validation (as the executor's set) and the queue turn's
            # release. Releasing both admits.
            ready.set_result(None)
            assert not ready.done()
            ready.release_queue()
            receipt = await submission
            assert receipt.transition.state.intent.owner_token == "rigctld-client-1"
        finally:
            if not submission.done():
                submission.cancel()
            await asyncio.gather(submission, return_exceptions=True)
        await managed.ptt_up("rigctld-client-1")
    finally:
        await managed.close()


async def test_tracker_unkey_supersedes_the_pending_key() -> None:
    managed = _gate_authority()
    try:
        tracker = ManagedPttTracker()
        key_submission, ready = tracker.begin_key(
            managed,
            owner="rigctld-client-1",
            on=True,
            expires_at_monotonic=asyncio.get_running_loop().time() + 3.0,
            connection_generation=object(),
        )
        assert ready is not None
        unkey_submission, unkey_ready = tracker.begin_key(
            managed,
            owner="rigctld-client-1",
            on=False,
            expires_at_monotonic=asyncio.get_running_loop().time() + 3.0,
            connection_generation=object(),
        )
        assert unkey_ready is None
        assert ready.superseded is True
        for pending in (key_submission, unkey_submission):
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    finally:
        await managed.close()


async def test_release_calls_the_handler_release_and_returns() -> None:
    released: list[str] = []

    async def _release(session_id: str) -> None:
        released.append(session_id)

    await release_managed_session_tx(_release, "s1", handback_timeout_s=1.0)
    assert released == ["s1"]


async def test_release_without_a_handler_release_is_a_noop() -> None:
    await release_managed_session_tx(None, "s1", handback_timeout_s=1.0)


async def test_release_that_raises_is_fail_soft() -> None:
    await release_managed_session_tx(_failing_release, "s1", handback_timeout_s=1.0)


async def test_release_that_hangs_is_abandoned_after_the_bound() -> None:
    started = asyncio.get_running_loop().time()

    async def _hang(session_id: str) -> None:
        del session_id
        await asyncio.sleep(30.0)

    await release_managed_session_tx(_hang, "s1", handback_timeout_s=0.05)
    assert asyncio.get_running_loop().time() - started < 30.0


async def test_managed_ptt_executor_rejects_a_missing_submission() -> None:
    from rigplane.runtime._poller_types import CommandQueue

    managed = _gate_authority()
    try:
        with pytest.raises(ManagedKeySetupError):
            await execute_managed_ptt(
                authority=managed,
                queue=CommandQueue(),
                owner="rigctld-client-1",
                on=True,
                submission=None,
                ready=None,
                source="rigctld",
                provider_generation=7,
            )
    finally:
        await managed.close()


def test_tci_is_a_classified_command_source_with_a_stable_owner() -> None:
    assert "tci" in get_args(CommandSource)
    assert _STABLE_OWNER_SOURCES["tci"] is TxSource.TCI


def test_tci_source_binds_a_managed_lease() -> None:
    radio = SimpleNamespace(managed_tx=object())
    bound = bind_managed_tx(radio, "tci", "tci-client-1")
    assert bound is not None
    assert bound.owner.session_id == "tci-client-1"


def test_managed_key_refused_is_neutral_to_ingress() -> None:
    assert issubclass(ManagedKeyRefused, Exception)
    assert issubclass(ManagedKeySetupError, Exception)
    assert not issubclass(ManagedKeyRefused, ManagedKeySetupError)
