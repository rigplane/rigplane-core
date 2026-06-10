"""Tests for the AudioBus RX heartbeat exposed in the runtime payload (MOR-564).

The bus stamps a monotonic timestamp on every RX frame fan-out and the web
runtime payload surfaces it (plus the existing per-subscriber stats) so RX
liveness becomes observable. Purely additive observability — no watchdog.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio_bus import AudioBus
from rigplane.web.server import WebConfig, WebServer


@pytest.fixture
def mock_radio():
    radio = SimpleNamespace()
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    return radio


@pytest.fixture
def bus(mock_radio):
    return AudioBus(mock_radio)


class _FakeWriter:
    """Minimal writer capturing the HTTP response bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _response_json(writer: _FakeWriter) -> tuple[int, dict]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


# ---------------------------------------------------------------------------
# Heartbeat stamp on the bus
# ---------------------------------------------------------------------------


def test_heartbeat_is_none_before_any_packet(bus):
    assert bus.last_rx_frame_monotonic is None
    assert bus.stats["last_rx_frame_monotonic"] is None


async def test_heartbeat_stamps_and_advances_on_each_packet(bus, mock_radio):
    sub = bus.subscribe(name="s1")
    await sub.start()

    before = time.monotonic()
    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=1, data=b"a"))
    first = bus.last_rx_frame_monotonic
    after = time.monotonic()
    assert first is not None
    assert before <= first <= after

    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=2, data=b"b"))
    second = bus.last_rx_frame_monotonic
    assert second is not None
    assert second >= first
    assert bus.stats["last_rx_frame_monotonic"] == second

    await sub.aclose()


# ---------------------------------------------------------------------------
# Runtime payload exposure
# ---------------------------------------------------------------------------


async def test_runtime_payload_exposes_audio_bus_heartbeat(bus, mock_radio):
    sub = bus.subscribe(name="probe")
    await sub.start()
    bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=1, data=b"x"))

    radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        _audio_bus=bus,
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()

    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001

    status, data = _response_json(writer)
    assert status == 200
    audio_bus = data["audioBus"]
    assert audio_bus["enabled"] is True
    assert isinstance(audio_bus["lastRxFrameMonotonic"], float)
    stats = audio_bus["stats"]
    assert stats["rx_active"] is True
    assert stats["subscriber_count"] == 1
    (sub_stats,) = stats["subscribers"]
    assert sub_stats["name"] == "probe"
    assert sub_stats["received"] == 1
    assert sub_stats["dropped"] == 0

    await sub.aclose()


async def test_runtime_payload_audio_bus_disabled_without_bus():
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()

    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001

    status, data = _response_json(writer)
    assert status == 200
    assert data["audioBus"] == {"enabled": False}
