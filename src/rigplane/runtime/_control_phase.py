"""Auth/login FSM, token renewal, watchdog, and reconnect for IcomRadio."""

from __future__ import annotations

import asyncio
import errno
import logging
import socket as _socket
import struct
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress as _suppress
from dataclasses import replace
from typing import TYPE_CHECKING, SupportsInt, cast

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
from rigplane.audio.route import AudioConfigSource

if TYPE_CHECKING:
    from ._runtime_protocols import ControlPhaseHost
    from .session_lifecycle import AttemptResult, RadioPresence

logger = logging.getLogger(__name__)

#: Discovery callable injected into :class:`ControlPhaseSessionMechanism` so the
#: lifecycle's ``scan()`` can probe for radios without ``rigplane.runtime``
#: importing ``rigplane.backends`` (layered-architecture boundary).  Matches
#: ``rigplane.backends.discovery.discover_lan_radios``.
_DiscoverFn = Callable[..., Awaitable[list[dict[str, object]]]]

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


class _DataPortDiscoveryCooldown(Exception):
    """Recoverable startup failure after control auth advertised a CI-V port."""


def _is_address_in_use(exc: OSError) -> bool:
    return exc.errno == errno.EADDRINUSE or "Address already in use" in str(exc)


__all__ = [
    "ControlPhaseRuntime",
    "ControlPhaseSessionMechanism",
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
    # Conninfo re-send pause for the WITHIN-attempt busy-retry inside
    # ``_connect_once`` (a packet-mechanism detail: how long to wait before
    # re-sending conninfo when the radio reports civ_port==0 mid-handshake).
    # NOTE: this is NOT the lifecycle's COOLDOWN-between-attempts policy — that
    # now lives solely in ``session_lifecycle.CoreRadioSessionLifecycle``.  The
    # legacy ``ControlPhaseRuntime.connect()`` retry wrapper and its
    # ``_DATA_PORT_COOLDOWN_RETRIES`` loop were removed in A3; the lifecycle
    # owns CONNECTING↔COOLDOWN (it RELEASES before each cooldown wait).
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

    async def _connect_once(self) -> None:
        """Open connection to the radio and authenticate."""
        h = self._host
        h._conn_state = RadioConnectionState.CONNECTING
        h._civ_stream_ready = False
        h._civ_recovering = False
        h._last_status_error = 0
        h._last_status_disconnected = False
        h._last_auth_error = 0
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
            logger.warning(
                "Radio host/control UDP open failed: %s:%d: %s", h._host, h._port, exc
            )
            raise ConnectionError(
                f"Failed to connect to {h._host}:{h._port}: {exc}"
            ) from exc
        except TimeoutError as exc:
            logger.warning(
                "Control discovery failed before auth: %s:%d: %s", h._host, h._port, exc
            )
            await h._ctrl_transport.disconnect()
            h._conn_state = RadioConnectionState.DISCONNECTED
            raise

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
            logger.warning(
                "Control auth failed: %s:%d error=0x%08X", h._host, h._port, auth.error
            )
            # Record the raw auth error so the lifecycle mechanism seam can tell a
            # hard credential rejection (0xFEFFFFFF) apart from a transient
            # "previous session still active" reject (0xFFFFFFFF) at the auth
            # stage — only the former is a non-retryable AUTH_CREDENTIALS hard
            # fail (D3 / lifecycle Note 1).
            h._last_auth_error = auth.error
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
                    codec_source = h._audio_stream_contract.rx_codec_source
                    allow_stereo_fallback = (
                        h._audio_codec in _STEREO_CODECS
                        and codec_source is AudioConfigSource.GLOBAL_DEFAULT
                    )
                    profile_selected_stereo = (
                        h._audio_codec in _STEREO_CODECS
                        and codec_source is AudioConfigSource.PROFILE_DEFAULT
                    )
                    if allow_stereo_fallback:
                        # Single-RX firmwares (IC-7300/IC-705, possibly IC-9700)
                        # may reject stereo rx_codec at conninfo. Retry once with
                        # PCM_1CH_16BIT before failing. See issue #797.
                        logger.warning(
                            "Status rejected session (error=0xFFFFFFFF) with "
                            "stereo rx_codec=%s; retrying with PCM_1CH_16BIT fallback",
                            h._audio_codec.name,
                        )
                        h._audio_codec = AudioCodec.PCM_1CH_16BIT
                        h._audio_stream_contract = replace(
                            h._audio_stream_contract,
                            rx_codec=AudioCodec.PCM_1CH_16BIT,
                            rx_channels=1,
                            rx_codec_source=AudioConfigSource.FALLBACK,
                            fallback_reason="conninfo-stereo-rx-rejected",
                        )
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
                    elif profile_selected_stereo:
                        logger.warning(
                            "Status rejected session (error=0xFFFFFFFF) with "
                            "profile-selected stereo rx_codec=%s; preserving profile "
                            "codec and entering session cooldown/retry path",
                            h._audio_codec.name,
                        )
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
        except TimeoutError as exc:
            logger.warning(
                "CI-V data-port discovery timeout after control auth/status "
                "(host=%s, control=%d, civ=%d, audio=%d): %s",
                h._host,
                h._port,
                h._civ_port,
                h._audio_port,
                exc,
            )
            await self._cleanup_data_port_discovery_timeout()
            raise _DataPortDiscoveryCooldown() from exc
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

    async def _cleanup_data_port_discovery_timeout(self) -> None:
        """Release startup resources after data-port discovery times out."""
        h = self._host
        # Timeout happens after asyncio has consumed civ_sock; the datagram
        # transport owns closing it.  The audio socket has not been consumed.
        h._civ_sock_pending = None
        self._close_pending_sockets()
        if h._civ_transport is not None:
            try:
                await h._civ_transport.disconnect()
            except Exception:
                logger.debug(
                    "data-port cooldown cleanup: civ disconnect failed",
                    exc_info=True,
                )
            h._civ_transport = None
        try:
            await h._ctrl_transport.disconnect()
        except Exception:
            logger.debug(
                "data-port cooldown cleanup: control disconnect failed",
                exc_info=True,
            )
        h._conn_state = RadioConnectionState.DISCONNECTED
        h._civ_stream_ready = False
        h._civ_recovering = False

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
        # Shield the token-remove on the graceful CONNECTED path too (A3-verifier
        # forward-note).  Now that recovery/shutdown can run this disconnect()
        # under asyncio cancellation (the lifecycle's teardown / SIGTERM
        # graceful-close cancels in-flight tasks), a pending cancel could
        # otherwise truncate the load-bearing token-remove mid-flight and leave
        # the radio's single-owner lock held.  ``asyncio.shield`` runs it to
        # completion even under a pending cancel, mirroring the already-shielded
        # token-remove in :meth:`release`.
        try:
            await asyncio.shield(self._send_token(0x01))
        except Exception:
            logger.debug("disconnect: token remove failed", exc_info=True)
        await h._ctrl_transport.disconnect()
        h._conn_state = RadioConnectionState.DISCONNECTED
        h._civ_stream_ready = False
        h._civ_recovering = False
        logger.info("Disconnected from %s:%d", h._host, h._port)

    # ------------------------------------------------------------------
    # Packet-mechanism seam for the unified session lifecycle (task A2)
    # ------------------------------------------------------------------
    #
    # These methods are the demoted, pure-mechanism surface the
    # ``CoreRadioSessionLifecycle`` policy layer drives.  They contain NO
    # retry/sleep/cooldown — that policy lives in the lifecycle.  A3 wires an
    # adapter (``ControlPhaseSessionMechanism`` below) so the lifecycle owns
    # CONNECTING↔COOLDOWN and ``ControlPhaseRuntime`` owns packet I/O only.

    async def connect_attempt(self) -> "AttemptResult":
        """Perform ONE connect attempt (auth + CI-V port); classify the outcome.

        Pure mechanism: no retry, no cooldown sleep.  The release obligation is
        considered registered the instant auth succeeds — the lifecycle's RAII
        discharges it via :meth:`release` on every exit path.

        Returns an :class:`~rigplane.runtime.session_lifecycle.AttemptResult`:

        * ``CONNECTED`` — ``_connect_once`` reached CONNECTED;
        * ``SESSION_NOT_READY`` — civ_port==0 / data-port discovery cooldown;
        * ``SESSION_BUSY_REJECT`` — status (or auth) error 0xFFFFFFFF —
          "previous session still active";
        * ``AUTH_CREDENTIALS`` — auth rejected with the *non-transient*
          credential error 0xFEFFFFFF.

        Classification rules (lifecycle Note 1; behaviour-preserving wiring):

        * The ONLY transient signal ``_connect_once`` emits is
          :class:`_DataPortDiscoveryCooldown` (CI-V data-port discovery timed out
          after a successful auth/status) → ``SESSION_NOT_READY``.  This mirrors
          the removed legacy ``connect()`` wrapper, which retried exactly this
          case (its ``_DATA_PORT_COOLDOWN_RETRIES`` loop) and let every other
          failure propagate.
        * **Note 1 (auth over-hard-fail fix):** ``_connect_once`` raises
          :class:`AuthenticationError` for ANY ``auth.success == False`` — which
          covers BOTH 0xFEFFFFFF (wrong credentials, hard) AND 0xFFFFFFFF
          (previous session active, transient).  Only 0xFEFFFFFF maps to the
          non-retryable ``AUTH_CREDENTIALS``; any other auth failure (notably the
          0xFFFFFFFF busy reject surfaced at the auth stage) maps to the
          transient ``SESSION_BUSY_REJECT`` so a transient auth glitch is retried
          rather than permanently failed.
        * Every other ``ConnectionError`` (the radio definitively rejected the
          session/codec after ``_connect_once`` exhausted its own within-attempt
          fallbacks; CI-V/control open failure; startup-readiness abort) is a
          genuine HARD error and is **propagated unchanged** — it is NOT
          downgraded to a silent transient retry.
        """
        from rigplane.runtime.session_lifecycle import AttemptOutcome, AttemptResult

        h = self._host
        try:
            await self._connect_once()
        except _DataPortDiscoveryCooldown:
            return AttemptResult(AttemptOutcome.SESSION_NOT_READY)
        except AuthenticationError:
            # Note 1: ONLY 0xFEFFFFFF is a hard credential failure.  Any other
            # auth failure (e.g. 0xFFFFFFFF "previous session active" surfaced at
            # the auth stage) is transient → cooldown-aware resident retry.
            if getattr(h, "_last_auth_error", 0) == 0xFEFFFFFF:
                return AttemptResult(AttemptOutcome.AUTH_CREDENTIALS)
            return AttemptResult(AttemptOutcome.SESSION_BUSY_REJECT)
        return AttemptResult(AttemptOutcome.CONNECTED)

    async def release(self) -> None:
        """Release the session UNCONDITIONALLY (token-remove + close + sockets).

        This is the guaranteed-release primitive (design §2.5).  Unlike
        :meth:`disconnect`, it does NOT early-return on
        ``conn_state != CONNECTED`` — a partially-claimed session (post-auth,
        pre-CONNECTED) is still released, closing graceful-close Holes 1/5/8.
        Idempotent: safe to call when nothing is claimed.
        """
        h = self._host
        # Best-effort teardown of any data/audio transports first (mirrors
        # disconnect ordering), then the always-sent token-remove.
        self._stop_watchdog()
        self._stop_reconnect()
        self._stop_token_renewal()
        self._close_pending_sockets()
        audio_t = getattr(h, "_audio_transport", None)
        if audio_t is not None:
            with _suppress(Exception):
                await self._send_audio_open_close(open_stream=False)
            with _suppress(Exception):
                await audio_t.disconnect()
            h._audio_transport = None
        civ_t = getattr(h, "_civ_transport", None)
        if civ_t is not None:
            with _suppress(Exception):
                await self._send_open_close(open_stream=False)
            with _suppress(Exception):
                await civ_t.disconnect()
            h._civ_transport = None
        # The token-remove is the load-bearing release: send it whenever a
        # control transport exists, regardless of conn_state.
        #
        # Note 2 (release truncated by cancel): when release runs on the SIGTERM
        # graceful-close path (``CoreRadioSessionLifecycle.request_shutdown`` →
        # teardown) a fresh cancellation may already be pending on this task.
        # The earlier ``await``s here (audio/CI-V disconnect) do NOT swallow
        # ``CancelledError`` — only ``Exception`` — so an un-shielded await
        # before the token-remove could be cancelled mid-flight and skip it.
        # ``asyncio.shield`` runs the token-remove to completion even under a
        # pending cancel, guaranteeing the radio is freed within the close
        # deadline.  We re-suppress here because ``shield`` re-raises the
        # outer ``CancelledError`` once the shielded coroutine finishes.
        ctrl_t = getattr(h, "_ctrl_transport", None)
        if ctrl_t is not None and h._token:
            with _suppress(Exception):
                await asyncio.shield(self._send_token(0x01))
        if ctrl_t is not None:
            with _suppress(Exception):
                await asyncio.shield(ctrl_t.disconnect())
        h._conn_state = RadioConnectionState.DISCONNECTED
        h._civ_stream_ready = False
        h._civ_recovering = False

    async def soft_reconnect(self) -> None:
        """Reconnect CI-V transport using existing control session."""
        h = self._host
        if h._civ_transport is not None:
            now = time.monotonic()
            last_data = getattr(h, "_last_civ_data_received", None)
            transport_open = (
                getattr(h._civ_transport, "_udp_transport", None) is not None
            )
            data_flowing = isinstance(last_data, (int, float)) and (
                now - float(last_data)
            ) <= getattr(h, "_civ_ready_idle_timeout", 3.0)
            if transport_open and data_flowing:
                h._conn_state = RadioConnectionState.CONNECTED
                h._civ_stream_ready = True
                h._civ_recovering = False
                h._last_civ_data_received = now
                # ``data_flowing`` being True implies ``last_data`` is numeric;
                # re-narrow for mypy so ``float(last_data)`` type-checks.
                assert isinstance(last_data, (int, float))
                logger.info(
                    "civ.soft_reconnect.noop",
                    extra={
                        "reason": "already_open_data_flowing",
                        "civ_idle_seconds": now - float(last_data),
                    },
                )
                return
            # Transport object exists but the stream is stalled (no data is
            # flowing).  Returning here is the freeze bug (#1217): recovery
            # would never progress past one attempt because the watchdog has
            # already exited and nothing re-arms it.  Tear the stalled
            # transport down so this call falls through to the genuine rebuild
            # path below, which re-establishes CI-V and re-arms the watchdog.
            logger.warning(
                "civ.soft_reconnect.already_open_stalled",
                extra={
                    "transport_open": transport_open,
                    "data_flowing": data_flowing,
                    "action": "tearing_down_stalled_transport_for_rebuild",
                },
            )
            await h._force_cleanup_civ()
            if h._civ_transport is not None:
                # Defensive: _force_cleanup_civ normally nulls the transport.
                # If a subclass/mock left it set, do not loop forever — bail.
                return
        if not h._ctrl_transport or not getattr(
            h._ctrl_transport, "_udp_transport", None
        ):
            logger.info("soft_reconnect: control transport gone, doing full connect")
            # The legacy ``connect()`` retry wrapper was removed in A3; the
            # lifecycle owns retry.  A full re-establish here is a single
            # mechanism attempt (``_connect_once``); the lifecycle's RECOVERING
            # accounting / exhaustion handles repeated failures.
            await self._connect_once()
            return

        h._conn_state = RadioConnectionState.CONNECTING
        h._civ_stream_ready = False
        h._civ_recovering = True

        # Tear down the audio transport BEFORE the CI-V rebuild and re-arm it
        # AFTER, mirroring the audio-before-civ order in ``disconnect()``.  Soft
        # reconnect is the repair path for a dropped LAN audio sub-stream too:
        # without an explicit teardown the stale audio-UDP socket FD leaks and
        # the next ``_ensure_audio_transport`` re-arm raises
        # ``RuntimeError: File descriptor ... is used by transport`` — RX never
        # recovers.  Snapshot live demand first so the re-arm can restore it.
        audio_runtime = getattr(h, "_audio_runtime", None)
        audio_snapshot = (
            audio_runtime.capture_snapshot() if audio_runtime is not None else None
        )
        teardown_audio = getattr(h, "_teardown_audio_transport", None)
        if teardown_audio is not None:
            try:
                await teardown_audio()
            except Exception:
                logger.debug("soft_reconnect: audio teardown failed", exc_info=True)

        from rigplane.transport import IcomTransport

        requested_local_port = getattr(h, "_civ_local_port", 0)
        h._civ_transport = IcomTransport()
        try:
            await h._civ_transport.connect(
                h._host,
                h._civ_port,
                local_host=getattr(h, "_local_bind_host", None),
                local_port=requested_local_port,
            )
        except OSError as exc:
            if requested_local_port and _is_address_in_use(exc):
                logger.warning(
                    "soft_reconnect: local CI-V port %d still busy, retrying with an ephemeral port",
                    requested_local_port,
                )
                h._civ_transport = IcomTransport()
                try:
                    await h._civ_transport.connect(
                        h._host,
                        h._civ_port,
                        local_host=getattr(h, "_local_bind_host", None),
                        local_port=0,
                    )
                except OSError as retry_exc:
                    h._civ_transport = None
                    ctrl_alive = getattr(h, "control_connected", False)
                    h._conn_state = (
                        RadioConnectionState.RECONNECTING
                        if ctrl_alive
                        else RadioConnectionState.DISCONNECTED
                    )
                    h._civ_stream_ready = False
                    h._civ_recovering = ctrl_alive
                    raise ConnectionError(
                        f"Failed to reconnect CI-V: {retry_exc}"
                    ) from retry_exc
            else:
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

        # Re-arm the audio transport on a fresh FD and restore any live demand.
        # Bounded/safe: failures are logged, never raised, so a CI-V soft
        # reconnect still succeeds even if audio cannot be re-established.
        ensure_audio = getattr(h, "_ensure_audio_transport", None)
        if ensure_audio is not None:
            try:
                await ensure_audio()
                if (
                    audio_runtime is not None
                    and audio_snapshot is not None
                    and getattr(h, "_auto_recover_audio", False)
                ):
                    await audio_runtime.recover(audio_snapshot)
            except Exception:
                logger.debug("soft_reconnect: audio re-arm failed", exc_info=True)

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
        contract = h._audio_stream_contract
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
            rx_codec=int(contract.rx_codec),
            tx_codec=int(contract.tx_codec),
            rx_sample_rate=contract.rx_sample_rate_hz,
            tx_sample_rate=contract.tx_sample_rate_hz,
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


class ControlPhaseSessionMechanism:
    """Production :class:`~rigplane.runtime.session_lifecycle.SessionMechanism`.

    Adapts a :class:`ControlPhaseRuntime` (the packet I/O mechanism) to the
    :class:`SessionMechanism` protocol the :class:`CoreRadioSessionLifecycle`
    policy layer drives (task A3).  One :meth:`connect_attempt` per lifecycle
    attempt; :meth:`release` for guaranteed teardown; :meth:`soft_reconnect_once`
    for recovery; a discovery-based :meth:`scan`.

    The lifecycle owns CONNECTING↔COOLDOWN retry; this adapter performs no
    sleeps or retries of its own beyond the packet-mechanism internals of
    ``_connect_once``.
    """

    def __init__(
        self,
        control_phase: ControlPhaseRuntime,
        *,
        discover_fn: "_DiscoverFn | None" = None,
    ) -> None:
        self._cp = control_phase
        # Presence-probe discovery is INJECTED from an upper layer (CLI/web).
        # ``rigplane.runtime`` may not import ``rigplane.backends`` (layered
        # architecture, enforced by import-linter), so the lifecycle's
        # ``scan()`` borrows a discovery callable supplied by the caller rather
        # than importing ``backends.discovery`` here.
        self._discover_fn = discover_fn

    async def connect_attempt(self) -> "AttemptResult":
        return await self._cp.connect_attempt()

    async def release(self) -> None:
        """Guaranteed release (token-remove + close).

        When a full CONNECTED session exists, route through the graceful
        :meth:`ControlPhaseRuntime.disconnect` (audio/CI-V teardown, generation
        advance, token-remove) so observable teardown behaviour is unchanged.
        Otherwise fall through to the unconditional :meth:`ControlPhaseRuntime.
        release`, which still token-removes a partially-claimed session (Holes
        1/5/8).  Both are idempotent.
        """
        h = self._cp._host
        if getattr(h, "_conn_state", None) is RadioConnectionState.CONNECTED:
            # Full graceful teardown (audio/CI-V stop, generation advance,
            # token-remove, ctrl disconnect).  This already sends token-remove,
            # so do NOT also run the partial-claim ``release`` afterwards.
            await self._cp.disconnect()
            return
        await self._cp.release()

    async def soft_reconnect_once(self) -> None:
        await self._cp.soft_reconnect()

    async def scan(
        self, targets: list[str] | None, *, timeout: float
    ) -> list["RadioPresence"]:
        from rigplane.runtime.session_lifecycle import RadioPresence

        if self._discover_fn is None:
            raise NotImplementedError(
                "lifecycle scan() requires a discovery callable; construct "
                "ControlPhaseSessionMechanism with discover_fn= from an upper "
                "layer (e.g. rigplane.backends.discovery.discover_lan_radios)."
            )
        found = await self._discover_fn(timeout=timeout)
        presences = [
            RadioPresence(
                host=str(r["host"]),
                remote_id=int(cast("SupportsInt", r["remote_id"])),
            )
            for r in found
        ]
        if targets is not None:
            wanted = set(targets)
            presences = [p for p in presences if p.host in wanted]
        return presences
