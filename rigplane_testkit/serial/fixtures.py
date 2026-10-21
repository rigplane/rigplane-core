"""Extract ``civ_pairs`` fixture rows from a serial bridge recording.

Input is the JSONL written by
:class:`~rigplane_testkit.serial.recorder.SerialCivRecorder`. The output rows
have the shape ``tests/fixtures/ic7610/civ_pairs.jsonl`` uses
(``window``, ``transport``, ``t``, ``dir``, ``frame``) so
:meth:`~rigplane_testkit.icom.replay.RecordedAnswerTable.from_civ_pairs` reads
them unchanged.

Trim rule (the one ``tests/fixtures/ic7610/README.md`` documents for its USB
window): per window, the first occurrence of every distinct client request,
the radio's byte-identical echo of it when the port echoes, and the reply
that closes it. A request nothing answered keeps its ``c2r`` row alone.
Radio frames that answer no pending request (CI-V transceive broadcasts,
frames addressed to the controller with no request outstanding) are not
pair rows; they are summarised separately, first sight and count.

Scope wave frames (``27 00``) are a stream, not replies: they close no
request and are NOT mixed into the pair rows (``RecordedAnswerTable`` pairs
each ``c2r`` row with the next ``r2c`` row). The first ``scope_sweeps``
complete sweeps - sequence 1, 2, ... up to the sequence maximum with no gap -
are kept as ``r2c`` rows of the separate window ``<window>_scope``; a sweep
with a missing or reordered packet is dropped whole. Every wave frame seen
feeds the cadence summary (frame count and first/last time), which is what a
frames-per-second baseline is computed from.

Chunks the bridge refused (``blocked`` rows) are never rows; their count is
returned so a capture log can say what a client tried and was not allowed.

CLI::

    python -m rigplane_testkit.serial.fixtures extract \\
        --in REC.jsonl --window NAME --radio-addr 0x94 --out-dir DIR

merges the window into ``DIR/civ_pairs.jsonl`` (replacing rows of the same
window) and rewrites ``DIR/unsolicited.jsonl`` entries of that window.
Run :mod:`rigplane_testkit.redaction_gate` over the output before committing.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path

from rigplane_testkit.serial.recorder import civ_frames

_CONTROLLER = 0xE0
_ACK = 0xFB
_NAK = 0xFA
_SCOPE_CMD = 0x27
_SCOPE_WAVE_SUB = 0x00


def _bcd(byte: int) -> int:
    return (byte >> 4) * 10 + (byte & 0x0F)


@dataclass
class WindowExtract:
    """What :func:`extract_window` found in one recording."""

    rows: list[dict] = field(default_factory=list)
    scope_rows: list[dict] = field(default_factory=list)
    unsolicited: list[dict] = field(default_factory=list)
    scope: dict = field(default_factory=dict)
    blocked_frames: int = 0
    echo: bool = False


def _is_wave(frame: bytes, radio_addr: int) -> bool:
    return (
        len(frame) > 9
        and frame[3] == radio_addr
        and frame[4] == _SCOPE_CMD
        and frame[5] == _SCOPE_WAVE_SUB
    )


def extract_window(
    path: str | Path,
    window: str,
    *,
    radio_addr: int,
    transport: str = "usb",
    scope_sweeps: int = 5,
) -> WindowExtract:
    """Trim one bridge recording into ``civ_pairs`` rows (module docstring).

    Requests may be pipelined (a polling client sends several before the
    first reply). The radio answers in order, so a data reply is paired with
    the oldest outstanding request whose body it extends (or, failing that,
    whose command and sub-command it repeats); every older outstanding
    request is then silent. An ACK/NAK closes the oldest outstanding
    request. Rows are emitted request-then-reply at reply time, so adjacent
    pairs stay adjacent whatever the interleaving.
    """
    out = WindowExtract()
    seen: set[bytes] = set()
    unsolicited: dict[bytes, dict] = {}
    pending: list[dict] = []  # oldest first: {req, kept, t, echo_t}
    sweep: list[dict] = []  # contiguous packets of the sweep being assembled
    sweeps = 0
    wave_count = 0
    wave_first: float | None = None
    wave_last: float | None = None

    def make_row(t: float, direction: str, frame: bytes, name: str) -> dict:
        return {
            "dir": direction,
            "frame": frame.hex(),
            "t": round(t, 3),
            "transport": transport,
            "window": name,
        }

    def row(t: float, direction: str, frame: bytes) -> None:
        out.rows.append(make_row(t, direction, frame, window))

    def emit_request(item: dict) -> None:
        if item["kept"]:
            row(item["t"], "c2r", item["req"])
            if item["echo_t"] is not None:
                row(item["echo_t"], "r2c", item["req"])

    def match(frame: bytes) -> int | None:
        """Index in ``pending`` that this radio reply closes, else None."""
        if frame[2] != _CONTROLLER or frame[3] != radio_addr:
            return None
        if frame[4] in (_ACK, _NAK):
            return 0 if pending else None
        body = frame[4:-1]
        for index, item in enumerate(pending):
            if body.startswith(item["req"][4:-1]):
                return index
        for index, item in enumerate(pending):
            req_body = item["req"][4:-1]
            if body[:1] == req_body[:1] and body[1:2] == req_body[1:2]:
                return index
        return None

    for t, direction, frame in civ_frames(path):
        if direction == "blocked":
            out.blocked_frames += 1
            continue
        if direction == "c2r":
            kept = frame not in seen
            seen.add(frame)
            pending.append({"req": frame, "kept": kept, "t": t, "echo_t": None})
            continue
        if direction != "r2c":
            continue
        if _is_wave(frame, radio_addr):
            wave_count += 1
            wave_first = t if wave_first is None else wave_first
            wave_last = t
            data = frame[6:-1]  # receiver byte, sequence, sequence max, ...
            seq, seq_max = (_bcd(data[1]), _bcd(data[2])) if len(data) > 2 else (0, 0)
            if seq == 1:
                sweep = []  # a new sweep starts; a partial one is dropped
            if seq >= 1 and len(sweep) == seq - 1:
                sweep.append(make_row(t, "r2c", frame, f"{window}_scope"))
                if seq == seq_max:
                    if sweeps < scope_sweeps:
                        out.scope_rows.extend(sweep)
                        sweeps += 1
                    sweep = []
            else:
                sweep = []  # gap or reorder: this sweep is not whole
            continue
        echoed = next((item for item in pending if item["req"] == frame), None)
        if echoed is not None and echoed["echo_t"] is None:
            out.echo = True
            echoed["echo_t"] = t
            continue
        index = match(frame)
        if index is None:
            entry = unsolicited.setdefault(
                frame, {"frame": frame.hex(), "first_t": round(t, 3), "count": 0}
            )
            entry["count"] += 1
            continue
        for item in pending[:index]:  # in-order radio: older requests got nothing
            emit_request(item)
        item = pending[index]
        del pending[: index + 1]
        emit_request(item)
        if item["kept"]:
            row(t, "r2c", frame)
    for item in pending:
        emit_request(item)

    out.unsolicited = list(unsolicited.values())
    out.scope = {
        "frames": wave_count,
        "first_t": None if wave_first is None else round(wave_first, 3),
        "last_t": None if wave_last is None else round(wave_last, 3),
        "sweeps_kept": sweeps,
    }
    return out


def _dump(rows: list[dict]) -> str:
    return "".join(
        json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows
    )


def _read_rows(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def merge_window(out_dir: Path, window: str, extract: WindowExtract) -> None:
    """Replace ``window`` in ``civ_pairs.jsonl`` / ``unsolicited.jsonl``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    pairs = out_dir / "civ_pairs.jsonl"
    names = (window, f"{window}_scope")
    kept = [r for r in _read_rows(pairs) if r["window"] not in names]
    pairs.write_text(_dump(kept + extract.rows + extract.scope_rows))
    unsol = out_dir / "unsolicited.jsonl"
    kept_u = [r for r in _read_rows(unsol) if r["window"] != window]
    new_u = [{"window": window, **entry} for entry in extract.unsolicited]
    unsol.write_text(_dump(kept_u + new_u))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.serial.fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    ext = sub.add_parser("extract", help="trim a bridge recording into fixture rows")
    ext.add_argument("--in", dest="source", required=True, help="bridge JSONL")
    ext.add_argument("--window", required=True, help="window name for the rows")
    ext.add_argument("--radio-addr", type=lambda s: int(s, 0), required=True)
    ext.add_argument("--transport", default="usb")
    ext.add_argument("--scope-sweeps", type=int, default=5)
    ext.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    result = extract_window(
        args.source,
        args.window,
        radio_addr=args.radio_addr,
        transport=args.transport,
        scope_sweeps=args.scope_sweeps,
    )
    merge_window(Path(args.out_dir), args.window, result)
    print(
        json.dumps(
            {
                "window": args.window,
                "rows": len(result.rows),
                "scope_rows": len(result.scope_rows),
                "unsolicited_distinct": len(result.unsolicited),
                "echo": result.echo,
                "blocked_frames": result.blocked_frames,
                "scope": result.scope,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
