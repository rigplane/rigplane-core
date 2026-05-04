"""TCP wire-level tests for RigctldServer.

Strategy
--------
- Use the REAL protocol module and handler (not mocked) so we exercise the
  full parse → execute → format pipeline over a genuine TCP socket.
- Inject AsyncMock radio and a MagicMock poller so no real CI-V traffic is
  generated.  The poller mock suppresses background polling tasks.
- Bind on port 0 (OS assigns a free ephemeral port).
- Send raw bytes, read raw bytes, assert exact wire format.

NOTE: Extended-protocol mode ('+' prefix) is not yet implemented in the
server's _handle_client loop, so wire tests for it are omitted.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from _caps import FULL_ICOM_CAPS
from icom_lan.radio_protocol import MetersCapable
from icom_lan.rigctld.contract import RigctldConfig
from icom_lan.rigctld.server import RigctldServer
from icom_lan.types import Mode

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockRadio(SimpleNamespace):
    pass


MetersCapable.register(_MockRadio)


def _addr(srv: RigctldServer) -> tuple[str, int]:
    assert srv._server is not None
    return srv._server.sockets[0].getsockname()  # type: ignore[index]


async def _connect(
    srv: RigctldServer,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    host, port = _addr(srv)
    return await asyncio.open_connection(host, port)


async def _close(writer: asyncio.StreamWriter) -> None:
    try:
        writer.close()
        await writer.wait_closed()
    except Exception:
        pass


async def _read(reader: asyncio.StreamReader, *, timeout: float = 2.0) -> bytes:
    """Read up to 4096 bytes from the reader with a timeout."""
    return await asyncio.wait_for(reader.read(4096), timeout=timeout)


async def _read_eof(reader: asyncio.StreamReader, *, timeout: float = 2.0) -> bytes:
    """Read until EOF (connection closed by server) with a timeout."""
    try:
        return await asyncio.wait_for(reader.read(4096), timeout=timeout)
    except asyncio.TimeoutError:
        return b""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_mock_radio() -> _MockRadio:
    """Explicit mock radio with sensible default return values."""
    radio = _MockRadio()
    radio.capabilities = set(FULL_ICOM_CAPS)
    # Required by assert_radio_startup_ready (startup_checks.py)
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    radio.get_freq = AsyncMock(return_value=14_074_000)
    radio.get_mode_info = AsyncMock(return_value=(Mode.USB, 2))  # USB, FIL2 = 2400 Hz
    radio.get_data_mode = AsyncMock(return_value=False)
    radio.get_s_meter = AsyncMock(return_value=120)
    radio.get_rf_power = AsyncMock(return_value=255)
    # ``get_swr`` is contracted as a calibrated ratio (>= 1.0); the
    # rigctld handler now passes the float through unchanged (#1173).
    radio.get_swr = AsyncMock(return_value=1.0)
    radio.set_freq = AsyncMock(return_value=None)
    radio.set_mode = AsyncMock(return_value=None)
    radio.set_data_mode = AsyncMock(return_value=None)
    radio.set_ptt = AsyncMock(return_value=None)
    radio.get_powerstat = AsyncMock(return_value=True)
    return radio


def _make_mock_poller() -> MagicMock:
    """MagicMock poller that suppresses background RadioPoller creation."""
    poller = MagicMock()
    poller.start = AsyncMock()
    poller.stop = AsyncMock()
    poller.write_busy = False
    return poller


@pytest.fixture
async def wire_server() -> RigctldServer:  # type: ignore[misc]
    """Real RigctldServer bound to 127.0.0.1:0 with mock radio + poller."""
    from icom_lan.rigctld.handler import RigctldHandler
    from icom_lan.rigctld.state_cache import StateCache

    radio = _make_mock_radio()
    poller = _make_mock_poller()
    cache = StateCache()  # Real cache to avoid AsyncMock.state_cache confusion
    # Populate level cache so get_level (STRENGTH/RFPOWER/SWR) returns numeric values
    cache.update_s_meter(120)
    cache.update_rf_power(1.0)
    cache.update_swr(1.0)
    cfg = RigctldConfig(
        host="127.0.0.1",
        port=0,
        client_timeout=2.0,
        command_timeout=1.0,
        cache_ttl=0.0,  # always fresh → deterministic radio calls
    )
    handler = RigctldHandler(radio, cfg)
    srv = RigctldServer(radio, cfg, _handler=handler, _poller=poller)
    async with srv:
        yield srv  # type: ignore[misc]


@pytest.fixture
async def ro_wire_server() -> RigctldServer:  # type: ignore[misc]
    """Read-only RigctldServer for testing that setters are rejected."""
    from icom_lan.rigctld.handler import RigctldHandler

    radio = _make_mock_radio()
    poller = _make_mock_poller()
    cfg = RigctldConfig(
        host="127.0.0.1",
        port=0,
        client_timeout=2.0,
        command_timeout=1.0,
        read_only=True,
        cache_ttl=0.0,
    )
    handler = RigctldHandler(radio, cfg)
    srv = RigctldServer(radio, cfg, _handler=handler, _poller=poller)
    async with srv:
        yield srv  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Basic get / set roundtrip
# ---------------------------------------------------------------------------


class TestGetSetRoundtrip:
    async def test_get_freq_wire(self, wire_server: RigctldServer) -> None:
        """'f\\n' → '14074000\\n' on the wire."""
        r, w = await _connect(wire_server)
        w.write(b"f\n")
        await w.drain()

        data = await _read(r)
        assert data == b"14074000\n"
        await _close(w)

    async def test_set_freq_wire(self, wire_server: RigctldServer) -> None:
        """'F 7050000\\n' → 'RPRT 0\\n' on the wire."""
        r, w = await _connect(wire_server)
        w.write(b"F 7050000\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT 0\n"
        await _close(w)

    async def test_get_mode_two_line_response(self, wire_server: RigctldServer) -> None:
        """'m\\n' → two-line response: mode + passband."""
        r, w = await _connect(wire_server)
        w.write(b"m\n")
        await w.drain()

        data = await _read(r)
        # Radio mock returns (Mode.USB, 2) and get_data_mode=False → USB / 2400 Hz.
        assert data == b"USB\n2400\n"
        await _close(w)

    async def test_get_vfo_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"v\n")
        await w.drain()

        data = await _read(r)
        assert data == b"VFOA\n"
        await _close(w)

    async def test_get_ptt_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"t\n")
        await w.drain()

        data = await _read(r)
        assert data == b"0\n"
        await _close(w)

    async def test_set_ptt_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"T 1\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT 0\n"
        await _close(w)

    async def test_get_split_vfo_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"s\n")
        await w.drain()

        data = await _read(r)
        assert data == b"0\nVFOA\n"
        await _close(w)

    async def test_get_rit_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"j\n")
        await w.drain()

        data = await _read(r)
        assert data == b"0\n"
        await _close(w)

    async def test_get_info_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\get_info\n")
        await w.drain()

        data = await _read(r)
        assert data == b"Icom IC-7610 (icom-lan)\n"
        await _close(w)

    async def test_chk_vfo_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\chk_vfo\n")
        await w.drain()

        data = await _read(r)
        assert data == b"0\n"
        await _close(w)

    async def test_get_powerstat_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\get_powerstat\n")
        await w.drain()

        data = await _read(r)
        assert data == b"1\n"
        await _close(w)

    async def test_get_lock_mode_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\get_lock_mode\n")
        await w.drain()

        data = await _read(r)
        assert data == b"0\n"
        await _close(w)

    async def test_set_mode_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"M USB 2400\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT 0\n"
        await _close(w)

    async def test_set_vfo_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"V VFOA\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT 0\n"
        await _close(w)

    async def test_set_split_vfo_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"S 0 VFOA\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT 0\n"
        await _close(w)


# ---------------------------------------------------------------------------
# dump_state wire format
# ---------------------------------------------------------------------------


class TestDumpStateWire:
    _EXPECTED_DUMP_STATE = (
        b"0\n3078\n1\n"
        b"100000.000000 60000000.000000 0x1ff -1 -1 0x3 0xf\n"
        b"0 0 0 0 0 0 0\n"
        b"1800000.000000 60000000.000000 0x1ff 5000 100000 0x3 0xf\n"
        b"0 0 0 0 0 0 0\n"
        b"0x1ff 1\n0 0\n"
        b"0x1ff 3000\n0x1ff 2400\n0x1ff 1800\n"
        b"0 0\n9999\n9999\n0\n0\n"
        b"12 20 0\n6 12 18 0\n"
        b"0x00011B3E\n0x00011B3E\n0x5401791B\n0x0001791B\n0\n0\n"
    )

    async def test_dump_state_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\dump_state\n")
        await w.drain()

        data = await _read(r)
        assert data == self._EXPECTED_DUMP_STATE

    async def test_dump_caps_wire(self, wire_server: RigctldServer) -> None:
        """'1' (dump_caps alias) returns same output as dump_state."""
        r, w = await _connect(wire_server)
        w.write(b"1\n")
        await w.drain()

        data = await _read(r)
        assert data == self._EXPECTED_DUMP_STATE


# ---------------------------------------------------------------------------
# Multiple commands on the same connection
# ---------------------------------------------------------------------------


class TestMultipleCommands:
    async def test_three_sequential_get_freq(self, wire_server: RigctldServer) -> None:
        """Three f commands on the same connection → three identical responses."""
        r, w = await _connect(wire_server)

        for _ in range(3):
            w.write(b"f\n")
            await w.drain()
            data = await _read(r)
            assert data == b"14074000\n"

        await _close(w)

    async def test_mixed_get_set_sequence(self, wire_server: RigctldServer) -> None:
        """set_freq then get_ptt on the same connection."""
        r, w = await _connect(wire_server)

        w.write(b"F 14074000\n")
        await w.drain()
        resp1 = await _read(r)
        assert resp1 == b"RPRT 0\n"

        w.write(b"t\n")
        await w.drain()
        resp2 = await _read(r)
        assert resp2 == b"0\n"

        await _close(w)

    async def test_set_then_get_ptt_reflects_state(
        self, wire_server: RigctldServer
    ) -> None:
        """set_ptt(1) then get_ptt should return 1."""
        r, w = await _connect(wire_server)

        w.write(b"T 1\n")
        await w.drain()
        await _read(r)  # consume RPRT 0

        w.write(b"t\n")
        await w.drain()
        data = await _read(r)
        assert data == b"1\n"

        await _close(w)

    async def test_get_and_set_interleaved(self, wire_server: RigctldServer) -> None:
        """Interleave several gets and sets; each response is correct."""
        r, w = await _connect(wire_server)

        commands_responses = [
            (b"f\n", b"14074000\n"),
            (b"v\n", b"VFOA\n"),
            (b"F 7050000\n", b"RPRT 0\n"),
            (b"j\n", b"0\n"),
        ]

        for cmd_bytes, expected in commands_responses:
            w.write(cmd_bytes)
            await w.drain()
            data = await _read(r)
            assert data == expected, f"failed on cmd {cmd_bytes!r}"

        await _close(w)


# ---------------------------------------------------------------------------
# Quit closes connection
# ---------------------------------------------------------------------------


class TestQuitWire:
    async def test_quit_closes_connection(self, wire_server: RigctldServer) -> None:
        """'q\\n' causes the server to close the TCP connection (EOF)."""
        r, w = await _connect(wire_server)
        w.write(b"q\n")
        await w.drain()

        data = await _read_eof(r)
        assert data == b""  # EOF — server closed the connection
        await _close(w)

    async def test_quit_after_commands(self, wire_server: RigctldServer) -> None:
        """Quit after a get command properly terminates the session."""
        r, w = await _connect(wire_server)

        w.write(b"f\n")
        await w.drain()
        await _read(r)  # consume get_freq response

        w.write(b"q\n")
        await w.drain()
        data = await _read_eof(r)
        assert data == b""
        await _close(w)

    async def test_quit_decrements_client_count(
        self, wire_server: RigctldServer
    ) -> None:
        r, w = await _connect(wire_server)
        await asyncio.sleep(0.05)  # let server register client
        assert wire_server._client_count == 1

        w.write(b"q\n")
        await w.drain()
        await _read_eof(r)
        await asyncio.sleep(0.05)  # let done-callback fire

        assert wire_server._client_count == 0
        await _close(w)


# ---------------------------------------------------------------------------
# Read-only mode
# ---------------------------------------------------------------------------


class TestReadOnlyMode:
    async def test_set_freq_read_only(self, ro_wire_server: RigctldServer) -> None:
        r, w = await _connect(ro_wire_server)
        w.write(b"F 14074000\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -22\n"
        await _close(w)

    async def test_set_mode_read_only(self, ro_wire_server: RigctldServer) -> None:
        r, w = await _connect(ro_wire_server)
        w.write(b"M USB\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -22\n"
        await _close(w)

    async def test_set_ptt_read_only(self, ro_wire_server: RigctldServer) -> None:
        r, w = await _connect(ro_wire_server)
        w.write(b"T 1\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -22\n"
        await _close(w)

    async def test_set_vfo_read_only(self, ro_wire_server: RigctldServer) -> None:
        r, w = await _connect(ro_wire_server)
        w.write(b"V VFOA\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -22\n"
        await _close(w)

    async def test_set_split_vfo_read_only(self, ro_wire_server: RigctldServer) -> None:
        r, w = await _connect(ro_wire_server)
        w.write(b"S 0 VFOA\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -22\n"
        await _close(w)

    async def test_get_commands_still_work_read_only(
        self, ro_wire_server: RigctldServer
    ) -> None:
        """Read commands should succeed even in read-only mode."""
        r, w = await _connect(ro_wire_server)
        w.write(b"f\n")
        await w.drain()

        data = await _read(r)
        assert data == b"14074000\n"
        await _close(w)


# ---------------------------------------------------------------------------
# Invalid / unknown commands
# ---------------------------------------------------------------------------


class TestInvalidCommands:
    async def test_unknown_command_returns_enimpl(
        self, wire_server: RigctldServer
    ) -> None:
        """Unknown command returns RPRT -4 (ENIMPL) without closing connection."""
        r, w = await _connect(wire_server)
        w.write(b"garbage\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -4\n"
        await _close(w)

    async def test_unknown_command_connection_stays_open(
        self, wire_server: RigctldServer
    ) -> None:
        """After an unknown-command error, the connection should stay open."""
        r, w = await _connect(wire_server)

        # First command: unknown
        w.write(b"garbage\n")
        await w.drain()
        await _read(r)  # consume RPRT -4

        # Second command: valid
        w.write(b"f\n")
        await w.drain()
        data = await _read(r)
        assert data == b"14074000\n"

        await _close(w)

    async def test_set_mode_invalid_mode_returns_einval(
        self, wire_server: RigctldServer
    ) -> None:
        """set_mode with an invalid mode name returns RPRT -1 (EINVAL)."""
        r, w = await _connect(wire_server)
        w.write(b"M INVALID\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -1\n"
        await _close(w)

    async def test_crlf_line_ending(self, wire_server: RigctldServer) -> None:
        """CRLF line endings are accepted (Windows clients)."""
        r, w = await _connect(wire_server)
        w.write(b"f\r\n")
        await w.drain()

        data = await _read(r)
        assert data == b"14074000\n"
        await _close(w)

    async def test_long_form_unknown_command(self, wire_server: RigctldServer) -> None:
        """Long-form unknown command '\\bogus' returns RPRT -4."""
        r, w = await _connect(wire_server)
        w.write(b"\\bogus_command\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -4\n"
        await _close(w)


# ---------------------------------------------------------------------------
# Level commands wire format
# ---------------------------------------------------------------------------


class TestLevelWire:
    async def test_get_level_strength_wire(self, wire_server: RigctldServer) -> None:
        """'l STRENGTH' with raw=120 → strength dB string terminated by newline."""
        r, w = await _connect(wire_server)
        w.write(b"l STRENGTH\n")
        await w.drain()

        data = await _read(r)
        # raw=120 → round((120/241)*114 - 54) = 3
        assert data == b"3\n"
        await _close(w)

    async def test_get_level_rfpower_wire(self, wire_server: RigctldServer) -> None:
        """'l RFPOWER' with raw=255 → '1.000000\\n'."""
        r, w = await _connect(wire_server)
        w.write(b"l RFPOWER\n")
        await w.drain()

        data = await _read(r)
        assert data == b"1.000000\n"
        await _close(w)

    async def test_get_level_swr_wire(self, wire_server: RigctldServer) -> None:
        """'l SWR' with calibrated ratio 1.0 → '1.000000\\n'."""
        r, w = await _connect(wire_server)
        w.write(b"l SWR\n")
        await w.drain()

        data = await _read(r)
        assert data == b"1.000000\n"
        await _close(w)

    async def test_get_level_unknown_level_wire(
        self, wire_server: RigctldServer
    ) -> None:
        """'l NOSUCHLEVEL' → RPRT -1 (EINVAL from handler)."""
        r, w = await _connect(wire_server)
        w.write(b"l NOSUCHLEVEL\n")
        await w.drain()

        data = await _read(r)
        assert data == b"RPRT -1\n"
        await _close(w)


# ---------------------------------------------------------------------------
# Power conversion commands
# ---------------------------------------------------------------------------


class TestPowerConversionWire:
    async def test_power2mw_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\power2mW 1.0 14074000 USB\n")
        await w.drain()

        data = await _read(r)
        assert data == b"100000\n"
        await _close(w)

    async def test_mw2power_wire(self, wire_server: RigctldServer) -> None:
        r, w = await _connect(wire_server)
        w.write(b"\\mW2power 100000 14074000 USB\n")
        await w.drain()

        data = await _read(r)
        assert data == b"1.000000\n"
        await _close(w)
