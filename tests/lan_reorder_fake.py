"""A LAN radio's CI-V port as seen by one ``IcomCivTransport`` (MOR-4205).

The fake numbers the radio's datagrams the way the radio does (one header
sequence number per tracked type-0 packet) and hands them to the transport
through its real datagram entry point (``_UdpProtocol.datagram_received``) in
whatever order a test chooses: reordered, duplicated, or with a number never
sent. It reads the retransmit requests the transport sends and, when
``serve_retransmits`` is on, answers them with the stored packet, as the
radio does (``tests/fixtures/ic7610/retransmit_gaps.jsonl``,
``gap_lost_buffered``).
"""

from __future__ import annotations

import struct

from rigplane.core.transport import CONTROL_SIZE, IcomTransport, _UdpProtocol

from _helpers import wrap_civ_in_udp

_ADDR = ("192.0.2.1", 50002)
_RETRANSMIT = 0x01


class LanRadioFake:
    """Numbers, sends and resends the radio's CI-V datagrams to one transport."""

    def __init__(self, transport: IcomTransport, *, first_seq: int = 1) -> None:
        self.transport = transport
        self.serve_retransmits = False
        self.next_seq = first_seq
        self.sent: dict[int, bytes] = {}
        self.retransmit_requests: list[int] = []
        self._protocol = _UdpProtocol(transport)
        transport._raw_send = self._from_client

    def packet(self, civ_frame: bytes) -> int:
        """Number one CI-V frame as the radio's next datagram; return its seq."""
        seq = self.next_seq
        self.sent[seq] = wrap_civ_in_udp(civ_frame, seq=seq)
        self.next_seq = (seq + 1) & 0xFFFF
        return seq

    def idle(self) -> int:
        """Number the radio's tracked idle keepalive (type 0, no CI-V)."""
        seq = self.next_seq
        pkt = bytearray(CONTROL_SIZE)
        struct.pack_into("<IHH", pkt, 0, CONTROL_SIZE, 0x00, seq)
        self.sent[seq] = bytes(pkt)
        self.next_seq = (seq + 1) & 0xFFFF
        return seq

    def deliver(self, *seqs: int) -> None:
        """Hand the numbered datagrams to the transport in this order."""
        for seq in seqs:
            self._protocol.datagram_received(self.sent[seq], _ADDR)

    def _from_client(self, data: bytes) -> None:
        if len(data) < CONTROL_SIZE or struct.unpack_from("<H", data, 4)[0] != (
            _RETRANSMIT
        ):
            return
        if len(data) == CONTROL_SIZE:
            asked = [struct.unpack_from("<H", data, 6)[0]]
        else:
            asked = []
            for offset in range(CONTROL_SIZE, len(data) - 3, 4):
                first, last = struct.unpack_from("<HH", data, offset)
                asked.extend(
                    (first + n) & 0xFFFF for n in range(((last - first) & 0xFFFF) + 1)
                )
        self.retransmit_requests.extend(asked)
        if self.serve_retransmits:
            for seq in asked:
                if seq in self.sent:
                    self.deliver(seq)


def drain_seqs(transport: IcomTransport) -> list[int]:
    """Header sequence numbers of everything the transport has queued, in order."""
    seqs = []
    while not transport._packet_queue.empty():
        seqs.append(
            struct.unpack_from("<H", transport._packet_queue.get_nowait(), 6)[0]
        )
    return seqs
