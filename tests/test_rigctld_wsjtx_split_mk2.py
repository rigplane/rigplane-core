"""WSJT-X "Split Operation: Rig" wire sequence against the IC-7300MK2 profile.

MOR-3115 reproduction: the rigctld command table implements ``S``/``s``
(set/get_split_vfo) but has no split-frequency / split-mode surface at
all.  WSJT-X "Split Operation: Rig" drives split through the Hamlib API
calls below, which netrigctl renders onto the wire as the short commands
``I``/``i`` (set/get split TX frequency) and ``X``/``x`` (set/get split
TX mode):

- ``Transceiver/HamlibTransceiver.cpp`` (WSJT-X ``do_tx_frequency``):
  ``rig_set_split_vfo`` ("tickle"), then ``rig_set_split_freq_mode``,
  then ``rig_set_split_vfo`` again ("Enable split last"); ``do_mode``
  in split reads ``rig_get_split_mode`` first.
- ``src/rig.c`` (Hamlib frontend ``rig_set_split_freq_mode``): when the
  backend provides no combined callback (netrigctl does not), it
  composes ``rig_set_split_freq`` + ``rig_set_split_mode``.
- ``rigs/dummy/netrigctl.c`` (wire): ``"I%s %s\\n"`` / ``"X%s %s %li\\n"``
  / ``"i%s\\n"`` / ``"x%s\\n"`` with the VFO prefix empty while
  ``\\chk_vfo`` answers ``0`` — which this server does for the
  single-receiver MK2 profile.

So a correct server must answer ``RPRT 0`` to ``I`` and ``X`` and reply
with values to ``i`` and ``x``.  Today the parser rejects all four as
unknown commands and the server answers ``RPRT -4`` (ENIMPL), which
netrigctl surfaces as ``-RIG_ENIMPL``; WSJT-X's ``error_check`` turns
that into a rig-control failure — split operation never starts.

RED pin for the gap recorded in MOR-3115: the split-frequency surface
needs new rigctld + runtime work (feature-sized) and is intentionally
not implemented here.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from serial_stub import SerialMockRadio
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer


def _apply_store_value(
    store: Any, path: FieldPath, value: Any, *, max_age: float
) -> None:
    # apply_current binds the store's live provider generation; a bare
    # apply() with the default generation 0 is silently rejected once the
    # server has advanced the store generation past 0.
    store.apply_current(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source="test",
                provider="tests",
                command_source="rigctld",
            ),
            timestamp_monotonic=1.0,
            max_age=max_age,
        )
    )


async def _exchange(
    writer: asyncio.StreamWriter,
    reader: asyncio.StreamReader,
    payload: bytes,
) -> bytes:
    """Send one command line and read everything currently replyable."""
    writer.write(payload)
    await writer.drain()
    return await asyncio.wait_for(reader.read(4096), timeout=2.0)


@pytest.fixture
def cfg() -> RigctldConfig:
    return RigctldConfig(
        host="127.0.0.1",
        port=0,
        max_clients=3,
        client_timeout=5.0,
        command_timeout=2.0,
    )


async def test_wsjtx_rig_split_sequence_on_ic7300mk2(cfg: RigctldConfig) -> None:
    """Drive the exact WSJT-X Rig-split sequence; every reply must be Hamlib-correct."""
    radio = SerialMockRadio(model="IC-7300MK2")
    await radio.connect()
    srv = RigctldServer(radio, cfg)
    await srv.start()
    # DEFER-classified split writes fail closed on unknown RF state; seed a
    # known-RX observation so the TX interlock stays out of this test.
    _apply_store_value(
        srv._state_store, FieldPath.global_("tx_state", "ptt"), False, max_age=1e9
    )
    try:
        host, port = srv._server.sockets[0].getsockname()[:2]  # type: ignore[union-attr]
        reader, writer = await asyncio.open_connection(host, port)
        try:
            # -- Hamlib open handshake --------------------------------------
            # Single-RX profile: chk_vfo answers "0", so netrigctl sends the
            # bare short forms for the rest of the session.
            assert await _exchange(writer, reader, b"\\chk_vfo\n") == b"0\n"

            # -- WSJT-X poll (do_poll): get_vfo, get_split_vfo, get_freq ----
            assert await _exchange(writer, reader, b"v\n") == b"VFOA\n"
            assert await _exchange(writer, reader, b"s\n") == b"0\nVFOA\n"
            assert await _exchange(writer, reader, b"f\n") == b"14074000\n"

            # -- do_tx_frequency(tx=14080100, mode=USB) ---------------------
            # "tickle" split so Hamlib's internal TX-VFO routing is correct.
            assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"

            # rig_set_split_freq -> netrigctl "I 14080100".
            # RED today: server answers RPRT -4 (unknown command 'I').
            assert await _exchange(writer, reader, b"I 14080100\n") == b"RPRT 0\n"

            # rig_set_split_mode -> netrigctl "X USB 0".
            # RED today: server answers RPRT -4 (unknown command 'X').
            assert await _exchange(writer, reader, b"X USB 0\n") == b"RPRT 0\n"

            # "Enable split last" (WSJT-X comment in do_tx_frequency).
            assert await _exchange(writer, reader, b"S 1 VFOB\n") == b"RPRT 0\n"

            # -- do_mode in split: rig_get_split_mode -> netrigctl "x" ------
            # Expects two value lines (mode token, passband Hz). RED today:
            # server answers RPRT -4 (unknown command 'x').
            mode_line = await _exchange(writer, reader, b"x\n")
            assert mode_line.splitlines()[0] == b"USB"

            # -- rig_get_split_freq -> netrigctl "i" ------------------------
            # Expects one value line: the split TX frequency in Hz. RED today:
            # server answers RPRT -4 (unknown command 'i').
            assert await _exchange(writer, reader, b"i\n") == b"14080100\n"
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await srv.stop()

    # The radio itself must have ended in split (the S commands already do
    # this today; kept so the target behaviour of the whole sequence stays
    # pinned).
    assert radio._split is True
