"""MOR-497(i)/(ii): the user-command side of the priority guarantee
(NORMAL priority, blocking dispatch) used to be checkable here, back when
the poller built the VFO-switch CI-V frame itself. Since that switch now goes through the public
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
from unittest.mock import AsyncMock, MagicMock

import pytest

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


@pytest.mark.asyncio
async def test_user_command_main_write_while_sub_active_never_switches() -> None:
    """A receiver=0 write while SUB is active is delegated unchanged: the
    poller sends no receiver select before or after it and no raw
    ``send_civ`` (MOR-3591). Whether MAIN may take it is the backend's
    decision (``CoreRadio`` refuses it while SUB is selected)."""
    radio = _make_radio(active="SUB")
    poller = RadioPoller(radio, CommandQueue(), radio_state=RadioState())

    await poller._execute(SetFreq(14_074_000, receiver=0))  # noqa: SLF001

    radio.select_receiver.assert_not_awaited()
    radio.set_freq.assert_awaited_once_with(14_074_000)
    radio.send_civ.assert_not_awaited()
