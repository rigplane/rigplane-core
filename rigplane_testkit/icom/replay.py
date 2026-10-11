"""Replay side of the session recorder: answer CI-V from a recording.

Pure, socket-free.  :class:`Recording` loads the JSONL written by
:class:`~rigplane_testkit.icom.recorder.IcomSessionRecorder`;
:class:`ReplayResponder` keys recorded CI-V request → reply pairs and answers
lookups, and reports the frames the radio sent without a matching request as
the unsolicited timeline.  MOR-3217 mounts this in the simulator.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

_CIV_HEADER_SIZE = 0x15  # bytes before the CI-V frame in a DATA packet
_CIV_PREAMBLE = b"\xfe\xfe"
_CIV_TERM = 0xFD
_CIV_ACK = 0xFB
_CIV_NAK = 0xFA


@dataclass(frozen=True, slots=True)
class Record:
    """One recorded datagram (a parsed JSONL line)."""

    t: float  # seconds since recording start
    direction: str  # "c2r" | "r2c"
    port: str  # "control" | "civ" | "audio"
    data: bytes
    redacted: tuple[str, ...] = ()


def extract_civ_frame(packet: bytes) -> bytes | None:
    """Pull the CI-V frame out of a DATA packet; None if not a CI-V carrier."""
    if len(packet) <= _CIV_HEADER_SIZE:
        return None
    datalen = struct.unpack_from("<H", packet, 0x11)[0]
    frame = packet[_CIV_HEADER_SIZE : _CIV_HEADER_SIZE + datalen]
    if len(frame) < 6 or frame[:2] != _CIV_PREAMBLE or frame[-1] != _CIV_TERM:
        return None
    return frame


def request_key(frame: bytes) -> tuple[int, int | None, bytes]:
    """Lookup key for a CI-V request: (cmd, subcmd, 2-byte data prefix).

    Commands without a subcommand (e.g. ``05`` set-frequency) fold their
    first payload bytes into the data-prefix slot, so two requests of the
    same command whose payloads share the first two bytes collapse onto one
    key.  Acceptable for replies: such commands are answered by a bare
    ACK/NAK whose bytes are identical for every request of that command, so
    the latest recording wins and a collided entry still answers correctly.
    """
    cmd = frame[4]
    payload = frame[5:-1]
    sub = payload[0] if payload else None
    rest = payload[1:3] if len(payload) > 1 else b""
    return (cmd, sub, rest)


class Recording:
    """A loaded recorder JSONL file."""

    def __init__(self, records: list[Record]) -> None:
        self.records = records

    @classmethod
    def load(cls, path: str | Path) -> Recording:
        """Parse a recorder JSONL file into records (order preserved)."""
        records: list[Record] = []
        with Path(path).open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                records.append(
                    Record(
                        t=float(obj["t"]),
                        direction=obj["dir"],
                        port=obj["port"],
                        data=bytes.fromhex(obj["hex"]),
                        redacted=tuple(obj.get("redacted", ())),
                    )
                )
        return cls(records)

    def civ_frames(
        self, direction: str | None = None
    ) -> Iterator[tuple[float, str, bytes]]:
        """Yield ``(t, direction, frame)`` for every CI-V-carrying record."""
        for rec in self.records:
            if rec.port != "civ":
                continue
            if direction is not None and rec.direction != direction:
                continue
            frame = extract_civ_frame(rec.data)
            if frame is not None:
                yield (rec.t, rec.direction, frame)


@dataclass
class ReplayResponder:
    """Answers CI-V requests with the bytes the radio actually answered.

    Built from a :class:`Recording`: walk the CI-V port in time order, match
    each radio frame to a pending request it plausibly answers, and keep the
    reply under the request's :func:`request_key` — latest recording wins.
    Radio frames that match no pending request are the unsolicited stream.

    Matching: a data reply pairs with the earliest pending request that
    shares its command, and its subcommand when the request has one; a bare
    ACK/NAK (0xFB/0xFA stands in the command slot and names no command)
    pairs with the most recent pending request — the client awaits each
    reply before sending the next, which keeps a request whose reply was
    lost from stealing later answers.

    Heuristic limits: an unsolicited frame sharing a command (and
    subcommand, when present) with an in-flight request is attributed as
    that request's reply (echo-free pairing is not recoverable from the wire
    alone), and with several requests outstanding at once an ACK's true
    owner is not recoverable — attribution picks the most recent, harmless
    while ACK frames are byte-identical.
    """

    recording: Recording
    answers: dict[tuple[int, int | None, bytes], bytes] = field(default_factory=dict)
    unanswered: list[bytes] = field(default_factory=list)
    _unsolicited: list[tuple[float, bytes]] = field(default_factory=list)

    def __post_init__(self) -> None:
        pending: list[bytes] = []  # client frames awaiting a reply, in order
        for _t, direction, frame in self.recording.civ_frames():
            if direction == "c2r":
                pending.append(frame)
                continue
            match = self._match(pending, frame)
            if match is not None:
                pending.remove(match)
                self.answers[request_key(match)] = frame
            else:
                self._unsolicited.append((_t, frame))

    @staticmethod
    def _match(pending: list[bytes], reply: bytes) -> bytes | None:
        if not pending:
            return None
        if reply[4] in (_CIV_ACK, _CIV_NAK):
            # ACK/NAK echoes no command; pair with the most recent request.
            return pending[-1]
        reply_sub = request_key(reply)[1]
        for req in pending:
            cmd, sub, _ = request_key(req)
            if cmd == reply[4] and (sub is None or sub == reply_sub):
                return req
        return None

    def respond(self, request_frame: bytes) -> bytes | None:
        """Return the recorded reply for a request frame, else None.

        Misses are appended to :attr:`unanswered`.
        """
        reply = self.answers.get(request_key(request_frame))
        if reply is None:
            self.unanswered.append(request_frame)
        return reply

    def unsolicited(self) -> Iterator[tuple[float, bytes]]:
        """Yield ``(t, frame)`` for radio frames with no matching request."""
        yield from self._unsolicited
