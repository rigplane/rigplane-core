"""Replayer LAN CI-V echo skip (fix round 1, PR #4097).

The real IC-7610 echoes every CI-V request frame back byte-identical on the
LAN CI-V port immediately before its reply. A hand-built Recording below pins
that ReplayResponder skips the echo: it is neither the reply nor unsolicited.
"""

from __future__ import annotations

import struct

from rigplane_testkit.icom import Record, Recording, ReplayResponder

_RADIO_ADDR = 0x98
_CONTROLLER = 0xE0


def _civ(cmd: int, payload: bytes = b"", *, to: int, frm: int) -> bytes:
    return b"\xfe\xfe" + bytes([to, frm, cmd]) + payload + b"\xfd"


def _civ_packet(frame: bytes) -> bytes:
    """Wrap a CI-V frame in the 0x15-byte DATA header the recorder captures."""
    pkt = bytearray(0x15 + len(frame))
    struct.pack_into("<I", pkt, 0x00, len(pkt))
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


def _freq_bcd(hz: int) -> bytes:
    digits = f"{hz:010d}"
    return bytes(
        (int(digits[9 - 2 * i - 1]) << 4) | int(digits[9 - 2 * i]) for i in range(5)
    )


class TestReplayEchoSkip:
    def test_echo_skipped_reply_answers_and_nothing_unsolicited(self) -> None:
        """c2r request, r2c byte-identical echo, r2c reply: respond returns the
        reply and the echo lands nowhere (established by respond + unsolicited
        below)."""
        request = _civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)
        reply = _civ(0x03, _freq_bcd(14_074_000), to=_CONTROLLER, frm=_RADIO_ADDR)
        recording = Recording(
            [
                Record(t=0.10, direction="c2r", port="civ", data=_civ_packet(request)),
                Record(t=0.11, direction="r2c", port="civ", data=_civ_packet(request)),
                Record(t=0.12, direction="r2c", port="civ", data=_civ_packet(reply)),
            ]
        )
        responder = ReplayResponder(recording)
        assert responder.respond(request) == reply
        assert list(responder.unsolicited()) == []

    def test_r2c_frame_with_no_pending_request_stays_unsolicited(self) -> None:
        """An r2c frame arriving with no pending request is unsolicited even
        when its bytes equal a request-shaped frame (the echo skip is scoped
        to pending requests, established by the non-empty timeline below)."""
        lone = _civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)
        recording = Recording(
            [Record(t=0.10, direction="r2c", port="civ", data=_civ_packet(lone))]
        )
        responder = ReplayResponder(recording)
        assert list(responder.unsolicited()) == [(0.10, lone)]
        assert responder.answers == {}
