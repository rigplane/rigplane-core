"""IC-7300 scope session: enable the wave stream, measure its rate, restore.

Connects the production serial client through the recording bridge
(``bridge.py``), reads the radio's scope panel / wave-output state with
``get_scope_session_state`` (reads only), enables the scope, counts
completed scope frames for ``--seconds``, then restores the exact state it
read (``restore_scope_session_state``, the call the web server itself
makes at session end) and reads it back. The summary is the per-radio
scope-rate baseline: frames per second, inter-frame interval statistics,
and whether the restore read back identical. Receive side only; the four
scope switch bodies are the only non-GET frames the bridge allows.

Exit codes: 0 done, 3 connect failed, 4 state did not read back identical.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rigplane.backends.config import SerialBackendConfig
from rigplane.backends.factory import create_radio


def interval_stats(stamps: list[float]) -> dict:
    gaps = sorted(b - a for a, b in zip(stamps, stamps[1:]))
    if not gaps:
        return {}
    return {
        "median_ms": round(statistics.median(gaps) * 1000, 1),
        "p95_ms": round(gaps[min(len(gaps) - 1, int(len(gaps) * 0.95))] * 1000, 1),
        "max_ms": round(gaps[-1] * 1000, 1),
    }


async def _run(args: argparse.Namespace) -> int:
    radio = create_radio(
        SerialBackendConfig(
            device=args.serial_port,
            baudrate=args.baud,
            radio_addr=args.radio_addr,
            model=args.model,
        )
    )
    document: dict = {
        "radio": {"model": args.model, "radio_addr": args.radio_addr},
        "started_utc": datetime.now(timezone.utc).isoformat(),
        "baud": args.baud,
        "seconds": args.seconds,
    }
    try:
        await radio.connect()
    except Exception as exc:
        print(f"connect failed: {type(exc).__name__}", file=sys.stderr)
        return 3
    stamps: list[float] = []
    exit_code = 0
    try:
        before = await radio.get_scope_session_state()
        document["state_before"] = {"panel": before[0], "wave_output": before[1]}
        radio.on_scope_data(lambda frame: stamps.append(time.monotonic()))
        try:
            await radio.enable_scope(timeout=args.seconds)
            await asyncio.sleep(args.seconds)
        finally:
            radio.on_scope_data(None)
            await radio.restore_scope_session_state(before)
        await asyncio.sleep(0.5)
        after = await radio.get_scope_session_state()
        document["state_after"] = {"panel": after[0], "wave_output": after[1]}
        document["restored_identical"] = after == before
        if after != before:
            exit_code = 4
    finally:
        await radio.disconnect()

    span = stamps[-1] - stamps[0] if len(stamps) > 1 else 0.0
    document["frames"] = len(stamps)
    document["fps"] = round((len(stamps) - 1) / span, 3) if span > 0 else None
    document["interval"] = interval_stats(stamps)
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document))
    return exit_code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--serial-port", required=True, help="socket://H:P (bridge)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--model", default="IC-7300")
    parser.add_argument("--radio-addr", type=lambda s: int(s, 0), default=0x94)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--out", required=True)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
