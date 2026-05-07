"""Opt-in hardware validation for IC-7610 LAN TX audio.

These tests transmit RF/audio when explicitly enabled. They are skipped by
default and require a controlled station setup such as a dummy load.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path

import pytest

from rigplane import IcomRadio
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.route import resolve_audio_route, rigctld_wsjtx_policy
from rigplane.audio_bridge import AudioBridge, FRAME_BYTES, SAMPLES_PER_FRAME
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer

from _audio_pipeline_helpers import PcmDiagnostics, sine_pcm16_mono

pytestmark = pytest.mark.hardware


_ENABLE_ENV = "RIGPLANE_HW_IC7610_AUDIO"
_ALLOW_TX_ENV = "RIGPLANE_HW_ALLOW_TX"
_HOST_ENV = "RIGPLANE_HW_ICOM_HOST"
_USER_ENV = "RIGPLANE_HW_ICOM_USER"
_PASS_ENV = "RIGPLANE_HW_ICOM_PASS"
_PASS_FILE_ENV = "RIGPLANE_HW_ICOM_PASS_FILE"
_RADIO_ADDR_ENV = "RIGPLANE_HW_ICOM_RADIO_ADDR"
_FRAME_COUNT_ENV = "RIGPLANE_HW_AUDIO_FRAMES"


@dataclass(frozen=True)
class HardwareRadioConfig:
    host: str
    username: str
    password: str
    radio_addr: int
    frame_count: int


class RigctldClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, line: str, *, read_timeout: float = 3.0) -> str:
        self._writer.write((line + "\n").encode("ascii"))
        await self._writer.drain()
        data = await asyncio.wait_for(self._reader.read(4096), timeout=read_timeout)
        return data.decode("ascii").rstrip("\n")

    async def close(self) -> None:
        self._writer.close()
        await self._writer.wait_closed()


def _flag_enabled(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw, 0)


def _password_from_env() -> str:
    direct = os.environ.get(_PASS_ENV)
    if direct:
        return direct

    pass_file = os.environ.get(_PASS_FILE_ENV)
    if pass_file:
        return Path(pass_file).read_text(encoding="utf-8").strip()

    return ""


def _hardware_config() -> HardwareRadioConfig:
    if not _flag_enabled(_ENABLE_ENV):
        pytest.skip(f"Set {_ENABLE_ENV}=1 to run IC-7610 LAN audio hardware test")
    if not _flag_enabled(_ALLOW_TX_ENV):
        pytest.skip(f"Set {_ALLOW_TX_ENV}=1 only with a safe transmitter/load setup")

    host = os.environ.get(_HOST_ENV, "").strip()
    username = os.environ.get(_USER_ENV, "").strip()
    password = _password_from_env()
    missing = [
        name
        for name, value in (
            (_HOST_ENV, host),
            (_USER_ENV, username),
            (f"{_PASS_ENV} or {_PASS_FILE_ENV}", password),
        )
        if not value
    ]
    if missing:
        pytest.skip("Missing hardware config: " + ", ".join(missing))

    return HardwareRadioConfig(
        host=host,
        username=username,
        password=password,
        radio_addr=_env_int(_RADIO_ADDR_ENV, 0x98),
        frame_count=_env_int(_FRAME_COUNT_ENV, 20),
    )


async def _connect_with_retries(radio: IcomRadio, attempts: int = 5) -> None:
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            await radio.connect()
            return
        except Exception as exc:
            last_exc = exc
            if attempt == attempts:
                break
            await asyncio.sleep(min(2 * attempt, 10))
    assert last_exc is not None
    raise last_exc


def _server_addr(server: RigctldServer) -> tuple[str, int]:
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    return str(host), int(port)


async def _make_client(server: RigctldServer) -> RigctldClient:
    host, port = _server_addr(server)
    reader, writer = await asyncio.open_connection(host, port)
    return RigctldClient(reader, writer)


@pytest.fixture
async def hardware_radio() -> AsyncGenerator[IcomRadio, None]:
    config = _hardware_config()
    radio = IcomRadio(
        config.host,
        username=config.username,
        password=config.password,
        radio_addr=config.radio_addr,
        model="IC-7610",
    )
    await _connect_with_retries(radio)
    try:
        yield radio
    finally:
        try:
            await radio.set_ptt(False)
        except Exception:
            pass
        await radio.disconnect()


async def test_ic7610_lan_rigctld_data2_pcm_tx_pipeline(
    hardware_radio: IcomRadio,
) -> None:
    config = _hardware_config()
    radio = hardware_radio

    route = resolve_audio_route(radio)
    wsjtx_data_mode, wsjtx_data_mod_input = rigctld_wsjtx_policy(route)
    assert (wsjtx_data_mode, wsjtx_data_mod_input) == (2, 5)

    original_mode = await radio.get_mode_info()
    original_data1_mod = await radio.get_data1_mod_input()
    original_data2_mod = await radio.get_data2_mod_input()

    server = RigctldServer(
        radio,
        RigctldConfig(
            host="127.0.0.1",
            port=0,
            max_clients=2,
            client_timeout=10.0,
            command_timeout=5.0,
            wsjtx_compat=False,
            wsjtx_data_mode=wsjtx_data_mode,
            wsjtx_data_mod_input=wsjtx_data_mod_input,
        ),
    )
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="Hardware Synthetic Loopback",
                input_channels=2,
                output_channels=2,
            )
        ]
    )
    bridge = AudioBridge(
        radio,
        device_name="Hardware Synthetic Loopback",
        tx_enabled=True,
        backend=backend,
        label="hardware-ic7610-audio",
    )

    tone_frame = sine_pcm16_mono(1000.0, samples=SAMPLES_PER_FRAME)
    assert len(tone_frame) == FRAME_BYTES
    diagnostics = PcmDiagnostics.from_pcm(tone_frame * config.frame_count)

    client: RigctldClient | None = None
    await server.start()
    try:
        await bridge.start()
        client = await _make_client(server)

        assert await client.send("M PKTUSB") == "RPRT 0"
        assert await client.send("T 1") == "RPRT 0"

        for _ in range(config.frame_count):
            backend.rx_streams[0].inject_frame(tone_frame)
            await asyncio.sleep(0.02)

        deadline = asyncio.get_running_loop().time() + 5.0
        while bridge.metrics.tx_frames < config.frame_count:
            if asyncio.get_running_loop().time() >= deadline:
                break
            await asyncio.sleep(0.05)

        assert await client.send("T 0") == "RPRT 0"

        actual_data1_mod = await radio.get_data1_mod_input()
        actual_data2_mod = await radio.get_data2_mod_input()
        metrics = bridge.metrics

        print(
            json.dumps(
                {
                    "route": route.__dict__,
                    "wsjtx_data_mode": wsjtx_data_mode,
                    "wsjtx_data_mod_input": wsjtx_data_mod_input,
                    "codec": str(getattr(radio, "audio_codec", "unknown")),
                    "host": config.host,
                    "model": getattr(radio, "model", "unknown"),
                    "frame_count": config.frame_count,
                    "pcm_peak": diagnostics.peak,
                    "pcm_rms": round(diagnostics.rms, 2),
                    "bridge_tx_frames": metrics.tx_frames,
                    "bridge_tx_overruns": metrics.tx_overruns,
                    "bridge_tx_level_dbfs": metrics.tx_level_dbfs,
                    "data1_mod_before": original_data1_mod,
                    "data1_mod_after": actual_data1_mod,
                    "data2_mod_before": original_data2_mod,
                    "data2_mod_after": actual_data2_mod,
                },
                sort_keys=True,
            )
        )

        assert actual_data1_mod == original_data1_mod
        assert actual_data2_mod == 5
        assert metrics.tx_frames >= config.frame_count
        assert metrics.tx_overruns == 0
        assert metrics.tx_level_dbfs > -70.0
    finally:
        if client is not None:
            try:
                await client.send("T 0")
            except Exception:
                pass
            await client.close()
        await bridge.stop()
        await server.stop()
        try:
            await radio.set_data2_mod_input(original_data2_mod)
        except Exception:
            pass
        try:
            await radio.set_mode(original_mode[0], filter_width=original_mode[1])
        except Exception:
            pass
