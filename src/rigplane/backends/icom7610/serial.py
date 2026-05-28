"""Serial adaptation layer for the IC-7610 backend."""

from __future__ import annotations

import logging

from ...exceptions import AudioFormatError, CommandError
from .._icom_serial_base import _IcomSerialRadioBase, _SERIAL_SCOPE_MIN_BAUD

logger = logging.getLogger(__name__)

__all__ = ["Icom7610SerialRadio"]


class Icom7610SerialRadio(_IcomSerialRadioBase):
    """IC-7610 backend wired to shared core over serial CI-V session driver."""

    _DEFAULT_MODEL = ""

    # ------------------------------------------------------------------
    # IC-7610 specific: stop_audio_rx_pcm delegates to stop_audio_rx_opus
    # ------------------------------------------------------------------

    async def stop_audio_rx_pcm(self) -> None:
        self._pcm_rx_user_callback = None
        await self.stop_audio_rx_opus()

    # ------------------------------------------------------------------
    # IC-7610 specific: Opus TX path
    # ------------------------------------------------------------------

    async def start_audio_tx_opus(self) -> None:
        self._check_connected()
        await self._serial_audio_driver.start_tx(
            sample_rate=self.audio_sample_rate,
            channels=self._serial_audio_channels_for_codec(),
            frame_ms=20,
        )

    async def push_audio_tx_opus(self, opus_data: bytes) -> None:
        self._check_connected()
        if not self._serial_audio_driver.tx_running:
            raise RuntimeError("Audio TX not started")
        payload = bytes(opus_data)
        if self._serial_codec_is_opus():
            transcoder = self._get_pcm_transcoder(
                sample_rate=self.audio_sample_rate,
                channels=self._serial_audio_channels_for_codec(),
                frame_ms=20,
            )
            payload = transcoder.opus_to_pcm(payload)
        await self._serial_audio_driver._push_tx_pcm(payload)

    async def stop_audio_tx_opus(self) -> None:
        await self._serial_audio_driver.stop_tx()
        self._pcm_tx_fmt = None

    # ------------------------------------------------------------------
    # IC-7610 specific: PCM TX with frame-size tracking
    # ------------------------------------------------------------------

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        # Signature mirrors the base ``AudioRuntimeMixin`` contract (``int |
        # None``) so subclassing does not violate the Liskov substitution
        # principle; ``None`` resolves to the serial-path defaults.
        if sample_rate is None:
            sample_rate = 48000
        if channels is None:
            channels = 1
        if frame_ms is None:
            frame_ms = 20
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

        # IC-7610 USB CODEC mic input is mono-only by hardware; the LAN path
        # enforces this by forcing txcodec to a mono value in _send_conninfo
        # (issue #794).  For the serial path we clamp channels to 1 here so
        # that PortAudio always opens the USB CODEC as a mono output stream.
        # Opening with channels=2 causes the IC-7610 ALC to behave erratically
        # for the first 5-10 seconds of TX (GH#1382 regression vs 0.16.4 where
        # the global default was PCM_1CH_16BIT → default_channels=1).
        tx_channels = 1

        self._check_connected()
        await self._serial_audio_driver.start_tx(
            sample_rate=sample_rate,
            channels=tx_channels,
            frame_ms=frame_ms,
        )
        self._pcm_tx_fmt = (sample_rate, tx_channels, frame_ms)

    async def push_audio_tx_pcm(
        self,
        pcm_bytes: bytes | bytearray | memoryview,
    ) -> None:
        self._check_connected()
        if self._pcm_tx_fmt is None:
            raise RuntimeError(
                "PCM TX not started; call start_audio_tx_pcm() before push_audio_tx_pcm()."
            )
        if not isinstance(pcm_bytes, (bytes, bytearray, memoryview)):
            raise AudioFormatError("PCM input must be bytes-like.")
        sample_rate, channels, frame_ms = self._pcm_tx_fmt
        if (sample_rate * frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )
        frame_samples = (sample_rate * frame_ms) // 1000
        expected = frame_samples * channels * 2
        frame = bytes(pcm_bytes)
        if len(frame) != expected:
            raise AudioFormatError(
                f"PCM frame size mismatch: expected {expected} bytes "
                f"({frame_ms}ms at {sample_rate}Hz, {channels}ch s16le), got {len(frame)}."
            )
        await self._serial_audio_driver._push_tx_pcm(frame)

    async def stop_audio_tx_pcm(self) -> None:
        await self.stop_audio_tx_opus()

    # ------------------------------------------------------------------
    # IC-7610 specific: more robust audio driver stop
    # ------------------------------------------------------------------

    async def _stop_serial_audio_driver(self) -> None:
        self._pcm_tx_fmt = None
        self._pcm_rx_user_callback = None
        self._opus_rx_user_callback = None
        try:
            await self._serial_audio_driver.stop_tx()
        except Exception:
            logger.debug("serial-audio: failed to stop TX path", exc_info=True)
        try:
            await self._serial_audio_driver.stop_rx()
        except Exception:
            logger.debug("serial-audio: failed to stop RX path", exc_info=True)

    # ------------------------------------------------------------------
    # IC-7610 specific: scope guardrail uses CommandError
    # ------------------------------------------------------------------

    def _ensure_scope_baud_guardrail(self) -> None:
        if self._serial_baudrate >= _SERIAL_SCOPE_MIN_BAUD:
            return

        msg = (
            "Scope over serial requires baudrate >= "
            f"{_SERIAL_SCOPE_MIN_BAUD} for stable command path; got baudrate="
            f"{self._serial_baudrate}. Set allow_low_baud_scope=True to override."
        )
        if not self._allow_low_baud_scope:
            raise CommandError(msg)

        if not self._low_baud_scope_warned:
            logger.warning("%s Running with override may increase timeout risk.", msg)
            self._low_baud_scope_warned = True
