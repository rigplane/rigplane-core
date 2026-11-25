"""Rigctld mode advertisement and round trips through simulated radio wires."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

import pytest

from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.radio_protocol import Radio
from rigplane.profiles.rig_loader import RigConfig, discover_rigs
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.radio import IcomRadio
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.serial.endpoints import TcpEndpoint
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake
from rigplane_testkit.sim.serve import build_lan_personality
from test_rigctld_wsjtx_split_mk2 import _client, _exchange
from testkit.test_g7250_session_wire import _G7250BenchFake

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
_SIM_PROFILES = ROOT / "rigplane_testkit/sim/profiles"
_SKIP_REASONS = {
    "TX-500": "There is no native TX-500 CAT backend or simulated CAT peer",
    "X6100": "There is no native X6100 session backend for the CI-V simulator",
}


def _profile_cases():
    for model, rig in discover_rigs(ROOT / "rigs").items():
        reason = _SKIP_REASONS.get(model)
        yield (
            pytest.param(rig, id=model, marks=pytest.mark.skip(reason=reason))
            if reason
            else pytest.param(rig, id=model)
        )


@asynccontextmanager
async def _simulated_radio(rig: RigConfig) -> AsyncIterator[Radio]:
    async with AsyncExitStack() as stack:
        if rig.model in {"G7250", "IC-7300", "X6200"}:
            if rig.model == "IC-7300":
                fake = CivSerialFake.from_profile(ROOT)
            elif rig.model == "G7250":
                fake = _G7250BenchFake()
            else:
                # Unverified token used by the existing X6200 serial simulator test.
                fake = CivSerialFake(model=rig.model, model_id=b"\x00\x01")
            endpoint = TcpEndpoint(fake)
            stack.push_async_callback(endpoint.stop)
            await endpoint.start()
            radio = create_radio(
                SerialBackendConfig(device=endpoint.device_url, model=rig.model)
            )
        elif rig.has_lan and rig.protocol_type == "civ":
            profile = rig.to_profile()
            sim = build_lan_personality(profile, enable_audio=True)
            stack.push_async_callback(sim.stop)
            await sim.start()
            radio = IcomRadio(
                "127.0.0.1",
                port=sim.control_port,
                username="testuser",
                password="testpass",
                profile=profile,
            )
        else:
            model = rig.model.lower().replace("-", "")
            rig_path = ROOT / "rigs" / f"{model}.toml"
            cat_fake = YaesuCatSerialFake.from_profile(
                ROOT,
                _SIM_PROFILES / f"{model}.toml",
                rig_path=rig_path,
            )
            endpoint = TcpEndpoint(cat_fake)
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
@pytest.mark.parametrize("rig", _profile_cases())
async def test_advertised_modes_round_trip(rig: RigConfig) -> None:
    model = rig.model
    expected = {
        "IC-705": EXPECTED_MODES | {"WFM"},
        "G7250": {"USB", "LSB", "CW", "AM", "FM"},
        "X6200": {"USB", "LSB", "CW", "CWR", "AM", "FM", "RTTY"},
    }.get(model, EXPECTED_MODES)
    async with _simulated_radio(rig) as radio:
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
                assert set(modes) == expected
                # The mask order ends with PKTUSB on packet-capable profiles.
                for mode in modes + (["RTTY"] if "PKTUSB" in expected else []):
                    reply = await _exchange(writer, reader, f"M {mode} 0\n".encode())
                    assert reply == b"RPRT 0\n", (model, mode, reply)
                    reply = await _exchange(writer, reader, b"m\n", lines=2)
                    assert reply.splitlines()[0] == mode.encode(), (model, mode, reply)
                for mode in {"WFM", "PKTRTTY"} - expected:
                    reply = await _exchange(writer, reader, f"M {mode} 0\n".encode())
                    assert reply == b"RPRT -1\n", (model, mode, reply)
        finally:
            await server.stop()
