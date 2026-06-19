"""AudioRuntimeMixin — audio streaming methods extracted from CoreRadio.

Part of the radio.py decomposition (#505). All methods are accessed via
``IcomRadio`` which inherits this mixin.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from typing import Callable

    from rigplane.audio import AudioPacket
    from rigplane.audio.route import AudioStreamContract, AudioStreamRequest

    from .radio import CoreRadio as _MixinBase  # type: ignore[attr-defined]
else:
    _MixinBase = object

from rigplane.audio._codecs import decode_ulaw_to_pcm16
from rigplane.audio._transcoder import (
    PcmAudioFormat,
    PcmOpusTranscoder,
    create_pcm_opus_transcoder,
)
from rigplane.audio import AudioStats, AudioStream
from rigplane.audio.pcm import PcmFrame
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

    # -- PCM-spine ingress state (MOR-591) — lazily initialized ----------
    _pcm_rx_taps: list[Callable[[PcmFrame | None], None]]
    _pcm_ingress_seq: int
    _pcm_ingress_transcoder: PcmOpusTranscoder
    _pcm_ingress_transcoder_fmt: tuple[int, int, int]
    _pcm_ingress_opus_dead: bool

    # ------------------------------------------------------------------
    # Neutral AudioTransport surface (MOR-532 epic, MOR-539)
    # ------------------------------------------------------------------

    async def start_rx(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        """Start receiving audio from the radio (codec-neutral).

        Connects the audio transport if not already connected, then begins
        streaming RX audio to the callback. Packets carry ``data`` encoded
        per :attr:`audio_codec`.

        Args:
            callback: Called with each :class:`AudioPacket`.
            jitter_depth: Jitter buffer depth (0 to disable, default 5).
                Implementation-specific widening of the minimal
                ``AudioTransport.start_rx(callback)`` signature.

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        self._check_connected()
        await self._ensure_audio_transport()
        assert self._audio_stream is not None
        self._opus_rx_user_callback = callback
        self._opus_rx_jitter_depth = jitter_depth
        self._arm_pcm_ingress()
        await self._audio_stream.start_rx(callback, jitter_depth=jitter_depth)

    async def stop_rx(self) -> None:
        """Stop receiving audio from the radio (codec-neutral)."""
        self._opus_rx_user_callback = None
        if self._audio_stream is not None:
            await self._audio_stream.stop_rx()

    async def start_tx(self) -> None:
        """Open the TX path, resolving format from the negotiated contract.

        Codec-neutral (no format arguments): when the negotiated TX codec is
        ``PCM_1CH_16BIT`` (direct Icom LAN conninfo) this follows the
        contract-driven :meth:`start_audio_tx_pcm` path; otherwise the raw
        stream-start (legacy opus) path.

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        if self.audio_tx_codec == AudioCodec.PCM_1CH_16BIT:
            await self.start_audio_tx_pcm()
        else:
            await self._start_tx_stream()

    async def push_tx(self, data: bytes) -> None:
        """Send one audio frame encoded per :attr:`audio_tx_codec`.

        Args:
            data: Wire-codec audio frame bytes.

        Raises:
            ConnectionError: If not connected.
            RuntimeError: If audio TX not started.
        """
        self._check_connected()
        if self._audio_stream is None:
            raise RuntimeError("Audio TX not started")
        await self._audio_stream.push_tx(data)

    async def stop_tx(self) -> None:
        """Close the TX path (codec-neutral)."""
        if self._audio_stream is not None:
            await self._audio_stream.stop_tx()
        self._pcm_tx_fmt = None

    async def _start_tx_stream(self) -> None:
        """Raw TX stream start (the legacy ``start_audio_tx_opus`` body).

        Deliberately does NOT touch ``_pcm_tx_fmt``: recovery snapshots rely
        on it staying ``None`` for opus-only TX users.

        Raises:
            ConnectionError: If not connected or audio port unavailable.
        """
        self._check_connected()
        await self._ensure_audio_transport()
        assert self._audio_stream is not None
        await self._audio_stream.start_tx()

    # ------------------------------------------------------------------
    # PCM-spine ingress: decode-at-ingress dual-publish (MOR-591, ADR §3.5)
    # ------------------------------------------------------------------

    def add_pcm_rx_tap(self, callback: Callable[[PcmFrame | None], None]) -> None:
        """Register a PCM-spine listener fed decoded frames at LAN ingress.

        The LAN adapter decodes the radio-negotiated RX wire codec
        (PCM16 / uLaw / Opus) to s16le ONCE and feeds every registered
        tap a :class:`PcmFrame` per delivered RX packet (``None`` for
        jitter-buffer gap placeholders) — in parallel with, never
        instead of, the legacy :class:`AudioPacket` callback
        (dual-publish during the spine migration, tenet T1). With no
        taps registered the decode is skipped entirely, so the default
        AudioPacket-only path stays byte- and cost-identical.
        """
        taps = getattr(self, "_pcm_rx_taps", None)
        if taps is None:
            taps = []
            self._pcm_rx_taps = taps
        if callback not in taps:
            taps.append(callback)

    def remove_pcm_rx_tap(self, callback: Callable[[PcmFrame | None], None]) -> None:
        """Remove a PCM-spine listener (no-op if not registered)."""
        taps = getattr(self, "_pcm_rx_taps", None)
        if taps is None:
            return
        try:
            taps.remove(callback)
        except ValueError:
            pass

    def _arm_pcm_ingress(self) -> None:
        """Arm the PCM ingress tap for a new RX session (MOR-591).

        Registers a single parallel RX tap on the LAN stream (duck-typed:
        stream doubles without ``add_rx_tap`` simply skip the PCM spine)
        and resets the per-session monotonic frame sequence. The legacy
        AudioPacket callback handed to ``AudioStream.start_rx`` is never
        wrapped or replaced — dual-publish, not substitution.
        """
        self._pcm_ingress_seq = 0
        self._pcm_ingress_opus_dead = False
        add_rx_tap = getattr(self._audio_stream, "add_rx_tap", None)
        if add_rx_tap is not None:
            add_rx_tap(self._on_pcm_ingress_packet)

    def _on_pcm_ingress_packet(self, packet: AudioPacket | None) -> None:
        """LAN-stream RX tap: decode once at ingress, fan out PcmFrames.

        Runs for every delivered RX packet, including jitter-buffer gap
        placeholders (forwarded as ``None``). Never raises — the stream
        RX loop has no exception guard around tap dispatch.
        """
        taps = getattr(self, "_pcm_rx_taps", None)
        if not taps:
            return
        frame: PcmFrame | None
        if packet is None:
            frame = None
        else:
            try:
                frame = self._decode_pcm_ingress(packet)
            except Exception:
                logger.debug("PCM ingress: decode failed", exc_info=True)
                return
            if frame is None:
                return
        for tap in list(taps):
            try:
                tap(frame)
            except Exception:
                logger.debug("PCM ingress: tap error", exc_info=True)

    def _decode_pcm_ingress(self, packet: AudioPacket) -> PcmFrame | None:
        """Decode one wire packet to s16le per the negotiated RX codec.

        Returns ``None`` for wire codecs without an s16le mapping here
        (8-bit PCM — no shipping profile negotiates it) and for Opus
        when the codec backend is unavailable; the legacy AudioPacket
        carrier keeps flowing either way.
        """
        codec = self.audio_codec
        if codec in (AudioCodec.PCM_1CH_16BIT, AudioCodec.PCM_2CH_16BIT):
            channels = 2 if codec == AudioCodec.PCM_2CH_16BIT else 1
            payload = packet.data  # already s16le — zero-copy passthrough
        elif codec in (AudioCodec.ULAW_1CH, AudioCodec.ULAW_2CH):
            channels = 2 if codec == AudioCodec.ULAW_2CH else 1
            payload = decode_ulaw_to_pcm16(packet.data)
        elif codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            channels = 2 if codec == AudioCodec.OPUS_2CH else 1
            opus_payload = self._decode_opus_ingress(packet.data, channels)
            if opus_payload is None:
                return None
            payload = opus_payload
        else:
            return None
        seq = self._pcm_ingress_seq
        self._pcm_ingress_seq = seq + 1
        return PcmFrame(
            sample_rate=self.audio_sample_rate,
            channels=channels,
            payload=payload,
            seq=seq,
        )

    def _decode_opus_ingress(self, data: bytes, channels: int) -> bytes | None:
        """Decode one Opus wire frame to s16le with a session-cached decoder.

        Uses a DEDICATED transcoder (not the ``_get_pcm_transcoder``
        cache) so the stateful Opus decoder is never evicted by the
        user-facing PCM RX/TX APIs. A failed backend init dead-flags the
        ingress for the session (reset on the next ``start_rx``) instead
        of retrying per frame.
        """
        if getattr(self, "_pcm_ingress_opus_dead", False):
            return None
        fmt = (self.audio_sample_rate, channels, 20)
        transcoder = getattr(self, "_pcm_ingress_transcoder", None)
        if (
            transcoder is None
            or getattr(self, "_pcm_ingress_transcoder_fmt", None) != fmt
        ):
            try:
                transcoder = create_pcm_opus_transcoder(
                    sample_rate=fmt[0], channels=fmt[1], frame_ms=fmt[2]
                )
            except Exception:
                logger.warning(
                    "PCM ingress: Opus decode unavailable — "
                    "PcmFrame publishing disabled for this RX session",
                    exc_info=True,
                )
                self._pcm_ingress_opus_dead = True
                return None
            self._pcm_ingress_transcoder = transcoder
            self._pcm_ingress_transcoder_fmt = fmt
        try:
            return transcoder.opus_to_pcm(data)
        except Exception:
            logger.debug("PCM ingress: Opus frame decode failed", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Audio streaming (legacy opus-family delegates + PCM conveniences)
    # ------------------------------------------------------------------

    async def start_audio_rx_opus(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int = 5,
    ) -> None:
        """Start receiving Opus audio from the radio.

        Back-compat delegate for :meth:`start_rx` (MOR-539).
        """
        await self.start_rx(callback, jitter_depth=jitter_depth)

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
        """Stop receiving Opus audio from the radio.

        Back-compat delegate for :meth:`stop_rx` (MOR-539).
        """
        await self.stop_rx()

    async def start_audio_tx_opus(self) -> None:
        """Start transmitting Opus audio to the radio.

        Back-compat delegate for the raw stream start (MOR-539). Unlike the
        neutral :meth:`start_tx`, this never routes through the PCM path —
        it must not set ``_pcm_tx_fmt`` (recovery snapshot semantics).
        """
        await self._start_tx_stream()

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

        # Explicit per-arg validation (rather than a loop) so the type checker
        # can narrow each value from ``int | None`` to ``int`` for the format
        # construction and the ``_pcm_tx_fmt`` tuple below.
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
            raise TypeError(
                f"sample_rate must be an int, got {type(sample_rate).__name__}."
            )
        if isinstance(channels, bool) or not isinstance(channels, int):
            raise TypeError(f"channels must be an int, got {type(channels).__name__}.")
        if isinstance(frame_ms, bool) or not isinstance(frame_ms, int):
            raise TypeError(f"frame_ms must be an int, got {type(frame_ms).__name__}.")

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
        """Send one wire-codec audio frame to the radio.

        Back-compat delegate for :meth:`push_tx` (MOR-539).
        """
        await self.push_tx(opus_data)

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
        """Stop transmitting Opus audio to the radio.

        Back-compat delegate for :meth:`stop_tx` (MOR-539).
        """
        await self.stop_tx()

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
    def audio_tx_codec(self) -> AudioCodec:
        """Effective TX codec (MOR-532 codec descriptor surface).

        Prefers the negotiated ``AudioStreamContract.tx_codec`` when a
        contract exists, falling back to the internal ``_audio_tx_codec``
        default (``PCM_1CH_16BIT``) — the same precedence resolved in
        ``IcomRadio.__init__``. Consumed by later MOR-532 epic steps.
        """
        contract = getattr(self, "_audio_stream_contract", None)
        tx_codec = getattr(contract, "tx_codec", None)
        if tx_codec is not None:
            return AudioCodec(tx_codec)
        return getattr(self, "_audio_tx_codec", AudioCodec.PCM_1CH_16BIT)

    @property
    def audio_duplex_mode(self) -> str:
        """Duplex capability (MOR-532 duplex descriptor surface).

        The Icom LAN UDP audio stream is genuinely full-duplex: RX frames
        keep flowing while TX is active, and ``stop_tx`` reverts the stream
        state to RECEIVING. Single source of duplex policy for later MOR-532
        epic steps.
        """
        return "full"

    @property
    def audio_setup_order(self) -> Literal["rx_first", "tx_first", "atomic"]:
        """Setup ordering descriptor (MOR-575, ADR §3.3).

        Derived from :attr:`audio_duplex_mode` — single source of truth,
        so the two descriptors never drift. The LAN UDP stream is
        ``"full"``-duplex, but ``stop_tx`` reverts the stream state from
        TRANSMITTING back to RECEIVING, so RX must be armed before TX →
        ``"rx_first"``. ``"exclusive"`` would map to ``"atomic"`` (one
        duplex stream — setup does not decompose into rx/tx-first);
        ``"half"`` or any unexpected/raising duplex mode degrades to the
        ``"rx_first"`` safe default. Consumed by the AudioSession
        (``audio/session.py`` ``_setup_order``) to sequence RX/TX arming.
        """
        try:
            mode = self.audio_duplex_mode
        except Exception:
            return "rx_first"
        if mode == "exclusive":
            return "atomic"
        # "full", "half", and anything unexpected → rx_first (safe default).
        return "rx_first"

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

    async def _teardown_audio_transport(self) -> None:
        """Tear down the audio stream and transport, releasing the UDP FD.

        Stops any active RX/TX stream, disconnects the audio transport (which
        closes the underlying asyncio datagram transport and frees its socket
        FD), and clears both ``_audio_stream`` and ``_audio_transport`` so a
        follow-up :meth:`_ensure_audio_transport` rebuilds from a clean slate.

        This is the teardown half of the EPIPE-storm recovery (``radio_recon
        nect.audio_error_watchdog_loop``), extracted so reconnect paths that
        rebuild only the CI-V transport can also re-arm audio without leaking
        the stale audio-UDP FD (``RuntimeError: File descriptor ... is used by
        transport`` on the next re-arm). Idempotent and exception-safe — every
        step is best-effort so a partially-dead transport still ends up fully
        cleared.
        """
        audio_stream = self._audio_stream
        if audio_stream is not None:
            try:
                await audio_stream.stop_rx()
                await audio_stream.stop_tx()
            except Exception:
                logger.debug("audio teardown: stream stop failed", exc_info=True)
            self._audio_stream = None

        audio_transport = getattr(self, "_audio_transport", None)
        if audio_transport is not None:
            try:
                await audio_transport.disconnect()
            except Exception:
                logger.debug(
                    "audio teardown: transport disconnect failed", exc_info=True
                )
            self._audio_transport = None  # type: ignore[assignment]

    async def _ensure_audio_transport(self) -> None:
        """Connect the audio transport if not already connected.

        If a previous audio transport is still attached and either the stream
        was already torn down while the transport lingered, or the transport is
        a real :class:`IcomTransport` whose underlying UDP datagram transport
        is gone/closed (``_udp_transport is None``), tear it down first so the
        stale socket FD is released before a fresh ``connect`` reserves a new
        one.  Without this guard a half-dead transport would leak its FD and
        the rebuild would raise
        ``RuntimeError: File descriptor ... is used by transport``.

        Transport objects that do not expose ``_udp_transport`` at all (e.g.
        test mocks/stubs) are intentionally *not* treated as stale — they fall
        through to the ``if self._audio_stream is not None: return``
        short-circuit below.
        """
        audio_transport = getattr(self, "_audio_transport", None)
        if audio_transport is not None and (
            self._audio_stream is None
            or (
                hasattr(audio_transport, "_udp_transport")
                and audio_transport._udp_transport is None
            )
        ):
            await self._teardown_audio_transport()

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
        except BaseException as exc:
            if audio_sock is not None:
                audio_sock.close()
                self._audio_sock_pending = None
            self._audio_transport = None  # type: ignore[assignment]
            if isinstance(exc, OSError):
                raise ConnectionError(
                    f"Failed to connect audio port {self._audio_port}: {exc}"
                ) from exc
            raise
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
