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
import struct
import time
from collections.abc import AsyncGenerator
from pathlib import Path

import pytest

from rigplane.audio import build_audio_packet

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.simulator import _HEADER_SIZE, _PT_CONTROL, _PT_DATA
from rigplane_testkit.icom.simulator_audio import AUDIO_HEADER_SIZE
from testkit.test_simulator_faults import (
    _CLIENT_ID,
    _HOST,
    _UdpClient,
    _ayt,
    _conninfo,
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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server() -> AsyncGenerator[MockIcomRadio]:
    s = MockIcomRadio(single_owner=True, keepalive_hold_s=10.0)
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

        server.audio_port_silence(at=0.0, for_seconds=0.3)
        await asyncio.sleep(0.02)  # the window arms on the next loop tick
        client.send(_ayt(), server.audio_port)
        await client.expect_silence(0.12)
        client.send(_ping(), server.audio_port)
        await client.expect_silence(0.05)

        ctrl.send(_ayt(), server.control_port)  # control port unaffected
        assert struct.unpack_from("<H", await ctrl.recv(), 4)[0] == _PT_I_AM_HERE

        await asyncio.sleep(0.25)  # window expired
        client.send(_ayt(), server.audio_port)
        await _recv_type(client, _PT_I_AM_HERE)

        frames = _rx_frames(await _collect_audio(client, 0.2))
        assert frames  # RX resumed with the window
        # Numbering advanced through the silence (the stream kept counting,
        # modelling the recorded outage where the client saw lost packets).
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
        last_inner = _u16be(first[-1][1], 0x12)

        server.audio_stream_drop()
        await client.expect_silence(0.15)
        assert server.session_held
        sent = server.audio_rx_frames_sent

        client.send(_ayt(), server.audio_port)  # the port still answers
        assert struct.unpack_from("<H", await client.recv(), 4)[0] == _PT_I_AM_HERE

        client.send(_audio_openclose(seq=1), server.audio_port)
        resumed = _rx_frames(await _collect_audio(client, 0.15))
        assert resumed
        assert server.audio_rx_frames_sent > sent
        # The stream epoch is the radio session: numbering continues.
        assert _u16be(resumed[0][1], 0x12) == ((last_inner + 1) & 0xFFFF)
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
        await asyncio.sleep(0.05)

        await client.expect_silence(0.2)
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
