"""CLI entry: ``python -m rigplane_testkit.icom record --radio HOST --out FILE``."""

from __future__ import annotations

import argparse
import asyncio

from rigplane_testkit.icom.recorder import IcomSessionRecorder


async def _record(args: argparse.Namespace) -> None:
    rec = IcomSessionRecorder(
        args.radio,
        args.port,
        args.out,
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_port=args.upstream_port,
    )
    await rec.start()
    print(
        f"proxy up: control={rec.control_port} civ={rec.civ_port} "
        f"audio={rec.audio_port} -> {args.radio}:{args.port}, "
        f"writing {args.out} (Ctrl-C to stop)"
    )
    try:
        await rec.serve_forever()
    finally:
        await rec.stop()
        print(f"wrote {rec.records_written} records")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.icom")
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="proxy an Icom LAN session to JSONL")
    rec.add_argument("--radio", required=True, help="radio host")
    rec.add_argument("--port", type=int, default=50001, help="radio control port")
    rec.add_argument("--out", required=True, help="output JSONL path")
    rec.add_argument(
        "--listen-host",
        default="127.0.0.1",
        help="bind address for the client-facing listeners and upstream "
        "sockets (default: 127.0.0.1)",
    )
    rec.add_argument(
        "--listen-port",
        type=int,
        default=0,
        help="fixed client-facing control port; CI-V and audio listeners "
        "bind +1/+2 when nonzero (default: 0 = ephemeral)",
    )
    rec.add_argument(
        "--upstream-port",
        type=int,
        default=0,
        help="fixed local port of the CI-V upstream socket, declared to the "
        "radio in the rewritten conninfo; the audio upstream binds +1 when "
        "nonzero (default: 0 = ephemeral, bound at start)",
    )
    args = parser.parse_args()
    if args.command == "record":
        try:
            asyncio.run(_record(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
