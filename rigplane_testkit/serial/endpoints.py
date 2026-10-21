"""Real byte-stream endpoints for ``CivSerialFake``: TCP and PTY (MOR-3594).

:class:`TcpEndpoint` serves pyserial's ``socket://`` URL with one fake connection per accepted
client; :class:`PtyEndpoint` exposes a pseudo-terminal slave path (macOS/Linux). Only the fake's
public surface is used (``open_serial_connection()``, ``usb_vanish``/``usb_reappear``,
``silence``): a vanished fake raises ``OSError(ENODEV)`` from opens and fails live reads/writes.
The TCP endpoint maps that to the wire: live clients drop and new opens close until
``usb_reappear``; the PTY drops only the fake connection, keeping the slave node for the process's
lifetime (a node disappear would renumber the path, like a USB replug).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from rigplane_testkit.serial.civ_serial import CivSerialFake

_CHUNK = 4096  # read size for both pump directions


def pty_supported() -> bool:
    """Whether this platform can host :class:`PtyEndpoint` (macOS/Linux)."""
    return sys.platform == "darwin" or sys.platform.startswith("linux")


class TcpEndpoint:
    """Serve a :class:`CivSerialFake` on a real TCP listener."""

    def __init__(self, fake: CivSerialFake, host: str = "127.0.0.1", port: int = 0):
        self._fake = fake
        self._host = host
        self._port = port
        self._server: asyncio.Server | None = None
        self._clients: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
        self._stopping = False

    async def start(self) -> None:
        """Bind the listener (idempotent)."""
        if self._server is not None:
            return
        self._stopping = False
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )

    @property
    def port(self) -> int:
        """The bound TCP port (0 while not listening)."""
        if self._server is None or not self._server.sockets:
            return 0
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def device_url(self) -> str:
        """The pyserial ``socket://`` URL (``start()`` must have run)."""
        if self._server is None:
            raise RuntimeError("TcpEndpoint.start() must have run")
        # Bracket an IPv6 literal, or the colons read as port delimiters.
        host = f"[{self._host}]" if ":" in self._host else self._host
        return f"socket://{host}:{self.port}"

    async def stop(self) -> None:
        """Close every client and the listener (idempotent)."""
        self._stopping = True  # first: handlers admitted later close themselves
        server, self._server = self._server, None
        if server is None:
            return
        clients, self._clients = self._clients, {}
        for writer in clients.values():
            writer.close()
        for task in clients:
            task.cancel()
        server.close()
        await server.wait_closed()
        await asyncio.gather(*clients, return_exceptions=True)

    async def _handle_client(
        self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        self._clients[task] = client_writer  # register before any yield
        fake_writer: Any = None
        try:
            if self._stopping:
                return  # admitted mid-shutdown: close with no answer
            try:
                fake_reader, fake_writer = await self._fake.open_serial_connection()
            except OSError:
                return  # vanished: the fresh socket closes with no answer
            loop = asyncio.get_running_loop()
            to_fake = loop.create_task(self._pump(client_reader, fake_writer))
            to_client = loop.create_task(self._pump(fake_reader, client_writer))
            try:
                # Either side ending (client EOF, vanish error) ends both.
                await asyncio.wait(
                    {to_fake, to_client}, return_when=asyncio.FIRST_COMPLETED
                )
            finally:
                for pump in (to_fake, to_client):
                    pump.cancel()
                await asyncio.gather(to_fake, to_client, return_exceptions=True)
        finally:
            self._clients.pop(task, None)
            client_writer.close()
            if fake_writer is not None:
                fake_writer.close()

    async def _pump(self, reader: Any, writer: Any) -> None:
        """Pump bytes one way between a client socket and the fake wire."""
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                writer.write(data)
                await writer.drain()
        except (OSError, ConnectionError):
            pass  # usb_vanish (ENODEV) or a gone peer
        finally:
            writer.close()


class PtyEndpoint:
    """Serve a :class:`CivSerialFake` on a pseudo-terminal slave path.

    One fake connection serves the endpoint's lifetime: ``usb_vanish``
    closes it, and the first bytes after ``usb_reappear`` open a fresh
    one. macOS/Linux only; :meth:`start` raises
    :class:`NotImplementedError` elsewhere.
    """

    def __init__(self, fake: CivSerialFake) -> None:
        self._fake = fake
        self._master: int | None = None
        self._slave: int | None = None
        self._path: str | None = None
        self._reader: asyncio.StreamReader | None = None
        self._writer: Any = None
        self._pump: asyncio.Task[None] | None = None
        self._reopen: asyncio.Task[None] | None = None
        self._inbox = bytearray()  # bytes seen while no connection is live

    @property
    def device(self) -> str:
        """The slave path (``start()`` must have run)."""
        if self._path is None:
            raise RuntimeError("PtyEndpoint.start() must have run")
        return self._path

    async def start(self) -> None:
        """Open the pty pair and the lifetime fake connection (idempotent)."""
        if self._master is not None:
            return
        if not pty_supported():
            raise NotImplementedError(
                "PTY serial simulation is available on macOS and Linux only; "
                "use TcpEndpoint (socket://) instead"
            )
        import tty

        # Transactional: open the fake first, publish only after all steps
        # succeed, so a failure leaves nothing behind.
        reader, writer = await self._fake.open_serial_connection()
        loop = asyncio.get_running_loop()
        master: int | None = None
        slave: int | None = None
        reader_added = False
        complete = False
        try:
            master, slave = os.openpty()
            tty.setraw(slave)
            os.set_blocking(master, False)
            path = os.ttyname(slave)
            loop.add_reader(master, self._on_master_readable)
            reader_added = True
            complete = True
        finally:
            if not complete:  # roll back only what exists, never masking
                with contextlib.suppress(Exception):
                    if reader_added:
                        loop.remove_reader(master)
                    for fd in (master, slave):
                        if fd is not None:
                            os.close(fd)
                with contextlib.suppress(Exception):
                    writer.close()
        self._master, self._slave = master, slave
        self._path = path
        self._reader, self._writer = reader, writer
        self._pump = loop.create_task(self._pump_fake_to_master())

    async def stop(self) -> None:
        """Close the pty pair and the fake connection (idempotent)."""
        master, self._master = self._master, None
        slave, self._slave = self._slave, None
        self._path = None
        if master is None:
            return
        loop = asyncio.get_running_loop()
        loop.remove_reader(master)
        pending = [task for task in (self._pump, self._reopen) if task is not None]
        for task in pending:
            task.cancel()
        self._pump = None
        self._reopen = None
        self._drop_fake_connection()
        self._inbox.clear()
        for fd in (master, slave):
            if fd is not None:
                with contextlib.suppress(OSError):
                    os.close(fd)
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, OSError):
                await task

    def _on_master_readable(self) -> None:
        """App bytes arrived on the master (loop reader callback)."""
        master = self._master
        if master is None:
            return
        try:
            data = os.read(master, _CHUNK)
        except OSError:
            return  # spurious wakeup or a pty hiccup; keep the endpoint up
        if not data:
            return
        writer = self._writer
        if writer is None:
            # No live connection: reopen for these bytes; while still
            # gone they die with the device.
            self._inbox += data
            if self._reopen is None:
                self._reopen = asyncio.get_running_loop().create_task(
                    self._reopen_and_feed()
                )
            return
        try:
            writer.write(data)
        except OSError:
            self._drop_fake_connection()  # vanish landed between reads

    async def _reopen_and_feed(self) -> None:
        try:
            reader, writer = await self._fake.open_serial_connection()
        except OSError:
            self._inbox.clear()  # still vanished: nothing answers
            return
        finally:
            self._reopen = None
        self._reader, self._writer = reader, writer
        self._pump = asyncio.get_running_loop().create_task(self._pump_fake_to_master())
        data = bytes(self._inbox)
        self._inbox.clear()
        try:
            writer.write(data)
        except OSError:
            self._drop_fake_connection()

    def _drop_fake_connection(self) -> None:
        self._reader = None
        writer, self._writer = self._writer, None
        if writer is not None:
            with contextlib.suppress(Exception):
                writer.close()

    async def _pump_fake_to_master(self) -> None:
        """Fake replies to the master fd (non-blocking, tiny CI-V frames)."""
        reader = self._reader
        assert reader is not None
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                master = self._master
                if master is not None:
                    try:
                        os.write(master, data)
                    except OSError:
                        pass  # an undrained pty drops bytes, like a full device
        except OSError:
            pass  # usb_vanish failed the reads; the reopen path takes over
        finally:
            if self._pump is asyncio.current_task():
                self._pump = None
                self._drop_fake_connection()


__all__ = ["PtyEndpoint", "TcpEndpoint", "pty_supported"]
