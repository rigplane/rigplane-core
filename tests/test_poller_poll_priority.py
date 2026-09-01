"""MOR-497(i): background polls must run at Priority.BACKGROUND so user
commands are never de-prioritized on the shared CI-V lane.

Deterministic priority assertions (not timing): every poll send-site must
pass ``priority=Priority.BACKGROUND`` to ``radio.send_civ``. The user-command
side of the MOR-497(i)/(ii) guarantee (NORMAL priority, blocking dispatch)
used to be checkable here too, back when the poller built the VFO-switch
CI-V frame itself. Since that switch now goes through the public
``radio.select_receiver`` API (no priority/wait_dispatch parameters of its
own), a poller-level mock can no longer observe what priority the frame
goes out at — this file only checks that ``_execute`` routes to
``select_receiver`` instead of a raw ``send_civ`` call. The actual
NORMAL/blocking pin now lives one layer down, in
``tests/test_radio_coverage.py::test_select_receiver_vfo_switch_stays_normal_priority_and_blocking``,
which drives ``CoreRadio.select_receiver`` against a mocked commander.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from unittest.mock import call as mock_call

import pytest

from rigplane.commands.commander import Priority
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import CommandQueue, RadioPoller, SetFreq

# MOR-1884: this suite drives ``RadioPoller._execute`` directly to exercise
# dispatch bodies; the interlock seat now lives at its head, so the RF
# premise is stated once here (see the fixture docstring in conftest.py).
pytestmark = pytest.mark.usefixtures("observed_rx_dispatch_premise")


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
    # The receiver=0-while-SUB-active branch restores MAIN via the public
    # select_receiver API (not a hand-built 0x07 CI-V frame); a bare
    # MagicMock attribute is not awaitable, so tests reaching that path
    # raise TypeError without this.
    radio.select_receiver = AsyncMock()
    return radio


def _priority_of(call) -> Priority | None:
    """Extract the ``priority`` kwarg from a ``send_civ`` call (None if absent)."""
    return call.kwargs.get("priority")


def _wait_dispatch_of(call) -> object:
    """Extract the ``wait_dispatch`` kwarg from a ``send_civ`` call (None if absent)."""
    return call.kwargs.get("wait_dispatch")


def logical_civ_call(
    call: Any, *, selected_unselected: bool = False
) -> tuple[int | None, int, int | None, bytes]:
    """Normalize a recorded send_civ call to route, command, sub, and data."""
    command = call.args[0]
    sub = call.kwargs.get("sub")
    data = call.kwargs.get("data", b"")
    if data is None:
        data = b""
    if command == 0x29:
        if sub is not None:
            raise AssertionError("cmd29 outer sub must be absent")
        if len(data) < 2:
            raise AssertionError("cmd29 must carry receiver and inner command")
        receiver, command, *inner = data
        return receiver, command, inner[0] if inner else None, bytes(inner[1:])
    if (
        selected_unselected
        and command in (0x25, 0x26)
        and sub is None
        and data == b"\x01"
    ):
        return None, command, 0x01, b""
    if command in (0x25, 0x26) and sub is None and data in (b"\x00", b"\x01"):
        return data[0], command, None, b""
    return None, command, sub, data


def test_logical_civ_call_public_send_defaults_and_cmd29_validation() -> None:
    assert logical_civ_call(mock_call(0x18)) == (None, 0x18, None, b"")
    assert logical_civ_call(mock_call(0x16, sub=0x57)) == (None, 0x16, 0x57, b"")
    assert logical_civ_call(mock_call(0x16, data=None)) == (None, 0x16, None, b"")
    with pytest.raises(AssertionError, match="outer sub"):
        logical_civ_call(mock_call(0x29, sub=0x42, data=b"\x00\x16"))


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

    for state_idx in range(len(poller._STATE_QUERIES)):  # noqa: SLF001
        poller._poll_index = 2 * state_idx + 1  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    calls = [logical_civ_call(call) for call in radio.send_civ.await_args_list]
    assert any(receiver is not None for receiver, _, _, _ in calls)
    assert any(command == 0x18 for _, command, _, _ in calls)
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND
        assert _wait_dispatch_of(call) is False


@pytest.mark.asyncio
async def test_unselected_slot_poll_sends_no_vfo_selection_frames() -> None:
    radio = _make_radio(model="IC-7300", active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    assert not poller._unselected_slot_gate(0)  # noqa: SLF001
    await poller._poll_unselected_slot(0)  # noqa: SLF001

    radio.send_civ.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_query_sends_fire_and_forget() -> None:
    """MOR-497(ii): the state-query poll path must be fire-and-forget so the
    poll burst does not park the poll loop on the commander future.

    Every ``send_civ`` from ``_send_one_state_query`` must be BACKGROUND AND
    carry ``wait_dispatch=False``.
    """
    radio = _make_radio(active="MAIN")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    for state_idx in range(len(poller._STATE_QUERIES)):  # noqa: SLF001
        poller._poll_index = 2 * state_idx + 1  # noqa: SLF001
        await poller._send_query()  # noqa: SLF001

    assert radio.send_civ.await_count >= 1
    calls = [logical_civ_call(call) for call in radio.send_civ.await_args_list]
    assert any(receiver is not None for receiver, _, _, _ in calls)
    assert any(command == 0x18 for _, command, _, _ in calls)
    for call in radio.send_civ.await_args_list:
        assert _priority_of(call) == Priority.BACKGROUND
        assert _wait_dispatch_of(call) is False


@pytest.mark.asyncio
async def test_user_command_vfo_switch_routes_through_select_receiver() -> None:
    """The receiver=0-while-SUB-active in-command VFO switch goes through
    ``radio.select_receiver`` (a public API with no priority/wait_dispatch
    parameters of its own) instead of a hand-built ``_civ(0x07, ...)``
    frame — this checks the ``select_receiver`` calls the poller makes and
    that no raw ``send_civ`` is used for this path.

    This does NOT check what priority or dispatch mode the CI-V frame
    ``select_receiver`` emits under the hood — a bare poller-level mock
    replaces ``select_receiver`` entirely, so it cannot observe that. The
    MOR-497(i)/(ii) NORMAL-priority/blocking-dispatch guarantee for that
    frame is pinned at
    ``tests/test_radio_coverage.py::test_select_receiver_vfo_switch_stays_normal_priority_and_blocking``
    instead, against a real ``CoreRadio.select_receiver`` and a mocked
    commander.
    """
    # active="SUB" so SetFreq(receiver=0) triggers the in-command
    # select_receiver(MAIN) switch and its select_receiver(SUB) restore.
    radio = _make_radio(active="SUB")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._execute(SetFreq(14_074_000, receiver=0))  # noqa: SLF001

    assert radio.select_receiver.await_args_list == [mock_call(0), mock_call(1)]
    radio.set_freq.assert_awaited_once_with(14_074_000)
    radio.send_civ.assert_not_awaited()
