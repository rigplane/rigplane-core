"""Icom LAN session recorder: a redacting UDP proxy that logs JSONL.

The recorder sits between a client (e.g. the production ``IcomRadio``) and a
radio (real or :class:`~rigplane_testkit.icom.MockIcomRadio`).  The client
connects to the recorder's control listener; the radio-advertised CI-V and
audio ports in the status packet are rewritten to the recorder's own
listeners, and the client-declared CI-V/audio ports in its 0x90 conninfo
packet are rewritten to the recorder's pre-bound upstream sockets, so the
CI-V/audio datagrams a radio sends to those declared ports, and its replies
to the recorder's source ports, land on the upstream sockets and are
relayed to the client.

Every datagram is appended as one JSON line::

    {"t": 1.234, "dir": "c2r", "port": "control", "hex": "..."}

``t`` is seconds (``time.monotonic``) since :meth:`IcomSessionRecorder.start`.
Credentials never reach disk: the login packet's username/password/computer
fields, the conninfo username field, the conninfo device-name field
(0x40..0x60, user-configurable text on a real radio), the session-token
slot (0x1C) of every control packet sized 0x40/0x50/0x60/0x90 — in both
directions — and, in the radio-originated conninfo notice (request type
0x00), the connected client's computer name (0x64..0x74) and IPv4 address
(0x84..0x88), are zeroed before writing, and the conninfo GUID/MAC area
(0x20..0x30) of every 0x90 packet is replaced with a deterministic sha256
pseudonym (all-zero spans stay zero).  The record carries
``"redacted": ["login"]`` / ``["token"]`` / ``["guid"]`` / ``["name"]`` /
``["ip"]`` markers naming what was masked.  The bytes
forwarded on the wire are never altered by redaction; the only transforms
applied to forwarded traffic are the two port rewrites — the
radio-advertised ports in a status packet and the client-declared ports in
a conninfo packet — and each rewrite records the original bytes (the
radio's, resp. the client's) before rewriting them.

Packet layout references (production code, not duplicated logic):
``src/rigplane/core/auth.py`` ``build_login_packet`` / ``build_conninfo_packet``
/ ``parse_status_response``.
"""

from __future__ import annotations

import asyncio
import hashlib
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
# Conninfo GUID / MAC area (protocol.md § Conninfo Packet; the client must
# echo the radio's GUID there), pseudonymised before writing.
_GUID_AREA = slice(0x20, 0x30)
_GUID_SALT = b"rigplane-testkit-guid:"
# Conninfo device name (protocol.md § Conninfo Packet; build_conninfo_packet
# packs radio_name there as plain ASCII) — user-configurable text on a real
# radio, zeroed before writing in both directions.
_CONNINFO_DEVICE_NAME = slice(0x40, 0x60)
# Conninfo client-declared local ports (protocol.md § Conninfo Packet;
# build_conninfo_packet packs them big-endian u32).
_CONNINFO_CIV_PORT_OFF = 0x7C
_CONNINFO_AUDIO_PORT_OFF = 0x80
# Radio-originated conninfo notice (request type 0x00 at 0x15, r2c): the
# connected client's computer name 0x64..0x74 and IPv4 address 0x84..0x88
# (recorded IC-7610 layout, documented in simulator.py _conninfo_notice;
# protocol.md § Conninfo Packet documents the client-built layout only).
# The busy u32 at 0x60 is protocol state (read by runtime/_control_phase.py
# _on_conninfo_notice) and is not masked.
_NOTICE_CLIENT_NAME = slice(0x64, 0x74)
_NOTICE_CLIENT_IP = slice(0x84, 0x88)


def _guid_pseudonym(span: bytes) -> bytes:
    """Deterministic 16-byte pseudonym for a conninfo GUID/MAC span."""
    return hashlib.sha256(_GUID_SALT + span).digest()[:16]


def redact_datagram(port: str, direction: str, data: bytes) -> tuple[bytes, list[str]]:
    """Return ``(safe_bytes, labels)`` with credential/token fields zeroed.

    Pure function over one datagram; the caller writes ``safe_bytes`` to
    disk and forwards the original.  Masked spans, per packet kind and
    direction (offsets per protocol.md § Auth / § Conninfo Packet; the
    radio notice layout is the recorded IC-7610 structure, see
    simulator.py ``_conninfo_notice``):

    - any control packet sized 0x40/0x50/0x60/0x90, both directions:
      the session-token slot 0x1C..0x20 → ``"token"``;
    - client login (0x80, request type 0x00): username/password/computer
      span 0x40..0x70 → ``"login"``;
    - client conninfo (0x90, request type 0x03): the encoded username
      copy 0x60..0x70 → ``"login"``;
    - any 0x90, both directions: the GUID/MAC area 0x20..0x30 is
      replaced with a deterministic pseudonym (an all-zero span stays
      zero) → ``"guid"``; the device-name field 0x40..0x60 → ``"name"``;
    - radio conninfo notice (0x90, request type 0x00, r2c): the
      connected client's computer name 0x64..0x74 (under the ``"name"``
      label every 0x90 already carries) and IPv4 address 0x84..0x88 →
      ``"ip"`` when the span is nonzero; the busy flag 0x60..0x64 is
      protocol state and is preserved.

    All other bytes pass through untouched.
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
    # The conninfo GUID/MAC area identifies the radio (and is echoed back by
    # the client) in both directions; replace it with a deterministic
    # pseudonym so the echo still equals the radio's value in the recording.
    # All-zero spans (a not-yet-assigned slot) stay zero.
    if len(data) == _CONNINFO_SIZE:
        span = bytes(data[_GUID_AREA])
        if span != bytes(16):
            pkt[_GUID_AREA] = _guid_pseudonym(span)
            labels.append("guid")
        # The device-name field is user-configurable text on a real radio;
        # zero it in both directions.
        pkt[_CONNINFO_DEVICE_NAME] = bytes(0x20)
        labels.append("name")
    # The radio-originated conninfo notice (request type 0x00 at 0x15)
    # differs from 0x60 on from the client-built packet: after the busy
    # flag u32 at 0x60 it carries the connected client's computer name
    # at 0x64..0x74 and IPv4 address at 0x84..0x88.  The name span adds
    # no label of its own (every 0x90 already carries "name" for the
    # device-name field above); "ip" is added only when the span was
    # nonzero, so an all-zero power-off notice gains no new labels.
    if direction == "r2c" and len(data) == _CONNINFO_SIZE and data[0x15] == 0x00:
        pkt[_NOTICE_CLIENT_NAME] = bytes(0x10)
        if data[_NOTICE_CLIENT_IP] != bytes(4):
            pkt[_NOTICE_CLIENT_IP] = bytes(4)
            labels.append("ip")
    return bytes(pkt), labels


class _Channel:
    """One proxied UDP port: a client-facing listener plus an upstream socket."""

    def __init__(self, recorder: IcomSessionRecorder, name: str) -> None:
        self.recorder = recorder
        self.name = name
        self.listener: asyncio.DatagramTransport | None = None
        self.upstream: asyncio.DatagramTransport | None = None
        # Radio-side address packets are sent to; None until learned
        # (the control channel learns it from the constructor's radio
        # address, CI-V/audio from the status packet's advertised ports).
        self.upstream_addr: tuple[str, int] | None = None
        self.client_addr: tuple[str, int] | None = None
        self.dropped = 0  # packets arrived before the upstream was known
        self.send_errors = 0  # upstream sends that raised (error_received)

    @property
    def port(self) -> int:
        """Bound client-facing port (0 while unbound)."""
        if self.listener is None:
            return 0
        sockname = self.listener.get_extra_info("sockname")
        return int(sockname[1]) if sockname else 0

    @property
    def upstream_port(self) -> int:
        """Bound local port of the upstream socket (0 while unbound).

        A radio that sends CI-V/audio datagrams to the client-declared
        conninfo ports (instead of replying to the source port) reaches
        this socket, so the conninfo rewrite declares this port.
        """
        if self.upstream is None:
            return 0
        sockname = self.upstream.get_extra_info("sockname")
        return int(sockname[1]) if sockname else 0

    async def open_listener(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _DownstreamProtocol(self), local_addr=(host, port)
        )
        self.listener = transport

    async def open_upstream(self, host: str, local_port: int) -> None:
        """Bind the upstream socket (unconnected) once; a second call is a
        no-op.  The radio-side address is remembered separately via
        :meth:`set_upstream_addr`, and packets are sent there with
        ``sendto``, so datagrams aimed at the socket's bound port still
        arrive whatever address the radio targets."""
        if self.upstream is not None:
            return
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: _UpstreamProtocol(self), local_addr=(host, local_port)
        )
        self.upstream = transport

    def set_upstream_addr(self, host: str, port: int) -> None:
        """Remember the radio-side address; the first advertisement wins.

        A different advertised port on an already-learned channel is
        ignored and logged (the radio fixes its CI-V/audio ports for the
        session).
        """
        if self.upstream_addr is not None:
            if port != self.upstream_addr[1]:
                logger.info(
                    "%s: status advertised port %d; upstream already "
                    "sending to %d, ignoring",
                    self.name,
                    port,
                    self.upstream_addr[1],
                )
            return
        self.upstream_addr = (host, port)

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
        # Record exactly what the client sent, then rewrite if needed.
        ch.recorder._record("c2r", ch.name, data)
        out = ch.recorder._rewrite_inbound(ch.name, data)
        if (
            ch.upstream is not None
            and not ch.upstream.is_closing()
            and ch.upstream_addr is not None
        ):
            ch.upstream.sendto(out, ch.upstream_addr)
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

    def error_received(self, exc: Exception) -> None:
        """Count and log a failed upstream send instead of staying silent."""
        ch = self._channel
        ch.send_errors += 1
        logger.warning("%s: upstream send failed: %s", ch.name, exc)


class IcomSessionRecorder:
    """UDP proxy that records an Icom LAN session to a JSONL file.

    Args:
        radio_host: Radio (or simulator) host.
        radio_port: Radio control port (50001 on real hardware).
        out_path: Destination JSONL file (truncated on :meth:`start`).
        listen_host: Bind address for the client-facing listeners.
        listen_port: Fixed client-facing control port (0 = ephemeral).  When
            nonzero, the CI-V and audio listeners bind ``listen_port + 1``
            and ``listen_port + 2``.
        upstream_port: Fixed local port of the CI-V upstream socket
            (0 = ephemeral, still bound at :meth:`start`).  When nonzero,
            the audio upstream binds ``upstream_port + 1``.  The
            client-declared conninfo ports are rewritten to these bound
            ports, so radio datagrams aimed at them still pass through the
            recorder.
        upstream_host: Bind address for the radio-facing upstream sockets
            (default ``0.0.0.0``, the wildcard).  Never tie them to
            ``listen_host``: a loopback listener bind cannot source packets
            to a radio on another host, and every send would fail with
            ``OSError``.
    """

    def __init__(
        self,
        radio_host: str,
        radio_port: int,
        out_path: str | Path,
        *,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        upstream_port: int = 0,
        upstream_host: str = "0.0.0.0",
    ) -> None:
        self._radio_host = radio_host
        self._radio_port = radio_port
        self._out_path = Path(out_path)
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._upstream_port = upstream_port
        self._upstream_host = upstream_host
        self._channels = {
            name: _Channel(self, name) for name in ("control", "civ", "audio")
        }
        self._file: IO[str] | None = None
        self._t0 = 0.0
        self.records_written = 0

    async def start(self) -> None:
        """Bind the listeners and upstream sockets, open the log."""
        self._t0 = time.monotonic()
        self._file = self._out_path.open("w", encoding="utf-8")
        for name, ch in self._channels.items():
            listener_port = {
                "control": self._listen_port,
                "civ": self._listen_port + 1 if self._listen_port else 0,
                "audio": self._listen_port + 2 if self._listen_port else 0,
            }[name]
            upstream_local = {
                "control": 0,
                "civ": self._upstream_port,
                "audio": self._upstream_port + 1 if self._upstream_port else 0,
            }[name]
            await ch.open_listener(self._listen_host, listener_port)
            await ch.open_upstream(self._upstream_host, upstream_local)
        # The control upstream target is the radio itself; CI-V and audio
        # upstreams learn their radio-side addresses from the status packet.
        self._channels["control"].set_upstream_addr(self._radio_host, self._radio_port)
        logger.info(
            "recording %s:%d -> %s (control=%d civ=%d audio=%d; "
            "upstream civ=%d audio=%d)",
            self._radio_host,
            self._radio_port,
            self._out_path,
            self.control_port,
            self.civ_port,
            self.audio_port,
            self._channels["civ"].upstream_port,
            self._channels["audio"].upstream_port,
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

    def _rewrite_inbound(self, port: str, data: bytes) -> bytes:
        """Rewrite the client-declared ports in a conninfo packet to ours.

        A real radio may send CI-V/audio datagrams to the local ports the
        client declared at 0x7C/0x80 instead of replying to the source port,
        so the forwarded copy must declare the recorder's pre-bound upstream
        ports.  All other packets are forwarded unchanged, and the recorded
        copy keeps the client's original bytes.
        """
        if port != "control" or len(data) != _CONNINFO_SIZE:
            return data
        if not (data[0x14] == 0x01 and data[0x15] == 0x03):
            return data
        pkt = bytearray(data)
        struct.pack_into(
            ">I", pkt, _CONNINFO_CIV_PORT_OFF, self._channels["civ"].upstream_port
        )
        struct.pack_into(
            ">I",
            pkt,
            _CONNINFO_AUDIO_PORT_OFF,
            self._channels["audio"].upstream_port,
        )
        return bytes(pkt)

    def _rewrite_outbound(self, port: str, data: bytes) -> bytes:
        """Rewrite the radio-advertised ports in a status packet to ours.

        All other packets are forwarded unchanged.  The radio's own
        CI-V/audio ports learned here only set the upstream sockets' send
        targets; the sockets themselves are bound once in
        :meth:`IcomSessionRecorder.start`.
        """
        if port != "control" or len(data) != _STATUS_SIZE:
            return data
        radio_civ = struct.unpack_from(">H", data, _STATUS_CIV_PORT_OFF)[0]
        radio_audio = struct.unpack_from(">H", data, _STATUS_AUDIO_PORT_OFF)[0]
        pkt = bytearray(data)
        civ_ch = self._channels["civ"]
        if radio_civ > 0:
            civ_ch.set_upstream_addr(self._radio_host, radio_civ)
            struct.pack_into(">H", pkt, _STATUS_CIV_PORT_OFF, civ_ch.port)
        audio_ch = self._channels["audio"]
        if radio_audio > 0:
            audio_ch.set_upstream_addr(self._radio_host, radio_audio)
            struct.pack_into(">H", pkt, _STATUS_AUDIO_PORT_OFF, audio_ch.port)
        return bytes(pkt)
