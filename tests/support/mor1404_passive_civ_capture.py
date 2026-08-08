"""Offline adjudication of externally captured MOR-1404 analyzer evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any

# fmt: off
PACKAGE_SCHEMA, RAW_SCHEMA, REPORT_SCHEMA = "rigplane.mor1404.external-package.v1", "rigplane.mor1404.raw-events.v1", "rigplane.mor1404.replay-report.v1"
MAX_PACKAGE_BYTES, MAX_RAW_BYTES, MAX_ARTIFACT_BYTES, MAX_REPORT_BYTES = 64 * 1024, 512 * 1024, 64 * 1024 * 1024, 1024 * 1024
MAX_RECORDS, MAX_DURATION_NS = 25_000, 120_000_000_000
MAX_INTERBYTE_GAP_NS = (20 * 10 * 1_000_000_000 + 115_200 - 1) // 115_200  # Twenty 115200-8N1 character times.
REQUIRED_ACTIONS, CHANNELS = ("capture_start", "ab_1", "tune_1", "ab_2", "tune_2", "capture_end"), ("radio_to_controller", "controller_to_radio", "dtr", "rts")
FIELDS = {name: set(fields.split()) for name, fields in {"package": "schema source capture timing completion actions", "source": "kind artifact sha256 size", "capture": "tool model version mapping sample_rate_hz baud data_bits parity stop_bits", "timing": "start_ns end_ns", "completion": "complete truncated dropped_records timestamp_gaps records bytes channels", "action marker": "name seq timestamp_ns", "session header": "kind schema source_sha256 source_size start_ns end_ns", "session footer": "kind schema source_sha256 records bytes dropped_records timestamp_gaps truncated complete channels stream_sha256", "byte": "kind seq timestamp_ns transport direction value", "line": "kind seq timestamp_ns line state", "action": "kind seq timestamp_ns name"}.items()}
class EvidenceError(ValueError):
    pass
def _require(condition: bool, message: str) -> None:
    if not condition:
        raise EvidenceError(message)
def _exact(value: Any, label: str, fields: set[str] | None = None) -> dict[str, Any]:
    expected = FIELDS[label] if fields is None else fields
    _require(isinstance(value, dict) and set(value) == expected, f"invalid {label} fields")
    return value
def _integer(value: Any, label: str) -> int:
    _require(not isinstance(value, bool) and isinstance(value, int) and value >= 0, f"invalid {label}")
    return value
def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
def _read(path: Path, limit: int, label: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb", buffering=0) as handle:
            descriptor = -1
            before = os.fstat(handle.fileno())
            _require(stat.S_ISREG(before.st_mode), f"{label} must be a regular file")
            _require(0 < before.st_size <= limit, f"{label} exceeds size bound")
            data = handle.read(limit + 1)
            after = os.fstat(handle.fileno())
            current = os.stat(path, follow_symlinks=False)
            stable = (before.st_dev, before.st_ino, stat.S_IFMT(before.st_mode)) == (after.st_dev, after.st_ino, stat.S_IFMT(after.st_mode)) == (current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode)) and (before.st_size, before.st_mtime_ns, before.st_ctime_ns) == (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
            _require(stable, f"{label} changed during read")
            _require(len(data) == before.st_size <= limit, f"{label} exceeds size bound or changed during read")
            return data
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceError(f"cannot read {label}") from exc
    finally:
        if descriptor >= 0:
            try:
                os.close(descriptor)
            except OSError:
                pass
def _load_package(path: Path) -> tuple[dict[str, Any], Path]:
    try:
        package = _exact(json.loads(_read(path, MAX_PACKAGE_BYTES, "package")), "package")
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid package JSON") from exc
    _require(package["schema"] == PACKAGE_SCHEMA, "incompatible package schema")
    source = _exact(package["source"], "source")
    _require(source["kind"] in ("logic_tap", "inline_usb"), "invalid source kind")
    relative = Path(source["artifact"] if isinstance(source["artifact"], str) else "")
    _require(str(relative) not in ("", ".") and not relative.is_absolute() and ".." not in relative.parts, "invalid artifact path")
    artifact = path.parent / relative
    native = _read(artifact, MAX_ARTIFACT_BYTES, "artifact")
    _require(_integer(source["size"], "artifact size") == len(native), "artifact size mismatch")
    _require(source["sha256"] == hashlib.sha256(native).hexdigest(), "artifact hash mismatch")
    capture = _exact(package["capture"], "capture")
    _require(all(isinstance(capture[name], str) and capture[name] for name in ("tool", "model", "version")), "invalid capture identity")
    mapping = _exact(capture["mapping"], "channel mapping", set(CHANNELS))
    _require(all(isinstance(value, str) and value for value in mapping.values()) and len(set(mapping.values())) == len(CHANNELS), "ambiguous channel mapping")
    _require(_integer(capture["sample_rate_hz"], "sample rate") >= 230_400, "invalid sample rate")
    _require((capture["baud"], capture["data_bits"], capture["parity"], capture["stop_bits"]) == (115200, 8, "N", 1), "capture must be 115200 8N1")
    timing = _exact(package["timing"], "timing")
    start, end = (_integer(timing["start_ns"], "start timestamp"), _integer(timing["end_ns"], "end timestamp"))
    _require(start < end and end - start <= MAX_DURATION_NS, "capture duration exceeds bound")
    completion = _exact(package["completion"], "completion")
    channels = _exact(completion["channels"], "channel completion", set(CHANNELS))
    _require(all(value is True for value in channels.values()), "missing required channels")
    _require(completion["complete"] is True and completion["truncated"] is False, "capture incomplete or truncated")
    _require(_integer(completion["dropped_records"], "dropped records") == 0, "capture reports dropped records")
    _require(_integer(completion["timestamp_gaps"], "timestamp gaps") == 0, "capture reports timestamp gaps")
    _require(_integer(completion["records"], "records") <= MAX_RECORDS, "records exceed bound")
    _require(_integer(completion["bytes"], "byte count") <= MAX_RAW_BYTES, "byte count exceeds bound")
    actions = package["actions"]
    _require(isinstance(actions, list) and [row.get("name") for row in actions if isinstance(row, dict)] == list(REQUIRED_ACTIONS), "incomplete action markers")
    for row in actions:
        row = _exact(row, "action marker")
        _integer(row["seq"], "action sequence")
        _integer(row["timestamp_ns"], "action timestamp")
    return package, artifact
def _load_events(path: Path, package: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw = _read(path, MAX_RAW_BYTES, "raw event file")
    _require(raw.endswith(b"\n"), "raw event footer is not complete")
    lines = raw.splitlines(keepends=True)
    _require(len(lines) >= 3, "raw event footer is missing")
    try:
        rows = [json.loads(line) for line in lines]
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("invalid raw event JSONL") from exc
    _require(all(_canonical(row) + b"\n" == line for row, line in zip(rows, lines, strict=True)), "raw event JSONL is not canonical")

    source, timing, completion = (package["source"], package["timing"], package["completion"])
    header = _exact(rows[0], "session header")
    expected_header = dict(kind="session", schema=RAW_SCHEMA, source_sha256=source["sha256"], source_size=source["size"], start_ns=timing["start_ns"], end_ns=timing["end_ns"])
    _require(header == expected_header, "header source binding mismatch")
    stream_hash = hashlib.sha256(b"".join(lines[:-1])).hexdigest()
    footer = _exact(rows[-1], "session footer")
    expected_footer = dict(kind="footer", schema=RAW_SCHEMA, source_sha256=source["sha256"], stream_sha256=stream_hash, **completion)
    _require(footer == expected_footer, "footer checksum or completion mismatch")

    events = rows[1:-1]
    _require(len(events) == completion["records"] <= MAX_RECORDS, "records mismatch or exceed bound")
    expected_transport = "uart" if source["kind"] == "logic_tap" else "usb"
    previous, byte_count = timing["start_ns"], 0
    lines_seen: set[str] = set()
    actions: list[dict[str, Any]] = []
    for sequence, event in enumerate(events):
        _require(isinstance(event, dict) and event.get("seq") == sequence, "event sequence gap")
        timestamp = _integer(event.get("timestamp_ns"), "event timestamp")
        _require(previous <= timestamp <= timing["end_ns"], "event timestamp gap or regression")
        previous = timestamp
        kind = event.get("kind")
        if kind == "byte":
            _exact(event, "byte")
            _require(event["transport"] == expected_transport and event["direction"] in CHANNELS[:2], "ambiguous byte direction or transport")
            _require(_integer(event["value"], "byte value") <= 255, "invalid byte value")
            byte_count += 1
        elif kind == "line":
            _exact(event, "line")
            _require(event["line"] in CHANNELS[2:] and isinstance(event["state"], bool), "invalid modem-line event")
            lines_seen.add(event["line"])
        elif kind == "action":
            _exact(event, "action")
            actions.append(dict(name=event["name"], seq=event["seq"], timestamp_ns=event["timestamp_ns"]))
        else:
            raise EvidenceError("invalid raw event kind")
    _require(lines_seen == set(CHANNELS[2:]), "missing required modem-line channels")
    _require(byte_count == completion["bytes"], "byte count mismatch")
    _require(actions == package["actions"], "action markers mismatch")
    return events, stream_hash

def _derive_frames(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[int]]:
    frames: list[dict[str, Any]] = []
    used: set[int] = set()
    for direction in CHANNELS[:2]:
        stream = [row for row in events if row["kind"] == "byte" and row["direction"] == direction]
        index = 0
        while index + 1 < len(stream):
            if (stream[index]["value"], stream[index + 1]["value"]) != (0xFE, 0xFE):
                index += 1
                continue
            end = next((pos for pos in range(index + 2, len(stream)) if stream[pos]["value"] == 0xFD), None)
            gap = next((pos for pos in range(index + 1, (end + 1) if end is not None else len(stream)) if stream[pos]["timestamp_ns"] - stream[pos - 1]["timestamp_ns"] > MAX_INTERBYTE_GAP_NS), None)
            if gap is not None:
                index = gap
                continue
            nested = next((pos for pos in range(index + 2, end or len(stream) - 1) if stream[pos]["value"] == 0xFE and stream[pos + 1]["value"] == 0xFE), None)
            if nested is not None:
                index = nested
                continue
            if end is None:
                break
            chunk = stream[index : end + 1]
            values = bytes(row["value"] for row in chunk)
            used.update(row["seq"] for row in chunk)
            frame = dict(direction=direction, transport=chunk[0]["transport"], hex=values.hex(), start_seq=chunk[0]["seq"], end_seq=chunk[-1]["seq"], start_ns=chunk[0]["timestamp_ns"], end_ns=chunk[-1]["timestamp_ns"])
            if len(values) >= 6:
                frame.update(to_addr=values[2], from_addr=values[3], command=values[4], data_hex=values[5:-1].hex())
            frames.append(frame)
            index = end + 1
    frames.sort(key=lambda row: row["start_seq"])
    unframed = [row["seq"] for row in events if row["kind"] == "byte" and row["seq"] not in used]
    return frames, unframed

def adjudicate(package_path: Path, raw_path: Path) -> dict[str, Any]:
    package, artifact = _load_package(Path(package_path))
    events, stream_hash = _load_events(Path(raw_path), package)
    frames, unframed = _derive_frames(events)
    inbound_07 = [row for row in frames if row.get("direction") == "radio_to_controller" and row.get("command") == 0x07]
    selectors = [row for row in inbound_07 if row.get("from_addr") == 0x94 and row.get("to_addr") in (0xE0, 0x00) and row.get("data_hex") in ("00", "01")]
    outbound = [row for row in events if row["kind"] == "byte" and row["direction"] == "controller_to_radio"]
    line_states = [row for row in events if row["kind"] == "line"]
    previous: dict[str, bool] = {}
    transitions: list[dict[str, Any]] = []
    for row in line_states:
        if row["line"] in previous and previous[row["line"]] != row["state"]:
            transitions.append(dict(line=row["line"], seq=row["seq"], timestamp_ns=row["timestamp_ns"], **{"from": previous[row["line"]], "to": row["state"]}))
        previous[row["line"]] = row["state"]
    safety = dict(verdict="FAIL" if outbound or transitions else "PASS", outbound_count=len(outbound), modem_transition_count=len(transitions))
    return dict(schema=REPORT_SCHEMA, frame_gap_ns=MAX_INTERBYTE_GAP_NS, native_artifact=dict(path=artifact.name, **package["source"]), raw_stream_sha256=stream_hash, raw_events=events, frames=frames, unframed_byte_seqs=unframed, inbound_07=inbound_07, selector_07=selectors, selector_07_present=bool(selectors), outbound_payloads=outbound, modem_line_states=line_states, modem_transitions=transitions, safety=safety)
def write_report(payload: dict[str, Any], output: Path) -> None:
    output = Path(output)
    _require(output.parent.is_dir(), "output parent is missing")
    payload_bytes = _canonical(payload)
    document = dict(schema=REPORT_SCHEMA, payload=payload, footer=dict(complete=True, payload_sha256=hashlib.sha256(payload_bytes).hexdigest()))
    rendered = _canonical(document) + b"\n"
    _require(len(rendered) <= MAX_REPORT_BYTES, "report exceeds size bound")
    temporary = output.with_name(f".{output.name}.partial")
    try:
        with temporary.open("xb") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(temporary, output)
        except FileExistsError as exc:
            raise EvidenceError("output already exists") from exc
    except EvidenceError:
        raise
    except FileExistsError as exc:
        raise EvidenceError("temporary output already exists") from exc
    except OSError as exc:
        raise EvidenceError("cannot publish output") from exc
    finally:
        temporary.unlink(missing_ok=True)
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for arguments in (("package",), ("raw_events",), ("--output",)):
        parser.add_argument(*arguments, **({"required": True} if arguments[0].startswith("--") else {}), type=Path)
    args = parser.parse_args(argv)
    try:
        write_report(adjudicate(args.package, args.raw_events), args.output)
    except EvidenceError as exc:
        parser.error(str(exc))
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
