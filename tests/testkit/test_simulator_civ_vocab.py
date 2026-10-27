"""Wire-level tests: the simulator's CI-V 0x25/0x26 and 0x1A 05 vocabulary.

A bare asyncio datagram client speaks the CI-V wire format against a real
``MockIcomRadio`` on loopback UDP — no production code involved — pinning the
selected/unselected receiver frequency and mode reads and writes (0x25/0x26,
both subcommand bytes), the 0x1A 05 menu items the product's startup
reader asks for at connect (``web/radio_poller.py``: ``_fetch_mod_inputs``
reads the MOD-input items 0x0091-0x0094 and ``_fetch_nb_controls`` reads the
NB items 0x0290/0x0291), and the 0x1A 06 DATA-mode reads/writes the
plain-SSB mode change now clears with: the documented DATA plus filter
form and the repo builder's DATA-only short form.

BCD decoding here is deliberately independent of the simulator's own
encoders so an encoding defect cannot cancel out.
"""

import asyncio
import struct

import pytest

from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.icom.replay import RecordedAnswerTable

from testkit.test_simulator_faults import _recv_reply

_HOST = "127.0.0.1"
_CLIENT_ID = 0x11223344
_PT_DATA = 0x00

_RADIO_ADDR = 0x98
_CONTROLLER_ADDR = 0xE0


# ---------------------------------------------------------------------------
# Plain UDP client (same shape as test_simulator_faults.py)
# ---------------------------------------------------------------------------


class _UdpClient(asyncio.DatagramProtocol):
    """Bare datagram client: everything received lands in ``queue``."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)

    def send(self, data: bytes, port: int) -> None:
        assert self.transport is not None
        self.transport.sendto(data, (_HOST, port))

    async def recv(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.queue.get(), timeout)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


# ---------------------------------------------------------------------------
# Wire builders / parsers (client side)
# ---------------------------------------------------------------------------


def _header(size: int, ptype: int, seq: int = 0) -> bytearray:
    pkt = bytearray(size)
    struct.pack_into("<I", pkt, 0x00, size)
    struct.pack_into("<H", pkt, 0x04, ptype)
    struct.pack_into("<H", pkt, 0x06, seq)
    struct.pack_into("<I", pkt, 0x08, _CLIENT_ID)
    return pkt


def _frame(cmd: int, data: bytes) -> bytes:
    """CI-V frame addressed to the radio."""
    return b"\xfe\xfe" + bytes([_RADIO_ADDR, _CONTROLLER_ADDR, cmd]) + data + b"\xfd"


def _civ_packet(frame: bytes, seq: int = 1) -> bytes:
    """Wrap a CI-V frame in a DATA header for the civ port."""
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _encode_bcd_freq(freq_hz: int) -> bytes:
    """Icom 5-byte BCD, least-significant byte first."""
    digits = f"{freq_hz:010d}"
    out = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])
        high = int(digits[9 - 2 * i - 1])
        out[i] = (high << 4) | low
    return bytes(out)


def _decode_bcd_freq(data: bytes) -> int:
    freq = 0
    for i, byte in enumerate(data):
        freq += (byte & 0x0F) * (10 ** (2 * i)) + (byte >> 4) * (10 ** (2 * i + 1))
    return freq


def _decode_bcd_value(data: bytes) -> int:
    """Decode packed BCD menu/level values, most significant pair first."""
    value = 0
    for byte in data:
        value = value * 100 + (byte >> 4) * 10 + (byte & 0x0F)
    return value


async def _send_civ(client: _UdpClient, port: int, frame: bytes) -> bytes:
    """Send one CI-V frame, return the CI-V frame of the reply.

    The shared ``_recv_reply`` skips the simulator's LAN CI-V echo (the
    request packet back byte-identical), so this resolves to the reply.
    """
    request = _civ_packet(frame)
    client.send(request, port)
    reply = await _recv_reply(client, request)
    assert reply[0x15:0x17] == b"\xfe\xfe"
    return reply[0x15:]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server():
    s = MockIcomRadio()
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client():
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


# ---------------------------------------------------------------------------
# 0x25 selected / unselected receiver frequency
# ---------------------------------------------------------------------------


async def test_selected_freq_read_returns_current_frequency_as_bcd(server, client):
    server.set_frequency(7_100_123)
    reply = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert reply[4] == 0x25
    assert reply[5] == 0x00  # receiver byte echoes the selected subcommand
    assert reply[-1] == 0xFD
    assert _decode_bcd_freq(reply[6:11]) == 7_100_123


async def test_0x05_write_and_selected_freq_read_agree_both_ways(server, client):
    # 0x05 write → visible through 0x25 00.
    ack = await _send_civ(
        client, server.civ_port, _frame(0x05, _encode_bcd_freq(7_200_000))
    )
    assert ack[4] == 0xFB  # ACK
    selected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert _decode_bcd_freq(selected[6:11]) == 7_200_000

    # 0x25 00 write → visible through 0x03.
    ack = await _send_civ(
        client, server.civ_port, _frame(0x25, b"\x00" + _encode_bcd_freq(7_300_000))
    )
    assert ack[4] == 0xFB
    plain = await _send_civ(client, server.civ_port, _frame(0x03, b""))
    assert _decode_bcd_freq(plain[5:10]) == 7_300_000


async def test_unselected_freq_write_then_read_round_trip(server, client):
    ack = await _send_civ(
        client, server.civ_port, _frame(0x25, b"\x01" + _encode_bcd_freq(3_500_000))
    )
    assert ack[4] == 0xFB
    unselected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x01"))
    assert unselected[5] == 0x01  # receiver byte echoes the unselected subcommand
    assert _decode_bcd_freq(unselected[6:11]) == 3_500_000

    # The unselected VFO has its own state: the selected frequency is untouched.
    selected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert _decode_bcd_freq(selected[6:11]) == 14_074_000


# ---------------------------------------------------------------------------
# 0x26 selected / unselected receiver mode
# ---------------------------------------------------------------------------


async def test_selected_mode_round_trip(server, client):
    # 0x26 00 set: receiver, mode (0x03=CW), data-mode flag, filter.
    ack = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00\x03\x01\x02"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert reply[4] == 0x26
    assert reply[5:9] == b"\x00\x03\x01\x02"

    # The selected mode/filter state is shared with 0x04.
    plain = await _send_civ(client, server.civ_port, _frame(0x04, b""))
    assert plain[5:7] == b"\x03\x02"


async def test_unselected_mode_read_returns_own_defaults(server, client):
    # Move the SELECTED receiver away from the unselected defaults first
    # (0x06 set -> CW, FIL3), so a 0x26 01 read that wrongly returns the
    # selected state cannot pass for the unselected one.
    ack = await _send_civ(client, server.civ_port, _frame(0x06, b"\x03\x03"))
    assert ack[4] == 0xFB
    selected = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert selected[5:9] == b"\x00\x03\x00\x03"  # CW, DATA OFF, FIL3

    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x01"))
    assert reply[4] == 0x26
    assert reply[5] == 0x01
    mode, data_mode, filt = reply[6], reply[7], reply[8]
    assert (mode, data_mode, filt) == (0x01, 0x00, 0x01)


async def test_unselected_mode_write_round_trip_leaves_selected_unchanged(
    server,
    client,
):
    # 0x26 01 set: receiver, mode (0x00=LSB), data-mode flag (0x01=ON), FIL2 —
    # distinct from the selected defaults on every byte the round trip reads.
    ack = await _send_civ(client, server.civ_port, _frame(0x26, b"\x01\x00\x01\x02"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x01"))
    assert reply[5:9] == b"\x01\x00\x01\x02"

    # The selected receiver is untouched: 0x04 still answers the defaults.
    plain = await _send_civ(client, server.civ_port, _frame(0x04, b""))
    assert plain[5:7] == b"\x01\x01"  # USB / FIL1


# ---------------------------------------------------------------------------
# 0x1A 05 menu items (startup MOD-input and NB reads)
# ---------------------------------------------------------------------------


async def test_1a05_mod_input_read_returns_modelled_byte(server, client):
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x92"))
    assert reply[4] == 0x1A
    assert reply[5] == 0x05
    # Item echo (00 92 = DATA1 MOD input) then the 1-byte BCD value 5 (LAN).
    assert reply[6:9] == b"\x00\x92\x05"


async def test_unknown_1a05_item_still_naks(server, client):
    # 00 33 (Quick Split) is a real IC-7610 menu item outside the modelled
    # table — it must NAK, not answer a made-up byte.
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x33"))
    assert reply[4] == 0xFA  # NAK


async def test_1a05_mod_input_write_then_read_round_trip(server, client):
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x91\x00"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x91"))
    # Item echo then the written value 0 (MIC), not the ACC default.
    assert reply[6:9] == b"\x00\x91\x00"


async def test_1a05_nb_depth_read_answers_valid_byte(server, client):
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x02\x90"))
    assert reply[4] == 0x1A
    assert reply[5] == 0x05
    assert reply[6:8] == b"\x02\x90"  # NB depth item echo
    assert reply[-1] == 0xFD
    # One BCD digit (0-9); the frame carries exactly one value byte.
    assert len(reply) == 10
    assert _decode_bcd_value(reply[8:9]) == 0  # documented default


async def test_1a05_nb_width_read_answers_valid_byte(server, client):
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x02\x91"))
    assert reply[6:8] == b"\x02\x91"  # NB width item echo
    assert reply[-1] == 0xFD
    # get_nb_width parses a 2-byte BCD payload (0-255), so the reply must
    # carry exactly two value bytes, not the 1-byte MOD-input form.
    assert len(reply) == 11
    assert _decode_bcd_value(reply[8:10]) == 0  # documented default


async def test_1a05_nb_width_write_then_read_round_trip(server, client):
    # 120 -> two BCD bytes 01 20 (big-endian digit pairs, the production
    # bcd_encode_value layout for byte_count=2).
    ack = await _send_civ(
        client, server.civ_port, _frame(0x1A, b"\x05\x02\x91\x01\x20")
    )
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x02\x91"))
    assert reply[8:10] == b"\x01\x20"


# ---------------------------------------------------------------------------
# 0x1A 06 DATA mode (the selected receiver's 0x26 DATA flag)
# ---------------------------------------------------------------------------


async def test_1a06_data_on_then_off_round_trip(server, client):
    # Short SET DATA1 (the builder form) → FB with GET reading 01 back
    # (plus the current filter), then SET OFF → FB, 00 00.
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert reply == b"\xfe\xfe\xe0\x98\x1a\x06\x01\x01\xfd"

    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x00"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert reply == b"\xfe\xfe\xe0\x98\x1a\x06\x00\x00\xfd"


async def test_1a06_data2_and_data3_round_trip(server, client):
    # The modelled builder contract admits DATA values 0-3; 2/3 are the
    # DATA2/DATA3 groups. The short SET preserves the default FIL1, so
    # each GET answers the value with filter 01.
    for value in (0x02, 0x03):
        ack = await _send_civ(
            client, server.civ_port, _frame(0x1A, bytes((0x06, value)))
        )
        assert ack[4] == 0xFB
        reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
        assert reply[6:8] == bytes((value, 0x01))


async def test_1a06_full_form_updates_data_and_filter(server, client):
    # The documented full form: DATA 02 with filter 03 in one SET.
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x02\x03"))
    assert ack[4] == 0xFB

    # GET reads both back, literally.
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert reply == b"\xfe\xfe\xe0\x98\x1a\x06\x02\x03\xfd"

    # The filter byte is the physical filter: 0x26 00 and 0x04 now report
    # FIL3, while mode, frequency and the unselected receiver are
    # untouched.
    mode_reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert mode_reply[5:9] == b"\x00\x01\x02\x03"
    plain = await _send_civ(client, server.civ_port, _frame(0x04, b""))
    assert plain[5:7] == b"\x01\x03"
    freq = await _send_civ(client, server.civ_port, _frame(0x03, b""))
    assert _decode_bcd_freq(freq[5:10]) == 14_074_000
    unselected = await _send_civ(client, server.civ_port, _frame(0x26, b"\x01"))
    assert unselected[5:9] == b"\x01\x01\x00\x01"


async def test_1a06_full_form_off_clears_data_preserves_filter(server, client):
    # FIL2 in place first (0x06 set), then DATA1 with FIL2 via the full
    # form, then the documented OFF form 00 00.
    ack = await _send_civ(client, server.civ_port, _frame(0x06, b"\x01\x02"))
    assert ack[4] == 0xFB
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01\x02"))
    assert ack[4] == 0xFB

    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x00\x00"))
    assert ack[4] == 0xFB

    # GET answers 00 00 (DATA OFF), but the physical filter stays FIL2
    # for 0x26 00 and 0x04 — DATA OFF does not zero it.
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert reply == b"\xfe\xfe\xe0\x98\x1a\x06\x00\x00\xfd"
    mode_reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert mode_reply[5:9] == b"\x00\x01\x00\x02"
    plain = await _send_civ(client, server.civ_port, _frame(0x04, b""))
    assert plain[5:7] == b"\x01\x02"


async def test_1a06_data_only_write_preserves_filter_mode_and_freq(server, client):
    # Distinct selected state first (CW / FIL3 via 0x06, shared with 0x26 00),
    # so a 1A 06 write that wrongly touched filter or mode cannot pass.
    ack = await _send_civ(client, server.civ_port, _frame(0x06, b"\x03\x03"))
    assert ack[4] == 0xFB
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01"))
    assert ack[4] == 0xFB

    # Only the DATA flag moved: 0x26 00 still answers CW / FIL3 with DATA1,
    # and the 1A 06 GET pairs the flag with the preserved physical filter.
    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert reply[5:9] == b"\x00\x03\x01\x03"
    data_reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert data_reply[6:8] == b"\x01\x03"
    # Frequency is untouched too.
    plain = await _send_civ(client, server.civ_port, _frame(0x03, b""))
    assert _decode_bcd_freq(plain[5:10]) == 14_074_000


async def test_1a06_and_0x26_data_flag_readback_agree(server, client):
    # A 0x26 00 write sets the flag 1A 06 reads back ...
    ack = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00\x01\x02\x02"))
    assert ack[4] == 0xFB
    mode_reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert mode_reply[7] == 0x02
    data_reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert data_reply[6:8] == b"\x02\x02"

    # ... and a 1A 06 write is what 0x26 00 reports (filter unchanged).
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x03"))
    assert ack[4] == 0xFB
    mode_reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert mode_reply[7] == 0x03
    assert mode_reply[8] == 0x02


async def test_1a06_write_is_scoped_to_the_selected_receiver():
    # A local dual_receiver sim — the shared fixture default is untouched.
    sim = MockIcomRadio(dual_receiver=True)
    await sim.start()
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    try:
        ack = await _send_civ(proto, sim.civ_port, _frame(0x07, b"\xd1"))
        assert ack[4] == 0xFB  # SUB selected

        ack = await _send_civ(proto, sim.civ_port, _frame(0x1A, b"\x06\x01"))
        assert ack[4] == 0xFB

        selected = await _send_civ(proto, sim.civ_port, _frame(0x26, b"\x00"))
        assert selected[7] == 0x01  # SUB (selected) carries the new flag
        unselected = await _send_civ(proto, sim.civ_port, _frame(0x26, b"\x01"))
        assert unselected[7] == 0x00  # MAIN keeps its own flag

        # Selecting MAIN back keeps both flags where they were.
        ack = await _send_civ(proto, sim.civ_port, _frame(0x07, b"\xd0"))
        assert ack[4] == 0xFB
        selected = await _send_civ(proto, sim.civ_port, _frame(0x26, b"\x00"))
        assert selected[7] == 0x00  # MAIN (selected) still 00
        unselected = await _send_civ(proto, sim.civ_port, _frame(0x26, b"\x01"))
        assert unselected[7] == 0x01  # SUB (unselected) still 01
    finally:
        proto.close()
        await sim.stop()


async def test_1a06_malformed_payloads_nak_without_state_change(server, client):
    ack = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01"))
    assert ack[4] == 0xFB

    # Genuinely invalid forms: DATA-on with filter 00 (no documented
    # pairing), DATA OFF with a filter (the manual footnote pins OFF to
    # 00 00), out-of-range bytes in either position, a 3-byte body, and
    # the short form's value 4 — all NAK.
    for bad in (
        b"\x06\x01\x00",
        b"\x06\x00\x01",
        b"\x06\x04\x02",
        b"\x06\x01\x04",
        b"\x06\x01\x01\x01",
        b"\x06\x04",
    ):
        reply = await _send_civ(client, server.civ_port, _frame(0x1A, bad))
        assert reply[4] == 0xFA

    # No mutation: the DATA1 set above (with the default FIL1) survives
    # every malformed form.
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert reply[6:8] == b"\x01\x01"


async def test_1a06_refused_frame_naks_without_state_change(server, client):
    # The refusal hook wins before the native handler, so no state moves.
    server.refused_civ_commands.add((0x1A, 0x06))
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01"))
    assert reply[4] == 0xFA
    assert server.refused_count == 1

    server.refused_civ_commands.clear()
    readback = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert readback == b"\xfe\xfe\xe0\x98\x1a\x06\x00\x00\xfd"
    physical = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert physical[5:9] == b"\x00\x01\x00\x01"  # USB, DATA OFF, FIL1


async def test_1a06_refusal_beats_recorded_ack_table(server, client):
    # A synthetic literal c2r/r2c pair (no recorded hardware data) whose
    # table would ACK the full-form DATA write ...
    rows = [
        {"dir": "c2r", "transport": "synthetic", "frame": "fefe98e01a060102fd"},
        {"dir": "r2c", "transport": "synthetic", "frame": "fefee098fbfd"},
    ]
    table = RecordedAnswerTable.from_civ_pairs(rows, transport="synthetic")
    request = b"\xfe\xfe\x98\xe0\x1a\x06\x01\x02\xfd"
    assert table.answer(request) == b"\xfe\xfe\xe0\x98\xfb\xfd"  # would ACK

    # ... yet the mounted table cannot override the refusal hook: the
    # write NAKs and never applies.
    server.recorded_answers = table
    server.refused_civ_commands.add((0x1A, 0x06))
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06\x01\x02"))
    assert reply[4] == 0xFA
    assert server.refused_count == 1

    server.refused_civ_commands.clear()
    readback = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x06"))
    assert readback == b"\xfe\xfe\xe0\x98\x1a\x06\x00\x00\xfd"
    physical = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert physical[5:9] == b"\x00\x01\x00\x01"  # USB, DATA OFF, FIL1
