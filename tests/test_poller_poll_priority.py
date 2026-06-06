"""MOR-497(i): background polls must run at Priority.BACKGROUND so user
commands are never de-prioritized on the shared CI-V lane.

Deterministic priority assertions (not timing): every poll send-site must
pass ``priority=Priority.BACKGROUND`` to ``radio.send_civ``, while user
commands routed through ``_execute`` must stay at the NORMAL default.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.commands.commander import Priority
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetFreq


def _make_radio(*, model: str = "IC-7610", active: str = "MAIN") -> MagicMock:
    """A CI-V-capable radio mock: ``send_civ`` is the lane the poller hits.

    A ``MagicMock`` satisfies ``CivCommandCapable`` (runtime-checkable
    protocol), so ``RadioPoller._civ`` reaches ``send_civ``.
    """
    profile = resolve_radio_profile(model=model)
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio._radio_state = SimpleNamespace(active=active)
    radio.send_civ = AsyncMock()
    radio.set_freq = AsyncMock()
    radio.set_mode = AsyncMock()
    return radio


def _priority_of(call) -> Priority | None:
    """Extract the ``priority`` kwarg from a ``send_civ`` call (None if absent)."""
    return call.kwargs.get("priority")


def _wait_dispatch_of(call) -> object:
    """Extract the ``wait_dispatch`` kwarg from a ``send_civ`` call (None if absent)."""
    return call.kwargs.get("wait_dispatch")


@pytest.mark.asyncio
async def test_meter_poll_sends_background_priority() -> None:
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    # poll_index 0 → even cycle → meter query.
    assert poller._poll_index % 2 == 0  # noqa: SLF001
    await poller._send_query()  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND


@pytest.mark.asyncio
async def test_state_query_sends_background_priority() -> None:
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_one_state_query(0x03, None, None)  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND


@pytest.mark.asyncio
async def test_unselected_slot_poll_sends_background_priority() -> None:
    # IC-7300 has swap_ab_code set and receiver_count == 1, so the
    # unselected-slot gate is satisfiable with a simple swap→query→swap-back.
    radio = _make_radio(model="IC-7300", active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    assert poller._unselected_slot_gate(0)  # noqa: SLF001  (sanity: gate open)
    await poller._poll_unselected_slot(0)  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND


@pytest.mark.asyncio
async def test_state_query_sends_fire_and_forget() -> None:
    """MOR-497(ii): the state-query poll path must be fire-and-forget so the
    poll burst does not park the poll loop on the commander future.

    Every ``send_civ`` from ``_send_one_state_query`` must be BACKGROUND AND
    carry ``wait_dispatch=False``.
    """
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._send_one_state_query(0x03, None, None)  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND
        assert _wait_dispatch_of(call) is False


@pytest.mark.asyncio
async def test_user_command_stays_normal_priority() -> None:
    """KEY GUARD: a user command's CI-V sends (including the in-command VFO
    switch) must NOT be de-prioritized to BACKGROUND, and must NEVER be made
    fire-and-forget (``wait_dispatch`` must stay blocking)."""
    # active="SUB" so SetFreq(receiver=0) triggers the in-command VFO switch
    # (_civ(0x07, vfo_main_code)) at radio_poller.py and its restore.
    radio = _make_radio(active="SUB")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._execute(SetFreq(14_074_000, receiver=0))  # noqa: SLF001

    # The VFO switch + restore fire via send_civ; none may be BACKGROUND, and
    # NONE may be fire-and-forget — user commands stay blocking/awaited.
    assert radio.send_civ.await_count >= 1
    for call in radio.send_civ.await_args_list:
        prio = _priority_of(call)
        assert prio in (None, Priority.NORMAL), (
            f"user command send_civ used {prio!r}, must not be BACKGROUND"
        )
        wd = _wait_dispatch_of(call)
        assert wd in (None, True), (
            f"user command send_civ used wait_dispatch={wd!r}, must not be False"
        )
