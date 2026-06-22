"""Mock UDP radio server for integration testing.

Emulates an Icom IC-7610 over UDP so that transport/radio/CLI tests can run
without real hardware.  Two asyncio datagram servers are started:

  * control port  – authentication handshake, pings, token renewal
  * CI-V port     – CI-V command / response exchange

Usage::

    server = MockIcomRadio()
    await server.start()
    # ... connect IcomRadio to server.control_port ...
    await server.stop()
"""

from __future__ import annotations

import asyncio
import logging
import random
import struct
import time

logger = logging.getLogger(__name__)

# Error codes carried in the status / login response at offset 0x30 (little-endian).
_ERR_OK = 0x00000000
_ERR_AUTH_FAIL = 0xFEFFFFFF  # credential failure (login)
_ERR_PREV_SESSION_ACTIVE = 0xFFFFFFFF  # busy / previous session still held

# Token request-type magics carried at pkt[0x15] of a 0x40-byte control packet.
_TOKEN_REMOVE = 0x01  # graceful withdraw — frees the held session immediately
_TOKEN_ACK = 0x02  # initial token acknowledgement
_TOKEN_RENEW = 0x05  # keepalive renewal

# OpenClose magics carried at pkt[0x15] of a 0x16-byte CI-V packet.
_OPENCLOSE_CLOSE = 0x00  # graceful data-port close — frees the held session
_OPENCLOSE_OPEN = 0x04

# Default accelerated keepalive hold (D7). Real radios hold ~40-60 s; the fast
# suite uses ~0.5 s so a dropped session frees quickly. A near-real value
# (~45-60 s) can be passed for the one slow timing test.
_DEFAULT_KEEPALIVE_HOLD_S = 0.5

# ---------------------------------------------------------------------------
# Protocol constants (duplicated here to keep mock self-contained)
# ---------------------------------------------------------------------------

_HEADER_SIZE = 0x10  # 16 bytes: len(4) + type(2) + seq(2) + sentid(4) + rcvdid(4)
_PING_SIZE = 0x15  # 21 bytes
_CIV_HEADER_SIZE = 0x15  # 21 bytes before CI-V frame in DATA packets

# Packet type codes
_PT_DATA = 0x00
_PT_CONTROL = 0x01
_PT_ARE_YOU_THERE = 0x03
_PT_I_AM_HERE = 0x04
_PT_DISCONNECT = 0x05
_PT_ARE_YOU_READY = 0x06
_PT_PING = 0x07

# CI-V addresses
_RADIO_DEFAULT_ADDR = 0x98
_CONTROLLER_ADDR = 0xE0

# CI-V command codes
_CIV_PREAMBLE = b"\xfe\xfe"
_CIV_TERM = b"\xfd"
_CMD_FREQ_GET = 0x03
_CMD_FREQ_SET = 0x05
_CMD_MODE_GET = 0x04
_CMD_MODE_SET = 0x06
_CMD_LEVEL = 0x14
_CMD_METER = 0x15
_CMD_ATT = 0x11
_CMD_PREAMP = 0x16
_CMD_CMD29 = 0x29
_CMD_ACK = 0xFB
_CMD_NAK = 0xFA
_SUB_RF_POWER = 0x0A
_SUB_S_METER = 0x02
_SUB_SWR_METER = 0x12
_SUB_ALC_METER = 0x13
_SUB_PREAMP_STATUS = 0x02
_SUB_DIGISEL_STATUS = 0x4E


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bcd_byte(value: int) -> int:
    """Encode 0-99 integer to one BCD byte (e.g. 18 → 0x18)."""
    return ((value // 10) << 4) | (value % 10)


def _level_bcd_encode(value: int) -> bytes:
    """Encode 0-9999 level to 2-byte BCD (e.g. 128 → b'\\x01\\x28')."""
    d = f"{value:04d}"
    return bytes([(int(d[0]) << 4) | int(d[1]), (int(d[2]) << 4) | int(d[3])])


def _bcd_encode_freq(freq_hz: int) -> bytes:
    """Encode frequency in Hz to Icom 5-byte BCD (little-endian)."""
    digits = f"{freq_hz:010d}"
    result = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])
        high = int(digits[9 - 2 * i - 1])
        result[i] = (high << 4) | low
    return bytes(result)


def _bcd_decode_freq(data: bytes) -> int:
    """Decode Icom 5-byte BCD frequency to Hz."""
    freq = 0
    for i in range(len(data)):
        high = (data[i] >> 4) & 0x0F
        low = data[i] & 0x0F
        freq += low * (10 ** (2 * i)) + high * (10 ** (2 * i + 1))
    return freq


# ---------------------------------------------------------------------------
# asyncio DatagramProtocol
# ---------------------------------------------------------------------------


class _MockProtocol(asyncio.DatagramProtocol):
    """Minimal datagram protocol that routes packets to MockIcomRadio."""

    def __init__(self, owner: "MockIcomRadio", label: str) -> None:
        self._owner = owner
        self._label = label
        self._transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self._transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            self._owner._on_packet(data, addr, self._label, self)
        except Exception:
            logger.exception("Mock %s: unhandled error", self._label)

    def error_received(self, exc: Exception) -> None:
        logger.debug("Mock %s UDP error: %s", self._label, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        pass

    def send(self, data: bytes, addr: tuple[str, int]) -> None:
        if self._transport is not None and not self._transport.is_closing():
            self._transport.sendto(data, addr)


# ---------------------------------------------------------------------------
# MockIcomRadio
# ---------------------------------------------------------------------------


class MockIcomRadio:
    """Asyncio UDP server emulating an IC-7610 for integration testing.

    Handles the full connection lifecycle on two ports:

    * **Control port** – discovery (AYT/IAH), login, token exchange, pings
    * **CI-V port**    – discovery, open/close, CI-V commands, pings

    After calling :meth:`start`, connect an :class:`~rigplane.radio.IcomRadio`
    to ``host:control_port`` and it will go through the full handshake.

    Args:
        host: Bind address for both servers.
        port: Control port hint (0 = OS-assigned).
        civ_port: CI-V port hint (0 = OS-assigned).
        username: Expected username (any value accepted when ``auth_fail`` is False).
        password: Expected password (unused; see ``auth_fail``).
        radio_addr: CI-V address the mock claims to be (default 0x98 = IC-7610).
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        civ_port: int = 0,
        username: str = "testuser",
        password: str = "testpass",
        radio_addr: int = _RADIO_DEFAULT_ADDR,
        *,
        single_owner: bool = False,
        keepalive_hold_s: float = _DEFAULT_KEEPALIVE_HOLD_S,
        drop_rate: float = 0.0,
        reorder_window: int = 0,
        unsolicited_civ: bool = False,
        unsolicited_interval_s: float = 0.1,
    ) -> None:
        self._host = host
        self._ctrl_port_hint = port
        self._civ_port_hint = civ_port
        self._username = username
        self._password = password
        self._radio_addr = radio_addr

        # UDP transports (set after start())
        self._ctrl_udp: asyncio.DatagramTransport | None = None
        self._civ_udp: asyncio.DatagramTransport | None = None

        # Actual bound ports
        self._actual_ctrl_port: int = 0
        self._actual_civ_port: int = 0

        # Connection state (learned during handshake)
        self.radio_id: int = 0xDEADBEEF
        self.token: int = 0x12345678
        self._ctrl_client_id: int = 0
        self._civ_client_id: int = 0
        self._ctrl_seq: int = 1
        self._civ_seq: int = 1

        # Radio state (for CI-V responses)
        self._frequency: int = 14_074_000
        self._mode: int = 0x01  # USB
        self._filter: int = 1
        self._power: int = 100  # 0-255 level
        self._s_meter: int = 120  # 0-255
        self._swr: int = 10  # 0-255
        self._alc: int = 0
        self._attenuator: int = 0  # dB (0, 3, 6, ... 45)
        self._preamp: int = 0  # 0=off, 1=PREAMP1, 2=PREAMP2
        self._digisel: int = 0  # 0=off, 1=on

        # Behaviour flags for edge-case tests
        self.auth_fail: bool = False
        self.response_delay: float = 0.0  # extra sleep before sending a response

        # ----------------------------------------------------------------
        # Session-lifecycle modelling (additive; all opt-in)
        # ----------------------------------------------------------------
        # When ``single_owner`` is True the radio behaves like a real IC-7610:
        # it tracks ONE owning session keyed by (remote_addr, control my_id).
        # A conninfo from a different owner while a session is held is rejected
        # with civ_port=0 + error=0xFFFFFFFF (previous-session-active). The held
        # session is freed immediately on token-remove or OpenClose(close), and
        # otherwise auto-released after ``keepalive_hold_s`` of silence.
        self.single_owner: bool = single_owner
        self.keepalive_hold_s: float = keepalive_hold_s
        # Owner identity = (remote_addr, my_id); None when no session is held.
        self._owner: tuple[tuple[str, int], int] | None = None
        self._keepalive_timer: asyncio.TimerHandle | None = None
        # Force civ_port=0 (not-ready, NOT a busy reject) until this monotonic
        # deadline — drives CONNECTING -> COOLDOWN -> CONNECTING without a
        # 0xFFFFFFFF reject. 0.0 means "always available".
        self._civ_unavailable_until: float = 0.0

        # Fault injection
        self.drop_rate: float = drop_rate  # probability [0,1] of dropping a reply
        self.reorder_window: int = reorder_window  # buffer N replies then flush
        self._stall_until: float = 0.0  # suppress unsolicited CI-V until this time
        self._reorder_buf: dict[
            str, list[tuple[_MockProtocol, bytes, tuple[str, int]]]
        ] = {
            "ctrl": [],
            "civ": [],
        }
        self._rng = random.Random(0xC0FFEE)

        # Autonomous state + unsolicited CI-V streaming
        self.unsolicited_civ: bool = unsolicited_civ
        self.unsolicited_interval_s: float = unsolicited_interval_s
        self._unsolicited_task: asyncio.Task[None] | None = None
        # Last CI-V client (set on OpenClose(open)) — target for unsolicited frames.
        self._civ_owner_addr: tuple[str, int] | None = None
        self._civ_owner_id: int = 0
        self.unsolicited_sent: int = 0  # count of autonomous frames emitted
        # Counters / observability for simulator-fidelity tests
        self.busy_rejects: int = 0  # number of 0xFFFFFFFF rejects sent
        self.released_count: int = 0  # number of sessions freed (any path)
        self.last_release_reason: str | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Bind both UDP servers and make the ports available."""
        loop = asyncio.get_running_loop()

        ctrl_transport, ctrl_proto = await loop.create_datagram_endpoint(
            lambda: _MockProtocol(self, "ctrl"),
            local_addr=(self._host, self._ctrl_port_hint),
        )
        self._ctrl_udp = ctrl_transport
        sockname = ctrl_transport.get_extra_info("sockname")
        self._actual_ctrl_port = sockname[1]

        civ_transport, civ_proto = await loop.create_datagram_endpoint(
            lambda: _MockProtocol(self, "civ"),
            local_addr=(self._host, self._civ_port_hint),
        )
        self._civ_udp = civ_transport
        sockname = civ_transport.get_extra_info("sockname")
        self._actual_civ_port = sockname[1]

        if self.unsolicited_civ:
            self._unsolicited_task = asyncio.get_running_loop().create_task(
                self._unsolicited_loop()
            )

        logger.debug(
            "MockIcomRadio started — ctrl=%d civ=%d",
            self._actual_ctrl_port,
            self._actual_civ_port,
        )

    async def stop(self) -> None:
        """Close both UDP servers and tear down background tasks/timers."""
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None
        if self._unsolicited_task is not None:
            self._unsolicited_task.cancel()
            try:
                await self._unsolicited_task
            except (asyncio.CancelledError, Exception):
                pass
            self._unsolicited_task = None
        if self._ctrl_udp is not None:
            self._ctrl_udp.close()
            self._ctrl_udp = None
        if self._civ_udp is not None:
            self._civ_udp.close()
            self._civ_udp = None
        logger.debug("MockIcomRadio stopped")

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def control_port(self) -> int:
        """Actual bound control port number."""
        return self._actual_ctrl_port

    @property
    def civ_port(self) -> int:
        """Actual bound CI-V port number."""
        return self._actual_civ_port

    @property
    def session_held(self) -> bool:
        """True when a session is currently owned (single-owner mode)."""
        return self._owner is not None

    @property
    def owner(self) -> tuple[tuple[str, int], int] | None:
        """Current owner identity ``((host, port), my_id)`` or ``None``."""
        return self._owner

    # ------------------------------------------------------------------
    # Session-lifecycle test knobs
    # ------------------------------------------------------------------

    def force_civ_unavailable_for(self, seconds: float) -> None:
        """Force ``civ_port=0`` (not-ready, NOT a busy reject) for a window.

        This drives the client's CONNECTING -> COOLDOWN -> CONNECTING path
        without emitting a ``0xFFFFFFFF`` previous-session-active reject. After
        the window the next conninfo gets a real CI-V port.
        """
        self._civ_unavailable_until = time.monotonic() + max(0.0, seconds)

    def clear_civ_unavailable(self) -> None:
        """Immediately make the CI-V port available again."""
        self._civ_unavailable_until = 0.0

    def stall_for(self, seconds: float) -> None:
        """Stop emitting unsolicited CI-V for ``seconds`` to trip the watchdog."""
        self._stall_until = time.monotonic() + max(0.0, seconds)

    def free_session(self, reason: str = "manual") -> None:
        """Release the held session immediately (test helper)."""
        self._release_session(reason)

    # ------------------------------------------------------------------
    # Session ownership (single-owner model)
    # ------------------------------------------------------------------

    def _release_session(self, reason: str) -> None:
        """Free the held session and cancel its keepalive timer."""
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
            self._keepalive_timer = None
        if self._owner is not None:
            logger.debug("Mock: session released (%s) owner=%s", reason, self._owner)
            self._owner = None
            self.released_count += 1
            self.last_release_reason = reason

    def _arm_keepalive(self) -> None:
        """(Re)start the keepalive auto-release timer for the held session."""
        if self._owner is None:
            return
        if self._keepalive_timer is not None:
            self._keepalive_timer.cancel()
        loop = asyncio.get_running_loop()
        self._keepalive_timer = loop.call_later(
            self.keepalive_hold_s,
            lambda: self._release_session("keepalive_timeout"),
        )

    def _claim_session(self, owner: tuple[tuple[str, int], int]) -> None:
        """Mark ``owner`` as the holder and arm the keepalive timer."""
        self._owner = owner
        self._arm_keepalive()

    def _refresh_keepalive(self, owner: tuple[tuple[str, int], int]) -> None:
        """Re-arm the keepalive timer if ``owner`` is the current holder."""
        if self.single_owner and self._owner == owner:
            self._arm_keepalive()

    def _conninfo_status(self, addr: tuple[str, int], sender_id: int) -> bytes:
        """Ownership-aware status reply for a conninfo (data-port request).

        Single-owner semantics:
          * a FOREIGN owner asking while a session is held -> busy reject
            (civ_port=0, error=0xFFFFFFFF);
          * the CI-V port forced unavailable -> not-ready (civ_port=0, error=0);
          * otherwise claim the session for this owner and return the CI-V port.

        When ``single_owner`` is False this preserves the legacy behaviour
        (always return the real CI-V port) so existing tests stay green.
        """
        if not self.single_owner:
            return self._status_response(sender_id)

        owner = (addr, sender_id)

        # Foreign held session -> previous-session-active reject.
        if self._owner is not None and self._owner != owner:
            self.busy_rejects += 1
            return self._status_response(
                sender_id, civ_port=0, error=_ERR_PREV_SESSION_ACTIVE
            )

        # CI-V port forced not-ready (cooldown driver; distinct from busy).
        if time.monotonic() < self._civ_unavailable_until:
            return self._status_response(sender_id, civ_port=0, error=_ERR_OK)

        # Available: claim (or refresh) the session for this owner.
        self._claim_session(owner)
        return self._status_response(sender_id)

    # ------------------------------------------------------------------
    # State setters (for test setup)
    # ------------------------------------------------------------------

    def set_frequency(self, hz: int) -> None:
        """Set the radio's current frequency (Hz)."""
        self._frequency = hz

    def set_mode(self, mode: int) -> None:
        """Set mode byte (e.g. 0x01=USB, 0x00=LSB)."""
        self._mode = mode

    def set_power(self, level: int) -> None:
        """Set RF power level (0-255)."""
        self._power = level

    def set_s_meter(self, value: int) -> None:
        """Set S-meter readback value (0-255)."""
        self._s_meter = value

    def set_swr(self, value: int) -> None:
        """Set SWR meter readback value (0-255)."""
        self._swr = value

    def set_attenuator(self, db: int) -> None:
        """Set attenuator dB value (0, 3, 6, …, 45)."""
        self._attenuator = db

    def set_preamp(self, level: int) -> None:
        """Set preamp level (0=off, 1=PREAMP1, 2=PREAMP2)."""
        self._preamp = level

    def set_digisel(self, on: bool) -> None:
        """Set DIGI-SEL (IP+) state."""
        self._digisel = 1 if on else 0

    # ------------------------------------------------------------------
    # Packet dispatch
    # ------------------------------------------------------------------

    def _emit(
        self,
        proto: _MockProtocol,
        pkt: bytes,
        addr: tuple[str, int],
        label: str,
    ) -> None:
        """Send a reply through the fault-injection layer (drop/reorder).

        Replies built by the handlers go through here instead of ``proto.send``
        directly so that ``drop_rate`` and ``reorder_window`` apply. Discovery
        and the session-critical handshake are never dropped — only data-plane
        and post-handshake replies — to keep connect() deterministic while still
        exercising the data watchdog and recovery paths.
        """
        if self.drop_rate > 0.0 and self._rng.random() < self.drop_rate:
            logger.debug("Mock: dropped %s reply (%d bytes)", label, len(pkt))
            return
        if self.reorder_window > 1:
            buf = self._reorder_buf[label]
            buf.append((proto, pkt, addr))
            if len(buf) >= self.reorder_window:
                self._rng.shuffle(buf)
                for p, b, a in buf:
                    p.send(b, a)
                buf.clear()
            return
        proto.send(pkt, addr)

    def _on_packet(
        self,
        data: bytes,
        addr: tuple[str, int],
        label: str,
        proto: _MockProtocol,
    ) -> None:
        """Entry point for all incoming packets."""
        if len(data) < _HEADER_SIZE:
            return
        ptype = struct.unpack_from("<H", data, 4)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]
        seq = struct.unpack_from("<H", data, 6)[0]

        if self.response_delay > 0:
            asyncio.get_running_loop().call_later(
                self.response_delay,
                lambda: self._dispatch(data, addr, ptype, sender_id, seq, label, proto),
            )
        else:
            self._dispatch(data, addr, ptype, sender_id, seq, label, proto)

    def _dispatch(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        label: str,
        proto: _MockProtocol,
    ) -> None:
        if label == "ctrl":
            self._ctrl_client_id = sender_id
            self._handle_ctrl(data, addr, ptype, sender_id, seq, proto)
        elif label == "civ":
            self._civ_client_id = sender_id
            self._handle_civ(data, addr, ptype, sender_id, seq, proto)

    # ------------------------------------------------------------------
    # Control port
    # ------------------------------------------------------------------

    def _handle_ctrl(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        proto: _MockProtocol,
    ) -> None:
        n = len(data)

        # Are You There → I Am Here
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_THERE:
            proto.send(self._ctrl_pkt(_PT_I_AM_HERE, 0, sender_id), addr)
            return

        # Are You Ready → echo Are You Ready
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_READY:
            proto.send(self._ctrl_pkt(_PT_ARE_YOU_READY, 0, sender_id), addr)
            return

        # Ping request → Ping reply (refreshes the keepalive for the owner)
        if n == _PING_SIZE and ptype == _PT_PING and data[0x10] == 0x00:
            self._refresh_keepalive((addr, sender_id))
            proto.send(self._ping_reply(data, sender_id), addr)
            return

        # Login (0x80 bytes)
        if n == 0x80:
            proto.send(self._login_response(data, sender_id), addr)
            return

        # Token ack (0x40 bytes, requesttype=0x02) → send GUID conninfo
        if n == 0x40 and data[0x15] == _TOKEN_ACK:
            self._refresh_keepalive((addr, sender_id))
            proto.send(self._guid_conninfo(sender_id), addr)
            return

        # Token renewal (0x40 bytes, requesttype=0x05) → refresh keepalive
        if n == 0x40 and data[0x15] == _TOKEN_RENEW:
            self._refresh_keepalive((addr, sender_id))
            return

        # Token remove (0x40 bytes, requesttype=0x01) → graceful close: free now
        if n == 0x40 and data[0x15] == _TOKEN_REMOVE:
            if self.single_owner and self._owner == (addr, sender_id):
                self._release_session("token_remove")
            return

        # Client conninfo (0x90 bytes, requesttype=0x03) → status with CI-V port
        if n == 0x90 and data[0x15] == 0x03:
            proto.send(self._conninfo_status(addr, sender_id), addr)
            return

        # Disconnect → free a held session for this owner
        if n == _HEADER_SIZE and ptype == _PT_DISCONNECT:
            if self.single_owner and self._owner == (addr, sender_id):
                self._release_session("disconnect")
            return

    # ------------------------------------------------------------------
    # CI-V port
    # ------------------------------------------------------------------

    def _handle_civ(
        self,
        data: bytes,
        addr: tuple[str, int],
        ptype: int,
        sender_id: int,
        seq: int,
        proto: _MockProtocol,
    ) -> None:
        n = len(data)

        # Are You There → I Am Here
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_THERE:
            proto.send(self._ctrl_pkt(_PT_I_AM_HERE, 0, sender_id), addr)
            return

        # Are You Ready → echo
        if n == _HEADER_SIZE and ptype == _PT_ARE_YOU_READY:
            proto.send(self._ctrl_pkt(_PT_ARE_YOU_READY, 0, sender_id), addr)
            return

        # Ping request → Ping reply
        if n == _PING_SIZE and ptype == _PT_PING and data[0x10] == 0x00:
            proto.send(self._ping_reply(data, sender_id), addr)
            return

        # OpenClose (0x16 bytes)
        #
        # Real radios typically begin streaming CI-V data soon after an OpenClose(open)
        # which makes the client "radio_ready" quickly. Our tests use startup readiness
        # checks (radio_ready), so we emit a tiny unsolicited CI-V frame on open to mark
        # the stream as active for mock-based integration tests.
        if n == 0x16:
            if data[0x15] == _OPENCLOSE_OPEN:  # open_stream
                self._civ_owner_addr = addr
                self._civ_owner_id = sender_id
                civ = self._civ_frame(to=0x00, frm=self._radio_addr, cmd=0x00)
                proto.send(self._wrap_civ(civ, sender_id), addr)
            elif data[0x15] == _OPENCLOSE_CLOSE:  # graceful data-port close
                # OpenClose(close) frees the single held session immediately.
                if self.single_owner and self._owner is not None:
                    self._release_session("openclose_close")
            return

        # Disconnect
        if n == _HEADER_SIZE and ptype == _PT_DISCONNECT:
            return

        # CI-V data (DATA type, larger than header)
        if ptype == _PT_DATA and n > _CIV_HEADER_SIZE:
            self._handle_civ_data(data, addr, sender_id, proto)

    def _handle_civ_data(
        self,
        data: bytes,
        addr: tuple[str, int],
        sender_id: int,
        proto: _MockProtocol,
    ) -> None:
        """Parse CI-V frame from a DATA packet and generate a response."""
        datalen = struct.unpack_from("<H", data, 0x11)[0]
        end = _CIV_HEADER_SIZE + datalen
        if end > len(data):
            return
        civ = data[_CIV_HEADER_SIZE:end]

        if len(civ) < 6:
            return
        if civ[:2] != _CIV_PREAMBLE:
            return
        if civ[-1:] != _CIV_TERM:
            return

        to_addr = civ[2]
        from_addr = civ[3]
        cmd = civ[4]
        payload = civ[5:-1]

        # Only handle commands addressed to this radio
        if to_addr != self._radio_addr:
            return

        response_civ = self._dispatch_civ(cmd, payload, from_addr)
        if response_civ is not None:
            proto.send(self._wrap_civ(response_civ, sender_id), addr)

    # ------------------------------------------------------------------
    # Autonomous state + unsolicited CI-V streaming
    # ------------------------------------------------------------------

    async def _unsolicited_loop(self) -> None:
        """Emit unsolicited (transceive-style) CI-V frames on a cadence.

        Drifts the frequency and pushes an unsolicited 0x00 (freq report) frame
        to the last CI-V client, modelling a radio that streams data on its own.
        Suppressed while ``stall_for`` is active so the data watchdog can trip.
        """
        try:
            while True:
                await asyncio.sleep(self.unsolicited_interval_s)
                if self._civ_udp is None:
                    continue
                if time.monotonic() < self._stall_until:
                    continue  # stalled: deliberately starve the watchdog
                addr = self._civ_owner_addr
                if addr is None:
                    continue
                # Drift state and broadcast a freq report (transceive style).
                self._frequency += 10
                civ = self._civ_frame(
                    to=0x00,
                    frm=self._radio_addr,
                    cmd=_CMD_FREQ_GET,
                    data=_bcd_encode_freq(self._frequency),
                )
                pkt = self._wrap_civ(civ, self._civ_owner_id)
                if self.drop_rate > 0.0 and self._rng.random() < self.drop_rate:
                    continue
                if self._civ_udp is not None and not self._civ_udp.is_closing():
                    self._civ_udp.sendto(pkt, addr)
                    self.unsolicited_sent += 1
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Mock: unsolicited CI-V loop error")

    # ------------------------------------------------------------------
    # CI-V command dispatcher
    # ------------------------------------------------------------------

    def _dispatch_civ(self, cmd: int, payload: bytes, from_addr: int) -> bytes | None:
        """Build a CI-V response frame for the given command."""
        to = from_addr  # respond to whoever sent
        frm = self._radio_addr

        # --- Command29 wrapper (ATT / PREAMP / DIGI-SEL) ---
        if cmd == _CMD_CMD29:
            if len(payload) < 2:
                return self._civ_nak(to, frm)
            receiver = payload[0]
            real_cmd = payload[1]
            inner = payload[2:]
            return self._dispatch_cmd29(real_cmd, inner, from_addr, receiver)

        # --- Frequency ---
        if cmd == _CMD_FREQ_GET:  # 0x03 get
            return self._civ_frame(
                to, frm, _CMD_FREQ_GET, data=_bcd_encode_freq(self._frequency)
            )

        if cmd == _CMD_FREQ_SET:  # 0x05 set
            if len(payload) == 5:
                self._frequency = _bcd_decode_freq(payload)
            return self._civ_ack(to, frm)

        # --- Mode ---
        if cmd == _CMD_MODE_GET:  # 0x04 get
            return self._civ_frame(
                to, frm, _CMD_MODE_GET, data=bytes([self._mode, self._filter])
            )

        if cmd == _CMD_MODE_SET:  # 0x06 set
            if payload:
                self._mode = payload[0]
                if len(payload) > 1:
                    self._filter = payload[1]
            return self._civ_ack(to, frm)

        # --- Level (0x14) ---
        if cmd == _CMD_LEVEL:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            rest = payload[1:]
            if sub == _SUB_RF_POWER:  # 0x0A RF power
                if rest:  # set
                    self._power = self._decode_level_bcd(rest)
                    return self._civ_ack(to, frm)
                else:  # get
                    return self._civ_frame(
                        to,
                        frm,
                        _CMD_LEVEL,
                        sub=_SUB_RF_POWER,
                        data=_level_bcd_encode(self._power),
                    )
            return self._civ_nak(to, frm)

        # --- Meter (0x15) ---
        if cmd == _CMD_METER:
            if not payload:
                return self._civ_nak(to, frm)
            sub = payload[0]
            if sub == _SUB_S_METER:  # 0x02
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_S_METER,
                    data=_level_bcd_encode(self._s_meter),
                )
            if sub == _SUB_SWR_METER:  # 0x12
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_SWR_METER,
                    data=_level_bcd_encode(self._swr),
                )
            if sub == _SUB_ALC_METER:  # 0x13
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_METER,
                    sub=_SUB_ALC_METER,
                    data=_level_bcd_encode(self._alc),
                )
            return self._civ_nak(to, frm)

        # ACK / NAK from client — ignore
        if cmd in (_CMD_ACK, _CMD_NAK):
            return None

        # TODO: Scope commands (0x27) — add when scope integration tests are needed

        # Unknown command → NAK
        return self._civ_nak(to, frm)

    def _dispatch_cmd29(
        self, real_cmd: int, inner: bytes, from_addr: int, receiver: int
    ) -> bytes | None:
        """Handle a Command29-prefixed CI-V command.

        For GET responses the reply is wrapped back in Command29 so the client
        can match ``our_cmd = 0x29`` in its response scan.
        For SET responses a plain ACK is returned (client matches 0xFB directly).
        """
        to = from_addr
        frm = self._radio_addr

        # ATT (0x11)
        if real_cmd == _CMD_ATT:
            if inner:  # SET — inner[0] is BCD-encoded dB
                raw = inner[0]
                self._attenuator = ((raw >> 4) & 0x0F) * 10 + (raw & 0x0F)
                return self._civ_ack(to, frm)
            # GET — wrap response in Command29
            bcd = _bcd_byte(self._attenuator)
            return self._civ_frame(
                to, frm, _CMD_CMD29, data=bytes([receiver, _CMD_ATT, bcd])
            )

        # PREAMP / DIGI-SEL (0x16)
        if real_cmd == _CMD_PREAMP:
            if not inner:
                return self._civ_nak(to, frm)
            sub = inner[0]
            rest = inner[1:]

            if sub == _SUB_PREAMP_STATUS:  # 0x02
                if rest:  # SET
                    self._preamp = rest[0]
                    return self._civ_ack(to, frm)
                # GET — wrap in Command29
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_PREAMP, _SUB_PREAMP_STATUS, self._preamp]
                    ),
                )

            if sub == _SUB_DIGISEL_STATUS:  # 0x4E
                if rest:  # SET
                    self._digisel = rest[0]
                    return self._civ_ack(to, frm)
                # GET — wrap in Command29
                return self._civ_frame(
                    to,
                    frm,
                    _CMD_CMD29,
                    data=bytes(
                        [receiver, _CMD_PREAMP, _SUB_DIGISEL_STATUS, self._digisel]
                    ),
                )

            return self._civ_nak(to, frm)

        return self._civ_nak(to, frm)

    # ------------------------------------------------------------------
    # CI-V frame builders
    # ------------------------------------------------------------------

    def _civ_frame(
        self,
        to: int,
        frm: int,
        cmd: int,
        sub: int | None = None,
        data: bytes | None = None,
    ) -> bytes:
        frame = bytearray(_CIV_PREAMBLE)
        frame.append(to)
        frame.append(frm)
        frame.append(cmd)
        if sub is not None:
            frame.append(sub)
        if data:
            frame.extend(data)
        frame.extend(_CIV_TERM)
        return bytes(frame)

    def _civ_ack(self, to: int, frm: int) -> bytes:
        return self._civ_frame(to, frm, _CMD_ACK)

    def _civ_nak(self, to: int, frm: int) -> bytes:
        return self._civ_frame(to, frm, _CMD_NAK)

    def _wrap_civ(self, civ_frame: bytes, client_id: int) -> bytes:
        """Wrap a CI-V frame in a 0x15-byte DATA header for the CI-V port."""
        total = _CIV_HEADER_SIZE + len(civ_frame)
        pkt = bytearray(total)
        struct.pack_into("<I", pkt, 0x00, total)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<H", pkt, 0x06, self._civ_seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x10] = 0xC1
        struct.pack_into("<H", pkt, 0x11, len(civ_frame))
        struct.pack_into(">H", pkt, 0x13, self._civ_seq)
        pkt[_CIV_HEADER_SIZE:] = civ_frame
        self._civ_seq = (self._civ_seq + 1) & 0xFFFF
        return bytes(pkt)

    @staticmethod
    def _decode_level_bcd(data: bytes) -> int:
        """Decode 2-byte BCD level (e.g. b'\\x01\\x28' → 128)."""
        d0 = (data[0] >> 4) & 0x0F
        d1 = data[0] & 0x0F
        d2 = (data[1] >> 4) & 0x0F
        d3 = data[1] & 0x0F
        return d0 * 1000 + d1 * 100 + d2 * 10 + d3

    # ------------------------------------------------------------------
    # Control packet builders
    # ------------------------------------------------------------------

    def _ctrl_pkt(self, ptype: int, seq: int, client_id: int) -> bytes:
        """Build a 0x10-byte control/discovery packet."""
        pkt = bytearray(_HEADER_SIZE)
        struct.pack_into("<I", pkt, 0x00, _HEADER_SIZE)
        struct.pack_into("<H", pkt, 0x04, ptype)
        struct.pack_into("<H", pkt, 0x06, seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        return bytes(pkt)

    def _ping_reply(self, data: bytes, client_id: int) -> bytes:
        """Build a ping reply echoing the received timestamp."""
        pkt = bytearray(_PING_SIZE)
        struct.pack_into("<I", pkt, 0x00, _PING_SIZE)
        struct.pack_into("<H", pkt, 0x04, _PT_PING)
        seq = struct.unpack_from("<H", data, 6)[0]
        struct.pack_into("<H", pkt, 0x06, seq)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x10] = 0x01  # reply flag
        pkt[0x11:0x15] = data[0x11:0x15]  # echo timestamp
        return bytes(pkt)

    def _login_response(self, data: bytes, sender_id: int) -> bytes:
        """Build a 0x60-byte login response.

        On auth_fail, sets the error field to 0xFEFFFFFF so that
        ``parse_auth_response`` marks the response as a failure.
        """
        tok_request = struct.unpack_from("<H", data, 0x1A)[0]
        pkt = bytearray(0x60)
        struct.pack_into("<I", pkt, 0x00, 0x60)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        struct.pack_into("<H", pkt, 0x1A, tok_request)
        if self.auth_fail:
            struct.pack_into("<I", pkt, 0x30, 0xFEFFFFFF)
        else:
            struct.pack_into("<I", pkt, 0x1C, self.token)
            # error at 0x30 stays 0x00000000 → success
            pkt[0x40:0x44] = b"FTTH"
        return bytes(pkt)

    def _guid_conninfo(self, sender_id: int) -> bytes:
        """Build a 0x90-byte conninfo that the radio sends after token-ack.

        The client reads the 16-byte GUID from offset 0x20.
        """
        pkt = bytearray(0x90)
        struct.pack_into("<I", pkt, 0x00, 0x90)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        # Fake GUID at 0x20-0x30
        pkt[0x20:0x30] = bytes(range(0x01, 0x11))
        return bytes(pkt)

    def _status_response(
        self,
        sender_id: int,
        *,
        civ_port: int | None = None,
        error: int = _ERR_OK,
    ) -> bytes:
        """Build a 0x50-byte status packet carrying the CI-V port number.

        Layout mirrors ``parse_status_response`` in auth.py (line ~400)
        and ``_build_status`` in test_radio_connect.py:
          offset 0x30 (LE u32) = error code
          offset 0x42 (BE u16) = CIV port
          offset 0x46 (BE u16) = audio port

        Args:
            sender_id: client connection ID to address the reply to.
            civ_port: CI-V port to advertise; ``None`` = the real bound port,
                ``0`` = not-ready / busy reject.
            error: error code written little-endian at 0x30
                (``0xFFFFFFFF`` = previous-session-active).
        """
        pkt = bytearray(0x50)
        struct.pack_into("<I", pkt, 0x00, 0x50)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, sender_id)
        struct.pack_into("<I", pkt, 0x30, error & 0xFFFFFFFF)
        port = self._actual_civ_port if civ_port is None else civ_port
        struct.pack_into(">H", pkt, 0x42, port)
        # Dummy audio port: keep within uint16 range even if OS assigns civ_port=65535.
        if port == 0:
            audio_port = 0
        else:
            audio_port = port + 1 if port < 65535 else 65534
        struct.pack_into(">H", pkt, 0x46, audio_port)
        return bytes(pkt)
