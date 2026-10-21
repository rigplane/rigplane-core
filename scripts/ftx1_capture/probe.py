"""FTX-1 capture driver: paced, once-only, read-only probes over the recorder bridge.

Point it at ``socket://HOST:PORT`` of a running
``python -m rigplane_testkit.yaesu record`` bridge (the Core server must not
be connected: the bridge serves one client). Every line from
``probe_list.build_probe_list`` is sent once, paced; the bridge's
``CatReadOnlyGuard`` independently drops anything that is not a profile read.

Per probe the class is ``data`` / ``refusal`` (``?;``) / ``silence``. The
run ABORTS on the first unexpected outcome — a reply that is not
``?;`` and does not carry the probe's own two-letter verb, a reply with a
byte outside 0x20-0x7E, or a transport error — and exits 2. A refusal or a
silence is an expected answer and is recorded, not an error. A state
snapshot (``SNAPSHOT_READS``) is taken before and after; any difference
exits 1.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rigplane.core.serial_open import open_serial_port
from rigplane.profiles.rig_loader import load_rig

from probe_list import Probe, build_probe_list

# Reads whose answers must be identical before and after the run.
SNAPSHOT_READS = (
    "IF;",
    "FA;",
    "FB;",
    "MD0;",
    "MD1;",
    "ST;",
    "FT;",
    "FR;",
    "PS;",
    "AI;",
    "TX;",
)

# A data reply starts with the whole request body (``NA1;`` -> ``NA1x;``), so a
# late reply to the previous request that merely shares the verb is rejected.
# The one recorded exception: the radio answers the SUB S-meter read with the
# side digit echoed as 0 (rigs/ftx1.toml: get_s_meter_sub).
_ECHO_EXCEPTIONS = {"SM1;": "SM0"}

EXIT_DIFF = 1
EXIT_ABORT = 2


class Abort(Exception):
    """An unexpected outcome that ends the run."""


async def _query(reader: Any, writer: Any, line: str, timeout: float) -> str | None:
    """Send one line; return the ';'-terminated reply, or None on silence."""
    try:
        writer.write(line.encode("ascii"))
        await writer.drain()
    except OSError as error:  # BrokenPipeError, ConnectionResetError, SerialException
        raise Abort(f"transport error sending {line}: {error!r}") from error
    try:
        raw = await asyncio.wait_for(reader.readuntil(b";"), timeout=timeout)
    except asyncio.TimeoutError:
        return None
    except (asyncio.IncompleteReadError, ConnectionError, OSError) as error:
        raise Abort(f"transport error after {line}: {error!r}") from error
    if any(b < 0x20 or b > 0x7E for b in raw):
        raise Abort(f"garbled reply to {line}: {raw!r}")
    return raw.decode("ascii")


def classify(line: str, reply: str | None) -> str:
    """``data`` / ``refusal`` / ``silence``; anything else is an Abort."""
    if reply is None:
        return "silence"
    if reply == "?;":
        return "refusal"
    if reply.startswith(line[:-1]) or reply.startswith(
        _ECHO_EXCEPTIONS.get(line, "\0")
    ):
        return "data"
    raise Abort(f"unexpected reply to {line}: {reply!r}")


async def run_probes(
    reader: Any, writer: Any, probes: list[Probe], pace: float, timeout: float
) -> list[dict[str, Any]]:
    """Send each probe once; return one result per probe (stops at an Abort)."""
    results: list[dict[str, Any]] = []
    for probe in probes:
        started = time.monotonic()
        reply = await _query(reader, writer, probe.line, timeout)
        entry = {
            "command": probe.command,
            "line": probe.line,
            "class": classify(probe.line, reply),
            "reply": reply,
            "latency_s": round(time.monotonic() - started, 4),
        }
        results.append(entry)
        print(json.dumps(entry), flush=True)
        await asyncio.sleep(pace)
    return results


async def snapshot(reader: Any, writer: Any, timeout: float) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for line in SNAPSHOT_READS:
        reply = await _query(reader, writer, line, timeout)
        classify(line, reply)  # an unsolicited or stale line aborts, it is not stored
        out[line] = reply
    return out


async def main_async(args: argparse.Namespace) -> int:
    config = load_rig(Path(args.profile))
    probes = build_probe_list(config)
    try:
        reader, writer = await open_serial_port(args.device, baudrate=args.baud)
    except OSError as error:  # serial.SerialException is an OSError
        print(f"ABORT: cannot open {args.device}: {error}", file=sys.stderr)
        return EXIT_ABORT
    try:
        try:
            before = await snapshot(reader, writer, args.timeout)
            results = await run_probes(reader, writer, probes, args.pace, args.timeout)
            after = await snapshot(reader, writer, args.timeout)
        except Abort as error:
            print(f"ABORT: {error}", file=sys.stderr)
            return EXIT_ABORT
    finally:
        writer.close()
    counts = {
        c: sum(r["class"] == c for r in results) for c in ("data", "refusal", "silence")
    }
    changed = {k: [before[k], after[k]] for k in before if before[k] != after[k]}
    Path(args.out).write_text(
        json.dumps(
            {
                "snapshot_before": before,
                "snapshot_after": after,
                "changed": changed,
                "counts": counts,
                "results": results,
            },
            indent=1,
        )
        + "\n"
    )
    print(f"probes {len(results)}: {counts}; snapshot changed: {changed or 'no'}")
    return EXIT_DIFF if changed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--device", required=True, help="socket://HOST:PORT of the bridge"
    )
    parser.add_argument("--profile", default="rigs/ftx1.toml", help="rig profile .toml")
    parser.add_argument("--baud", type=int, default=38400)
    parser.add_argument(
        "--pace", type=float, default=0.3, help="seconds between probes"
    )
    parser.add_argument("--timeout", type=float, default=1.5, help="answer window")
    parser.add_argument("--out", required=True, help="results JSON path")
    sys.exit(asyncio.run(main_async(parser.parse_args())))


if __name__ == "__main__":
    main()
