"""Reusable fake external Hamlib ``rigctld`` TCP simulator for tests."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rigplane.rigctld.contract import HamlibError

_GET_FREQ = {"f", r"\get_freq"}
_SET_FREQ = {"F", r"\set_freq"}
_GET_MODE = {"m", r"\get_mode"}
_SET_MODE = {"M", r"\set_mode"}
_GET_PTT = {"t", r"\get_ptt"}
_SET_PTT = {"T", r"\set_ptt"}
_GET_VFO = {"v", r"\get_vfo"}
_SET_VFO = {"V", r"\set_vfo"}
_QUIT = {"q", r"\quit"}


@dataclass(slots=True)
class FakeRigctldState:
    """Mutable radio state exposed through fake ``rigctld`` commands."""

    frequency_hz: int = 14_074_000
    mode: str = "USB"
    passband_hz: int = 2400
    ptt: int = 0
    vfo: str = "VFOA"


@dataclass(slots=True)
class FakeRigctldBehavior:
    """Scriptable edge-case behavior for the fake simulator.

    Keys in the command collections may be either the first command token
    (``"f"``) or the complete stripped command line (``"F 7050000"``).
    """

    response_delay: float = 0.0
    command_delays: dict[str, float] = field(default_factory=dict)
    disconnect_commands: set[str] = field(default_factory=set)
    malformed_responses: dict[str, bytes] = field(default_factory=dict)
    unsupported_commands: set[str] = field(default_factory=set)


class FakeRigctldServer:
    """Small asyncio TCP server that speaks the Hamlib ``rigctld`` wire shape."""

    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 0,
        state: FakeRigctldState | None = None,
        behavior: FakeRigctldBehavior | None = None,
    ) -> None:
        self.host = host
        self._port_hint = port
        self.state = state or FakeRigctldState()
        self.behavior = behavior or FakeRigctldBehavior()
        self.commands_seen: list[str] = []
        self._server: asyncio.AbstractServer | None = None
        self._stopping = False
        self._tasks: set[asyncio.Task[None]] = set()
        self._writers: set[asyncio.StreamWriter] = set()

    @property
    def port(self) -> int:
        return self.address[1]

    @property
    def address(self) -> tuple[str, int]:
        if self._server is None or self._server.sockets is None:
            raise RuntimeError("fake rigctld server is not started")
        host, port = self._server.sockets[0].getsockname()[:2]
        return str(host), int(port)

    async def __aenter__(self) -> FakeRigctldServer:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: object,
        exc: object,
        tb: object,
    ) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._stopping = False
        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self._port_hint,
        )

    async def stop(self) -> None:
        self._stopping = True
        server = self._server
        if self._server is not None:
            self._server.close()
            self._server = None

        writers = list(self._writers)
        for writer in writers:
            _abort_writer(writer)

        current_task = asyncio.current_task()
        tasks = [task for task in self._tasks if task is not current_task]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        for writer in writers:
            try:
                await asyncio.wait_for(writer.wait_closed(), timeout=0.1)
            except (OSError, TimeoutError):
                pass
        self._writers.clear()

        if server is not None:
            try:
                await asyncio.wait_for(server.wait_closed(), timeout=0.1)
            except TimeoutError:
                pass

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        self._writers.add(writer)
        try:
            while True:
                raw_line = await reader.readline()
                if raw_line == b"":
                    break

                line = raw_line.decode("ascii", errors="replace").strip()
                if not line:
                    continue

                self.commands_seen.append(line)
                key = _command_key(line)

                if _matches(self.behavior.disconnect_commands, line, key):
                    break

                delay = _lookup_delay(self.behavior, line, key)
                if delay > 0:
                    await asyncio.sleep(delay)

                malformed = _lookup(self.behavior.malformed_responses, line, key)
                if malformed is not None:
                    await _write_response(writer, malformed)
                    continue

                if _matches(self.behavior.unsupported_commands, line, key):
                    await _write_response(writer, _error(HamlibError.ENIMPL))
                    continue

                if key in _QUIT:
                    break

                await _write_response(writer, self._response_for(line, key))
        except (ConnectionError, OSError):
            pass
        finally:
            if task is not None:
                self._tasks.discard(task)
            self._writers.discard(writer)
            if self._stopping:
                _abort_writer(writer)
                return
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    def _response_for(self, line: str, key: str) -> bytes:
        tokens = line.split()

        if key in _GET_FREQ:
            return f"{self.state.frequency_hz}\n".encode("ascii")
        if key in _SET_FREQ:
            if len(tokens) != 2:
                return _error(HamlibError.EINVAL)
            try:
                self.state.frequency_hz = int(tokens[1])
            except ValueError:
                return _error(HamlibError.EINVAL)
            return _ok()

        if key in _GET_MODE:
            return f"{self.state.mode}\n{self.state.passband_hz}\n".encode("ascii")
        if key in _SET_MODE:
            if len(tokens) not in {2, 3}:
                return _error(HamlibError.EINVAL)
            self.state.mode = tokens[1].upper()
            self.state.passband_hz = int(tokens[2]) if len(tokens) == 3 else 0
            return _ok()

        if key in _GET_PTT:
            return f"{self.state.ptt}\n".encode("ascii")
        if key in _SET_PTT:
            if len(tokens) != 2 or tokens[1] not in {"0", "1"}:
                return _error(HamlibError.EINVAL)
            self.state.ptt = int(tokens[1])
            return _ok()

        if key in _GET_VFO:
            return f"{self.state.vfo}\n".encode("ascii")
        if key in _SET_VFO:
            if len(tokens) != 2:
                return _error(HamlibError.EINVAL)
            self.state.vfo = tokens[1].upper()
            return _ok()

        return _error(HamlibError.ENIMPL)


def _command_key(line: str) -> str:
    return line.split(maxsplit=1)[0]


def _matches(candidates: set[str], line: str, key: str) -> bool:
    return line in candidates or key in candidates


def _lookup(mapping: dict[str, bytes], line: str, key: str) -> bytes | None:
    if line in mapping:
        return mapping[line]
    return mapping.get(key)


def _lookup_delay(behavior: FakeRigctldBehavior, line: str, key: str) -> float:
    if line in behavior.command_delays:
        return behavior.command_delays[line]
    return behavior.command_delays.get(key, behavior.response_delay)


async def _write_response(writer: asyncio.StreamWriter, response: bytes) -> None:
    if writer.is_closing():
        return
    writer.write(response)
    await writer.drain()


def _abort_writer(writer: asyncio.StreamWriter) -> None:
    writer.close()
    writer.transport.abort()


def _ok() -> bytes:
    return _error(HamlibError.OK)


def _error(code: int | HamlibError) -> bytes:
    return f"RPRT {int(code)}\n".encode("ascii")
