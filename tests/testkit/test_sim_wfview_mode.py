"""wfview-mode behaviour knobs of the MockIcomRadio simulator (MOR-4436).

Recorded evidence: a real Xiegu G7250 fw V1.0.1 in "wfview" network mode
(MOR-4436; the run recordings live on the bench host, not in this repo):

* discovery: the CI-V port answers AYT with ping REQUESTs (never
  I-Am-Here) and AYR normally; the audio port answers neither I-Am-Here
  nor AYR: it starts the RX stream right after AYT and the ping REQUEST
  follows the first RX burst, so data always arrives first;
* SET confirmation: every SET is confirmed by an echo from the radio
  address, never FB/FA; GETs answer the normal data replies;
* CI-V address: only frames to the radio's own address (0xA0) are
  answered;
* frequency: ``05 <freq>`` is confirmed but NOT applied, while
  ``25 00 <freq>`` is applied;
* scope span: GET ``27 15 00`` answers receiver byte 00 plus a 6-byte
  little-endian BCD value (192000 = the FULL display span); the
  centre-mode ``27 00`` wave's f2 field carries half that value.

Every knob defaults to today's behaviour byte for byte; the
``test_defaults_*`` tests pin that. All tests run on the virtual clock;
silence is asserted by advancing the clock and checking the client's
queue, never by waiting out a real timeout.
"""

from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

import pytest

from rigplane.commands import build_civ_frame, parse_civ_frame
from rigplane.testing.timing import VirtualClock
from rigplane.types import bcd_decode, bcd_encode

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.simulator import _CIV_HEADER_SIZE, _PT_DATA

from testkit.ic7610_fakes import ic7610_lan_fake

from testkit.test_simulator_audio import _audio_openclose, _ayr
from testkit.test_simulator_faults import (
    _CLIENT_ID,
    _HOST,
    _UdpClient,
    _ayt,
    _header,
    _is_echo_of,
    _openclose,
)

pytestmark = pytest.mark.timeout(30)

_RADIO_ADDR = 0xA0
_CONTROLLER_ADDR = 0xE0

_PT_I_AM_HERE = 0x04
_PT_ARE_YOU_READY = 0x06
_PT_PING = 0x07

_DEFAULT_FREQ_HZ = 14_074_000
_G7250_FREQ_HZ = 7_138_000  # the frequency the bench audit read (MOR-4352)


@asynccontextmanager
async def _radio(**kwargs: Any) -> AsyncGenerator[MockIcomRadio]:
    sim = MockIcomRadio(**kwargs)
    try:
        await sim.start()
        yield sim
    finally:
        await sim.stop()


@pytest.fixture
async def client() -> AsyncGenerator[_UdpClient]:
    proto = _UdpClient()
    await asyncio.get_running_loop().create_datagram_endpoint(
        lambda: proto, local_addr=(_HOST, 0)
    )
    try:
        yield proto
    finally:
        proto.close()


def _civ_data(frame: bytes, seq: int) -> bytes:
    """Wrap a CI-V frame in a CI-V-port DATA packet (production layout).

    Mirrors the simulator's own ``_wrap_civ`` the way the sibling
    ``_freq_get`` helper does: 0xC1 at 0x10, the frame length at 0x11,
    the header sequence echoed big-endian at 0x13.
    """
    pkt = bytearray(_header(_CIV_HEADER_SIZE + len(frame), _PT_DATA, seq=seq))
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    struct.pack_into(">H", pkt, 0x13, seq)
    pkt[_CIV_HEADER_SIZE:] = frame
    return bytes(pkt)


def _request(cmd: int, payload: bytes = b"") -> bytes:
    """A CI-V request frame built with the production frame builder."""
    return build_civ_frame(_RADIO_ADDR, _CONTROLLER_ADDR, cmd, data=payload)


def _request_echo(cmd: int, payload: bytes = b"") -> bytes:
    """The radio-address echo of a request: to/from swapped, bytes kept."""
    return build_civ_frame(_CONTROLLER_ADDR, _RADIO_ADDR, cmd, data=payload)


async def _wire(
    client: _UdpClient, sim: MockIcomRadio, frame: bytes, seq: int
) -> bytes:
    """Send one CI-V frame, skip the LAN echo, return the reply frame."""
    request = _civ_data(frame, seq)
    client.send(request, sim.civ_port)
    echo = await client.recv()
    assert _is_echo_of(echo, request), "the LAN echo precedes the reply"
    reply = await client.recv()
    return reply[_CIV_HEADER_SIZE:]


async def _assert_silence(
    clock: VirtualClock, client: _UdpClient, seconds: float = 0.5
) -> None:
    """Advance the clock and assert nothing arrived (no real waiting)."""
    await clock.elapse(seconds)
    for _ in range(3):
        await asyncio.sleep(0)
    assert client.queue.empty(), "expected silence on the wire"


def _ping_fields(pkt: bytes) -> tuple[int, int, int, int, int]:
    """(len, type, header seq, sentid, rcvdid) of a handshake packet."""
    assert len(pkt) == 0x15
    return (
        struct.unpack_from("<I", pkt, 0)[0],
        struct.unpack_from("<H", pkt, 4)[0],
        struct.unpack_from("<H", pkt, 6)[0],
        struct.unpack_from("<I", pkt, 8)[0],
        struct.unpack_from("<I", pkt, 12)[0],
    )


async def _centre_waves(client: _UdpClient) -> list[bytes]:
    """Drain the queue, keeping centre-mode 27 00 wave frames."""
    for _ in range(3):
        await asyncio.sleep(0)
    waves = []
    while not client.queue.empty():
        try:
            pkt = client.queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        if len(pkt) <= _CIV_HEADER_SIZE:
            continue
        try:
            parsed = parse_civ_frame(pkt[_CIV_HEADER_SIZE:])
        except ValueError:
            continue
        if (
            parsed.command == 0x27
            and parsed.sub == 0
            and len(parsed.data) >= 15
            and parsed.data[3] == 0x00
        ):
            waves.append(pkt[_CIV_HEADER_SIZE:])
    return waves


# ---------------------------------------------------------------------------
# Defaults: today's behaviour, byte for byte
# ---------------------------------------------------------------------------


async def test_defaults_pin_today_behaviour(
    virtual_clock: VirtualClock, client: _UdpClient
) -> None:
    """Without knobs: I-Am-Here on both ports, FB SETs, applied 05, NAK 27 15."""
    async with _radio() as sim:
        radio_addr = 0x98  # the simulator default (IC-7610)

        client.send(_ayt(), sim.civ_port)
        iah = await client.recv()
        assert struct.unpack_from("<H", iah, 4)[0] == _PT_I_AM_HERE

        client.send(_ayr(), sim.civ_port)
        ayr = await client.recv()
        assert struct.unpack_from("<H", ayr, 4)[0] == _PT_ARE_YOU_READY

        await _assert_silence(virtual_clock, client)

        set_mode = build_civ_frame(radio_addr, _CONTROLLER_ADDR, 0x06, data=b"\x01\x01")
        reply = await _wire(client, sim, set_mode, seq=1)
        assert reply == build_civ_frame(_CONTROLLER_ADDR, radio_addr, 0xFB), (
            "SET answers FB today"
        )

        get_freq = build_civ_frame(radio_addr, _CONTROLLER_ADDR, 0x03)
        reply = await _wire(client, sim, get_freq, seq=2)
        assert reply == build_civ_frame(
            _CONTROLLER_ADDR,
            radio_addr,
            0x03,
            data=bcd_encode(_DEFAULT_FREQ_HZ),
        )

        set_freq = build_civ_frame(
            radio_addr,
            _CONTROLLER_ADDR,
            0x05,
            data=bcd_encode(_G7250_FREQ_HZ),
        )
        assert await _wire(client, sim, set_freq, seq=3) == build_civ_frame(
            _CONTROLLER_ADDR, radio_addr, 0xFB
        )
        reply = await _wire(client, sim, get_freq, seq=4)
        assert reply == build_civ_frame(
            _CONTROLLER_ADDR,
            radio_addr,
            0x03,
            data=bcd_encode(_G7250_FREQ_HZ),
        ), "05 is applied today"

        for payload in (b"\x15", b"\x15\x00"):
            span_get = build_civ_frame(radio_addr, _CONTROLLER_ADDR, 0x27, data=payload)
            assert await _wire(client, sim, span_get, seq=5) == build_civ_frame(
                _CONTROLLER_ADDR, radio_addr, 0xFA
            ), "27 15 NAKs today"

    async with _radio(enable_audio=True) as sim:
        client.send(_ayt(), sim.audio_port)
        iah_audio = await client.recv()
        assert struct.unpack_from("<H", iah_audio, 4)[0] == _PT_I_AM_HERE

        client.send(_ayr(), sim.audio_port)
        ayr_audio = await client.recv()
        assert struct.unpack_from("<H", ayr_audio, 4)[0] == _PT_ARE_YOU_READY

        # No stream starts before the audio OpenClose(open): the port
        # answered discovery only.
        assert sim.audio_rx_frames_sent == 0
        await _assert_silence(virtual_clock, client)


# ---------------------------------------------------------------------------
# substream_discovery="ping"
# ---------------------------------------------------------------------------


async def test_ping_mode_civ_ayt_answers_ping_request(
    virtual_clock: VirtualClock, client: _UdpClient
) -> None:
    """CI-V AYT draws one ping REQUEST (never I-Am-Here); AYR echoes as today."""
    async with _radio(radio_addr=_RADIO_ADDR, substream_discovery="ping") as sim:
        client.send(_ayt(), sim.civ_port)
        first = await client.recv()
        length, ptype, seq, sentid, rcvdid = _ping_fields(first)
        assert length == 0x15
        assert ptype == _PT_PING
        assert seq == 0, "pings number their own per-port sequence from 0"
        assert sentid == sim.radio_id
        assert rcvdid == _CLIENT_ID
        assert first[0x10] == 0x00, "request, not reply"

        client.send(_ayt(), sim.civ_port)
        second = await client.recv()
        assert _ping_fields(second) == (0x15, _PT_PING, 1, sim.radio_id, _CLIENT_ID)

        # One ping per AYT and no I-Am-Here anywhere in the exchange.
        await _assert_silence(virtual_clock, client)

        client.send(_ayr(), sim.civ_port)
        ayr = await client.recv()
        assert struct.unpack_from("<H", ayr, 4)[0] == _PT_ARE_YOU_READY


async def _drain_audio(client: _UdpClient) -> list[bytes]:
    """Flush loopback delivery, then take every queued datagram."""
    for _ in range(10):
        await asyncio.sleep(0)
    out = []
    while not client.queue.empty():
        try:
            out.append(client.queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return out


def _rx_inner_seq(pkt: bytes) -> int:
    return struct.unpack_from(">H", pkt, 0x12)[0]


async def test_ping_mode_audio_ayt_starts_rx_first_then_pings(
    virtual_clock: VirtualClock, client: _UdpClient
) -> None:
    """Audio AYT starts RX first; the ping follows the first burst."""
    async with _radio(
        radio_addr=_RADIO_ADDR,
        substream_discovery="ping",
        enable_audio=True,
    ) as sim:
        client.send(_ayt(), sim.audio_port)

        # Let the loopback AYT land first: elapse() settles an already
        # quiet loop, so without this the AYT would still be in flight
        # when virtual time jumps.
        for _ in range(100):
            if sim._audio_client is not None:
                break
            await asyncio.sleep(0)
        assert sim._audio_client is not None, "the AYT started the RX stream"

        # No immediate ping: the first datagrams are RX data, and the
        # one ping REQUEST follows the first burst (recorded G7250
        # run3: RX data from t=1.055, the first ping at t=1.532).
        await virtual_clock.elapse(0.06)
        got = await _drain_audio(client)
        assert len(got) >= 4, "a first burst plus the ping arrive"
        pings = [
            pkt
            for pkt in got
            if len(pkt) == 0x15 and struct.unpack_from("<H", pkt, 4)[0] == _PT_PING
        ]
        assert len(pings) == 1, "one ping per AYT"
        ping = pings[0]
        assert _ping_fields(ping) == (
            0x15,
            _PT_PING,
            0,
            sim.radio_id,
            _CLIENT_ID,
        )
        assert ping[0x10] == 0x00, "request, not reply"
        assert got.index(ping) > 0, "data arrives before the ping"
        assert all(len(pkt) > 0x18 for pkt in got[: got.index(ping)])
        assert sim.audio_rx_frames_sent >= 3
        assert [_rx_inner_seq(pkt) for pkt in got[:3]] == [0, 1, 2]

        # AYR draws no handshake of any kind; the RX stream keeps flowing.
        client.send(_ayr(), sim.audio_port)
        await virtual_clock.elapse(0.3)
        rested = await _drain_audio(client)
        assert rested, "the stream outlives the AYR"
        assert all(len(pkt) > 0x18 for pkt in rested)
        last_inner = _rx_inner_seq(rested[-1])

        # A later OpenClose(open) from the same client keeps working:
        # the numbering continues (retarget, no second stream).
        client.send(_audio_openclose(seq=0), sim.audio_port)
        await virtual_clock.elapse(0.06)
        later = [await client.recv() for _ in range(3)]
        assert [_rx_inner_seq(pkt) for pkt in later] == [
            (last_inner + 1 + i) & 0xFFFF for i in range(3)
        ]


# ---------------------------------------------------------------------------
# Only the radio's own address is answered (no knob)
# ---------------------------------------------------------------------------


async def test_only_frames_to_own_address_are_answered(
    virtual_clock: VirtualClock, client: _UdpClient
) -> None:
    """GET 03 to 0x88/0x94 draws silence; to 0xA0 it is answered."""
    async with _radio(radio_addr=_RADIO_ADDR) as sim:
        for to_addr in (0x88, 0x94):
            foreign = build_civ_frame(to_addr, _CONTROLLER_ADDR, 0x03)
            client.send(_civ_data(foreign, seq=1), sim.civ_port)
            await _assert_silence(virtual_clock, client)

        own = _request(0x03)
        assert await _wire(client, sim, own, seq=2) == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x03,
            data=bcd_encode(_DEFAULT_FREQ_HZ),
        )


# ---------------------------------------------------------------------------
# ignored_civ_commands (+ write_confirmation="echo" interaction)
# ---------------------------------------------------------------------------


async def test_ignored_commands_confirm_without_applying(
    client: _UdpClient,
) -> None:
    """05 echoes but changes nothing; the native 25 00 SET applies."""
    async with _radio(
        radio_addr=_RADIO_ADDR,
        write_confirmation="echo",
        lan_civ_echo=False,
        ignored_civ_commands={(0x05, None)},
    ) as sim:
        set_freq = _request(0x05, bcd_encode(_G7250_FREQ_HZ))
        client.send(_civ_data(set_freq, seq=1), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == _request_echo(
            0x05, bcd_encode(_G7250_FREQ_HZ)
        )
        assert sim.ignored_count == 1

        get_freq = _request(0x03)
        client.send(_civ_data(get_freq, seq=2), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x03,
            data=bcd_encode(_DEFAULT_FREQ_HZ),
        ), "the ignored 05 changed nothing"

        set_selected = _request(0x25, b"\x00" + bcd_encode(_G7250_FREQ_HZ))
        client.send(_civ_data(set_selected, seq=3), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == _request_echo(
            0x25, b"\x00" + bcd_encode(_G7250_FREQ_HZ)
        )

        client.send(_civ_data(get_freq, seq=4), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x03,
            data=bcd_encode(_G7250_FREQ_HZ),
        ), "the native 25 00 SET applied"
        assert sim.ignored_count == 1


async def test_ignored_commands_answer_fb_without_echo_knob(
    client: _UdpClient,
) -> None:
    """Without echo mode the ignored SET is confirmed with a plain FB."""
    async with _radio(
        radio_addr=_RADIO_ADDR,
        lan_civ_echo=False,
        ignored_civ_commands={(0x05, None)},
    ) as sim:
        set_freq = _request(0x05, bcd_encode(_G7250_FREQ_HZ))
        client.send(_civ_data(set_freq, seq=1), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR, _RADIO_ADDR, 0xFB
        )
        get_freq = _request(0x03)
        client.send(_civ_data(get_freq, seq=2), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x03,
            data=bcd_encode(_DEFAULT_FREQ_HZ),
        )


# ---------------------------------------------------------------------------
# span_preset_semantics
# ---------------------------------------------------------------------------


async def test_span_full_width_models_recorded_g7250_bytes(
    client: _UdpClient,
) -> None:
    """full_width/6 bytes: GET answers the recorded 192000 reading."""
    async with _radio(
        radio_addr=_RADIO_ADDR,
        lan_civ_echo=False,
        span_preset_semantics="full_width",
        scope_span_value_bytes=6,
        # The recorded IC-7610 LAN table: its writable 5-byte 27 15 00 row
        # would ACK the short SET below, and it learned a NAK for the bare
        # 27 15 — so this test pins both halves of the span fallthrough.
        recorded_answers=ic7610_lan_fake().recorded_answers,
    ) as sim:
        get_span = _request(0x27, b"\x15\x00")
        client.send(_civ_data(get_span, seq=1), sim.civ_port)
        reply = (await client.recv())[_CIV_HEADER_SIZE:]
        assert reply == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x27,
            data=b"\x15\x00" + bcd_encode(192_000, width=6),
        )
        assert reply == bytes.fromhex("fefee0a0271500002019000000fd")

        set_span = _request(0x27, b"\x15\x00" + bcd_encode(48_000, width=6))
        client.send(_civ_data(set_span, seq=2), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR, _RADIO_ADDR, 0xFB
        )
        assert sim.scope_span_wire_hz == 48_000
        client.send(_civ_data(get_span, seq=3), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR,
            _RADIO_ADDR,
            0x27,
            data=b"\x15\x00" + bcd_encode(48_000, width=6),
        ), "spans read back exactly"

        short_set = _request(0x27, b"\x15\x00" + bcd_encode(48_000, width=5))
        client.send(_civ_data(short_set, seq=4), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR, _RADIO_ADDR, 0xFA
        ), "a 5-byte SET to a 6-byte radio NAKs"
        assert sim.scope_span_wire_hz == 48_000

        bare = _request(0x27, b"\x15")
        client.send(_civ_data(bare, seq=5), sim.civ_port)
        assert (await client.recv())[_CIV_HEADER_SIZE:] == build_civ_frame(
            _CONTROLLER_ADDR, _RADIO_ADDR, 0xFA
        ), "bare 27 15 keeps today's fallthrough"


async def test_span_wave_f2_tracks_wire_value(
    virtual_clock: VirtualClock, client: _UdpClient
) -> None:
    """Emitted centre-mode waves carry the wire span (f2)."""
    cases: tuple[tuple[Literal["full_width", "half_width"], int, int, int], ...] = (
        ("full_width", 6, 48_000, 24_000),
        ("half_width", 5, 96_000, 96_000),
    )
    for semantics, width, set_hz, wave_hz in cases:
        async with _radio(
            radio_addr=_RADIO_ADDR,
            span_preset_semantics=semantics,
            scope_span_value_bytes=width,
            scope_stream=True,
            clock=virtual_clock,
        ) as sim:
            seq = 1
            client.send(_openclose(seq=seq), sim.civ_port)
            seq += 1
            marker = await client.recv()
            assert marker[_CIV_HEADER_SIZE:] == build_civ_frame(0x00, _RADIO_ADDR, 0x00)

            scope_on = _request(0x27, b"\x11\x01")
            assert await _wire(client, sim, scope_on, seq) == build_civ_frame(
                _CONTROLLER_ADDR, _RADIO_ADDR, 0xFB
            )
            seq += 1
            set_span = _request(0x27, b"\x15\x00" + bcd_encode(set_hz, width=width))
            assert await _wire(client, sim, set_span, seq) == build_civ_frame(
                _CONTROLLER_ADDR, _RADIO_ADDR, 0xFB
            )

            virtual_clock.advance(0.5)
            assert sim.pump_scope_stream() == 1
            waves = await _centre_waves(client)
            assert waves, "the open scope emits centre-mode waves"
            for wave in waves:
                assert bcd_decode(bytes(wave[15:20])) == wave_hz


def test_scope_span_value_bytes_rejects_other_widths() -> None:
    """Only the Icom frequency-field widths (5, 6) are modellable."""
    with pytest.raises(ValueError, match="5 or 6"):
        MockIcomRadio(scope_span_value_bytes=4)


def test_controller_ack_frame_gets_no_reply_with_table_mounted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A controller's own FB frame is terminal: no table lookup, no reply."""
    sim = ic7610_lan_fake()  # recorded LAN table mounted, knobs at defaults
    calls: list[bytes] = []
    real_answer = sim.recorded_answers.answer
    assert real_answer is not None

    def _spy(frame: bytes) -> bytes | None:
        calls.append(frame)
        return real_answer(frame)

    monkeypatch.setattr(sim.recorded_answers, "answer", _spy)
    assert sim._dispatch_civ(0xFB, b"", 0xE0) is None  # noqa: SLF001
    assert calls == []
