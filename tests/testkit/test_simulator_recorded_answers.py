"""LAN simulator: MockIcomRadio answering from the recorded answer table.

Focused raw-UDP tests for the ``recorded_answers`` mount on
:class:`~rigplane_testkit.icom.MockIcomRadio`, built by
``tests/testkit/ic7610_fakes.py: ic7610_lan_fake``. They pin the mount's
answer order at the wire: the opt-in refusal hook NAKs first and is never
overridden by the table; a native handler's data/ACK answer is kept; a
native NAK — and a request with no native handler — falls to the recorded
table; a body the table has no entry for still NAKs; and with no table
mounted the same requests NAK exactly as before the mount. The at-scale
measurement of the same order is the conformance answer-class replay
(``tests/testkit/test_ic7610_conformance.py``).
"""

from __future__ import annotations

from rigplane_testkit.icom import MockIcomRadio, _bcd_encode_freq
from rigplane_testkit.icom.replay import extract_civ_frame

from testkit.ic7610_fakes import ic7610_lan_fake
from testkit.test_ic7610_conformance import _civ_packet, _lan_sim
from testkit.test_simulator_faults import _UdpClient

# The simulator models split natively (MOR-3632); these tests turn that off
# (``models_tx_frequency=False``) so split stays a command only the recorded
# table answers. Recorded LAN facts of the split-frequency command (0x0F), from the
# ``lan`` rows of tests/fixtures/ic7610/civ_pairs.jsonl: the bare ``0F``
# read of the probe window is answered with split OFF (``00``), and the
# ``0F 01`` write of the rx_write_restore window with an ACK.
_SPLIT_READ = bytes.fromhex("fefe98e00ffd")
_SPLIT_READ_OFF_REPLY = bytes.fromhex("fefee0980f00fd")
_SPLIT_WRITE_ON = bytes.fromhex("fefe98e00f01fd")
_ACK_REPLY = bytes.fromhex("fefee098fbfd")
_NAK_REPLY = bytes.fromhex("fefee098fafd")
_SPLIT_READ_ON_REPLY = bytes.fromhex("fefee0980f01fd")

_FREQ_GET = b"\xfe\xfe\x98\xe0\x03\xfd"
# The recording answers ``03`` with 14,280,600 Hz (probe window,
# civ_pairs.jsonl) — distinct from any frequency written below, so a reply
# from the table can never satisfy the native-state assertion.
_FREQ_SET = b"\xfe\xfe\x98\xe0\x05" + _bcd_encode_freq(7_100_000) + b"\xfd"
_FREQ_SET_REPLY = _bcd_encode_freq(7_100_000)
# Cmd 0x22 appears in no recorded row and has no native handler.
_UNRECORDED = bytes.fromhex("fefe98e02200fd")


async def _ask_frame(
    client: _UdpClient, port: int, frame: bytes, *, seq: int
) -> bytes | None:
    """Send one CI-V frame; assert the LAN echo, return the reply's frame."""
    pkt = _civ_packet(frame, seq=seq)
    client.send(pkt, port)
    echo = await client.recv()
    assert echo == pkt, "the LAN CI-V echo precedes the reply"
    return extract_civ_frame(await client.recv())


async def test_recorded_split_read_and_write_answer_from_the_table() -> None:
    async with _lan_sim(ic7610_lan_fake(models_tx_frequency=False)) as (server, client):
        reply = await _ask_frame(client, server.civ_port, _SPLIT_READ, seq=1)
        assert reply == _SPLIT_READ_OFF_REPLY
        reply = await _ask_frame(client, server.civ_port, _SPLIT_WRITE_ON, seq=2)
        assert reply == _ACK_REPLY
        reply = await _ask_frame(client, server.civ_port, _SPLIT_READ, seq=3)
        assert reply == _SPLIT_READ_ON_REPLY


async def test_native_state_wins_over_the_recording() -> None:
    async with _lan_sim(ic7610_lan_fake()) as (server, client):
        reply = await _ask_frame(client, server.civ_port, _FREQ_SET, seq=1)
        assert reply == _ACK_REPLY
        reply = await _ask_frame(client, server.civ_port, _FREQ_GET, seq=2)
        assert reply is not None
        assert reply[5:-1] == _FREQ_SET_REPLY


async def test_body_neither_native_nor_recorded_still_naks() -> None:
    async with _lan_sim(ic7610_lan_fake()) as (server, client):
        reply = await _ask_frame(client, server.civ_port, _UNRECORDED, seq=1)
        assert reply == _NAK_REPLY


async def test_refused_command_still_naks_over_the_table() -> None:
    sim = ic7610_lan_fake(models_tx_frequency=False)
    sim.refused_civ_commands = {(0x0F, None)}
    async with _lan_sim(sim) as (server, client):
        reply = await _ask_frame(client, server.civ_port, _SPLIT_READ, seq=1)
        assert reply == _NAK_REPLY
        assert sim.refused_count == 1


async def test_without_a_table_the_split_read_still_naks() -> None:
    async with _lan_sim(MockIcomRadio(models_tx_frequency=False)) as (server, client):
        reply = await _ask_frame(client, server.civ_port, _SPLIT_READ, seq=1)
        assert reply == _NAK_REPLY
