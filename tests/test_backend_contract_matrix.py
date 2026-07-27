"""Backend-agnostic contract tests for LAN and serial-ready mock backends."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Protocol

import pytest

from rigplane import IC_7610_ADDR
from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.commands import (
    CONTROLLER_ADDR,
    _CMD_LEVEL,
    _SUB_RF_POWER,
    build_civ_frame,
)
from rigplane.radio import IcomRadio
from rigplane.types import Mode

from _helpers import freq_response as _freq_response
from _helpers import mode_response as _mode_response
from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp


def _power_response(level: int) -> bytes:
    d = f"{level:04d}"
    b0 = (int(d[0]) << 4) | int(d[1])
    b1 = (int(d[2]) << 4) | int(d[3])
    civ = build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        _CMD_LEVEL,
        sub=_SUB_RF_POWER,
        data=bytes([b0, b1]),
    )
    return _wrap_civ_in_udp(civ)


class _MockLanTransport:
    def __init__(self) -> None:
        self.connected = False
        self.disconnected = False
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}
        self.sent_packets: list[bytes] = []
        self.send_seq = 0
        self.ping_seq = 0
        self.my_id = 0x00010001
        self.remote_id = 0xDEADBEEF
        self.rx_packet_count = 0

    async def connect(self, host: str, port: int) -> None:
        self.connected = True

    async def disconnect(self) -> None:
        self.disconnected = True
        self.connected = False

    def start_ping_loop(self) -> None:
        return None

    def start_retransmit_loop(self) -> None:
        return None

    async def send_tracked(self, data: bytes) -> None:
        self.sent_packets.append(data)
        self.send_seq += 1
        for pkt in self._responses_by_send.pop(self.send_seq, []):
            self._responses.put_nowait(pkt)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response(self, packet: bytes) -> None:
        self._responses.put_nowait(packet)

    def queue_response_on_send(self, send_number: int, packet: bytes) -> None:
        self._responses_by_send.setdefault(send_number, []).append(packet)

    @property
    def _raw_send(self):  # pragma: no cover - compatibility hook
        return lambda data: self.sent_packets.append(data)

    @_raw_send.setter
    def _raw_send(self, value):  # pragma: no cover - compatibility hook
        return None


class _ContractRadio(Protocol):
    async def get_freq(self, receiver: int = 0) -> int: ...
    async def set_freq(self, freq: int, receiver: int = 0) -> None: ...
    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]: ...
    async def set_mode(
        self, mode: str, filter_width: int | None = None, receiver: int = 0
    ) -> None: ...
    async def get_rf_power(self) -> int: ...
    async def set_rf_power(self, level: int) -> None: ...
    async def set_ptt(self, on: bool) -> None: ...
    async def disconnect(self) -> None: ...
    @property
    def model(self) -> str: ...
    @property
    def capabilities(self) -> set[str]: ...
    @property
    def connected(self) -> bool: ...
    @property
    def radio_ready(self) -> bool: ...


@dataclass(slots=True)
class _BackendFixture:
    name: str
    radio: _ContractRadio
    arrange_freq_response: callable
    arrange_mode_response: callable
    arrange_power_response: callable
    assert_ptt: callable


@pytest.fixture(params=["lan", "serial"])
def backend_fixture(request: pytest.FixtureRequest) -> _BackendFixture:
    name = str(request.param)
    if name == "lan":
        transport = _MockLanTransport()
        radio = IcomRadio("192.168.1.100")
        radio._civ_transport = transport
        radio._ctrl_transport = transport
        radio._connected = True
        radio._civ_stream_ready = True
        radio._last_civ_data_received = 1e9

        def _arrange_freq(freq: int) -> None:
            transport.queue_response_on_send(
                transport.send_seq + 1, _freq_response(freq)
            )

        def _arrange_mode(mode: Mode, filt: int) -> None:
            transport.queue_response_on_send(
                transport.send_seq + 1, _mode_response(mode, filt)
            )

        def _arrange_power(level: int) -> None:
            transport.queue_response_on_send(
                transport.send_seq + 1, _power_response(level)
            )

        def _assert_ptt(expected: bool) -> None:
            assert radio.state_cache.ptt is expected

        return _BackendFixture(
            name="lan",
            radio=radio,
            arrange_freq_response=_arrange_freq,
            arrange_mode_response=_arrange_mode,
            arrange_power_response=_arrange_power,
            assert_ptt=_assert_ptt,
        )

    serial_radio = SerialMockRadio()

    def _noop_arrange_freq(freq: int) -> None:
        return None

    def _noop_arrange_mode(mode: Mode, filt: int) -> None:
        return None

    def _noop_arrange_power(level: int) -> None:
        return None

    def _assert_serial_ptt(expected: bool) -> None:
        assert serial_radio.state_cache.ptt is expected

    return _BackendFixture(
        name="serial",
        radio=serial_radio,
        arrange_freq_response=_noop_arrange_freq,
        arrange_mode_response=_noop_arrange_mode,
        arrange_power_response=_noop_arrange_power,
        assert_ptt=_assert_serial_ptt,
    )


@pytest.mark.asyncio
async def test_backend_contract_frequency(backend_fixture: _BackendFixture) -> None:
    await backend_fixture.radio.set_freq(7_074_000)
    backend_fixture.arrange_freq_response(7_074_000)
    assert await backend_fixture.radio.get_freq() == 7_074_000


@pytest.mark.asyncio
async def test_backend_contract_mode(backend_fixture: _BackendFixture) -> None:
    await backend_fixture.radio.set_mode("LSB", filter_width=2)
    backend_fixture.arrange_mode_response(Mode.LSB, 2)
    mode_name, filt = await backend_fixture.radio.get_mode()
    assert mode_name == "LSB"
    assert filt == 2


@pytest.mark.asyncio
async def test_backend_contract_power(backend_fixture: _BackendFixture) -> None:
    await backend_fixture.radio.set_rf_power(150)
    backend_fixture.arrange_power_response(150)
    assert await backend_fixture.radio.get_rf_power() == 150


@pytest.mark.asyncio
async def test_backend_contract_ptt_cache_behavior(
    backend_fixture: _BackendFixture,
) -> None:
    await backend_fixture.radio.set_ptt(True)
    backend_fixture.assert_ptt(backend_fixture.name == "serial")
    await backend_fixture.radio.set_ptt(False)
    backend_fixture.assert_ptt(False)


@pytest.mark.asyncio
async def test_backend_contract_metadata_and_disconnect(
    backend_fixture: _BackendFixture,
) -> None:
    assert backend_fixture.radio.model == "IC-7610"
    assert "tx" in backend_fixture.radio.capabilities
    await backend_fixture.radio.disconnect()
    assert backend_fixture.radio.connected is False


@pytest.mark.asyncio
async def test_backend_contract_radio_ready_tracks_connection(
    backend_fixture: _BackendFixture,
) -> None:
    expected_initial = backend_fixture.name == "lan"
    assert backend_fixture.radio.radio_ready is expected_initial
    if backend_fixture.name == "serial":
        await backend_fixture.radio.connect()
        assert backend_fixture.radio.connected is True
        assert backend_fixture.radio.radio_ready is True
