"""Recording-proxy relay tests (MOR-3821 C0, mini-only).

Drives the real leaf ``TciServer`` as the fake upstream and the real
``eesdr-tci`` Listener as the client, with the proxy under test in the
middle on real loopback sockets. Covers relay fidelity both directions,
header-only binary recording (never payload bytes), the recording size
cap, the repo-path refusal, and clean shutdown.
"""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import socket
import struct
from pathlib import Path

import pytest

from rigplane.tci.frames import (
    HEADER_SIZE,
    SampleType,
    StreamHeader,
    StreamType,
    pack_header,
)
from rigplane.tci.handler import CommandOutcome
from rigplane.tci.server import TciServer
from rigplane.ws import make_accept_key

pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")

import importlib  # noqa: E402

listener_mod = importlib.import_module("eesdr_tci.listener")

Listener = listener_mod.Listener

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "tci_capture" / "proxy.py"


def load_proxy():  # type: ignore[no-untyped-def]
    """Load the proxy script as a module (fails until the script exists)."""
    import sys

    spec = importlib.util.spec_from_file_location("tci_capture_proxy", SCRIPT)
    if spec is None or spec.loader is None:
        raise FileNotFoundError(f"proxy script missing: {SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    # Visible under its own name: dataclasses under
    # ``from __future__ import annotations`` resolve string field types
    # through ``sys.modules``.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def proxy_module():  # type: ignore[no-untyped-def]
    return load_proxy()


@pytest.fixture()
def tcp_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


BURST = [
    "protocol:tciserver,1;",
    "device:rigplane;",
    "trx_count:1;",
    "channels_count:2;",
    "vfo:0,0,14074000;",
    "vfo:0,1,14074000;",
    "modulation:0,USB;",
    "trx:0,false;",
    "receive_only:false;",
    "ready;",
]


class FakeUpstream:
    """Leaf handler: init burst, echo vfo writes, record everything."""

    def __init__(self) -> None:
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.binaries: list[bytes] = []
        self.disconnect_calls = 0

    async def on_connect(self) -> list[str]:
        return list(BURST)

    async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
        self.commands.append((name, args))
        if name == "vfo":
            line = f"vfo:{','.join(args)};"
            return CommandOutcome(to_sender=[line])
        return CommandOutcome(to_sender=[f"{name}:{','.join(args)};"])

    async def on_binary(self, payload: bytes) -> None:
        self.binaries.append(bytes(payload))

    async def on_disconnect(self) -> None:
        self.disconnect_calls += 1


class Observed:
    """Collects real-listener param and data callbacks."""

    def __init__(self) -> None:
        self.params: list[tuple[object, object, object, object]] = []
        self.packets: list[object] = []

    async def on_param(
        self, name: object, rx: object, sub_rx: object, params: object
    ) -> None:
        self.params.append((name, rx, sub_rx, params))

    async def on_data(self, packet: object) -> None:
        self.packets.append(packet)


def make_upstream(handler: FakeUpstream) -> TciServer:
    def _allow(headers: dict[str, str]) -> None:
        return None

    return TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=0,
        allow=_allow,  # type: ignore[arg-type]
    )


async def wait_until(predicate: object, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # type: ignore[operator]
            await asyncio.sleep(0.005)


async def connect_listener(port: int, observed: Observed) -> object:
    listener = Listener(f"ws://127.0.0.1:{port}")
    listener.add_param_listener("*", observed.on_param)
    listener.add_data_listener("*", observed.on_data)
    await listener.start()
    await listener.ready()
    return listener


async def close_listener(listener: object) -> None:
    listener.shutdown()  # type: ignore[attr-defined]
    try:
        async with asyncio.timeout(5.0):
            await listener.wait()  # type: ignore[attr-defined]
    except (asyncio.CancelledError, Exception):
        pass
    await asyncio.sleep(0)


def only_capture(tmp_path: Path) -> Path:
    files = sorted(tmp_path.glob("*.jsonl"))
    assert len(files) == 1
    return files[0]


async def test_text_relayed_both_ways_and_recorded(
    tcp_available: None, proxy_module: object, tmp_path: Path
) -> None:
    handler = FakeUpstream()
    upstream = make_upstream(handler)
    await upstream.start()
    proxy = proxy_module.Proxy(  # type: ignore[attr-defined]
        "127.0.0.1",
        0,
        "127.0.0.1",
        upstream.bound_port,
        out_dir=tmp_path,
        label="wsjtx",
    )
    try:
        port = await proxy.start()
        observed = Observed()
        listener = await connect_listener(port, observed)
        try:
            await wait_until(lambda: len(observed.params) >= len(BURST))
            await listener.send("vfo:0,0,14074001;")  # type: ignore[attr-defined]
            await wait_until(lambda: ("VFO", 0, 0, 14074001) in observed.params)
            await wait_until(
                lambda: ("vfo", ("0", "0", "14074001")) in handler.commands
            )
        finally:
            await close_listener(listener)
    finally:
        await proxy.stop()
        await upstream.stop()

    import json

    capture = only_capture(tmp_path)
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    assert records[0]["event"] == "session_start"
    assert records[-1]["event"] == "session_end"
    c2s = [r for r in records if r.get("dir") == "c2s" and r.get("opcode") == 1]
    s2c = [r for r in records if r.get("dir") == "s2c" and r.get("opcode") == 1]
    assert "vfo:0,0,14074001;" in [r["text"] for r in c2s]
    assert "vfo:0,0,14074001;" in [r["text"] for r in s2c]
    for record in records:
        if "text" in record:
            assert "t_mono" in record and "t_wall" in record


async def test_binary_recorded_as_header_only(
    tcp_available: None, proxy_module: object, tmp_path: Path
) -> None:
    handler = FakeUpstream()
    upstream = make_upstream(handler)
    await upstream.start()
    proxy = proxy_module.Proxy(  # type: ignore[attr-defined]
        "127.0.0.1",
        0,
        "127.0.0.1",
        upstream.bound_port,
        out_dir=tmp_path,
        label="rx",
    )
    try:
        port = await proxy.start()
        observed = Observed()
        listener = await connect_listener(port, observed)
        try:
            payload = struct.pack("<4h", 0, 1000, -1000, 32767)
            header = pack_header(
                StreamHeader(
                    receiver=0,
                    sample_rate=48000,
                    sample_type=SampleType.INT16,
                    codec=0,
                    crc=0,
                    length=4,
                    stream_type=StreamType.RX_AUDIO,
                    channels=1,
                    count=7,
                )
            )
            upstream.broadcast_binary(header + payload)
            await wait_until(lambda: len(observed.packets) > 0)
            tx_header = pack_header(
                StreamHeader(
                    receiver=0,
                    sample_rate=48000,
                    sample_type=SampleType.INT16,
                    codec=0,
                    crc=0,
                    length=4,
                    stream_type=StreamType.TX_AUDIO,
                    channels=1,
                    count=3,
                )
            )
            await listener.send(tx_header + payload)  # type: ignore[attr-defined]
            await wait_until(lambda: len(handler.binaries) > 0)
            assert handler.binaries[0] == tx_header + payload
            packet = observed.packets[-1]
            assert packet.data == payload  # type: ignore[attr-defined]
        finally:
            await close_listener(listener)
    finally:
        await proxy.stop()
        await upstream.stop()

    import json

    capture = only_capture(tmp_path)
    raw = capture.read_bytes()
    assert payload not in raw
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    binaries = [r for r in records if r.get("opcode") == 2]
    assert len(binaries) >= 2
    by_dir = {(r["dir"], r["bin"]["type"]) for r in binaries}
    assert ("s2c", 1) in by_dir
    assert ("c2s", 2) in by_dir
    rx = next(r for r in binaries if r["dir"] == "s2c" and r["bin"]["type"] == 1)
    assert rx["bin"]["receiver"] == 0
    assert rx["bin"]["sample_rate"] == 48000
    assert rx["bin"]["format"] == 0
    assert rx["bin"]["length"] == 4
    assert rx["bin"]["channels"] == 1
    assert rx["bin"]["count"] == 7
    assert rx["payload_len"] == HEADER_SIZE + len(payload)
    assert "codec" in rx["bin"] and "crc" in rx["bin"]


async def test_size_cap_stops_recording_not_relay(
    tcp_available: None, proxy_module: object, tmp_path: Path
) -> None:
    handler = FakeUpstream()
    upstream = make_upstream(handler)
    await upstream.start()
    proxy = proxy_module.Proxy(  # type: ignore[attr-defined]
        "127.0.0.1",
        0,
        "127.0.0.1",
        upstream.bound_port,
        out_dir=tmp_path,
        label="capped",
        max_bytes=200,
    )
    try:
        port = await proxy.start()
        observed = Observed()
        listener = await connect_listener(port, observed)
        try:
            await wait_until(lambda: len(observed.params) >= len(BURST))
            await listener.send("vfo:0,0,14074001;")  # type: ignore[attr-defined]
            await wait_until(lambda: ("VFO", 0, 0, 14074001) in observed.params)
        finally:
            await close_listener(listener)
    finally:
        await proxy.stop()
        await upstream.stop()

    import json

    capture = only_capture(tmp_path)
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    trailer = records[-1]
    assert trailer["event"] == "session_end"
    assert trailer["truncated"] is True


def test_out_dir_inside_repo_refused(proxy_module: object, tmp_path: Path) -> None:
    outside = proxy_module.check_out_dir(tmp_path / "x")  # type: ignore[attr-defined]
    assert Path(outside).name == "x"
    repo_dir = Path(__file__).resolve().parent
    with pytest.raises(proxy_module.OutDirRefused):  # type: ignore[attr-defined]
        proxy_module.check_out_dir(repo_dir / "capture")  # type: ignore[attr-defined]
    code = proxy_module.main(  # type: ignore[attr-defined]
        [
            "--listen",
            "127.0.0.1:50002",
            "--upstream",
            "127.0.0.1:50001",
            "--out",
            str(repo_dir / "capture"),
            "--label",
            "x",
        ]
    )
    assert code == 2
    assert not (repo_dir / "capture").exists()


def test_non_loopback_host_flagged(proxy_module: object) -> None:
    assert proxy_module.is_loopback_host("127.0.0.1") is True  # type: ignore[attr-defined]
    assert proxy_module.is_loopback_host("::1") is True  # type: ignore[attr-defined]
    assert proxy_module.is_loopback_host("localhost") is True  # type: ignore[attr-defined]
    assert proxy_module.is_loopback_host("0.0.0.0") is False  # type: ignore[attr-defined]
    assert proxy_module.is_loopback_host("192.168.1.2") is False  # type: ignore[attr-defined]


async def test_upstream_ping_and_close_replies_are_masked(
    tcp_available: None, proxy_module: object, tmp_path: Path
) -> None:
    """Fake upstream pings and closes; proxy replies must be masked (RFC 6455).

    The proxy speaks to upstream as a client, so every frame it builds
    there (PONG, CLOSE) must carry a mask. The capture file must still
    open with session_start and close with session_end.
    """
    seen: dict[str, tuple[int, bytes, bool]] = {}

    async def _fake_upstream(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
            key = ""
            for line in head.decode("ascii").split("\r\n")[1:]:
                name, sep, value = line.partition(":")
                if sep and name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
            writer.write(
                (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {make_accept_key(key)}\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await writer.drain()
            # Unmasked PING, server role.
            writer.write(bytes([0x89, 0x05]) + b"ping!")
            await writer.drain()
            pong_head = await reader.readexactly(2)
            pong_masked = bool(pong_head[1] & 0x80)
            pong_len = pong_head[1] & 0x7F
            pong_key = await reader.readexactly(4) if pong_masked else b""
            pong_raw = await reader.readexactly(pong_len) if pong_len else b""
            pong_payload = (
                bytes(b ^ pong_key[i % 4] for i, b in enumerate(pong_raw))
                if pong_masked
                else pong_raw
            )
            seen["pong"] = (pong_head[0] & 0x0F, pong_payload, pong_masked)
            # Unmasked CLOSE 1000, server role.
            writer.write(bytes([0x88, 0x02]) + struct.pack("!H", 1000))
            await writer.drain()
            close_head = await reader.readexactly(2)
            close_masked = bool(close_head[1] & 0x80)
            close_len = close_head[1] & 0x7F
            close_key = await reader.readexactly(4) if close_masked else b""
            close_raw = await reader.readexactly(close_len) if close_len else b""
            close_payload = (
                bytes(b ^ close_key[i % 4] for i, b in enumerate(close_raw))
                if close_masked
                else close_raw
            )
            seen["close"] = (close_head[0] & 0x0F, close_payload, close_masked)
        finally:
            writer.close()

    upstream = await asyncio.start_server(_fake_upstream, "127.0.0.1", 0)
    assert upstream.sockets
    upstream_port = int(upstream.sockets[0].getsockname()[1])
    proxy = proxy_module.Proxy(  # type: ignore[attr-defined]
        "127.0.0.1",
        0,
        "127.0.0.1",
        upstream_port,
        out_dir=tmp_path,
        label="mask",
    )
    client_writer: asyncio.StreamWriter | None = None
    try:
        port = await proxy.start()
        async with asyncio.timeout(15.0):
            reader, client_writer = await asyncio.open_connection("127.0.0.1", port)
            key = base64.b64encode(b"0123456789abcdef").decode("ascii")
            client_writer.write(
                (
                    "GET / HTTP/1.1\r\n"
                    "Host: 127.0.0.1\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n"
                    "\r\n"
                ).encode("ascii")
            )
            await client_writer.drain()
            head = await reader.readuntil(b"\r\n\r\n")
            assert head.startswith(b"HTTP/1.1 101")

            async def _session_over() -> bool:
                files = sorted(tmp_path.glob("*.jsonl"))
                if not files:
                    return False
                text = files[0].read_text(encoding="utf-8")
                return '"session_end"' in text

            while not await _session_over():
                await asyncio.sleep(0.02)
    finally:
        if client_writer is not None:
            client_writer.close()
        await proxy.stop()
        upstream.close()
        await upstream.wait_closed()

    assert seen["pong"][0] == 0xA
    assert seen["pong"][1] == b"ping!"
    assert seen["pong"][2] is True
    assert seen["close"][0] == 0x8
    assert seen["close"][1] == struct.pack("!H", 1000)
    assert seen["close"][2] is True

    import json

    capture = only_capture(tmp_path)
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    assert records[0]["event"] == "session_start"
    assert records[-1]["event"] == "session_end"


async def test_clean_shutdown_writes_session_end(
    tcp_available: None, proxy_module: object, tmp_path: Path
) -> None:
    import json

    handler = FakeUpstream()
    upstream = make_upstream(handler)
    await upstream.start()
    proxy = proxy_module.Proxy(  # type: ignore[attr-defined]
        "127.0.0.1",
        0,
        "127.0.0.1",
        upstream.bound_port,
        out_dir=tmp_path,
        label="stop",
    )
    try:
        port = await proxy.start()
        observed = Observed()
        listener = await connect_listener(port, observed)
        await wait_until(lambda: len(observed.params) >= len(BURST))
        await close_listener(listener)
        await wait_until(lambda: len(list(tmp_path.glob("*.jsonl"))) == 1)
    finally:
        async with asyncio.timeout(10.0):
            await proxy.stop()
        await upstream.stop()

    capture = only_capture(tmp_path)
    records = [json.loads(line) for line in capture.read_text().splitlines()]
    assert records[0]["event"] == "session_start"
    assert records[-1]["event"] == "session_end"
    assert records[-1]["truncated"] is False
