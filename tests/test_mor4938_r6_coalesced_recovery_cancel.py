"""MOR-4938 item 2 (R6): a coalesced waiter's cancel kills the shared LAN recovery.

RED probe — tests only, no product change. An SDK caller that coalesces onto
the in-flight lifecycle recovery (``CoreRadioSessionLifecycle.soft_reconnect``
awaiting the shared ``_recover_task``) and then cancels its own await forwards
the cancellation into the shared run: ``Task.cancel()`` cancels the task's own
``_fut_waiter``, which here IS the shared recovery task. The codebase states
the rule at ``src/rigplane/runtime/_civ_rx.py:1180-1194``, and MOR-2083 fixed
the poller layer the same way (``asyncio.wait`` never exposes the shared task
as a waiter's ``_fut_waiter``). The shared attempt dies wherever it is parked,
the lifecycle strands in RECOVERING with no task, and the watchdog handoff
that was awaiting it ends cancelled, so it never re-arms the data watchdog —
a key held across the gap then has nobody watching it.

Marked ``xfail(strict=True)``: must FAIL on main (the bug reproduces) and
XPASS once the shared run is insulated from waiter cancellation, which forces
the marker off.
"""

from __future__ import annotations

import asyncio
from collections.abc import Generator
from unittest.mock import MagicMock, patch

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.session_lifecycle import LifecycleState
from rigplane.testing.timing import VirtualClock, await_until
from test_radio import MockTransport


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(transport: MockTransport) -> Generator[IcomRadio, None, None]:
    r = IcomRadio("192.168.1.100", model="IC-7610")
    # CI-V path down: this is why recovery runs. Control session alive (the
    # re-arm below may proceed): the mock has no UDP marker of its own.
    r._civ_transport = None
    r._ctrl_transport = transport
    setattr(transport, "_udp_transport", object())
    r._conn_state = RadioConnectionState.CONNECTED
    r._civ_recovering = False
    r._civ_runtime._reconnect_task = None
    r._civ_runtime._recovery_cleaned_task = None
    r._civ_runtime._pending_replacement = None
    r._civ_runtime._fast_reconnect_task = None
    yield r
    r._connected = False


@pytest.mark.timeout(30)
@pytest.mark.xfail(strict=True, reason="MOR-4938 R6")
async def test_coalesced_waiter_cancel_leaves_shared_recovery_alive(
    radio: IcomRadio,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> None:
    """The shared recovery survives a coalesced SDK waiter's own cancellation.

    Uses the simulator transport and the shared virtual loop clock. Nothing on
    this path sleeps (the parked attempt resolves via an event, the in-process
    radio's recovery backoff is zero), so no ``elapse`` is needed and the
    ``await_until`` bounds below stay on real time.
    """
    _ = virtual_clock
    lifecycle = radio._session_lifecycle
    entered = asyncio.Event()
    release = asyncio.Event()

    async def parked_attempt() -> None:
        entered.set()
        await release.wait()

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", parked_attempt)

    cleanups: list[str] = []

    async def record_cleanup() -> None:
        cleanups.append("force_cleanup")

    monkeypatch.setattr(radio, "_force_cleanup_civ", record_cleanup)

    with patch.object(radio._civ_runtime, "start_data_watchdog", MagicMock()) as rearm:
        started = radio._civ_runtime.request_recovery_now("simulated CI-V stall")
        assert started
        watchdog = radio._civ_runtime._reconnect_task
        assert watchdog is not None
        await await_until(
            lambda: lifecycle._recover_task is not None and entered.is_set(),
            timeout=10.0,
            description="the watchdog recovery reaching its parked attempt",
        )
        shared = lifecycle._recover_task
        assert shared is not None

        # SDK caller coalesces onto the shared run. Observing the registration
        # proves it is already suspended in ``await recovery`` (registration
        # and suspension are one step with no await between), so the cancel
        # below cannot land early.
        sdk: asyncio.Task[None] | None = None
        try:
            sdk = asyncio.create_task(radio.soft_reconnect())
            await await_until(
                lambda: lifecycle._recovery_waiters.get(sdk) is shared,
                timeout=10.0,
                description="the SDK caller coalescing onto the shared recovery",
            )

            sdk.cancel()
            await asyncio.gather(sdk, return_exceptions=True)
            assert sdk.cancelled()

            # Let the (surviving) shared run finish.
            release.set()
            await asyncio.gather(watchdog, return_exceptions=True)

            # Desired end-state. On main the waiter's cancel propagates into
            # the shared task, so the lifecycle strands in RECOVERING with no
            # task and the watchdog never re-arms.
            assert shared is not None and not shared.cancelled()
            assert watchdog.done() and not watchdog.cancelled()
            assert lifecycle.state is LifecycleState.CONNECTED
            assert cleanups == ["force_cleanup"]
            assert rearm.called
            assert lifecycle._recover_task is None
            assert radio._civ_runtime._reconnect_task is None
            assert lifecycle._recovery_waiters == {}
        finally:
            release.set()
            for task in (sdk, watchdog, shared):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (sdk, watchdog, shared) if task is not None),
                return_exceptions=True,
            )
