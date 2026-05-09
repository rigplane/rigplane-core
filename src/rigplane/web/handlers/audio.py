"""Audio WebSocket handlers — broadcaster + per-client handler."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

from ..._audio_codecs import decode_ulaw_to_pcm16
from ..._audio_transcoder import PcmOpusTranscoder, create_pcm_opus_transcoder
from ...dsp.tap_registry import TapHandle, TapRegistry
from ...env_config import (
    get_audio_broadcaster_high_watermark,
)
from ...types import AudioCodec
from ..protocol import (  # noqa: TID251
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
    decode_json,
    encode_audio_frame,
    encode_json,
)
from ..websocket import WS_OP_BINARY, WS_OP_TEXT, WebSocketConnection  # noqa: TID251

if TYPE_CHECKING:
    from ...capabilities import CAP_AUDIO as _CAP_AUDIO_TYPE  # noqa: F401
    from ...dsp.pipeline import DSPPipeline
    from ...radio_protocol import Radio

from ...capabilities import CAP_AUDIO, CAP_LAN_DUAL_RX_AUDIO_ROUTING

__all__ = ["AudioBroadcaster", "AudioHandler"]

logger = logging.getLogger(__name__)


class _AudioPacketLike(Protocol):
    data: bytes


class _AudioSubscription(Protocol):
    async def start(self) -> None: ...

    def stop(self) -> None: ...

    def __aiter__(self) -> AsyncIterator[_AudioPacketLike | None]: ...


class _AudioBus(Protocol):
    def subscribe(self, name: str = "") -> _AudioSubscription: ...


class AudioBroadcaster:
    """Single-instance RX audio broadcaster shared by all AudioHandler clients.

    Uses :class:`~rigplane.audio_bus.AudioBus` to subscribe to the radio-native
    RX stream.  Browser WebSocket clients may receive a consumer-specific
    transport codec, while PCM taps continue to see decoded radio audio.
    """

    HIGH_WATERMARK: int = 10

    def __init__(self, radio: "Radio | None") -> None:
        self.HIGH_WATERMARK = get_audio_broadcaster_high_watermark()
        self._radio = radio
        self._clients: dict[int, asyncio.Queue[bytes]] = {}
        self._client_ws: dict[int, WebSocketConnection] = {}
        self._subscription: _AudioSubscription | None = None
        self._relay_task: asyncio.Task[None] | None = None
        self._seq: int = 0
        self._web_codec: int = AUDIO_CODEC_PCM16
        self._radio_codec: AudioCodec | None = None
        self._sample_rate: int = 48000
        self._channels: int = 1
        self._browser_opus_transcoder: PcmOpusTranscoder | None = None
        self._browser_opus_transcoder_key: tuple[int, int, int] | None = None
        self._browser_opus_warned: bool = False
        self._lock = asyncio.Lock()
        # Optional DSP pipeline (inserted between codec decode and tap/distribute).
        # NOTE: the DSP pipeline and the tap registry both operate on decoded
        # PCM16.  When the radio's native codec is Opus (IC-705 and future
        # Opus-only models) we pass the frame through un-decoded to preserve
        # wire quality, so neither DSP nor taps run.  See ``_refresh_codec_state``
        # for the one-shot warning that fires when this combination is detected,
        # and ``docs/internals/rfc-audio-v1-mini.md`` for the user-facing note.
        # Issue #762.
        self._dsp_pipeline: DSPPipeline | None = None
        # One-shot flag so the Opus-DSP warning log fires at most once per
        # broadcaster lifetime, regardless of whether DSP or codec is set first.
        self._dsp_opus_warned: bool = False
        # Multi-consumer PCM tap registry (replaces single _pcm_tap)
        self._tap_registry = TapRegistry()
        self._legacy_tap_handle: TapHandle | None = None
        # Flag raised by invalidate_codec_state(); checked at top of each
        # _relay_loop iteration to pick up mid-stream mono↔stereo switches
        # driven by the audio_config WS handler (issue #766, unblocks #721).
        self._codec_stale: bool = False

    async def subscribe(
        self, ws: WebSocketConnection | None = None
    ) -> asyncio.Queue[bytes]:
        """Register a new WebSocket client and start relaying if first."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.HIGH_WATERMARK)
        client_id = id(queue)
        async with self._lock:
            self._clients[client_id] = queue
            if ws is not None:
                self._client_ws[client_id] = ws
            # Start relay if no active subscription, or if relay task died
            relay_alive = self._relay_task is not None and not self._relay_task.done()
            if self._subscription is None or not relay_alive:
                # Clean up stale subscription/task if needed
                if self._subscription is not None and not relay_alive:
                    logger.info("audio-broadcaster: relay task dead, restarting")
                    if self._relay_task is not None:
                        self._relay_task.cancel()
                        self._relay_task = None
                    self._subscription.stop()
                    self._subscription = None
                if self._radio:
                    await self._start_relay()
        logger.info("audio-broadcaster: client added (total=%d)", len(self._clients))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        """Unregister a client and stop relay if last (unless PCM tap is active)."""
        client_id = id(queue)
        async with self._lock:
            self._clients.pop(client_id, None)
            self._client_ws.pop(client_id, None)
            if (
                not self._clients
                and self._subscription is not None
                and not self._tap_registry.active
            ):
                await self._stop_relay()
        logger.info("audio-broadcaster: client removed (total=%d)", len(self._clients))

    def set_pcm_tap(self, callback: "Callable[[bytes], None] | None") -> None:
        """Register a tap that receives decoded PCM16 audio data.

        Compatibility wrapper around :class:`TapRegistry`. Manages a
        single "legacy" tap slot. For new consumers prefer
        ``_tap_registry.register()`` directly.

        Args:
            callback: Function receiving PCM16 bytes, or None to unregister.
        """
        # Unregister previous legacy tap if any
        if self._legacy_tap_handle is not None:
            self._tap_registry.unregister(self._legacy_tap_handle)
            self._legacy_tap_handle = None
        if callback is not None:
            self._legacy_tap_handle = self._tap_registry.register("legacy", callback)

    def set_dsp_pipeline(self, pipeline: "DSPPipeline | None") -> None:
        """Attach or detach a DSP processing pipeline.

        When set, every decoded PCM16 frame is passed through
        :meth:`DSPPipeline.process_bytes` before tap distribution and
        client encoding.  Pass ``None`` to remove the pipeline.

        NOTE: DSP (and the PCM tap registry used by the FFT scope) are
        silently skipped when the radio's native codec is Opus.  Issue
        #762 — a one-shot WARNING is logged the first time this
        combination is observed; see :meth:`_maybe_warn_dsp_opus_gate`.
        """
        self._dsp_pipeline = pipeline
        if pipeline is not None:
            self._maybe_warn_dsp_opus_gate()

    def _maybe_warn_dsp_opus_gate(self) -> None:
        """Fire a one-shot WARNING if DSP is active on an Opus-native radio.

        Safe to call from either order: ``set_dsp_pipeline`` call site,
        or from codec refresh when the codec flips to Opus mid-stream.
        Issue #762.
        """
        if self._dsp_opus_warned:
            return
        if self._dsp_pipeline is None:
            return
        if self._radio_codec not in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return
        logger.warning(
            "audio-broadcaster: DSP pipeline is configured but the radio's "
            "native codec is Opus — DSP and FFT-scope tap dispatch are "
            "skipped to preserve wire quality.  See issue #762 / "
            "docs/internals/rfc-audio-v1-mini.md."
        )
        self._dsp_opus_warned = True

    async def ensure_relay(self) -> None:
        """Ensure the relay loop is running (for PCM tap consumers like FFT scope).

        Unlike :meth:`subscribe`, this does not create a client queue —
        it just guarantees that audio packets flow through the relay so
        that the PCM tap callback fires.
        """
        async with self._lock:
            relay_alive = self._relay_task is not None and not self._relay_task.done()
            if not relay_alive and self._radio:
                await self._start_relay()
                logger.info("audio-broadcaster: relay started for PCM tap")

    async def reap_dead_clients(self) -> int:
        """Remove clients whose WebSocket is no longer alive. Returns count removed."""
        async with self._lock:
            dead_ids = [
                cid for cid, ws in list(self._client_ws.items()) if not ws.is_alive()
            ]
            for cid in dead_ids:
                self._clients.pop(cid, None)
                self._client_ws.pop(cid, None)
            # Stop relay if no clients remain (and no PCM tap active)
            if (
                not self._clients
                and self._subscription is not None
                and not self._tap_registry.active
            ):
                await self._stop_relay()
            remaining = len(self._clients)
        if dead_ids:
            logger.info(
                "audio-broadcaster: reaped %d dead clients (total=%d)",
                len(dead_ids),
                remaining,
            )
        return len(dead_ids)

    def invalidate_codec_state(self) -> None:
        """Mark broadcaster's cached codec state as stale.

        The next ``_relay_loop`` iteration will call ``_refresh_codec_state``
        to pick up any radio-side codec / channel / sample-rate changes.
        Called by ``AudioHandler._handle_audio_config`` after a successful
        CI-V Phones L/R Mix set, since that can flip the radio from mono
        to stereo output (issue #766, unblocks #721).
        """
        self._codec_stale = True

    def _refresh_codec_state(self, *, first: bool = False) -> None:
        """Read radio's current codec / channels / sample rate into cache.

        Called from ``_start_relay`` (``first=True``) and from ``_relay_loop``
        whenever ``invalidate_codec_state`` has been called since the last
        refresh. Behavior-preserving extraction of the block previously
        inlined in ``_start_relay``.
        """
        # Resolve browser transport from the radio-native codec plus profile
        # consumer policy; this must not feed back into the radio conninfo codec.
        _codec = getattr(self._radio, "audio_codec", None)
        if isinstance(_codec, AudioCodec):
            self._radio_codec = _codec
            self._channels = (
                2
                if _codec
                in (
                    AudioCodec.PCM_2CH_16BIT,
                    AudioCodec.ULAW_2CH,
                    AudioCodec.OPUS_2CH,
                )
                else 1
            )
            self._web_codec = self._resolve_web_rx_codec(_codec)
            logger.info(
                "audio-broadcaster: radio codec=%s (0x%02x) → web_codec=0x%02x",
                _codec.name,
                int(_codec),
                self._web_codec,
            )
        elif first:
            logger.warning(
                "audio-broadcaster: no radio codec info, defaulting to PCM16"
            )
        _sr = getattr(self._radio, "audio_sample_rate", None)
        if isinstance(_sr, int) and not isinstance(_sr, bool) and _sr > 0:
            self._sample_rate = _sr
        logger.info(
            "audio-broadcaster: %s codec=0x%02x sr=%d ch=%d",
            "starting relay" if first else "codec state refreshed",
            self._web_codec,
            self._sample_rate,
            self._channels,
        )
        # Re-check DSP-on-Opus after the codec may have just flipped.
        # Issue #762.
        self._maybe_warn_dsp_opus_gate()

    def _resolve_web_rx_codec(self, radio_codec: AudioCodec) -> int:
        """Resolve browser RX transport codec from profile consumer policy."""
        if radio_codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return AUDIO_CODEC_OPUS

        default_codec = {
            AudioCodec.PCM_1CH_16BIT: AUDIO_CODEC_PCM16,
            AudioCodec.PCM_2CH_16BIT: AUDIO_CODEC_PCM16,
            AudioCodec.ULAW_1CH: AUDIO_CODEC_PCM16,
            AudioCodec.ULAW_2CH: AUDIO_CODEC_PCM16,
        }.get(radio_codec, AUDIO_CODEC_PCM16)

        backend_id = getattr(self._radio, "backend_id", None)
        if isinstance(backend_id, str) and backend_id != "rigplane":
            return default_codec

        profile = getattr(self._radio, "profile", None)
        transport = getattr(profile, "browser_rx_transport", None)
        transcode_to_opus = getattr(profile, "browser_rx_transcode_to_opus", None)
        if transport == "pcm":
            return AUDIO_CODEC_PCM16
        if transport == "opus":
            return AUDIO_CODEC_OPUS if transcode_to_opus is not False else default_codec
        if transport == "auto" and transcode_to_opus is True:
            return AUDIO_CODEC_OPUS
        return default_codec

    def _encode_browser_rx_frame(
        self,
        pcm_data: bytes,
        frame_ms: int,
    ) -> tuple[int, bytes]:
        """Apply browser-only RX transport encoding after PCM consumers run."""
        if self._web_codec != AUDIO_CODEC_OPUS:
            return self._web_codec, pcm_data
        if self._radio_codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return AUDIO_CODEC_OPUS, pcm_data

        key = (self._sample_rate, self._channels, frame_ms)
        try:
            if (
                self._browser_opus_transcoder is None
                or self._browser_opus_transcoder_key != key
            ):
                self._browser_opus_transcoder = create_pcm_opus_transcoder(
                    sample_rate=self._sample_rate,
                    channels=self._channels,
                    frame_ms=frame_ms,
                )
                self._browser_opus_transcoder_key = key
            return AUDIO_CODEC_OPUS, self._browser_opus_transcoder.pcm_to_opus(pcm_data)
        except Exception as exc:
            if not self._browser_opus_warned:
                logger.warning(
                    "audio: browser Opus transcode unavailable, emitting PCM16: %s",
                    exc,
                )
                self._browser_opus_warned = True
            self._browser_opus_transcoder = None
            self._browser_opus_transcoder_key = None
            return AUDIO_CODEC_PCM16, pcm_data

    async def _apply_phones_mix_off(self) -> None:
        """Force Phones L/R Mix = OFF so the LAN stream is separated stereo.

        Dual-RX routing contract (#792, epic #787): the frontend recovers
        ``focus`` × ``split_stereo`` by gating the splitter's L=MAIN /
        R=SUB outputs.  If the radio was left in Mix ON from a prior
        session or from the physical menu, it pre-sums the receivers
        before LAN transmission and the frontend can no longer isolate
        a single receiver.  Send 0x1A 05 00 72 00 once per relay start
        so new sessions always begin in a predictable state.

        Gated on the ``lan_dual_rx_audio_routing`` capability.  Declared
        only on IC-7610 — IC-9700 also has ``receiver_count=2`` but its
        menu layout does not include ``0x1A 05 00 72``, and FTX-1 runs
        on Yaesu CAT which has no ``send_civ`` at all.  Sending this
        CI-V on either would silent-NAK (IC-9700) or raise AttributeError
        (FTX-1).  Issue #799.
        """
        if not self._radio:
            return
        if CAP_LAN_DUAL_RX_AUDIO_ROUTING not in self._radio.capabilities:
            return
        try:
            await self._radio.send_civ(  # type: ignore[attr-defined]
                0x1A,
                sub=0x05,
                data=bytes([0x00, 0x72, 0x00]),
                wait_response=False,
            )
            logger.info("audio-broadcaster: Phones L/R Mix = OFF sent (1A 05 00 72 00)")
        except Exception:
            logger.warning(
                "audio-broadcaster: Phones L/R Mix init failed", exc_info=True
            )

    async def _start_relay(self) -> None:
        if not self._radio or CAP_AUDIO not in self._radio.capabilities:
            return

        await self._apply_phones_mix_off()
        self._refresh_codec_state(first=True)

        try:
            bus = self._radio.audio_bus  # type: ignore[attr-defined]
            self._subscription = cast(_AudioBus, bus).subscribe(name="web-audio")
            await self._subscription.start()
            self._relay_task = asyncio.create_task(self._relay_loop())
        except Exception:
            logger.exception("audio-broadcaster: failed to start relay")
            self._subscription = None

    async def _relay_loop(self) -> None:
        """Read packets from AudioBus subscription and fan out to WS clients."""
        if self._subscription is None:
            return
        try:
            async for pkt in self._subscription:
                if pkt is None:
                    continue
                if self._codec_stale:
                    self._refresh_codec_state()
                    self._codec_stale = False
                if self._seq < 3 or self._seq % 500 == 0:
                    logger.info(
                        "audio: rx packet #%d, web_codec=0x%02x, data=%d bytes",
                        self._seq,
                        self._web_codec,
                        len(pkt.data),
                    )

                # Decode audio data if needed
                audio_data = pkt.data
                if self._radio_codec in (AudioCodec.ULAW_1CH, AudioCodec.ULAW_2CH):
                    try:
                        audio_data = decode_ulaw_to_pcm16(audio_data)
                    except Exception as e:
                        logger.warning("audio: failed to decode ulaw data: %s", e)
                        # Fall back to original data
                        audio_data = pkt.data

                # Apply DSP pipeline if configured (operates on s16le PCM).
                # Opus-native radios bypass DSP to avoid decode + re-encode
                # quality loss on the wire; see issue #762 for the design
                # note and ``_maybe_warn_dsp_opus_gate`` for the one-shot
                # warning that surfaces this asymmetry to the operator.
                if self._dsp_pipeline is not None and self._radio_codec not in (
                    AudioCodec.OPUS_1CH,
                    AudioCodec.OPUS_2CH,
                ):
                    try:
                        audio_data = self._dsp_pipeline.process_bytes(
                            audio_data, self._sample_rate
                        )
                    except Exception:
                        logger.debug("audio: dsp pipeline error", exc_info=True)

                # Fan out PCM data to all registered taps (FFT scope, analyzers, etc.).
                # Opus-native radios do not feed taps — consumers get silence.
                # Same rationale as the DSP gate above.  Issue #762.
                if self._tap_registry.active and self._radio_codec not in (
                    AudioCodec.OPUS_1CH,
                    AudioCodec.OPUS_2CH,
                ):
                    self._tap_registry.feed(audio_data)

                # frame_ms is derived from the actual payload size (issue #765).
                # The hardcoded 20 ms here was the root cause of the 2026-04-16
                # companion crash (epic #764): IC-7610 dispatches 1364-byte PCM16
                # mono packets ~= 14.2 ms, not 20 ms, and downstream consumers
                # trusted the label.  Browser ignores frame_ms (rx-player.ts
                # sizes buffers from payload); companion + native clients need
                # the header to match reality.  See protocol.py docstring.
                _bytes_per_sample = 2
                _denom = max(1, self._sample_rate * self._channels * _bytes_per_sample)
                _frame_ms = max(1, min((len(audio_data) * 1000) // _denom, 255))
                _frame_codec, _frame_audio_data = self._encode_browser_rx_frame(
                    audio_data,
                    _frame_ms,
                )
                frame = encode_audio_frame(
                    MSG_TYPE_AUDIO_RX,
                    _frame_codec,
                    self._seq,
                    self._sample_rate // 100,
                    self._channels,
                    _frame_ms,
                    _frame_audio_data,
                )
                self._seq = (self._seq + 1) & 0xFFFF
                dead_ids: list[int] = []
                for client_id, q in list(self._clients.items()):
                    ws = self._client_ws.get(client_id)
                    if ws is not None and not ws.is_alive():
                        dead_ids.append(client_id)
                        continue
                    try:
                        q.put_nowait(frame)
                    except asyncio.QueueFull:
                        try:
                            q.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        try:
                            q.put_nowait(frame)
                        except asyncio.QueueFull:
                            pass
                for client_id in dead_ids:
                    self._clients.pop(client_id, None)
                    self._client_ws.pop(client_id, None)
                    logger.info("audio-broadcaster: removed dead client during relay")
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("audio-broadcaster: relay loop error")

    async def _stop_relay(self) -> None:
        if self._relay_task is not None:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
            self._relay_task = None
        if self._subscription is not None:
            self._subscription.stop()
            self._subscription = None
        logger.info("audio-broadcaster: relay stopped")


class AudioHandler:
    """Handler for the /api/v1/audio WebSocket channel.

    Streams RX audio from the radio to the browser as binary audio frames,
    and accepts TX audio frames from the browser to push to the radio.

    Control flow:
        Client sends JSON text: ``audio_start`` / ``audio_stop``
        Server sends binary audio frames continuously while RX is active.
        Client sends binary Opus frames while TX is active (after PTT on).

    Args:
        ws: Established WebSocket connection.
        radio: Radio protocol instance (may be None).
    """

    def __init__(
        self,
        ws: WebSocketConnection,
        radio: "Radio | None",
        broadcaster: "AudioBroadcaster | None" = None,
    ) -> None:
        self._ws = ws
        self._radio = radio
        self._broadcaster = broadcaster
        self._rx_active = False
        self._tx_active = False
        self._seq: int = 0
        self._frame_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._done = asyncio.Event()
        # Opus decoder for TX when radio uses PCM codec.
        # Created lazily on TX start so we pass the radio's negotiated sample rate
        # (otherwise a 24 kHz radio silently drops TX audio decoded at 48 kHz — #691).
        self._transcoder: PcmOpusTranscoder | None = None
        self._transcoder_rate: int = 0

    async def run(self) -> None:
        """Run the audio channel lifecycle.

        Reader and sender run as concurrent tasks. When EITHER exits
        (WS close, send timeout, error), the other is cancelled and
        cleanup (unsubscribe from broadcaster) runs unconditionally.
        """
        reader = asyncio.create_task(self._reader_loop(), name="audio-reader")
        sender = asyncio.create_task(self._sender_loop(), name="audio-sender")
        try:
            # Wait for the first task to finish — then cancel the other
            done, pending = await asyncio.wait(
                {reader, sender}, return_when=asyncio.FIRST_COMPLETED
            )
            # Log which task exited first
            for task in done:
                exc = task.exception() if not task.cancelled() else None
                if exc:
                    logger.warning(
                        "audio: %s exited with error: %s", task.get_name(), exc
                    )
                else:
                    logger.debug("audio: %s exited normally", task.get_name())
            # Cancel the remaining task and close WS to unblock any stuck recv()
            for task in pending:
                task.cancel()
            # Close WS to ensure recv() in reader raises EOF
            try:
                await self._ws.close(1001, "peer task exited")
            except Exception:
                pass
            for task in pending:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        except Exception:
            logger.exception("audio: handler error")
            reader.cancel()
            sender.cancel()
        finally:
            self._done.set()
            await self._stop_rx()
            if self._tx_active:
                self._tx_active = False
                logger.info("audio: TX cleanup on handler exit")
            logger.info("audio: handler finished")

    async def _reader_loop(self) -> None:
        """Read control messages and TX audio from client."""
        try:
            while True:
                opcode, payload = await self._ws.recv()
                if opcode == WS_OP_TEXT:
                    try:
                        msg = decode_json(payload.decode("utf-8"))
                    except ValueError:
                        continue
                    await self._handle_control(msg)
                elif opcode == WS_OP_BINARY:
                    # TX audio frame from browser
                    await self._handle_tx_audio(payload)
        except EOFError as exc:
            logger.info("audio: reader EOF: %s", exc)
        except asyncio.IncompleteReadError as exc:
            logger.info("audio: reader incomplete: %s", exc)

    async def _handle_control(self, msg: dict[str, Any]) -> None:
        """Handle audio_start / audio_stop messages."""
        logger.info("audio: control msg: %s", msg)
        msg_type = msg.get("type", "")
        direction = msg.get("direction", "rx")

        if msg_type == "audio_start":
            if direction == "rx":
                await self._start_rx()
            elif direction == "tx":
                if self._radio and CAP_AUDIO in self._radio.capabilities:
                    self._ensure_tx_transcoder()
                    try:
                        if self._tx_codec() == AudioCodec.PCM_1CH_16BIT:
                            await self._radio.start_audio_tx_pcm(  # type: ignore[attr-defined]
                                sample_rate=self._tx_sample_rate()
                            )
                        else:
                            await self._radio.start_audio_tx_opus()  # type: ignore[attr-defined]
                    except RuntimeError as exc:
                        if "Already transmitting" in str(exc):
                            logger.info("audio: TX already started by poller, reusing")
                        else:
                            raise
                self._tx_active = True
                logger.info("audio: TX active")
        elif msg_type == "audio_stop":
            if direction == "rx":
                await self._stop_rx()
            elif direction == "tx":
                if self._radio and CAP_AUDIO in self._radio.capabilities:
                    if self._tx_codec() == AudioCodec.PCM_1CH_16BIT:
                        await self._radio.stop_audio_tx_pcm()  # type: ignore[attr-defined]
                    else:
                        await self._radio.stop_audio_tx_opus()  # type: ignore[attr-defined]
                self._tx_active = False
                logger.info("audio: TX stopped")
        elif msg_type == "audio_config":
            await self._handle_audio_config(msg)

    # --------------------------------------------------------------
    # audio_config — MAIN/SUB focus + stereo split (issue #752, revised in #788)
    # --------------------------------------------------------------
    #
    # IC-7610 CI-V ``0x1A 05 00 72`` "Connectors > Phones > L/R Mix" —
    # per official CI-V reference p. 5: **data is 00 or 01 only**.
    #
    #   0x00 = Mix OFF → L=MAIN, R=SUB separated in the LAN stream.
    #   0x01 = Mix ON  → radio pre-sums MAIN + SUB to both channels
    #                    before transmission (LAN output becomes mono).
    #
    # Contract for dual-RX routing (epic #787, #792): the backend must
    # **always** keep Mix OFF while a 2-channel codec is active.  The
    # frontend recovers ``focus`` × ``split_stereo`` purely via WebAudio
    # gain/pan on the separated L=MAIN / R=SUB pair (#789).  If Mix is
    # ON the radio has already mixed the receivers, so ``focus=main`` /
    # ``focus=sub`` can no longer isolate a single receiver — the
    # frontend would play the summed signal instead.
    #
    # Both ``focus`` and ``split_stereo`` stay on the wire so the client
    # can persist + echo them, but neither round-trips to CI-V anymore.
    # Broadcaster start-up also forces Mix OFF once per session via
    # :meth:`AudioBroadcaster._apply_phones_mix_off` so the LAN stream
    # is separated from the first packet, independent of prior radio
    # state.
    _VALID_AUDIO_FOCUS: frozenset[str] = frozenset({"main", "sub", "both"})

    async def _handle_audio_config(self, msg: dict[str, Any]) -> None:
        """Apply MAIN/SUB audio focus + stereo split via CI-V Phones L/R Mix.

        Fire-and-forget on radios declaring the
        ``lan_dual_rx_audio_routing`` capability (IC-7610 only today —
        see ``capabilities.CAP_LAN_DUAL_RX_AUDIO_ROUTING``).  Echoes the
        applied config back on the WS so the client can confirm
        persistence.  Silent no-op on radios that don't declare the
        capability — e.g. IC-9700 (dual-RX but different menu layout)
        and FTX-1 (Yaesu CAT, no ``send_civ`` at all).  Issue #799.
        """
        focus = msg.get("focus", "")
        split_stereo = bool(msg.get("split_stereo", False))

        if focus not in self._VALID_AUDIO_FOCUS:
            await self._send_error(
                f"audio_config: invalid focus {focus!r}; "
                f"expected one of {sorted(self._VALID_AUDIO_FOCUS)}"
            )
            return

        if not self._radio:
            return  # no radio attached — cannot apply
        if CAP_LAN_DUAL_RX_AUDIO_ROUTING not in self._radio.capabilities:
            logger.debug(
                "audio_config: ignored — radio lacks lan_dual_rx_audio_routing"
            )
            return

        # Phones L/R Mix is always kept OFF (0x00) on dual-RX radios; see
        # class-level comment above and #792.  ``focus`` / ``split_stereo``
        # echo back for client-side persistence but don't drive CI-V.
        phones_byte = 0x00
        try:
            await self._radio.send_civ(  # type: ignore[attr-defined]
                0x1A,
                sub=0x05,
                data=bytes([0x00, 0x72, phones_byte]),
                wait_response=False,
            )
        except Exception:
            logger.warning("audio_config: CI-V send failed", exc_info=True)
            await self._send_error("audio_config: CI-V send failed")
            return

        logger.info(
            "audio_config: focus=%s split_stereo=%s → phones=0x%02X",
            focus,
            split_stereo,
            phones_byte,
        )
        # Radio may switch mono↔stereo output after this CI-V; broadcaster's
        # cached codec/channels must be refreshed on the next relay iteration
        # (issue #766, unblocks #721 stereo toggle).
        if self._broadcaster is not None:
            self._broadcaster.invalidate_codec_state()
        await self._send_json(
            {
                "type": "audio_config",
                "focus": focus,
                "split_stereo": split_stereo,
                "applied": True,
            }
        )

    async def _send_json(self, obj: dict[str, Any]) -> None:
        """Send a JSON message to the WS client."""
        try:
            await self._ws.send_text(encode_json(obj))
        except Exception:
            logger.debug("audio: _send_json failed", exc_info=True)

    async def _send_error(self, message: str) -> None:
        """Send an error message envelope to the WS client."""
        await self._send_json({"type": "error", "message": message})

    def _ensure_tx_transcoder(self) -> None:
        """Create (or recreate) the TX Opus→PCM transcoder at the radio's rate.

        TX-side fix for issue #691: previously the transcoder was constructed
        in ``__init__`` before the radio's negotiated ``audio_sample_rate`` was
        known, so the browser's 24 kHz Opus stream was decoded to 48 kHz PCM
        and the radio silently dropped TX audio. Called on ``audio_start
        direction=tx``, when the rate is guaranteed available.
        """
        contract = getattr(self._radio, "audio_stream_contract", None)
        sr = getattr(contract, "tx_sample_rate_hz", None)
        if not isinstance(sr, int) or isinstance(sr, bool) or sr <= 0:
            sr = getattr(self._radio, "audio_sample_rate", None)
        rate = (
            sr if isinstance(sr, int) and not isinstance(sr, bool) and sr > 0 else 48000
        )
        if self._transcoder is not None and self._transcoder_rate == rate:
            return
        try:
            self._transcoder = create_pcm_opus_transcoder(sample_rate=rate)
            self._transcoder_rate = rate
            logger.info("audio: TX transcoder ready at %d Hz", rate)
        except Exception:
            logger.debug("audio: TX transcoder unavailable (opus codec missing?)")
            self._transcoder = None
            self._transcoder_rate = 0

    def _tx_codec(self) -> AudioCodec | None:
        contract = getattr(self._radio, "audio_stream_contract", None)
        tx_codec = getattr(contract, "tx_codec", None)
        if tx_codec is not None:
            return tx_codec
        return getattr(self._radio, "audio_codec", None)

    def _tx_sample_rate(self) -> int:
        contract = getattr(self._radio, "audio_stream_contract", None)
        tx_sr = getattr(contract, "tx_sample_rate_hz", None)
        if isinstance(tx_sr, int) and not isinstance(tx_sr, bool) and tx_sr > 0:
            return tx_sr
        return 48000

    async def _start_rx(self) -> None:
        """Subscribe to audio broadcaster for RX frames."""
        if not self._broadcaster:
            return
        self._rx_active = True
        self._frame_queue = await self._broadcaster.subscribe(ws=self._ws)
        logger.info("audio: subscribed to RX broadcast")

    async def _stop_rx(self) -> None:
        """Unsubscribe from audio broadcaster."""
        if not self._rx_active or not self._broadcaster:
            return
        self._rx_active = False
        await self._broadcaster.unsubscribe(self._frame_queue)
        logger.info("audio: unsubscribed from RX broadcast")

    async def _handle_tx_audio(self, payload: bytes) -> None:
        """Forward TX audio from browser to radio."""
        if not self._tx_active:
            logger.debug(
                "audio: TX frame ignored (tx_active=False), size=%d", len(payload)
            )
            return
        if not self._radio:
            logger.warning("audio: TX frame ignored (no radio), size=%d", len(payload))
            return
        if CAP_AUDIO not in self._radio.capabilities:
            logger.warning(
                "audio: TX frame ignored (radio missing audio capability), size=%d",
                len(payload),
            )
            return
        if len(payload) < AUDIO_HEADER_SIZE:
            logger.warning(
                "audio: TX frame too small (%d < %d), ignoring",
                len(payload),
                AUDIO_HEADER_SIZE,
            )
            return
        # Extract audio data after 8-byte header (frontend sends Opus)
        opus_data = payload[AUDIO_HEADER_SIZE:]
        if opus_data:
            try:
                # Browser TX sends Opus; radio-native PCM TX must be decoded
                # according to the accepted TX contract, not the RX codec.
                if self._tx_codec() == AudioCodec.PCM_1CH_16BIT and self._transcoder:
                    try:
                        # Decode Opus → PCM16
                        pcm_data = self._transcoder.opus_to_pcm(opus_data)
                        await self._radio.push_audio_tx_pcm(pcm_data)  # type: ignore[attr-defined]
                        tx_data_desc = f"{len(pcm_data)} bytes pcm"
                    except Exception as e:
                        logger.warning(
                            "audio: Opus decode failed: %s, dropping frame", e
                        )
                        return
                else:
                    # Radio uses Opus or PCM_1CH_8BIT/etc → send Opus as-is
                    await self._radio.push_audio_tx_opus(opus_data)  # type: ignore[attr-defined]
                    tx_data_desc = f"{len(opus_data)} bytes opus"

                # Log every 50th frame to avoid spam
                if not hasattr(self, "_tx_frame_count"):
                    self._tx_frame_count = 0
                self._tx_frame_count += 1
                if self._tx_frame_count <= 3 or self._tx_frame_count % 50 == 0:
                    logger.info(
                        "audio: TX frame #%d pushed to radio (%s)",
                        self._tx_frame_count,
                        tx_data_desc,
                    )
            except Exception:
                logger.warning("audio: push TX error", exc_info=True)

    async def _sender_loop(self) -> None:
        """Send queued audio frames to the WebSocket client."""
        sent = 0
        try:
            while not self._done.is_set():
                try:
                    frame = await asyncio.wait_for(
                        self._frame_queue.get(),
                        timeout=0.5,
                    )
                    # Wrap send in timeout to detect dead WebSocket connections
                    # If send blocks >5s, connection is likely dead (half-open TCP)
                    try:
                        await asyncio.wait_for(
                            self._ws.send_binary(frame),
                            timeout=5.0,
                        )
                    except TimeoutError:
                        logger.warning(
                            "audio: send timeout after %d frames (dead connection), exiting",
                            sent,
                        )
                        break  # Exit loop, trigger cleanup in finally
                    sent += 1
                    if sent <= 3 or sent % 500 == 0:
                        logger.info(
                            "audio: sent frame #%d (%d bytes)", sent, len(frame)
                        )
                except TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.info("audio: sender cancelled after %d frames", sent)
        except (EOFError, OSError) as exc:
            logger.info("audio: sender stopped after %d frames: %s", sent, exc)
        except Exception:
            logger.exception("audio: sender error after %d frames", sent)
