"""Auth/login FSM, token renewal, watchdog, and reconnect for IcomRadio."""

from __future__ import annotations

import asyncio
import logging
import socket as _socket
import struct
import time
from typing import TYPE_CHECKING

from rigplane.core.auth import (
    build_conninfo_packet,
    build_login_packet,
    parse_auth_response,
    parse_status_response,
)
from ._connection_state import RadioConnectionState
from rigplane.core.exceptions import AuthenticationError, ConnectionError, TimeoutError
from .startup_checks import wait_for_radio_startup_ready
from rigplane.core.transport import ConnectionState, IcomTransport
from rigplane.core.types import AudioCodec

if TYPE_CHECKING:
    from ._runtime_protocols import ControlPhaseHost

logger = logging.getLogger(__name__)

# Packet size constants (per wfview packettypes.h).
OPENCLOSE_SIZE = 0x16
TOKEN_ACK_SIZE = 0x40
CONNINFO_SIZE = 0x90
STATUS_SIZE = 0x50

# Stereo codec IDs — used to decide stereo→mono fallback on session rejection.
# See #797.
_STEREO_CODECS = (
    AudioCodec.PCM_2CH_8BIT,
    AudioCodec.PCM_2CH_16BIT,
    AudioCodec.ULAW_2CH,
    AudioCodec.OPUS_2CH,
)

__all__ = [
    "ControlPhaseRuntime",
    "OPENCLOSE_SIZE",
    "TOKEN_ACK_SIZE",
    "CONNINFO_SIZE",
    "STATUS_SIZE",
]


class ControlPhaseRuntime:
    """Composed control-phase runtime: connect, disconnect, token renewal, watchdog.

    Holds a reference to the host (CoreRadio); all state remains on the host.
    """

    TOKEN_RENEWAL_INTERVAL = 60.0
    TOKEN_PACKET_SIZE = 0x40
    WATCHDOG_CHECK_INTERVAL = 0.5
    _WATCHDOG_HEALTH_LOG_INTERVAL = 30.0
    _STATUS_RETRY_PAUSE = 10.0
    _STATUS_REJECT_COOLDOWN = 30.0

    def __init__(self, host: ControlPhaseHost) -> None:
        self._host = host

    def _resolve_local_bind_host(self) -> str:
        """Resolve the routed local interface IP used to reach the radio."""
        h = self._host
        probe = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            probe.connect((h._host, h._port))
            local_host = probe.getsockname()[0]
        except OSError:
            logger.debug(
                "Falling back to wildcard UDP bind while resolving local interface for %s:%d",
                h._host,
                h._port,
                exc_info=True,
            )
            return "0.0.0.0"
        finally:
            probe.close()
        return local_host or "0.0.0.0"

    def _close_pending_sockets(self) -> None:
        """Close any pre-bound sockets that were not yet consumed by a transport."""
        h = self._host
        for attr in ("_civ_sock_pending", "_audio_sock_pending"):
            sock = getattr(h, attr, None)
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
                setattr(h, attr, None)

    async def connect(self) -> None:
        """Open connection to the radio and authenticate."""
        h = self._host
        h._conn_state = RadioConnectionState.CONNECTING
        h._civ_stream_ready = False
        h._civ_recovering = False
        h._last_status_error = 0
        h._last_status_disconnected = False
        # Re-enable queue during setup — we need status packets from radio
        h._ctrl_transport._discard_data_packets = False
        local_bind_host = self._resolve_local_bind_host()
        h._local_bind_host = local_bind_host

        _reconnect = getattr(h, "_has_connected_once", False)
        try:
            if _reconnect:
                await h._ctrl_transport.reconnect(
                    h._host,
                    h._port,
                    local_host=local_bind_host,
                )
            else:
                await h._ctrl_transport.connect(
                    h._host,
                    h._port,
                    local_host=local_bind_host,
                )
        except OSError as exc:
            raise ConnectionError(
                f"Failed to connect to {h._host}:{h._port}: {exc}"
            ) from exc

        h._ctrl_transport.start_ping_loop()
        h._ctrl_transport.start_retransmit_loop()

        login_pkt = build_login_packet(
            h._username,
            h._password,
            sender_id=h._ctrl_transport.my_id,
            receiver_id=h._ctrl_transport.remote_id,
        )
        await h._ctrl_transport.send_tracked(login_pkt)
        resp_data = await self._wait_for_packet(
            h._ctrl_transport,
            size=0x60,
            label="login response",
        )
        auth = parse_auth_response(resp_data)
        if not auth.success:
            raise AuthenticationError(
                f"Authentication failed (error=0x{auth.error:08X})"
            )
        h._token = auth.token
        h._tok_request = auth.tok_request
        logger.info(
            "Authenticated with %s:%d, token=0x%08X",
            h._host,
            h._port,
            h._token,
        )

        await self._send_token_ack()

        guid = await self._receive_guid()

        _civ_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        _civ_sock.bind((local_bind_host, 0))
        _civ_local_port = _civ_sock.getsockname()[1]
        _audio_sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        _audio_sock.bind((local_bind_host, 0))
        _audio_local_port = _audio_sock.getsockname()[1]
        # Sockets are kept open until the transport consumes them,
        # eliminating the TOCTOU race where another process could grab
        # the port between close() and the transport bind.
        logger.debug(
            "Reserved local ports on %s: civ=%d, audio=%d",
            local_bind_host,
            _civ_local_port,
            _audio_local_port,
        )

        h._civ_port = h._port + 1
        h._audio_port = h._port + 2
        h._civ_local_port = _civ_local_port
        h._audio_local_port = _audio_local_port
        h._civ_sock_pending = _civ_sock
        h._audio_sock_pending = _audio_sock
        await self._send_conninfo(guid, _civ_local_port, _audio_local_port)

        try:
            civ_port = await self._receive_civ_port()
            if civ_port > 0 and civ_port != h._civ_port:
                logger.warning(
                    "Radio reported non-default civ_port=%d (expected %d), using radio value",
                    civ_port,
                    h._civ_port,
                )
                h._civ_port = civ_port
            elif civ_port == 0:
                # Fail fast on immediate session rejection (error=0xFFFFFFFF).
                # Stereo rejection: downgrade to mono and retry once (#797).
                # Mono rejection: raise immediately.
                if getattr(h, "_last_status_error", 0) == 0xFFFFFFFF:
                    if h._audio_codec in _STEREO_CODECS:
                        # Single-RX firmwares (IC-7300/IC-705, possibly IC-9700)
                        # may reject stereo rx_codec at conninfo. Retry once with
                        # PCM_1CH_16BIT before failing. See issue #797.
                        logger.warning(
                            "Status rejected session (error=0xFFFFFFFF) with "
                            "stereo rx_codec=%s; retrying with PCM_1CH_16BIT fallback",
                            h._audio_codec.name,
                        )
                        h._audio_codec = AudioCodec.PCM_1CH_16BIT
                        h._last_status_error = 0
                        await self._send_conninfo(
                            guid, _civ_local_port, _audio_local_port
                        )
                        civ_port = await self._receive_civ_port()
                        if civ_port > 0:
                            logger.info(
                                "Stereo-to-mono fallback succeeded (civ_port=%d)",
                                civ_port,
                            )
                            h._civ_port = civ_port
                        elif getattr(h, "_last_status_error", 0) == 0xFFFFFFFF:
                            # Mono also rejected outright — no busy-retry will help.
                            self._close_pending_sockets()
                            await h._ctrl_transport.disconnect()
                            h._conn_state = RadioConnectionState.DISCONNECTED
                            error_val = getattr(h, "_last_status_error", 0)
                            raise ConnectionError(
                                f"Radio rejected session allocation with both "
                                f"stereo and mono rx_codec "
                                f"(final error=0x{error_val:08X}). "
                                "A previous session may still be active. "
                                "Wait 30-60s and retry."
                            )
                        # else: civ_port=0 without 0xFFFFFFFF — session still
                        # warming up after fallback. Fall through to the
                        # busy-retry loop with the now-mono codec.
                    else:
                        self._close_pending_sockets()
                        await h._ctrl_transport.disconnect()
                        h._conn_state = RadioConnectionState.DISCONNECTED
                        raise ConnectionError(
                            f"Radio rejected session allocation (civ_port=0, "
                            f"error=0x{h._last_status_error:08X}). "
                            "A previous session may still be active. "
                            "Wait 30-60s and retry."
                        )

                # Busy-retry loop — runs when:
                #   (a) first status was civ_port=0 without 0xFFFFFFFF, or
                #   (b) mono fallback above produced civ_port=0 without 0xFFFFFFFF.
                if civ_port == 0:
                    retry_pause = self._status_retry_pause()
                    logger.warning(
                        "Status returned civ_port=0 — radio session not ready. "
                        "Retrying after %.0fs pause (previous session may still be held)...",
                        retry_pause,
                    )
                    for _retry in range(3):
                        await asyncio.sleep(retry_pause)
                        logger.info("Retrying conninfo (attempt %d/3)...", _retry + 1)
                        await self._send_conninfo(
                            guid, _civ_local_port, _audio_local_port
                        )
                        try:
                            civ_port = await self._receive_civ_port()
                            if civ_port > 0:
                                logger.info("Radio now reports civ_port=%d", civ_port)
                                h._civ_port = civ_port
                                break
                        except asyncio.TimeoutError:
                            pass
                    else:
                        self._close_pending_sockets()
                        await h._ctrl_transport.disconnect()
                        h._conn_state = RadioConnectionState.DISCONNECTED
                        error_val = getattr(h, "_last_status_error", 0)
                        raise ConnectionError(
                            f"Radio rejected session allocation (civ_port=0, "
                            f"error=0x{error_val:08X}) after retries. "
                            "A previous session may still be active. "
                            "Wait 30-60s and retry."
                        )
        except asyncio.TimeoutError:
            logger.debug("No status packet received, using default ports")
            logger.warning("Audio port not in status, using default %d", h._audio_port)

        from rigplane.transport import IcomTransport

        h._civ_transport = IcomTransport()
        h._civ_transport._scope_shed_callback = h._scope_assembler.shed_incomplete
        civ_sock = getattr(h, "_civ_sock_pending", None)
        try:
            await h._civ_transport.connect(
                h._host,
                h._civ_port,
                local_host=getattr(h, "_local_bind_host", None),
                local_port=h._civ_local_port,
                sock=civ_sock,
            )
        except OSError as exc:
            # create_datagram_endpoint never ran — safe to close both sockets.
            self._close_pending_sockets()
            await h._ctrl_transport.disconnect()
            raise ConnectionError(
                f"Failed to connect CI-V port {h._civ_port}: {exc}"
            ) from exc
        except Exception:
            # asyncio consumed civ_sock — don't double-close it.
            h._civ_sock_pending = None
            # audio socket is still ours — clean it up.
            audio_sock = getattr(h, "_audio_sock_pending", None)
            if audio_sock is not None:
                audio_sock.close()
                h._audio_sock_pending = None
            raise
        else:
            # Socket consumed by asyncio — no longer ours to close.
            h._civ_sock_pending = None

        h._civ_transport.start_ping_loop()
        h._civ_transport.start_retransmit_loop()
        h._civ_transport.start_idle_loop()

        await self._send_open_close(open_stream=True)

        await asyncio.sleep(0.3)
        await self._flush_queue(h._civ_transport)

        civ_rt = getattr(h, "_civ_runtime", None)
        if civ_rt is not None:
            civ_rt.advance_generation("connect")
        else:
            h._advance_civ_generation("connect")
        h._civ_last_waiter_gc_monotonic = time.monotonic()
        if civ_rt is not None:
            civ_rt.start_pump()
            civ_rt.start_data_watchdog()
            civ_rt.start_worker()
        else:
            h._start_civ_rx_pump()
            h._start_civ_data_watchdog()
            h._start_civ_worker()
        h._conn_state = RadioConnectionState.CONNECTED
        h._ctrl_transport.state = ConnectionState.CONNECTED
        # Control transport queue has no consumer after setup — discard
        # incoming data packets to prevent unbounded queue growth.
        h._ctrl_transport._discard_data_packets = True
        self._start_token_renewal()
        if h._auto_reconnect:
            self._start_watchdog()

        try:
            await wait_for_radio_startup_ready(
                h,  # type: ignore[arg-type]
                timeout=getattr(h, "_timeout", 5.0),
                component="radio connect",
            )
        except RuntimeError as exc:
            await h.disconnect()
            raise ConnectionError(str(exc)) from exc

        h._has_connected_once = True
        logger.info(
            "Connected to %s (control=%d, civ=%d)",
            h._host,
            h._port,
            h._civ_port,
        )

    async def disconnect(self) -> None:
        """Cleanly disconnect from the radio."""
        h = self._host
        if h._conn_state != RadioConnectionState.CONNECTED:
            return
        h._conn_state = RadioConnectionState.DISCONNECTING
        civ_rt = getattr(h, "_civ_runtime", None)
        if civ_rt is not None:
            civ_rt.advance_generation("disconnect")
        else:
            h._advance_civ_generation("disconnect")
        self._stop_watchdog()
        self._stop_reconnect()
        self._stop_token_renewal()
        stop_audio_wd = getattr(h, "_stop_audio_watchdog", None)
        if stop_audio_wd is not None:
            await stop_audio_wd()
        if h._audio_stream is not None:
            await h._audio_stream.stop_rx()
            await h._audio_stream.stop_tx()
            h._audio_stream = None
        h._pcm_tx_fmt = None
        h._pcm_rx_user_callback = None
        h._opus_rx_user_callback = None
        if h._audio_transport is not None:
            try:
                await self._send_audio_open_close(open_stream=False)
            except Exception:
                logger.debug("disconnect: audio open/close failed", exc_info=True)
            await h._audio_transport.disconnect()
            h._audio_transport = None
        if h._civ_transport:
            try:
                await self._send_open_close(open_stream=False)
            except Exception:
                logger.debug("disconnect: civ open/close failed", exc_info=True)
            if civ_rt is not None:
                await civ_rt.stop_data_watchdog()
                await civ_rt.stop_worker()
                await civ_rt.stop_pump()
            else:
                await h._stop_civ_data_watchdog()
                await h._stop_civ_worker()
                await h._stop_civ_rx_pump()
            await h._civ_transport.disconnect()
            h._civ_transport = None
        try:
            await self._send_token(0x01)
        except Exception:
            logger.debug("disconnect: token remove failed", exc_info=True)
        await h._ctrl_transport.disconnect()
        h._conn_state = RadioConnectionState.DISCONNECTED
        h._civ_stream_ready = False
        h._civ_recovering = False
        logger.info("Disconnected from %s:%d", h._host, h._port)

    async def soft_reconnect(self) -> None:
        """Reconnect CI-V transport using existing control session."""
        h = self._host
        if h._civ_transport is not None:
            logger.warning("soft_reconnect: CI-V transport already open")
            return
        if not h._ctrl_transport or not getattr(
            h._ctrl_transport, "_udp_transport", None
        ):
            logger.info("soft_reconnect: control transport gone, doing full connect")
            await self.connect()
            return

        h._conn_state = RadioConnectionState.CONNECTING
        h._civ_stream_ready = False
        h._civ_recovering = True

        from rigplane.transport import IcomTransport

        h._civ_transport = IcomTransport()
        try:
            await h._civ_transport.connect(
                h._host,
                h._civ_port,
                local_host=getattr(h, "_local_bind_host", None),
                local_port=getattr(h, "_civ_local_port", 0),
            )
        except OSError as exc:
            h._civ_transport = None
            ctrl_alive = getattr(h, "control_connected", False)
            h._conn_state = (
                RadioConnectionState.RECONNECTING
                if ctrl_alive
                else RadioConnectionState.DISCONNECTED
            )
            h._civ_stream_ready = False
            h._civ_recovering = ctrl_alive
            raise ConnectionError(f"Failed to reconnect CI-V: {exc}") from exc

        h._civ_transport.start_ping_loop()
        h._civ_transport.start_retransmit_loop()
        h._civ_transport.start_idle_loop()
        await self._send_open_close(open_stream=True)

        civ_rt = getattr(h, "_civ_runtime", None)
        if civ_rt is not None:
            civ_rt.advance_generation("soft_reconnect")
        else:
            h._advance_civ_generation("soft_reconnect")
        h._civ_last_waiter_gc_monotonic = time.monotonic()
        if civ_rt is not None:
            await civ_rt.stop_pump()
            civ_rt.start_pump()
        else:
            await h._stop_civ_rx_pump()
            h._start_civ_rx_pump()
        h._conn_state = RadioConnectionState.CONNECTED
        setattr(h._civ_transport, "_udp_error_count", 0)
        h._last_civ_data_received = time.monotonic()
        if civ_rt is not None:
            civ_rt.start_worker()
            civ_rt.start_data_watchdog()
        else:
            h._start_civ_worker()
            h._start_civ_data_watchdog()
        logger.info("Soft reconnect to %s (civ=%d)", h._host, h._civ_port)
        on_reconnect = getattr(h, "_on_reconnect", None)
        if on_reconnect is not None:
            try:
                on_reconnect()
            except Exception:
                logger.debug(
                    "soft_reconnect: _on_reconnect callback failed", exc_info=True
                )

    async def _send_token_ack(self) -> None:
        h = self._host
        pkt = bytearray(TOKEN_ACK_SIZE)
        struct.pack_into("<I", pkt, 0x00, TOKEN_ACK_SIZE)
        struct.pack_into("<I", pkt, 0x08, h._ctrl_transport.my_id)
        struct.pack_into("<I", pkt, 0x0C, h._ctrl_transport.remote_id)
        struct.pack_into(">I", pkt, 0x10, TOKEN_ACK_SIZE - 0x10)
        pkt[0x14] = 0x01
        pkt[0x15] = 0x02
        struct.pack_into(">H", pkt, 0x16, h._auth_seq)
        h._auth_seq += 1
        struct.pack_into("<H", pkt, 0x1A, h._tok_request)
        struct.pack_into("<I", pkt, 0x1C, h._token)
        struct.pack_into(">H", pkt, 0x24, 0x0798)
        await h._ctrl_transport.send_tracked(bytes(pkt))
        logger.debug("Token ack sent (token=0x%08X)", h._token)

    async def _receive_guid(self) -> bytes | None:
        await asyncio.sleep(0.3)
        guid = None
        h = self._host
        for _ in range(30):
            try:
                d = await h._ctrl_transport.receive_packet(timeout=0.1)
                if len(d) == CONNINFO_SIZE:
                    guid = d[0x20:0x30]
                    logger.debug("Got radio GUID: %s", guid.hex())
            except asyncio.TimeoutError:
                break
        return guid

    async def _send_conninfo(
        self,
        guid: bytes | None,
        civ_local_port: int = 0,
        audio_local_port: int = 0,
    ) -> None:
        h = self._host
        # TX codec must always be mono for IC-7610 (and all Icom LAN radios
        # we target): the mic path through the transceiver is 1-channel, and
        # the stock firmware rejects conninfo with ``error=0xFFFFFFFF`` when
        # ``txcodec`` is a 2ch value (0x08 / 0x10 / 0x20 / 0x41).  wfview
        # enforces the same constraint in its UI by only offering mono TX
        # codecs (``settingswidget.cpp:118-124``).  Our Python client used to
        # mirror the RX codec into TX, which broke stereo RX on LAN.  See
        # issue #794.  We force ``PCM_1CH_16BIT`` so stereo RX works without
        # any user-visible tradeoff — the radio uses its own separately
        # configured TX modulation source regardless of this byte.
        h._audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        tx_codec = int(h._audio_tx_codec)
        conninfo = build_conninfo_packet(
            sender_id=h._ctrl_transport.my_id,
            receiver_id=h._ctrl_transport.remote_id,
            username=h._username,
            token=h._token,
            tok_request=h._tok_request,
            radio_name=getattr(h, "model", "IC-7610"),
            mac_address=b"\x00" * 6,
            auth_seq=h._auth_seq,
            guid=guid,
            rx_codec=int(h._audio_codec),
            tx_codec=tx_codec,
            rx_sample_rate=h._audio_sample_rate,
            tx_sample_rate=h._audio_sample_rate,
            civ_local_port=civ_local_port,
            audio_local_port=audio_local_port,
        )
        h._auth_seq += 1
        await h._ctrl_transport.send_tracked(conninfo)
        logger.debug(
            "Conninfo sent (civ_local=%d, audio_local=%d)",
            civ_local_port,
            audio_local_port,
        )

    async def _receive_civ_port(self) -> int:
        h = self._host
        deadline = time.monotonic() + 2.0
        civ_port = 0
        status_packets_seen = 0
        while time.monotonic() < deadline:
            try:
                remaining = max(0.1, deadline - time.monotonic())
                d = await h._ctrl_transport.receive_packet(timeout=min(remaining, 0.3))
                if len(d) != STATUS_SIZE:
                    continue
                status = parse_status_response(d)
                got_civ: int = status.civ_port
                got_audio: int = status.audio_port
                status_packets_seen += 1
                h._last_status_error = status.error
                h._last_status_disconnected = status.disconnected
                logger.info(
                    "Status: civ_port=%d, audio_port=%d, error=0x%08X, disconnected=%s",
                    got_civ,
                    got_audio,
                    status.error,
                    status.disconnected,
                )
                if got_audio > 0:
                    h._audio_port = got_audio
                if status.error == 0xFFFFFFFF:
                    logger.warning(
                        "Status indicates session rejection (error=0x%08X); "
                        "forcing retry/cooldown path",
                        status.error,
                    )
                    return 0
                if got_civ > 0:
                    return got_civ
                if status_packets_seen >= 2:
                    logger.warning(
                        "Status packet has civ_port=0, falling back to default"
                    )
                    break
            except asyncio.TimeoutError:
                continue
        return civ_port

    def _status_retry_pause(self) -> float:
        if getattr(self._host, "_last_status_error", 0) == 0xFFFFFFFF:
            return self._STATUS_REJECT_COOLDOWN
        return self._STATUS_RETRY_PAUSE

    async def _send_open_close(self, *, open_stream: bool) -> None:
        h = self._host
        if h._civ_transport is None:
            return
        await self._send_open_close_on_transport(
            h._civ_transport,
            send_seq=h._civ_send_seq,
            open_stream=open_stream,
        )
        h._civ_send_seq = (h._civ_send_seq + 1) & 0xFFFF

    async def _send_audio_open_close(self, *, open_stream: bool) -> None:
        h = self._host
        if h._audio_transport is None:
            return
        await self._send_open_close_on_transport(
            h._audio_transport,
            send_seq=h._audio_send_seq,
            open_stream=open_stream,
        )
        h._audio_send_seq = (h._audio_send_seq + 1) & 0xFFFF

    async def _send_open_close_on_transport(
        self,
        transport: IcomTransport,
        *,
        send_seq: int,
        open_stream: bool,
    ) -> None:
        pkt = bytearray(OPENCLOSE_SIZE)
        struct.pack_into("<I", pkt, 0x00, OPENCLOSE_SIZE)
        struct.pack_into("<I", pkt, 0x08, transport.my_id)
        struct.pack_into("<I", pkt, 0x0C, transport.remote_id)
        struct.pack_into("<H", pkt, 0x10, 0x01C0)
        struct.pack_into(">H", pkt, 0x13, send_seq)
        pkt[0x15] = 0x04 if open_stream else 0x00
        await transport.send_tracked(bytes(pkt))
        logger.debug("OpenClose(%s) sent", "open" if open_stream else "close")

    async def _wait_for_packet(
        self, transport: IcomTransport, *, size: int, label: str
    ) -> bytes:
        deadline = time.monotonic() + 2.0
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"{label} timed out")
            try:
                data: bytes = await transport.receive_packet(timeout=remaining)
            except asyncio.TimeoutError:
                raise TimeoutError(f"{label} timed out")
            if len(data) == size:
                return data
            logger.debug(
                "Skipping packet (len=%d) while waiting for %s", len(data), label
            )

    @staticmethod
    async def _flush_queue(transport: IcomTransport, max_pkts: int = 200) -> int:
        count = 0
        for _ in range(max_pkts):
            try:
                await transport.receive_packet(timeout=0.01)
                count += 1
            except asyncio.TimeoutError:
                break
        return count

    def _start_token_renewal(self) -> None:
        h = self._host
        if h._token_task is None or h._token_task.done():
            h._token_task = asyncio.create_task(self._token_renewal_loop())

    def _stop_token_renewal(self) -> None:
        h = self._host
        if h._token_task is not None and not h._token_task.done():
            h._token_task.cancel()
            h._token_task = None

    async def _token_renewal_loop(self) -> None:
        h = self._host
        try:
            while h._conn_state == RadioConnectionState.CONNECTED:
                await asyncio.sleep(self.TOKEN_RENEWAL_INTERVAL)
                if h._conn_state != RadioConnectionState.CONNECTED:
                    break
                try:
                    await self._send_token(0x05)
                    logger.debug("Token renewal sent")
                except Exception as exc:
                    logger.warning("Token renewal failed: %s", exc)
        except asyncio.CancelledError:
            pass

    async def _send_token(self, magic: int) -> None:
        h = self._host
        pkt = bytearray(self.TOKEN_PACKET_SIZE)
        struct.pack_into("<I", pkt, 0x00, self.TOKEN_PACKET_SIZE)
        struct.pack_into("<H", pkt, 0x04, 0x00)
        struct.pack_into("<I", pkt, 0x08, h._ctrl_transport.my_id)
        struct.pack_into("<I", pkt, 0x0C, h._ctrl_transport.remote_id)
        struct.pack_into(">I", pkt, 0x10, self.TOKEN_PACKET_SIZE - 0x10)
        pkt[0x14] = 0x01
        pkt[0x15] = magic
        struct.pack_into(">H", pkt, 0x16, h._auth_seq)
        h._auth_seq += 1
        struct.pack_into("<H", pkt, 0x1A, h._tok_request)
        struct.pack_into(">H", pkt, 0x24, 0x0798)
        struct.pack_into("<I", pkt, 0x1C, h._token)
        await h._ctrl_transport.send_tracked(bytes(pkt))

    def _start_watchdog(self) -> None:
        h = self._host
        if h._watchdog_task is None or h._watchdog_task.done():
            h._watchdog_task = asyncio.create_task(h._watchdog_loop())

    def _stop_watchdog(self) -> None:
        h = self._host
        if h._watchdog_task is not None and not h._watchdog_task.done():
            h._watchdog_task.cancel()
            h._watchdog_task = None

    def _stop_reconnect(self) -> None:
        h = self._host
        if h._reconnect_task is not None and not h._reconnect_task.done():
            h._reconnect_task.cancel()
            h._reconnect_task = None
