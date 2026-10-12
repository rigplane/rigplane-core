"""Redaction gate tests (MOR-3325): planted leaks are found, never echoed."""

from __future__ import annotations

import json
from pathlib import Path

from testkit.test_recorder import _PASS, _USER, _record_session

from rigplane_testkit.icom.fixtures import main as fixtures_main
from rigplane_testkit.redaction_gate import Finding
from rigplane_testkit.redaction_gate import main as gate_main
from rigplane_testkit.redaction_gate import scan

_SECRET = "shack-pc-name-9"
_IP = "10.9.8.7"
_MAC = "aa:bb:cc:dd:ee:ff"


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


class TestByteFindings:
    def test_ipv4_both_byte_orders(self, tmp_path) -> None:
        fwd = b"\x00\x01\x02\x03" + bytes([10, 9, 8, 7]) + b"\x00\x00"
        rev = b"\x00\x01\x02\x03" + bytes([7, 8, 9, 10]) + b"\x00\x00"
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"hex": fwd.hex()}, {"hex": rev.hex()}])
        findings = scan([str(target)], ipv4=(_IP,))
        assert Finding(str(target), 1, "ipv4-bytes", 0) in findings
        assert Finding(str(target), 2, "ipv4-bytes", 0) in findings

    def test_mac_deny_and_ascii_run_in_hex(self, tmp_path) -> None:
        target = tmp_path / "leak.jsonl"
        _write_jsonl(
            target,
            [
                {"hex": (b"\x00\x01" + bytes.fromhex("aabbccddeeff") + b"\x00").hex()},
                {"hex": (b"\x00\x00" + _SECRET.encode() + b"\xff\x00").hex()},
                {"hex": (b"\x00\x00" + b"HELLO WORLD" + b"\x00\x00").hex()},
            ],
        )
        findings = scan([str(target)], mac=(_MAC,), deny=(_SECRET,))
        assert Finding(str(target), 1, "mac-bytes", 0) in findings
        assert Finding(str(target), 2, "deny-bytes", 0) in findings
        # ascii-run index is the run's byte offset in the decoded payload.
        assert Finding(str(target), 3, "ascii-run", 2) in findings


class TestScopeWaveExemption:
    def test_wave_frame_full_of_printables_not_flagged(self, tmp_path) -> None:
        frame = b"\xfe\xfe\xe0\x98\x27\x00" + b"ABCDEFGH" * 16 + b"\xfd"
        target = tmp_path / "wave.jsonl"
        _write_jsonl(target, [{"hex": (bytes(0x15) + frame).hex()}])
        assert scan([str(target)], ipv4=(_IP,), mac=(_MAC,), deny=(_SECRET,)) == []

    def test_printables_outside_wave_frame_flagged(self, tmp_path) -> None:
        blob = b"\xfe\xfe\xe0\x98\x27\x00" + b"ABCDEFGH" + b"\xfd"
        blob += b"SHACKPC1"  # printable run after the terminator
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"hex": blob.hex()}])
        assert [f.kind for f in scan([str(target)])] == ["ascii-run"]

    def test_wave_prefix_without_terminator_flagged(self, tmp_path) -> None:
        # A "27 00" prefix with no FD terminator is not a scope wave frame;
        # the printable run after it must stay flagged.
        blob = b"\xfe\xfe\xe0\x98\x27\x00" + b"SHACKPC1"
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"hex": blob.hex()}])
        assert [f.kind for f in scan([str(target)])] == ["ascii-run"]

    def test_wave_prefix_before_next_frame_flagged(self, tmp_path) -> None:
        # The first FD after a "27 00" prefix may belong to the NEXT
        # frame; that frame's FD must not exempt this prefix's run.
        blob = b"\xfe\xfe\xe0\x98\x27\x00" + b"SHACKPC1"
        blob += b"\xfe\xfe\xe0\x98\x03\x00\xfd"
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"hex": blob.hex()}])
        assert [f.kind for f in scan([str(target)])] == ["ascii-run"]

    def test_wave_prefix_before_another_wave_frame_flagged(self, tmp_path) -> None:
        # Same shape with a scope wave frame as the follower: only a
        # prefix whose OWN FD comes first exempts anything.
        blob = b"\xfe\xfe\xe0\x98\x27\x00" + b"SHACKPC1"
        blob += b"\xfe\xfe\xe0\x98\x27\x00\xfd"
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"hex": blob.hex()}])
        assert [f.kind for f in scan([str(target)])] == ["ascii-run"]


class TestTextFindings:
    def test_deny_string_in_plain_text(self, tmp_path) -> None:
        target = tmp_path / "leak.jsonl"
        _write_jsonl(target, [{"note": f"client host {_SECRET} logged"}])
        assert scan([str(target)], deny=(_SECRET,)) == [
            Finding(str(target), 1, "deny-text", 0)
        ]

    def test_ipv4_text_and_dotted_quad(self, tmp_path) -> None:
        target = tmp_path / "leak.jsonl"
        _write_jsonl(
            target,
            [
                {"note": f"radio at {_IP}"},
                {"note": "peer 10.1.2.3"},
                {"note": "127.0.0.1 0.0.0.0 192.0.2.4 198.51.100.7 203.0.113.9"},
            ],
        )
        findings = scan([str(target)], ipv4=(_IP,))
        assert Finding(str(target), 1, "ipv4-text", 0) in findings
        assert Finding(str(target), 1, "dotted-quad", 0) in findings
        assert Finding(str(target), 2, "dotted-quad", 0) in findings
        # loopback, wildcard and the RFC 5737 documentation ranges pass
        assert not [f for f in findings if f.line == 3]


class TestCleanExtraction:
    async def test_extracted_simulator_session_passes(self, tmp_path) -> None:
        await _record_session(tmp_path, unsolicited=False)
        out = tmp_path / "out"
        fixtures_main(
            ["extract", "--in", str(tmp_path / "session.jsonl"), "--out-dir", str(out)]
        )
        paths = [str(p) for p in sorted(out.iterdir())]
        assert paths  # the extraction produced files
        assert scan(paths, ipv4=(_IP,), mac=(_MAC,), deny=(_USER, _PASS, _SECRET)) == []


class TestCli:
    def test_repeatable_flags_do_not_swallow_paths(self, tmp_path, capsys) -> None:
        f1 = tmp_path / "one.jsonl"
        f2 = tmp_path / "two.jsonl"
        _write_jsonl(f1, [{"hex": bytes([10, 9, 8, 7]).hex()}])
        _write_jsonl(f2, [{"note": "peer 10.1.2.3"}])
        # One value per flag: a value directly before the positional paths
        # must not swallow them, and repeating a flag keeps every entry.
        code = gate_main(
            ["--ipv4", _IP, "--ipv4", "10.1.2.3", "--mac", _MAC, str(f1), str(f2)]
        )
        out = capsys.readouterr()
        assert code == 1
        assert f"{f1}:1 ipv4-bytes#0" in out.out
        # Index 1 is the repeated --ipv4 entry, proving both were kept.
        assert f"{f2}:1 ipv4-text#1" in out.out
        assert f"{f2}:1 dotted-quad#0" in out.out

    def test_paths_printed_relative_to_cwd(self, tmp_path, monkeypatch, capsys) -> None:
        dirty = tmp_path / "dirty.jsonl"
        _write_jsonl(dirty, [{"note": f"host {_SECRET}"}])
        deny_file = tmp_path / "deny.txt"
        deny_file.write_text(_SECRET + "\n")
        monkeypatch.chdir(tmp_path)
        assert gate_main(["--deny-file", str(deny_file), str(dirty)]) == 1
        # The scanned path is printed relative to the CWD, not verbatim:
        # a deny entry inside a directory name must not surface.
        assert capsys.readouterr().out.splitlines()[0] == "dirty.jsonl:1 deny-text#0"

    def test_exit_codes_and_no_secret_echo(self, tmp_path, capsys) -> None:
        dirty = tmp_path / "dirty.jsonl"
        _write_jsonl(
            dirty, [{"note": f"host {_SECRET}"}, {"hex": _SECRET.encode().hex()}]
        )
        deny_file = tmp_path / "deny.txt"
        deny_file.write_text(_SECRET + "\n")
        assert gate_main(["--deny-file", str(deny_file), str(dirty)]) == 1
        out = capsys.readouterr()
        assert f"{dirty}:1 deny-text#0" in out.out
        assert f"{dirty}:2 deny-bytes#0" in out.out
        assert _SECRET not in out.out and _SECRET not in out.err
        # A Finding carries no matched value, so its repr is safe too.
        assert _SECRET not in repr(scan([str(dirty)], deny=(_SECRET,)))
        clean = tmp_path / "clean.jsonl"
        _write_jsonl(clean, [{"note": "nothing here"}])
        assert gate_main(["--deny-file", str(deny_file), str(clean)]) == 0
        assert capsys.readouterr().out == ""
