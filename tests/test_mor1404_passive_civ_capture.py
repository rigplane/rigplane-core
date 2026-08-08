from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path

import pytest

from support import mor1404_passive_civ_capture as evidence


# fmt: off
def _line(row: dict[str, object]) -> bytes:
    return (json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n").encode()
def _events(*, outbound: bool = False, transition: bool = False, transport: str = "uart") -> list[dict[str, object]]:
    rows: list[dict[str, object]] = [
        {"kind": "line", "timestamp_ns": 100, "line": "dtr", "state": False},
        {"kind": "line", "timestamp_ns": 100, "line": "rts", "state": False},
        {"kind": "action", "timestamp_ns": 101, "name": "capture_start"},
        {"kind": "byte", "timestamp_ns": 102, "transport": transport, "direction": "radio_to_controller", "value": 0x99},
    ]
    for offset, value in enumerate(bytes.fromhex("FE FE E0 94 07 01 FD"), 103):
        rows.append({"kind": "byte", "timestamp_ns": offset, "transport": transport, "direction": "radio_to_controller", "value": value})
    rows.extend({"kind": "action", "timestamp_ns": timestamp, "name": name} for timestamp, name in ((111, "ab_1"), (112, "tune_1"), (113, "ab_2"), (114, "tune_2")))
    if outbound:
        rows.append({"kind": "byte", "timestamp_ns": 115, "transport": transport, "direction": "controller_to_radio", "value": 0xFE})
    if transition:
        rows.append({"kind": "line", "timestamp_ns": 116, "line": "dtr", "state": True})
    rows.extend([{"kind": "byte", "timestamp_ns": 117, "transport": transport, "direction": "radio_to_controller", "value": 0xFE}, {"kind": "action", "timestamp_ns": 118, "name": "capture_end"}])
    return [row | {"seq": seq} for seq, row in enumerate(rows)]
def _package(tmp_path: Path, events: list[dict[str, object]], source_kind: str = "logic_tap") -> tuple[Path, Path]:
    artifact = tmp_path / "capture.sal"
    artifact.write_bytes(b"immutable-native-analyzer-artifact")
    digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
    actions = [{"name": row["name"], "seq": row["seq"], "timestamp_ns": row["timestamp_ns"]} for row in events if row["kind"] == "action"]
    byte_count = sum(row["kind"] == "byte" for row in events)
    start_ns, end_ns = events[0]["timestamp_ns"], events[-1]["timestamp_ns"]
    package = {
        "schema": evidence.PACKAGE_SCHEMA,
        "source": {"kind": source_kind, "artifact": artifact.name, "sha256": digest, "size": artifact.stat().st_size},
        "capture": {"tool": "Saleae Logic", "model": "Logic Pro 8", "version": "2.4.14", "mapping": {"radio_to_controller": "digital-0", "controller_to_radio": "digital-1", "dtr": "digital-2", "rts": "digital-3"}, "sample_rate_hz": 1_000_000, "baud": 115200, "data_bits": 8, "parity": "N", "stop_bits": 1},
        "timing": {"start_ns": start_ns, "end_ns": end_ns},
        "completion": {"complete": True, "truncated": False, "dropped_records": 0, "timestamp_gaps": 0, "records": len(events), "bytes": byte_count, "channels": {"radio_to_controller": True, "controller_to_radio": True, "dtr": True, "rts": True}},
        "actions": actions,
    }
    package_path = tmp_path / "package.json"
    package_path.write_bytes(_line(package))
    header = {"kind": "session", "schema": evidence.RAW_SCHEMA, "source_sha256": digest, "source_size": artifact.stat().st_size, "start_ns": start_ns, "end_ns": end_ns}
    body = b"".join(_line(row) for row in [header, *events])
    footer = {"kind": "footer", "schema": evidence.RAW_SCHEMA, "source_sha256": digest, "records": len(events), "bytes": byte_count, "dropped_records": 0, "timestamp_gaps": 0, "truncated": False, "complete": True, "channels": package["completion"]["channels"], "stream_sha256": hashlib.sha256(body).hexdigest()}
    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_bytes(body + _line(footer))
    return package_path, raw_path
def test_offline_evidence_preserves_raw_and_adjudicates_selector(tmp_path: Path) -> None:
    for source_kind, transport in (("logic_tap", "uart"), ("inline_usb", "usb")):
        package, raw = _package(tmp_path, _events(transport=transport), source_kind)
        result = evidence.adjudicate(package, raw)
        assert result["safety"] == {"verdict": "PASS", "outbound_count": 0, "modem_transition_count": 0}
        assert result["selector_07_present"] is True
        assert [frame["hex"] for frame in result["inbound_07"]] == ["fefee0940701fd"]
        assert result["outbound_payloads"] == []
        assert result["modem_transitions"] == []
        assert result["raw_events"] == _events(transport=transport)
        assert result["unframed_byte_seqs"] == [3, len(_events()) - 2]
def test_any_outbound_or_modem_transition_fails_safety(tmp_path: Path) -> None:
    package, raw = _package(tmp_path, _events(outbound=True, transition=True))
    result = evidence.adjudicate(package, raw)
    assert result["safety"]["verdict"] == "FAIL"
    assert [(row["direction"], row["value"]) for row in result["outbound_payloads"]] == [("controller_to_radio", 0xFE)]
    assert result["modem_transitions"] == [{"line": "dtr", "seq": 16, "timestamp_ns": 116, "from": False, "to": True}]
@pytest.mark.parametrize(("mutate", "message"), [(lambda package, raw: package["completion"].__setitem__("dropped_records", 1), "dropped"), (lambda package, raw: package["completion"].__setitem__("timestamp_gaps", 1), "timestamp gaps"), (lambda package, raw: package.__setitem__("actions", package["actions"][:-1]), "action markers"), (lambda package, raw: package["capture"]["mapping"].__setitem__("rts", "digital-2"), "mapping")])
def test_rejects_incomplete_or_ambiguous_package(tmp_path: Path, mutate, message: str) -> None:
    package_path, raw_path = _package(tmp_path, _events())
    package = json.loads(package_path.read_text())
    mutate(package, raw_path)
    package_path.write_bytes(_line(package))
    with pytest.raises(evidence.EvidenceError, match=message):
        evidence.adjudicate(package_path, raw_path)
def test_rejects_hash_footer_sequence_and_bounds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    package, raw = _package(tmp_path, _events())
    (tmp_path / "capture.sal").write_bytes(b"changed")
    with pytest.raises(evidence.EvidenceError, match="artifact"):
        evidence.adjudicate(package, raw)
    package, raw = _package(tmp_path, _events())
    raw.write_bytes(b"".join(raw.read_bytes().splitlines(keepends=True)[:-1]))
    with pytest.raises(evidence.EvidenceError, match="footer"):
        evidence.adjudicate(package, raw)
    for field, value, message in (("seq", 9, "sequence"), ("timestamp_ns", 99, "timestamp")):
        package, raw = _package(tmp_path, _events())
        rows = [json.loads(line) for line in raw.read_bytes().splitlines()]
        rows[2][field] = value
        body = b"".join(_line(row) for row in rows[:-1])
        rows[-1]["stream_sha256"] = hashlib.sha256(body).hexdigest()
        raw.write_bytes(body + _line(rows[-1]))
        with pytest.raises(evidence.EvidenceError, match=message):
            evidence.adjudicate(package, raw)
    package, raw = _package(tmp_path, _events())
    monkeypatch.setattr(evidence, "MAX_RECORDS", 2)
    with pytest.raises(evidence.EvidenceError, match="records"):
        evidence.adjudicate(package, raw)
def test_atomic_report_has_complete_checksum_and_cli(tmp_path: Path) -> None:
    package, raw = _package(tmp_path, _events())
    output = tmp_path / "report.json"
    assert evidence.main([str(package), str(raw), "--output", str(output)]) == 0
    document = json.loads(output.read_text())
    payload = json.dumps(document["payload"], sort_keys=True, separators=(",", ":")).encode()
    assert document["footer"] == {"complete": True, "payload_sha256": hashlib.sha256(payload).hexdigest()}
    with pytest.raises(evidence.EvidenceError, match="exists"):
        evidence.write_report(document["payload"], output)
def test_actual_entrypoint_closure_is_offline_files_only() -> None:
    path = Path("tests/support/mor1404_passive_civ_capture.py")
    tree = ast.parse(path.read_text(), filename=str(path))
    imports = {node.module.split(".", 1)[0] if isinstance(node, ast.ImportFrom) else alias.name.split(".", 1)[0] for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom)) for alias in node.names}
    assert imports <= {"__future__", "argparse", "hashlib", "json", "os", "pathlib", "stat", "typing"}
    forbidden = {"serial", "serial_asyncio", "termios", "tty", "CoreRadio", "web", "poller", "backend", "Popen", "system", "execv", "atexit", "_exit"}
    assert forbidden.isdisjoint({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)})
    assert not Path("tests/support/mor1404_sitecustomize/sitecustomize.py").exists()
def test_frame_gap_expires_partial_without_stitching(tmp_path: Path) -> None:
    for gap, present in ((evidence.MAX_INTERBYTE_GAP_NS, True), (evidence.MAX_INTERBYTE_GAP_NS + 1, False), (1_000_000_000, False)):
        events, shift = _events(), gap - 1
        for row in events[10:]:
            row["timestamp_ns"] += shift
        result = evidence.adjudicate(*_package(tmp_path, events))
        assert result["selector_07_present"] is present
        if not present:
            assert set(range(4, 11)) <= set(result["unframed_byte_seqs"])
@pytest.mark.parametrize("replacement_kind", ["oversized", "symlink"])
def test_read_binds_one_regular_descriptor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replacement_kind: str) -> None:
    target, replacement = tmp_path / "input", tmp_path / "replacement"
    target.write_bytes(b"x")
    if replacement_kind == "oversized":
        replacement.write_bytes(b"y" * 64)
    else:
        payload = tmp_path / "payload"
        payload.write_bytes(b"z")
        replacement.symlink_to(payload)
    def swap_after_validation(path: Path) -> bool:
        if path == target:
            target.unlink()
            replacement.replace(target)
            return False
        return Path.is_symlink(path)
    monkeypatch.setattr(Path, "is_symlink", swap_after_validation)
    assert evidence._read(target, 1, "race input") == b"x"
def test_read_rejects_symlink_and_fifo(tmp_path: Path) -> None:
    target, link, fifo = tmp_path / "target", tmp_path / "link", tmp_path / "fifo"
    target.write_bytes(b"x")
    link.symlink_to(target)
    os.mkfifo(fifo)
    for path in (link, fifo):
        with pytest.raises(evidence.EvidenceError, match="regular file|cannot read"):
            evidence._read(path, 16, "special input")
def test_publish_does_not_clobber_concurrent_target(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output, victim = tmp_path / "report.json", b"concurrent-owner"
    original_link = os.link
    def race_link(source, destination, **kwargs):
        output.write_bytes(victim)
        return original_link(source, destination, **kwargs)
    monkeypatch.setattr(os, "link", race_link)
    with pytest.raises(evidence.EvidenceError, match="exists"):
        evidence.write_report({}, output)
    assert output.read_bytes() == victim
    assert not output.with_name(f".{output.name}.partial").exists()
