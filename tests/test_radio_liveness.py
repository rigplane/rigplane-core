"""MOR-3845: decoded radio liveness and conservative daemon-only evidence."""

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
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.ic9700 import Ic9700SerialRadio
from rigplane.backends.icom7610 import Icom7610SerialRadio
from dataclasses import replace
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
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
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
from test_icom7610_serial_radio import _FakeSerialCivLink


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
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
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
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
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


@pytest.mark.parametrize("command,data", [(0x04, b"\xff"), (0x03, b"\xff" * 5)])
async def test_civ_invalid_payload_preserves_stamp(
    civ_radio: IcomRadio, command: int, data: bytes
) -> None:
    civ_radio.last_valid_frame_monotonic = 1.0
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, command, data=data)
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0


@pytest.mark.parametrize(
    "command,sub,data",
    [
        (0x03, None, bcd_encode(14_074_000)),
        (0x27, 0x00, b"\x00\x01\x01"),
        (0xFB, None, b""),
        (0xFA, None, b""),
    ],
)
async def test_civ_retired_generation_cannot_stamp(
    civ_radio: IcomRadio, command: int, sub: int | None, data: bytes
) -> None:
    retired = civ_radio._civ_epoch  # noqa: SLF001
    civ_radio._civ_runtime.advance_generation("retire test source")  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, command, sub=sub, data=data)
    )
    await civ_radio._civ_runtime._route_civ_frame(frame, generation=retired)  # noqa: SLF001
    assert civ_radio.last_valid_frame_monotonic is None


def test_civ_connection_generation_resets_the_stamp(civ_radio: IcomRadio) -> None:
    civ_radio.last_valid_frame_monotonic = time.monotonic()  # noqa: SLF001
    civ_radio._civ_runtime.advance_generation("test reconnect")  # noqa: SLF001
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


def test_civ_generation_callback_sees_cleared_stamp(
    civ_radio: IcomRadio, monkeypatch: pytest.MonkeyPatch
) -> None:
    civ_radio.last_valid_frame_monotonic = 1.0
    tracker = civ_radio._civ_request_tracker  # noqa: SLF001
    advance = tracker.advance_generation
    observed: list[float | None] = []

    def advance_observed(error: Exception) -> int:
        observed.append(civ_radio.last_valid_frame_monotonic)
        return advance(error)

    monkeypatch.setattr(tracker, "advance_generation", advance_observed)
    civ_radio._civ_runtime.advance_generation("observe retirement")  # noqa: SLF001
    assert observed == [None]


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

    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    assert await radio.get_freq() == 14_074_000
    assert cat_transport.last_valid_frame_monotonic is not None


@pytest.mark.parametrize("receiver", [0, 1])
@pytest.mark.parametrize("code", range(7))
async def test_cat_agc_readback_is_valid_liveness(
    cat_transport: YaesuCatTransport, receiver: int, code: int
) -> None:
    cat_transport._reader = FakeStreamReader([f"GT{receiver}{code};".encode()])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    before = time.monotonic()
    assert await radio.get_agc(receiver=receiver) == code
    stamp = radio.last_valid_frame_monotonic
    assert stamp is not None
    assert before <= stamp <= time.monotonic()


@pytest.mark.parametrize("receiver", [0, 1])
async def test_cat_invalid_agc_preserves_stamp(
    cat_transport: YaesuCatTransport, receiver: int
) -> None:
    cat_transport._reader = FakeStreamReader([f"GT{receiver}Z;".encode()])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    with pytest.raises(ValueError):
        await radio.get_agc(receiver=receiver)
    assert radio.last_valid_frame_monotonic == 1.0


@pytest.mark.parametrize(
    "response,method", [(b"FAgarbage;", "get_freq"), (b"MD0Z;", "get_mode")]
)
async def test_cat_invalid_payload_preserves_stamp(
    cat_transport: YaesuCatTransport, response: bytes, method: str
) -> None:
    cat_transport._reader = FakeStreamReader([response])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    with pytest.raises(ValueError):
        await getattr(radio, method)()
    assert cat_transport.last_valid_frame_monotonic == 1.0


async def test_cat_raw_prefix_match_does_not_stamp(
    cat_transport: YaesuCatTransport,
) -> None:
    cat_transport._reader = FakeStreamReader([b"FA014074000;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    assert await cat_transport.query("FA;") == "FA014074000"
    assert cat_transport.last_valid_frame_monotonic is None


async def test_cat_close_clears_stamp_before_waiting(
    cat_transport: YaesuCatTransport,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    writer = FakeStreamWriter()

    async def wait_closed() -> None:
        entered.set()
        await release.wait()

    writer.wait_closed = wait_closed  # type: ignore[method-assign]
    cat_transport._writer = writer  # type: ignore[assignment]  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    task = asyncio.create_task(cat_transport.close())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        assert cat_transport.last_valid_frame_monotonic is None
    finally:
        release.set()
        await task


async def test_cat_failed_reopen_clears_stamp(monkeypatch: pytest.MonkeyPatch) -> None:
    transport = YaesuCatTransport(device="/dev/test")
    transport.last_valid_frame_monotonic = 1.0
    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port",
        AsyncMock(side_effect=OSError("closed")),
    )
    with pytest.raises(Exception, match="closed"):
        await transport.connect()
    assert transport.last_valid_frame_monotonic is None


async def test_cat_disconnect_clears_stamp_before_audio_wait(
    cat_transport: YaesuCatTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    observed: list[float | None] = []

    async def stop_rx() -> None:
        observed.append(radio.last_valid_frame_monotonic)

    monkeypatch.setattr(radio._audio_driver, "stop_rx", stop_rx)  # noqa: SLF001
    await radio.disconnect()
    assert observed == [None]


async def test_cat_retired_inflight_query_cannot_stamp(
    cat_transport: YaesuCatTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    entered = asyncio.Event()
    release = asyncio.Event()

    async def pending_query(*args: Any, **kwargs: Any) -> str:
        entered.set()
        await release.wait()
        return "FA014074000"

    monkeypatch.setattr(cat_transport, "query", pending_query)
    task = asyncio.create_task(radio.get_freq())
    try:
        await asyncio.wait_for(entered.wait(), 1)
        await cat_transport.close()
        release.set()
        await task
        assert radio.last_valid_frame_monotonic is None
    finally:
        release.set()
        await task


async def test_cat_reconnect_callback_sees_cleared_stamp(
    cat_transport: YaesuCatTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    poller = YaesuCatPoller(radio)
    observed: list[float | None] = []
    monkeypatch.setattr(cat_transport, "_maybe_reconnect_needed", lambda: True)
    monkeypatch.setattr(cat_transport, "reconnect", AsyncMock())
    poller.bind_provider_generation(
        capture=lambda: 1,
        advance=lambda: observed.append(radio.last_valid_frame_monotonic) or 1,
    )
    await poller._try_reconnect()  # noqa: SLF001
    assert observed == [None]


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
    transport.last_valid_frame_monotonic = time.monotonic()

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


@pytest.mark.parametrize("command,lines", [("f", 1), ("m", 2), ("t", 1), ("v", 1)])
async def test_rigctld_cached_data_cannot_prove_radio_liveness(
    command: str, lines: int
) -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            assert transport.last_valid_frame_monotonic is None
            await transport.query(command, response_lines=lines)
            assert transport.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_rigctld_physical_and_cached_frequency_are_indistinguishable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a simulated physical read loses its provenance at the wire boundary."""
    async with FakeRigctldServer() as server:
        physical_reads = []
        response_for = server._response_for

        def physical_response(line: str, key: str) -> bytes:
            if key == "f":
                physical_reads.append(server.state.frequency_hz)
            return response_for(line, key)

        monkeypatch.setattr(server, "_response_for", physical_response)
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio._transport.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            assert physical_reads == [14_074_000]
            assert radio.last_valid_frame_monotonic is None
            # Byte-identical cache hit: the client cannot distinguish it.
            server.behavior.malformed_responses["f"] = b"14074000\n"
            assert await radio.get_freq() == 14_074_000
            assert physical_reads == [14_074_000]
            assert radio.last_valid_frame_monotonic is None
        finally:
            await radio.disconnect()


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
        transport.last_valid_frame_monotonic = time.monotonic()
        await transport.close()
        assert transport.last_valid_frame_monotonic is None

        await transport.connect()
        try:
            assert transport.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_rigctld_invalid_payload_preserves_stamp() -> None:
    async with FakeRigctldServer(
        behavior=FakeRigctldBehavior(malformed_responses={"f": b"garbage\n"})
    ) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            radio._transport.last_valid_frame_monotonic = 1.0  # noqa: SLF001
            with pytest.raises(Exception, match="malformed frequency"):
                await radio.get_freq()
            assert radio.last_valid_frame_monotonic == 1.0
        finally:
            await radio.disconnect()


async def test_rigctld_cancellation_clears_stamp_before_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        transport.last_valid_frame_monotonic = 1.0
        observed: list[float | None] = []
        transport.bind_provider_generation(
            advance=lambda: observed.append(transport.last_valid_frame_monotonic) or 1
        )
        entered = asyncio.Event()

        async def pending_read(*args: Any) -> str:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        monkeypatch.setattr(transport, "_read_line", pending_read)
        task = asyncio.create_task(transport.query("f", response_lines=1))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await task
            assert observed == [None]
            assert transport.last_valid_frame_monotonic is None
        finally:
            await transport.close()


async def test_rigctld_failed_reopen_clears_stamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = RigctldTransport(host="127.0.0.1")
    transport.last_valid_frame_monotonic = 1.0
    monkeypatch.setattr(
        asyncio, "open_connection", AsyncMock(side_effect=OSError("closed"))
    )
    with pytest.raises(Exception, match="closed"):
        await transport.connect()
    assert transport.last_valid_frame_monotonic is None


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


@pytest.mark.parametrize(
    "radio_cls", [Ic705SerialRadio, Ic7300SerialRadio, Icom7610SerialRadio, Ic9700SerialRadio]
)
@pytest.mark.parametrize("action", ["disconnect", "failed_reopen"])
async def test_serial_partial_lifecycle_retires_liveness_before_wait(
    radio_cls: Any, action: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rigplane import RadioConnectionState
    from rigplane.exceptions import ConnectionError as RadioConnectionError

    link = _FakeSerialCivLink(model_id=0x01)
    radio = radio_cls(
        device="/dev/test", civ_link=link, _enumerate_serial_ports_fn=lambda: []
    )
    radio._profile = replace(radio._profile, expected_identity_ids=("FF",))
    radio._civ_min_interval = 0.001
    radio._civ_get_timeout = 0.05
    await radio.connect()
    assert radio.conn_state is RadioConnectionState.CONNECTING
    assert radio.last_valid_frame_monotonic is not None
    assert (await _serve_info(radio))["connection"]["liveness"]["lastFrameAgeMs"] is not None
    retired = radio._civ_epoch
    entered, release = asyncio.Event(), asyncio.Event()

    async def pending() -> None:
        entered.set()
        await release.wait()
        if action == "failed_reopen":
            raise OSError("reopen failed")

    if action == "disconnect":
        monkeypatch.setattr(radio, "_stop_civ_data_watchdog", pending)
        task = asyncio.create_task(radio.disconnect())
    else:
        link.connected = link.ready = link.healthy = False
        monkeypatch.setattr(radio._serial_session, "connect", pending)
        task = asyncio.create_task(radio.connect())
    try:
        await asyncio.wait_for(entered.wait(), 2)
        assert (await _serve_info(radio))["connection"]["liveness"]["lastFrameAgeMs"] is None
        frame = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, radio._radio_addr, 0x19, sub=0, data=b"\x01")
        )
        await radio._civ_runtime._route_civ_frame(frame, generation=retired)
        assert radio.last_valid_frame_monotonic is None
        release.set()
        if action == "failed_reopen":
            with pytest.raises(RadioConnectionError, match="reopen failed"):
                await task
        else:
            await task
        assert (await _serve_info(radio))["connection"]["liveness"]["lastFrameAgeMs"] is None
    finally:
        release.set()
        await asyncio.gather(task, return_exceptions=True)
        monkeypatch.undo()
        await radio.disconnect()


async def test_identity_only_liveness_refresh_reaches_existing_websocket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace
    from rigplane.web import runtime_helpers
    from test_web_server import _close_ws, _ws_connect, _ws_recv_frame

    radio = IcomRadio("127.0.0.1", model="IC-7610")
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    radio.set_state_change_callback(server._on_radio_state_change)
    listener = await asyncio.start_server(server._handle_connection, "127.0.0.1", 0)
    async with listener:
        port = listener.sockets[0].getsockname()[1]
        reader, writer, _ = await _ws_connect("127.0.0.1", port, "/api/v1/ws")

        async def receive_state() -> dict[str, Any]:
            async with asyncio.timeout(5):
                while True:
                    _, payload = await _ws_recv_frame(reader)
                    event = json.loads(payload)
                    if event["type"] == "state_update":
                        return event["data"]

        try:
            baseline = await receive_state()
            assert baseline["data"]["connection"]["liveness"]["lastFrameAgeMs"] is None
            before = radio._state_store.snapshot()
            for _ in range(2):
                frame = parse_civ_frame(
                    build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x19, sub=0, data=b"\x01")
                )
                await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)
                update = await receive_state()
                body = update.get("changed", update.get("data"))
                assert body["connection"]["liveness"]["lastFrameAgeMs"] is not None
                assert update["transportSeq"] > baseline["transportSeq"]
                key = server._public_state_delivery_key(before, health_revision=0)
                with monkeypatch.context() as clock:
                    clock.setattr(
                        runtime_helpers, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + 1)
                    )
                    assert server._public_state_delivery_key(before, health_revision=0) == key
                baseline = update
            after = radio._state_store.snapshot()
            assert after.state_revision == before.state_revision
            assert after.freshness_revision == before.freshness_revision
            assert after.observation_seq == before.observation_seq
        finally:
            await _close_ws(writer)
            radio.set_state_change_callback(None)


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
            # Standard rigctld replies carry no physical-read provenance.
            assert await radio.get_freq() == 14_074_000
            liveness = (await _serve_info(radio))["connection"]["liveness"]
            assert liveness["lastFrameAgeMs"] is None
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


# ---------------------------------------------------------------------------
# MOR-3845 round 3: only successfully decoded radio information stamps
# ---------------------------------------------------------------------------


async def test_civ_empty_ptt_frame_preserves_stamp(civ_radio: IcomRadio) -> None:
    """A 1C/00 frame with no PTT value decodes only to an unknown-PTT
    observation — unknown is not radio information, so the stamp is kept."""
    civ_radio.last_valid_frame_monotonic = 1.0  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x1C, sub=0x00, data=b"")
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0  # noqa: SLF001


@pytest.mark.parametrize("data", [b"\x02", b"\xff", b"\x00\x00"])
async def test_civ_invalid_ptt_byte_preserves_stamp(
    civ_radio: IcomRadio, data: bytes
) -> None:
    """PTT bytes outside the exact 0x00/0x01 readback never count as decoded
    radio information, even though they still project a fail-closed unknown."""
    civ_radio.last_valid_frame_monotonic = 1.0  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x1C, sub=0x00, data=data)
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0  # noqa: SLF001


async def test_civ_orphan_scope_chunk_preserves_stamp(
    civ_radio: IcomRadio,
) -> None:
    """An orphan 27/00 continuation (final sequence with no sequence-1
    metadata and no pixels) is an incomplete assembly, not a valid frame."""
    civ_radio.last_valid_frame_monotonic = 1.0  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(
            CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0x00, data=b"\x00\x02\x02"
        )
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0  # noqa: SLF001


async def test_civ_invalid_bcd_att_preserves_stamp(civ_radio: IcomRadio) -> None:
    """A 0x11 attenuator byte with invalid BCD nibbles is undecodable: no
    observation and no stamp."""
    civ_radio.last_valid_frame_monotonic = 1.0  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x11, data=b"\xff")
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0  # noqa: SLF001


async def test_civ_valid_scope_frame_stamps_liveness(
    civ_radio: IcomRadio,
) -> None:
    """Valid counterpart: a complete single-packet LAN scope frame (sequence
    1 of 1 with metadata and pixels) still advances the stamp."""
    from rigplane.types import bcd_encode as _bcd_encode

    payload = (
        b"\x00"  # receiver MAIN
        b"\x01\x01"  # seq 1 of 1 (BCD)
        b"\x01"  # fixed mode
        + _bcd_encode(14_000_000)
        + _bcd_encode(14_350_000)
        + b"\x00"  # in range
        + bytes(range(10))  # pixels
    )
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0x00, data=payload)
    )
    before = time.monotonic()
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    stamp = civ_radio.last_valid_frame_monotonic  # noqa: SLF001
    assert stamp is not None
    assert before <= stamp <= time.monotonic()


@pytest.mark.parametrize(
    "answer,reader",
    [
        (b"RA0Z;", "read_attenuator"),
        (b"PA0Z;", "read_preamp"),
        (b"FTZ;", "get_tx_source"),
        (b"VSZ;", "read_vfo_select"),
    ],
)
async def test_cat_garbage_single_char_answer_preserves_stamp(
    cat_transport: YaesuCatTransport, answer: bytes, reader: str
) -> None:
    """A template match on a non-numeric char is not a decoded value: the
    reader must reject it and the pre-existing stamp must be preserved."""
    cat_transport._reader = FakeStreamReader([answer])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    cat_transport.last_valid_frame_monotonic = 1.0
    with pytest.raises(ValueError):
        await getattr(radio, reader)()
    assert cat_transport.last_valid_frame_monotonic == 1.0


@pytest.mark.parametrize(
    "answer,reader,expected",
    [
        (b"RA01;", "read_attenuator", True),
        (b"PA02;", "read_preamp", 2),
        (b"FT1;", "get_tx_source", 1),
        (b"VS1;", "read_vfo_select", 1),
    ],
)
async def test_cat_numeric_single_char_answer_stamps_liveness(
    cat_transport: YaesuCatTransport,
    answer: bytes,
    reader: str,
    expected: object,
) -> None:
    """Valid counterparts: exact-digit answers decode and advance the stamp."""
    cat_transport._reader = FakeStreamReader([answer])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    assert await getattr(radio, reader)() == expected
    assert cat_transport.last_valid_frame_monotonic is not None


@pytest.mark.parametrize(
    "command,sub,data",
    [
        (0x14, 0x01, b"\x00\xff"),
        (0x15, 0x02, b"\xff\x00"),
    ],
)
async def test_civ_invalid_bcd_level_preserves_stamp(
    civ_radio: IcomRadio, command: int, sub: int, data: bytes
) -> None:
    civ_radio.last_valid_frame_monotonic = 1.0
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, command, sub=sub, data=data)
    )
    await civ_radio._civ_runtime._route_civ_frame(
        frame, generation=civ_radio._civ_epoch
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0


async def test_civ_identity_reply_advances_the_stamp(
    civ_radio: IcomRadio,
) -> None:
    """A validated 19/00 transceiver-ID reply is decoded radio information:
    identity-only traffic must advance the stamp without touching the store."""
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    before_store = civ_radio._state_store.snapshot()  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x19, sub=0x00, data=b"\x01\x06")
    )
    before = time.monotonic()
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    stamp = civ_radio.last_valid_frame_monotonic  # noqa: SLF001
    assert stamp is not None
    assert before <= stamp <= time.monotonic()
    after_store = civ_radio._state_store.snapshot()  # noqa: SLF001
    assert after_store.state_revision == before_store.state_revision
    assert after_store.observation_seq == before_store.observation_seq


async def test_civ_malformed_identity_reply_preserves_stamp(
    civ_radio: IcomRadio,
) -> None:
    """A 19/00 reply with no identity bytes carries no evidence."""
    civ_radio.last_valid_frame_monotonic = 1.0  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x19, sub=0x00, data=b"")
    )
    await civ_radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=civ_radio._civ_epoch,  # noqa: SLF001
    )
    assert civ_radio.last_valid_frame_monotonic == 1.0  # noqa: SLF001


async def test_civ_retired_identity_reply_cannot_stamp(
    civ_radio: IcomRadio,
) -> None:
    """A valid 19/00 reply on a retired connection generation is discarded."""
    civ_radio.last_valid_frame_monotonic = None  # noqa: SLF001
    retired = civ_radio._civ_epoch  # noqa: SLF001
    civ_radio._civ_runtime.advance_generation("retire test source")  # noqa: SLF001
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x19, sub=0x00, data=b"\x01\x06")
    )
    await civ_radio._civ_runtime._route_civ_frame(frame, generation=retired)  # noqa: SLF001
    assert civ_radio.last_valid_frame_monotonic is None  # noqa: SLF001


async def test_cat_identity_answer_advances_the_stamp(
    cat_transport: YaesuCatTransport,
) -> None:
    """The connect-time ``ID;`` read decodes ``ID0840;`` — that validated
    identity reply must stamp liveness like any other decoded read."""
    cat_transport._reader = FakeStreamReader([b"ID0840;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    before = time.monotonic()
    assert await radio._read_identity_answer() == "0840"  # noqa: SLF001
    stamp = cat_transport.last_valid_frame_monotonic
    assert stamp is not None
    assert before <= stamp <= time.monotonic()


async def test_cat_malformed_identity_answer_preserves_stamp(
    cat_transport: YaesuCatTransport,
) -> None:
    """A non-numeric ``ID;`` answer is not identity evidence."""
    cat_transport._reader = FakeStreamReader([b"IDZZZZ;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    assert await radio._read_identity_answer() is None  # noqa: SLF001
    assert cat_transport.last_valid_frame_monotonic is None


async def test_cat_silent_identity_answer_preserves_stamp(
    cat_transport: YaesuCatTransport,
) -> None:
    """Silence on the ``ID;`` probe carries no evidence."""
    cat_transport._reader = FakeStreamReader([])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    assert await radio._read_identity_answer() is None  # noqa: SLF001
    assert cat_transport.last_valid_frame_monotonic is None


async def test_cat_retired_identity_answer_cannot_stamp(
    cat_transport: YaesuCatTransport, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An ``ID;`` answer that outlives its connection generation (a reconnect
    raced the probe) validates but must not stamp."""
    cat_transport._reader = FakeStreamReader([b"ID0840;"])  # type: ignore[assignment]  # noqa: SLF001
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]  # noqa: SLF001
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport  # noqa: SLF001
    real_query = cat_transport.query

    async def _retiring_query(command: str, **kwargs: object) -> str:
        radio._identity_generation += 1  # noqa: SLF001
        return await real_query(command, **kwargs)

    monkeypatch.setattr(cat_transport, "query", _retiring_query)
    assert await radio._read_identity_answer() == "0840"  # noqa: SLF001
    assert cat_transport.last_valid_frame_monotonic is None


@pytest.mark.parametrize("value", [True, -1, 10000, "Z", "840", "０８４０"])
async def test_cat_invalid_decoded_identity_preserves_stamp(
    cat_transport: YaesuCatTransport, monkeypatch: pytest.MonkeyPatch, value: object
) -> None:
    cat_transport._reader = FakeStreamReader([b"ID0840;"])  # type: ignore[assignment]
    cat_transport._writer = FakeStreamWriter()  # type: ignore[assignment]
    cat_transport.last_valid_frame_monotonic = 1.0
    radio = YaesuCatRadio("/dev/test")
    radio._transport = cat_transport
    monkeypatch.setattr(radio._parsers["get_id"], "parse", lambda _: {"model": value})
    assert await radio._read_identity_answer() is None
    assert radio.last_valid_frame_monotonic == 1.0


async def test_civ_incomplete_scope_sequence_preserves_stamp(
    civ_radio: IcomRadio,
) -> None:
    civ_radio.last_valid_frame_monotonic = 1.0
    metadata = (
        b"\x00\x01\x03\x01" + bcd_encode(14_000_000) + bcd_encode(14_350_000) + b"\x00"
    )
    for payload in (metadata, b"\x00\x03\x03\x10"):
        frame = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0, data=payload)
        )
        await civ_radio._civ_runtime._route_civ_frame(
            frame, generation=civ_radio._civ_epoch
        )
    assert civ_radio.last_valid_frame_monotonic == 1.0


async def test_civ_scope_assembly_cannot_cross_connection_generation(
    civ_radio: IcomRadio,
) -> None:
    runtime = civ_radio._civ_runtime
    metadata = (
        b"\x00\x01\x02\x01" + bcd_encode(14_000_000) + bcd_encode(14_350_000) + b"\x00"
    )
    frame = parse_civ_frame(
        build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0, data=metadata)
    )
    await runtime._route_civ_frame(frame, generation=civ_radio._civ_epoch)
    runtime.advance_generation("retire partial scope")
    frame = parse_civ_frame(
        build_civ_frame(
            CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0, data=b"\x00\x02\x02\x10"
        )
    )
    await runtime._route_civ_frame(frame, generation=civ_radio._civ_epoch)
    assert civ_radio.last_valid_frame_monotonic is None


async def test_civ_invalid_scope_metadata_cannot_seed_liveness(
    civ_radio: IcomRadio,
) -> None:
    civ_radio.last_valid_frame_monotonic = 1.0
    metadata = b"\x00\x01\x02\x01" + b"\xff" * 5 + bcd_encode(14_350_000) + b"\x00"
    for payload in (metadata, b"\x00\x02\x02\x10"):
        frame = parse_civ_frame(
            build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0x27, sub=0, data=payload)
        )
        await civ_radio._civ_runtime._route_civ_frame(
            frame, generation=civ_radio._civ_epoch
        )
    assert civ_radio.last_valid_frame_monotonic == 1.0
