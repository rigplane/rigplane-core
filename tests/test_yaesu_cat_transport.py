"""Tests for Yaesu CAT serial transport."""

from __future__ import annotations

import asyncio
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.yaesu_cat import (
    CatTimeoutError,
    CatTransportError,
    YaesuCatTransport,
)
from rigplane.backends.yaesu_cat.transport import CatCommandRejected


class FakeStreamReader:
    """Fake asyncio StreamReader for testing."""

    def __init__(self, responses: list[bytes]) -> None:
        self._responses = list(responses)
        self._index = 0

    async def readuntil(self, separator: bytes) -> bytes:
        """Return next canned response."""
        if self._index >= len(self._responses):
            raise asyncio.TimeoutError("No more responses")
        data = self._responses[self._index]
        self._index += 1
        return data


class FakeStreamWriter:
    """Fake asyncio StreamWriter for testing."""

    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        """Record written data."""
        self.written.append(data)

    async def drain(self) -> None:
        """No-op drain."""
        pass

    def close(self) -> None:
        """Mark as closed."""
        self.closed = True

    async def wait_closed(self) -> None:
        """No-op wait."""
        pass


@pytest.fixture
def mock_serial_connection() -> Any:
    """Mock serial_asyncio module."""
    mock_module = MagicMock()
    sys.modules["serial_asyncio"] = mock_module
    yield mock_module
    sys.modules.pop("serial_asyncio", None)


class TestYaesuCatTransport:
    """Tests for YaesuCatTransport basic operations."""

    async def test_connect_opens_serial_port(self, mock_serial_connection: Any) -> None:
        """connect() opens serial port with correct parameters."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test", baudrate=38400)
        await transport.connect()

        assert transport.connected
        mock_serial_connection.open_serial_connection.assert_called_once_with(
            url="/dev/test",
            baudrate=38400,
            bytesize=8,
            parity="N",
            stopbits=1,
        )

    async def test_connect_twice_is_noop(self, mock_serial_connection: Any) -> None:
        """Second connect() call is a no-op."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        await transport.connect()  # Should not call open_serial_connection again

        assert mock_serial_connection.open_serial_connection.call_count == 1

    async def test_close_closes_writer(self, mock_serial_connection: Any) -> None:
        """close() closes the writer."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        await transport.close()

        assert not transport.connected
        assert writer.closed

    async def test_write_sends_command_with_terminator(
        self, mock_serial_connection: Any
    ) -> None:
        """write() sends command with semicolon terminator."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        await transport.write("FA")

        assert writer.written == [b"FA;"]

    async def test_write_preserves_existing_terminator(
        self, mock_serial_connection: Any
    ) -> None:
        """write() doesn't double-terminate if command already has `;`."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        await transport.write("FA;")

        assert writer.written == [b"FA;"]

    async def test_write_without_connect_raises(self) -> None:
        """write() raises if not connected."""
        transport = YaesuCatTransport(device="/dev/test")

        with pytest.raises(CatTransportError, match="not connected"):
            await transport.write("FA;")

    async def test_write_rejected_raises_and_names_frame(
        self, mock_serial_connection: Any
    ) -> None:
        """write() raises CatCommandRejected when the radio answers '?;'
        instead of silently discarding it as a drained stale line
        (MOR-2103)."""
        reader = FakeStreamReader([b"?;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()

        with pytest.raises(CatCommandRejected, match=r"CT002;"):
            await transport.write("CT002;")

        assert writer.written == [b"CT002;"]

    async def test_readline_returns_response_without_terminator(
        self, mock_serial_connection: Any
    ) -> None:
        """readline() returns response with terminator stripped."""
        reader = FakeStreamReader([b"FA014074000;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        response = await transport.readline()

        assert response == "FA014074000"

    async def test_readline_timeout_raises(self, mock_serial_connection: Any) -> None:
        """readline() raises CatTimeoutError on timeout."""
        reader = FakeStreamReader([])  # No responses = timeout
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test", timeout=0.1)
        await transport.connect()

        with pytest.raises(CatTimeoutError, match="Read timeout"):
            await transport.readline()

    async def test_readline_without_connect_raises(self) -> None:
        """readline() raises if not connected."""
        transport = YaesuCatTransport(device="/dev/test")

        with pytest.raises(CatTransportError, match="not connected"):
            await transport.readline()

    async def test_query_sends_and_reads(self, mock_serial_connection: Any) -> None:
        """query() sends command and returns response."""
        reader = FakeStreamReader([b"FA014074000;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()
        response = await transport.query("FA;")

        assert writer.written == [b"FA;"]
        assert response == "FA014074000"

    async def test_query_with_echo_suppression(
        self, mock_serial_connection: Any
    ) -> None:
        """query() skips echo line when echo_suppression=True."""
        # Radio echoes request, then sends real response
        reader = FakeStreamReader([b"FA;", b"FA014074000;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test", echo_suppression=True)
        await transport.connect()
        response = await transport.query("FA;")

        assert writer.written == [b"FA;"]
        assert response == "FA014074000"  # Second line, not echo

    async def test_query_without_echo_suppression(
        self, mock_serial_connection: Any
    ) -> None:
        """query() returns first line when echo_suppression=False."""
        reader = FakeStreamReader([b"FA;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test", echo_suppression=False)
        await transport.connect()
        response = await transport.query("FA;")

        assert response == "FA"  # First line (echo itself)

    async def test_context_manager_opens_and_closes(
        self, mock_serial_connection: Any
    ) -> None:
        """Context manager opens on enter, closes on exit."""
        reader = FakeStreamReader([])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        async with YaesuCatTransport(device="/dev/test") as transport:
            assert transport.connected

        assert not transport.connected
        assert writer.closed


class TestYaesuCatTransportEdgeCases:
    """Edge cases and error handling."""

    async def test_connect_without_serial_asyncio_raises(self) -> None:
        """connect() raises helpful error if pyserial-asyncio not installed."""
        import builtins

        real_import = builtins.__import__

        def _mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "serial_asyncio":
                raise ImportError("mocked: no serial_asyncio")
            return real_import(name, *args, **kwargs)

        old_module = sys.modules.pop("serial_asyncio", None)
        try:
            builtins.__import__ = _mock_import  # type: ignore[assignment]
            transport = YaesuCatTransport(device="/dev/test")
            with pytest.raises(CatTransportError, match="pyserial"):
                await transport.connect()
        finally:
            builtins.__import__ = real_import  # type: ignore[assignment]
            if old_module:
                sys.modules["serial_asyncio"] = old_module

    async def test_multiple_queries_in_sequence(
        self, mock_serial_connection: Any
    ) -> None:
        """Multiple query() calls work in sequence."""
        reader = FakeStreamReader([b"FA014074000;", b"MD02;", b"PC2005;"])
        writer = FakeStreamWriter()
        mock_serial_connection.open_serial_connection = AsyncMock(
            return_value=(reader, writer)
        )

        transport = YaesuCatTransport(device="/dev/test")
        await transport.connect()

        freq = await transport.query("FA;")
        mode = await transport.query("MD0;")
        power = await transport.query("PC;")

        assert freq == "FA014074000"
        assert mode == "MD02"
        assert power == "PC2005"
        assert writer.written == [b"FA;", b"MD0;", b"PC;"]
