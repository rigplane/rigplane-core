"""CLI entry: ``python -m rigplane_testkit.serial record --device DEV --out FILE``."""

from __future__ import annotations

import argparse
import asyncio

from rigplane_testkit.serial.recorder import SerialCivRecorder


async def _record(args: argparse.Namespace) -> None:
    rec = SerialCivRecorder(
        args.device,
        args.out,
        baudrate=args.baud,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
    )
    await rec.start()
    print(
        f"bridge up: socket://{args.listen_host}:{rec.listen_port} -> "
        f"{args.device}, writing {args.out} (Ctrl-C to stop)"
    )
    try:
        await rec.serve_forever()
    finally:
        await rec.stop()
        print(f"wrote {rec.records_written} records")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.serial")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="bridge a serial CI-V port to JSONL")
    rec.add_argument("--device", required=True, help="serial device path")
    rec.add_argument("--out", required=True, help="output JSONL path")
    rec.add_argument("--baud", type=int, default=115200, help="baud rate")
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
