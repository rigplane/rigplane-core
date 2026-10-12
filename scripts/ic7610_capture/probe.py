"""IC-7610 capture driver: paced read-only probes over a recorded link.

Connects the production radio client (``rigplane.backends.factory
.create_radio``) and sends every probe from ``probe_list.build_probe_list``
once, paced, so the recording around the link holds the radio's answer
class per command: ``data`` / ``ack`` / ``nak`` / ``silence`` / ``error``,
with latency. Read-only by construction: ``run_probes`` sends a frame only
when ``probe_list.is_forbidden`` accepts its body against the allow-list
built once from the profile by ``probe_list.build_allowed_bodies``.

The probe loop drives ``CivRuntime.execute_civ_transaction`` directly:
unlike ``send_civ_raw``, whose GET path (``CivRuntime._execute_civ_raw``)
registers only a response waiter, the transaction registers a NAK-only
waiter too, so a CI-V refusal (command 0xFA) settles the transaction and
is classified ``nak`` instead of running out the answer window. A bare
ACK (0xFB) settles no waiter at all — the ACK waiter the transaction
registers is NAK-only, and ``core/civ.py: CivRequestTracker.resolve``
skips NAK-only waiters for ACK events — so ``run_probes`` detects it
through the radio's public ``civ_stats()`` counter ``ack_orphans`` (where
the tracker parks an unclaimed ACK/NAK) rising across the answer window,
and classifies the probe ``ack`` without reply bytes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rigplane.radio import IcomRadio

sys.path.insert(0, str(Path(__file__).resolve().parent))

from rigplane.backends.config import (
    BackendConfig,
    LanBackendConfig,
    SerialBackendConfig,
)
from rigplane.backends.factory import create_radio
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, command_carries_sub
from rigplane.core.types import CivFrame
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import discover_rigs

from probe_list import (
    Probe,
    build_allowed_bodies,
    build_probe_list,
    is_forbidden,
)

__all__ = ["build_frame", "classify_reply", "main", "run_probes", "summarize"]

CLASSES = ("data", "ack", "nak", "silence", "error")

_EXIT_CONNECT_FAILED = 3


def build_frame(frame_body: bytes, radio_addr: int) -> bytes:
    """Wrap *frame_body* as ``FE FE <radio_addr> E0 <body> FD``.

    Uses ``rigplane.commands.build_civ_frame``, splitting the body into
    command / sub / data with the same ``command_carries_sub`` predicate
    the runtime's own builders use.
    """
    command = frame_body[0]
    rest = frame_body[1:]
    if command_carries_sub(command) and rest:
        sub, data = rest[0], rest[1:]
    else:
        sub, data = None, rest
    return build_civ_frame(
        radio_addr, CONTROLLER_ADDR, command, sub=sub, data=data or None
    )


def classify_reply(frame: CivFrame) -> str:
    """Classify one answered CI-V transaction by its reply frame.

    A NAK is a reply whose command byte is 0xFA, an ACK 0xFB, anything
    else is data. Silence never reaches here — it surfaces as a timeout
    in :func:`run_probes`.
    """
    if frame.command == 0xFA:
        return "nak"
    if frame.command == 0xFB:
        return "ack"
    return "data"


def summarize(results: list[dict]) -> dict[str, int]:
    """Count probe result classes over the fixed class set."""
    summary = {name: 0 for name in CLASSES}
    for entry in results:
        summary[entry["class"]] += 1
    return summary


async def run_probes(
    radio: "IcomRadio",
    probes: list[Probe],
    allowed: frozenset[bytes] | set[bytes],
    pace: float,
    timeout: float,
) -> list[dict]:
    """Send each probe once and classify the radio's answer.

    *radio* must be connected. Each frame is gated by
    ``probe_list.is_forbidden`` against *allowed*, built once per profile
    by ``probe_list.build_allowed_bodies``; a refused probe is recorded
    as ``error`` and never leaves the wire. A transaction that runs out
    the answer window is ``ack`` when the radio's ``civ_stats()``
    ``ack_orphans`` counter rose across the window — a bare ACK parked
    by ``core/civ.py: CivRequestTracker.resolve``, which skips the
    NAK-only waiter this transaction registered — and ``silence``
    otherwise; an ``ack`` probe carries no ``reply_hex`` because the
    counter carries no frame.
    """
    results: list[dict] = []
    civ_runtime = radio._civ_runtime
    for probe_ in probes:
        if is_forbidden(probe_.frame_body, allowed):
            _record_and_log(results, probe_, None, "error", None, 0.0)
            continue
        frame = build_frame(probe_.frame_body, radio._radio_addr)
        orphans_before = radio.civ_stats()["ack_orphans"]
        started = time.monotonic()
        try:
            transaction = await civ_runtime.execute_civ_transaction(
                frame, expect="data", timeout=timeout
            )
        except asyncio.TimeoutError:
            acked = radio.civ_stats()["ack_orphans"] > orphans_before
            _record_and_log(
                results, probe_, frame, "ack" if acked else "silence", None, started
            )
        except Exception:
            _record_and_log(results, probe_, frame, "error", None, started)
        else:
            _record_and_log(
                results,
                probe_,
                frame,
                classify_reply(transaction.frame),
                (transaction.frame_bytes or b"").hex() or None,
                started,
            )
        if pace:
            await asyncio.sleep(pace)
    return results


def _record_and_log(
    results: list[dict],
    probe_: Probe,
    frame: bytes | None,
    outcome: str,
    reply_hex: str | None,
    started: float | None,
) -> None:
    latency_ms = round((time.monotonic() - started) * 1000.0, 1) if started else 0.0
    results.append(
        {
            "name": probe_.name,
            "frame_hex": frame.hex() if frame else probe_.frame_body.hex(),
            "class": outcome,
            "latency_ms": latency_ms,
            "reply_hex": reply_hex,
        }
    )
    print(f"{probe_.name:<28} {outcome:<8} {latency_ms:8.1f} ms  {reply_hex or '-'}")


def load_profile(model: str) -> RadioProfile:
    """Load the rig profile whose ``[radio].model`` equals *model*."""
    rigs_dir = Path(__file__).resolve().parents[2] / "rigs"
    configs = discover_rigs(rigs_dir)
    config = configs.get(model)
    if config is None:
        available = ", ".join(sorted(configs)) or "none"
        raise SystemExit(
            f"no rig profile for model {model!r} in {rigs_dir} ({available})"
        )
    return config.to_profile()


def build_backend_config(args: argparse.Namespace, password: str) -> BackendConfig:
    if args.backend == "lan":
        return LanBackendConfig(
            host=args.host,
            port=args.control_port,
            username=args.user,
            password=password,
            radio_addr=args.radio_addr,
            model=args.model,
        )
    return SerialBackendConfig(
        device=args.serial_port,
        baudrate=args.baud,
        radio_addr=args.radio_addr,
        model=args.model,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--backend", choices=("lan", "serial"), required=True)
    parser.add_argument("--host", help="radio hostname (lan)")
    parser.add_argument(
        "--control-port", type=int, default=50001, help="lan control port"
    )
    parser.add_argument("--user", default="", help="lan username")
    parser.add_argument("--pass-file", help="file holding the lan password")
    parser.add_argument("--serial-port", help="serial device (e.g. socket://H:P)")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--model", default="IC-7610")
    parser.add_argument("--radio-addr", type=lambda s: int(s, 0), default=0x98)
    parser.add_argument("--out", required=True, help="output JSON path")
    parser.add_argument(
        "--pace", type=float, default=0.3, help="seconds between probes"
    )
    parser.add_argument(
        "--timeout", type=float, default=1.5, help="per-probe answer window"
    )
    parser.add_argument("--limit", type=int, help="send only the first N probes")
    args = parser.parse_args(argv)
    if args.backend == "lan":
        missing = [
            name
            for name, value in (
                ("--host", args.host),
                ("--user", args.user),
                ("--pass-file", args.pass_file),
            )
            if not value
        ]
        if missing:
            parser.error(f"--backend lan requires {', '.join(missing)}")
    elif not args.serial_port:
        parser.error("--backend serial requires --serial-port")
    return args


async def _run(args: argparse.Namespace) -> int:
    profile = load_profile(args.model)
    probes = build_probe_list(profile)
    allowed = build_allowed_bodies(profile)
    if args.limit is not None:
        probes = probes[: args.limit]

    password = (
        Path(args.pass_file).read_text(encoding="utf-8").strip()
        if args.pass_file
        else ""
    )
    radio = create_radio(build_backend_config(args, password))
    document = {
        "radio": {"model": args.model, "radio_addr": args.radio_addr},
        "backend": args.backend,
        "started_utc": datetime.now(timezone.utc).isoformat(),
    }
    try:
        await radio.connect()
    except Exception as exc:
        print(f"connect failed: {type(exc).__name__}", file=sys.stderr)
        return _EXIT_CONNECT_FAILED
    try:
        results = await run_probes(radio, probes, allowed, args.pace, args.timeout)
    finally:
        await radio.disconnect()

    document["probes"] = results
    document["summary"] = summarize(results)
    Path(args.out).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_run(parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
