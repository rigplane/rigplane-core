"""MOR-4416: LAN ordered ingress re-anchors after a sequence-numbering jump.

Through the real LAN stack (``IcomRadio`` + ``IcomCivTransport``) against the
testkit simulator: the first numbered CI-V packet a fresh client sees carries
a number far ahead of the stream that follows (``civ_tx_seq_jump`` — the
owner's IC-705 field log, a number from the session that just ended). Every
fresh packet behind that anchor must not drop forever: after a run longer
than the reorder window the transport re-anchors onto the live stream and
CI-V answers reach the parser — long before the radio's counter could pass
the stale anchor and before the 60 s CI-V data watchdog fires.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.core.transport import REORDER_WINDOW
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect
from testkit.ic705_fakes import ic705_lan_fake
from testkit.ic7610_fakes import ic7610_lan_fake

pytestmark = pytest.mark.timeout(90)

_REALTIME = pytest.mark.realtime(reason="real UDP simulator, real reconnect timing")

# Far ahead of any live stream, like the ~20000-ahead number in the field log.
_JUMP = 20000
# The simulator streams unsolicited freq reports fast so the behind-run that
# triggers the re-anchor builds in well under a second.
_STREAM_INTERVAL_S = 0.002
# Generous outer bound: re-anchor (~1 s of stream) plus the identity re-read
# backoff must finish long before the 60 s CI-V data watchdog could fire.
_READY_DEADLINE_S = 20.0


def _sim(factory, **kw) -> MockIcomRadio:
    return factory(
        single_owner=True,
        keepalive_hold_s=30.0,
        civ_tx_seq_jump=_JUMP,
        unsolicited_civ=True,
        unsolicited_interval_s=_STREAM_INTERVAL_S,
        **kw,
    )


async def _wait_connected(radio: IcomRadio) -> None:
    """Wait out the identity hold: CONNECTED once the first answers arrive."""
    deadline = asyncio.get_running_loop().time() + _READY_DEADLINE_S
    while radio.conn_state is not RadioConnectionState.CONNECTED:
        assert asyncio.get_running_loop().time() < deadline, (
            f"radio never reached CONNECTED (conn_state={radio.conn_state})"
        )
        await asyncio.sleep(0.05)


@pytest.mark.parametrize(
    "factory, model",
    [(ic705_lan_fake, "IC-705"), (ic7610_lan_fake, "IC-7610")],
    ids=["ic705", "ic7610"],
)
@_REALTIME
async def test_stale_high_first_packet_reanchors_and_civ_answers(
    factory, model: str
) -> None:
    sim = _sim(factory)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
        timeout=0.5,
    )
    try:
        with fast_connect():
            await asyncio.wait_for(radio.connect(), 10.0)
            await _wait_connected(radio)
            freq = await asyncio.wait_for(radio.get_freq(), 5.0)
        assert freq > 0
        stats = radio._civ_transport.rx_order_stats  # noqa: SLF001
        assert stats.reanchored == 1
        # The run up to the window still counts as duplicates — bounded, not
        # the thousands the field log showed.
        assert stats.duplicate <= REORDER_WINDOW
    finally:
        await radio.disconnect()
        await sim.stop()


@pytest.mark.parametrize(
    "factory, model",
    [(ic705_lan_fake, "IC-705"), (ic7610_lan_fake, "IC-7610")],
    ids=["ic705", "ic7610"],
)
@_REALTIME
async def test_reconnect_within_15s_after_clean_disconnect_answers_promptly(
    factory, model: str
) -> None:
    """A clean disconnect, then a new client within 15 s whose first packet
    still carries the previous session's numbering: CI-V answers promptly."""
    sim = _sim(factory)
    await sim.start()
    first = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
        timeout=0.5,
    )
    second = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model=model,
        timeout=0.5,
    )
    try:
        with fast_connect():
            await asyncio.wait_for(first.connect(), 10.0)
            await _wait_connected(first)
            await first.disconnect()
            assert first.conn_state is RadioConnectionState.DISCONNECTED

            # The new client connects at once (well within 15 s); the radio's
            # open marker again carries a jumped-ahead number.
            await asyncio.wait_for(second.connect(), 10.0)
            await _wait_connected(second)
            freq = await asyncio.wait_for(second.get_freq(), 5.0)
        assert freq > 0
        stats = second._civ_transport.rx_order_stats  # noqa: SLF001
        assert stats.reanchored == 1
        assert stats.duplicate <= REORDER_WINDOW
    finally:
        await second.disconnect()
        await sim.stop()
