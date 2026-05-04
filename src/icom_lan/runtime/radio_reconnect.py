"""Watchdog and reconnect loops for :class:`IcomRadio`.

Extracted from ``radio.py`` (issue #1259, Tier 3 wave 3 of #1063) to slim down
the god-object module. The two coroutines here read/write radio state through
internal attributes and helpers (``_check_connected``/``_connected``/
``_advance_civ_generation`` are call hubs that stay on ``IcomRadio`` per the
spike — they are invoked here via the ``radio`` parameter).

Behaviour is intentionally identical to the previous ``IcomRadio._watchdog_loop``
and ``IcomRadio._reconnect_loop`` methods; the public ``IcomRadio`` methods now
delegate here. Public API, reconnect timing and watchdog cadence are unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from icom_lan.runtime._connection_state import RadioConnectionState
from icom_lan.core.exceptions import AuthenticationError
from icom_lan.core.transport import IcomTransport

if TYPE_CHECKING:
    # Internal implementation module for IcomRadio — the TID251 ban targets
    # external consumers (web/, rigctld/), not radio.py's own helpers.
    from icom_lan.radio import IcomRadio  # type: ignore[attr-defined]  # noqa: TID251

logger = logging.getLogger(__name__)


async def watchdog_loop(radio: IcomRadio) -> None:
    """Monitor connection health via transport packet queue activity.

    If no packets are received for ``watchdog_timeout`` seconds,
    triggers a reconnect attempt.

    Reference: wfview icomudpaudio.cpp watchdog() — 30s timeout.
    """
    last_activity = time.monotonic()
    last_health_log = time.monotonic()
    last_rx_count = radio._ctrl_transport.rx_packet_count
    last_civ_count = radio._civ_transport.rx_packet_count if radio._civ_transport else 0
    try:
        while radio._connected:
            await asyncio.sleep(radio.WATCHDOG_CHECK_INTERVAL)
            if not radio._connected:
                break

            # Check if any transport has received new packets since last check
            ctrl_count = radio._ctrl_transport.rx_packet_count
            civ_count = (
                radio._civ_transport.rx_packet_count if radio._civ_transport else 0
            )
            if ctrl_count != last_rx_count or civ_count != last_civ_count:
                last_activity = time.monotonic()
                last_rx_count = ctrl_count
                last_civ_count = civ_count

            now = time.monotonic()
            idle = now - last_activity

            # Periodic health status log
            if now - last_health_log >= radio._WATCHDOG_HEALTH_LOG_INTERVAL:
                audio_t = getattr(radio, "_audio_transport", None)
                audio_rx = getattr(audio_t, "rx_packet_count", 0) if audio_t else 0
                audio_err = getattr(audio_t, "_udp_error_count", 0) if audio_t else 0
                logger.info(
                    "Transport health: ctrl_rx=%d civ_rx=%d audio_rx=%d audio_errors=%d idle=%.1fs",
                    ctrl_count,
                    civ_count,
                    audio_rx,
                    audio_err,
                    idle,
                )
                last_health_log = now

            if idle > radio._watchdog_timeout:
                logger.warning(
                    "Watchdog: no activity for %.1fs, triggering reconnect",
                    idle,
                )
                radio._conn_state = RadioConnectionState.RECONNECTING
                radio._civ_runtime.advance_generation("watchdog-timeout")
                radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
                return
    except asyncio.CancelledError:
        pass


async def reconnect_loop(radio: IcomRadio) -> None:
    """Attempt to reconnect with exponential backoff."""
    delay = radio._reconnect_delay
    attempt = 0
    try:
        while radio._conn_state != RadioConnectionState.DISCONNECTED:
            attempt += 1
            logger.info("Reconnect attempt %d (delay=%.1fs)", attempt, delay)
            try:
                radio._civ_runtime.advance_generation("reconnect-attempt")
                # Capture audio state for auto-recovery.
                audio_snapshot = radio._audio_runtime.capture_snapshot()
                # Clean up old transports
                radio._stop_token_renewal()
                if radio._audio_stream is not None:
                    try:
                        await radio._audio_stream.stop_rx()
                        await radio._audio_stream.stop_tx()
                    except Exception:
                        logger.debug(
                            "reconnect: audio_stream stop failed", exc_info=True
                        )
                    radio._audio_stream = None
                if radio._audio_transport is not None:
                    try:
                        await radio._audio_transport.disconnect()
                    except Exception:
                        logger.debug(
                            "reconnect: audio_transport disconnect failed",
                            exc_info=True,
                        )
                    radio._audio_transport = None
                if radio._civ_transport is not None:
                    try:
                        await radio._civ_transport.disconnect()
                    except Exception:
                        logger.debug(
                            "reconnect: civ_transport disconnect failed",
                            exc_info=True,
                        )
                    radio._civ_transport = None
                try:
                    await radio._send_token(0x01)
                except Exception:
                    logger.debug("reconnect: token remove failed", exc_info=True)
                try:
                    await radio._ctrl_transport.disconnect()
                except Exception:
                    logger.debug(
                        "reconnect: ctrl_transport disconnect failed", exc_info=True
                    )

                # Re-initialize transport
                radio._ctrl_transport = IcomTransport()
                await radio.connect()
                logger.info("Reconnected successfully after %d attempts", attempt)
                if radio._auto_recover_audio and audio_snapshot is not None:
                    await radio._audio_runtime.recover(audio_snapshot)
                return
            except (AuthenticationError, ValueError, TypeError) as exc:
                logger.error(
                    "Reconnect aborted — permanent error after %d attempt(s): %s",
                    attempt,
                    exc,
                )
                radio._conn_state = RadioConnectionState.DISCONNECTED
                return
            except Exception as exc:
                radio._conn_state = RadioConnectionState.RECONNECTING
                logger.warning("Reconnect attempt %d failed: %s", attempt, exc)
                await asyncio.sleep(delay)
                delay = min(delay * 2, radio._reconnect_max_delay)
    except asyncio.CancelledError:
        logger.info("Reconnect cancelled")


# Number of UDP errors on the audio transport that triggers audio-only recovery.
# The radio generates ~12 EPIPE/s after a power-cycle; 50 errors ≈ 4s of storm
# before we act — fast enough to bound log spam, lenient enough to ignore
# transient bursts during normal keep-alive renegotiation.
_AUDIO_ERROR_THRESHOLD: int = 50

# How often (seconds) the audio watchdog checks the error counter.
_AUDIO_WATCHDOG_INTERVAL: float = 5.0


async def audio_error_watchdog_loop(radio: "IcomRadio") -> None:  # type: ignore[name-defined]
    """Monitor audio transport for sustained EPIPE storms; recover audio-only.

    When the audio transport accumulates ``_AUDIO_ERROR_THRESHOLD`` UDP errors,
    the watchdog tears down the audio transport, reconnects it, and restores any
    active streams via the existing snapshot/recover path.  The ctrl and CI-V
    transports are left untouched.

    Architecture decision: dedicated audio watchdog (not extending the main
    watchdog) because the main watchdog uses ctrl+civ packet activity to reset
    its idle timer.  When ctrl+civ are healthy but audio is dead the main
    watchdog never fires — the audio watchdog fills that gap.
    """
    try:
        while radio._connected:
            await asyncio.sleep(_AUDIO_WATCHDOG_INTERVAL)
            if not radio._connected:
                break

            audio_t = getattr(radio, "_audio_transport", None)
            if audio_t is None:
                continue

            error_count = getattr(audio_t, "_udp_error_count", 0)
            if error_count < _AUDIO_ERROR_THRESHOLD:
                continue

            logger.warning(
                "Audio watchdog: EPIPE storm detected (%d errors), "
                "triggering audio-only recovery",
                error_count,
            )

            snapshot = radio._audio_runtime.capture_snapshot()

            # Tear down: stop streams, disconnect transport.
            audio_stream = getattr(radio, "_audio_stream", None)
            if audio_stream is not None:
                try:
                    await audio_stream.stop_rx()
                    await audio_stream.stop_tx()
                except Exception:
                    logger.debug(
                        "audio-watchdog: stream stop failed", exc_info=True
                    )
                radio._audio_stream = None

            try:
                await audio_t.disconnect()
            except Exception:
                logger.debug(
                    "audio-watchdog: transport disconnect failed", exc_info=True
                )
            radio._audio_transport = None

            if not radio._connected:
                break

            # Reconnect: new transport starts with _udp_error_count == 0.
            try:
                await radio._ensure_audio_transport()
                if radio._connected and snapshot is not None and radio._auto_recover_audio:
                    await radio._audio_runtime.recover(snapshot)
                if radio._connected:
                    logger.info("Audio watchdog: audio-only recovery succeeded")
            except Exception as exc:
                logger.warning(
                    "Audio watchdog: audio-only recovery failed: %s", exc
                )
            # Loop continues to monitor the new (or absent) transport.
    except asyncio.CancelledError:
        pass
