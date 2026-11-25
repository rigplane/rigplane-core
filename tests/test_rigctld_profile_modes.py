"""Rigctld mode advertisement and round trips through simulated radio wires."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.radio_protocol import Radio
from rigplane.profiles.rig_loader import load_rig
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.radio import IcomRadio
from rigplane_testkit.serial.endpoints import TcpEndpoint
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake
from rigplane_testkit.sim.serve import build_lan_personality
from test_rigctld_wsjtx_split_mk2 import _client, _exchange

ROOT = Path(__file__).resolve().parents[1]
# Hamlib rig.h mode bits, including the unused AMS slot before PKTLSB.
MODE_BITS = (
    "AM",
    "CW",
    "USB",
    "LSB",
    "RTTY",
    "FM",
    "WFM",
    "CWR",
    "RTTYR",
    "AMS",
    "PKTLSB",
    "PKTUSB",
)
EXPECTED_MODES = {
    "AM",
    "CW",
    "USB",
    "LSB",
    "RTTY",
    "FM",
    "CWR",
    "RTTYR",
    "PKTLSB",
    "PKTUSB",
}


@asynccontextmanager
async def _simulated_radio(model: str) -> AsyncIterator[Radio]:
    rig_path = ROOT / "rigs" / f"{model}.toml"
    rig = load_rig(rig_path)
    async with AsyncExitStack() as stack:
        if model == "ic705":
            profile = rig.to_profile()
            sim = build_lan_personality(profile, enable_audio=True)
            stack.push_async_callback(sim.stop)
            await sim.start()
            radio: Radio = IcomRadio(
                "127.0.0.1",
                port=sim.control_port,
                username="testuser",
                password="testpass",
                profile=profile,
            )
        else:
            fake = YaesuCatSerialFake.from_profile(
                ROOT,
                ROOT / "rigplane_testkit/sim/profiles" / f"{model}.toml",
                rig_path=rig_path,
            )
            endpoint = TcpEndpoint(fake)
            stack.push_async_callback(endpoint.stop)
            await endpoint.start()
            radio = YaesuCatRadio(endpoint.device_url, profile=rig)
        stack.push_async_callback(radio.disconnect)
        await radio.connect()
        assert radio.connected
        yield radio


@pytest.mark.asyncio
@pytest.mark.realtime(
    reason="Uses loopback sockets for rigctld and the simulated CAT/CI-V wires"
)
@pytest.mark.parametrize(
    ("model", "extra_modes"),
    [("ftx1", set()), ("ft991a", set()), ("ic705", {"WFM"})],
)
async def test_advertised_modes_round_trip(model: str, extra_modes: set[str]) -> None:
    async with _simulated_radio(model) as radio:
        config = RigctldConfig(host="127.0.0.1", port=0, cache_ttl=0, command_timeout=2)
        server = RigctldServer(radio, config, allow_not_ready=True)
        await server.start()
        try:
            async with _client(server) as (reader, writer):
                dump = await _exchange(writer, reader, b"\\dump_state\n", lines=27)
                rows = dump.splitlines()
                assert rows[-1] == b"done"
                mask = int(rows[3].split()[2], 16)
                assert int(rows[5].split()[2], 16) == mask
                assert all(int(rows[i].split()[0], 16) == mask for i in (7, 9, 10, 11))
                modes = [
                    name for bit, name in enumerate(MODE_BITS) if mask & (1 << bit)
                ]
                assert mask >> len(MODE_BITS) == 0
                assert set(modes) == EXPECTED_MODES | extra_modes
                # Also exercise the transition from PKTUSB to ordinary RTTY.
                for mode in [*modes, "RTTY"]:
                    reply = await _exchange(writer, reader, f"M {mode} 0\n".encode())
                    assert reply == b"RPRT 0\n", (model, mode, reply)
                    reply = await _exchange(writer, reader, b"m\n", lines=2)
                    assert reply.splitlines()[0] == mode.encode(), (model, mode, reply)
                for mode in {"WFM", "PKTRTTY"} - extra_modes:
                    reply = await _exchange(writer, reader, f"M {mode} 0\n".encode())
                    assert reply == b"RPRT -1\n", (model, mode, reply)
        finally:
            await server.stop()
