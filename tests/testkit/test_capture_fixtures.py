"""Fixture extraction from recorded Icom LAN sessions (MOR-3309).

The session is a real UDP exchange of the production ``IcomRadio`` client
against ``MockIcomRadio`` through ``IcomSessionRecorder``, recorded by
``tests/testkit/test_recorder.py: _record_session`` (imported, not copied).
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

from testkit.test_recorder import _record_session

from rigplane_testkit.icom.fixtures import (
    audio_headers,
    cadence,
    civ_frames,
    control_structure,
    load_records,
)
from rigplane_testkit.icom.fixtures import main as fixtures_main
from rigplane_testkit.icom.replay import Record

_OUT_FILES = (
    "civ_frames.jsonl",
    "control_structure.jsonl",
    "cadence.json",
    "audio_headers.jsonl",
)


def _rec(t: float, direction: str, port: str, data: bytes) -> Record:
    return Record(t=t, direction=direction, port=port, data=data)


def _packet(
    size: int, ptype: int = 0, reqtype: int | None = None, flag: int | None = None
) -> bytes:
    pkt = bytearray(size)
    pkt[0x04] = ptype  # u16 LE packet type
    if flag is not None:
        pkt[0x10] = flag  # ping request/reply flag byte
    if reqtype is not None:
        pkt[0x15] = reqtype
    return bytes(pkt)


def _civ_packet(frame: bytes) -> bytes:
    """A DATA datagram carrying ``frame`` after the 0x15-byte LAN header."""
    pkt = bytearray(0x15 + len(frame))
    pkt[0x11:0x13] = len(frame).to_bytes(2, "little")
    pkt[0x15:] = frame
    return bytes(pkt)


def _cli(in_path: Path, out_dir: Path, *extra: str) -> None:
    fixtures_main(["extract", "--in", str(in_path), "--out-dir", str(out_dir), *extra])


def _outputs(out_dir: Path) -> dict[str, bytes]:
    return {name: (out_dir / name).read_bytes() for name in _OUT_FILES}


class TestRecordedSession:
    """Extraction over a session recorded through the proxy."""

    async def test_civ_frames_carry_identity_exchange(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        frames = civ_frames(load_records(tmp_path / "session.jsonl"))
        assert frames
        # Only the CI-V frame survives — the 0x15-byte LAN header is dropped.
        for f in frames:
            assert f["frame"].startswith("fefe")
            assert f["frame"].endswith("fd")
            assert f["t"] == round(f["t"], 3)
        by_dir = {"c2r": [], "r2c": []}
        for f in frames:
            by_dir[f["dir"]].append(bytes.fromhex(f["frame"]))
        # The connect-time identity read (cmd 0x19 sub 0x00) and its answer.
        assert any(fr[4] == 0x19 and fr[5] == 0x00 for fr in by_dir["c2r"])
        assert any(fr[4] == 0x19 and fr[5] == 0x00 for fr in by_dir["r2c"])

    async def test_control_structure_login_no_payload(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        structure = control_structure(load_records(tmp_path / "session.jsonl"))
        # The 0x80-byte login is visible as structure only.
        assert any(e["dir"] == "c2r" and e["len"] == 0x80 for e in structure)
        for entry in structure:
            assert set(entry) == {"t", "dir", "len", "type", "reqtype"}

    async def test_cadence_counts_session_pings(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        result = cadence(load_records(tmp_path / "session.jsonl"))
        pings = result["pings"]
        # At least one ping request and its reply passed through the proxy;
        # each key also records which side initiated (request vs reply).
        assert sum(p["count"] for p in pings.values()) >= 2
        assert any(":c2r:" in key for key in pings)
        assert any(":r2c:" in key for key in pings)
        assert any(key.endswith(":request") for key in pings)
        assert any(key.endswith(":reply") for key in pings)
        assert isinstance(result["token_renewals"], list)
        assert set(result["audio_lengths"]) == {"c2r", "r2c"}

    async def test_cli_deterministic_and_sorted(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        src = tmp_path / "session.jsonl"
        _cli(src, tmp_path / "out1")
        _cli(src, tmp_path / "out2")
        assert _outputs(tmp_path / "out1") == _outputs(tmp_path / "out2")
        for name in _OUT_FILES:  # sorted keys, one record per line
            for line in (tmp_path / "out1" / name).read_text().splitlines():
                assert list(json.loads(line)) == sorted(json.loads(line))

    async def test_gzip_input_matches_plain(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        src = tmp_path / "session.jsonl"
        gz = tmp_path / "session.jsonl.gz"
        gz.write_bytes(gzip.compress(src.read_bytes()))
        _cli(src, tmp_path / "plain")
        _cli(gz, tmp_path / "gzipped")
        assert _outputs(tmp_path / "plain") == _outputs(tmp_path / "gzipped")


class TestCadenceAndAudio:
    """Cadence statistics and audio headers over fabricated records."""

    def test_ping_stats_split_by_request_and_reply(self) -> None:
        # Same direction, mixed flags: the kind must come from the flag
        # byte at 0x10, not from the direction (a radio-originated ping
        # the client answers is a c2r reply).
        request = _packet(0x15, 0x07, flag=0x00)
        reply = _packet(0x15, 0x07, flag=0x01)
        records = [
            *[_rec(t, "c2r", "control", request) for t in (0.0, 0.5, 1.0, 1.5)],
            *[_rec(t, "c2r", "control", reply) for t in (0.2, 0.7, 1.2, 1.7)],
        ]
        pings = cadence(records)["pings"]
        assert set(pings) == {"control:c2r:request", "control:c2r:reply"}
        for key in ("control:c2r:request", "control:c2r:reply"):
            assert pings[key]["count"] == 4
            assert pings[key]["median_s"] == 0.5

    def test_pings_renews_retransmits_histogram(self) -> None:
        ping = _packet(0x15, 0x07)
        records = [
            *[_rec(t, "c2r", "control", ping) for t in (0.0, 0.5, 1.0, 2.0)],
            _rec(3.0, "c2r", "control", _packet(0x40, reqtype=0x05)),
            _rec(3.2, "r2c", "control", _packet(0x40)),
            _rec(4.0, "c2r", "control", _packet(0x40, reqtype=0x05)),
            _rec(0.1, "c2r", "civ", _packet(0x10, 0x01)),
            _rec(0.2, "r2c", "audio", bytes(0x500)),
            _rec(0.3, "r2c", "audio", bytes(0x500)),
            _rec(5.0, "r2c", "civ", _packet(0x15, 0x07, flag=0x01)),
        ]
        result = cadence(records)
        stats = result["pings"]["control:c2r:request"]
        # Intervals [0.5, 0.5, 1.0]: linear-interpolated p95 = 0.95.
        assert (stats["count"], stats["median_s"]) == (4, 0.5)
        assert (stats["p95_s"], stats["max_s"], stats["seconds"]) == (0.95, 1.0, 2.0)
        # A single reply ping has no interval statistics.
        lone = result["pings"]["civ:r2c:reply"]
        assert lone["count"] == 1 and lone["median_s"] is None
        assert result["token_renewals"] == [
            {"t": 3.0, "answered_after_s": 0.2},
            {"t": 4.0, "answered_after_s": None},
        ]
        assert result["retransmit_requests"] == {"civ:c2r": 1}
        assert result["audio_lengths"] == {"c2r": {}, "r2c": {"1280": 2}}

    def test_civ_window_bounds_are_inclusive(self) -> None:
        # Both bounds are inclusive and both are pinned: t=1.0 is cut
        # only by t_from, t=4.0 only by t_to (the docstring states it).
        records = [
            _rec(t, "c2r", "civ", _civ_packet(b"\xfe\xfe\xe0\x98\x19\x00\xfd"))
            for t in (1.0, 2.0, 3.0, 4.0)
        ]
        assert [f["t"] for f in civ_frames(records, t_from=2.0, t_to=3.0)] == [2.0, 3.0]

    def test_audio_headers_truncated_and_limited_per_direction(self) -> None:
        data = bytes(range(0x40))
        records = [
            _rec(i * 0.01, direction, "audio", data)
            for i in range(4)
            for direction in ("c2r", "r2c")
        ]
        headers = audio_headers(records, limit=3)
        assert headers[0] == {"t": 0.0, "dir": "c2r", "hex": data[:0x18].hex()}
        assert len([h for h in headers if h["dir"] == "c2r"]) == 3
        assert len([h for h in headers if h["dir"] == "r2c"]) == 3
