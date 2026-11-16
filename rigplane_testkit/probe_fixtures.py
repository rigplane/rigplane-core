"""Convert ``rigplane-probe-v1`` captures into replay fixture rows.

A probe file is a paired, classified table of what the run sent and what
the radio answered; the fixture ``*_pairs.jsonl`` files hold the same
facts trimmed to request/reply rows. These converters bridge the two so
every customer capture becomes a regression test: convert, mount the rows
as a :class:`~rigplane_testkit.icom.replay.RecordedAnswerTable` (CI-V)
or :class:`~rigplane_testkit.yaesu.replay.RecordedCatAnswerTable` (CAT),
and run the real ``rigplane probe`` CLI against the fake.

Outcome to rows, for both protocols: ``data``, ``refused`` and
``ack_only`` rows give a ``c2r`` + ``r2c`` pair (the CI-V request frame
is the :func:`~rigplane.commands.build_civ_frame` envelope around the
row's body, built exactly the way the probe CLI sends it); ``silent``
rows give the ``c2r`` row alone; ``malformed``, ``unsolicited`` and
``transport_error`` rows are NOT emitted as pairs. Skipped reads are
never emitted either: only reads the run actually sent (``commands``)
become rows. One row per distinct request: the first occurrence wins,
like the existing extractors.

CAT silence needs no special casing here: the converter emits the bare
request row, and ``RecordedCatAnswerTable.from_cat_pairs`` records a
request the next request overtakes — and a trailing request with no
reply — as silence. The ``t`` values are a synthetic timeline from the
rows' own latencies (cumulative, never asserted), so the same file
always converts byte-identically.

CLI::

    python -m rigplane_testkit.probe_fixtures convert \\
        --in FILE.json --window NAME --out-dir DIR [--radio-addr 0xNN]

merges the window into ``DIR/civ_pairs.jsonl`` or ``DIR/cat_pairs.jsonl``
(replacing rows of the same window, the way
:mod:`rigplane_testkit.serial.fixtures` merges), with sorted keys and
one trailing newline per row. The protocol comes from the file; the
CI-V radio address defaults to the profile named in the file.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.probe.model import ProbeFile
from rigplane.profiles import resolve_radio_profile

#: Outcomes that carry a reply and become a request/reply pair.
_PAIR_OUTCOMES = frozenset({"data", "refused", "ack_only"})


def _civ_request_frame(body: bytes, radio_addr: int) -> bytes:
    """The wire frame the probe CLI sends for one CI-V body.

    Split exactly the way ``rigplane.cli._probe`` splits it before
    sending (the split is envelope-invariant, but this stays on the
    production code path so a future envelope change lands here too).
    """
    command, rest = body[0], body[1:]
    if command_carries_sub(command) and rest:
        sub, data = rest[0], rest[1:]
    else:
        sub, data = None, rest
    return build_civ_frame(
        radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data or None
    )


def probe_to_civ_pairs(
    probe: ProbeFile, *, radio_addr: int, window: str
) -> list[dict[str, Any]]:
    """Trim one CI-V probe file to ``civ_pairs`` fixture rows."""
    rows: list[dict[str, Any]] = []
    seen: set[bytes] = set()
    elapsed_ms = 0.0
    for row in probe.commands:
        if row.outcome not in _PAIR_OUTCOMES and row.outcome != "silent":
            continue
        body = bytes.fromhex(row.request_hex)
        if body in seen:
            continue
        seen.add(body)
        request = _civ_request_frame(body, radio_addr)
        started_s = round(elapsed_ms / 1000.0, 3)
        elapsed_ms += row.latency_ms
        rows.append(
            {
                "dir": "c2r",
                "frame": request.hex(),
                "t": started_s,
                "transport": probe.radio.port_kind,
                "window": window,
            }
        )
        if row.outcome == "silent":
            continue
        if row.reply_hex is None:
            raise ValueError(
                f"probe row {row.name!r} pairs {row.outcome!r} with no reply"
            )
        reply = bytes.fromhex(row.reply_hex)
        if len(reply) < 6 or reply[:2] != b"\xfe\xfe" or reply[-1:] != b"\xfd":
            raise ValueError(f"probe row {row.name!r} reply is not a CI-V frame")
        rows.append(
            {
                "dir": "r2c",
                "frame": reply.hex(),
                "t": round(elapsed_ms / 1000.0, 3),
                "transport": probe.radio.port_kind,
                "window": window,
            }
        )
    return rows


def probe_to_cat_pairs(probe: ProbeFile, *, window: str) -> list[dict[str, Any]]:
    """Trim one CAT probe file to ``cat_pairs`` fixture rows."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    elapsed_ms = 0.0
    for row in probe.commands:
        if row.outcome not in _PAIR_OUTCOMES and row.outcome != "silent":
            continue
        line = bytes.fromhex(row.request_hex).decode("ascii")
        if line in seen:
            continue
        seen.add(line)
        started_s = round(elapsed_ms / 1000.0, 3)
        elapsed_ms += row.latency_ms
        rows.append(
            {
                "dir": "c2r",
                "line": line,
                "t": started_s,
                "transport": probe.radio.port_kind,
                "window": window,
            }
        )
        if row.outcome == "silent":
            continue
        if row.reply_text is not None:
            reply_line = row.reply_text
        elif row.reply_hex is not None:
            reply_line = bytes.fromhex(row.reply_hex).decode("ascii")
        else:
            raise ValueError(
                f"probe row {row.name!r} pairs {row.outcome!r} with no reply"
            )
        rows.append(
            {
                "dir": "r2c",
                "line": reply_line,
                "t": round(elapsed_ms / 1000.0, 3),
                "transport": probe.radio.port_kind,
                "window": window,
            }
        )
    return rows


def _dump(rows: list[dict[str, Any]]) -> str:
    return "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in rows
    )


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line
    ]


def _merge_window_rows(
    out_dir: Path, filename: str, window: str, rows: list[dict[str, Any]]
) -> Path:
    """Replace ``window`` in ``filename``; rewrite sorted, newline-terminated."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / filename
    kept = [row for row in _read_rows(path) if row.get("window") != window]
    path.write_text(_dump(kept + rows), encoding="utf-8")
    return path


def _profile_default_addr(profile: str) -> int:
    return resolve_radio_profile(model=profile).civ_addr


def main(argv: list[str] | None = None) -> int:
    """Convert one probe file into fixture rows (see the module docstring)."""
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.probe_fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    convert = sub.add_parser("convert", help="trim a probe file into pairs rows")
    convert.add_argument("--in", dest="source", required=True, help="probe JSON file")
    convert.add_argument("--window", required=True, help="window name for the rows")
    convert.add_argument("--out-dir", required=True, help="fixtures directory")
    convert.add_argument(
        "--radio-addr",
        type=lambda value: int(value, 0),
        default=None,
        help="CI-V radio address (default: the file's profile)",
    )
    args = parser.parse_args(argv)
    probe = ProbeFile.from_json(Path(args.source).read_text(encoding="utf-8"))
    if probe.radio.protocol == "civ":
        radio_addr = args.radio_addr
        if radio_addr is None:
            radio_addr = _profile_default_addr(probe.radio.profile)
        if not 0x00 <= radio_addr <= 0xFF:
            parser.error(f"--radio-addr {radio_addr!r} is not one address byte")
        rows = probe_to_civ_pairs(probe, radio_addr=radio_addr, window=args.window)
        path = _merge_window_rows(
            Path(args.out_dir), "civ_pairs.jsonl", args.window, rows
        )
    elif probe.radio.protocol == "yaesu_cat":
        rows = probe_to_cat_pairs(probe, window=args.window)
        path = _merge_window_rows(
            Path(args.out_dir), "cat_pairs.jsonl", args.window, rows
        )
    else:
        parser.error(f"unsupported probe protocol {probe.radio.protocol!r}")
        raise AssertionError("unreachable")
    print(json.dumps({"window": args.window, "rows": len(rows), "file": str(path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
