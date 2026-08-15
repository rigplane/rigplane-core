"""Local WebServer target for the FTX-1 RIT Chromium acceptance test."""

from __future__ import annotations

import argparse
import asyncio
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime._poller_types import (
    CommandQueue,
    SetRitFrequency,
    SetRitStatus,
    SetRitTxStatus,
)
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[2]
SOURCE = SourceMetadata(
    source="test",
    provider="ftx1_rit_fake",
    transport="memory",
)


class Ftx1RitFake:
    """In-memory RIT/XIT intent and readback only; no physical or TX surfaces."""

    backend_id = "ftx1_rit_fake"
    model = "FTX-1"
    connected = True
    control_connected = True
    radio_ready = True
    capabilities = {"rit", "xit"}

    def __init__(self) -> None:
        self.profile = resolve_radio_profile(model=self.model)
        self.state_store = StateStore()
        self._callback: Callable[[Sequence[Observation]], None] | None = None
        self._seed("slow_state", "active", "MAIN")
        self._seed("tx_state", "rit_on", False)
        self._seed("tx_state", "rit_tx", False)
        self._seed("operator_controls", "rit_freq", 0)

    def _observation(self, family: str, name: str, value: Any) -> Observation:
        return Observation(
            path=FieldPath.global_(family, name),
            value=value,
            source=SOURCE,
            timestamp_monotonic=time.monotonic(),
            provider_generation=self.state_store.provider_generation,
        )

    def _seed(self, family: str, name: str, value: Any) -> None:
        self.state_store.apply(self._observation(family, name, value))

    def _readback(self, family: str, name: str, value: Any) -> None:
        if self._callback is None:
            raise RuntimeError("observation poller is not attached")
        self._callback((self._observation(family, name, value),))

    async def set_rit_status(self, on: bool) -> None:
        self._readback("tx_state", "rit_on", bool(on))

    async def set_rit_tx_status(self, on: bool) -> None:
        self._readback("tx_state", "rit_tx", bool(on))

    async def set_rit_frequency(self, freq: int) -> None:
        if (
            not isinstance(freq, int)
            or isinstance(freq, bool)
            or not -9999 <= freq <= 9999
        ):
            raise ValueError("RIT frequency must be a safe integer in [-9999, 9999]")
        self._readback("operator_controls", "rit_freq", freq)

    def create_observation_poller(
        self,
        *,
        callback: Callable[[Sequence[Observation]], None],
        command_queue: CommandQueue | None = None,
    ) -> Ftx1RitPoller:
        self._callback = callback
        return Ftx1RitPoller(self, command_queue)


class Ftx1RitPoller:
    def __init__(self, radio: Ftx1RitFake, queue: CommandQueue | None) -> None:
        self._radio = radio
        self._queue = queue
        self._stopped = asyncio.Event()

    async def start(self) -> None:
        while not self._stopped.is_set():
            if self._queue is None or not self._queue.has_commands:
                if self._queue is None:
                    await asyncio.sleep(0.05)
                else:
                    await self._queue.wait(timeout=0.05)
                continue
            for entry in self._queue.drain_entries():
                try:
                    command = entry.command
                    if isinstance(command, SetRitStatus):
                        await self._radio.set_rit_status(command.on)
                    elif isinstance(command, SetRitTxStatus):
                        await self._radio.set_rit_tx_status(command.on)
                    elif isinstance(command, SetRitFrequency):
                        await self._radio.set_rit_frequency(command.freq)
                    else:
                        raise ValueError(
                            f"unsupported fake intent: {type(command).__name__}"
                        )
                    if entry.future is not None and not entry.future.done():
                        entry.future.set_result(None)
                except Exception as exc:
                    if (
                        entry.command_service is not None
                        and entry.command_id is not None
                    ):
                        entry.command_service.fail_command(
                            entry.command_id, message=str(exc)
                        )
                    if entry.future is not None and not entry.future.done():
                        entry.future.set_exception(exc)

    async def stop(self) -> None:
        self._stopped.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18765, type=int)
    parser.add_argument("--static-dir", default=ROOT / "frontend" / "dist", type=Path)
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    if not args.static_dir.is_dir():
        raise SystemExit(f"frontend build is missing: {args.static_dir}")
    server = WebServer(
        Ftx1RitFake(),
        WebConfig(
            host=args.host,
            port=args.port,
            static_dir=args.static_dir,
            radio_model="FTX-1",
            discovery=False,
            read_only=True,
        ),
    )
    print(f"FTX-1 RIT fake ready at http://{args.host}:{args.port}/?ui=v2", flush=True)
    await server.serve_forever()


if __name__ == "__main__":
    asyncio.run(run())
