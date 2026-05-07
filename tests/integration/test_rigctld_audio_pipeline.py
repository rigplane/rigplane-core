"""Integration tests for rigctld WSJT-X command replay into LAN TX audio."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.lan_stream import AudioStream, TX_IDENT
from rigplane.audio.route import resolve_audio_route, rigctld_wsjtx_policy
from rigplane.audio_bridge import AudioBridge, FRAME_BYTES, SAMPLES_PER_FRAME
from rigplane.radio import IcomRadio
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.types import AudioCodec

from _audio_pipeline_helpers import (
    assert_contiguous_sequences,
    collect_tx_audio_packets,
    pcm_rms,
    sine_pcm16_mono,
)

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]


class _RecordingAudioTransport:
    my_id = 0xAABBCCDD
    remote_id = 0x11223344

    def __init__(self) -> None:
        self.send_tracked = AsyncMock()

    async def receive_packet(self, *, timeout: float = 1.0) -> bytes:
        _ = timeout
        await asyncio.sleep(0)
        raise TimeoutError


class _IdleCivTransport:
    async def receive_packet(self, *, timeout: float = 0.2) -> bytes:
        await asyncio.sleep(timeout)
        raise asyncio.TimeoutError


class RecordingLanRadio(IcomRadio):
    """Fake-connected LAN radio that records CAT side effects."""

    def __init__(self) -> None:
        super().__init__(
            "192.0.2.10",
            username="u",
            password="p",
            timeout=0.05,
            model="IC-7610",
        )
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self._mode: str = "USB"
        self._filter_width: int | None = None
        self._data_mode: int | bool = False
        self._ptt = False

    def fake_connect_audio(self, transport: _RecordingAudioTransport) -> None:
        self._connected = True
        self._civ_transport = _IdleCivTransport()
        self._conn_state = RadioConnectionState.CONNECTED
        self._civ_stream_ready = True
        self._audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self._audio_stream = AudioStream(transport)  # type: ignore[arg-type]

    @property
    def control_connected(self) -> bool:
        return self._connected

    @property
    def radio_ready(self) -> bool:
        return self._connected

    async def set_mode(
        self,
        mode: object,
        filter_width: int | None = None,
        receiver: int = 0,
    ) -> None:
        mode_name = str(mode)
        self.calls.append(("set_mode", (mode_name, filter_width, receiver)))
        self._mode = mode_name
        self._filter_width = filter_width

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        _ = receiver
        return self._mode, self._filter_width

    async def set_data_mode(self, on: int | bool, receiver: int = 0) -> None:
        self.calls.append(("set_data_mode", (on, receiver)))
        self._data_mode = on

    async def get_data_mode(self) -> bool:
        return bool(self._data_mode)

    async def set_data1_mod_input(self, source: int) -> None:
        self.calls.append(("set_data1_mod_input", (source,)))

    async def set_data2_mod_input(self, source: int) -> None:
        self.calls.append(("set_data2_mod_input", (source,)))

    async def set_ptt(self, on: bool) -> None:
        self.calls.append(("set_ptt", (bool(on),)))
        self._ptt = bool(on)
        self._state_cache.update_ptt(bool(on))


class RigctldClient:
    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer

    async def send(self, line: str, *, read_timeout: float = 1.0) -> str:
        self._writer.write((line + "\n").encode("ascii"))
        await self._writer.drain()
        data = await asyncio.wait_for(self._reader.read(4096), timeout=read_timeout)
        return data.decode("ascii").rstrip("\n")

    async def close(self) -> None:
        self._writer.close()
        await self._writer.wait_closed()


def _server_addr(server: RigctldServer) -> tuple[str, int]:
    assert server._server is not None
    host, port = server._server.sockets[0].getsockname()[:2]
    return str(host), int(port)


async def _make_client(server: RigctldServer) -> RigctldClient:
    host, port = _server_addr(server)
    reader, writer = await asyncio.open_connection(host, port)
    return RigctldClient(reader, writer)


@pytest.fixture
async def rigctld_audio_setup() -> AsyncGenerator[
    tuple[
        RecordingLanRadio,
        RigctldServer,
        AudioBridge,
        FakeAudioBackend,
        _RecordingAudioTransport,
    ],
    None,
]:
    radio = RecordingLanRadio()
    transport = _RecordingAudioTransport()
    radio.fake_connect_audio(transport)

    route = resolve_audio_route(radio)
    wsjtx_data_mode, wsjtx_data_mod_input = rigctld_wsjtx_policy(route)
    config = RigctldConfig(
        host="127.0.0.1",
        port=0,
        max_clients=2,
        client_timeout=5.0,
        command_timeout=2.0,
        wsjtx_compat=False,
        wsjtx_data_mode=wsjtx_data_mode,
        wsjtx_data_mod_input=wsjtx_data_mod_input,
    )
    server = RigctldServer(radio, config)

    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="BlackHole Test",
                input_channels=2,
                output_channels=2,
            )
        ]
    )
    bridge = AudioBridge(
        radio,
        device_name="BlackHole Test",
        tx_enabled=True,
        backend=backend,
    )

    await server.start()
    await bridge.start()
    try:
        yield radio, server, bridge, backend, transport
    finally:
        await bridge.stop()
        await server.stop()
        radio._connected = False


async def test_rigctld_wsjtx_replay_drives_data2_lan_tx_audio_pipeline(
    rigctld_audio_setup: tuple[
        RecordingLanRadio,
        RigctldServer,
        AudioBridge,
        FakeAudioBackend,
        _RecordingAudioTransport,
    ],
) -> None:
    radio, server, _bridge, backend, transport = rigctld_audio_setup

    route = resolve_audio_route(radio)
    assert rigctld_wsjtx_policy(route) == (2, 5)

    client = await _make_client(server)
    try:
        assert await client.send("M PKTUSB") == "RPRT 0"
        assert await client.send("T 1") == "RPRT 0"

        frame_count = 4
        tone_frame = sine_pcm16_mono(1000.0, samples=SAMPLES_PER_FRAME)
        assert len(tone_frame) == FRAME_BYTES
        for _ in range(frame_count):
            backend.rx_streams[0].inject_frame(tone_frame)
            await asyncio.sleep(0.01)

        assert await client.send("T 0") == "RPRT 0"
    finally:
        await client.close()

    assert ("set_data2_mod_input", (5,)) in radio.calls
    assert ("set_data_mode", (2, 0)) in radio.calls
    assert not any(call[0] == "set_data1_mod_input" for call in radio.calls)
    assert [args[0] for name, args in radio.calls if name == "set_ptt"] == [
        True,
        False,
    ]

    packets = collect_tx_audio_packets(transport.send_tracked.await_args_list)
    payloads = [packet.data for packet in packets if pcm_rms(packet.data) > 0.0]

    assert len(payloads) >= frame_count
    assert {packet.ident for packet in packets} == {TX_IDENT}
    assert_contiguous_sequences(packets)
