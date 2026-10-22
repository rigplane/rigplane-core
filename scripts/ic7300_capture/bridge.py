"""Read-only recording bridge for the bench IC-7300 (USB CI-V).

Wraps ``rigplane_testkit.serial.recorder.SerialCivRecorder`` with a client
frame guard, so a client pointed at ``socket://HOST:PORT`` (the Core server,
``rigplane validate --read-only``, ``probe.py``) can only send the commands
the capture allows. The allowed set is the IC-7610 capture's allow-list
(``scripts/ic7610_capture/probe_list.py``: every ``get_*`` body of the rig
profile, minus the refused command families) plus the exact status/scope reads in ``READ_EXTRA_BODIES`` and the four scope switches
``27 10 00/01`` (panel) and ``27 11 00/01`` (wave data output), which the
scope window needs and which the product's own scope session restores.

Everything else is dropped before the serial port and written to the
recording as ``blocked``; the exit summary prints blocked-chunk counts
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
import importlib
import json
import signal
import sys
from pathlib import Path
from types import ModuleType


def _load_siblings(directory: Path, names: tuple[str, ...]) -> dict[str, ModuleType]:
    """Import top-level script modules from *directory* without leaving a trace.

    The capture scripts of several radios share module names (``probe``,
    ``probe_list``) and import each other by those bare names, so a plain
    ``sys.path`` import would collide with another radio's copy when both are
    loaded in one process (one pytest run). Load ours with the names free,
    then put whatever was registered before back.
    """
    saved = {name: sys.modules.pop(name, None) for name in names}
    sys.path.insert(0, str(directory))
    try:
        return {name: importlib.import_module(name) for name in names}
    finally:
        sys.path.remove(str(directory))
        for name in names:
            sys.modules.pop(name, None)
            if saved[name] is not None:
                sys.modules[name] = saved[name]


_IC7610 = _load_siblings(
    Path(__file__).resolve().parents[1] / "ic7610_capture", ("probe_list", "probe")
)
PROBE = _IC7610["probe"]
PROBE_LIST = _IC7610["probe_list"]
load_profile = PROBE.load_profile
build_allowed_bodies = PROBE_LIST.build_allowed_bodies
is_forbidden = PROBE_LIST.is_forbidden

from rigplane.commands import CONTROLLER_ADDR  # noqa: E402
from rigplane_testkit.serial import CivFrameGuard, SerialCivRecorder  # noqa: E402

#: Scope panel / wave-output switches (``27 10 xx``, ``27 11 xx``).
SCOPE_SWITCH_BODIES = frozenset(
    bytes((0x27, sub, value)) for sub in (0x10, 0x11) for value in (0x00, 0x01)
)


#: Exact read bodies the Core server issues that the probe list leaves out: the
#: PTT and tuner status reads (``1C 00``, ``1C 01`` with NO data byte: a read
#: cannot key; any value byte makes it a write and is refused) and the scope
#: panel / wave-output state reads (``27 10``, ``27 11``) and the scope
#: setting reads with their selector byte (``27 14..1D 00``, ``27 1E rr ee``).
READ_EXTRA_BODIES = frozenset(
    [bytes((0x1C, 0x00)), bytes((0x1C, 0x01)), bytes((0x27, 0x10)), bytes((0x27, 0x11))]
    + [
        bytes((0x27, sub, 0x00))
        for sub in (0x14, 0x15, 0x16, 0x17, 0x19, 0x1A, 0x1B, 0x1C, 0x1D)
    ]
    + [bytes((0x27, 0x1E, rng, edge)) for rng in (1, 2, 3) for edge in (1, 2, 3)]
)


def make_guard(model: str, radio_addr: int) -> CivFrameGuard:
    """The frame guard for ``SerialCivRecorder(client_filter=guard.new_filter)``."""
    allowed = build_allowed_bodies(load_profile(model))

    def guard(frame: bytes) -> bool:
        if len(frame) < 6 or frame[2] != radio_addr or frame[3] != CONTROLLER_ADDR:
            return False  # not a controller request for this radio
        body = frame[4:-1]
        if body in SCOPE_SWITCH_BODIES or body in READ_EXTRA_BODIES:
            return True
        return not is_forbidden(body, allowed)

    return CivFrameGuard(guard)


def blocked_summary(path: Path) -> dict[str, int]:
    """Blocked-frame counts per command byte (contents are never reported)."""
    counts: collections.Counter[str] = collections.Counter()
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row["dir"] == "blocked":
            raw = bytes.fromhex(row["hex"])
            counts[
                f"{raw[4]:02x}" if raw[:2] == b"\xfe\xfe" and len(raw) > 4 else "junk"
            ] += 1
    return dict(sorted(counts.items()))


async def _run(args: argparse.Namespace) -> int:
    rec = SerialCivRecorder(
        args.device,
        args.out,
        baudrate=args.baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        client_filter=make_guard(args.model, args.radio_addr).new_filter,
    )
    await rec.start()
    print(f"bridge up: socket://{args.listen_host}:{rec.listen_port}", flush=True)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(
            NotImplementedError
        ):  # Windows: Ctrl-C cancels the task
            loop.add_signal_handler(sig, stop.set)
    serve = asyncio.ensure_future(rec.serve_forever())
    stopper = asyncio.ensure_future(stop.wait())
    try:
        await asyncio.wait({serve, stopper}, return_when=asyncio.FIRST_COMPLETED)
    finally:  # also on cancellation (Windows Ctrl-C): never leave the port open
        await rec.stop()
        for task in (serve, stopper):
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    print(
        json.dumps(
            {
                "records": rec.records_written,
                "blocked_chunks": rec.blocked_lines,
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
    try:
        return asyncio.run(_run(parser.parse_args(argv)))
    except KeyboardInterrupt:  # Windows Ctrl-C; _run already closed the port
        return 0


if __name__ == "__main__":
    sys.exit(main())
