"""A new Core process can release the preceding process's held LAN session."""

from __future__ import annotations

import asyncio
import json
import os
import stat
import struct
import sys
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime import _control_phase
from rigplane.testing.timing import VirtualClock, await_until
from testkit.test_lan_session_release import HeldSessionRadio

pytestmark = pytest.mark.timeout(40)

_CHILD = """
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
from rigplane.radio import IcomRadio
from rigplane.runtime import _control_phase
from rigplane.testing.timing import VirtualClock

async def main():
    radio = IcomRadio('127.0.0.1', port=int(sys.argv[1]), username='u',
                      password='p', model='IC-705', timeout=5.0)
    phase = radio._control_phase
    state_dir = Path(sys.argv[2])
    original_path = phase._session_record_path
    phase._session_record_path = lambda: state_dir / original_path().name
    with pytest.MonkeyPatch.context() as patch:
        VirtualClock().scope_to(patch, _control_phase)
        patch.setattr(phase, '_resolve_local_bind_host', lambda: '127.0.0.1')
        patch.setattr(phase, '_start_watchdog', lambda: None)
        patch.setattr(phase, '_start_token_renewal', lambda: None)
        patch.setattr(_control_phase, 'wait_for_radio_startup_ready', AsyncMock())
        patch.setattr(radio, '_fetch_initial_state', AsyncMock())
        await radio.connect()
        print('READY', flush=True)
        await asyncio.Event().wait()

asyncio.run(main())
"""


@pytest.mark.asyncio
async def test_new_process_releases_killed_process_session_on_first_discovery(
    tmp_path: Path,
) -> None:
    sim = HeldSessionRadio(0xA4)
    await sim.start()
    children: list[asyncio.subprocess.Process] = []
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(root / "src"), str(root), env.get("PYTHONPATH", "")]
    )

    async def start_child() -> asyncio.subprocess.Process:
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            _CHILD,
            str(sim.control_port),
            str(tmp_path),
            cwd=root,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        children.append(child)
        assert child.stdout is not None
        ready = asyncio.create_task(child.stdout.readline())
        try:
            await await_until(ready.done, description="Core child ready or exited")
            assert ready.result() == b"READY\n"
        finally:
            ready.cancel()
            await asyncio.gather(ready, return_exceptions=True)
        return child

    try:
        first = await start_child()
        assert sim.session_held
        old_owner = sim.owner
        records = list(tmp_path.glob("icom-session-*.json"))
        assert len(records) == 1
        assert stat.S_IMODE(records[0].stat().st_mode) == 0o600
        old_record = json.loads(records[0].read_text())
        first.kill()
        await first.wait()
        assert sim.session_held
        discoveries = sim.discovery_count

        await start_child()

        assert sim.discovery_count == discoveries + 1
        assert sim.login_count == 2
        assert sim.owner != old_owner  # a fresh socket and session identity
        assert sim.last_release_reason == "token_remove"
        assert len(sim.removes) == 3
        for packet in sim.removes:
            assert struct.unpack_from("<I", packet, 0x1C)[0] == old_record["token"]
            assert struct.unpack_from("<II", packet, 0x08) == (
                old_record["my_id"],
                old_record["remote_id"],
            )
            assert (
                struct.unpack_from("<H", packet, 0x1A)[0] == old_record["tok_request"]
            )
    finally:
        for child in children:
            if child.returncode is None:
                child.kill()
            await child.wait()
        await sim.stop()


@pytest.mark.parametrize("bad", ["corrupt", "version", "old", "peer", "nan", "range"])
@pytest.mark.asyncio
async def test_invalid_record_is_discarded_before_discovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    bad: str,
) -> None:
    sim = HeldSessionRadio(0xA4)
    await sim.start()
    radio = IcomRadio("127.0.0.1", port=sim.control_port, model="IC-705")
    phase = radio._control_phase
    clock = VirtualClock()
    clock.scope_to(monkeypatch, _control_phase)
    path = phase._session_record_path()
    record = dict(
        version=1,
        peer=["127.0.0.1", sim.control_port],
        token=123,
        my_id=456,
        remote_id=789,
        tok_request=12,
        written_at=1000.0,
    )
    monkeypatch.setattr(_control_phase.time, "time", lambda: 1000.0)
    if bad == "version":
        record["version"] = 2
    elif bad == "old":
        record["written_at"] = 519.0
    elif bad == "peer":
        record["peer"] = ["192.0.2.1", sim.control_port]
    elif bad == "nan":
        record["written_at"] = float("nan")
    elif bad == "range":
        record["tok_request"] = 65536
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{" if bad == "corrupt" else json.dumps(record))
    discover = radio._ctrl_transport._discover

    async def discover_after_discard() -> None:
        assert not path.exists()
        await discover()

    monkeypatch.setattr(radio._ctrl_transport, "_discover", discover_after_discard)
    monkeypatch.setattr(phase, "_resolve_local_bind_host", lambda: "127.0.0.1")
    monkeypatch.setattr(phase, "_start_watchdog", lambda: None)
    monkeypatch.setattr(phase, "_start_token_renewal", lambda: None)
    monkeypatch.setattr(_control_phase, "wait_for_radio_startup_ready", AsyncMock())
    monkeypatch.setattr(radio, "_fetch_initial_state", AsyncMock())
    try:
        await radio.connect()
        assert sim.discovery_count == 1
        assert sim.removes == []
    finally:
        await radio.disconnect()
        await sim.stop()


def test_confirmed_release_clears_record_but_unconfirmed_release_keeps_it(
    tmp_path: Path,
) -> None:
    radio = IcomRadio("192.0.2.1", model="IC-705")
    phase, ctrl = radio._control_phase, radio._ctrl_transport
    ctrl._udp_peer_addr = ("192.0.2.1", 50001)
    ctrl.my_id, ctrl.remote_id = 123, 456
    radio._token, radio._tok_request = 789, 12
    phase._remember_session_for_release()
    phase._write_session_record()
    path = phase._session_record_path()
    assert path.exists()
    reply = bytearray(0x40)
    reply[0x14:0x16] = b"\x02\x01"
    struct.pack_into("<I", reply, 0x1C, radio._token + 1)
    phase._on_token_reply(bytes(reply))
    assert path.exists()
    struct.pack_into("<I", reply, 0x1C, radio._token)
    struct.pack_into("<I", reply, 0x30, 0xFFFFFFFF)
    phase._on_token_reply(bytes(reply))
    assert path.exists()
    struct.pack_into("<I", reply, 0x30, 0)
    phase._on_token_reply(bytes(reply))
    assert not path.exists()
    assert phase._previous_session is None


@pytest.mark.asyncio
async def test_failed_persisted_release_is_bounded_and_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = IcomRadio("radio.example", model="IC-705")
    phase, ctrl = radio._control_phase, radio._ctrl_transport
    ctrl._udp_peer_addr = ("192.0.2.1", 50001)
    ctrl.my_id, ctrl.remote_id = 123, 456
    radio._token, radio._tok_request = 789, 12
    phase._write_session_record()
    assert phase._previous_session is None  # disk is the only release source
    clock = VirtualClock()
    clock.scope_to(monkeypatch, _control_phase)
    send = Mock(side_effect=OSError("simulated lost socket"))
    monkeypatch.setattr(ctrl, "_raw_send", send)
    await phase._release_previous_session()
    assert send.call_count == 3
    assert clock.sleeps == [0.2, 0.2]
    assert not phase._session_record_path().exists()
    assert phase._previous_session is None


@pytest.mark.asyncio
async def test_token_ack_log_does_not_include_token(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    radio = IcomRadio("192.0.2.1", model="IC-705")
    radio._token = 0x1234ABCD
    monkeypatch.setattr(radio._ctrl_transport, "send_tracked", AsyncMock())
    with caplog.at_level("DEBUG", logger=_control_phase.__name__):
        await radio._control_phase._send_token_ack()
    assert "Token ack sent" in caplog.text
    assert "1234abcd" not in caplog.text.lower()
    assert str(radio._token) not in caplog.text
