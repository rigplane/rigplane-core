"""Replayer tests: synthetic JSONL unit coverage + a real recorded session.

Covers the MOR-3216 replayer contract: ``Recording.load``,
``ReplayResponder.respond`` (latest wins, misses land in ``unanswered``) and
``unsolicited()`` ordering — including against a session recorded through the
proxy with the production client.
"""

from __future__ import annotations

import json
import struct

from rigplane.radio import IcomRadio

from rigplane_testkit.icom import (
    IcomSessionRecorder,
    MockIcomRadio,
    Recording,
    ReplayResponder,
)
from rigplane_testkit.icom.replay import extract_civ_frame, request_key

from _perf_helpers import fast_connect

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


def _write_jsonl(path, entries: list[tuple[float, str, bytes]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for t, direction, data in entries:
            fh.write(
                json.dumps({"t": t, "dir": direction, "port": "civ", "hex": data.hex()})
                + "\n"
            )


def _synthetic_recording(tmp_path) -> Recording:
    freq_req = _civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)
    mode_req = _civ(0x04, to=_RADIO_ADDR, frm=_CONTROLLER)
    reply1 = _civ(0x03, _freq_bcd(14_074_000), to=_CONTROLLER, frm=_RADIO_ADDR)
    reply2 = _civ(0x03, _freq_bcd(7_074_000), to=_CONTROLLER, frm=_RADIO_ADDR)
    mode_reply = _civ(0x04, b"\x01\x01", to=_CONTROLLER, frm=_RADIO_ADDR)
    unso1 = _civ(0x03, _freq_bcd(14_074_010), to=0x00, frm=_RADIO_ADDR)
    unso2 = _civ(0x03, _freq_bcd(14_074_020), to=0x00, frm=_RADIO_ADDR)
    _write_jsonl(
        tmp_path / "syn.jsonl",
        [
            (0.10, "c2r", _civ_packet(freq_req)),
            (0.11, "r2c", _civ_packet(reply1)),
            (0.20, "r2c", _civ_packet(unso1)),
            (0.30, "c2r", _civ_packet(mode_req)),
            (0.31, "r2c", _civ_packet(mode_reply)),
            (0.40, "c2r", _civ_packet(freq_req)),
            (0.41, "r2c", _civ_packet(reply2)),
            (0.50, "r2c", _civ_packet(unso2)),
        ],
    )
    return Recording.load(tmp_path / "syn.jsonl")


class TestRecordingLoad:
    def test_load_parses_rows(self, tmp_path) -> None:
        recording = _synthetic_recording(tmp_path)
        assert len(recording.records) == 8
        first = recording.records[0]
        assert first.t == 0.10
        assert first.direction == "c2r"
        assert first.port == "civ"
        assert extract_civ_frame(first.data) == _civ(
            0x03, to=_RADIO_ADDR, frm=_CONTROLLER
        )


class TestReplayResponder:
    def test_answers_recorded_request_latest_wins(self, tmp_path) -> None:
        responder = ReplayResponder(_synthetic_recording(tmp_path))
        req = _civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)
        assert responder.respond(req) == _civ(
            0x03, _freq_bcd(7_074_000), to=_CONTROLLER, frm=_RADIO_ADDR
        )
        assert responder.unanswered == []

    def test_mode_lookup_by_cmd(self, tmp_path) -> None:
        responder = ReplayResponder(_synthetic_recording(tmp_path))
        reply = responder.respond(_civ(0x04, to=_RADIO_ADDR, frm=_CONTROLLER))
        assert reply == _civ(0x04, b"\x01\x01", to=_CONTROLLER, frm=_RADIO_ADDR)

    def test_unrecorded_command_is_unanswered(self, tmp_path) -> None:
        responder = ReplayResponder(_synthetic_recording(tmp_path))
        unknown = _civ(0x1A, b"\x05", to=_RADIO_ADDR, frm=_CONTROLLER)
        assert responder.respond(unknown) is None
        assert responder.unanswered == [unknown]

    def test_subcmd_reply_not_stolen_by_lost_reply(self, tmp_path) -> None:
        """PR #4035 round 1: the ``14 01`` reply was lost on the wire, so the
        recording holds ``14 01``, then ``14 0A`` and its ``14 0A 01 28``
        answer; the answer must pair with ``14 0A`` by (cmd, subcmd), not
        with the stale ``14 01`` by cmd alone."""
        req_lost = _civ(0x14, b"\x01", to=_RADIO_ADDR, frm=_CONTROLLER)
        req_next = _civ(0x14, b"\x0a", to=_RADIO_ADDR, frm=_CONTROLLER)
        reply = _civ(0x14, b"\x0a\x01\x28", to=_CONTROLLER, frm=_RADIO_ADDR)
        path = tmp_path / "lost.jsonl"
        _write_jsonl(
            path,
            [
                (0.10, "c2r", _civ_packet(req_lost)),
                (0.20, "c2r", _civ_packet(req_next)),
                (0.21, "r2c", _civ_packet(reply)),
            ],
        )
        responder = ReplayResponder(Recording.load(path))
        assert responder.respond(req_next) == reply
        assert responder.unanswered == []
        assert list(responder.unsolicited()) == []

    def test_ack_paired_with_most_recent_request(self, tmp_path) -> None:
        """With the ``14 01`` reply lost, a later bare ACK (0xFB stands in
        the command slot and names no command) must pair with the request
        just sent, not with the stale head of the queue."""
        req_lost = _civ(0x14, b"\x01", to=_RADIO_ADDR, frm=_CONTROLLER)
        req_set = _civ(0x05, _freq_bcd(14_074_000), to=_RADIO_ADDR, frm=_CONTROLLER)
        ack = _civ(0xFB, to=_CONTROLLER, frm=_RADIO_ADDR)
        path = tmp_path / "ack.jsonl"
        _write_jsonl(
            path,
            [
                (0.10, "c2r", _civ_packet(req_lost)),
                (0.20, "c2r", _civ_packet(req_set)),
                (0.21, "r2c", _civ_packet(ack)),
            ],
        )
        responder = ReplayResponder(Recording.load(path))
        assert responder.respond(req_set) == ack
        assert responder.unanswered == []

    def test_unsolicited_in_order(self, tmp_path) -> None:
        responder = ReplayResponder(_synthetic_recording(tmp_path))
        timeline = list(responder.unsolicited())
        assert [t for t, _ in timeline] == [0.20, 0.50]
        assert all(f[4] == 0x03 for _, f in timeline)

    def test_request_key_shape(self) -> None:
        assert request_key(_civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)) == (
            0x03,
            None,
            b"",
        )
        assert request_key(_civ(0x14, b"\x0a", to=_RADIO_ADDR, frm=_CONTROLLER)) == (
            0x14,
            0x0A,
            b"",
        )


class TestReplayFromRecordedSession:
    async def test_get_freq_answered_with_recorded_bytes(self, tmp_path) -> None:
        mock = MockIcomRadio(username="replayuser", password="replaypass1")
        await mock.start()
        out = tmp_path / "session.jsonl"
        recorder = IcomSessionRecorder("127.0.0.1", mock.control_port, out)
        await recorder.start()
        try:
            radio = IcomRadio(
                host="127.0.0.1",
                port=recorder.control_port,
                username="replayuser",
                password="replaypass1",
                model="IC-7610",
            )
            with fast_connect():
                await radio.connect()
            assert await radio.get_freq() == 14_074_000
            mock.set_frequency(7_074_000)
            assert await radio.get_freq() == 7_074_000
            await radio.disconnect()
        finally:
            await recorder.stop()
            await mock.stop()

        recording = Recording.load(out)
        responder = ReplayResponder(recording)
        req = _civ(0x03, to=_RADIO_ADDR, frm=_CONTROLLER)
        # Latest wins: the second get_freq reply (7 074 000 Hz BCD).
        expected = _civ(0x03, _freq_bcd(7_074_000), to=_CONTROLLER, frm=_RADIO_ADDR)
        assert responder.respond(req) == expected
        assert responder.unanswered == []
        # No unsolicited traffic was enabled, so only the open marker (cmd 0x00)
        # may appear on the timeline.
        assert all(f[4] == 0x00 for _, f in responder.unsolicited())
