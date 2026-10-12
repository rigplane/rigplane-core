"""Extract commit-safe fixtures from a recorded Icom LAN session.

Input is the JSONL written by
:class:`~rigplane_testkit.icom.recorder.IcomSessionRecorder` (one datagram
per line: ``{"t", "dir", "port", "hex", "redacted"?}``).  The extractor
keeps only what conformance fixtures need and nothing that identifies the
radio or the client computer: :func:`civ_frames` (LAN header dropped),
:func:`control_structure` (who sent what kind of control packet when —
never payload bytes), :func:`cadence` (ping / token-renew / retransmit /
audio-length statistics), :func:`audio_headers` (samples truncated).

CLI::

    python -m rigplane_testkit.icom.fixtures extract \
        --in REC.jsonl[.gz] --out-dir DIR [--civ-from T] [--civ-to T]

writes ``civ_frames.jsonl``, ``control_structure.jsonl``, ``cadence.json``
and ``audio_headers.jsonl`` (sorted keys, one record per line, trailing
newline; the same input produces byte-identical output).  Gate the result
before committing: ``python -m rigplane_testkit.redaction_gate
--deny-file SECRETS.txt DIR/*``.
"""

from __future__ import annotations

import argparse
import gzip
import json
import statistics
import struct
from pathlib import Path

from rigplane_testkit.icom.replay import Record, extract_civ_frame

_PING_SIZE = 0x15  # ping packet length (request and reply)
_PING_TYPE = 0x07  # u16 LE packet type at offset 0x04
_RETRANSMIT_TYPE = 0x01  # retransmit-request packet type
_TOKEN_SIZE = 0x40  # token ack / renew / remove packet length
_TOKEN_RENEW = 0x05  # reqtype byte (0x15) of a token-renew request
_TOKEN_ANSWER_WINDOW_S = 0.5  # max delay for a token renew to count answered
_AUDIO_HEADER_BYTES = 0x18  # audio bytes kept by audio_headers


def load_records(path: str | Path) -> list[Record]:
    """Parse a recorder JSONL file (``.jsonl`` or ``.jsonl.gz``) into records."""
    path = Path(path)
    records: list[Record] = []
    opener = gzip.open if path.suffix == ".gz" else Path.open
    with opener(path, "rt", encoding="utf-8") as fh:  # type: ignore[arg-type]
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
    return records


def civ_frames(
    records: list[Record],
    *,
    t_from: float | None = None,
    t_to: float | None = None,
) -> list[dict]:
    """CI-V frames on the civ port, LAN header dropped; ``t_from`` /
    ``t_to`` bound the record timestamps inclusively.  Each entry is
    ``{"t": round(t, 3), "dir": "c2r"|"r2c", "frame": hex}``."""
    out: list[dict] = []
    for rec in records:
        if rec.port != "civ":
            continue
        if t_from is not None and rec.t < t_from:
            continue
        if t_to is not None and rec.t > t_to:
            continue
        frame = extract_civ_frame(rec.data)
        if frame is not None:
            out.append(
                {"t": round(rec.t, 3), "dir": rec.direction, "frame": frame.hex()}
            )
    return out


def control_structure(records: list[Record]) -> list[dict]:
    """Shape of the control exchange, without a single payload byte.

    One entry per ``control`` record: ``{"t", "dir", "len", "type",
    "reqtype"}``; ``type`` is the u16 LE at 0x04 (None when the datagram
    is shorter than 6 bytes), ``reqtype`` the byte at 0x15 when
    ``len > 0x15``, else None.
    """
    out: list[dict] = []
    for rec in records:
        if rec.port != "control":
            continue
        data = rec.data
        out.append(
            {
                "t": round(rec.t, 3),
                "dir": rec.direction,
                "len": len(data),
                "type": struct.unpack_from("<H", data, 0x04)[0]
                if len(data) >= 0x06
                else None,
                "reqtype": data[0x15] if len(data) > 0x15 else None,
            }
        )
    return out


def _p95(intervals: list[float]) -> float:
    """Linear-interpolated 95th percentile of a non-empty sample."""
    ordered = sorted(intervals)
    rank = (len(ordered) - 1) * 0.95
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def _round_or_none(value: float | None) -> float | None:
    return round(value, 3) if value is not None else None


def cadence(records: list[Record]) -> dict:
    """Timing statistics over the recorded session.

    Returns ``{"pings", "token_renewals", "retransmit_requests",
    "audio_lengths"}``: pings (0x15-byte type-0x07 packets) keyed
    ``"port:dir"`` with ``count`` / ``median_s`` / ``p95_s`` / ``max_s``
    interval statistics (null when fewer than two pings) and the
    first-to-last ``seconds`` span; token renewals as ``[{"t",
    "answered_after_s"|None}]`` — a c2r 0x40-byte control packet with
    reqtype 0x05, answered when an r2c 0x40-byte control packet follows
    within 0.5 s; retransmit-request (type-0x01) counts per ``"port:dir"``;
    audio packet length histograms per direction.  Floats are rounded to
    3 decimals.
    """
    pings: dict[str, list[float]] = {}
    retransmits: dict[str, int] = {}
    renew_requests: list[float] = []
    token_replies: list[float] = []
    audio: dict[str, dict[str, int]] = {"c2r": {}, "r2c": {}}
    for rec in records:
        data = rec.data
        key = f"{rec.port}:{rec.direction}"
        ptype = struct.unpack_from("<H", data, 0x04)[0] if len(data) >= 0x06 else None
        if len(data) == _PING_SIZE and ptype == _PING_TYPE:
            pings.setdefault(key, []).append(rec.t)
        if ptype == _RETRANSMIT_TYPE:
            retransmits[key] = retransmits.get(key, 0) + 1
        if rec.port == "control" and len(data) == _TOKEN_SIZE:
            if rec.direction == "c2r" and data[0x15] == _TOKEN_RENEW:
                renew_requests.append(rec.t)
            elif rec.direction == "r2c":
                token_replies.append(rec.t)
        if rec.port == "audio":
            bucket = audio[rec.direction]
            length = str(len(data))
            bucket[length] = bucket.get(length, 0) + 1
    return {
        "pings": {
            key: {
                "count": len(ts),
                "median_s": _round_or_none(
                    statistics.median(_intervals(ts)) if len(ts) > 1 else None
                ),
                "p95_s": _round_or_none(_p95(_intervals(ts)) if len(ts) > 1 else None),
                "max_s": _round_or_none(max(_intervals(ts)) if len(ts) > 1 else None),
                "seconds": round(ts[-1] - ts[0], 3) if len(ts) > 1 else 0.0,
            }
            for key, ts in sorted(pings.items())
        },
        "token_renewals": [
            {"t": round(t, 3), "answered_after_s": _answer_delay(t, token_replies)}
            for t in renew_requests
        ],
        "retransmit_requests": dict(sorted(retransmits.items())),
        "audio_lengths": audio,
    }


def _intervals(ts: list[float]) -> list[float]:
    return [b - a for a, b in zip(ts, ts[1:])]


def _answer_delay(t: float, replies: list[float]) -> float | None:
    for reply in replies:
        if t < reply <= t + _TOKEN_ANSWER_WINDOW_S:
            return round(reply - t, 3)
    return None


def audio_headers(records: list[Record], limit: int = 50) -> list[dict]:
    """First ``limit`` audio records per direction, samples truncated away.

    Each entry is ``{"t", "dir", "hex"}`` with ``hex`` holding only the
    first 0x18 bytes (the audio packet header).
    """
    counts = {"c2r": 0, "r2c": 0}
    out: list[dict] = []
    for rec in records:
        if rec.port != "audio" or counts[rec.direction] >= limit:
            continue
        counts[rec.direction] += 1
        out.append(
            {
                "t": round(rec.t, 3),
                "dir": rec.direction,
                "hex": rec.data[:_AUDIO_HEADER_BYTES].hex(),
            }
        )
    return out


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.icom.fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    ext = sub.add_parser("extract", help="extract commit-safe fixtures")
    ext.add_argument("--in", dest="in_path", required=True, help="input .jsonl[.gz]")
    ext.add_argument("--out-dir", required=True, help="output directory")
    ext.add_argument("--civ-from", type=float, default=None, help="CI-V from (s)")
    ext.add_argument("--civ-to", type=float, default=None, help="CI-V window end (s)")
    args = parser.parse_args(argv)
    if args.command == "extract":
        records = load_records(args.in_path)
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        _write_jsonl(
            out / "civ_frames.jsonl",
            civ_frames(records, t_from=args.civ_from, t_to=args.civ_to),
        )
        _write_jsonl(out / "control_structure.jsonl", control_structure(records))
        (out / "cadence.json").write_text(
            json.dumps(cadence(records), sort_keys=True) + "\n"
        )
        _write_jsonl(out / "audio_headers.jsonl", audio_headers(records))


if __name__ == "__main__":
    main()
