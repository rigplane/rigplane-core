"""Wire-level tests for the simulated IC-7610 LAN audio port.

Bare asyncio UDP clients against a real ``MockIcomRadio`` — reusing the
``_UdpClient`` and packet builders of ``tests/testkit/test_simulator_faults.py``
— plus one production-client test driving ``IcomRadio``, ``AudioBridge``
and ``FakeAudioBackend`` end to end (the PTT-gated TX path). The recorded
reference is ``tests/fixtures/ic7610/audio_stream.json`` and
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

from _perf_helpers import fast_connect
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bridge import AudioBridge
from rigplane.radio import IcomRadio

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


def _tx_audio(seq: int, inner_seq: int, payload: bytes) -> bytes:
    """Client→radio audio DATA frame — ``audio/lan_stream.py``:
    ``build_audio_packet`` (LE ident 0x0080 at 0x10, BE inner seq at
    0x12, BE payload length at 0x16), sent tracked."""
    total = AUDIO_HEADER_SIZE + len(payload)
    pkt = _header(total, _PT_DATA, seq=seq)
    struct.pack_into("<H", pkt, 0x10, 0x0080)
    struct.pack_into(">H", pkt, 0x12, inner_seq)
    struct.pack_into(">H", pkt, 0x16, len(payload))
    pkt[AUDIO_HEADER_SIZE:] = payload
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


def _complete_bursts(packets: list[tuple[float, bytes]]):
    """Group RX frames into bursts (inter-arrival under 5 ms); complete
    three-packet bursts only, as (arrival, packet) tuples."""
    bursts: list[list[tuple[float, bytes]]] = []
    for item in _rx_frames(packets):
        if bursts and item[0] - bursts[-1][0][0] < 0.005:
            bursts[-1].append(item)
        else:
            bursts.append([item])
    return [b for b in bursts if len(b) == 3]


async def _recv_type(client: _UdpClient, ptype: int, timeout: float = 2.0) -> bytes:
    """Receive the next packet of ``ptype``, skipping RX audio data."""
    deadline = time.monotonic() + timeout
    while True:
        pkt = await client.recv(timeout=max(0.05, deadline - time.monotonic()))
        if struct.unpack_from("<H", pkt, 4)[0] == ptype:
            return pkt


def _u16be(pkt: bytes, off: int) -> int:
    return struct.unpack_from(">H", pkt, off)[0]


async def _wait_for(predicate, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"condition not met within {timeout} s")


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


async def _declare_civ_port(
    server: MockIcomRadio, ctrl: _UdpClient, port: int
) -> None:
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
    bursts = _complete_bursts(await _collect_audio(client, 0.5))
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
        await _declare_civ_port(server, ctrl, civ.transport.get_extra_info("sockname")[1])
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

        bursts = _complete_bursts(window)
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
    await _open_audio(client, server)
    t0 = time.monotonic()
    client.send(_idle(seq=1), server.audio_port)
    client.send(_tx_audio(2, 0, bytes(1364)), server.audio_port)
    client.send(_tx_audio(3, 1, bytes(556)), server.audio_port)
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
    await _open_audio(client, server)
    client.send(_tx_audio(5, 0, bytes(64)), server.audio_port)
    client.send(_tx_audio(9, 1, bytes(64)), server.audio_port)  # gap 6..8

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
        await _open_audio(client, server)
        client.send(_tx_audio(1, 0, bytes(64)), server.audio_port)
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
# Production client: PTT-gated TX through AudioBridge + FakeAudioBackend
# ---------------------------------------------------------------------------


async def test_ptt_gates_tx_frames_to_the_sink_via_production_client() -> None:
    """MOR-3256 shape: with PTT on, mic frames reach the radio's audio port;
    with PTT off none do — the production client drives everything.

    ``IcomRadio`` connects for real; ``AudioBridge`` captures from a
    ``FakeAudioBackend`` device (the CLAUDE.md audio-test rule: no one-off
    audio mocks) and its TX gate is the production PTT observation
    (``read_transmit_state`` — the CI-V 0x1C read decoded by the runtime).
    """
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=30.0)
    await server.start()
    radio = IcomRadio(
        host=_HOST,
        port=server.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
    )
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(0),
                name="RigPlane Virtual Cable",
                input_channels=1,
                output_channels=2,
            )
        ]
    )

    async def ptt_gate() -> bool:
        return (await radio.read_transmit_state()).value is True

    with fast_connect():
        await radio.connect()
    bridge = AudioBridge(
        radio,
        device_name="RigPlane Virtual Cable",
        backend=backend,
        tx_gate=ptt_gate,
    )
    try:
        await bridge.start()
        capture = backend.rx_streams[-1]
        # 20 ms of 48 kHz mono s16 with a strong ramp (past the bridge's
        # silence suppression): 960 samples × 2 bytes = 1920.
        frame = b"".join(struct.pack("<h", 3000 + (i % 800)) for i in range(960))

        # PTT off: the gate suppresses every captured frame.
        for _ in range(3):
            capture.inject_frame(frame)
        await asyncio.sleep(0.4)
        assert server.audio_tx_frames == []

        # PTT on (production fire-and-forget SET, observed via the GET).
        await radio.set_ptt(True)
        assert (await radio.read_transmit_state()).value is True

        # Feed the capture at the mic cadence; the bridge's echo-tail drop
        # eats the first frame after the gate opens, so >= 3 pushes land.
        for _ in range(4):
            capture.inject_frame(frame)
            await asyncio.sleep(0.03)
        await _wait_for(lambda: len(server.audio_tx_frames) >= 6, 2.0)
        # One 1920-byte PCM frame chunks into 1364 + 556 wire payloads
        # (audio/lan_stream.py: MAX_AUDIO_PAYLOAD), inner seq per chunk.
        payload_lens = [f.payload_len for f in server.audio_tx_frames]
        assert payload_lens == [1364, 556] * (len(payload_lens) // 2)
        inner = [f.inner_seq for f in server.audio_tx_frames]
        assert inner == list(range(len(inner)))

        # PTT off again: the gate closes.
        await radio.set_ptt(False)
        assert (await radio.read_transmit_state()).value is False
        frozen = len(server.audio_tx_frames)
        for _ in range(2):
            capture.inject_frame(frame)
        await asyncio.sleep(0.4)
        assert len(server.audio_tx_frames) == frozen
    finally:
        await bridge.stop()
        await radio.disconnect()
        await server.stop()
