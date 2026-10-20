"""CLI entry: ``python -m rigplane_testkit.sim serve --radio ic7610``."""

from __future__ import annotations

import argparse
import asyncio
import logging

from rigplane_testkit.sim.serve import DEFAULT_KEEPALIVE_HOLD_S, run_serve


def _control_arg(value: str) -> tuple[str, int]:
    host, sep, port = value.rpartition(":")
    bracketed = host.startswith("[") and host.endswith("]")
    if bracketed:
        host = host[1:-1]  # accept the bracketed "[IPv6]:port" spelling
    if not sep or not host or not port.isdigit() or int(port) > 65535:
        raise argparse.ArgumentTypeError(f"expected HOST:PORT, got {value!r}")
    if ":" in host and not bracketed:
        # A bare IPv6 literal ("--control ::1") silently splits at its last
        # colon; the bracketed spelling is the unambiguous form.
        raise argparse.ArgumentTypeError(
            f"bracket IPv6 literals as [host]:port, got {value!r}"
        )
    return host, int(port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.sim")
    parser.description = "Run a rigplane testkit radio simulator standalone."
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser(
        "serve", help="run a radio simulator until SIGINT/SIGTERM or POST /stop"
    )
    serve.add_argument(
        "--radio", choices=["ic7610"], required=True, help="simulated radio model"
    )
    serve.add_argument(
        "--username",
        default=None,
        help="login username the simulator accepts (default: MockIcomRadio's fixed test username)",
    )
    serve.add_argument(
        "--password",
        default=None,
        help="login password the simulator accepts; never printed, logged or returned "
        "(default: the fixed test password)",
    )
    serve.add_argument("--seed", type=int, default=0, help="FaultPlan seed (default 0)")
    serve.add_argument(
        "--bind",
        default="127.0.0.1",
        help="simulator bind address (default: 127.0.0.1; 0.0.0.0 is an explicit "
        "opt-in to reach it from another machine)",
    )
    serve.add_argument(
        "--control",
        type=_control_arg,
        default=("127.0.0.1", 0),
        metavar="HOST:PORT",
        help="local control endpoint; IPv6 literals bracketed as [host]:port "
        "(default: 127.0.0.1:0, an ephemeral port reported on stdout)",
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
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    args = build_parser().parse_args()
    if args.command == "serve":
        try:
            asyncio.run(run_serve(args))
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
