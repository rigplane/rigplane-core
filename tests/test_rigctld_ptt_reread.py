# mypy: disable-error-code=untyped-decorator
"""MOR-1903 — standalone rigctld drives its own CI-V PTT re-read.

Standalone ``rigplane serve`` has no cadence poller: ``due_requests()`` is
called only by the web radio poller, so nothing in that deployment ever sends a
``0x1C/0x00`` read after connect. With no observation the strict resolver behind
the MOR-1881 DEFER gate stays UNKNOWN forever and every frequency, mode, VFO and
key-down command is refused with ``RPRT -9``.

These tests pin the missing *request*, never new data: the value that clears the
gate may only arrive through the existing CI-V ingress, decoded from a real
radio reply (MOR-1900 — no mirror-derived truth, no synthesised default).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Generator
from typing import Any
from unittest.mock import AsyncMock

import pytest

from test_radio import MockTransport

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.radio import IcomRadio
from rigplane.rigctld import handler as handler_mod
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.protocol import parse_line
from rigplane.rigctld.server import PTT_REREAD_INTERVAL_SECONDS, RigctldServer
from rigplane.runtime import tx_interlock
from rigplane.runtime._civ_rx import _OBSERVATION_MAX_AGE_SECONDS
from rigplane.types import CivFrame

_PTT_PATH = FieldPath.global_("tx_state", "ptt")
_PTT_TTL_SECONDS = _OBSERVATION_MAX_AGE_SECONDS[("global", "tx_state", "ptt")]

# Profiles with no ``[state_acquisition]`` block at all — no scheduler is built
# for them, so the on-demand acquisition path cannot help them either.
_UNDECLARED_CIV_MODELS = ("IC-705", "IC-9700", "X6100")


def _make_radio(model: str) -> IcomRadio:
    radio = IcomRadio("198.51.100.10", model=model)
    transport = MockTransport()
    # ``assert_radio_startup_ready`` probes this on the control transport.
    transport._udp_transport = object()  # type: ignore[attr-defined]
    radio._civ_transport = transport  # noqa: SLF001
    radio._ctrl_transport = transport  # noqa: SLF001
    radio._connected = True  # noqa: SLF001
    radio._civ_recovering = False  # noqa: SLF001
    radio._civ_stream_ready = True  # noqa: SLF001
    radio._last_civ_data_received = time.monotonic()  # noqa: SLF001
    return radio


def _ptt_reply(radio: IcomRadio, *, transmitting: bool) -> CivFrame:
    """The radio's own answer to ``0x1C/0x00`` — the only admissible source."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=radio._radio_addr,  # noqa: SLF001
        command=0x1C,
        sub=0x00,
        data=bytes([1 if transmitting else 0]),
        receiver=None,
    )


async def _deliver(radio: IcomRadio, frame: CivFrame) -> None:
    await radio._civ_runtime._route_civ_frame(  # noqa: SLF001
        frame,
        generation=radio._civ_epoch,  # noqa: SLF001
    )


def _attach_handler(server: RigctldServer, radio: Any) -> Any:
    """Build the handler exactly as ``RigctldServer.start()`` does."""
    server._bootstrap_state_acquisition()  # noqa: SLF001
    store = server._state_store  # noqa: SLF001
    assert store is not None
    handler = handler_mod.RigctldHandler(
        radio,
        server._config,  # noqa: SLF001
        state_store=store,
        state_model_service=server._state_model_service,  # noqa: SLF001
    )
    handler.bind_provider_generation(lambda: store.provider_generation)
    server._rig_handler = handler  # noqa: SLF001
    return handler


def _ptt_read_calls(send_civ: AsyncMock) -> list[Any]:
    return [
        call
        for call in send_civ.await_args_list
        if call.args[:1] == (0x1C,) and call.kwargs.get("sub") == 0x00
    ]


@pytest.fixture  # type: ignore[untyped-decorator]
def civ_radio() -> Generator[IcomRadio, None, None]:
    radio = _make_radio("IC-705")
    yield radio
    radio._connected = False  # noqa: SLF001


# --- AC-1: the cadence is sub-TTL, derived from the field's own TTL ---------


def test_ptt_reread_interval_is_derived_from_the_observation_ttl() -> None:
    """Two reads per freshness window, derived from ``_civ_rx``'s own table.

    A cadence at or above the TTL reproduces today's sawtooth (MOR-1903 §2.6).
    """
    assert 0 < PTT_REREAD_INTERVAL_SECONDS < _PTT_TTL_SECONDS
    assert _PTT_TTL_SECONDS / PTT_REREAD_INTERVAL_SECONDS >= 2.0


# --- AC-2: RF truth stays resolvable for as long as the radio answers -------


async def test_rf_truth_never_decays_while_the_radio_answers(
    civ_radio: IcomRadio,
) -> None:
    """Sampled across more than two freshness windows: never UNKNOWN."""
    server = RigctldServer(civ_radio, RigctldConfig(port=0))
    handler = _attach_handler(server, civ_radio)
    send_civ = AsyncMock(return_value=None)

    async def answering_send_civ(cmd: int, **kwargs: Any) -> None:
        await send_civ(cmd, **kwargs)
        if cmd == 0x1C and kwargs.get("sub") == 0x00:
            await _deliver(civ_radio, _ptt_reply(civ_radio, transmitting=False))

    civ_radio.send_civ = answering_send_civ  # type: ignore[method-assign]
    server._client_count = 1  # noqa: SLF001
    server._start_ptt_reread_task()  # noqa: SLF001
    try:
        await asyncio.sleep(PTT_REREAD_INTERVAL_SECONDS * 2.0)
        deadline = time.monotonic() + _PTT_TTL_SECONDS * 2.5
        samples = 0
        while time.monotonic() < deadline:
            assert (
                handler._resolve_rigctld_rf_state()  # noqa: SLF001
                is tx_interlock.RfState.RX
            )
            samples += 1
            await asyncio.sleep(0.05)
        assert samples > 20
    finally:
        await server._stop_ptt_reread_task()  # noqa: SLF001

    calls = _ptt_read_calls(send_civ)
    assert len(calls) >= 2, calls
    assert all(call.kwargs.get("wait_response") is False for call in calls)


# --- AC-3: DEFER writes and key-down succeed once a real answer lands -------


@pytest.mark.parametrize("model", _UNDECLARED_CIV_MODELS)
async def test_writes_succeed_after_the_radio_answers_the_reread(model: str) -> None:
    radio = _make_radio(model)
    send_civ = AsyncMock(return_value=None)
    radio.send_civ = send_civ  # type: ignore[method-assign]
    server = RigctldServer(radio, RigctldConfig(port=0))
    handler = _attach_handler(server, radio)
    server._client_count = 1  # noqa: SLF001
    server._start_ptt_reread_task()  # noqa: SLF001
    try:
        for wire in (b"F 14074000", b"M USB 2400", b"V VFOB", b"T 1"):
            refused = await handler.execute(parse_line(wire), session_id="s1")
            assert refused.error is not None and int(refused.error) == -9, wire

        await asyncio.sleep(PTT_REREAD_INTERVAL_SECONDS * 4.0)
        assert _ptt_read_calls(send_civ), "no 0x1C/0x00 read was ever sent"

        await _deliver(radio, _ptt_reply(radio, transmitting=False))
        assert handler._resolve_rigctld_rf_state() is tx_interlock.RfState.RX  # noqa: SLF001

        # ``V VFOB`` on a dual-RX rig (IC-9700) takes the ACK-confirmed
        # receiver-switch path, which a non-answering stub cannot complete.
        accepted_wires = [b"F 14074000", b"M USB 2400", b"T 1"]
        if radio.receiver_count == 1:
            accepted_wires.insert(2, b"V VFOB")
        for wire in accepted_wires:
            accepted = await handler.execute(parse_line(wire), session_id="s1")
            assert accepted.error is None or int(accepted.error) == 0, (wire, accepted)
    finally:
        handler.stop_key_down_backstop()
        await server._stop_ptt_reread_task()  # noqa: SLF001
        radio._connected = False  # noqa: SLF001


# --- AC-4: absence is never filled in (the MOR-1900 invariant) --------------


async def test_unanswered_reread_never_produces_rf_truth(
    civ_radio: IcomRadio,
) -> None:
    """A radio that does not answer must leave the gate closed.

    Never an observation of the driver's own, never the legacy PTT mirror.
    """
    send_civ = AsyncMock(return_value=None)
    civ_radio.send_civ = send_civ  # type: ignore[method-assign]
    server = RigctldServer(civ_radio, RigctldConfig(port=0))
    handler = _attach_handler(server, civ_radio)
    server._client_count = 1  # noqa: SLF001
    server._start_ptt_reread_task()  # noqa: SLF001
    try:
        await asyncio.sleep(PTT_REREAD_INTERVAL_SECONDS * 6.0)
        assert len(_ptt_read_calls(send_civ)) >= 2

        store = server._state_store  # noqa: SLF001
        assert store is not None
        with pytest.raises(KeyError):
            store.snapshot().field(_PTT_PATH)
        assert handler._resolve_rigctld_rf_state() is tx_interlock.RfState.UNKNOWN  # noqa: SLF001

        for wire in (b"F 14074000", b"M USB 2400", b"V VFOB", b"T 1"):
            refused = await handler.execute(parse_line(wire), session_id="s1")
            assert refused.error is not None and int(refused.error) == -9, wire
    finally:
        handler.stop_key_down_backstop()
        await server._stop_ptt_reread_task()  # noqa: SLF001


# --- AC-5: client gating and clean teardown ---------------------------------


async def test_reread_task_is_cancelled_and_awaited_on_stop(
    civ_radio: IcomRadio,
) -> None:
    civ_radio.send_civ = AsyncMock(return_value=None)  # type: ignore[method-assign]
    server = RigctldServer(civ_radio, RigctldConfig(port=0))
    await server.start()
    try:
        task = server._ptt_reread_task  # noqa: SLF001
        assert task is not None and not task.done()
    finally:
        await server.stop()
    assert server._ptt_reread_task is None  # noqa: SLF001
    assert task.done()


async def test_started_server_drives_reads_only_while_a_client_is_connected(
    civ_radio: IcomRadio,
) -> None:
    """End-to-end over the real listener and the real wire protocol."""
    send_civ = AsyncMock(return_value=None)
    civ_radio.send_civ = send_civ  # type: ignore[method-assign]
    server = RigctldServer(civ_radio, RigctldConfig(host="127.0.0.1", port=0))
    await server.start()
    try:
        await asyncio.sleep(PTT_REREAD_INTERVAL_SECONDS * 4.0)
        assert _ptt_read_calls(send_civ) == []

        assert server._server is not None  # noqa: SLF001
        port = server._server.sockets[0].getsockname()[1]  # noqa: SLF001
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            await asyncio.sleep(PTT_REREAD_INTERVAL_SECONDS * 6.0)
            assert len(_ptt_read_calls(send_civ)) >= 2

            # Unanswered so far: the seat is still fail-closed on the wire.
            writer.write(b"F 14074000\n")
            await writer.drain()
            assert await reader.readline() == b"RPRT -9\n"

            await _deliver(civ_radio, _ptt_reply(civ_radio, transmitting=False))
            writer.write(b"F 14074000\n")
            await writer.drain()
            assert await reader.readline() == b"RPRT 0\n"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await server.stop()


# --- AC-7: non-CI-V radios are untouched by 1903-A (FTX-1 gap: MOR-1903-B) --


def test_yaesu_radio_gets_no_civ_reread_task() -> None:
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
    from rigplane.radio_protocol import CivCommandCapable

    radio = YaesuCatRadio("/dev/null")
    assert not isinstance(radio, CivCommandCapable)

    server = RigctldServer(radio, RigctldConfig(port=0))
    server._bootstrap_state_acquisition()  # noqa: SLF001
    server._client_count = 1  # noqa: SLF001
    server._start_ptt_reread_task()  # noqa: SLF001
    assert server._ptt_reread_task is None  # noqa: SLF001


@pytest.mark.xfail(
    reason="MOR-1903-B: no Yaesu acquisition executor exists for the standalone "
    "rigctld drain, so FTX-1 can never resolve RF truth in that deployment.",
    strict=True,
)
def test_yaesu_standalone_can_resolve_rf_truth() -> None:
    from rigplane.backends.yaesu_cat.radio import YaesuCatRadio

    radio = YaesuCatRadio("/dev/null")
    server = RigctldServer(radio, RigctldConfig(port=0))
    handler = _attach_handler(server, radio)
    assert handler._resolve_rigctld_rf_state() is not tx_interlock.RfState.UNKNOWN  # noqa: SLF001
