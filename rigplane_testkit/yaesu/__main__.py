"""CLI entry: ``python -m rigplane_testkit.yaesu record --device DEV --out FILE``.

Bridges a real Yaesu CAT serial port to a local TCP listener and records the
session to JSONL, RX-only: the bridge forwards only the READ lines of the rig
profile (``rigplane_testkit.yaesu.CatReadOnlyGuard``) and logs every other
line as ``blocked``. Point the Core server (or any CAT client) at
``socket://127.0.0.1:<port>``; set a rig profile name or file with
``--profile`` (default ``ftx1``).
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.serial.recorder import SerialCivRecorder
from rigplane_testkit.yaesu.cat_fake import _RIGS_DIR
from rigplane_testkit.yaesu.recorder import CatReadOnlyGuard


async def _record(args: argparse.Namespace) -> None:
    profile = Path(args.profile)
    config = load_rig(profile if profile.suffix else _RIGS_DIR / f"{args.profile}.toml")
    guard = CatReadOnlyGuard.from_config(config)
    rec = SerialCivRecorder(
        args.device,
        args.out,
        baudrate=args.baud or config.default_baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        client_filter=guard.new_filter,
    )
    await rec.start()
    print(
        f"read-only CAT bridge up: socket://{args.listen_host}:{rec.listen_port} -> "
        f"{args.device}, writing {args.out} (Ctrl-C to stop)",
        flush=True,
    )
    try:
        await rec.serve_forever()
    finally:
        await rec.stop()
        print(f"wrote {rec.records_written} records, blocked {rec.blocked_lines} lines")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.yaesu")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="bridge a Yaesu CAT port to JSONL, read-only")
    rec.add_argument("--device", required=True, help="serial device path")
    rec.add_argument("--out", required=True, help="output JSONL path")
    rec.add_argument(
        "--profile", default="ftx1", help="rig profile name or .toml path (ftx1)"
    )
    rec.add_argument(
        "--baud", type=int, default=0, help="baud rate (default: the profile's)"
    )
    rec.add_argument("--listen-host", default="127.0.0.1", help="TCP bind address")
    rec.add_argument(
        "--listen-port", type=int, default=0, help="TCP bind port (0 = ephemeral)"
    )
    args = parser.parse_args()
    if args.command == "record":
        try:
            asyncio.run(_record(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
