"""Tests for src/rigplane/rigctld/server.py.

Strategy
--------
- Inject mock protocol and handler via the private _protocol / _handler kwargs
  on RigctldServer so these tests never need a real radio or real protocol impl.
- Use asyncio.open_connection as the test client.
- Port 0 → OS assigns a free ephemeral port; read it from server._server.sockets.
- asyncio_mode = "auto" (pyproject.toml) — no @pytest.mark.asyncio needed.
"""

from __future__ import annotations

import asyncio
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from rigplane.backends.icom7610.drivers.serial_stub import SerialMockRadio
from rigplane.rigctld.contract import (
    ClientSession,
    HamlibError,
    RigctldCommand,
    RigctldConfig,
    RigctldResponse,
)
from rigplane.rigctld.server import RigctldServer, run_rigctld_server
from rigplane.types import Mode

# ---------------------------------------------------------------------------
# Canned objects shared across tests
# ---------------------------------------------------------------------------

_FREQ_CMD = RigctldCommand(short_cmd="f", long_cmd="get_freq", is_set=False)
_FREQ_RESP = RigctldResponse(values=["14074000"], error=0)
_RESPONSE_BYTES = b"14074000\n"
_ERROR_BYTES = b"RPRT -8\n"  # EPROTO
_TIMEOUT_BYTES = b"RPRT -5\n"  # ETIMEOUT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _addr(server: RigctldServer) -> tuple[str, int]:
    """Return (host, port) for a started server."""
    assert server._server is not None
    return server._server.sockets[0].getsockname()


async def _connect(
    server: RigctldServer,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = _addr(server)
    return await asyncio.open_connection(host, port)


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _read_all(reader: asyncio.StreamReader, *, timeout: float = 1.0) -> bytes:
    """Read until EOF or timeout."""
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return b""


class _ContractPrewarmRadio:
    def __init__(self, mode: str, data_mode: bool = False) -> None:
        self.mode = mode
        self.data_mode = data_mode
        self.set_data_mode = AsyncMock(side_effect=self._set_data_mode)

    async def get_mode(self, receiver: int = 0) -> tuple[str, int | None]:
        assert receiver == 0
        return self.mode, 2

    async def get_data_mode(self) -> bool:
        return self.data_mode

    async def _set_data_mode(self, on: bool) -> None:
        self.data_mode = on


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_radio() -> MagicMock:
    radio = MagicMock(name="radio")
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    return radio


@pytest.fixture
def cfg() -> RigctldConfig:
    return RigctldConfig(
        host="127.0.0.1",
        port=0,  # OS assigns a free port
        max_clients=3,
        client_timeout=0.5,
        command_timeout=0.3,
    )


@pytest.fixture
def proto() -> MagicMock:
    """Mock protocol module with canned responses."""
    m = MagicMock(name="protocol")
    m.parse_line.return_value = _FREQ_CMD
    m.format_response.return_value = _RESPONSE_BYTES
    m.format_error.return_value = _ERROR_BYTES
    return m


@pytest.fixture
def handler() -> MagicMock:
    """Mock handler *instance* (not the class) with async execute."""
    m = MagicMock(name="handler")
    m.execute = AsyncMock(return_value=_FREQ_RESP)
    return m


@pytest.fixture
async def server(
    mock_radio: MagicMock, cfg: RigctldConfig, proto: MagicMock, handler: MagicMock
) -> RigctldServer:  # type: ignore[misc]
    srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
    await srv.start()
    yield srv  # type: ignore[misc]
    await srv.stop()


@pytest.fixture
async def server_serial_radio(
    cfg: RigctldConfig,
) -> tuple[RigctldServer, SerialMockRadio]:
    """RigctldServer running on top of a real SerialMockRadio core."""
    radio = SerialMockRadio()
    await radio.connect()
    srv = RigctldServer(radio, cfg)
    await srv.start()
    try:
        yield srv, radio
    finally:
        await srv.stop()


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


class TestLifecycle:
    async def test_start_creates_server(self, server: RigctldServer) -> None:
        assert server._server is not None

    async def test_stop_closes_server(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()
        await srv.stop()
        assert srv._server is None

    async def test_context_manager(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        async with RigctldServer(
            mock_radio, cfg, _protocol=proto, _handler=handler
        ) as srv:
            host, port = _addr(srv)
            assert port > 0
        assert srv._server is None

    async def test_double_stop_is_safe(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()
        await srv.stop()
        await srv.stop()  # second call must not raise

    async def test_start_does_not_bind_backend_state_cache_by_default(
        self, cfg: RigctldConfig
    ) -> None:
        radio = SerialMockRadio()
        await radio.connect()
        srv = RigctldServer(radio, cfg)
        await srv.start()
        try:
            assert srv._rig_handler is not None
            assert srv._poller is None
            assert srv._rig_handler._cache is not radio.state_cache
        finally:
            await srv.stop()


# ---------------------------------------------------------------------------
# Accept / response cycle
# ---------------------------------------------------------------------------


class TestAcceptResponse:
    async def test_single_command_response(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)

        await _close(w)

    async def test_multiple_commands_same_connection(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)

        for _ in range(3):
            w.write(b"f\n")
            await w.drain()
            data = await asyncio.wait_for(r.read(4096), timeout=1.0)
            assert data == _RESPONSE_BYTES

        assert proto.parse_line.call_count == 3
        await _close(w)

    async def test_set_command_calls_execute(
        self, server: RigctldServer, proto: MagicMock, handler: MagicMock
    ) -> None:
        set_cmd = RigctldCommand("F", "set_freq", args=("14074000",), is_set=True)
        proto.parse_line.return_value = set_cmd
        proto.format_response.return_value = b"RPRT 0\n"

        r, w = await _connect(server)
        w.write(b"F 14074000\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT 0\n"
        handler.execute.assert_called_once_with(set_cmd)
        await _close(w)

    async def test_format_response_receives_session(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()
        await asyncio.wait_for(r.read(4096), timeout=1.0)

        # format_response must be called with (cmd, resp, ClientSession)
        call_args = proto.format_response.call_args
        assert call_args is not None
        _cmd, _resp, session = call_args.args
        assert isinstance(session, ClientSession)
        assert session.client_id > 0

        await _close(w)

    async def test_blank_lines_are_skipped(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"\n\n\nf\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)
        await _close(w)

    async def test_crlf_line_ending_accepted(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        r, w = await _connect(server)
        w.write(b"f\r\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        proto.parse_line.assert_called_once_with(b"f", ANY)
        await _close(w)


class TestSemiIntegrationSerialMockRadio:
    async def test_get_and_set_frequency_flows_through_core(
        self, server_serial_radio: tuple[RigctldServer, SerialMockRadio]
    ) -> None:
        """f/F commands go through real RigctldHandler into SerialMockRadio."""
        server, radio = server_serial_radio
        reader, writer = await _connect(server)
        try:
            # Initial frequency from SerialMockRadio default state.
            writer.write(b"f\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert b"14074000" in data

            # Change frequency and verify both protocol response and core state.
            writer.write(b"F 7050000\n")
            await writer.drain()
            data_set = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_set == b"RPRT 0\n"

            writer.write(b"f\n")
            await writer.drain()
            data_after = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_after == b"7050000\n"

            freq_core = await radio.get_freq()
            assert freq_core == 7_050_000
        finally:
            await _close(writer)

    async def test_get_and_set_mode_flows_through_core(
        self, server_serial_radio: tuple[RigctldServer, SerialMockRadio]
    ) -> None:
        """m/M commands go through real RigctldHandler into SerialMockRadio."""
        server, radio = server_serial_radio
        reader, writer = await _connect(server)
        try:
            # Initial mode from SerialMockRadio default state.
            writer.write(b"m\n")
            await writer.drain()
            data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            lines = data.decode().splitlines()
            assert lines[0] == "USB"

            # Change mode to LSB with passband and verify both layers.
            writer.write(b"M LSB 2400\n")
            await writer.drain()
            data_set = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data_set == b"RPRT 0\n"

            writer.write(b"m\n")
            await writer.drain()
            data_after = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            after_lines = data_after.decode().splitlines()
            assert after_lines[0] == "LSB"

            mode_core, filt_core = await radio.get_mode()
            assert mode_core == "LSB"
            assert filt_core == 2
        finally:
            await _close(writer)


# ---------------------------------------------------------------------------
# Quit command
# ---------------------------------------------------------------------------


class TestQuit:
    async def test_quit_closes_connection(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        w.write(b"q\n")
        await w.drain()

        data = await _read_all(r)
        assert data == b""  # server closed the connection
        await _close(w)

    async def test_quit_decrements_client_count(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        w.write(b"q\n")
        await w.drain()
        await _read_all(r)

        # Give event loop a beat to run the done callback.
        await asyncio.sleep(0.05)
        assert server._client_count == 0
        await _close(w)


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_parse_error_sends_enimpl(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """Unknown commands (ValueError) return ENIMPL, not EPROTO."""
        proto.parse_line.side_effect = ValueError("unknown command")
        proto.format_error.return_value = b"RPRT -4\n"

        r, w = await _connect(server)
        w.write(b"garbage\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT -4\n"
        proto.format_error.assert_called_with(HamlibError.ENIMPL)
        await _close(w)

    async def test_parse_error_connection_stays_open(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """After a parse error the connection should remain open."""
        # First command: parse error
        proto.parse_line.side_effect = [ValueError("bad"), _FREQ_CMD]
        proto.format_error.return_value = b"RPRT -8\n"

        r, w = await _connect(server)
        w.write(b"garbage\n")
        await w.drain()
        await asyncio.wait_for(r.read(4096), timeout=1.0)  # consume error

        # Second command: succeeds
        proto.parse_line.side_effect = None
        proto.parse_line.return_value = _FREQ_CMD
        w.write(b"f\n")
        await w.drain()
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES

        await _close(w)

    async def test_handler_exception_sends_eio(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        handler.execute.side_effect = RuntimeError("radio exploded")
        proto.format_error.return_value = b"RPRT -6\n"

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == b"RPRT -6\n"
        proto.format_error.assert_called_with(HamlibError.EIO)
        await _close(w)

    async def test_line_too_long_closes_connection(self, server: RigctldServer) -> None:
        r, w = await _connect(server)
        # max_line_length default is 1024; send > 1024 bytes without \n first
        oversized = b"x" * 1025 + b"\n"
        w.write(oversized)
        await w.drain()

        data = await _read_all(r)
        assert data == b""  # connection closed

        await _close(w)


# ---------------------------------------------------------------------------
# Command timeout
# ---------------------------------------------------------------------------


class TestCommandTimeout:
    async def test_slow_handler_gets_etimeout(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        async def slow(cmd: RigctldCommand) -> RigctldResponse:
            await asyncio.sleep(10)  # > command_timeout=0.3
            return _FREQ_RESP  # pragma: no cover

        handler.execute = slow
        proto.format_error.return_value = _TIMEOUT_BYTES

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        # Should receive timeout error within 1s (command_timeout=0.3)
        data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert data == _TIMEOUT_BYTES
        proto.format_error.assert_called_with(HamlibError.ETIMEOUT)
        await _close(w)

    async def test_connection_still_usable_after_timeout(
        self, server: RigctldServer, handler: MagicMock, proto: MagicMock
    ) -> None:
        """After a command timeout the client can send another command."""
        call_count = 0

        async def sometimes_slow(cmd: RigctldCommand) -> RigctldResponse:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                await asyncio.sleep(10)  # first: timeout
            return _FREQ_RESP

        handler.execute = sometimes_slow
        proto.format_error.return_value = _TIMEOUT_BYTES

        r, w = await _connect(server)

        # First command times out
        w.write(b"f\n")
        await w.drain()
        err_data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert err_data == _TIMEOUT_BYTES

        # Second command succeeds
        w.write(b"f\n")
        await w.drain()
        ok_data = await asyncio.wait_for(r.read(4096), timeout=1.0)
        assert ok_data == _RESPONSE_BYTES

        await _close(w)


# ---------------------------------------------------------------------------
# Idle timeout
# ---------------------------------------------------------------------------


class TestIdleTimeout:
    async def test_idle_client_gets_disconnected(self, server: RigctldServer) -> None:
        """client_timeout=0.5; sending nothing should close the connection."""
        r, w = await _connect(server)

        # Read should return EOF after idle timeout fires.
        data = await asyncio.wait_for(r.read(4096), timeout=2.0)
        assert data == b""

        await _close(w)

    async def test_active_client_resets_timeout(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        """Each command resets the idle clock."""
        r, w = await _connect(server)

        # Send two commands 0.3s apart (< client_timeout=0.5) — should work.
        for _ in range(2):
            w.write(b"f\n")
            await w.drain()
            await asyncio.wait_for(r.read(4096), timeout=1.0)
            await asyncio.sleep(0.2)

        await _close(w)


# ---------------------------------------------------------------------------
# Max clients
# ---------------------------------------------------------------------------


class TestMaxClients:
    async def test_max_clients_enforced(
        self, server: RigctldServer, cfg: RigctldConfig
    ) -> None:
        """Connecting max_clients+1 should get an immediate EOF on the last."""
        connections: list[tuple[asyncio.StreamReader, asyncio.StreamWriter]] = []

        for _ in range(cfg.max_clients):
            r, w = await _connect(server)
            connections.append((r, w))

        # Give event loop a beat so all connections are registered.
        await asyncio.sleep(0.05)

        # One extra — should be rejected.
        r_extra, w_extra = await _connect(server)
        data = await _read_all(r_extra)
        assert data == b""  # immediate EOF

        for r, w in connections:
            await _close(w)
        await _close(w_extra)

    async def test_client_count_decreases_on_disconnect(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        proto.parse_line.return_value = RigctldCommand("q", "quit")

        r, w = await _connect(server)
        await asyncio.sleep(0.05)
        assert server._client_count == 1

        w.write(b"q\n")
        await w.drain()
        await _read_all(r)
        await asyncio.sleep(0.05)

        assert server._client_count == 0
        await _close(w)


# ---------------------------------------------------------------------------
# Concurrent clients
# ---------------------------------------------------------------------------


class TestConcurrentClients:
    async def test_three_concurrent_clients(self, server: RigctldServer) -> None:
        """All three clients should receive independent responses."""
        conns = [await _connect(server) for _ in range(3)]

        for _, w in conns:
            w.write(b"f\n")
            await w.drain()

        results = []
        for r, _ in conns:
            data = await asyncio.wait_for(r.read(4096), timeout=1.0)
            results.append(data)

        assert all(d == _RESPONSE_BYTES for d in results)

        for r, w in conns:
            await _close(w)

    async def test_each_client_has_unique_id(
        self, server: RigctldServer, proto: MagicMock
    ) -> None:
        conns = [await _connect(server) for _ in range(3)]

        for _, w in conns:
            w.write(b"f\n")
            await w.drain()

        for r, _ in conns:
            await asyncio.wait_for(r.read(4096), timeout=1.0)

        # Collect the sessions passed to format_response
        sessions = [call.args[2] for call in proto.format_response.call_args_list]
        ids = {s.client_id for s in sessions}
        assert len(ids) == 3, "each client should have a unique client_id"

        for r, w in conns:
            await _close(w)


# ---------------------------------------------------------------------------
# Graceful shutdown
# ---------------------------------------------------------------------------


class TestGracefulShutdown:
    async def test_stop_closes_active_clients(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()

        r, w = await _connect(srv)
        await asyncio.sleep(0.05)  # ensure task is running

        await srv.stop()

        # Client should receive EOF after server stops.
        data = await _read_all(r)
        assert data == b""
        await _close(w)

    async def test_stop_cancels_all_tasks(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        await srv.start()

        readers_writers = [await _connect(srv) for _ in range(2)]
        await asyncio.sleep(0.05)

        assert srv._client_count == 2
        await srv.stop()
        # Allow done callbacks to fire after task cancellation
        await asyncio.sleep(0.05)
        assert srv._client_count == 0

        for r, w in readers_writers:
            await _close(w)

    async def test_serve_forever_stops_on_cancel(
        self,
        mock_radio: MagicMock,
        cfg: RigctldConfig,
        proto: MagicMock,
        handler: MagicMock,
    ) -> None:
        srv = RigctldServer(mock_radio, cfg, _protocol=proto, _handler=handler)
        task = asyncio.get_event_loop().create_task(srv.serve_forever())
        await asyncio.sleep(0.05)

        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=2.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass

        assert srv._server is None


# ---------------------------------------------------------------------------
# Abrupt disconnect
# ---------------------------------------------------------------------------


class TestAbruptDisconnect:
    async def test_abrupt_disconnect_does_not_crash_server(
        self, server: RigctldServer
    ) -> None:
        r, w = await _connect(server)

        # Close without sending anything (abrupt).
        w.close()
        await asyncio.sleep(0.1)

        # Server must still be running and accept new clients.
        assert server._server is not None
        r2, w2 = await _connect(server)
        w2.write(b"f\n")
        await w2.drain()
        data = await asyncio.wait_for(r2.read(4096), timeout=1.0)
        assert data == _RESPONSE_BYTES
        await _close(w2)

    async def test_disconnect_mid_session_handled(
        self, server: RigctldServer, handler: MagicMock
    ) -> None:
        """Handler may be awaiting execute when client disconnects."""
        evt = asyncio.Event()

        async def blocking(cmd: RigctldCommand) -> RigctldResponse:
            evt.set()
            await asyncio.sleep(10)  # blocking
            return _FREQ_RESP  # pragma: no cover

        handler.execute = blocking

        r, w = await _connect(server)
        w.write(b"f\n")
        await w.drain()

        # Wait until handler is entered, then yank the connection.
        await asyncio.wait_for(evt.wait(), timeout=1.0)
        w.close()
        await asyncio.sleep(0.2)

        # Server should still be alive.
        assert server._server is not None


# ---------------------------------------------------------------------------
# run_rigctld_server convenience helper
# ---------------------------------------------------------------------------


class TestRunRigctldServer:
    async def test_run_stops_on_cancel(self, mock_radio: MagicMock) -> None:
        """run_rigctld_server should exit cleanly when cancelled."""
        # Use a no-op handler/protocol so start() doesn't fail on stubs.
        proto = MagicMock()
        proto.parse_line.return_value = _FREQ_CMD
        proto.format_response.return_value = _RESPONSE_BYTES
        proto.format_error.return_value = _ERROR_BYTES

        hdl = MagicMock()
        hdl.execute = AsyncMock(return_value=_FREQ_RESP)

        # Patch the module-level imports that run_rigctld_server triggers.
        import rigplane.rigctld.server as server_mod

        orig_cls = server_mod.RigctldServer

        def _patched_cls(radio: MagicMock, config: RigctldConfig) -> RigctldServer:
            return orig_cls(radio, config, _protocol=proto, _handler=hdl)

        server_mod.RigctldServer = _patched_cls  # type: ignore[assignment]
        try:
            task = asyncio.get_event_loop().create_task(
                run_rigctld_server(mock_radio, host="127.0.0.1", port=0)
            )
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=2.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        finally:
            server_mod.RigctldServer = orig_cls  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# WSJT-X compatibility prewarm
# ---------------------------------------------------------------------------


class TestWsjtxCompatPrewarm:
    async def test_prewarm_falls_back_to_core_radio_contract(self) -> None:
        radio = _ContractPrewarmRadio("USB", data_mode=False)
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(radio, cfg, _protocol=MagicMock(), _handler=MagicMock())

        await srv._wsjtx_compat_prewarm()

        radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_enables_data_when_usb_and_data_off(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=False)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_skips_when_data_already_on(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=True)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_not_called()

    async def test_prewarm_configured_data2_falls_back_on_single_data_profile(
        self, mock_radio: MagicMock
    ) -> None:
        cfg = RigctldConfig(
            wsjtx_compat=True,
            wsjtx_data_mode=2,
            wsjtx_data_mod_input=5,
        )
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.profile = MagicMock(data_mode_count=1)
        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))
        mock_radio.get_data_mode = AsyncMock(return_value=True)
        mock_radio.set_data_mode = AsyncMock(return_value=None)
        mock_radio.set_data2_mod_input = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data2_mod_input.assert_not_called()
        mock_radio.set_data_mode.assert_awaited_once_with(True)

    async def test_prewarm_skips_for_non_ssb_modes(self, mock_radio: MagicMock) -> None:
        cfg = RigctldConfig(wsjtx_compat=True)
        srv = RigctldServer(
            mock_radio, cfg, _protocol=MagicMock(), _handler=MagicMock()
        )

        mock_radio.get_mode_info = AsyncMock(return_value=(Mode.CW, None))
        mock_radio.get_data_mode = AsyncMock(return_value=False)
        mock_radio.set_data_mode = AsyncMock(return_value=None)

        await srv._wsjtx_compat_prewarm()

        mock_radio.set_data_mode.assert_not_called()
