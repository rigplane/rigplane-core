"""Lost UDP teardown must not keep the next Icom LAN login behind a hold."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from rigplane.core.exceptions import DiscoveryTimeout
from rigplane.radio import IcomRadio
from rigplane.runtime import _control_phase
from rigplane.testing.timing import VirtualClock, await_until
from rigplane_testkit.icom import MockIcomRadio

pytestmark = pytest.mark.timeout(30)


class HeldSessionRadio(MockIcomRadio):
    """Exercise silence during a hold and token-keyed release on a fresh socket."""

    def __init__(self, address: int) -> None:
        super().__init__(
            radio_addr=address,
            single_owner=True,
            keepalive_hold_s=86.0,
            require_control_discovery=True,
        )
        self.discovery_count = 0
        self.removes: list[bytes] = []
        self.drop_removes = 0

    def _handle_ctrl(self, data, addr, ptype, sender_id, seq, proto):
        if len(data) == 0x10 and ptype == 0x03:
            self.discovery_count += 1
            if self.session_held:
                return
        if len(data) == 0x40 and data[0x15] == 0x01:
            self.removes.append(data)
            if self.drop_removes:
                self.drop_removes -= 1
                return
            # Model the requested token-keyed release from a fresh UDP port.
            if (
                self.owner is not None
                and addr[0] == self.owner[0][0]
                and sender_id == self.owner[1]
                and struct.unpack_from("<I", data, 0x1C)[0] == self.token
            ):
                addr = self.owner[0]
        super()._handle_ctrl(data, addr, ptype, sender_id, seq, proto)


@pytest.fixture(params=[("IC-705", 0xA4), ("IC-7610", 0x98)])
async def lan(
    request: pytest.FixtureRequest,
    monkeypatch: pytest.MonkeyPatch,
    virtual_clock: VirtualClock,
) -> AsyncIterator[tuple[IcomRadio, HeldSessionRadio]]:
    model, address = request.param
    sim = HeldSessionRadio(address)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="u",
        password="p",
        model=model,
        timeout=5.0,
    )
    # Advance only the mechanism's packet-spacing sleeps. Socket delivery is
    # awaited as a real event; recurring control tasks are outside this test.
    virtual_clock.scope_to(monkeypatch, _control_phase)
    monkeypatch.setattr(
        radio._control_phase, "_resolve_local_bind_host", lambda: "127.0.0.1"
    )
    monkeypatch.setattr(radio._control_phase, "_start_watchdog", lambda: None)
    monkeypatch.setattr(radio._control_phase, "_start_token_renewal", lambda: None)
    monkeypatch.setattr(_control_phase, "wait_for_radio_startup_ready", AsyncMock())
    monkeypatch.setattr(radio, "_fetch_initial_state", AsyncMock())
    try:
        yield radio, sim
    finally:
        await radio.disconnect()
        await sim.stop()


async def _connect(radio: IcomRadio) -> None:
    task = asyncio.create_task(radio.connect())
    try:
        await await_until(task.done, description="Icom LAN connect completion")
        await task
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("teardown", ["disconnect", "release"])
async def test_reconnect_releases_old_token_before_first_discovery(
    lan: tuple[IcomRadio, HeldSessionRadio],
    virtual_clock: VirtualClock,
    teardown: str,
) -> None:
    radio, sim = lan
    await _connect(radio)
    ctrl = radio._ctrl_transport
    old_session = (radio._token, ctrl.my_id, ctrl.remote_id, radio._tok_request)
    assert sim.session_held
    # Lose teardown datagrams on both sockets, as on an interrupted LAN.
    assert radio._civ_transport is not None
    civ = radio._civ_transport
    ctrl_send, civ_send = ctrl._raw_send, civ._raw_send
    ctrl._raw_send = lambda data: None
    civ._raw_send = lambda data: None
    try:
        if teardown == "release":
            await radio._control_phase.release()
        await radio.disconnect()
    finally:
        ctrl._raw_send, civ._raw_send = ctrl_send, civ_send
    assert sim.session_held
    discoveries = sim.discovery_count
    started = virtual_clock.now
    sim.drop_removes = 1

    await _connect(radio)

    assert sim.discovery_count == discoveries + 1
    assert sim.login_count == 2
    assert sim.last_release_reason == "token_remove"
    assert virtual_clock.now - started < 2.0  # not the 86-second hold expiry
    assert len(sim.removes) == 3
    assert radio._control_phase._previous_session is None
    for packet in sim.removes:
        assert (
            struct.unpack_from("<I", packet, 0x1C)[0],
            struct.unpack_from("<I", packet, 0x08)[0],
            struct.unpack_from("<I", packet, 0x0C)[0],
            struct.unpack_from("<H", packet, 0x1A)[0],
        ) == old_session
    assert not any(
        len(packet) == 0x40 and packet[0x15] == 0x01
        for packet in ctrl.tx_buffer.values()
    )


async def test_no_previous_session_sends_no_stale_token_remove(
    lan: tuple[IcomRadio, HeldSessionRadio],
) -> None:
    radio, sim = lan
    await _connect(radio)
    assert sim.discovery_count == 1
    assert sim.login_count == 1
    assert sim.removes == []


async def test_failed_discovery_does_not_replay_consumed_session_again(
    lan: tuple[IcomRadio, HeldSessionRadio],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio, sim = lan
    await _connect(radio)
    await radio.disconnect()
    await await_until(lambda: bool(sim.removes), description="initial token release")
    removes = len(sim.removes)
    ctrl = radio._ctrl_transport
    discover = ctrl._discover
    monkeypatch.setattr(
        ctrl, "_discover", AsyncMock(side_effect=DiscoveryTimeout("silent discovery"))
    )
    with pytest.raises(DiscoveryTimeout, match="silent discovery"):
        await _connect(radio)
    await await_until(
        lambda: len(sim.removes) == removes + 3, description="bounded release replay"
    )
    assert radio._control_phase._previous_session is None
    assert radio._token == 0

    monkeypatch.setattr(ctrl, "_discover", discover)
    await _connect(radio)
    assert len(sim.removes) == removes + 3


@pytest.mark.parametrize("fail_remove", [False, True])
async def test_data_port_timeout_removes_token_before_control_close(
    lan: tuple[IcomRadio, HeldSessionRadio],
    monkeypatch: pytest.MonkeyPatch,
    fail_remove: bool,
) -> None:
    radio, sim = lan
    await _connect(radio)
    ctrl = radio._ctrl_transport
    events: list[str] = []
    send = ctrl._raw_send
    disconnect = ctrl.disconnect

    def record_send(data: bytes) -> None:
        if len(data) == 0x40 and data[0x15] == 0x01:
            events.append("remove")
            if fail_remove:
                raise OSError("simulated token-remove send failure")
        send(data)

    async def record_close() -> None:
        events.append("close")
        await disconnect()

    monkeypatch.setattr(ctrl, "_raw_send", record_send)
    monkeypatch.setattr(ctrl, "disconnect", record_close)
    await radio._control_phase._cleanup_data_port_discovery_timeout()
    assert events == ["remove", "close"]
