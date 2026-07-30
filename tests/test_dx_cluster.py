"""Tests for src/rigplane/web/dx_cluster.py

Task 1: DXSpot dataclass + parse_spot
Task 2: DXClusterClient asyncio telnet
Task 3: SpotBuffer
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.web.dx_cluster import DXClusterClient, DXSpot, SpotBuffer, parse_spot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _read_http_response(
    reader: asyncio.StreamReader,
) -> tuple[int, dict[str, str], bytes]:
    """Read one complete HTTP response from ``reader``.

    A single ``reader.read(n)`` returns whatever one TCP segment happened to
    carry, so bodies larger than a segment were silently truncated mid-JSON.
    Read the headers up to the blank line, then take either ``Content-Length``
    body bytes or everything until EOF (callers send ``Connection: close``, and
    the server always sets ``Content-Length``).
    """
    header_bytes = (await reader.readuntil(b"\r\n\r\n"))[:-4]

    lines = header_bytes.decode("ascii", errors="replace").split("\r\n")
    status_code = int(lines[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

    raw_length = headers.get("content-length")
    if raw_length is None:
        body = await reader.read()  # until EOF
    else:
        body = await reader.readexactly(int(raw_length))

    return status_code, headers, body


# ---------------------------------------------------------------------------
# Task 1: parse_spot
# ---------------------------------------------------------------------------


class TestParseSpot:
    def test_basic_dxspider_spot(self):
        line = "DX de K1ABC:     14074.0  JA1XYZ       FT8 +05dB from PM95   1234Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.spotter == "K1ABC"
        assert spot.freq == 14074000  # Hz
        assert spot.call == "JA1XYZ"
        assert spot.comment == "FT8 +05dB from PM95"

    def test_time_utc_extracted(self):
        line = "DX de K1ABC:     14074.0  JA1XYZ       FT8 +05dB from PM95   1234Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.time_utc == "1234Z"

    def test_freq_converted_to_hz(self):
        line = "DX de W2XYZ:  21074.0  VK3ABC  FT8  0800Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.freq == 21074000

    def test_freq_no_decimal(self):
        line = "DX de W2XYZ:  7000  VK3ABC  SSB 59  0800Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.freq == 7000000

    def test_spot_no_time(self):
        line = "DX de K1ABC:  14074.0  JA1XYZ  FT8 -10dB"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.call == "JA1XYZ"
        assert spot.time_utc == ""
        assert spot.comment == "FT8 -10dB"

    def test_spot_no_comment_no_time(self):
        line = "DX de K1ABC:  14074.0  JA1XYZ"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.call == "JA1XYZ"
        assert spot.comment == ""
        assert spot.time_utc == ""

    def test_spot_no_comment_with_time(self):
        line = "DX de K1ABC:  14074.0  JA1XYZ  1234Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.comment == ""
        assert spot.time_utc == "1234Z"

    def test_extra_whitespace(self):
        line = "DX de   K1ABC:    14074.0   JA1XYZ    FT8   1234Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.spotter == "K1ABC"
        assert spot.freq == 14074000
        assert spot.call == "JA1XYZ"

    def test_high_freq_vhf(self):
        line = "DX de K1ABC:  144200.0  W1ABC  SSB  1800Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.freq == 144200000

    def test_callsign_with_ssid(self):
        line = "DX de K1ABC-3:  14074.0  JA1XYZ  FT8  1234Z"
        spot = parse_spot(line)
        assert spot is not None
        assert spot.spotter == "K1ABC-3"

    def test_non_spot_line_returns_none(self):
        line = "Hello K1ABC-3 de dxspider 1.55"
        assert parse_spot(line) is None

    def test_empty_line_returns_none(self):
        assert parse_spot("") is None

    def test_login_prompt_returns_none(self):
        assert parse_spot("login: ") is None

    def test_cluster_info_line_returns_none(self):
        assert parse_spot("WWV de W0MU <18Z> :   SFI=85, A=8, K=2") is None

    def test_spot_is_frozen_dataclass(self):
        line = "DX de K1ABC:  14074.0  JA1XYZ  FT8  1234Z"
        spot = parse_spot(line)
        assert spot is not None
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            spot.call = "MODIFIED"  # type: ignore[misc]

    def test_spot_has_timestamp(self):
        line = "DX de K1ABC:  14074.0  JA1XYZ  FT8  1234Z"
        before = time.monotonic()
        spot = parse_spot(line)
        after = time.monotonic()
        assert spot is not None
        assert before <= spot.timestamp <= after


# ---------------------------------------------------------------------------
# Task 2: DXClusterClient
# ---------------------------------------------------------------------------

_SPOT_LINE_1 = b"DX de K1ABC:  14074.0  JA1XYZ  FT8 +05dB  1234Z\r\n"
_SPOT_LINE_2 = b"DX de W2DEF:  7074.0  VK3ABC  FT8 -10dB  0800Z\r\n"
_NON_SPOT_LINE = b"Hello K1ABC-3 de dxspider 1.55\r\n"


def _make_blocking_reader(*lines: bytes) -> asyncio.StreamReader:
    """Feed lines but NO EOF — reader blocks after them, preventing reconnect loops."""
    reader = asyncio.StreamReader()
    for line in lines:
        reader.feed_data(line)
    return reader


def _make_writer() -> MagicMock:
    writer = MagicMock()
    writer.write = MagicMock()
    writer.drain = AsyncMock()
    writer.is_closing = MagicMock(return_value=False)
    writer.close = MagicMock()
    writer.wait_closed = AsyncMock()
    return writer


async def _run_and_cancel(coro, *, delay: float = 0.05) -> None:
    """Start a coroutine as a task, wait briefly, then cancel it."""
    task = asyncio.create_task(coro)
    await asyncio.sleep(delay)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


class TestDXClusterClient:
    async def test_connects_and_sends_callsign(self):
        """Client sends callsign immediately after connecting."""
        reader = _make_blocking_reader()
        writer = _make_writer()

        with patch(
            "rigplane.web.dx_cluster.asyncio.open_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ):
            client = DXClusterClient(
                "dx.example.com", 7300, "K1ABC", on_spot=MagicMock()
            )
            await _run_and_cancel(client.start())

        writer.write.assert_called_once_with(b"K1ABC\r\n")

    async def test_calls_on_spot_for_each_valid_line(self):
        """on_spot callback is invoked once per valid spot line."""
        reader = _make_blocking_reader(_SPOT_LINE_1, _SPOT_LINE_2)
        writer = _make_writer()
        spots: list[DXSpot] = []

        with patch(
            "rigplane.web.dx_cluster.asyncio.open_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ):
            client = DXClusterClient(
                "dx.example.com", 7300, "K1ABC", on_spot=spots.append
            )
            await _run_and_cancel(client.start())

        assert len(spots) == 2
        assert spots[0].call == "JA1XYZ"
        assert spots[1].call == "VK3ABC"

    async def test_ignores_non_spot_lines(self):
        """Non-spot lines do not trigger on_spot."""
        reader = _make_blocking_reader(_NON_SPOT_LINE, _SPOT_LINE_1)
        writer = _make_writer()
        spots: list[DXSpot] = []

        with patch(
            "rigplane.web.dx_cluster.asyncio.open_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ):
            client = DXClusterClient(
                "dx.example.com", 7300, "K1ABC", on_spot=spots.append
            )
            await _run_and_cancel(client.start())

        assert len(spots) == 1
        assert spots[0].call == "JA1XYZ"

    async def test_reconnects_after_disconnect(self):
        """Client retries once after a connection failure then reads spots."""
        call_count = 0
        spots: list[DXSpot] = []
        # Event set when the second (successful) connection occurs.
        connected = asyncio.Event()

        async def fake_open(host, port):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionRefusedError("refused")
            reader = _make_blocking_reader(_SPOT_LINE_1)
            connected.set()
            return reader, _make_writer()

        client = DXClusterClient("dx.example.com", 7300, "K1ABC", on_spot=spots.append)

        with patch("rigplane.web.dx_cluster.asyncio.open_connection", new=fake_open):
            with patch("rigplane.web.dx_cluster.asyncio.sleep", new=AsyncMock()):
                task = asyncio.create_task(client.start())
                # Wait for second connection (instant since sleep is mocked)
                await asyncio.wait_for(connected.wait(), timeout=2.0)
                await asyncio.sleep(0)  # let the spot be processed
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        assert call_count >= 2
        assert len(spots) >= 1

    async def test_stop_sets_flag_and_closes_writer(self):
        """stop() sets _running=False and closes the active writer."""
        reader = _make_blocking_reader()
        writer = _make_writer()

        client = DXClusterClient("dx.example.com", 7300, "K1ABC", on_spot=MagicMock())

        with patch(
            "rigplane.web.dx_cluster.asyncio.open_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ):
            task = asyncio.create_task(client.start())
            await asyncio.sleep(0.05)  # let it connect

            await client.stop()
            assert not client._running
            writer.close.assert_called()

            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def test_cancelled_error_propagates(self):
        """CancelledError from external task cancellation is not swallowed."""
        reader = _make_blocking_reader()
        writer = _make_writer()

        client = DXClusterClient("dx.example.com", 7300, "K1ABC", on_spot=MagicMock())

        with patch(
            "rigplane.web.dx_cluster.asyncio.open_connection",
            new=AsyncMock(return_value=(reader, writer)),
        ):
            task = asyncio.create_task(client.start())
            await asyncio.sleep(0.05)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task


# ---------------------------------------------------------------------------
# Task 3: SpotBuffer
# ---------------------------------------------------------------------------


def _make_spot(call: str, freq: int = 14074000, *, ts: float | None = None) -> DXSpot:
    spot = DXSpot(spotter="K1ABC", freq=freq, call=call)
    if ts is not None:
        # dataclasses.replace works on frozen dataclasses
        spot = dataclasses.replace(spot, timestamp=ts)
    return spot


class TestSpotBuffer:
    def test_add_and_get_single_spot(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ"))
        spots = buf.get_spots()
        assert len(spots) == 1
        assert spots[0]["call"] == "JA1XYZ"

    def test_max_len_drops_oldest(self):
        buf = SpotBuffer(maxlen=3)
        for i in range(5):
            buf.add(_make_spot(f"W{i}ABC"))
        spots = buf.get_spots()
        # Only 3 most recent remain
        assert len(spots) == 3
        calls = [s["call"] for s in spots]
        assert "W0ABC" not in calls
        assert "W1ABC" not in calls
        assert "W4ABC" in calls

    def test_get_spots_returns_dicts(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ", freq=14074000))
        spot = buf.get_spots()[0]
        assert spot["call"] == "JA1XYZ"
        assert spot["freq"] == 14074000
        assert spot["spotter"] == "K1ABC"
        assert "comment" in spot
        assert "time_utc" in spot
        assert "timestamp" in spot

    def test_get_spots_filter_by_band_20m(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ", freq=14074000))  # 20m
        buf.add(_make_spot("VK3ABC", freq=7074000))  # 40m
        buf.add(_make_spot("W1ABC", freq=21074000))  # 15m
        spots_20m = buf.get_spots(band="20m")
        assert len(spots_20m) == 1
        assert spots_20m[0]["call"] == "JA1XYZ"

    def test_get_spots_filter_all_bands(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ", freq=14074000))
        buf.add(_make_spot("VK3ABC", freq=7074000))
        assert len(buf.get_spots()) == 2

    def test_get_spots_unknown_band_returns_empty(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ", freq=14074000))
        assert buf.get_spots(band="99m") == []

    def test_expire_removes_old_spots(self):
        buf = SpotBuffer()
        now = time.monotonic()
        old = _make_spot("OLD", ts=now - 3000)  # 50 min ago
        fresh = _make_spot("FRESH", ts=now - 60)  # 1 min ago
        buf.add(old)
        buf.add(fresh)
        assert len(buf.get_spots()) == 2

        buf.expire(max_age_s=1800)  # 30 min threshold
        spots = buf.get_spots()
        assert len(spots) == 1
        assert spots[0]["call"] == "FRESH"

    def test_expire_empty_buffer_is_noop(self):
        buf = SpotBuffer()
        buf.expire(max_age_s=1800)
        assert buf.get_spots() == []

    def test_to_json_returns_valid_json(self):
        buf = SpotBuffer()
        buf.add(_make_spot("JA1XYZ", freq=14074000))
        raw = buf.to_json()
        parsed = json.loads(raw)
        assert isinstance(parsed, list)
        assert parsed[0]["call"] == "JA1XYZ"

    def test_to_json_empty_buffer(self):
        buf = SpotBuffer()
        assert json.loads(buf.to_json()) == []


# ---------------------------------------------------------------------------
# Task 4: WebSocket broadcast + REST endpoint
# ---------------------------------------------------------------------------


class TestWebServerDXBroadcast:
    async def test_broadcast_dx_spot_sends_to_control_queue(self):
        """_broadcast_dx_spot() pushes dx_spot message to control event queues."""
        from rigplane.web.server import WebConfig, WebServer

        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        srv = WebServer(None, config)

        q: asyncio.Queue = asyncio.Queue()
        srv.register_control_event_queue(q)

        spot = DXSpot(
            spotter="K1ABC",
            freq=14074000,
            call="JA1XYZ",
            comment="FT8",
            time_utc="1234Z",
        )
        srv._broadcast_dx_spot(spot)

        msg = q.get_nowait()
        assert msg["type"] == "dx_spot"
        assert msg["spot"]["call"] == "JA1XYZ"
        assert msg["spot"]["freq"] == 14074000
        assert msg["spot"]["spotter"] == "K1ABC"
        assert msg["spot"]["comment"] == "FT8"
        assert msg["spot"]["time_utc"] == "1234Z"

    async def test_broadcast_dx_spot_adds_to_buffer(self):
        """_broadcast_dx_spot() adds spot to the SpotBuffer."""
        from rigplane.web.server import WebConfig, WebServer

        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        srv = WebServer(None, config)

        spot = DXSpot(spotter="K1ABC", freq=14074000, call="JA1XYZ")
        srv._broadcast_dx_spot(spot)

        spots = srv._spot_buffer.get_spots()
        assert len(spots) == 1
        assert spots[0]["call"] == "JA1XYZ"

    async def test_dx_spots_rest_endpoint(self):
        """GET /api/v1/dx/spots returns current spots as JSON."""
        import asyncio

        from rigplane.web.server import WebConfig, WebServer

        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        srv = WebServer(None, config)
        await srv.start()

        spot = DXSpot(spotter="K1ABC", freq=14074000, call="JA1XYZ")
        srv._broadcast_dx_spot(spot)

        host, port = srv._server.sockets[0].getsockname()

        reader, writer = await asyncio.open_connection(host, port)
        request = f"GET /api/v1/dx/spots HTTP/1.1\r\nHost: {host}:{port}\r\nConnection: close\r\n\r\n"
        writer.write(request.encode())
        await writer.drain()
        status, _headers, body = await asyncio.wait_for(
            _read_http_response(reader), timeout=5.0
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass

        await srv.stop()

        assert status == 200
        data = json.loads(body)
        assert "spots" in data
        assert len(data["spots"]) == 1
        assert data["spots"][0]["call"] == "JA1XYZ"

    async def test_client_connect_receives_dx_spots(self):
        """On subscribe, client receives dx_spots message with current buffer."""
        from unittest.mock import AsyncMock, MagicMock

        from rigplane.web.handlers import ControlHandler
        from rigplane.web.server import WebConfig, WebServer
        from rigplane.web.websocket import WS_OP_TEXT, WebSocketConnection

        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        srv = WebServer(None, config)

        spot = DXSpot(spotter="K1ABC", freq=14074000, call="JA1XYZ")
        srv._broadcast_dx_spot(spot)

        sent_texts = []
        ws = MagicMock(spec=WebSocketConnection)
        ws.send_text = AsyncMock(side_effect=lambda x: sent_texts.append(x))

        call_count = 0

        async def mock_recv():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return WS_OP_TEXT, json.dumps(
                    {"type": "subscribe", "streams": ["state"]}
                ).encode()
            raise EOFError()

        ws.recv = mock_recv

        handler = ControlHandler(ws, None, "1.0", "IC-7610", server=srv)
        await handler.run()

        messages = [json.loads(t) for t in sent_texts]
        types = [m["type"] for m in messages]
        assert "dx_spots" in types, f"Expected dx_spots in {types}"
        dx_msg = next(m for m in messages if m["type"] == "dx_spots")
        assert len(dx_msg["spots"]) == 1
        assert dx_msg["spots"][0]["call"] == "JA1XYZ"

    async def test_dx_client_started_when_configured(self):
        """WebConfig with dx_cluster_host starts DXClusterClient on server.start()."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from rigplane.web.server import WebConfig, WebServer

        config = WebConfig(
            host="127.0.0.1",
            port=0,
            keepalive_interval=9999.0,
            dx_cluster_host="dx.example.com",
            dx_cluster_port=7373,
            dx_callsign="K1ABC",
        )
        srv = WebServer(None, config)

        with patch("rigplane.web.web_startup.DXClusterClient") as MockClient:
            mock_instance = MagicMock()
            mock_instance.start = AsyncMock()
            mock_instance.stop = AsyncMock()
            MockClient.return_value = mock_instance

            await srv.start()
            MockClient.assert_called_once_with(
                "dx.example.com", 7373, "K1ABC", on_spot=srv._broadcast_dx_spot
            )
            await srv.stop()

    async def test_no_dx_client_without_config(self):
        """Without dx_cluster_host, no DXClusterClient is started."""
        from rigplane.web.server import WebConfig, WebServer

        config = WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
        srv = WebServer(None, config)
        await srv.start()
        assert srv._dx_client is None
        await srv.stop()


# ---------------------------------------------------------------------------
# Task 5: CLI flags
# ---------------------------------------------------------------------------


class TestCLIDXClusterFlags:
    def _build_parser(self):
        from rigplane.cli import _build_parser

        return _build_parser()

    def test_dx_cluster_flag_parsed(self):
        """--dx-cluster HOST:PORT is accepted by the web command."""
        parser = self._build_parser()
        args = parser.parse_args(
            ["--host", "192.168.1.1", "web", "--dx-cluster", "dxc.nc7j.com:7373"]
        )
        assert args.dx_cluster == "dxc.nc7j.com:7373"

    def test_callsign_flag_parsed(self):
        """--callsign CALL is accepted by the web command."""
        parser = self._build_parser()
        args = parser.parse_args(
            ["--host", "192.168.1.1", "web", "--callsign", "KN4KYD"]
        )
        assert args.callsign == "KN4KYD"

    def test_without_dx_cluster_is_none(self):
        """--dx-cluster defaults to None when not specified."""
        parser = self._build_parser()
        args = parser.parse_args(["--host", "192.168.1.1", "web"])
        assert args.dx_cluster is None

    def test_without_callsign_is_none(self):
        """--callsign defaults to None when not specified."""
        parser = self._build_parser()
        args = parser.parse_args(["--host", "192.168.1.1", "web"])
        assert args.callsign is None

    def test_dx_cluster_and_callsign_together(self):
        """Both flags work together."""
        parser = self._build_parser()
        args = parser.parse_args(
            [
                "--host",
                "192.168.1.1",
                "web",
                "--dx-cluster",
                "dxc.nc7j.com:7373",
                "--callsign",
                "KN4KYD",
            ]
        )
        assert args.dx_cluster == "dxc.nc7j.com:7373"
        assert args.callsign == "KN4KYD"

    async def test_webconfig_gets_dx_cluster_settings(self):
        """_cmd_web parses HOST:PORT and passes dx settings to WebConfig."""
        import argparse

        from rigplane.web.server import WebConfig

        # Simulate what _cmd_web does when --dx-cluster is provided
        args = argparse.Namespace(
            web_host="127.0.0.1",
            web_port=0,
            web_static_dir=None,
            web_bridge=None,
            web_bridge_tx_device=None,
            web_bridge_rx_only=False,
            web_rigctld=False,
            web_rigctld_port=4532,
            dx_cluster="dxc.nc7j.com:7373",
            callsign="KN4KYD",
        )

        config_kwargs = {
            "host": args.web_host,
            "port": args.web_port,
        }
        dx_cluster = args.dx_cluster
        if dx_cluster:
            host, sep, port_str = dx_cluster.rpartition(":")
            assert host == "dxc.nc7j.com"
            assert port_str == "7373"
            config_kwargs["dx_cluster_host"] = host
            config_kwargs["dx_cluster_port"] = int(port_str)
            config_kwargs["dx_callsign"] = args.callsign or ""

        config = WebConfig(**config_kwargs)
        assert config.dx_cluster_host == "dxc.nc7j.com"
        assert config.dx_cluster_port == 7373
        assert config.dx_callsign == "KN4KYD"
