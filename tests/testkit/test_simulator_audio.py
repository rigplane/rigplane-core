"""Wire-level tests for the simulated IC-7610 LAN audio port.

Bare asyncio UDP clients against a real ``MockIcomRadio`` — reusing the
``_UdpClient`` and packet builders of ``tests/testkit/test_simulator_faults.py``
— with client TX frames built by the production packet builder
(``src/rigplane/audio/lan_stream.py``: ``build_audio_packet``). The
recorded reference is ``tests/fixtures/ic7610/audio_stream.json`` and
``audio_rx_headers.jsonl``; the layout tests parse those files and derive
their expectations from them instead of restating the bytes.
"""

from __future__ import annotations

import asyncio
import json
import socket
import struct
import time
import wave
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from rigplane.audio import build_audio_packet

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.simulator import (
    _HEADER_SIZE,
    _PT_CONTROL,
    _PT_DATA,
    _PT_DISCONNECT,
)
from rigplane_testkit.icom.simulator_audio import (
    _AUDIO_BURST_PERIOD_S,
    AUDIO_HEADER_SIZE,
    TxAudioFormat,
)
from testkit.test_simulator_faults import (
    _CLIENT_ID,
    _HOST,
    _UdpClient,
    _ayt,
    _bare_conninfo,
    _conninfo,
    _conninfo_tx_format,
    _header,
    _login,
    _openclose,
    _ping,
)

_PT_I_AM_HERE = 0x04
_PT_PING = 0x07

_FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ic7610"


# ---------------------------------------------------------------------------
# Recorded-reference and wire helpers
# ---------------------------------------------------------------------------


def _recorded_burst_shape() -> tuple[tuple[int, int], ...]:
    """(packet length, BE u16 ident at 0x10) per position in one burst.

    Derived from ``audio_rx_headers.jsonl``: group the 50 recorded headers
    into bursts (inter-arrival under 5 ms) and keep the shape of the
    complete three-packet bursts.
    """
    rows = [
        json.loads(line)
        for line in (_FIXTURES / "audio_rx_headers.jsonl").read_text().splitlines()
        if line.strip()
    ]
    bursts: list[list[dict]] = []
    for row in rows:
        if bursts and row["t"] - bursts[-1][0]["t"] < 0.005:
            bursts[-1].append(row)
        else:
            bursts.append([row])
    shapes = {
        tuple(
            (r["len"], struct.unpack_from(">H", bytes.fromhex(r["header"]), 0x10)[0])
            for r in b
        )
        for b in bursts
        if len(b) == 3
    }
    assert len(shapes) == 1, f"recorded bursts disagree: {shapes}"
    return shapes.pop()


def _ayr() -> bytes:
    """Are You Ready — 0x10-byte type-0x06 discovery packet."""
    return bytes(_header(0x10, 0x06))


def _audio_openclose(seq: int = 0, *, open_stream: bool = True) -> bytes:
    """Audio-port OpenClose — the production packet shape
    (``runtime/_control_phase.py``: ``_send_open_close_on_transport``:
    LE u16 0x01C0 at 0x10, its own counter BE u16 at 0x13, 0x04/0x00 at
    0x15, sent tracked with the header seq at 0x06)."""
    pkt = _header(0x16, _PT_DATA, seq=seq)
    struct.pack_into("<H", pkt, 0x10, 0x01C0)
    struct.pack_into(">H", pkt, 0x13, seq)
    pkt[0x15] = 0x04 if open_stream else 0x00
    return bytes(pkt)


def _idle(seq: int) -> bytes:
    """Tracked 0x10-byte type-0 keepalive (the transport's idle loop)."""
    return bytes(_header(_HEADER_SIZE, _PT_DATA, seq=seq))


def _tx_audio(seq: int, inner_seq: int, payload: bytes, radio_id: int) -> bytes:
    """Client→radio audio DATA frame in the production layout: the packet
    ``src/rigplane/audio/lan_stream.py``: ``build_audio_packet`` builds
    (ident 0x0080 at 0x10, BE inner seq at 0x12, BE payload length at
    0x16), with the tracked header seq the production transport writes
    at 0x06 (``core/transport.py``: ``send_tracked``)."""
    pkt = bytearray(
        build_audio_packet(
            payload, sender_id=_CLIENT_ID, receiver_id=radio_id, send_seq=inner_seq
        )
    )
    struct.pack_into("<H", pkt, 0x06, seq)
    return bytes(pkt)


def _retransmit_single(missing: int) -> bytes:
    """Client retransmit request — ``core/transport.py``:
    ``_build_retransmit_requests`` single form (header seq = the number)."""
    return bytes(_header(_HEADER_SIZE, _PT_CONTROL, seq=missing))


def _token_remove() -> bytes:
    pkt = _header(0x40, _PT_DATA)
    pkt[0x15] = 0x01  # _TOKEN_REMOVE
    return bytes(pkt)


# Control and audio sender ids for the default-mode logout test: DIFFERENT,
# like the production client (``core/transport.py``: ``my_id`` derives each
# socket's id from its local port). The shared builders pin ``_CLIENT_ID``,
# so this test builds its packets with an explicit sender id.
_CTRL_ID = 0x11111111
_AUDIO_ID = 0x22222222
_FOREIGN_ID = 0x0BADF00D


def _header_as(sender_id: int, size: int, ptype: int, seq: int = 0) -> bytearray:
    """Wire header with an explicit sender id (``_header`` pins ``_CLIENT_ID``)."""
    pkt = bytearray(size)
    struct.pack_into("<I", pkt, 0x00, size)
    struct.pack_into("<H", pkt, 0x04, ptype)
    struct.pack_into("<H", pkt, 0x06, seq)
    struct.pack_into("<I", pkt, 0x08, sender_id)
    return pkt


def _login_as(sender_id: int) -> bytes:
    """Login packet from ``sender_id`` (token request, like ``_login``)."""
    pkt = _header_as(sender_id, 0x80, _PT_DATA)
    struct.pack_into("<H", pkt, 0x1A, 0x0001)  # token request
    return bytes(pkt)


def _audio_open_as(sender_id: int, seq: int = 0) -> bytes:
    """Audio-port OpenClose(open) from ``sender_id`` (like ``_audio_openclose``)."""
    pkt = _header_as(sender_id, 0x16, _PT_DATA, seq=seq)
    struct.pack_into("<H", pkt, 0x10, 0x01C0)
    struct.pack_into(">H", pkt, 0x13, seq)
    pkt[0x15] = 0x04
    return bytes(pkt)


def _token_remove_as(sender_id: int, token: int) -> bytes:
    """Token-remove from ``sender_id`` carrying ``token`` at 0x1C."""
    pkt = _header_as(sender_id, 0x40, _PT_DATA)
    pkt[0x15] = 0x01  # _TOKEN_REMOVE
    struct.pack_into("<I", pkt, 0x1C, token)
    return bytes(pkt)


def _disconnect_as(sender_id: int) -> bytes:
    """Control-port disconnect from ``sender_id``."""
    return bytes(_header_as(sender_id, _HEADER_SIZE, _PT_DISCONNECT))


async def _udp() -> _UdpClient:
    c = _UdpClient()
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: c, local_addr=(_HOST, 0)
    )
    return c


async def _open_audio(client: _UdpClient, server: MockIcomRadio) -> int:
    """AYT→IAH then the audio OpenClose(open); returns the radio id."""
    client.send(_ayt(), server.audio_port)
    iah = await client.recv()
    assert struct.unpack_from("<H", iah, 4)[0] == _PT_I_AM_HERE
    client.send(_audio_openclose(seq=0), server.audio_port)
    return struct.unpack_from("<I", iah, 8)[0]


async def _open_audio_as(
    client: _UdpClient, server: MockIcomRadio, sender_id: int
) -> int:
    """AYT→IAH then the audio OpenClose(open) from ``sender_id``."""
    client.send(_ayt(), server.audio_port)
    iah = await client.recv()
    assert struct.unpack_from("<H", iah, 4)[0] == _PT_I_AM_HERE
    client.send(_audio_open_as(sender_id), server.audio_port)
    return struct.unpack_from("<I", iah, 8)[0]


async def _collect_audio(
    client: _UdpClient, seconds: float
) -> list[tuple[float, bytes]]:
    """Collect audio-port packets for a window as (arrival, packet)."""
    out: list[tuple[float, bytes]] = []
    deadline = time.monotonic() + seconds
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return out
        try:
            out.append((time.monotonic(), await client.recv(timeout=remaining)))
        except asyncio.TimeoutError:
            return out


def _rx_frames(
    packets: list[tuple[float, bytes]],
) -> list[tuple[float, bytes]]:
    return [(t, p) for t, p in packets if len(p) > AUDIO_HEADER_SIZE]


def _complete_bursts(
    packets: list[tuple[float, bytes]], shape: tuple[tuple[int, int], ...]
):
    """Group RX frames into bursts by CONSECUTIVE tracked header seq
    (0x06), not arrival timing — robust to jitter and reordering on a
    loaded host. Keeps complete three-packet runs whose packet lengths
    match the recorded ``shape`` (a run broken by a lost packet does not
    match and is dropped); returns (arrival, packet) tuples in sequence
    order."""
    frames = sorted(
        _rx_frames(packets), key=lambda tp: struct.unpack_from("<H", tp[1], 6)[0]
    )
    lengths = tuple(n for n, _ in shape)
    bursts: list[list[tuple[float, bytes]]] = []
    run: list[tuple[float, bytes]] = []
    prev_seq: int | None = None
    for item in frames:
        seq = struct.unpack_from("<H", item[1], 6)[0]
        if run and prev_seq is not None and (seq - prev_seq) & 0xFFFF == 1:
            run.append(item)
        else:
            run = [item]
        prev_seq = seq
        if len(run) == 3:
            if tuple(len(p) for _, p in run) == lengths:
                bursts.append(run)
            run = []
    return bursts


async def _recv_type(client: _UdpClient, ptype: int, timeout: float = 2.0) -> bytes:
    """Receive the next packet of ``ptype``, skipping RX audio data."""
    deadline = time.monotonic() + timeout
    while True:
        pkt = await client.recv(timeout=max(0.05, deadline - time.monotonic()))
        if struct.unpack_from("<H", pkt, 4)[0] == ptype:
            return pkt


def _u16be(pkt: bytes, off: int) -> int:
    return struct.unpack_from(">H", pkt, off)[0]


async def _drain(
    client: _UdpClient, quiet_s: float = 0.03, budget_s: float = 2.0
) -> None:
    """Read everything the client socket holds until a quiet window passes.

    Packets already sent (in the socket buffer or in flight) before a stop
    must not leak into the silence assertion that follows. The budget only
    binds when the stream never stops (a regression): the drain gives up
    after ``budget_s`` and the silence assertion that follows fails fast on
    the still-flowing stream instead of draining forever.
    """
    start = time.monotonic()
    while time.monotonic() - start < budget_s:
        try:
            await client.recv(timeout=quiet_s)
        except TimeoutError:
            return


async def _await_rx_stop(server: MockIcomRadio, timeout: float = 2.0) -> int:
    """Wait until ``audio_rx_frames_sent`` stops advancing; return the value.

    A running stream advances the counter every burst (three packets per
    20 ms), so two equal samples a burst period apart mean the stream
    stopped. A mutation that keeps the stream running after the stop keeps
    the counter moving and times this out, failing the test.
    """
    deadline = time.monotonic() + timeout
    previous = server.audio_rx_frames_sent
    while True:
        await asyncio.sleep(0.03)
        current = server.audio_rx_frames_sent
        if current == previous:
            await asyncio.sleep(0.03)
            if server.audio_rx_frames_sent == current:
                return current
        previous = current
        assert time.monotonic() < deadline, (
            f"RX counter kept advancing past the stop: {current}"
        )


async def _await_client_seq(server: MockIcomRadio, timeout: float = 2.0) -> int:
    """Wait until the open has registered its tracked seq; return it.

    The client's open datagram is still in flight when the IAH reply to
    the earlier AYT arrives, so the first assertion after ``_open_audio``
    must wait for the server to process it rather than assume delivery.
    """
    deadline = time.monotonic() + timeout
    while server.audio_last_client_seq is None:
        await asyncio.sleep(0.01)
        assert time.monotonic() < deadline, "audio open never registered"
    return server.audio_last_client_seq


async def _await_client_seq_value(
    server: MockIcomRadio, value: int, timeout: float = 2.0
) -> None:
    """Wait until ``audio_last_client_seq`` reads ``value`` (delivery of
    the client's datagram takes a loop tick; a fixed sleep would hope)."""
    deadline = time.monotonic() + timeout
    while server.audio_last_client_seq != value:
        await asyncio.sleep(0.01)
        assert time.monotonic() < deadline, (
            f"client seq stuck at {server.audio_last_client_seq}, want {value}"
        )


async def _assert_rx_stopped(
    server: MockIcomRadio, client: _UdpClient, quiet_s: float = 0.1
) -> None:
    """Pin a stopped RX stream: counter frozen, socket drained, then quiet.

    After the stop trigger, waits for the simulator's own counter to stop
    advancing, reads everything the client socket still holds, then asserts
    no further packet arrives for ``quiet_s`` (at least two burst periods)
    and the counter did not move.
    """
    frozen = await _await_rx_stop(server)
    await _drain(client)
    await client.expect_silence(quiet_s)
    assert server.audio_rx_frames_sent == frozen


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server() -> AsyncGenerator[MockIcomRadio]:
    s = MockIcomRadio(single_owner=True, keepalive_hold_s=10.0, enable_audio=True)
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def capture_server() -> AsyncGenerator[MockIcomRadio]:
    """The audio listener with opt-in TX payload capture (capture_tx_audio)."""
    s = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=10.0,
        enable_audio=True,
        capture_tx_audio=True,
    )
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client() -> AsyncGenerator[_UdpClient]:
    c = await _udp()
    yield c
    c.close()


async def _claim(server: MockIcomRadio, ctrl: _UdpClient) -> None:
    """Login + conninfo from ``ctrl``: claims the single-owner session."""
    ctrl.send(_login(), server.control_port)
    await ctrl.recv()
    ctrl.send(_conninfo(), server.control_port)
    await ctrl.recv()


async def _declare_civ_port(server: MockIcomRadio, ctrl: _UdpClient, port: int) -> None:
    """Conninfo declaring a CI-V local port (BE u32 at 0x7C), like the
    production client (``core/auth.py``: ``build_conninfo_packet``)."""
    declared = bytearray(_conninfo())
    struct.pack_into(">I", declared, 0x7C, port)
    ctrl.send(bytes(declared), server.control_port)
    await ctrl.recv()


# ---------------------------------------------------------------------------
# Recorded RX layout and cadence
# ---------------------------------------------------------------------------


async def test_rx_stream_matches_recorded_layout_and_cadence(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """The RX stream replays the recorded burst shape and header layout;
    every expectation is derived from the two fixture files."""
    shape = _recorded_burst_shape()
    stats = json.loads((_FIXTURES / "audio_stream.json").read_text())
    assert set(int(n) for n in stats["rx_length_histogram"]) == {1136, 1388}
    assert sum(n - AUDIO_HEADER_SIZE for n, _ in shape) == 3840  # 20 ms stereo s16

    radio_id = await _open_audio(client, server)
    bursts = _complete_bursts(await _collect_audio(client, 0.5), shape)
    assert len(bursts) >= 8, f"only {len(bursts)} complete bursts in 0.5 s"

    for burst in bursts:
        assert [len(p) for _, p in burst] == [n for n, _ in shape]
        for (_, pkt), (_, ident) in zip(burst, shape):
            assert struct.unpack_from("<I", pkt, 0)[0] == len(pkt)  # len LE u32
            assert struct.unpack_from("<H", pkt, 0x04)[0] == _PT_DATA
            assert struct.unpack_from("<I", pkt, 8)[0] == radio_id  # sentid
            assert struct.unpack_from("<I", pkt, 0x0C)[0] == _CLIENT_ID
            assert _u16be(pkt, 0x10) == ident
            assert _u16be(pkt, 0x14) == 0  # recorded: always 0
            assert _u16be(pkt, 0x16) == len(pkt) - AUDIO_HEADER_SIZE

    # Both sequences step 1 per packet, wrap-continuous (recorded:
    # step 1 for 91407 of 91418 packets).
    flat = [p for burst in bursts for _, p in burst]
    seqs = [struct.unpack_from("<H", p, 6)[0] for p in flat]
    inner = [_u16be(p, 0x12) for p in flat]
    assert all((c - p) & 0xFFFF == 1 for p, c in zip(seqs, seqs[1:]))
    assert all((c - p) & 0xFFFF == 1 for p, c in zip(inner, inner[1:]))

    # Known synthetic payload: byte ramp seeded by the inner sequence.
    for p in flat:
        assert p[AUDIO_HEADER_SIZE] == _u16be(p, 0x12) & 0xFF
        assert p[AUDIO_HEADER_SIZE + 1] == (_u16be(p, 0x12) + 1) & 0xFF

    starts = [b[0][0] for b in bursts]  # bursts every ~20 ms (recorded)
    assert all(0.012 <= c - p <= 0.080 for p, c in zip(starts, starts[1:]))
    assert {sum(len(p) - AUDIO_HEADER_SIZE for _, p in b) for b in bursts} == {3840}
    assert server.audio_rx_frames_sent >= len(flat)


# ---------------------------------------------------------------------------
# Handshake: same transport handshake as the CI-V port
# ---------------------------------------------------------------------------


async def test_audio_port_handshake_and_no_stream_before_open(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """AYT→IAH, AreYouReady echo, ping reply — and no RX before the open."""
    client.send(_ayt(), server.audio_port)
    assert struct.unpack_from("<H", await client.recv(), 4)[0] == _PT_I_AM_HERE

    client.send(_ayr(), server.audio_port)
    assert struct.unpack_from("<H", await client.recv(), 4)[0] == 0x06

    client.send(_ping(), server.audio_port)
    reply = await client.recv()
    assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
    assert reply[0x10] == 0x01  # reply flag
    assert reply[0x11:0x15] == b"\x01\x02\x03\x04"  # timestamp echoed

    await client.expect_silence(0.25)  # nothing streams before the open
    assert server.audio_rx_frames_sent == 0


# ---------------------------------------------------------------------------
# RX keeps flowing across a CI-V-only soft reconnect (recorded, MOR-3361)
# ---------------------------------------------------------------------------


async def test_rx_keeps_flowing_across_civ_soft_reconnect(
    server: MockIcomRadio,
) -> None:
    """A CI-V-only reconnect (fresh conninfo + CI-V open from a new socket)
    never stops RX — the recorded outage shape (MOR-3361/MOR-3358): RX
    resumed gap-free the moment the link returned."""
    ctrl, civ, audio = await _udp(), await _udp(), await _udp()
    try:
        await _claim(server, ctrl)
        await _declare_civ_port(
            server, ctrl, civ.transport.get_extra_info("sockname")[1]
        )
        civ.send(_openclose(seq=0), server.civ_port)
        await civ.recv()  # stream-active marker

        await _open_audio(audio, server)
        task = asyncio.get_running_loop().create_task(_collect_audio(audio, 1.0))
        await asyncio.sleep(0.3)
        # The soft reconnect: a NEW CI-V socket, its port declared in a
        # fresh conninfo from the same control client, then its open.
        civ2 = await _udp()
        try:
            await _declare_civ_port(
                server, ctrl, civ2.transport.get_extra_info("sockname")[1]
            )
            civ2.send(_openclose(seq=0), server.civ_port)
            await civ2.recv()
        finally:
            civ2.close()
        window = await task

        bursts = _complete_bursts(window, _recorded_burst_shape())
        assert len(bursts) >= 30, f"{len(bursts)} bursts in 1.0 s"
        starts = [b[0][0] for b in bursts]
        worst = max(c - p for p, c in zip(starts, starts[1:]))
        assert worst <= 0.1, f"RX stream paused for {worst:.3f} s at reconnect"
        assert server.session_held  # the session outlived the reconnect
    finally:
        for c in (ctrl, civ, audio):
            c.close()


# ---------------------------------------------------------------------------
# TX sink: every client audio DATA frame is recorded
# ---------------------------------------------------------------------------


async def test_tx_sink_records_client_frames_and_numbering(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """DATA frames land in ``audio_tx_frames``; tracked numbering follows
    every type-0 packet (idle/OpenClose included), per session."""
    radio_id = await _open_audio(client, server)
    t0 = time.monotonic()
    client.send(_idle(seq=1), server.audio_port)
    client.send(_tx_audio(2, 0, bytes(1364), radio_id), server.audio_port)
    client.send(_tx_audio(3, 1, bytes(556), radio_id), server.audio_port)
    await asyncio.sleep(0.1)
    t1 = time.monotonic()

    assert server.audio_last_client_seq == 3
    assert len(server.audio_tx_frames) == 2  # idle/OpenClose are not frames
    first, second = server.audio_tx_frames
    assert (first.seq, first.inner_seq, first.payload_len) == (2, 0, 1364)
    assert (second.seq, second.inner_seq, second.payload_len) == (3, 1, 556)
    assert t0 <= first.received_at <= t1
    assert t0 <= second.received_at <= t1


async def test_no_retransmit_requests_from_radio_despite_client_gap(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Recorded: ZERO retransmit requests from the radio on the audio port
    however the client's numbering breaks; client requests are counted."""
    radio_id = await _open_audio(client, server)
    client.send(_tx_audio(5, 0, bytes(64), radio_id), server.audio_port)
    client.send(_tx_audio(9, 1, bytes(64), radio_id), server.audio_port)  # gap 6..8

    packets = await _collect_audio(client, 0.35)  # CI-V loop fires at 0.1 s
    # Every radio-originated packet in the window must be RX data: a
    # retransmit request (type 0x01, single or ranges) would fail this.
    assert {struct.unpack_from("<H", p, 4)[0] for _, p in packets} <= {_PT_DATA}
    assert server.audio_last_client_seq == 9

    client.send(_retransmit_single(7), server.audio_port)
    await asyncio.sleep(0.1)
    assert server.audio_retransmit_requests_received == 1
    # and the request itself draws no answer (nothing but RX data arrives)
    more = await _collect_audio(client, 0.15)
    assert {struct.unpack_from("<H", p, 4)[0] for _, p in more} <= {_PT_DATA}


# ---------------------------------------------------------------------------
# Events: audio_port_silence, audio_stream_drop
# ---------------------------------------------------------------------------


async def test_audio_port_silence_blacks_out_only_the_audio_port(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Silence window: no IAH, no ping reply, no RX; control unaffected."""
    ctrl = await _udp()
    try:
        await _open_audio(client, server)
        assert _rx_frames(await _collect_audio(client, 0.1))

        server.audio_port_silence(at=0.0, for_seconds=0.5)
        await asyncio.sleep(0.02)  # the window arms on the next loop tick
        await _drain(client)  # pre-window packets still arriving
        client.send(_ayt(), server.audio_port)
        await client.expect_silence(0.12)
        client.send(_ping(), server.audio_port)
        await client.expect_silence(0.05)

        ctrl.send(_ayt(), server.control_port)  # control port unaffected
        assert struct.unpack_from("<H", await ctrl.recv(), 4)[0] == _PT_I_AM_HERE

        await asyncio.sleep(0.35)  # window expired
        client.send(_ayt(), server.audio_port)
        await _recv_type(client, _PT_I_AM_HERE)

        frames = _rx_frames(await _collect_audio(client, 0.2))
        assert frames  # RX resumed with the window
        # Both sequences keep stepping through the silence while nothing is
        # sent, modelling the recorded outage where the client saw lost
        # packets (`audio_rx_frames_sent` counts only sent packets).
        assert _u16be(frames[0][1], 0x12) > 24
    finally:
        ctrl.close()


async def test_audio_stream_drop_stops_rx_and_keeps_the_session(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """audio_stream_drop(): RX stops, session kept, handshake alive; a new
    open restarts the stream with continued numbering."""
    ctrl = await _udp()
    try:
        await _claim(server, ctrl)
        await _open_audio(client, server)
        first = _rx_frames(await _collect_audio(client, 0.1))
        assert first

        server.audio_stream_drop()
        await _assert_rx_stopped(server, client)
        assert server.session_held
        sent = server.audio_rx_frames_sent
        # The expected resume number comes from the simulator's own state:
        # packets sent between the last collected frame and the stop are
        # drained by _assert_rx_stopped, so the last collected frame is
        # stale — but the stream counter is frozen with the stream and
        # holds the next number the resumed stream will emit.
        expected_inner = server._audio_rx_inner_seq

        client.send(_ayt(), server.audio_port)  # the port still answers
        assert struct.unpack_from("<H", await client.recv(), 4)[0] == _PT_I_AM_HERE

        client.send(_audio_openclose(seq=1), server.audio_port)
        resumed = _rx_frames(await _collect_audio(client, 0.15))
        assert resumed
        assert server.audio_rx_frames_sent > sent
        # The stream epoch is the radio session: numbering continues.
        assert _u16be(resumed[0][1], 0x12) == expected_inner
    finally:
        ctrl.close()


# ---------------------------------------------------------------------------
# Session end stops RX (token remove / drop_session / reboot)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "trigger",
    ["token_remove", "drop_session", "reboot"],
)
async def test_session_end_stops_rx(
    server: MockIcomRadio, client: _UdpClient, trigger: str
) -> None:
    """Every session-end path (token-remove/logout, drop_session, reboot)
    stops the RX stream and resets the per-session client numbering."""
    ctrl = await _udp()
    try:
        await _claim(server, ctrl)
        radio_id = await _open_audio(client, server)
        client.send(_tx_audio(1, 0, bytes(64), radio_id), server.audio_port)
        await asyncio.sleep(0.1)
        assert server.audio_last_client_seq == 1
        assert _rx_frames(await _collect_audio(client, 0.08))

        if trigger == "token_remove":
            ctrl.send(_token_remove(), server.control_port)
        elif trigger == "drop_session":
            server.drop_session(at=0.0, notice=False)
        else:
            server.reboot(at=0.0, down_for=0.3)

        await _assert_rx_stopped(server, client)
        assert server.audio_last_client_seq is None  # per-session numbering
    finally:
        ctrl.close()


# ---------------------------------------------------------------------------
# TX sink scope: production frame layout, recording stops with the stream
# ---------------------------------------------------------------------------


async def test_tx_sink_records_production_frames_until_the_session_ends(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """A raw client that has opened the audio stream sends DATA frames in
    the production TX layout (``build_audio_packet``); ``audio_tx_frames``
    records header seq, inner seq, payload length and arrival time — and
    nothing after the session ends (token remove)."""
    ctrl = await _udp()
    try:
        await _claim(server, ctrl)
        radio_id = await _open_audio(client, server)
        t0 = time.monotonic()
        client.send(_tx_audio(2, 0, bytes(1364), radio_id), server.audio_port)
        client.send(_tx_audio(3, 1, bytes(556), radio_id), server.audio_port)
        await asyncio.sleep(0.1)
        t1 = time.monotonic()

        assert len(server.audio_tx_frames) == 2
        first, second = server.audio_tx_frames
        assert (first.seq, first.inner_seq, first.payload_len) == (2, 0, 1364)
        assert (second.seq, second.inner_seq, second.payload_len) == (3, 1, 556)
        assert all(t0 <= f.received_at <= t1 for f in (first, second))
        assert server.audio_last_client_seq == 3

        # Session end (token remove): frames sent afterwards are recorded
        # nowhere — not in the sink, not in the per-stream numbering.
        ctrl.send(_token_remove(), server.control_port)
        await asyncio.sleep(0.05)
        client.send(_tx_audio(4, 2, bytes(64), radio_id), server.audio_port)
        client.send(_idle(seq=5), server.audio_port)
        await asyncio.sleep(0.1)
        assert server.audio_tx_frames == [first, second]
        assert server.audio_last_client_seq is None
    finally:
        ctrl.close()


# ---------------------------------------------------------------------------
# TX sink capture: payload bytes and PTT state per frame (capture_tx_audio)
# ---------------------------------------------------------------------------


async def test_tx_sink_captures_payload_and_ptt_per_frame(
    capture_server: MockIcomRadio, client: _UdpClient
) -> None:
    """With capture_tx_audio=True each sink frame carries the payload bytes
    after the 0x18 header (byte-exact) and the simulator's PTT state at
    arrival; ``audio_tx_pcm`` concatenates payloads in arrival order."""
    radio_id = await _open_audio(client, capture_server)
    tone = bytes(range(256)) * 2
    client.send(_tx_audio(1, 0, tone, radio_id), capture_server.audio_port)
    await _await_client_seq_value(capture_server, 1)  # the frame was processed
    capture_server._set_simulated_ptt(True, wire_set=False)  # noqa: SLF001
    client.send(_tx_audio(2, 1, b"\x01\x02", radio_id), capture_server.audio_port)
    await _await_client_seq_value(capture_server, 2)
    capture_server._set_simulated_ptt(False, wire_set=False)  # noqa: SLF001
    client.send(_tx_audio(3, 2, b"\x03\x04", radio_id), capture_server.audio_port)
    await _await_client_seq_value(capture_server, 3)

    frames = capture_server.audio_tx_frames
    assert len(frames) == 3
    assert [f.payload for f in frames] == [tone, b"\x01\x02", b"\x03\x04"]
    assert [f.ptt for f in frames] == [False, True, False]
    assert capture_server.audio_tx_pcm() == tone + b"\x01\x02\x03\x04"
    assert capture_server.audio_tx_pcm(since=1) == b"\x01\x02\x03\x04"


async def test_write_tx_wav_roundtrip(
    capture_server: MockIcomRadio, client: _UdpClient, tmp_path: Path
) -> None:
    """write_tx_wav writes a RIFF/WAV (16-bit PCM) that reads back via the
    stdlib wave module to exactly the captured PCM."""
    radio_id = await _open_audio(client, capture_server)
    pcm = bytes(range(256)) * 8  # 1024 samples of a byte ramp, s16le-shaped
    half = len(pcm) // 2
    client.send(_tx_audio(1, 0, pcm[:half], radio_id), capture_server.audio_port)
    client.send(_tx_audio(2, 1, pcm[half:], radio_id), capture_server.audio_port)
    await _await_client_seq_value(capture_server, 2)  # both frames processed

    path = tmp_path / "tx.wav"
    capture_server.write_tx_wav(path, sample_rate=48000)
    with wave.open(str(path), "rb") as wav:
        assert (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) == (
            1,
            2,
            48000,
        )
        assert wav.readframes(wav.getnframes()) == pcm


async def test_tx_capture_off_keeps_payload_empty(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Default (capture_tx_audio=False): the sink still records metadata,
    but the payload is not retained — b"" per frame, empty audio_tx_pcm."""
    radio_id = await _open_audio(client, server)
    client.send(_tx_audio(1, 0, bytes(64), radio_id), server.audio_port)
    await _await_client_seq_value(server, 1)  # the frame was processed
    assert len(server.audio_tx_frames) == 1
    assert server.audio_tx_frames[0].payload == b""
    assert server.audio_tx_frames[0].payload_len == 64  # metadata unchanged
    assert server.audio_tx_pcm() == b""


# ---------------------------------------------------------------------------
# Learned TX format: the sink records the conninfo-declared TX format
# ---------------------------------------------------------------------------


async def test_tx_sink_learns_the_accepted_conninfo_tx_format(
    server: MockIcomRadio,
) -> None:
    """Only the fresh login's OWN accepted conninfo teaches the TX format
    (production conninfo layout, ``core/auth.py``: tx codec at 0x73, BE u32
    tx sample rate at 0x78): the production-faithful default learns 48 kHz
    PCM16, a bare conninfo (rate 0, never sent by a real client) teaches
    nothing, and a renegotiated conninfo replaces the learned format."""
    ctrl = await _udp()
    try:
        ctrl.send(_login(), server.control_port)
        await ctrl.recv()
        assert server.audio_tx_format is None

        ctrl.send(_conninfo(), server.control_port)
        await ctrl.recv()
        assert server.audio_tx_format == TxAudioFormat(codec=0x04, sample_rate=48000)

        ctrl.send(_bare_conninfo(), server.control_port)
        await ctrl.recv()
        assert server.audio_tx_format == TxAudioFormat(codec=0x04, sample_rate=48000)

        ctrl.send(
            _conninfo_tx_format(tx_codec=0x04, tx_sample_rate=16000),
            server.control_port,
        )
        await ctrl.recv()
        assert server.audio_tx_format == TxAudioFormat(codec=0x04, sample_rate=16000)
    finally:
        ctrl.close()


async def test_tx_capture_format_marks_a_mixed_capture() -> None:
    """Sink frames snapshot the format learned at their arrival;
    ``tx_capture_format`` returns the single distinct learned format
    across the slice, or None when the slice carries none or spans a
    format change (a new session renegotiating its conninfo mid-capture)."""
    server = MockIcomRadio(enable_audio=True)
    await server.start()
    ctrl_a, ctrl_b, audio = await _udp(), await _udp(), await _udp()
    try:
        ctrl_a.send(_login(), server.control_port)
        await ctrl_a.recv()
        ctrl_a.send(_conninfo(), server.control_port)
        await ctrl_a.recv()
        radio_id = await _open_audio(audio, server)
        audio.send(_tx_audio(1, 0, bytes(64), radio_id), server.audio_port)
        await _await_client_seq_value(server, 1)
        learned_48k = TxAudioFormat(codec=0x04, sample_rate=48000)
        assert server.audio_tx_frames[0].tx_format == learned_48k
        assert server.tx_capture_format() == learned_48k

        # A new session: new login, new conninfo at 16 kHz.
        ctrl_b.send(_login(), server.control_port)
        await ctrl_b.recv()
        ctrl_b.send(
            _conninfo_tx_format(tx_codec=0x04, tx_sample_rate=16000),
            server.control_port,
        )
        await ctrl_b.recv()
        audio.send(_tx_audio(2, 1, bytes(64), radio_id), server.audio_port)
        await _await_client_seq_value(server, 2)
        first, second = server.audio_tx_frames
        learned_16k = TxAudioFormat(codec=0x04, sample_rate=16000)
        assert first.tx_format == learned_48k
        assert second.tx_format == learned_16k
        assert server.tx_capture_format() is None  # mixed: no single label
        assert server.tx_capture_format(since=1) == learned_16k
    finally:
        for c in (ctrl_a, ctrl_b, audio):
            c.close()
        await server.stop()


async def test_tx_capture_format_without_frames_or_format_is_none(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """No frames (nothing captured) and frames with no learned format
    (a bare conninfo, rate 0) both give no capture format."""
    assert server.tx_capture_format() is None
    ctrl = await _udp()
    try:
        ctrl.send(_login(), server.control_port)
        await ctrl.recv()
        ctrl.send(_bare_conninfo(), server.control_port)
        await ctrl.recv()
        radio_id = await _open_audio(client, server)
        client.send(_tx_audio(1, 0, bytes(64), radio_id), server.audio_port)
        await _await_client_seq_value(server, 1)
        assert server.audio_tx_frames[0].tx_format is None
        assert server.tx_capture_format() is None
    finally:
        ctrl.close()


# ---------------------------------------------------------------------------
# Opt-in listener: default off, nothing answers
# ---------------------------------------------------------------------------


async def test_default_audio_port_answers_nothing() -> None:
    """Default (listener off): the status reply still advertises an audio
    port, but nothing answers there — AYT and ping draw silence."""
    server = MockIcomRadio()
    await server.start()
    ctrl = await _udp()
    try:
        assert server.audio_port == 0  # no listener bound
        ctrl.send(_login(), server.control_port)
        await ctrl.recv()
        ctrl.send(_conninfo(), server.control_port)
        status = await ctrl.recv()
        advertised = struct.unpack_from(">H", status, 0x46)[0]
        assert advertised != 0  # still advertised, as before
        assert advertised not in (server.control_port, server.civ_port)
        adjacent = server.civ_port + 1 if server.civ_port < 65535 else 65534
        if adjacent != server.control_port:
            assert advertised == adjacent

        probe = await _udp()
        try:
            probe.send(_ayt(), advertised)
            await probe.expect_silence(0.2)
            probe.send(_ping(), advertised)
            await probe.expect_silence(0.1)
        finally:
            probe.close()
    finally:
        ctrl.close()
        await server.stop()


@pytest.mark.parametrize("endpoint", ["control_port", "civ_port"])
async def test_disabled_audio_advertisement_avoids_live_endpoint(
    endpoint: str,
) -> None:
    """The status CIV-port override forces the old dummy onto a real listener."""
    server = MockIcomRadio()
    await server.start()
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind((_HOST, 0))
    probe.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        live_port = getattr(server, endpoint)
        assert server.audio_port == 0
        assert server._audio_udp is None
        await loop.sock_sendto(probe, _ayt(), (_HOST, live_port))
        control_reply, control_addr = await asyncio.wait_for(
            loop.sock_recvfrom(probe, 1024), 1.0
        )
        assert control_addr == (_HOST, live_port)
        assert struct.unpack_from("<H", control_reply, 4)[0] == _PT_I_AM_HERE

        status = server._status_response(_CLIENT_ID, civ_port=live_port - 1)
        advertised = struct.unpack_from(">H", status, 0x46)[0]
        for packet in (_ayt(), _ping()):
            await loop.sock_sendto(probe, packet, (_HOST, advertised))
            try:
                response = await asyncio.wait_for(loop.sock_recvfrom(probe, 1024), 0.2)
            except TimeoutError:
                response = None
            assert response is None, (
                f"expected silence, received {response}; "
                f"control reply={control_reply.hex()} from {control_addr}"
            )
        assert advertised not in (server.control_port, server.civ_port)
    finally:
        probe.close()
        await server.stop()
    assert server._ctrl_udp is None
    assert server._civ_udp is None
    assert server._audio_udp is None


async def test_disabled_audio_preserves_explicit_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sockets = [socket.socket(socket.AF_INET, socket.SOCK_DGRAM) for _ in range(2)]
    for sock in sockets:
        sock.bind((_HOST, 0))
        sock.setblocking(False)
    ctrl_port, civ_port = [sock.getsockname()[1] for sock in sockets]
    loop = asyncio.get_running_loop()
    create_endpoint = loop.create_datagram_endpoint

    async def reserved_endpoint(factory, *, local_addr):
        sock = sockets[0] if local_addr[1] == ctrl_port else sockets[1]
        assert local_addr == sock.getsockname()
        return await create_endpoint(factory, sock=sock)

    monkeypatch.setattr(loop, "create_datagram_endpoint", reserved_endpoint)
    server = MockIcomRadio(port=ctrl_port, civ_port=civ_port)
    try:
        await server.start()
        assert (server.control_port, server.civ_port) == (ctrl_port, civ_port)
        assert server.audio_port == 0
        assert server._audio_udp is None
        status = server._status_response(_CLIENT_ID)
        assert struct.unpack_from(">H", status, 0x42)[0] == civ_port
        assert struct.unpack_from(">H", status, 0x46)[0] not in (ctrl_port, civ_port)
    finally:
        await server.stop()
        await asyncio.sleep(0)
        for sock in sockets:
            sock.close()
    assert server._ctrl_udp is None
    assert server._civ_udp is None
    assert server._audio_udp is None


@pytest.mark.parametrize(
    ("ctrl_port", "civ_port", "advertised"),
    [(65534, 65535, 1), (65535, 65534, 1), (50000, 50001, 50002)],
)
def test_disabled_audio_advertisement_port_boundaries(
    ctrl_port: int, civ_port: int, advertised: int
) -> None:
    server = MockIcomRadio(port=ctrl_port, civ_port=civ_port)
    server._actual_ctrl_port = ctrl_port
    server._actual_civ_port = civ_port
    status = server._status_response(_CLIENT_ID)
    assert struct.unpack_from(">H", status, 0x42)[0] == civ_port
    assert struct.unpack_from(">H", status, 0x46)[0] == advertised
    not_ready = server._status_response(_CLIENT_ID, civ_port=0)
    assert struct.unpack_from(">H", not_ready, 0x46)[0] == 0


# ---------------------------------------------------------------------------
# Tracked client numbering: open, idle, re-open, retarget
# ---------------------------------------------------------------------------


async def test_reopen_records_its_tracked_seq(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """A re-open records its own tracked seq (old counter + 1): the open is
    the first tracked packet of the (re)started stream, like the CI-V port."""
    radio_id = await _open_audio(client, server)
    assert await _await_client_seq(server) == 0
    client.send(_tx_audio(1, 0, bytes(64), radio_id), server.audio_port)
    client.send(_tx_audio(2, 1, bytes(64), radio_id), server.audio_port)
    await _await_client_seq_value(server, 2)

    client.send(_audio_openclose(seq=3), server.audio_port)  # re-open: 2 + 1
    await _await_client_seq_value(server, 3)


async def test_idle_advances_client_seq(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Tracked idle keepalives advance ``audio_last_client_seq`` while the
    stream lives."""
    await _open_audio(client, server)
    assert await _await_client_seq(server) == 0
    client.send(_idle(seq=4), server.audio_port)
    await _await_client_seq_value(server, 4)


async def test_reopen_from_new_address_retargets_rx(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """A re-open from a new client address retargets the RX stream there."""
    await _open_audio(client, server)
    assert _rx_frames(await _collect_audio(client, 0.1))

    moved = await _udp()
    try:
        moved.send(_ayt(), server.audio_port)
        iah = await moved.recv()
        assert struct.unpack_from("<H", iah, 4)[0] == _PT_I_AM_HERE
        moved.send(_audio_openclose(seq=1), server.audio_port)
        assert _rx_frames(await _collect_audio(moved, 0.2))
    finally:
        moved.close()


async def test_rx_rate_matches_recorded_fixture(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """The simulator's burst cadence matches the recorded fixture and the
    stream stays live: the burst period constant equals ``3 /
    rx_packets_per_s`` from ``tests/fixtures/ic7610/audio_stream.json``
    (three packets per burst, recorded) within 1 % — a deterministic pin
    that rejects both a 16 ms and a 30 ms cadence mutation — while the
    wall-clock side keeps only a loose liveness floor (measured rate over
    >= 1 s is >= 60 % of recorded, catching "RX stopped / stalled" without
    failing a correct run under load)."""
    recorded = float(
        json.loads((_FIXTURES / "audio_stream.json").read_text())["rx_packets_per_s"]
    )
    assert _AUDIO_BURST_PERIOD_S == pytest.approx(3 / recorded, rel=0.01), (
        f"burst period {_AUDIO_BURST_PERIOD_S}s vs recorded {3 / recorded:.6f}s"
    )
    await _open_audio(client, server)
    frames = _rx_frames(await _collect_audio(client, 1.2))
    assert len(frames) >= 100, f"only {len(frames)} RX frames in 1.2 s"
    span = frames[-1][0] - frames[0][0]
    assert span >= 1.0, f"RX span only {span:.3f} s"
    rate = (len(frames) - 1) / span
    assert rate >= 0.6 * recorded, f"{rate:.1f}/s vs recorded {recorded}/s"


# ---------------------------------------------------------------------------
# Default mode: the audio opener's own logout stops RX
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("trigger", ["token_remove", "disconnect"])
async def test_own_logout_stops_rx_in_default_mode(trigger: str) -> None:
    """Without an owned session the audio opener's own logout still ends the
    RX stream even though its control and audio sockets carry DIFFERENT
    sender ids (``_CTRL_ID`` vs ``_AUDIO_ID``, like the production client —
    ``core/transport.py``: ``my_id``): a token-remove retires only with the
    live token and ends the stream with it, a disconnect ends it when it
    comes from the last login. A stale-token remove and a foreign
    disconnect change nothing."""
    server = MockIcomRadio(enable_audio=True)
    await server.start()
    audio = await _udp()
    ctrl = await _udp()
    try:
        ctrl.send(_login_as(_CTRL_ID), server.control_port)
        await ctrl.recv()
        live = server.token

        await _open_audio_as(audio, server, _AUDIO_ID)
        assert _rx_frames(await _collect_audio(audio, 0.1))

        ctrl.send(
            _token_remove_as(_CTRL_ID, (live + 1) & 0xFFFFFFFF),
            server.control_port,
        )
        await asyncio.sleep(0.1)
        assert _rx_frames(await _collect_audio(audio, 0.08))  # stale: flowing
        assert server.token == live  # nothing retired

        ctrl.send(_disconnect_as(_FOREIGN_ID), server.control_port)
        await asyncio.sleep(0.1)
        assert _rx_frames(await _collect_audio(audio, 0.08))  # foreign: flowing

        if trigger == "token_remove":
            ctrl.send(_token_remove_as(_CTRL_ID, live), server.control_port)
        else:
            ctrl.send(_disconnect_as(_CTRL_ID), server.control_port)
        await _assert_rx_stopped(server, audio)
        assert server.audio_last_client_seq is None
        if trigger == "token_remove":
            assert server.token != live  # the live epoch retired
    finally:
        audio.close()
        ctrl.close()
        await server.stop()


# ---------------------------------------------------------------------------
# Silence/start/stop details: close, delayed start, status port, CI-V alive
# ---------------------------------------------------------------------------


async def test_audio_openclose_close_stops_rx(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """Audio-port OpenClose(close) stops the RX stream."""
    await _open_audio(client, server)
    assert _rx_frames(await _collect_audio(client, 0.1))

    client.send(_audio_openclose(seq=1, open_stream=False), server.audio_port)
    await _assert_rx_stopped(server, client)


async def test_audio_port_silence_honours_delayed_start(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """``audio_port_silence(at=...)`` with a non-zero ``at``: the port
    still answers and streams during the delay, goes quiet for the window,
    and answers again after."""
    await _open_audio(client, server)
    assert _rx_frames(await _collect_audio(client, 0.1))

    t0 = time.monotonic()
    server.audio_port_silence(at=0.3, for_seconds=0.6)
    # The delay is honoured, not immediate: the port still answers and the
    # stream still flows during it. A lone pre-window reply would also win
    # a race against a broken immediate arming, so the stream's stop time
    # is the pin: the last RX arrival lands just after `at`, not at the
    # arming and not at the collection's end.
    client.send(_ayt(), server.audio_port)
    await _recv_type(client, _PT_I_AM_HERE)
    pre_window = _rx_frames(await _collect_audio(client, 0.6))
    assert pre_window, "no RX at all after arming a delayed silence"
    last = pre_window[-1][0] - t0
    # The lower bound is what catches "`at` ignored" (an immediate arming
    # stops the stream at ~0); the upper bound is generous on purpose — CI
    # load can delay the loop, and the window itself ends at 0.9 s.
    assert 0.15 <= last <= 2.0, f"stream stopped at {last:.3f} s into the delay"

    await _drain(client)  # pre-window packets still arriving
    client.send(_ayt(), server.audio_port)
    await client.expect_silence(0.1)  # during the window: nothing

    await asyncio.sleep(0.3)  # window expired
    client.send(_ayt(), server.audio_port)
    await _recv_type(client, _PT_I_AM_HERE)  # answers again


async def test_status_audio_port_equals_bound_port(
    server: MockIcomRadio,
) -> None:
    """The status reply's audio port at 0x46 is the bound listener."""
    ctrl = await _udp()
    try:
        ctrl.send(_login(), server.control_port)
        await ctrl.recv()
        ctrl.send(_conninfo(), server.control_port)
        status = await ctrl.recv()
        assert struct.unpack_from(">H", status, 0x46)[0] == server.audio_port != 0
    finally:
        ctrl.close()


async def test_civ_answers_during_audio_silence(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """``audio_port_silence`` blacks out only the audio port: the CI-V port
    still answers discovery and pings."""
    civ = await _udp()
    try:
        await _open_audio(client, server)
        assert _rx_frames(await _collect_audio(client, 0.08))

        server.audio_port_silence(at=0.0, for_seconds=0.4)
        await asyncio.sleep(0.05)  # window armed
        civ.send(_ayt(), server.civ_port)
        assert struct.unpack_from("<H", await civ.recv(), 4)[0] == _PT_I_AM_HERE
        civ.send(_ping(), server.civ_port)
        reply = await civ.recv()
        assert struct.unpack_from("<H", reply, 4)[0] == _PT_PING
        assert reply[0x10] == 0x01  # reply flag
        assert reply[0x11:0x15] == b"\x01\x02\x03\x04"  # timestamp echoed
    finally:
        civ.close()


async def test_rx_counter_counts_only_sent_packets(
    server: MockIcomRadio, client: _UdpClient
) -> None:
    """``audio_rx_frames_sent`` counts sends, not suppressed emissions: it is
    frozen through a silence window (both sequences still step, so the
    client sees the gap) and advances again after."""
    await _open_audio(client, server)
    assert _rx_frames(await _collect_audio(client, 0.1))

    server.audio_port_silence(at=0.0, for_seconds=0.4)
    await asyncio.sleep(0.05)  # window armed
    frozen = server.audio_rx_frames_sent
    await asyncio.sleep(0.2)  # mid-window: emissions suppressed
    assert server.audio_rx_frames_sent == frozen

    await asyncio.sleep(0.25)  # window expired
    assert _rx_frames(await _collect_audio(client, 0.2))
    assert server.audio_rx_frames_sent > frozen

    # Reboot characterization: the session end stops the stream before the
    # dark window, so nothing is emitted there to (mis)count either.
    server.reboot(at=0.0, down_for=0.3)
    await _assert_rx_stopped(server, client)
