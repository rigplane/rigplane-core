"""Memory Store/Clear WebServer targets for the memory seam Chromium tests (MOR-3644).

Simulated radios only — this fake never touches hardware. MemoryJourneyRadio
stands in for the real Icom backend's memory surface
(``src/rigplane/runtime/radio.py``: ``IcomRadio.set_memory_mode`` /
``memory_write`` / ``memory_to_vfo`` / ``memory_clear`` /
``read_memory_entries`` — CI-V 0x08 select, 0x09 write-VFO-to-memory, 0x0A
memory-to-VFO, the profile-declared clear (0x0B or 1A 00 + address + FF) and
the 1A 00 channel read). It models those verbs as an in-memory channel store
keyed by ``(group, channel)`` behind the real WebServer, so the Playwright
seam tests cross browser UI click -> panel-commands -> WebSocket -> server
handler -> backend dispatch -> simulator, and read memory contents back
through ``GET /api/v1/memories`` or a ``memory_to_vfo`` recall.

``--model`` picks the rig profile the fake resolves:

- ``IC-7610`` (default): channel-addressed memory with a verified
  ``[memory].write_layout`` and no ``memory_read`` capability — Store and
  Clear both pass the write-layout admission gate; read-back is a recall.
- ``IC-705``: group+channel-addressed memory with a verified write layout AND
  ``memory_read`` — read-back goes through ``GET /api/v1/memories``; a Clear
  without an explicit group is refused by the server (the panel never sends
  one).
- ``FTX-1``: a ``[memory]`` section with NO ``write_layout`` — Clear is
  refused ``memory_write_unverified`` before anything is enqueued.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.types import MemoryEntry
from rigplane.exceptions import CommandError
from rigplane.web.server import WebConfig, WebServer

ROOT = Path(__file__).resolve().parents[2]
# Non-shipping simulator helpers live at the repository root (as in pytest).
sys.path.insert(0, str(ROOT))
# SerialMockRadio and the journey fake are non-shipping test doubles that
# live under tests/.
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from journey_web_server import JourneyRadio  # noqa: E402
from serial_stub import _MockObservationPoller  # noqa: E402

MODELS = ("IC-7610", "IC-705", "FTX-1")


class MemoryJourneyRadio(JourneyRadio):
    """JourneyRadio plus a simulated MemoryCapable / MemoryReadCapable store.

    The memory channel contents live in ``_channels`` — the fake's analogue
    of the radio's own memory bank. ``memory_write`` copies the active VFO
    slot into the selected channel (the CI-V 0x09 semantics: the "radio"
    builds its own record, which is why this verb needs no host-side write
    layout); ``memory_to_vfo`` copies a stored channel back, and a cleared or
    never-stored channel recalls as a no-op, exactly like a radio with an
    empty slot. ``set_memory_contents``/``set_bsr`` exist only to satisfy the
    MemoryCapable protocol shape; the seam tests never call them.
    """

    def __init__(self, *, model: str = "IC-7610") -> None:
        super().__init__(model=model)
        self._channels: dict[tuple[int, int], tuple[int, str]] = {}
        self._selected_memory: tuple[int, int] = (0, 1)

    async def set_memory_mode(self, channel: int, group: int | None = None) -> None:
        self._selected_memory = (0 if group is None else group, channel)

    async def memory_write(self) -> None:
        slot = self._slots[0][self._vfo_slot[0]]
        self._channels[self._selected_memory] = (slot.freq, slot.mode.name)

    async def memory_to_vfo(self, channel: int) -> None:
        stored = self._channels.get((self._selected_memory[0], channel))
        if stored is None:
            return
        freq, mode = stored
        await self.set_freq(freq)
        await self.set_mode(mode)

    async def memory_clear(self, channel: int, group: int | None = None) -> None:
        self._channels.pop((0 if group is None else group, channel), None)

    async def set_memory_contents(self, mem: Any) -> None:
        raise CommandError("set_memory_contents is not modelled by this simulator")

    async def set_bsr(self, bsr: Any) -> None:
        raise CommandError("set_bsr is not modelled by this simulator")

    async def read_memory_entries(
        self, group: int, channels: Sequence[int]
    ) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for channel in channels:
            stored = self._channels.get((group, channel))
            if stored is None:
                entries.append(MemoryEntry(group=group, channel=channel, empty=True))
            else:
                freq, mode = stored
                entries.append(
                    MemoryEntry(
                        group=group,
                        channel=channel,
                        empty=False,
                        frequency_hz=freq,
                        mode=mode,
                    )
                )
        return entries

    def baseline_observations(self) -> tuple[Observation, ...]:
        """Journey baseline plus the VFO-slot identity the Store path needs.

        ``makeMemoryHandlers().onStore`` reads the ACTIVE slot's identity
        (``main.activeSlot``, ``main.vfoA.freqHz``/``mode``), which the plain
        journey baseline never emits — the journey scenarios store nothing.
        Emit the mock's own A/B slot state so the store/clear affordances
        enable exactly as they do against a radio that answers 0x07 0xD2.
        """
        now = time.monotonic()
        meta = SourceMetadata(
            source="state_poller", provider="serial_mock", native_id="baseline"
        )
        observations: list[Observation] = list(super().baseline_observations())
        for receiver in sorted(self._rx):
            rid = str(receiver)
            observations.append(
                Observation(
                    path=FieldPath.active_slot(rid),
                    value=self._vfo_slot[receiver],
                    source=meta,
                    timestamp_monotonic=now,
                    max_age=None,
                )
            )
            for slot_name in ("A", "B"):
                slot = self._slots[receiver][slot_name]
                for name, value in (
                    ("freq_hz", slot.freq),
                    ("mode", slot.mode.name),
                    ("filter_num", slot.filter_width),
                    ("data_mode", slot.data_mode),
                ):
                    observations.append(
                        Observation(
                            path=FieldPath.vfo_slot(rid, slot_name, "freq_mode", name),
                            value=value,
                            source=meta,
                            timestamp_monotonic=now,
                            max_age=None,
                        )
                    )
        return tuple(observations)

    def create_observation_poller(
        self,
        *,
        callback: Any,
        command_queue: Any = None,
    ) -> "_MemoryObservationPoller":
        return _MemoryObservationPoller(
            self, callback=callback, command_queue=command_queue
        )


class _MemoryObservationPoller(_MockObservationPoller):
    """The mock observation poller plus the memory command family.

    Everything else still falls through to the shared drain; the memory verbs
    the web control handler enqueues (SetMemoryMode / MemoryWrite /
    MemoryToVfo / MemoryClear) would otherwise be silently ignored by the
    plain mock's ``case _`` arm.
    """

    async def _execute_command(self, cmd: Any) -> None:
        from rigplane._poller_types import (
            MemoryClear,
            MemoryToVfo,
            MemoryWrite,
            SetMemoryMode,
        )

        radio = self._radio
        match cmd:
            case SetMemoryMode(channel=channel, group=group):
                await radio.set_memory_mode(channel, group=group)
            case MemoryWrite():
                await radio.memory_write()
            case MemoryToVfo(channel=channel):
                await radio.memory_to_vfo(channel)
            case MemoryClear(channel=channel, group=group):
                await radio.memory_clear(channel, group=group)
            case _:
                await super()._execute_command(cmd)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=18771, type=int)
    parser.add_argument("--static-dir", default=ROOT / "frontend" / "dist", type=Path)
    parser.add_argument(
        "--model",
        default="IC-7610",
        choices=MODELS,
        help="rig profile the simulated radio resolves (default: IC-7610)",
    )
    parser.add_argument(
        "--freq",
        default=None,
        type=int,
        help="set the radio's MAIN frequency in Hz before serving (default: baseline)",
    )
    return parser.parse_args()


async def run() -> None:
    args = parse_args()
    if not args.static_dir.is_dir():
        raise SystemExit(f"frontend build is missing: {args.static_dir}")
    radio = MemoryJourneyRadio(model=args.model)
    await radio.connect()
    try:
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
                read_only=False,
            ),
        )
        await server.serve_forever(
            on_started=lambda: print(
                f"{radio.model} memory journey fake ready at "
                f"http://{args.host}:{args.port}/?ui=v2",
                flush=True,
            )
        )
    finally:
        await radio.disconnect()


if __name__ == "__main__":
    asyncio.run(run())
