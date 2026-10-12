"""Local WebServer target for the operator-journey Chromium test.

Serves the built frontend over a real ``WebServer`` whose radio is the
in-memory ``SerialMockRadio`` with its default IC-7610 profile; no serial
device is opened and nothing reaches a radio.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[2]
# SerialMockRadio is a non-shipping test double that lives under tests/.
sys.path.insert(0, str(ROOT / "tests"))
from serial_stub import SerialMockRadio  # noqa: E402


class JourneyRadio(SerialMockRadio):
    """SerialMockRadio plus the active-receiver observation the frequency entry needs."""

    # Production counterpart: CivRuntime._observations_from_frame in src/rigplane/runtime/_civ_rx.py (CI-V 0x07 0xD2 reply -> slow_state.active).
    def baseline_observations(self) -> tuple[Observation, ...]:
        active_receiver = Observation(
            path=FieldPath.global_("slow_state", "active"),
            value="MAIN",
            source=SourceMetadata(
                source="state_poller", provider="serial_mock", native_id="baseline"
            ),
            timestamp_monotonic=time.monotonic(),
            max_age=None,
        )
        return (*super().baseline_observations(), active_receiver)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18766, type=int)
    parser.add_argument("--static-dir", default=ROOT / "frontend" / "dist", type=Path)
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="start the WebServer with WebConfig.read_only=True",
    )
    parser.add_argument(
        "--freq",
        default=None,
        type=int,
        help="set the radio's MAIN frequency in Hz before serving (default: SerialMockRadio baseline)",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    if not args.static_dir.is_dir():
        raise SystemExit(f"frontend build is missing: {args.static_dir}")
    radio = JourneyRadio()
    await radio.connect()
    if args.freq is not None:
        await radio.set_freq(args.freq)
    server = WebServer(
        radio,
        WebConfig(
            host=args.host,
            port=args.port,
            static_dir=args.static_dir,
            radio_model=radio.model,
            discovery=False,
            read_only=args.read_only,
        ),
    )
    await server.serve_forever(
        on_started=lambda: print(
            f"{radio.model} journey fake ready at http://{args.host}:{args.port}/?ui=v2",
            flush=True,
        )
    )


if __name__ == "__main__":
    asyncio.run(run())
