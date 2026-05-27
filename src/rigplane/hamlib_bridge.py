"""Local Hamlib A1 bridge runner — MOR-166 slice 1.

A transparent CI-V byte transport between a stock Hamlib ``rigctld`` and a
RigPlane radio, built on the MOR-164 raw CI-V pipe. RigPlane owns the real
transport; Hamlib stays the sole CAT master.

    rigctl client → rigctld (front) → ``-r <host>:<back-port>`` → HamlibBridge
        ↳ TX: radio.send_civ_raw_fire_and_forget(frame)
        ↳ RX: radio.add_raw_civ_listener(cb) → frame written back to rigctld

This is an internal module: no UI, no Pro workflow, no assisted discovery, and
no credential handling (the caller owns the already-connected radio). Session
ownership / poller quiescing (slice 2) and non-Icom serial rigs (slice 3) are
intentionally out of scope here.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

logger = logging.getLogger(__name__)

# Conservative defaults for the Hamlib side: let RigPlane own pacing, never let a
# bare ACK look like a timeout, force CAT PTT (so PTT rides the data path, not
# RTS/DTR which cannot cross TCP), and disable Hamlib's state cache.
DEFAULT_RIGCTLD_CONF = (
    "retry=0,timeout=3000,post_write_delay=0,write_delay=0,cache_timeout=0,ptt_type=RIG"
)

_CIV_PREAMBLE = b"\xfe\xfe"
_CIV_TERMINATOR = 0xFD


@dataclass(slots=True)
class BridgeFrame:
    """One CI-V frame crossing the bridge — a trace/audit record.

    ``direction`` is relative to the radio: ``"tx"`` = Hamlib → radio (a frame
    rigctld emitted, forwarded to the real radio), ``"rx"`` = radio → Hamlib.
    CI-V frames carry no credentials, so the raw bytes are safe to record.
    """

    t_ms: float
    direction: str
    frame: bytes

    @property
    def hex(self) -> str:
        return self.frame.hex(" ")


class RawCivPipe(Protocol):
    """Minimal radio surface the bridge needs — the MOR-164 raw CI-V pipe."""

    async def send_civ_raw_fire_and_forget(self, frame: bytes) -> None: ...

    def add_raw_civ_listener(self, callback: Callable[[bytes], Any]) -> Any: ...


class HamlibBridge:
    """Runs a back-side CI-V proxy and (optionally) a stock ``rigctld``.

    Typical use::

        async with HamlibBridge(radio, model="3078", civaddr=0x98) as bridge:
            # rigctld is now translating rigctl text on bridge.front_port and
            # emitting raw CI-V to the real radio via RigPlane.
            ...

    For tests, call :meth:`open_transport` to bring up just the proxy (no
    rigctld) and drive the back-side socket directly.
    """

    def __init__(
        self,
        radio: RawCivPipe,
        *,
        model: str,
        civaddr: int | None = None,
        host: str = "127.0.0.1",
        front_port: int = 4532,
        rigctld_path: str | None = None,
        rigctld_conf: str = DEFAULT_RIGCTLD_CONF,
        stderr_path: str | None = None,
        on_frame: Callable[[BridgeFrame], None] | None = None,
        trace_maxlen: int = 10_000,
    ) -> None:
        self._radio = radio
        self.model = model
        self.civaddr = civaddr
        self.host = host
        self.front_port = front_port
        self._rigctld_path = rigctld_path or shutil.which("rigctld") or "rigctld"
        self._rigctld_conf = rigctld_conf
        self._stderr_path = stderr_path
        self._on_frame = on_frame
        self.frames: deque[BridgeFrame] = deque(maxlen=trace_maxlen)

        self._server: asyncio.AbstractServer | None = None
        self._back_port: int | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._subscription: Any = None
        self._proc: asyncio.subprocess.Process | None = None
        self._t0 = time.monotonic()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def back_port(self) -> int:
        if self._back_port is None:
            raise RuntimeError("bridge transport is not open")
        return self._back_port

    async def open_transport(self) -> int:
        """Bind the back-side listener and wire the radio's raw CI-V pipe.

        Returns the chosen back-side TCP port (rigctld's ``-r`` target).
        """
        if self._server is not None:
            return self.back_port
        self._subscription = self._radio.add_raw_civ_listener(self._on_radio_frame)
        self._server = await asyncio.start_server(self._handle_back, self.host, 0)
        self._back_port = self._server.sockets[0].getsockname()[1]
        await self._server.start_serving()
        logger.info(
            "hamlib-bridge: back-side CI-V listener up on %s:%d",
            self.host,
            self._back_port,
        )
        return self._back_port

    def rigctld_argv(self) -> list[str]:
        """The exact argv used to launch stock rigctld against this bridge."""
        argv = [
            self._rigctld_path,
            "-m",
            self.model,
            "-r",
            f"{self.host}:{self.back_port}",
            "-t",
            str(self.front_port),
        ]
        if self.civaddr is not None:
            argv += ["-c", str(self.civaddr)]
        argv += ["-C", self._rigctld_conf]
        return argv

    async def spawn_rigctld(self) -> asyncio.subprocess.Process:
        """Launch stock rigctld pointed at the back-side listener."""
        stderr = (
            open(self._stderr_path, "wb")  # noqa: SIM115 - lifetime tied to proc
            if self._stderr_path
            else asyncio.subprocess.DEVNULL
        )
        self._proc = await asyncio.create_subprocess_exec(
            *self.rigctld_argv(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=stderr,
        )
        logger.info("hamlib-bridge: spawned rigctld pid=%s", self._proc.pid)
        return self._proc

    async def start(self) -> None:
        """Open the transport and spawn rigctld."""
        await self.open_transport()
        await self.spawn_rigctld()

    async def stop(self) -> None:
        """Tear down rigctld, the listener, and the raw-pipe subscription."""
        if self._proc is not None and self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                self._proc.kill()
        self._proc = None

        if self._subscription is not None:
            self._subscription.close()
            self._subscription = None

        if self._server is not None:
            self._server.close()
            self._server = None
        self._back_port = None
        self._writer = None

    async def __aenter__(self) -> HamlibBridge:
        await self.start()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.stop()

    # ------------------------------------------------------------------
    # Transparent pipe
    # ------------------------------------------------------------------

    def _record(self, direction: str, frame: bytes) -> None:
        rec = BridgeFrame(
            t_ms=round((time.monotonic() - self._t0) * 1000, 1),
            direction=direction,
            frame=frame,
        )
        self.frames.append(rec)
        if self._on_frame is not None:
            try:
                self._on_frame(rec)
            except Exception:  # noqa: BLE001 - never let an audit sink break the pipe
                logger.exception("hamlib-bridge: on_frame sink raised")

    def _on_radio_frame(self, frame: bytes) -> None:
        """Raw CI-V pipe listener: forward a radio frame to rigctld (RX)."""
        self._record("rx", frame)
        writer = self._writer
        if writer is not None and not writer.is_closing():
            try:
                writer.write(frame)  # tiny CI-V frame; OS buffer handles it
            except Exception:  # noqa: BLE001
                logger.debug("hamlib-bridge: write to rigctld failed", exc_info=True)

    async def _handle_back(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._writer = writer
        peer = writer.get_extra_info("peername")
        logger.info("hamlib-bridge: rigctld connected from %s", peer)
        buf = bytearray()
        try:
            while True:
                data = await reader.read(4096)
                if not data:
                    break
                buf += data
                for frame in self._extract_frames(buf):
                    self._record("tx", frame)
                    try:
                        await self._radio.send_civ_raw_fire_and_forget(frame)
                    except Exception:  # noqa: BLE001
                        logger.exception("hamlib-bridge: radio TX failed")
        except (ConnectionError, OSError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None

    @staticmethod
    def _extract_frames(buf: bytearray) -> list[bytes]:
        """Pull complete ``FE FE … FD`` frames out of *buf* (mutates it)."""
        frames: list[bytes] = []
        while True:
            end = buf.find(_CIV_TERMINATOR)
            if end < 0:
                break
            chunk = bytes(buf[: end + 1])
            del buf[: end + 1]
            start = chunk.find(_CIV_PREAMBLE)
            if start < 0:
                continue  # leading noise before a preamble — drop it
            frames.append(chunk[start:])
        return frames


__all__ = ["BridgeFrame", "HamlibBridge", "RawCivPipe", "DEFAULT_RIGCTLD_CONF"]
