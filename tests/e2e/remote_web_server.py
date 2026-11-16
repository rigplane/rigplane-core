"""Local WebServer target for the remote/browser E2E rows (L1-L3, P0).

The operator-journey fake (tests/e2e/journey_web_server.py) refuses
``ptt_on``: its SerialMockRadio opens no device and no managed-transmit
authority is composed. The remote rows need a fake whose keyed state is
observable, so this server drives the real ``IcomRadio`` against the
IC-7610 LAN fake (``stateful_ptt=True, ptt_on_session_loss="hold"``) with
the production managed TX composition attached, and serves frontend/dist
like the journey fake plus the gated WebRTC answerer for the P0 probe.

Simulator observation sidecar (test-only, same process): a minimal HTTP
server on --sim-port answers

    GET  /sim          -> SimSnapshot as JSON (ptt_on, freq_hz, ptt_transitions
                          [[unix_s, bool], ...]: the simulator's own PTT journal)
    GET  /sim/contract -> the SimSnapshot field annotations; the Node consumer
                          validates every /sim response against them
    POST /sim/freq  -> {"hz": int} sets the simulator frequency silently
                       (the radio's state poller picks it up, ~2 s cadence)

so the harness never needs a test hook inside src/ to read "keyed".
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import dataclasses
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# Non-shipping simulator helpers live at the repository root (as in pytest).
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from testkit.ic7610_fakes import ic7610_lan_fake  # noqa: E402

from rigplane.radio import IcomRadio  # noqa: E402
from rigplane.runtime.managed_tx_composition import (  # noqa: E402
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore  # noqa: E402
from rigplane.web.server import WebConfig, WebServer  # noqa: E402
from rigplane.web.web_startup import (  # noqa: E402
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)


@dataclasses.dataclass(frozen=True)
class SimSnapshot:
    """The producer-owned shape of ``GET /sim`` (one definition, serialised
    with ``dataclasses.asdict``; the Node harness validates against the
    annotations served at ``/sim/contract``)."""

    ptt_on: bool
    freq_hz: int
    #: ``MockIcomRadio.ptt_transitions`` as (unix seconds, keyed).
    ptt_transitions: tuple[tuple[float, bool], ...]


def sim_snapshot(sim: Any) -> SimSnapshot:
    """Read the simulator; its journal's monotonic stamps become wall-clock
    seconds (the clock the Node side compares) with one offset per read."""
    wall_minus_monotonic = time.time() - time.monotonic()
    return SimSnapshot(
        ptt_on=bool(sim.ptt_on),
        # The simulator keeps the current frequency private (set_frequency
        # exists, no public getter); read the slot the recorded answer table
        # and the CI-V handlers share, as journey_web_server.py does.
        freq_hz=int(sim._frequency),  # noqa: SLF001
        ptt_transitions=tuple(
            (stamp + wall_minus_monotonic, keyed)
            for stamp, keyed in sim.ptt_transitions
        ),
    )


def sim_contract() -> dict[str, str]:
    """Field name -> annotation text of :class:`SimSnapshot`."""
    return {field.name: str(field.type) for field in dataclasses.fields(SimSnapshot)}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18780, type=int)
    parser.add_argument("--sim-port", default=None, type=int)
    parser.add_argument("--static-dir", default=ROOT / "frontend" / "dist", type=Path)
    return parser.parse_args()


class _SimSidecar:
    """Minimal HTTP observation endpoint over the live simulator."""

    def __init__(self, sim: Any) -> None:
        self._sim = sim

    async def serve(self, host: str, port: int) -> asyncio.AbstractServer:
        async def handler(
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter
        ) -> None:
            try:
                await self._handle(reader, writer)
            except Exception:  # noqa: BLE001 - diagnostics only, then close
                import traceback

                traceback.print_exc()
            finally:
                writer.close()

        return await asyncio.start_server(handler, host, port)

    async def _handle(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        request = (await reader.readline()).decode()
        parts = request.split()
        if len(parts) < 2:
            await self._respond(writer, 400, {"error": "bad request line"})
            return
        method, path = parts[0], parts[1]
        headers: dict[str, str] = {}
        while True:
            line = await reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break
            name, _, value = line.decode().partition(":")
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        body = await reader.readexactly(length) if length else b""

        if method == "GET" and path == "/sim":
            await self._respond(
                writer, 200, dataclasses.asdict(sim_snapshot(self._sim))
            )
        elif method == "GET" and path == "/sim/contract":
            await self._respond(writer, 200, sim_contract())
        elif method == "POST" and path == "/sim/freq":
            hz = int(json.loads(body)["hz"])
            self._sim.set_frequency(hz)
            await self._respond(writer, 200, {"ok": True, "freq_hz": hz})
        else:
            await self._respond(writer, 404, {"error": "not found"})

    async def _respond(
        self, writer: asyncio.StreamWriter, status: int, payload: dict[str, Any]
    ) -> None:
        body = json.dumps(payload).encode()
        writer.write(
            f"HTTP/1.1 {status} {'OK' if status == 200 else 'ERR'}\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Content-Type: application/json\r\n"
            "Connection: close\r\n\r\n".encode()
            + body
        )
        await writer.drain()


async def run() -> None:
    args = parse_args()
    if not args.static_dir.is_dir():
        raise SystemExit(f"frontend build is missing: {args.static_dir}")
    sim_port = args.sim_port if args.sim_port is not None else args.port + 1
    # Production wiring over the LAN fake, as in
    # tests/test_webrtc_attach_path.py::_stand_up_stack: the hold-loss fake,
    # the composed managed TX authority, and the gated WebRTC answerer.
    sim = ic7610_lan_fake(
        single_owner=True,
        stateful_ptt=True,
        ptt_on_session_loss="hold",
        # The browser voice-TX path arms the radio's audio route before it
        # keys; without enable_audio the UI's ptt_on is accepted (ok) but
        # keying never completes on the fake.
        enable_audio=True,
        capture_tx_audio=True,
    )
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
        watchdog_timeout=1.5,
    )
    composition: ManagedTxComposition | None = None
    sidecar = _SimSidecar(sim)
    sim_server: asyncio.AbstractServer | None = None
    web: WebServer | None = None
    try:
        config_path = (
            Path(tempfile.gettempdir()) / f"remote-e2e-managed-tx-{args.port}.json"
        )
        ManagedTxTotConfigStore(config_path).set_timeout_seconds(30.0)
        composition = ManagedTxComposition(radio, config_path=config_path)
        install_managed_tx_composition(radio, composition)
        await radio.connect()
        web = WebServer(
            radio,
            WebConfig(
                host=args.host,
                port=args.port,
                static_dir=args.static_dir,
                radio_model=radio.model,
                discovery=False,
                webrtc_enabled=True,
            ),
        )
        prepare_managed_tx_observation_generation(web)
        await composition.bind_state_store(web.command_state_store)
        attach_managed_tx_composition(web, composition)
        sim_server = await sidecar.serve("127.0.0.1", sim_port)
        await web.serve_forever(
            on_started=lambda: print(
                f"{radio.model} remote fake ready at http://{args.host}:{args.port}/?ui=v2 "
                f"(sim sidecar http://127.0.0.1:{sim_port}/sim)",
                flush=True,
            )
        )
    finally:
        if sim_server is not None:
            sim_server.close()
        if web is not None:
            with contextlib.suppress(Exception):
                await web.stop()
        if composition is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(
                    composition.shutdown(termination=asyncio.Event()), 5.0
                )
        with contextlib.suppress(Exception):
            await asyncio.wait_for(radio.disconnect(), 10.0)
        with contextlib.suppress(Exception):
            await sim.stop()


if __name__ == "__main__":
    asyncio.run(run())
