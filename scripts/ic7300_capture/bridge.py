"""Read-only recording bridge for the bench IC-7300 (USB CI-V).

Wraps ``rigplane_testkit.serial.recorder.SerialCivRecorder`` with a client
frame guard, so a client pointed at ``socket://HOST:PORT`` (the Core server,
``rigplane validate --read-only``, ``probe.py``) can only send the commands
the capture allows. The allowed set is the IC-7610 capture's allow-list
(``scripts/ic7610_capture/probe_list.py``: every ``get_*`` body of the rig
profile, minus the refused command families) plus the four scope switches
``27 10 00/01`` (panel) and ``27 11 00/01`` (wave data output), which the
scope window needs and which the product's own scope session restores.

Everything else is dropped before the serial port and written to the
recording as ``c2r_blocked``; the exit summary prints blocked-frame counts
per command, never frame contents.

Usage::

    python scripts/ic7300_capture/bridge.py --device /dev/cu.SLAB_USBtoUART \\
        --out ~/captures/ic7300-probe.jsonl [--model IC-7300] [--baud 115200]
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import contextlib
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ic7610_capture"))

from probe import load_profile  # noqa: E402
from probe_list import build_allowed_bodies, is_forbidden  # noqa: E402

from rigplane.commands import CONTROLLER_ADDR  # noqa: E402
from rigplane_testkit.serial.recorder import SerialCivRecorder  # noqa: E402

#: Scope panel / wave-output switches (``27 10 xx``, ``27 11 xx``).
SCOPE_SWITCH_BODIES = frozenset(
    bytes((0x27, sub, value)) for sub in (0x10, 0x11) for value in (0x00, 0x01)
)


def make_guard(model: str, radio_addr: int):
    """Predicate over one client frame: ``True`` = forward to the radio."""
    allowed = build_allowed_bodies(load_profile(model))

    def guard(frame: bytes) -> bool:
        if len(frame) < 6 or frame[2] != radio_addr or frame[3] != CONTROLLER_ADDR:
            return False  # not a controller request for this radio
        body = frame[4:-1]
        if body in SCOPE_SWITCH_BODIES:
            return True
        return not is_forbidden(body, allowed)

    return guard


def blocked_summary(path: Path) -> dict[str, int]:
    """Blocked-frame counts per command byte (contents are never reported)."""
    counts: collections.Counter[str] = collections.Counter()
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row["dir"] == "c2r_blocked":
            raw = bytes.fromhex(row["hex"])
            counts[f"{raw[4]:02x}" if len(raw) > 4 else "junk"] += 1
    return dict(sorted(counts.items()))


async def _run(args: argparse.Namespace) -> int:
    rec = SerialCivRecorder(
        args.device,
        args.out,
        baudrate=args.baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        c2r_guard=make_guard(args.model, args.radio_addr),
    )
    await rec.start()
    print(f"bridge up: socket://{args.listen_host}:{rec.listen_port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    serve = asyncio.ensure_future(rec.serve_forever())
    stopper = asyncio.ensure_future(stop.wait())
    await asyncio.wait({serve, stopper}, return_when=asyncio.FIRST_COMPLETED)
    await rec.stop()
    for task in (serve, stopper):
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    print(
        json.dumps(
            {
                "records": rec.records_written,
                "blocked_frames": rec.blocked_frames,
                "blocked_by_command": blocked_summary(Path(args.out)),
            }
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--device", required=True, help="serial device path")
    parser.add_argument("--out", required=True, help="recording JSONL path")
    parser.add_argument("--model", default="IC-7300")
    parser.add_argument("--radio-addr", type=lambda s: int(s, 0), default=0x94)
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=0)
    return asyncio.run(_run(parser.parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
