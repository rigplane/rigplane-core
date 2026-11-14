"""Writes confirmed by the radio's echo, end to end (MOR-4436).

Against the LAN simulator in ``write_confirmation="echo"`` mode a SET is
answered by the byte-identical echo instead of FB: a blocking write
succeeds, a fire-and-forget sink drains, and the echo still feeds the
state store. The default FB mode is pinned unchanged.
"""

from __future__ import annotations

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.radio import IcomRadio
from rigplane.testing.timing import await_until
from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect


def _bcd2(value: int) -> bytes:
    """Encode a 0-255 value as 2-byte BCD, the 0x14 level wire format."""
    digits = f"{value:04d}"
    return bytes(
        [(int(digits[0]) << 4) | int(digits[1]), (int(digits[2]) << 4) | int(digits[3])]
    )


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
async def test_blocking_write_succeeds_against_echo_radio() -> None:
    sim = MockIcomRadio(write_confirmation="echo")
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            result = await radio.send_civ_transaction(
                0x14, 0x0A, data=_bcd2(200), expect="ack", timeout=2.0
            )
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()
    assert result.status == "ack"
    # The synthesized FB was never on the wire: its bytes are the
    # canonical rebuild, never another frame's remembered bytes.
    assert result.frame_bytes == build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFB)


@pytest.mark.asyncio
async def test_fire_and_forget_write_sink_retired_by_echo() -> None:
    sim = MockIcomRadio(write_confirmation="echo")
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            await radio.set_rf_power(200)
            await await_until(
                lambda: radio._civ_request_tracker.ack_sink_count == 0,  # noqa: SLF001
                timeout=2.0,
                description="echo retires the fire-and-forget write sink",
            )
            assert await radio.get_rf_power() == 200
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()


@pytest.mark.asyncio
async def test_answer_without_pending_write_updates_state() -> None:
    sim = MockIcomRadio()
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            assert await radio.get_rf_power() == 100
            field = radio._state_store.snapshot().field(  # noqa: SLF001
                "global.operator_controls.power_level"
            )
            assert field.value == pytest.approx(100 / 255)
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()


@pytest.mark.asyncio
async def test_echo_confirming_write_also_updates_state() -> None:
    sim = MockIcomRadio(write_confirmation="echo")
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            result = await radio.send_civ_transaction(
                0x14, 0x0A, data=_bcd2(200), expect="ack", timeout=2.0
            )
            assert result.status == "ack"
            field = radio._state_store.snapshot().field(  # noqa: SLF001
                "global.operator_controls.power_level"
            )
            assert field.value == pytest.approx(200 / 255)
            assert await radio.get_rf_power() == 200
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()


@pytest.mark.asyncio
async def test_blocking_write_still_acks_with_fb() -> None:
    sim = MockIcomRadio()
    await sim.start()
    try:
        radio = await _connect(sim)
        try:
            result = await radio.send_civ_transaction(
                0x14, 0x0A, data=_bcd2(200), expect="ack", timeout=2.0
            )
        finally:
            await radio.disconnect()
    finally:
        await sim.stop()
    assert result.status == "ack"
