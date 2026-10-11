"""Recorder end-to-end: production IcomRadio through the proxy into MockIcomRadio.

Covers the MOR-3216 recorder contract: both directions and ports in the
JSONL, credential/token redaction, and the unsolicited timeline.  Replayer
assertions live in ``test_replay.py``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import socket
import struct

from rigplane.core.auth import (
    build_conninfo_packet,
    build_login_packet,
    encode_credentials,
    parse_status_response,
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

_GUID_SALT = b"rigplane-testkit-guid:"


def _free_port_run(count: int = 3) -> int:
    """Find a UDP port whose run of ``count`` ports (itself included) is free."""
    for _ in range(100):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            base = probe.getsockname()[1]
        held: list[socket.socket] = []
        try:
            for offset in range(count):
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.bind(("127.0.0.1", base + offset))
                held.append(sock)
        except OSError:
            for sock in held:
                sock.close()
            continue  # occupied run; try another base
        for sock in held:
            sock.close()
        return base
    raise RuntimeError("no free consecutive UDP port run found")


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
        assert set(labels) == {"token", "login", "guid", "name"}
        assert safe[0x1C:0x20] == bytes(4)
        assert safe[0x60:0x70] == bytes(0x10)

    def test_login_response_token_zeroed(self) -> None:
        pkt = bytearray(0x60)
        struct.pack_into("<I", pkt, 0x1C, 0x12345678)
        safe, labels = redact_datagram("control", "r2c", bytes(pkt))
        assert labels == ["token"]
        assert safe[0x1C:0x20] == bytes(4)

    def test_radio_conninfo_token_zeroed(self) -> None:
        """Radio→client conninfo keeps the token at 0x1C (protocol.md §
        Conninfo Packet: conninfo is sent by both radio and client;
        ``build_conninfo_packet`` in ``src/rigplane/core/auth.py`` packs it
        little-endian at 0x1C..0x20)."""
        pkt = build_conninfo_packet(
            sender_id=2,
            receiver_id=1,
            username=_USER,
            token=0x12345678,
            tok_request=7,
            radio_name="IC-7610",
            mac_address=bytes(6),
        )
        safe, labels = redact_datagram("control", "r2c", pkt)
        assert labels == ["token", "guid", "name"]
        assert struct.pack("<I", 0x12345678) not in safe
        assert safe[0x1C:0x20] == bytes(4)

    def test_redact_datagram_guid(self) -> None:
        """The 0x90 GUID/MAC area (0x20..0x30, protocol.md § Conninfo
        Packet) is replaced by sha256("rigplane-testkit-guid:" + span)[:16]
        in both directions; an all-zero span stays zero with no label."""
        pkt = build_conninfo_packet(
            sender_id=2,
            receiver_id=1,
            username=_USER,
            token=0x12345678,
            tok_request=7,
            radio_name="IC-7610",
            mac_address=bytes(6),
        )
        for direction in ("r2c", "c2r"):
            safe, labels = redact_datagram("control", direction, pkt)
            assert "guid" in labels
            original = pkt[0x20:0x30]
            expected = hashlib.sha256(_GUID_SALT + original).digest()[:16]
            assert safe[0x20:0x30] == expected
            assert safe[0x20:0x30] != original

        zero = bytearray(0x90)
        struct.pack_into("<I", zero, 0x00, 0x90)
        safe, labels = redact_datagram("control", "r2c", bytes(zero))
        assert "guid" not in labels
        assert safe[0x20:0x30] == bytes(16)

    def test_redact_datagram_device_name_zeroed(self) -> None:
        """The 0x90 device-name field (0x40..0x60, protocol.md § Conninfo
        Packet; ``build_conninfo_packet`` packs ``radio_name`` there as
        plain ASCII) is user-configurable text on a real radio and is
        zeroed in both directions."""
        pkt = build_conninfo_packet(
            sender_id=2,
            receiver_id=1,
            username=_USER,
            token=0x12345678,
            tok_request=7,
            radio_name="SHACK-IC7610",
            mac_address=bytes(6),
        )
        for direction in ("r2c", "c2r"):
            safe, labels = redact_datagram("control", direction, pkt)
            assert "name" in labels
            assert safe[0x40:0x60] == bytes(0x20)
            assert b"SHACK-IC7610" not in safe

    def test_status_packet_token_slot_zeroed(self) -> None:
        """The 0x50 status packet shares the control header (§ Status Packet:
        error 0x30, CI-V port 0x42, audio port 0x46, read by
        ``parse_status_response``); the round-1 probe carried live token
        bytes in its 0x1C slot, and masking must keep the ports parseable."""
        pkt = bytearray(0x50)
        struct.pack_into("<I", pkt, 0x00, 0x50)
        struct.pack_into("<I", pkt, 0x1C, 0x12345678)
        struct.pack_into(">H", pkt, 0x42, 50002)
        struct.pack_into(">H", pkt, 0x46, 50003)
        safe, labels = redact_datagram("control", "r2c", bytes(pkt))
        assert labels == ["token"]
        assert struct.pack("<I", 0x12345678) not in safe
        status = parse_status_response(safe)
        assert (status.civ_port, status.audio_port) == (50002, 50003)

    def test_token_reply_zeroed(self) -> None:
        """A 0x40 token reply is radio-originated with the reply marker
        0x14 = 0x02 (requests use 0x01) and the token at 0x1C (§ Token Ack);
        the request-only gate let it reach disk untouched."""
        pkt = bytearray(0x40)
        struct.pack_into("<I", pkt, 0x00, 0x40)
        pkt[0x14] = 0x02  # reply marker, not the 0x01 request marker
        pkt[0x15] = 0x02  # token ack
        struct.pack_into("<I", pkt, 0x1C, 0x12345678)
        safe, labels = redact_datagram("control", "r2c", bytes(pkt))
        assert labels == ["token"]
        assert struct.pack("<I", 0x12345678) not in safe

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

    async def test_fixed_listen_ports(self, tmp_path) -> None:
        """listen_port=N binds control=N, civ=N+1, audio=N+2, and a full
        client session through the pinned ports still logs in."""
        base = _free_port_run()
        mock = MockIcomRadio(username=_USER, password=_PASS)
        await mock.start()
        out = tmp_path / "fixed.jsonl"
        recorder = IcomSessionRecorder(
            "127.0.0.1", mock.control_port, out, listen_port=base
        )
        await recorder.start()
        try:
            assert recorder.control_port == base
            assert recorder.civ_port == base + 1
            assert recorder.audio_port == base + 2
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
            await radio.disconnect()
        finally:
            await recorder.stop()
            await mock.stop()

    async def test_fixed_upstream_ports(self, tmp_path) -> None:
        """upstream_port=N binds the CI-V upstream socket to N and the
        audio upstream to N+1; the control upstream stays ephemeral."""
        base = _free_port_run()
        recorder = IcomSessionRecorder(
            "127.0.0.1", 1, tmp_path / "upstream.jsonl", upstream_port=base
        )
        await recorder.start()
        try:
            assert recorder._channels["civ"].upstream_port == base
            assert recorder._channels["audio"].upstream_port == base + 1
            control = recorder._channels["control"].upstream_port
            assert control != 0
            assert control not in (base, base + 1)
        finally:
            await recorder.stop()

    async def test_unroutable_radio_target_no_send_errors(self, tmp_path) -> None:
        """Under default settings the radio-facing upstream sockets bind the
        wildcard address, never ``listen_host``: a recorder targeting an
        unroutable host (TEST-NET-1) forwards one client packet with no
        OSError and ``send_errors == 0`` — binding the upstream to the
        (loopback) listen host is the regression this pins."""
        loop = asyncio.get_running_loop()
        out = tmp_path / "unroutable.jsonl"
        recorder = IcomSessionRecorder("192.0.2.1", 50001, out)
        await recorder.start()
        client_transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        try:
            client_transport.sendto(
                b"\x00" * 0x40, ("127.0.0.1", recorder.control_port)
            )
            await asyncio.sleep(0.1)
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            assert len(rows) == 1  # recorded and forwarded, not dropped
            control = recorder._channels["control"]
            assert control.dropped == 0
            assert control.send_errors == 0
        finally:
            await recorder.stop()
            client_transport.close()

    async def test_guid_pseudonymised_consistently(self, tmp_path) -> None:
        """Both 0x90 records — the radio's conninfo and the client's echo —
        carry the same 16-byte sha256 pseudonym instead of the radio's GUID
        bytes, and both records are marked "guid"."""
        _mock, rows = await _record_session(tmp_path, unsolicited=False)
        conninfos = [r for r in rows if len(bytes.fromhex(r["hex"])) == 0x90]
        radio_rows = [r for r in conninfos if r["dir"] == "r2c"]
        echo_rows = [r for r in conninfos if r["dir"] == "c2r"]
        assert radio_rows and echo_rows
        radio_guid = bytes.fromhex(radio_rows[0]["hex"])[0x20:0x30]
        echo_guid = bytes.fromhex(echo_rows[0]["hex"])[0x20:0x30]
        # The simulator's _guid_conninfo writes bytes(range(0x01, 0x11)).
        radio_original = bytes(range(0x01, 0x11))
        expected = hashlib.sha256(_GUID_SALT + radio_original).digest()[:16]
        assert radio_guid == echo_guid
        assert radio_guid == expected
        assert radio_guid != radio_original
        for row in (radio_rows[0], echo_rows[0]):
            assert "guid" in row["redacted"]


class _SinkProto(asyncio.DatagramProtocol):
    """Collects every datagram it receives (a fake radio endpoint)."""

    def __init__(self) -> None:
        self.received: list[bytes] = []

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.received.append(data)


class TestConninfoPortRewrite:
    async def test_client_conninfo_ports_rewritten_to_upstream(self, tmp_path) -> None:
        """The client's declared CI-V/audio ports (0x7C/0x80, big-endian
        u32 — protocol.md § Conninfo Packet) are rewritten in the forwarded
        copy to the recorder's pre-bound upstream ports; the JSONL record
        of the same packet keeps the client's original bytes."""
        loop = asyncio.get_running_loop()
        radio_transport, radio_proto = await loop.create_datagram_endpoint(
            _SinkProto, local_addr=("127.0.0.1", 0)
        )
        client_transport, _ = await loop.create_datagram_endpoint(
            asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
        )
        radio_port = radio_transport.get_extra_info("sockname")[1]
        out = tmp_path / "rewrite.jsonl"
        recorder = IcomSessionRecorder("127.0.0.1", radio_port, out)
        await recorder.start()
        try:
            orig = build_conninfo_packet(
                sender_id=1,
                receiver_id=2,
                username=_USER,
                token=0x12345678,
                tok_request=7,
                radio_name="IC-7610",
                mac_address=bytes(6),
                civ_local_port=45555,
                audio_local_port=45556,
            )
            client_transport.sendto(orig, ("127.0.0.1", recorder.control_port))
            for _ in range(100):
                if radio_proto.received:
                    break
                await asyncio.sleep(0.02)
            forwarded = radio_proto.received[0]
            civ_upstream = recorder._channels["civ"].upstream_port
            audio_upstream = recorder._channels["audio"].upstream_port
            assert civ_upstream != 0 and audio_upstream != 0  # bound at start
            assert struct.unpack_from(">I", forwarded, 0x7C)[0] == civ_upstream
            assert struct.unpack_from(">I", forwarded, 0x80)[0] == audio_upstream

            rows = [json.loads(line) for line in out.read_text().splitlines()]
            conn = next(
                r
                for r in rows
                if r["dir"] == "c2r" and len(bytes.fromhex(r["hex"])) == 0x90
            )
            recorded = bytes.fromhex(conn["hex"])
            assert struct.unpack_from(">I", recorded, 0x7C)[0] == 45555
            assert struct.unpack_from(">I", recorded, 0x80)[0] == 45556
        finally:
            await recorder.stop()
            client_transport.close()
            radio_transport.close()


class TestUpstreamOpenGuard:
    async def test_duplicate_status_keeps_first_remote(self, tmp_path, caplog) -> None:
        """A second advertised port must not move an already-learned
        upstream remote (first advertisement wins); it is ignored and
        logged."""
        rec = IcomSessionRecorder("127.0.0.1", 1, tmp_path / "guard.jsonl")
        ch = rec._channels["civ"]
        await ch.open_upstream("127.0.0.1", 0)
        try:
            with caplog.at_level(logging.INFO, logger="rigplane_testkit.icom.recorder"):
                ch.set_upstream_addr("127.0.0.1", 50002)
                ch.set_upstream_addr("127.0.0.1", 50004)  # already learned
                assert ch.upstream_addr == ("127.0.0.1", 50002)
            assert "50004" in caplog.text
        finally:
            ch.close()
