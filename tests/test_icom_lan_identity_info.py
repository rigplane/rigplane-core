"""MOR-4382: the Icom LAN identity read is information only.

Real ``IcomRadio`` over real UDP against the Icom LAN simulator. The ``19 00``
answer is published as ``connection.identity``; a missing answer, an
unverified one or a different model never refuses a command and never holds a
state read.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

import pytest

from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.runtime.radio import IcomRadio
from rigplane_testkit.icom import MockIcomRadio

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.realtime(reason="real UDP simulator and its answer window"),
]


class _SilentIdentity(MockIcomRadio):
    """A radio that never answers the ``19 00`` identity read."""

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        if cmd == 0x19 and payload == b"\x00":
            return None
        return super()._dispatch_civ(cmd, payload, from_addr)


@contextlib.asynccontextmanager
async def _connected(
    *,
    model_id: bytes = b"\x98",
    sim_cls: type[MockIcomRadio] = MockIcomRadio,
    enable_audio: bool = False,
) -> AsyncIterator[tuple[IcomRadio, MockIcomRadio]]:
    sim = sim_cls(enable_audio=enable_audio)
    sim.model_id = model_id
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="mor4382",
        password="mor4382",
        timeout=0.5,
        model="IC-7610",
    )
    try:
        await radio.connect()
        yield radio, sim
    finally:
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


async def _wait_status(radio: IcomRadio, wanted: RadioIdentityStatus) -> Any:
    for _ in range(100):
        identity = radio.connection_identity
        if identity is not None and identity.status is wanted:
            return identity
        await asyncio.sleep(0.05)
    raise AssertionError(f"identity never became {wanted}: {radio.connection_identity}")


async def test_matching_model_is_verified_and_commands_work() -> None:
    async with _connected() as (radio, _sim):
        identity = await _wait_status(radio, RadioIdentityStatus.VERIFIED)
        assert identity.answered_model == "IC-7610"
        await radio.set_freq(7_074_000)
        assert await radio.get_freq() == 7_074_000


async def test_a_different_model_is_reported_and_commands_still_work() -> None:
    async with _connected(model_id=b"\x94") as (radio, _sim):
        identity = await _wait_status(radio, RadioIdentityStatus.IDENTITY_MISMATCH)
        assert identity.expected_model == "IC-7610"
        assert identity.answered_id == "94"
        assert radio.connected is True
        await radio.set_freq(14_074_000)
        assert await radio.get_freq() == 14_074_000


async def test_a_silent_identity_read_never_blocks_the_link() -> None:
    async with _connected(sim_cls=_SilentIdentity) as (radio, _sim):
        await _wait_status(radio, RadioIdentityStatus.NO_RESPONSE)
        assert radio.connected is True
        await radio.set_freq(3_573_000)
        assert await radio.get_freq() == 3_573_000


async def test_disconnect_clears_identity_and_a_new_connect_reads_it_again() -> None:
    async with _connected() as (radio, _sim):
        await _wait_status(radio, RadioIdentityStatus.VERIFIED)
        await radio.disconnect()
        assert radio.connection_identity is None
        await radio.connect()
        await _wait_status(radio, RadioIdentityStatus.VERIFIED)
        assert await radio.get_freq() > 0


async def test_audio_start_right_after_connect_works_while_identity_is_unanswered() -> (
    None
):
    """The browser's ``audio_start`` lands right after a reconnect.

    The radio has not answered ``19 00`` yet. The RX start used to be refused
    ("commands are refused until the radio answers its 19 00 model
    identification"), which raised the "Radio audio capture could not start"
    toast; now the start goes through at once.
    """
    async with _connected(sim_cls=_SilentIdentity, enable_audio=True) as (radio, _sim):
        await radio.disconnect()
        await radio.connect()
        sub = await radio.audio_session.subscribe_rx("web-audio")
        try:
            packet = await sub.get(timeout=10.0)
            assert packet is not None
        finally:
            await sub.release()
