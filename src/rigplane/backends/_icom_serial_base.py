"""Shared base class for Icom serial (USB CI-V) radio backends.

Consolidates the connection lifecycle, audio plumbing, scope guardrails,
CIV watchdog, and soft-reconnect logic that is identical across all
serial-backed radios (IC-705, IC-7300, IC-9700, IC-7610).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING, Callable, Protocol

from .._connection_state import RadioConnectionState
from ..audio import AudioPacket
from ..commands import parse_ack_nak, scope_off as _scope_off_cmd
from ..exceptions import AudioFormatError, CommandError, ConnectionError
from ..radio import CoreRadio
from ..types import AudioCodec, ScopeCompletionPolicy, get_audio_capabilities

if TYPE_CHECKING:
    from .icom7610.drivers.serial_civ_link import SerialCivLink
    from .icom7610.drivers.serial_session import SerialSessionDriver
    from ..profiles import RadioProfile

logger = logging.getLogger(__name__)
_AUDIO_CAPABILITIES = get_audio_capabilities()
_DEFAULT_AUDIO_CODEC = _AUDIO_CAPABILITIES.default_codec
_DEFAULT_AUDIO_SAMPLE_RATE = _AUDIO_CAPABILITIES.default_sample_rate_hz
_TWO_CHANNEL_CODECS = {
    AudioCodec.PCM_2CH_8BIT,
    AudioCodec.PCM_2CH_16BIT,
    AudioCodec.ULAW_2CH,
    AudioCodec.OPUS_2CH,
}
_SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS = 50.0
_SERIAL_SCOPE_MIN_BAUD = 115200


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class _SerialAudioDriver(Protocol):
    async def start_rx(
        self,
        callback: Callable[[bytes], None] | None = None,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def stop_rx(self) -> None: ...

    async def start_tx(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None: ...

    async def stop_tx(self) -> None: ...

    async def _push_tx_pcm(self, frame: bytes) -> None: ...

    @property
    def tx_running(self) -> bool: ...


class _IcomSerialRadioBase(CoreRadio):
    """Base for all Icom serial-backend radios.

    Subclasses must set ``_DEFAULT_MODEL`` as a class variable.
    """

    _DEFAULT_MODEL: str = ""
    _SERIAL_WATCHDOG_INTERVAL_S = 0.2
    _SERIAL_WATCHDOG_RETRY_S = 0.5

    def __init__(
        self,
        *,
        device: str,
        baudrate: int = 115200,
        radio_addr: int | None = None,
        timeout: float = 5.0,
        audio_codec: AudioCodec | int = _DEFAULT_AUDIO_CODEC,
        audio_sample_rate: int = _DEFAULT_AUDIO_SAMPLE_RATE,
        rx_device: str | None = None,
        tx_device: str | None = None,
        ptt_mode: str = "civ",
        profile: "RadioProfile | str | None" = None,
        model: str | None = None,
        allow_low_baud_scope: bool = False,
        civ_link: SerialCivLink | None = None,
        session_driver: SerialSessionDriver | None = None,
        audio_driver: _SerialAudioDriver | None = None,
    ) -> None:
        from .icom7610.drivers.serial_civ_link import SerialCivLink
        from .icom7610.drivers.serial_session import SerialSessionDriver
        from ..audio.usb_driver import UsbAudioDriver

        if session_driver is not None and civ_link is not None:
            raise ValueError("Provide either civ_link or session_driver, not both.")
        super().__init__(
            host=device,
            port=0,
            username="",
            password="",
            radio_addr=radio_addr,
            timeout=timeout,
            audio_codec=audio_codec,
            audio_sample_rate=audio_sample_rate,
            profile=profile,
            model=model or self._DEFAULT_MODEL or None,
        )
        self._serial_device = device
        self._serial_baudrate = baudrate
        self._serial_rx_device_override = rx_device
        self._serial_tx_device_override = tx_device
        if ptt_mode != "civ":
            raise ValueError(
                "Unsupported serial PTT mode. Only 'civ' is currently supported."
            )
        self._serial_ptt_mode = ptt_mode
        self._allow_low_baud_scope = allow_low_baud_scope or _env_bool(
            "ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD",
            default=False,
        )
        self._low_baud_scope_warned = False
        serial_min_interval_ms = float(
            os.environ.get(
                "ICOM_SERIAL_CIV_MIN_INTERVAL_MS",
                f"{_SERIAL_DEFAULT_CIV_MIN_INTERVAL_MS}",
            )
        )
        if serial_min_interval_ms <= 0:
            raise ValueError("ICOM_SERIAL_CIV_MIN_INTERVAL_MS must be > 0")
        self._civ_min_interval = serial_min_interval_ms / 1000.0
        serial_link = civ_link or SerialCivLink(device=device, baudrate=baudrate)
        self._serial_session = session_driver or SerialSessionDriver(serial_link)
        self._serial_audio_driver = audio_driver or UsbAudioDriver(
            rx_device=rx_device,
            tx_device=tx_device,
            serial_port=device,
            sample_rate=audio_sample_rate,
            channels=self._serial_audio_channels_for_codec(),
            frame_ms=20,
            backend=None,  # default PortAudioBackend
        )
        self._serial_audio_seq = 0

    # ------------------------------------------------------------------
    # Backend identity
    # ------------------------------------------------------------------

    @property
    def backend_id(self) -> str:
        """Stable backend family identifier — ``"icom_serial"`` for serial CI-V."""
        return "icom_serial"

    # ------------------------------------------------------------------
    # Connection properties
    # ------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        if self._conn_state != RadioConnectionState.CONNECTED:
            return False
        if self._civ_transport is None:
            return False
        return self._serial_session.connected

    @property
    def control_connected(self) -> bool:
        return self._serial_session.connected

    @property
    def radio_ready(self) -> bool:
        if not self.connected:
            return False
        if self._civ_recovering or not self._civ_stream_ready:
            return False
        return self._serial_session.ready

    # ------------------------------------------------------------------
    # Connect / disconnect / reconnect
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        if self.connected:
            return

        self._conn_state = RadioConnectionState.CONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = False
        self._last_status_error = 0
        self._last_status_disconnected = False
        try:
            await self._serial_session.connect()
        except Exception as exc:
            self._conn_state = RadioConnectionState.DISCONNECTED
            self._civ_stream_ready = False
            self._civ_recovering = False
            raise ConnectionError(
                f"Failed to connect serial session on {self._serial_device}: {exc}"
            ) from exc

        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
        self._civ_transport = self._serial_session.civ_transport  # type: ignore[assignment]
        self._advance_civ_generation("serial-connect")
        self._civ_last_waiter_gc_monotonic = time.monotonic()
        self._last_civ_data_received = time.monotonic()
        self._start_civ_rx_pump()
        self._start_civ_data_watchdog()
        self._start_civ_worker()

        self._conn_state = RadioConnectionState.CONNECTED
        self._civ_stream_ready = self._serial_session.ready
        self._civ_recovering = not self._civ_stream_ready
        logger.info(
            "Connected to %s over serial (%s @ %d baud)",
            self.model,
            self._serial_device,
            self._serial_baudrate,
        )

    async def soft_disconnect(self) -> None:
        await self.disconnect()

    async def disconnect(self) -> None:
        # Always stop watchdog first to avoid orphan retry loops on failed reconnects.
        await self._stop_civ_data_watchdog()
        await self._stop_serial_audio_driver()
        if (
            self._conn_state != RadioConnectionState.CONNECTED
            and not self._serial_session.connected
        ):
            self._conn_state = RadioConnectionState.DISCONNECTED
            self._civ_stream_ready = False
            self._civ_recovering = False
            return
        if self._conn_state != RadioConnectionState.CONNECTED:
            await self._stop_civ_worker()
            await self._stop_civ_rx_pump()
            await self._serial_session.disconnect()
            self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
            self._civ_transport = None
            self._conn_state = RadioConnectionState.DISCONNECTED
            self._civ_stream_ready = False
            self._civ_recovering = False
            return
        await super().disconnect()
        await self._serial_session.disconnect()
        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]

    async def soft_reconnect(self) -> None:
        if self._serial_session.ready and self._civ_transport is not None:
            return

        self._conn_state = RadioConnectionState.RECONNECTING
        self._civ_stream_ready = False
        self._civ_recovering = True
        self._advance_civ_generation("serial-soft-reconnect")
        await self._stop_civ_worker()
        await self._stop_civ_rx_pump()
        await self._serial_session.disconnect()

        try:
            await self._serial_session.connect()
        except Exception as exc:
            # Keep recovery state so watchdog can continue retries.
            self._conn_state = RadioConnectionState.RECONNECTING
            self._civ_stream_ready = False
            self._civ_recovering = True
            raise ConnectionError(
                f"Failed to reconnect serial session on {self._serial_device}: {exc}"
            ) from exc

        self._ctrl_transport = self._serial_session.control_transport  # type: ignore[assignment]
        self._civ_transport = self._serial_session.civ_transport  # type: ignore[assignment]
        self._civ_last_waiter_gc_monotonic = time.monotonic()
        self._last_civ_data_received = time.monotonic()
        self._start_civ_rx_pump()
        self._start_civ_worker()

        self._conn_state = RadioConnectionState.CONNECTED
        self._civ_stream_ready = self._serial_session.ready
        self._civ_recovering = not self._civ_stream_ready
        if self._on_reconnect is not None:
            try:
                self._on_reconnect()
            except Exception:
                logger.debug(
                    "serial soft_reconnect: _on_reconnect callback failed",
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Scope
    # ------------------------------------------------------------------

    async def enable_scope(
        self,
        *,
        output: bool = True,
        policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.VERIFY,
        timeout: float = 5.0,
    ) -> None:
        self._check_connected()
        self._ensure_scope_baud_guardrail()
        await super().enable_scope(output=output, policy=policy, timeout=timeout)

    async def disable_scope(
        self, *, policy: ScopeCompletionPolicy | str = ScopeCompletionPolicy.FAST
    ) -> None:
        await super().disable_scope(policy=policy)
        pol = ScopeCompletionPolicy(policy)
        wait_resp = pol == ScopeCompletionPolicy.STRICT
        resp = await self._send_civ_raw(
            _scope_off_cmd(to_addr=self._radio_addr),
            wait_response=wait_resp,
        )
        if wait_resp and resp is not None and parse_ack_nak(resp) is False:
            raise CommandError("Radio rejected scope disable")

    # ------------------------------------------------------------------
    # Audio RX
    # ------------------------------------------------------------------

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable and accept AudioPacket | None.")
        if isinstance(jitter_depth, bool) or not isinstance(jitter_depth, int):
            raise TypeError(
                f"jitter_depth must be an int, got {type(jitter_depth).__name__}."
            )
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        self._check_connected()

        self._opus_rx_user_callback = callback
        self._opus_rx_jitter_depth = jitter_depth

        sample_rate = self.audio_sample_rate
        channels = self._serial_audio_channels_for_codec()
        frame_ms = 20
        transcoder = (
            self._get_pcm_transcoder(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
            if self._serial_codec_is_opus()
            else None
        )

        def _on_pcm_frame(pcm_frame: bytes) -> None:
            payload = pcm_frame
            if transcoder is not None:
                try:
                    payload = transcoder.pcm_to_opus(pcm_frame)
                except Exception:
                    logger.warning(
                        "serial-audio: failed to encode PCM frame to Opus",
                        exc_info=True,
                    )
                    return
            packet = AudioPacket(
                ident=0x9781,
                send_seq=self._serial_audio_seq,
                data=payload,
            )
            self._serial_audio_seq = (self._serial_audio_seq + 1) & 0xFFFF
            callback(packet)

        await self._serial_audio_driver.start_rx(
            _on_pcm_frame,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_rx_opus(self) -> None:
        self._opus_rx_user_callback = None
        await self._serial_audio_driver.stop_rx()

    async def start_audio_rx_pcm(
        self,
        callback: Callable[[bytes | None], None],
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
        jitter_depth: int = 5,
    ) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable and accept bytes | None.")
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
            ("jitter_depth", jitter_depth),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if jitter_depth < 0:
            raise ValueError(f"jitter_depth must be >= 0, got {jitter_depth}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        self._check_connected()
        self._pcm_rx_user_callback = callback
        self._pcm_rx_jitter_depth = jitter_depth

        def _on_pcm_frame(pcm_frame: bytes) -> None:
            callback(pcm_frame)

        await self._serial_audio_driver.start_rx(
            _on_pcm_frame,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_rx_pcm(self) -> None:
        self._pcm_rx_user_callback = None
        await self._serial_audio_driver.stop_rx()

    # ------------------------------------------------------------------
    # Audio TX
    # ------------------------------------------------------------------

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> None:
        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )

        self._check_connected()
        await self._serial_audio_driver.start_tx(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_tx_pcm(self) -> None:
        await self._serial_audio_driver.stop_tx()

    async def _push_pcm_tx(self, frame: bytes) -> None:
        if not isinstance(frame, bytes):
            raise TypeError(f"frame must be bytes, got {type(frame).__name__}.")
        if len(frame) == 0:
            raise ValueError("frame must not be empty.")

        self._check_connected()
        await self._serial_audio_driver._push_tx_pcm(frame)

    # ------------------------------------------------------------------
    # Serial stubs (no-ops for serial transport)
    # ------------------------------------------------------------------

    async def _send_open_close(self, *, open_stream: bool) -> None:
        _ = open_stream
        return None

    async def _send_token(self, magic: int) -> None:
        _ = magic
        return None

    # ------------------------------------------------------------------
    # CIV data watchdog
    # ------------------------------------------------------------------

    def _start_civ_data_watchdog(self) -> None:
        _existing_watchdog = getattr(self, "_civ_data_watchdog_task", None)
        if _existing_watchdog is not None and not _existing_watchdog.done():
            return
        self._civ_data_watchdog_task = asyncio.create_task(
            self._serial_civ_watchdog_loop(),
            name="serial-civ-watchdog",
        )

    async def _stop_civ_data_watchdog(self) -> None:
        task = getattr(self, "_civ_data_watchdog_task", None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._civ_data_watchdog_task = None

    async def _serial_civ_watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._SERIAL_WATCHDOG_INTERVAL_S)
                if self._conn_state not in (
                    RadioConnectionState.CONNECTED,
                    RadioConnectionState.RECONNECTING,
                ):
                    continue
                if self._serial_session.ready:
                    self._civ_stream_ready = True
                    self._civ_recovering = False
                    self._conn_state = RadioConnectionState.CONNECTED
                    self._last_civ_data_received = time.monotonic()
                    continue

                self._civ_stream_ready = False
                self._civ_recovering = True
                try:
                    await self.soft_reconnect()
                except Exception:
                    logger.warning(
                        "serial-civ-watchdog: soft reconnect failed",
                        exc_info=True,
                    )
                    await asyncio.sleep(self._SERIAL_WATCHDOG_RETRY_S)
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _stop_serial_audio_driver(self) -> None:
        await self._serial_audio_driver.stop_rx()
        await self._serial_audio_driver.stop_tx()

    def _ensure_scope_baud_guardrail(self) -> None:
        if self._serial_baudrate >= _SERIAL_SCOPE_MIN_BAUD:
            return
        if not self._allow_low_baud_scope:
            if not self._low_baud_scope_warned:
                logger.warning(
                    "Scope disabled at low baud rate (%d < %d). "
                    "Set allow_low_baud_scope=True to override or set "
                    "ICOM_SERIAL_SCOPE_ALLOW_LOW_BAUD=1.",
                    self._serial_baudrate,
                    _SERIAL_SCOPE_MIN_BAUD,
                )
                self._low_baud_scope_warned = True
            raise ConnectionError(
                f"Scope unavailable at {self._serial_baudrate} baud "
                f"(minimum {_SERIAL_SCOPE_MIN_BAUD}). "
                f"Set allow_low_baud_scope=True to override."
            )

    def _serial_audio_channels_for_codec(self) -> int:
        return 2 if self._audio_codec in _TWO_CHANNEL_CODECS else 1

    def _serial_codec_is_opus(self) -> bool:
        return self._audio_codec in {
            AudioCodec.OPUS_1CH,
            AudioCodec.OPUS_2CH,
        }

    async def _ensure_audio_started(self) -> None:
        pass

    async def _ensure_audio_stopped(self) -> None:
        pass


__all__ = [
    "_IcomSerialRadioBase",
    "_SerialAudioDriver",
    "_env_bool",
    "_SERIAL_SCOPE_MIN_BAUD",
]
