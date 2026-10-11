"""Recorder end-to-end: production IcomRadio through the proxy into MockIcomRadio.

Covers the MOR-3216 recorder contract: both directions and ports in the
JSONL, credential/token redaction, and the unsolicited timeline.  Replayer
assertions live in ``test_replay.py``.
"""

from __future__ import annotations

import asyncio
import json
import struct

from rigplane.core.auth import (
    build_conninfo_packet,
    build_login_packet,
    encode_credentials,
)
from rigplane.radio import IcomRadio

from rigplane_testkit.icom import (
    IcomSessionRecorder,
    MockIcomRadio,
    Record,
    ReplayResponder,
    Recording,
    redact_datagram,
)

from _perf_helpers import fast_connect

_USER = "recorderuser"
_PASS = "recorderpass9"  # 12-16 chars so the encoding span is distinctive


async def _record_session(
    tmp_path, *, unsolicited: bool
) -> tuple[MockIcomRadio, list[dict]]:
    """Drive a login + a few CI-V gets through the proxy; return JSONL rows."""
    mock = MockIcomRadio(
        username=_USER,
        password=_PASS,
        unsolicited_civ=unsolicited,
        unsolicited_interval_s=0.05,
    )
    await mock.start()
    out = tmp_path / "session.jsonl"
    recorder = IcomSessionRecorder("127.0.0.1", mock.control_port, out)
    await recorder.start()
    try:
        radio = IcomRadio(
            host="127.0.0.1",
            port=recorder.control_port,
            username=_USER,
            password=_PASS,
            model="IC-7610",
        )
        with fast_connect():
            await radio.connect()
        assert radio.connected
        await radio.get_freq()
        await radio.get_mode()
        await asyncio.sleep(0.2)  # let keepalives + unsolicited frames flow
        await radio.disconnect()
    finally:
        await recorder.stop()
        await mock.stop()
    rows = [json.loads(line) for line in out.read_text().splitlines()]
    return mock, rows


class TestRedactDatagram:
    """Pure-function redaction over real packet builders."""

    def test_login_packet_credentials_zeroed(self) -> None:
        pkt = build_login_packet(_USER, _PASS, sender_id=1, receiver_id=2)
        safe, labels = redact_datagram("control", "c2r", pkt)
        assert labels == ["login"]
        assert safe[0x40:0x70] == bytes(0x30)
        assert encode_credentials(_USER) not in safe
        assert encode_credentials(_PASS) not in safe
        # Untouched outside the credential span.
        assert safe[:0x40] == pkt[:0x40]

    def test_conninfo_token_and_username_zeroed(self) -> None:
        pkt = build_conninfo_packet(
            sender_id=1,
            receiver_id=2,
            username=_USER,
            token=0x12345678,
            tok_request=7,
            radio_name="IC-7610",
            mac_address=bytes(6),
        )
        safe, labels = redact_datagram("control", "c2r", pkt)
        assert set(labels) == {"token", "login"}
        assert safe[0x1C:0x20] == bytes(4)
        assert safe[0x60:0x70] == bytes(0x10)

    def test_login_response_token_zeroed(self) -> None:
        pkt = bytearray(0x60)
        struct.pack_into("<I", pkt, 0x1C, 0x12345678)
        safe, labels = redact_datagram("control", "r2c", bytes(pkt))
        assert labels == ["token"]
        assert safe[0x1C:0x20] == bytes(4)

    def test_plain_civ_packet_untouched(self) -> None:
        pkt = b"\x15\x00\x00\x00" + bytes(0x11)
        safe, labels = redact_datagram("civ", "c2r", pkt)
        assert safe == pkt
        assert labels == []


class TestRecordSession:
    async def test_directions_ports_and_redaction(self, tmp_path) -> None:
        mock, rows = await _record_session(tmp_path, unsolicited=False)

        assert {r["dir"] for r in rows} == {"c2r", "r2c"}
        ports = {r["port"] for r in rows}
        assert {"control", "civ"} <= ports

        # Timestamps are monotonic in file order.
        ts = [r["t"] for r in rows]
        assert ts == sorted(ts)

        # The login packet is on disk only in redacted form.
        login = next(
            r
            for r in rows
            if r["dir"] == "c2r" and len(bytes.fromhex(r["hex"])) == 0x80
        )
        assert login["redacted"] == ["login"]
        assert bytes.fromhex(login["hex"])[0x40:0x70] == bytes(0x30)

        # Neither credential nor the session token appears anywhere in the file.
        blob = "".join(r["hex"] for r in rows)
        assert encode_credentials(_USER).hex() not in blob
        assert encode_credentials(_PASS).hex() not in blob
        assert struct.pack("<I", mock.token).hex() not in blob
        # Token masking was actually exercised (token + conninfo + login rsp).
        token_rows = [r for r in rows if "token" in r.get("redacted", [])]
        assert len(token_rows) >= 3

    async def test_unsolicited_frames_on_timeline(self, tmp_path) -> None:
        _mock, rows = await _record_session(tmp_path, unsolicited=True)
        recording = Recording(
            [
                Record(
                    t=r["t"],
                    direction=r["dir"],
                    port=r["port"],
                    data=bytes.fromhex(r["hex"]),
                    redacted=tuple(r.get("redacted", ())),
                )
                for r in rows
            ]
        )
        responder = ReplayResponder(recording)
        timeline = list(responder.unsolicited())
        # The mock emits one cmd-0x00 frame on open plus periodic freq reports.
        assert len(timeline) >= 2
        assert [t for t, _ in timeline] == sorted(t for t, _ in timeline)
        assert all(f[4] in (0x00, 0x03) for _, f in timeline)
