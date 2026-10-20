"""CLI entry: ``python -m rigplane_testkit.sim serve --radio ic7610``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from rigplane_testkit.sim.serve import DEFAULT_KEEPALIVE_HOLD_S, run_serve


def _control_arg(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    if not sep or not host or not port.isdigit() or int(port) > 65535:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m rigplane_testkit.sim",
        description="Run a rigplane testkit radio simulator as a standalone process.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser(
        "serve", help="run a radio simulator until SIGINT/SIGTERM or POST /stop"
    )
    # The choices list is the extension point for further models
    # (FTX-1 CAT and IC-7300 serial are planned, MOR-3593).
    serve.add_argument(
        "--radio",
        choices=["ic7610"],
        required=True,
        help="simulated radio model",
    )
    serve.add_argument("--seed", type=int, default=0, help="FaultPlan seed (default 0)")
    serve.add_argument(
        "--bind",
        default="127.0.0.1",
        help="simulator bind address (default: 127.0.0.1; 0.0.0.0 is an "
        "explicit opt-in to be reachable from another machine)",
    )
    serve.add_argument(
        "--control",
        type=_control_arg,
        default=("127.0.0.1", 0),
        metavar="HOST:PORT",
        help="local control endpoint address (default: 127.0.0.1:0, an "
        "ephemeral port reported on stdout)",
    )
    serve.add_argument(
        "--trace",
        default=None,
        help="append the event trace as JSON lines to this file",
    )
    serve.add_argument(
        "--keepalive-hold-s",
        type=float,
        default=DEFAULT_KEEPALIVE_HOLD_S,
        help="seconds the radio holds a dead client's session "
        "(default: %(default)s, the recorded real IC-7610 hold)",
    )
    return parser


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    args = build_parser().parse_args()
    if args.command == "serve":
        try:
            asyncio.run(run_serve(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
