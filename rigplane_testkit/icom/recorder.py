"""Icom LAN session recorder: a redacting UDP proxy that logs JSONL.

The recorder sits between a client (e.g. the production ``IcomRadio``) and a
radio (real or :class:`~rigplane_testkit.icom.MockIcomRadio`).  The client
connects to the recorder's control listener; the radio-advertised CI-V and
audio ports in the status packet are rewritten to the recorder's own
listeners, so every datagram on all three ports passes through.

Every datagram is appended as one JSON line::

    {"t": 1.234, "dir": "c2r", "port": "control", "hex": "..."}

``t`` is seconds (``time.monotonic``) since :meth:`IcomSessionRecorder.start`.
Credentials never reach disk: the login packet's username/password/computer
fields, the conninfo username field and the session-token slot (0x1C) of
every control packet sized 0x40/0x50/0x60/0x90 — in both directions — are
zeroed before writing, and the record carries ``"redacted": ["login"]`` /
``["token"]`` markers naming what was masked.  The bytes forwarded on the
wire are never altered by redaction (only the status port rewrite touches
forwarded traffic, and the original radio packet is what gets recorded).

Packet layout references (production code, not duplicated logic):
``src/rigplane/core/auth.py`` ``build_login_packet`` / ``build_conninfo_packet``
/ ``parse_status_response``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import struct
import time
from pathlib import Path
from typing import IO

logger = logging.getLogger(__name__)

# Packet sizes (mirrors simulator.py / runtime._control_phase constants).
_LOGIN_SIZE = 0x80  # client → radio login request
_LOGIN_RESPONSE_SIZE = 0x60  # radio → client login response (carries token)
_TOKEN_SIZE = 0x40  # token ack / renew / remove
_CONNINFO_SIZE = 0x90  # client → radio stream request (carries token + username)
_STATUS_SIZE = 0x50  # radio → client status (carries advertised ports)
# Control packets whose 0x1C slot is the session token, masked in both
# directions (see redact_datagram).
_TOKEN_PACKET_SIZES = (
    _TOKEN_SIZE,
    _STATUS_SIZE,
    _LOGIN_RESPONSE_SIZE,
    _CONNINFO_SIZE,
)

# Field offsets (see src/rigplane/core/auth.py).
_STATUS_CIV_PORT_OFF = 0x42  # big-endian u16
_STATUS_AUDIO_PORT_OFF = 0x46  # big-endian u16
_TOKEN_FIELD = slice(0x1C, 0x20)  # little-endian u32 session token
# Login: encoded username 0x40-0x50, encoded password 0x50-0x60,
# computer name 0x60-0x70 — zero the whole span.
_LOGIN_CREDENTIALS = slice(0x40, 0x70)
# Conninfo carries the encoded username again at 0x60-0x70.
_CONNINFO_USERNAME = slice(0x60, 0x70)


def redact_datagram(port: str, direction: str, data: bytes) -> tuple[bytes, list[str]]:
    """Return ``(safe_bytes, labels)`` with credential/token fields zeroed.

    Pure function over one datagram; the caller writes ``safe_bytes`` to disk
    and forwards the original.  ``labels`` names what was masked:
    ``"login"`` for credential fields, ``"token"`` for session-token fields.
    """
    pkt = bytearray(data)
    labels: list[str] = []
    if port != "control":
        return bytes(pkt), labels
    # The session token sits at 0x1C in the 0x40/0x60/0x90 control packets
    # (protocol.md § Token Ack / § Auth Response / § Conninfo Packet — sent
    # by radio and client; see build_conninfo_packet / parse_auth_response
    # in src/rigplane/core/auth.py).  The 0x50 status packet shares the
    # header layout and probe captures carried live token bytes there, so
    # the slot is masked in both directions by size alone.
    if len(data) in _TOKEN_PACKET_SIZES:
        pkt[_TOKEN_FIELD] = bytes(4)
        labels.append("token")
    # Credentials ride only in client-originated request packets: the login
    # username/password/computer span and the conninfo username copy.
    if direction == "c2r" and len(data) >= 0x20 and data[0x14] == 0x01:
        if len(data) == _LOGIN_SIZE and data[0x15] == 0x00:
            pkt[_LOGIN_CREDENTIALS] = bytes(0x30)
            labels.append("login")
        elif len(data) == _CONNINFO_SIZE and data[0x15] == 0x03:
            pkt[_CONNINFO_USERNAME] = bytes(0x10)
            labels.append("login")
    return bytes(pkt), labels


class _Channel:
    """One proxied UDP port: a client-facing listener plus an upstream socket."""

    def __init__(self, recorder: IcomSessionRecorder, name: str) -> None:
        self.recorder = recorder
        self.name = name
        self.listener: asyncio.DatagramTransport | None = None
        self.upstream: asyncio.DatagramTransport | None = None
        self._upstream_task: asyncio.Task[None] | None = None
        self._upstream_port = 0  # radio-side port the upstream connects to
        self.client_addr: tuple[str, int] | None = None
        self.dropped = 0  # packets arrived before the upstream was known

    @property
    def port(self) -> int:
        """Bound client-facing port (0 while unbound)."""
        if self.listener is None:
            return 0
        sockname = self.listener.get_extra_info("sockname")
        return int(sockname[1]) if sockname else 0

    async def open_listener(self, host: str) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DownstreamProtocol(self), local_addr=(host, 0)
        )
        self.listener = transport

    def open_upstream(self, host: str, port: int) -> None:
        """Open the upstream endpoint once; the first advertisement wins.

        A second status packet while the connect is in flight cannot start
        another one; once open, a different advertised port is ignored and
        logged (the radio fixes its CI-V/audio ports for the session).
        """
        if self._upstream_task is not None and not self._upstream_task.done():
            return
        if self.upstream is not None:
            if port != self._upstream_port:
                logger.info(
                    "%s: status advertised port %d; upstream already "
                    "connected to %d, ignoring",
                    self.name,
                    port,
                    self._upstream_port,
                )
            return
        self._upstream_task = asyncio.get_running_loop().create_task(
            self._open_upstream(host, port)
        )

    async def _open_upstream(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UpstreamProtocol(self), remote_addr=(host, port)
        )
        self.upstream = transport
        self._upstream_port = port

    def close(self) -> None:
        for transport in (self.listener, self.upstream):
            if transport is not None and not transport.is_closing():
                transport.close()
        self.listener = None
        self.upstream = None


class _DownstreamProtocol(asyncio.DatagramProtocol):
    """Receives client datagrams on a listener and forwards them upstream."""

    def __init__(self, channel: _Channel) -> None:
        self._channel = channel

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        ch = self._channel
        ch.client_addr = addr
        ch.recorder._record("c2r", ch.name, data)
        if ch.upstream is not None and not ch.upstream.is_closing():
            ch.upstream.sendto(data)
        else:
            ch.dropped += 1


class _UpstreamProtocol(asyncio.DatagramProtocol):
    """Receives radio datagrams upstream and forwards them to the client."""

    def __init__(self, channel: _Channel) -> None:
        self._channel = channel

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        ch = self._channel
        # Record exactly what the radio sent, then rewrite if needed.
        ch.recorder._record("r2c", ch.name, data)
        out = ch.recorder._rewrite_outbound(ch.name, data)
        if (
            ch.client_addr is not None
            and ch.listener is not None
            and not ch.listener.is_closing()
        ):
            ch.listener.sendto(out, ch.client_addr)


class IcomSessionRecorder:
    """UDP proxy that records an Icom LAN session to a JSONL file.

    Args:
        radio_host: Radio (or simulator) host.
        radio_port: Radio control port (50001 on real hardware).
        out_path: Destination JSONL file (truncated on :meth:`start`).
        listen_host: Bind address for the client-facing listeners.
    """

    def __init__(
        self,
        radio_host: str,
        radio_port: int,
        out_path: str | Path,
        *,
        listen_host: str = "127.0.0.1",
    ) -> None:
        self._radio_host = radio_host
        self._radio_port = radio_port
        self._out_path = Path(out_path)
        self._listen_host = listen_host
        self._channels = {
            name: _Channel(self, name) for name in ("control", "civ", "audio")
        }
        self._file: IO[str] | None = None
        self._t0 = 0.0
        self.records_written = 0

    async def start(self) -> None:
        """Bind the listeners, connect the control upstream, open the log."""
        self._t0 = time.monotonic()
        self._file = self._out_path.open("w", encoding="utf-8")
        for ch in self._channels.values():
            await ch.open_listener(self._listen_host)
        await self._channels["control"]._open_upstream(
            self._radio_host, self._radio_port
        )
        logger.info(
            "recording %s:%d -> %s (control=%d civ=%d audio=%d)",
            self._radio_host,
            self._radio_port,
            self._out_path,
            self.control_port,
            self.civ_port,
            self.audio_port,
        )

    async def stop(self) -> None:
        """Close all sockets and flush/close the JSONL file."""
        for ch in self._channels.values():
            ch.close()
        if self._file is not None:
            self._file.close()
            self._file = None

    async def serve_forever(self) -> None:
        """Run until cancelled (CLI use)."""
        await asyncio.Event().wait()

    @property
    def control_port(self) -> int:
        """Client-facing control port the client should connect to."""
        return self._channels["control"].port

    @property
    def civ_port(self) -> int:
        """Client-facing CI-V port advertised via the status rewrite."""
        return self._channels["civ"].port

    @property
    def audio_port(self) -> int:
        """Client-facing audio port advertised via the status rewrite."""
        return self._channels["audio"].port

    def _record(self, direction: str, port: str, data: bytes) -> None:
        safe, labels = redact_datagram(port, direction, data)
        record: dict[str, object] = {
            "t": round(time.monotonic() - self._t0, 6),
            "dir": direction,
            "port": port,
            "hex": safe.hex(),
        }
        if labels:
            record["redacted"] = labels
        if self._file is not None:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()
            self.records_written += 1

    def _rewrite_outbound(self, port: str, data: bytes) -> bytes:
        """Rewrite the radio-advertised ports in a status packet to ours.

        All other packets are forwarded unchanged.  The radio's own CI-V/audio
        ports learned here drive the (lazy) upstream channels.
        """
        if port != "control" or len(data) != _STATUS_SIZE:
            return data
        radio_civ = struct.unpack_from(">H", data, _STATUS_CIV_PORT_OFF)[0]
        radio_audio = struct.unpack_from(">H", data, _STATUS_AUDIO_PORT_OFF)[0]
        pkt = bytearray(data)
        civ_ch = self._channels["civ"]
        if radio_civ > 0:
            civ_ch.open_upstream(self._radio_host, radio_civ)
            struct.pack_into(">H", pkt, _STATUS_CIV_PORT_OFF, civ_ch.port)
        audio_ch = self._channels["audio"]
        if radio_audio > 0:
            audio_ch.open_upstream(self._radio_host, radio_audio)
            struct.pack_into(">H", pkt, _STATUS_AUDIO_PORT_OFF, audio_ch.port)
        return bytes(pkt)
