"""MOR-3845: the universal radio liveness stamp and ``connection.liveness``.

``Radio.last_valid_frame_monotonic`` is stamped by every backend at its
decode point — only after a frame decodes AND carries radio-originated
information. Raw bytes, malformed traffic, receiver start-up, a reconnect
without an answer and daemon-only acks never advance it; every connection
generation resets it. ``/api/v1/info`` and the WebSocket state expose it as
``connection.liveness`` with the age computed at response time.
"""

from __future__ import annotations

import asyncio
import json
import struct
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from _perf_helpers import fast_connect
from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from mock_server import MockIcomRadio
from rigplane import IC_7610_ADDR
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.backends.rigctld_client.observations import (
    build_external_rigctld_acquisition_profile,
    resolve_external_rigctld_poll_intervals,
)
from rigplane.backends.yaesu_cat import (
    CatTimeoutError,
    YaesuCatRadio,
    YaesuCatTransport,
)
from rigplane.backends.yaesu_cat.transport import CatCommandRejected
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.state_store import StateStore
from rigplane.radio import IcomRadio
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.types import bcd_encode
from rigplane.web.runtime_helpers import (
    build_public_state_payload_from_snapshot,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.state_schema import ServerStatePublic
from test_radio import MockTransport
from test_yaesu_cat_transport import FakeStreamReader, FakeStreamWriter


def _liveness(radio: Any, *, now: float | None = None) -> dict[str, Any] | None:
    # Imported lazily so the pre-change run fails per test (the builder does
    # not exist yet) instead of erroring at collection for the whole module.
    from rigplane.web.runtime_helpers import connection_liveness_payload

    return connection_liveness_payload(radio, now=now if now is not None else None)


# ---------------------------------------------------------------------------
# Payload builder — shape and response-time age
# ---------------------------------------------------------------------------


class _StampRadio:
    """Minimal radio carrying only the liveness protocol members."""

    liveness_source = "radio"
    liveness_stale_after_ms = 2_000

    def __init__(
        self,
        stamp: float | None = None,
        *,
        source: str = "radio",
        stale_after_ms: int = 2_000,
    ) -> None:
        self.last_valid_frame_monotonic = stamp
        self.liveness_source = source
        self.liveness_stale_after_ms = stale_after_ms


def test_liveness_payload_is_none_without_a_radio() -> None:
    assert _liveness(None) is None


def test_liveness_payload_reports_null_age_when_never_answered() -> None:
    radio = _StampRadio(stamp=None)
    assert _liveness(radio) == {
        "lastFrameAgeMs": None,
        "staleAfterMs": 2_000,
        "source": "radio",
    }


def test_liveness_payload_computes_the_age_at_response_time() -> None:
    radio = _StampRadio(stamp=100.0)
    payload = _liveness(radio, now=100.75)
    assert payload is not None
    assert payload["lastFrameAgeMs"] == 750
    assert payload["staleAfterMs"] == 2_000
    assert payload["source"] == "radio"


def test_liveness_payload_accepts_the_simulator_source() -> None:
    radio = _StampRadio(stamp=5.0, source="simulator")
    payload = _liveness(radio, now=5.25)
    assert payload is not None
    assert payload["source"] == "simulator"
    assert payload["lastFrameAgeMs"] == 250


def test_liveness_payload_covers_a_quiet_but_healthy_window() -> None:
    """A stamp aged by most of the ready/idle window (quiet RX, sustained TX
    — the acquisition cadence keeps frames flowing) stays below the
    backend-declared threshold."""
    radio = _StampRadio(stamp=0.0, stale_after_ms=2_000)
    payload = _liveness(radio, now=1.5)
    assert payload is not None
    assert payload["lastFrameAgeMs"] == 1_500
    assert payload["lastFrameAgeMs"] < payload["staleAfterMs"]


# ---------------------------------------------------------------------------
# Icom CI-V — the shared decode point (LAN + every serial Icom backend)
# ---------------------------------------------------------------------------


@pytest.fixture
def civ_radio() -> IcomRadio:
    radio = IcomRadio("192.168.1.100", model="IC-7610")  # noqa: S106
    transport = MockTransport()
    radio._civ_transport = transport  # noqa: SLF001
    radio._ctrl_transport = transport  # noqa: SLF001
    radio._connected = True  # noqa: SLF001
    return radio


def _radio_freq_response() -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR, IC_7610_ADDR, 0x03, data=bcd_encode(14_074_000)
    )


async def test_civ_valid_frame_advances_the_stamp(civ_radio: IcomRadio) -> None:
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    frame = parse_civ_frame(_radio_freq_response())
    before = time.monotonic()
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame, generation=MagicMock()
    )
    stamp = civ_radio.last_valid_frame_monotonic  # noqa: SLF001
    assert stamp is not None
    assert before <= stamp <= time.monotonic()


async def test_civ_frame_not_from_the_radio_never_stamps(
    civ_radio: IcomRadio,
) -> None:
    """A frame sent by the controller (our own command on the wire) is not
    radio-originated information."""
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    echo = build_civ_frame(
        IC_7610_ADDR, CONTROLLER_ADDR, 0x03, data=bcd_encode(14_074_000)
    )
    frame = parse_civ_frame(echo)
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame, generation=MagicMock()
    )
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


async def test_civ_malformed_traffic_does_not_advance_the_stamp(
    civ_radio: IcomRadio,
) -> None:
    """Raw bytes on the wire (garbage CI-V) count for
    ``_last_civ_data_received`` but never for liveness: nothing decoded."""
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    garbage = b"\xff\xff\xff\xff"
    pkt = bytearray(CIV_HEADER_SIZE + len(garbage))
    struct.pack_into("<I", pkt, 0, len(pkt))
    pkt[CIV_HEADER_SIZE:] = garbage
    civ_radio._civ_transport.queue_response(bytes(pkt))  # noqa: SLF001

    civ_radio._civ_runtime.start_pump()  # noqa: SLF001
    await asyncio.sleep(0.15)
    if civ_radio._civ_rx_task:  # noqa: SLF001
        civ_radio._civ_rx_task.cancel()  # noqa: SLF001
        try:
            await civ_radio._civ_rx_task  # noqa: SLF001
        except asyncio.CancelledError:
            pass

    assert civ_radio._last_civ_data_received is not None  # noqa: SLF001
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


async def test_civ_receiver_startup_does_not_stamp(civ_radio: IcomRadio) -> None:
    """A freshly attached transport with a connected socket has received
    nothing decodable — receiver start-up is not liveness evidence."""
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


def test_civ_connection_generation_resets_the_stamp(civ_radio: IcomRadio) -> None:
    civ_radio.last_valid_frame_monotonic = time.monotonic()  # noqa: SLF001
    civ_radio._civ_runtime.advance_generation("test reconnect")  # noqa: SLF001
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


def test_civ_stale_after_derives_from_the_ready_idle_timeout(
    civ_radio: IcomRadio,
) -> None:
    """The threshold is the same window ``radio_ready`` grants a quiet but
    healthy CI-V stream — the backend's own timeout, not a new poller."""
    expected = int(civ_radio._civ_ready_idle_timeout * 1000)  # noqa: SLF001
    assert civ_radio.liveness_stale_after_ms == expected  # noqa: SLF001
    assert civ_radio.liveness_source == "radio"  # noqa: SLF001


# ---------------------------------------------------------------------------
# Yaesu CAT
# ---------------------------------------------------------------------------


@pytest.fixture
def cat_transport() -> YaesuCatTransport:
    transport = YaesuCatTransport(device="/dev/test", baudrate=38400, timeout=0.2)
    transport._connected = True  # noqa: SLF001
    return transport


async def test_cat_query_match_stamps_liveness(
    cat_transport: YaesuCatTransport,
) -> None:
    cat_transport._reader = FakeStreamReader([b"FA014074000;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001

    assert await cat_transport.query("FA;") == "FA014074000"
    assert cat_transport.last_valid_frame_monotonic is not None


async def test_cat_rejected_answer_does_not_stamp(
    cat_transport: YaesuCatTransport,
) -> None:
    cat_transport._reader = FakeStreamReader([b"?;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(CatCommandRejected):
        await cat_transport.query("FA;")
    assert cat_transport.last_valid_frame_monotonic is None


async def test_cat_silence_does_not_stamp(cat_transport: YaesuCatTransport) -> None:
    """No answer within the timeout — a reconnect-without-answer never
    counts as liveness."""
    cat_transport._reader = FakeStreamReader([])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(CatTimeoutError):
        await cat_transport.query("FA;")
    assert cat_transport.last_valid_frame_monotonic is None


async def test_cat_reconnect_resets_the_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock = MagicMock()
    monkeypatch.setattr(
        "rigplane.core.serial_open._open_with_idle_lines",
        lambda **kwargs: mock.open_serial_connection(**kwargs),
    )
    mock.open_serial_connection = AsyncMock(
        return_value=(FakeStreamReader([b"ID0840;"]), FakeStreamWriter())
    )
    transport = YaesuCatTransport(device="/dev/test", baudrate=38400, timeout=0.2)
    await transport.connect()
    assert await transport.query("ID;") == "ID0840"
    assert transport.last_valid_frame_monotonic is not None

    await transport.close()
    mock.open_serial_connection = AsyncMock(
        return_value=(FakeStreamReader([]), FakeStreamWriter())
    )
    await transport.connect()
    assert transport.last_valid_frame_monotonic is None


def test_cat_stale_after_derives_from_the_read_timeout() -> None:
    """``staleAfterMs`` is twice the transport's per-attempt read timeout —
    the same 2x ladder the CI-V health classifier uses; the CAT poller's
    75 ms fast / 1 s slow cadences stay far below it."""
    radio = YaesuCatRadio("/dev/test")
    assert radio.liveness_source == "radio"
    expected = int(radio._transport.timeout * 2 * 1000)  # noqa: SLF001
    assert radio.liveness_stale_after_ms == expected  # noqa: SLF001
    assert radio.liveness_stale_after_ms >= 2_000  # noqa: SLF001


# ---------------------------------------------------------------------------
# External rigctld client — daemon traffic is never radio liveness
# ---------------------------------------------------------------------------


async def test_rigctld_query_data_lines_stamp_liveness() -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            assert transport.last_valid_frame_monotonic is None
            assert await transport.query("f", response_lines=1) == ["14074000"]
            assert transport.last_valid_frame_monotonic is not None
        finally:
            await transport.close()


async def test_rigctld_command_ack_does_not_stamp() -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            await transport.command("F 14074000")
            assert transport.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_rigctld_reconnect_resets_the_stamp() -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        assert await transport.query("f", response_lines=1) == ["14074000"]
        assert transport.last_valid_frame_monotonic is not None
        await transport.close()

        await transport.connect()
        try:
            assert transport.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_rigctld_radio_reports_daemon_source_and_cadence_threshold() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert radio.liveness_source == "daemon"
            medium, _slow = resolve_external_rigctld_poll_intervals(
                build_external_rigctld_acquisition_profile(
                    vfo_supported=radio._vfo_supported  # noqa: SLF001
                )
            )
            expected = int((medium + radio._transport.timeout) * 1000)  # noqa: SLF001
            assert radio.liveness_stale_after_ms == expected
            # One full medium poll period of quiet stays fresh.
            assert radio.liveness_stale_after_ms > int(medium * 1000)
        finally:
            await radio.disconnect()


# ---------------------------------------------------------------------------
# Simulator end-to-end — the testkit IC-7610 simulator through the real
# UDP → RX pump → parse → route chain
# ---------------------------------------------------------------------------


async def test_simulator_frames_stamp_liveness(mock_radio: MockIcomRadio) -> None:
    radio = IcomRadio(
        host="127.0.0.1",
        port=mock_radio.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    with fast_connect():
        await radio.connect()
    try:
        assert isinstance(await radio.get_freq(), int)
        stamp = radio.last_valid_frame_monotonic  # noqa: SLF001
        assert stamp is not None
        payload = _liveness(radio)
        assert payload is not None
        assert payload["source"] == "radio"
        assert payload["lastFrameAgeMs"] is not None
        assert payload["lastFrameAgeMs"] < payload["staleAfterMs"]
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# Public surfaces — /api/v1/info and the WebSocket state payload
# ---------------------------------------------------------------------------


class _MemoryWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

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

    def is_closing(self) -> bool:
        return False

    def get_extra_info(self, *_args: Any, **_kwargs: Any) -> Any:
        return ("127.0.0.1", 0)


async def _serve_info(radio: Any) -> dict[str, Any]:
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _MemoryWriter()
    await server._handle_http(  # noqa: SLF001
        writer, "GET", "/api/v1/info", {"host": "localhost:8470"}
    )
    text = bytes(writer.buffer).decode("ascii", errors="replace")
    body = text.split("\r\n\r\n", 1)[1]
    return json.loads(body or "{}")


async def test_api_info_liveness_stays_null_until_a_real_read() -> None:
    """The daemon-only phase (connected, VFO probe refused, no rig read yet)
    must show daemon reachability as ``null`` age, never as radio liveness."""
    behavior = FakeRigctldBehavior(unsupported_commands={"v"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            payload = await _serve_info(radio)
            liveness = payload["connection"]["liveness"]
            assert liveness == {
                "lastFrameAgeMs": None,
                "staleAfterMs": radio.liveness_stale_after_ms,
                "source": "daemon",
            }
            # A fresh read of a radio value flips the age to a real number.
            assert await radio.get_freq() == 14_074_000
            liveness = (await _serve_info(radio))["connection"]["liveness"]
            assert liveness["lastFrameAgeMs"] is not None
            assert liveness["lastFrameAgeMs"] < liveness["staleAfterMs"]
            assert liveness["source"] == "daemon"
        finally:
            await radio.disconnect()


def test_ws_state_payload_carries_liveness_next_to_connection() -> None:
    radio = _StampRadio(stamp=time.monotonic() - 0.25, stale_after_ms=2_000)
    payload = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=radio,
        receiver_count=1,
    )
    liveness = payload["connection"]["liveness"]
    assert liveness is not None
    assert set(liveness) == {"lastFrameAgeMs", "staleAfterMs", "source"}
    assert isinstance(liveness["lastFrameAgeMs"], int)
    assert 0 <= liveness["lastFrameAgeMs"] < 2_000
    assert liveness["staleAfterMs"] == 2_000
    assert liveness["source"] == "radio"
    ServerStatePublic.model_validate(payload)


def test_ws_state_payload_liveness_is_null_without_a_radio() -> None:
    payload = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=None,
        receiver_count=1,
    )
    assert payload["connection"]["liveness"] is None
    ServerStatePublic.model_validate(payload)
