"""AudioRuntimeMixin — audio streaming methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from typing import Callable

    from rigplane.audio import AudioPacket
    from rigplane.audio.route import AudioStreamContract, AudioStreamRequest

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from rigplane.audio._transcoder import (
    PcmAudioFormat,
    PcmOpusTranscoder,
    create_pcm_opus_transcoder,
)
from rigplane.audio import AudioStats, AudioStream
from rigplane.core.exceptions import AudioFormatError, ConnectionError
from rigplane.core.transport import IcomTransport
from rigplane.core.types import AudioCodec

logger = logging.getLogger(__name__)


class AudioRuntimeMixin(_MixinBase):  # type: ignore[misc]
    """Audio streaming methods for CoreRadio (mixin)."""

    # -- type stubs for attributes defined in CoreRadio.__init__ ---------
    _audio_stream: AudioStream | None
    _pcm_transcoder: PcmOpusTranscoder | None
    _pcm_transcoder_fmt: tuple[int, int, int] | None
    _pcm_tx_fmt: tuple[int, int, int] | None
    _pcm_rx_user_callback: Callable[[bytes | None], None] | None
    _opus_rx_user_callback: Callable[[AudioPacket | None], None] | None
    _audio_codec: AudioCodec
    _audio_tx_codec: AudioCodec
    _audio_sample_rate: int
    _audio_stream_request: AudioStreamRequest
    _audio_stream_contract: AudioStreamContract

    # ------------------------------------------------------------------
    # Audio streaming
    # ------------------------------------------------------------------

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        """Start receiving Opus audio from the radio.

        Connects the audio transport if not already connected,
        then begins streaming RX audio to the callback.

        Args:
            callback: Called with each :class:`AudioPacket`.
            jitter_depth: Jitter buffer depth (0 to disable, default 5).

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        self._check_connected()
        await self._ensure_audio_transport()
        assert self._audio_stream is not None
        self._opus_rx_user_callback = callback
        self._opus_rx_jitter_depth = jitter_depth
        await self._audio_stream.start_rx(callback, jitter_depth=jitter_depth)

    async def start_audio_rx_pcm(
        self,
        callback: Callable[[bytes | None], None],
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
        jitter_depth: int = 5,
    ) -> None:
        """Start receiving decoded PCM audio from the radio.

        This high-level API decodes incoming Opus RX frames to fixed-size
        PCM frames and delivers them to ``callback``. Gap placeholders are
        passed through as ``None`` when jitter buffering detects loss.

        Args:
            callback: Called with decoded PCM frame bytes, or ``None`` for gaps.
            sample_rate: PCM sample rate in Hz (Opus-supported values only).
            channels: PCM channels (1 or 2).
            frame_ms: Frame duration in ms (10/20/40/60).
            jitter_depth: Jitter buffer depth (0 to disable, default 5).

        Raises:
            ConnectionError: If not connected or audio port unavailable.
            TypeError: If callback is not callable or numeric args are not ints.
            ValueError: If ``jitter_depth`` is negative.
            AudioCodecBackendError: If Opus backend is unavailable.
            AudioFormatError: If PCM format is unsupported.
        """
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

        self._check_connected()

        # Validate codec/backend and PCM format before stream startup.
        self._get_pcm_transcoder(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )
        self._pcm_rx_user_callback = callback
        self._pcm_rx_jitter_depth = jitter_depth
        await self.start_audio_rx_opus(
            self._build_pcm_rx_callback(
                callback,
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            ),
            jitter_depth=jitter_depth,
        )

    async def stop_audio_rx_pcm(self) -> None:
        """Stop receiving decoded PCM audio from the radio."""
        self._pcm_rx_user_callback = None
        await self.stop_audio_rx_opus()

    async def stop_audio_rx_opus(self) -> None:
        """Stop receiving Opus audio from the radio."""
        self._opus_rx_user_callback = None
        if self._audio_stream is not None:
            await self._audio_stream.stop_rx()

    def _add_opus_rx_tap(
        self,
        callback: Callable[[AudioPacket | None], None],
    ) -> None:
        """Add an additional opus RX listener (non-exclusive, parallel to main callback)."""
        if self._audio_stream is not None:
            self._audio_stream.add_rx_tap(callback)

    def _remove_opus_rx_tap(
        self,
        callback: Callable[[AudioPacket | None], None],
    ) -> None:
        """Remove an opus RX tap."""
        if self._audio_stream is not None:
            self._audio_stream.remove_rx_tap(callback)

    async def start_audio_tx_opus(self) -> None:
        """Start transmitting Opus audio to the radio.

        Connects the audio transport if not already connected.

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        self._check_connected()
        await self._ensure_audio_transport()
        assert self._audio_stream is not None
        await self._audio_stream.start_tx()

    async def start_audio_tx_pcm(
        self,
        *,
        sample_rate: int | None = None,
        channels: int | None = None,
        frame_ms: int | None = None,
    ) -> None:
        """Start transmitting PCM audio to the radio.

        This high-level API validates PCM format settings and starts the
        underlying LAN audio TX stream.  Despite the legacy low-level method
        name, Icom LAN TX is negotiated as PCM_1CH_16BIT in conninfo, so PCM
        frames must be sent as raw s16le bytes rather than Opus frames.

        Args:
            sample_rate: PCM sample rate in Hz (Opus-supported values only).
            channels: PCM channels (1 or 2).
            frame_ms: Frame duration in ms (10/20/40/60).

        Raises:
            ConnectionError: If not connected or audio port unavailable.
            TypeError: If numeric args are not ints.
            AudioCodecBackendError: If TX codec is Opus and backend is unavailable.
            AudioFormatError: If PCM format is unsupported.
        """
        contract = getattr(self, "_audio_stream_contract", None)
        if sample_rate is None:
            sample_rate = getattr(contract, "tx_sample_rate_hz", None) or getattr(
                self, "_audio_tx_sample_rate", 48000
            )
        if channels is None:
            channels = getattr(contract, "tx_channels", None) or 1
        if frame_ms is None:
            frame_ms = 20

        for name, value in (
            ("sample_rate", sample_rate),
            ("channels", channels),
            ("frame_ms", frame_ms),
        ):
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}.")

        self._check_connected()

        tx_codec = getattr(self, "_audio_tx_codec", AudioCodec.PCM_1CH_16BIT)
        if tx_codec == AudioCodec.OPUS_1CH:
            self._get_pcm_transcoder(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
        else:
            # Validate PCM format before stream startup.  Direct Icom LAN TX
            # does not require libopus because conninfo negotiates
            # PCM_1CH_16BIT.
            PcmOpusTranscoder._validate_format(  # noqa: SLF001
                PcmAudioFormat(
                    sample_rate=sample_rate,
                    channels=channels,
                    frame_ms=frame_ms,
                )
            )
        await self.start_audio_tx_opus()
        self._pcm_tx_fmt = (sample_rate, channels, frame_ms)

    async def push_audio_tx_opus(self, opus_data: bytes) -> None:
        """Send an Opus-encoded audio frame to the radio.

        Args:
            opus_data: Opus-encoded audio data.

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If audio TX not started.
        """
        self._check_connected()
        if self._audio_stream is None:
            raise RuntimeError("Audio TX not started")
        await self._audio_stream.push_tx(opus_data)

    async def push_audio_tx_pcm(
        self,
        pcm_bytes: bytes | bytearray | memoryview,
    ) -> None:
        """Encode and send one PCM audio frame to the radio.

        Args:
            pcm_bytes: One fixed-size PCM frame (s16le, interleaved).

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If PCM TX not started with :meth:`start_audio_tx_pcm`.
            AudioFormatError: If frame type/size is invalid.
            AudioTranscodeError: If encode operation fails.
        """
        self._check_connected()
        if self._pcm_tx_fmt is None:
            raise RuntimeError(
                "PCM TX not started; call start_audio_tx_pcm() before push_audio_tx_pcm()."
            )
        sample_rate, channels, frame_ms = self._pcm_tx_fmt
        await self._push_audio_tx_pcm_internal(
            pcm_bytes,
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )

    async def stop_audio_tx_pcm(self) -> None:
        """Stop transmitting PCM audio to the radio."""
        await self.stop_audio_tx_opus()

    async def stop_audio_tx_opus(self) -> None:
        """Stop transmitting Opus audio to the radio."""
        if self._audio_stream is not None:
            await self._audio_stream.stop_tx()
        self._pcm_tx_fmt = None

    async def start_audio_opus(
        self,
        rx_callback: Callable[[AudioPacket | None], None],
        *,
        tx_enabled: bool = True,
        jitter_depth: int = 5,
    ) -> None:
        """Start full-duplex Opus audio (RX + optional TX).

        Convenience method that starts both RX and TX audio streams
        on the same transport.

        Args:
            rx_callback: Called with each :class:`AudioPacket` (or None for gaps).
            tx_enabled: Whether to also enable TX (default True).
            jitter_depth: Jitter buffer depth (0 to disable, default 5).

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        await self.start_audio_rx_opus(rx_callback, jitter_depth=jitter_depth)
        if tx_enabled:
            assert self._audio_stream is not None
            await self._audio_stream.start_tx()

    async def stop_audio_opus(self) -> None:
        """Stop all Opus audio streams (RX and TX)."""
        await self.stop_audio_tx_opus()
        await self.stop_audio_rx_opus()

    def get_audio_stats(self) -> dict[str, bool | int | float | str]:
        """Return runtime audio stats for the active stream.

        Returns a JSON-friendly dictionary with packet/loss/jitter/buffer/latency
        metrics. If no audio stream is active, returns a zeroed idle snapshot.
        """
        if self._audio_stream is None:
            return AudioStats.inactive().to_dict()
        return self._audio_stream.get_audio_stats()

    def _get_pcm_transcoder(
        self,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> PcmOpusTranscoder:
        """Get/create cached PCM<->Opus transcoder for internal PCM hooks."""
        key = (sample_rate, channels, frame_ms)
        if self._pcm_transcoder is not None and self._pcm_transcoder_fmt == key:
            return self._pcm_transcoder
        self._pcm_transcoder = create_pcm_opus_transcoder(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )
        self._pcm_transcoder_fmt = key
        return self._pcm_transcoder

    def _build_pcm_rx_callback(
        self,
        callback: Callable[[bytes | None], None],
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> Callable[[AudioPacket | None], None]:
        """Internal adapter: AudioPacket callback -> PCM callback."""

        def _on_audio_packet(packet: AudioPacket | None) -> None:
            if packet is None:
                callback(None)
                return
            pcm_frame = self._decode_audio_packet_to_pcm(
                packet,
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
            callback(pcm_frame)

        return _on_audio_packet

    def _decode_audio_packet_to_pcm(
        self,
        packet: AudioPacket,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> bytes:
        """Internal helper for future high-level RX PCM APIs."""
        transcoder = self._get_pcm_transcoder(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )
        return transcoder.opus_to_pcm(packet.data)

    async def _push_audio_tx_pcm_internal(
        self,
        pcm_data: bytes | bytearray | memoryview,
        *,
        sample_rate: int = 48000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> None:
        """Internal helper for future high-level TX PCM APIs."""
        fmt = PcmAudioFormat(
            sample_rate=sample_rate,
            channels=channels,
            frame_ms=frame_ms,
        )
        PcmOpusTranscoder._validate_format(fmt)  # noqa: SLF001
        if not isinstance(pcm_data, (bytes, bytearray, memoryview)):
            raise AudioFormatError("PCM input must be bytes-like.")
        frame = bytes(pcm_data)
        if len(frame) != fmt.frame_bytes:
            raise AudioFormatError(
                f"PCM frame size mismatch: expected {fmt.frame_bytes} bytes "
                f"({fmt.frame_ms}ms at {fmt.sample_rate}Hz, "
                f"{fmt.channels}ch s16le), got {len(frame)}."
            )

        tx_codec = getattr(self, "_audio_tx_codec", AudioCodec.PCM_1CH_16BIT)
        if tx_codec == AudioCodec.PCM_1CH_16BIT:
            # push_audio_tx_opus is the historical low-level packet push.  The
            # payload must match the negotiated Icom TX codec; direct Icom LAN
            # conninfo forces PCM_1CH_16BIT for TX, so send the PCM frame
            # unchanged.
            await self.push_audio_tx_opus(frame)
            return
        if tx_codec == AudioCodec.OPUS_1CH:
            transcoder = self._get_pcm_transcoder(
                sample_rate=sample_rate,
                channels=channels,
                frame_ms=frame_ms,
            )
            await self.push_audio_tx_opus(transcoder.pcm_to_opus(frame))
            return
        raise AudioFormatError(f"PCM TX is not supported for TX codec {tx_codec!r}.")

    @property
    def audio_codec(self) -> AudioCodec:
        """Configured audio codec."""
        return self._audio_codec

    @property
    def audio_sample_rate(self) -> int:
        """Configured audio sample rate in Hz."""
        return self._audio_sample_rate

    @property
    def audio_stream_request(self) -> "AudioStreamRequest":
        """Requested radio-native audio values before conninfo fallback."""
        return self._audio_stream_request

    @property
    def audio_stream_contract(self) -> "AudioStreamContract":
        """Effective radio-native audio values accepted for this connection."""
        return self._audio_stream_contract

    async def _ensure_audio_transport(self) -> None:
        """Connect the audio transport if not already connected."""
        if self._audio_stream is not None:
            return

        if self._audio_port == 0:
            raise ConnectionError("Audio port not available")

        self._audio_transport = IcomTransport()
        audio_sock = getattr(self, "_audio_sock_pending", None)
        try:
            await self._audio_transport.connect(
                self._host,
                self._audio_port,
                local_host=getattr(self, "_local_bind_host", None),
                local_port=getattr(self, "_audio_local_port", 0),
                sock=audio_sock,
            )
        except OSError as exc:
            if audio_sock is not None:
                audio_sock.close()
                self._audio_sock_pending = None
            self._audio_transport = None  # type: ignore[assignment]
            raise ConnectionError(
                f"Failed to connect audio port {self._audio_port}: {exc}"
            ) from exc
        else:
            if audio_sock is not None:
                self._audio_sock_pending = None

        self._audio_transport.start_ping_loop()
        self._audio_transport.start_retransmit_loop()
        self._audio_transport.start_idle_loop()

        # Per wfview, audio stream also uses OpenClose on its own UDP channel.
        await self._send_audio_open_close(open_stream=True)

        self._audio_stream = AudioStream(self._audio_transport)
        logger.info("Audio transport connected on port %d", self._audio_port)

        # Start EPIPE-storm watchdog for this transport session.
        self._start_audio_watchdog()
