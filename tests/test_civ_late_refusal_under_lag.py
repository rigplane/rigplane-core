"""Scope startup read under a slow link, end to end over LAN (MOR-4526).

The web poller sends its reads fire-and-forget; the radio refuses some of
them with a bare FA, which names no command. Before a blocking read the
runtime releases older fire-and-forget sinks after a short grace
(``_drain_ack_sinks_before_blocking``, 120 ms). On a link whose replies lag
longer than that, the refusals of the released reads arrive after the
blocking read is on the wire. The scope session's startup read
(``get_scope_session_state``, CI-V ``27 10``) then took a stranger's FA as
its own refusal, the scope subscription failed, and the browser received no
panorama frames at all (the slow-link tuning row, 0.5 s reply lag).

Real LAN simulator (``MockIcomRadio`` with ``response_delay``), real
``IcomRadio`` and its CI-V pump; nothing in the tracker is faked.
"""

from __future__ import annotations

import pytest

from rigplane.commands.commander import Priority
from rigplane.core.exceptions import CommandRejectedError
from rigplane.radio import IcomRadio
from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

#: A read the simulator refuses with a bare FA (scope sub-command 1D is not
#: one of its recorded forms).
_REFUSED_READ = (0x27, 0x1D)
#: Longer than the 120 ms drain grace — the reply lag of the failing row.
_REPLY_LAG_S = 0.5


async def _connect(sim: MockIcomRadio) -> IcomRadio:
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=2.0,
        model="IC-7610",
    )
    with fast_connect():
        await radio.connect()
    return radio


@pytest.mark.asyncio
async def test_scope_startup_read_survives_late_refusals_of_released_polls() -> None:
    sim = MockIcomRadio()
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            # Precondition: the simulator really refuses this read.
            with pytest.raises(CommandRejectedError):
                await radio.send_civ(*_REFUSED_READ, wait_response=True)

            sim.response_delay = _REPLY_LAG_S
            for _ in range(3):
                await radio.send_civ(
                    *_REFUSED_READ, wait_response=False, priority=Priority.BACKGROUND
                )

            panel, output = await radio.get_scope_session_state()
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()
    assert (panel, output) == (bool(sim._scope_10), bool(sim._scope_11))  # noqa: SLF001
