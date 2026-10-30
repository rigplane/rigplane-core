"""Transparent TCI WebSocket recording proxy (MOR-3821 C0).

Sits between a real TCI client and a real TCI server, relays every frame
both directions unchanged (text and binary), and records the session to a
JSON-lines file outside the git repo: text frames in full, binary frames
as the decoded 64-byte header fields plus payload length only. Audio
payload bytes are never written to disk.

Framing and parsing come from the one shared implementation,
``rigplane.ws`` (the upstream leg uses its client role with
``mask_outgoing=True``); header decoding from the stdlib-only leaf
``rigplane.tci.frames``. Pings are answered by each connection's own
``recv`` loop, not relayed.

Usage (capture)::

    python scripts/tci_capture/proxy.py \\
        --listen 127.0.0.1:50002 --upstream 127.0.0.1:50001 \\
        --out /tmp/tci-capture --label wsjtx

Usage (report on a capture)::

    python scripts/tci_capture/proxy.py --report /tmp/tci-capture/wsjtx-....jsonl

Usage (client-to-server fixture extraction)::

    python scripts/tci_capture/proxy.py --extract-fixtures cap.jsonl \\
        --fixtures-out tests/fixtures/tci/wsjtx.tci
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import json
import logging
import os
import struct
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from rigplane.tci.frames import HEADER_SIZE, unpack_header
from rigplane.ws import (
    WS_OP_BINARY,
    WS_OP_TEXT,
    WebSocketConnection,
    WebSocketError,
    make_accept_key,
    upgrade_response,
)

__all__ = [
    "COVERED",
    "DEFAULT_MAX_BYTES",
    "OPEN",
    "CoverageItem",
    "CoverageReport",
    "OutDirRefused",
    "Proxy",
    "check_out_dir",
    "coverage_report",
    "extract_fixtures",
    "format_report",
    "is_loopback_host",
    "load_capture",
    "main",
]

logger = logging.getLogger(__name__)

#: Refusal when ``--out`` points inside a git checkout (captures stay out
#: of the repo by design, section 10.5).
#: Default ceiling for one session's recording file (256 MiB).
DEFAULT_MAX_BYTES = 256 * 1024 * 1024
#: Largest HTTP upgrade head accepted (bytes, before the blank line).
_MAX_UPGRADE_HEAD = 16 * 1024
#: Bound on one network handshake phase.
_HANDSHAKE_TIMEOUT_S = 10.0
#: Bound on the whole proxy shutdown.
_STOP_TIMEOUT_S = 5.0

COVERED = "COVERED"
OPEN = "OPEN"


class OutDirRefused(ValueError):
    """Raised when the capture directory is inside a git checkout."""


def is_loopback_host(host: str) -> bool:
    """True for loopback bind targets (IPv4, IPv6, ``localhost``)."""
    return host in ("127.0.0.1", "::1", "localhost")


def check_out_dir(out: str | Path) -> Path:
    """Resolve ``out`` and refuse it when it sits inside a git checkout.

    Walks up from the resolved path looking for a ``.git`` entry (a
    directory in a normal clone, a file in a worktree). Raises
    :class:`OutDirRefused` on a hit; otherwise returns the path.
    """
    resolved = Path(out).resolve()
    probe: Path | None = resolved
    while True:
        if (probe / ".git").exists():
            raise OutDirRefused(
                f"refusing to record inside the git checkout at {probe}: "
                "captures are evidence kept outside the repo"
            )
        parent = probe.parent
        if parent == probe:
            return resolved
        probe = parent


def _utc_stamp() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _decode_binary(payload: bytes) -> dict[str, object]:
    """Describe one binary frame without keeping its payload bytes."""
    try:
        header = unpack_header(payload)
    except (struct.error, ValueError) as exc:
        return {"header_error": str(exc)}
    return {
        "receiver": header.receiver,
        "sample_rate": header.sample_rate,
        "format": int(header.sample_type),
        "codec": header.codec,
        "crc": header.crc,
        "length": header.length,
        "type": int(header.stream_type),
        "channels": header.channels,
        "count": header.count,
        "reserved": list(header.reserv),
    }


class _Recorder:
    """One session's JSON-lines file; stops writing past the size cap."""

    def __init__(self, path: Path, max_bytes: int) -> None:
        self._path = path
        self._max_bytes = max_bytes
        self._file = path.open("w", encoding="utf-8")
        self._written = 0
        self.truncated = False
        self.counts = {"c2s_text": 0, "s2c_text": 0, "c2s_bin": 0, "s2c_bin": 0}

    def _write(self, record: dict[str, object]) -> None:
        line = json.dumps(record)
        if self._written + len(line) + 1 > self._max_bytes:
            self.truncated = True
            return
        self._file.write(line + "\n")
        self._written += len(line) + 1

    def start(
        self,
        *,
        label: str,
        listen: str,
        upstream: str,
        t_wall: str,
    ) -> None:
        self._write(
            {
                "event": "session_start",
                "label": label,
                "listen": listen,
                "upstream": upstream,
                "t_wall": t_wall,
            }
        )

    def record_text(
        self, direction: str, text: bytes, t_mono: float, t_wall: str
    ) -> None:
        try:
            decoded = text.decode("utf-8")
        except UnicodeDecodeError as exc:
            self._write(
                {
                    "t_mono": t_mono,
                    "t_wall": t_wall,
                    "dir": direction,
                    "opcode": WS_OP_TEXT,
                    "text_error": str(exc),
                }
            )
            return
        self.counts[f"{direction}_text"] += 1
        self._write(
            {
                "t_mono": t_mono,
                "t_wall": t_wall,
                "dir": direction,
                "opcode": WS_OP_TEXT,
                "text": decoded,
            }
        )

    def record_binary(
        self, direction: str, payload: bytes, t_mono: float, t_wall: str
    ) -> None:
        self.counts[f"{direction}_bin"] += 1
        self._write(
            {
                "t_mono": t_mono,
                "t_wall": t_wall,
                "dir": direction,
                "opcode": WS_OP_BINARY,
                "bin": _decode_binary(payload),
                "payload_len": len(payload),
            }
        )

    def finish(self, reason: str) -> None:
        # The trailer bypasses the size cap: a truncated session must
        # still say so in its own file.
        line = json.dumps(
            {
                "event": "session_end",
                "reason": reason,
                "truncated": self.truncated,
                "counts": dict(self.counts),
            }
        )
        try:
            self._file.write(line + "\n")
        finally:
            self._file.close()


def _wall_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


async def _read_http_head(reader: asyncio.StreamReader) -> bytes:
    """Read one HTTP head, ending exactly at the blank line.

    ``readuntil`` consumes through the separator and leaves whatever
    follows (burst frames pipelined with the 101) buffered in the same
    reader, which the new connection keeps using, so nothing is lost.
    A plain ``read(n)`` would swallow those bytes into a local copy.
    """
    try:
        async with asyncio.timeout(_HANDSHAKE_TIMEOUT_S):
            head = await reader.readuntil(b"\r\n\r\n")
    except (
        asyncio.LimitOverrunError,
        asyncio.IncompleteReadError,
        TimeoutError,
    ) as exc:
        raise ValueError("unreadable upgrade head") from exc
    if len(head) > _MAX_UPGRADE_HEAD:
        raise ValueError("upgrade head too large")
    return head


def _parse_request(head: bytes) -> tuple[str, dict[str, str]]:
    """Split an upgrade head into (target, lowercase headers)."""
    text = head.decode("ascii")
    lines = text.split("\r\n")
    target = lines[0].split(" ")[1]
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if separator and name.strip():
            headers[name.strip().lower()] = value.strip()
    return target, headers


async def _accept_client(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter
) -> WebSocketConnection | None:
    """Server-side upgrade for one inbound socket; None when refused."""
    try:
        head = await _read_http_head(reader)
        _, headers = _parse_request(head)
    except (ValueError, UnicodeDecodeError, IndexError):
        writer.close()
        return None
    outcome = upgrade_response(
        headers,
        allow=lambda _headers: None,
        allow_deflate=False,
        strict_tci=True,
    )
    if not hasattr(outcome, "response"):
        status = outcome.status  # type: ignore[union-attr]
        reason = outcome.reason  # type: ignore[union-attr]
        body = outcome.body  # type: ignore[union-attr]
        reply = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
        ).encode("ascii") + body
        writer.write(reply)
        await writer.drain()
        writer.close()
        return None
    writer.write(outcome.response)  # type: ignore[union-attr]
    await writer.drain()
    return WebSocketConnection(reader, writer)


async def _connect_upstream(
    host: str, port: int
) -> tuple[WebSocketConnection, asyncio.StreamWriter] | None:
    """Client-side upgrade against the real TCI server; None on failure."""
    try:
        async with asyncio.timeout(_HANDSHAKE_TIMEOUT_S):
            reader, writer = await asyncio.open_connection(host, port)
    except OSError:
        return None
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        "GET / HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    try:
        writer.write(request.encode("ascii"))
        await writer.drain()
        head = await _read_http_head(reader)
    except (OSError, ValueError):
        writer.close()
        return None
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        writer.close()
        return None
    if not text.startswith("HTTP/1.1 101"):
        writer.close()
        return None
    accept = ""
    for line in text.split("\r\n")[1:]:
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "sec-websocket-accept":
            accept = value.strip()
    if accept != make_accept_key(key):
        writer.close()
        return None
    # Client role: the upstream server expects every frame masked
    # (RFC 6455 section 5.1), including the PONG/CLOSE replies that
    # ``recv`` builds on this connection.
    return WebSocketConnection(reader, writer, mask_outgoing=True), writer


class _Session:
    """One client connection plus its upstream connection and file."""

    def __init__(
        self,
        session_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        upstream_host: str,
        upstream_port: int,
        recorder: _Recorder,
        clock: Callable[[], float],
        label: str,
        listen: str,
    ) -> None:
        self._id = session_id
        self._reader = reader
        self._writer = writer
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._recorder = recorder
        self._clock = clock
        self._label = label
        self._listen = listen
        self._client: WebSocketConnection | None = None
        self._server: WebSocketConnection | None = None
        self._server_writer: asyncio.StreamWriter | None = None

    def _close_transports(self) -> None:
        for conn in (self._client, self._server):
            if conn is not None:
                try:
                    conn.abort()
                except Exception:
                    pass
        for writer in (self._writer, self._server_writer):
            if writer is not None:
                try:
                    writer.close()
                except Exception:
                    pass

    async def run(self) -> None:
        self._recorder.start(
            label=self._label,
            listen=self._listen,
            upstream=f"{self._upstream_host}:{self._upstream_port}",
            t_wall=_wall_now(),
        )
        reason = "client-closed"
        try:
            self._client = await _accept_client(self._reader, self._writer)
            if self._client is None:
                reason = "client-handshake-failed"
                return
            connected = await _connect_upstream(
                self._upstream_host, self._upstream_port
            )
            if connected is None:
                reason = "upstream-unavailable"
                return
            self._server, self._server_writer = connected
            reason = await self._pump()
        finally:
            self._close_transports()
            self._recorder.finish(reason)

    async def _pump(self) -> str:
        assert self._client is not None
        assert self._server is not None
        client = self._client
        server = self._server

        async def forward_client() -> None:
            while True:
                opcode, payload = await client.recv()
                moment = self._clock()
                if opcode == WS_OP_TEXT:
                    self._recorder.record_text("c2s", payload, moment, _wall_now())
                    await server.send_text(payload.decode("utf-8", "replace"))
                elif opcode == WS_OP_BINARY:
                    self._recorder.record_binary("c2s", payload, moment, _wall_now())
                    await server.send_binary(payload)
                else:
                    continue

        async def forward_server() -> None:
            while True:
                opcode, payload = await server.recv()
                moment = self._clock()
                if opcode == WS_OP_TEXT:
                    self._recorder.record_text("s2c", payload, moment, _wall_now())
                    await client.send_text(payload.decode("utf-8", "replace"))
                elif opcode == WS_OP_BINARY:
                    self._recorder.record_binary("s2c", payload, moment, _wall_now())
                    await client.send_binary(payload)
                # Ping/pong never surface here: each connection's own
                # ``recv`` answers them, so they are not relayed.

        to_client = asyncio.ensure_future(forward_client())
        to_server = asyncio.ensure_future(forward_server())
        try:
            done, pending = await asyncio.wait(
                {to_client, to_server}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()
            for task in done | pending:
                try:
                    await task
                except (asyncio.CancelledError, EOFError, OSError, WebSocketError):
                    pass
                except Exception:
                    logger.debug("proxy %s: forward task failed", self._id)
            if to_client in done and to_server not in done:
                return "client-closed"
            if to_server in done and to_client not in done:
                return "upstream-closed"
            return "both-closed"
        finally:
            for task in (to_client, to_server):
                if not task.done():
                    task.cancel()
            await asyncio.gather(to_client, to_server, return_exceptions=True)


class Proxy:
    """The recording proxy: loopback listener, upstream relay, files."""

    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        *,
        out_dir: str | Path,
        label: str = "session",
        max_bytes: int = DEFAULT_MAX_BYTES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._upstream_host = upstream_host
        self._upstream_port = upstream_port
        self._out_dir = check_out_dir(out_dir)
        self._label = label
        self._max_bytes = max_bytes
        self._clock = clock
        self._server: asyncio.Server | None = None
        self._sessions: set[asyncio.Task[None]] = set()
        self._counter = 0

    @property
    def listen_port(self) -> int:
        """Actual bound port (``listen_port=0`` picks an ephemeral one)."""
        if self._server is None or not self._server.sockets:
            raise RuntimeError("proxy is not started")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> int:
        """Bind the listener and start accepting; returns the bound port."""
        self._out_dir.mkdir(parents=True, exist_ok=True)
        if not is_loopback_host(self._listen_host):
            print(
                f"WARNING: listening on {self._listen_host}, not loopback; "
                "TCI audio and control pass through in the clear",
                file=sys.stderr,
            )
        self._server = await asyncio.start_server(
            self._accept, self._listen_host, self._listen_port
        )
        return self.listen_port

    async def stop(self) -> None:
        """Stop accepting, finish every session, close every file."""
        server, self._server = self._server, None
        if server is not None:
            server.close()
            try:
                async with asyncio.timeout(_STOP_TIMEOUT_S):
                    await server.wait_closed()
            except TimeoutError:
                logger.debug("proxy: wait_closed timed out")
        sessions = list(self._sessions)
        for task in sessions:
            task.cancel()
        if sessions:
            await asyncio.gather(*sessions, return_exceptions=True)

    async def _accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        self._counter += 1
        stamp = _utc_stamp()
        path = self._out_dir / f"{self._label}-{stamp}.jsonl"
        if path.exists():
            path = self._out_dir / f"{self._label}-{stamp}-{self._counter}.jsonl"
        recorder = _Recorder(path, self._max_bytes)
        session = _Session(
            f"session-{self._counter}",
            reader,
            writer,
            self._upstream_host,
            self._upstream_port,
            recorder,
            self._clock,
            self._label,
            f"{self._listen_host}:{self._listen_port}",
        )
        task: asyncio.Task[None] = asyncio.ensure_future(session.run())
        self._sessions.add(task)
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._sessions.discard(task)


@dataclass(frozen=True)
class CoverageItem:
    """One open item: COVERED with evidence, or OPEN with what is missing."""

    id: str
    status: str
    evidence: str


@dataclass(frozen=True)
class CoverageReport:
    """The per-item verdicts over one capture."""

    items: tuple[CoverageItem, ...]
    complete: bool = field(compare=False)

    def by_id(self, item_id: str) -> CoverageItem:
        for item in self.items:
            if item.id == item_id:
                return item
        raise KeyError(f"unknown coverage item: {item_id}")


def _text_commands(records: Sequence[dict[str, object]]) -> list[str]:
    texts: list[str] = []
    for record in records:
        text = record.get("text")
        if isinstance(text, str):
            texts.append(text)
    return texts


def _command_names(texts: Sequence[str]) -> list[str]:
    names: list[str] = []
    for text in texts:
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment:
                continue
            name = segment.split(":")[0].strip().lower()
            if name:
                names.append(name)
    return names


def _first_value(texts: Sequence[str], prefix: str) -> str | None:
    for text in texts:
        for segment in text.split(";"):
            segment = segment.strip().lower()
            if segment.startswith(prefix):
                return segment[len(prefix) :].strip(" ;")
    return None


def _echo_delay_ms(records: Sequence[dict[str, object]], prefix: str) -> float | None:
    """Delay between the first c2s write and the next matching s2c echo.

    TCI messages may batch several `;`-terminated commands in one frame,
    so each `;`-separated command is inspected on its own through the
    shared `_first_value` splitter; the record's own timestamp and
    direction still order the write before the echo.
    """
    lowered = prefix.lower()
    wrote_at: float | None = None
    for record in records:
        if record.get("opcode") != WS_OP_TEXT or "text" not in record:
            continue
        text = str(record["text"])
        moment = record.get("t_mono")
        if not isinstance(moment, (int, float)):
            continue
        if record.get("dir") == "c2s" and wrote_at is None:
            if _first_value([text], lowered) is not None:
                wrote_at = float(moment)
        elif record.get("dir") == "s2c" and wrote_at is not None:
            if _first_value([text], lowered) is not None:
                return (float(moment) - wrote_at) * 1000.0
    return None


def _binaries(
    records: Sequence[dict[str, object]],
    direction: str,
    stream_type: int,
) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for record in records:
        if record.get("dir") != direction or record.get("opcode") != 2:
            continue
        header = record.get("bin")
        if isinstance(header, dict) and header.get("type") == stream_type:
            found.append(record)
    return found


_BYTES_PER_SAMPLE = {0: 2, 1: 3, 2: 4, 3: 4}


def coverage_report(records: Sequence[dict[str, object]]) -> CoverageReport:
    """Verdict per open item of design 5.2 plus the 8.1 header questions.

    Items without the needed events are OPEN, never guessed; evidence
    strings quote what the capture held (values, echo delays, header
    fields) so a human can answer the open question from them.
    """
    rows = [r for r in records if isinstance(r, dict)]
    s2c_texts = _text_commands([r for r in rows if r.get("dir") == "s2c"])
    c2s_texts = _text_commands([r for r in rows if r.get("dir") == "c2s"])
    s2c_names = _command_names(s2c_texts)

    items: list[CoverageItem] = []

    if "protocol" in s2c_names and "ready" in s2c_names:
        items.append(
            CoverageItem(
                "init_burst",
                COVERED,
                f"first s2c commands: {','.join(s2c_names[:8])}",
            )
        )
    else:
        items.append(
            CoverageItem("init_burst", OPEN, "no s2c protocol:/ready; burst seen")
        )

    protocol = _first_value(s2c_texts, "protocol:")
    device = _first_value(s2c_texts, "device:")
    if protocol is not None and device is not None:
        items.append(
            CoverageItem(
                "protocol_device",
                COVERED,
                f"protocol={protocol} device={device}",
            )
        )
    else:
        items.append(
            CoverageItem("protocol_device", OPEN, "protocol:/device: strings not seen")
        )

    for item_id, prefix in (("vfo0_echo", "vfo:0,0,"), ("vfo1_echo", "vfo:0,1,")):
        delay = _echo_delay_ms(rows, prefix)
        if delay is not None:
            items.append(
                CoverageItem(item_id, COVERED, f"write+echo {delay:.0f} ms apart")
            )
        else:
            items.append(
                CoverageItem(item_id, OPEN, f"no c2s {prefix} write with s2c echo seen")
            )

    split = _first_value(c2s_texts, "split_enable:")
    if split is not None:
        items.append(CoverageItem("split_enable", COVERED, f"c2s split_enable:{split}"))
    else:
        items.append(CoverageItem("split_enable", OPEN, "no c2s split_enable seen"))

    modulation = _first_value(c2s_texts, "modulation:")
    if modulation is not None:
        items.append(
            CoverageItem("modulation", COVERED, f"c2s modulation:{modulation}")
        )
    else:
        items.append(CoverageItem("modulation", OPEN, "no c2s modulation set seen"))

    audio_start: str | None = None
    audio_argc = 0
    for text in c2s_texts:
        for segment in text.split(";"):
            segment = segment.strip()
            if not segment or ":" not in segment:
                continue
            name, _, args = segment.partition(":")
            if name.strip().lower() == "audio_start":
                audio_start = args.strip()
                audio_argc = len([a for a in args.split(",") if a.strip()])
                break
    if audio_start is not None:
        items.append(
            CoverageItem(
                "audio_start",
                COVERED,
                f"c2s audio_start argc={audio_argc} args={audio_start}",
            )
        )
    else:
        items.append(CoverageItem("audio_start", OPEN, "no c2s audio_start seen"))

    drive: str | None = None
    for text in s2c_texts:
        value = _first_value([text], "drive:")
        if value is not None and len(value.split(",")) == 2:
            drive = value
            break
    if drive is not None:
        items.append(
            CoverageItem("drive_index", COVERED, f"s2c drive:{drive} (trx,power)")
        )
    else:
        items.append(
            CoverageItem("drive_index", OPEN, "no two-argument s2c drive: seen")
        )

    rx_headers = _binaries(rows, "s2c", 1)
    if rx_headers:
        first = rx_headers[0]["bin"]
        assert isinstance(first, dict)
        items.append(
            CoverageItem(
                "rx_header",
                COVERED,
                f"length={first.get('length')} channels={first.get('channels')} "
                f"count={first.get('count')} rate={first.get('sample_rate')} "
                f"format={first.get('format')}",
            )
        )
        width = _BYTES_PER_SAMPLE.get(int(first.get("format", -1)), 0)
        payload_len = int(rx_headers[0].get("payload_len", 0))
        if width:
            samples_bytes = payload_len - HEADER_SIZE
            expected = int(first.get("length", -1)) * width
            verdict = "match" if samples_bytes == expected else "DIFFERS"
            items.append(
                CoverageItem(
                    "length_semantics",
                    COVERED,
                    f"payload={samples_bytes} length*bps={expected} {verdict}",
                )
            )
        else:
            items.append(
                CoverageItem(
                    "length_semantics",
                    COVERED,
                    f"unknown format {first.get('format')}; "
                    f"payload={payload_len - HEADER_SIZE}",
                )
            )
    else:
        items.append(CoverageItem("rx_header", OPEN, "no s2c RX audio header seen"))
        items.append(
            CoverageItem(
                "length_semantics",
                OPEN,
                "no s2c RX audio header to check length against",
            )
        )

    trx = _first_value(c2s_texts, "trx:0,true")
    if trx is not None:
        items.append(CoverageItem("trx_set", COVERED, f"c2s trx:0,true{trx}"))
    else:
        items.append(CoverageItem("trx_set", OPEN, "no c2s trx:0,true seen"))

    if _binaries(rows, "s2c", 3):
        items.append(CoverageItem("tx_chrono", COVERED, "s2c TX_CHRONO seen"))
    else:
        items.append(CoverageItem("tx_chrono", OPEN, "no s2c TX_CHRONO seen"))

    # Design 8.3: the server sends TX_CHRONO (type 3) while the client
    # answers with TX_AUDIO (type 2), so the TX header comes from c2s.
    tx_headers = _binaries(rows, "c2s", 2)
    if tx_headers:
        first = tx_headers[0]["bin"]
        assert isinstance(first, dict)
        items.append(
            CoverageItem(
                "tx_header",
                COVERED,
                f"length={first.get('length')} channels={first.get('channels')} "
                f"count={first.get('count')} rate={first.get('sample_rate')} "
                f"format={first.get('format')}",
            )
        )
    else:
        items.append(CoverageItem("tx_header", OPEN, "no c2s TX header seen"))

    return CoverageReport(
        items=tuple(items), complete=all(i.status == COVERED for i in items)
    )


def format_report(report: CoverageReport) -> str:
    """Human-readable coverage verdict, one line per item plus a summary."""
    lines = [f"{item.status:7} {item.id:16} {item.evidence}" for item in report.items]
    covered = sum(1 for item in report.items if item.status == COVERED)
    total = len(report.items)
    if report.complete:
        lines.append(f"COVERAGE: COMPLETE ({covered}/{total})")
    else:
        missing = ", ".join(item.id for item in report.items if item.status == OPEN)
        lines.append(f"COVERAGE: INCOMPLETE ({covered}/{total}) - OPEN: {missing}")
    return "\n".join(lines) + "\n"


def load_capture(path: str | Path) -> list[dict[str, object]]:
    """Read one JSON-lines capture file into record dicts."""
    records: list[dict[str, object]] = []
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                loaded = json.loads(line)
                if isinstance(loaded, dict):
                    records.append(loaded)
    return records


def extract_fixtures(records: Sequence[dict[str, object]], dest: str | Path) -> int:
    """Write only client-to-server text lines to a hand-reviewable file.

    Server output is never included: the design forbids using another
    program's output as a test expectation. Binary frames carry no text.
    Returns the number of lines written.
    """
    lines = [
        str(record["text"])
        for record in records
        if record.get("dir") == "c2s"
        and record.get("opcode") == WS_OP_TEXT
        and isinstance(record.get("text"), str)
    ]
    target = Path(dest)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return len(lines)


def _parse_host_port(value: str) -> tuple[str, int]:
    host, separator, port_text = value.rpartition(":")
    if not separator or not host:
        raise ValueError(f"expected HOST:PORT, got {value!r}")
    return host, int(port_text)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: capture, report, or extract. Returns an exit code."""
    parser = argparse.ArgumentParser(
        description="Transparent TCI WebSocket recording proxy (MOR-3821 C0)"
    )
    parser.add_argument("--listen", default="127.0.0.1:50002")
    parser.add_argument("--upstream", default="127.0.0.1:50001")
    parser.add_argument("--out", default=None)
    parser.add_argument("--label", default="session")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--report", default=None, metavar="CAPTURE.jsonl")
    parser.add_argument("--extract-fixtures", default=None, metavar="CAPTURE.jsonl")
    parser.add_argument("--fixtures-out", default=None)
    args = parser.parse_args(argv)

    def _read_capture(path: str) -> list[dict[str, object]] | None:
        try:
            return load_capture(path)
        except (OSError, ValueError) as exc:
            print(f"error: cannot read capture {path}: {exc}", file=sys.stderr)
            return None

    if args.report is not None:
        records = _read_capture(args.report)
        if records is None:
            return 2
        print(format_report(coverage_report(records)), end="")
        return 0

    if args.extract_fixtures is not None:
        if args.fixtures_out is None:
            parser.error("--extract-fixtures needs --fixtures-out")
        records = _read_capture(args.extract_fixtures)
        if records is None:
            return 2
        count = extract_fixtures(records, args.fixtures_out)
        print(f"wrote {count} client-to-server lines to {args.fixtures_out}")
        return 0

    out = args.out if args.out is not None else tempfile.mkdtemp(prefix="tci-capture-")
    try:
        resolved_out = check_out_dir(out)
    except OutDirRefused as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    try:
        listen_host, listen_port = _parse_host_port(args.listen)
        upstream_host, upstream_port = _parse_host_port(args.upstream)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    proxy = Proxy(
        listen_host,
        listen_port,
        upstream_host,
        upstream_port,
        out_dir=resolved_out,
        label=args.label,
        max_bytes=args.max_bytes,
    )

    async def _run() -> None:
        port = await proxy.start()
        print(
            f"recording {listen_host}:{port} -> "
            f"{upstream_host}:{upstream_port} in {resolved_out}",
            flush=True,
        )
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            pass
        finally:
            await proxy.stop()

    try:
        asyncio.run(_run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
