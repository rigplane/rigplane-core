"""Audio WebSocket handlers — broadcaster + per-client handler."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, Protocol, cast

from ..._audio_codecs import decode_ulaw_to_pcm16
from ..._audio_transcoder import PcmOpusTranscoder, create_pcm_opus_transcoder
from ...audio.bus import STAGE_RX_POST_DSP
from ...audio.session import RxSubscription
from ...audio.usb_driver import AudioAlreadyStartedError
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
    MSG_TYPE_AUDIO_TX,
    decode_json,
    encode_audio_frame,
    encode_json,
)
from ..transport import Connection  # noqa: TID251
from ..websocket import WS_OP_BINARY, WS_OP_TEXT  # noqa: TID251

if TYPE_CHECKING:
    from ...audio.session import TxLease
    from ...capabilities import CAP_AUDIO as _CAP_AUDIO_TYPE  # noqa: F401
    from ...dsp.pipeline import DSPPipeline
    from ...radio_protocol import Radio

from ...capabilities import CAP_AUDIO, CAP_LAN_DUAL_RX_AUDIO_ROUTING

__all__ = ["AudioBroadcaster", "AudioHandler"]

logger = logging.getLogger(__name__)

_TX_CLEANUP_STOP_TIMEOUT_SECONDS = 2.0

# Adaptive egress codec controller windows (MOR-588, ADR §3.6).
# Degradation must be SUSTAINED for DEGRADE_WINDOW_S before PCM16→Opus
# engages; Opus→PCM16 requires a fully clean CLEAN_WINDOW_S; DWELL_S is
# the minimum hold after ANY switch (anti-flap). Per-instance copies on
# the broadcaster make them injectable for deterministic tests.
DEGRADE_WINDOW_S = 3.0
CLEAN_WINDOW_S = 30.0
DWELL_S = 10.0

# Fixed Opus egress frame duration (MOR-596).  Radio RX packets are not
# Opus-frame-aligned (IC-7610 LAN @48k stereo ≈1280 B ≈6.67 ms), while
# ``PcmOpusTranscoder`` accepts exactly one fixed frame per encode call —
# per-client PCM is therefore reframed into exact 20 ms frames before encode.
OPUS_EGRESS_FRAME_MS = 20


@dataclass
class _AdaptiveEgressState:
    """Per-client adaptive egress controller state (MOR-588, ADR §3.6).

    Exists ONLY for browser WS clients that declared Opus decode
    capability (``preferred_rx_codec=opus``) on an adaptive-enabled
    broadcaster. The initial codec is always PCM16 — Opus is never the
    initial codec and engages only on sustained link degradation.
    """

    codec: int = AUDIO_CODEC_PCM16
    # time.monotonic() of the last codec switch (dwell anchor); None
    # until the first switch.
    last_switch: float | None = None
    # Start of the current continuous degradation episode; reset when
    # evidence stops for longer than the degrade window.
    degrade_since: float | None = None
    # Most recent degradation evidence (clean-window anchor).
    last_evidence: float | None = None
    # High-water marks of the cumulative MOR-585 counters; a counter
    # rising past its mark is one unit of degradation evidence.
    seen_underruns: int = 0
    seen_queue_drops: int = 0


def _is_benign_tx_restart(exc: RuntimeError) -> bool:
    """True when TX start failed only because the stream is already open.

    The Icom LAN stream and the USB driver raise
    :class:`~rigplane.audio.usb_driver.AudioAlreadyStartedError` (a
    ``RuntimeError`` subclass, MOR-563), meaning the poller (or a prior
    client) already opened TX and the handler can simply reuse it —
    re-raising would close the audio WebSocket (MOR-541 review note,
    MOR-544). The substring fallback covers not-yet-migrated raisers that
    still signal the same condition with a bare ``RuntimeError``.
    """
    if isinstance(exc, AudioAlreadyStartedError):
        return True
    text = str(exc).lower()
    return "already transmitting" in text or "already started" in text


def _parse_preferred_rx_codec(msg: dict[str, Any]) -> int | None:
    value = msg.get("preferred_rx_codec")
    if value in ("pcm16", "pcm", "raw"):
        return AUDIO_CODEC_PCM16
    if value == "opus":
        return AUDIO_CODEC_OPUS
    return None


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

    def __init__(
        self,
        radio: "Radio | None",
        *,
        on_client_count_change: Callable[[], None] | None = None,
        adaptive_egress: bool = False,
    ) -> None:
        self.HIGH_WATERMARK = get_audio_broadcaster_high_watermark()
        self._radio = radio
        self._on_client_count_change = on_client_count_change
        self._clients: dict[int, asyncio.Queue[bytes]] = {}
        self._client_ws: dict[int, Connection] = {}
        self._client_rx_codec: dict[int, int] = {}
        # RX source handle: a session-routed RxSubscription when the radio
        # owns an AudioSession (MOR-608 — registers RX demand so the
        # MOR-581 watchdog covers browser-only listeners), or a legacy
        # bus AudioSubscription for radios without one. Both yield the
        # same AudioPacket|None stream via ``async for``.
        self._subscription: _AudioSubscription | RxSubscription | None = None
        self._relay_task: asyncio.Task[None] | None = None
        self._seq: int = 0
        self._web_codec: int = AUDIO_CODEC_PCM16
        self._radio_codec: AudioCodec | None = None
        self._sample_rate: int = 48000
        self._channels: int = 1
        # Per-client egress encoder pool (MOR-584, ADR §3.6): each Opus
        # WS client owns a dedicated transcoder (Opus encoders are
        # stateful), keyed by client id and torn down on disconnect.
        # Value: (transcoder, (sample_rate, channels, frame_ms)).
        self._client_opus_transcoders: dict[
            int, tuple[PcmOpusTranscoder, tuple[int, int, int]]
        ] = {}
        # Per-client PCM reframing accumulators for Opus egress (MOR-596):
        # s16le bytes buffered until a full 20 ms frame is available.
        # Cleared on unsubscribe/reap, adaptive switch to PCM16, and any
        # codec-state refresh (sample rate / channel changes).
        self._client_opus_pcm_buffers: dict[int, bytearray] = {}
        # Per-client link-quality state (MOR-585, ADR §3.6): the latest
        # client-reported ``audio_stats`` snapshot plus the server-side
        # drop-oldest eviction counter for the bounded WS queue.  Stats
        # collection only — read by the step-19 adaptive egress codec
        # controller; nothing here changes codec selection or behavior.
        self._client_link_quality: dict[int, dict[str, int | float]] = {}
        self._client_queue_drops: dict[int, int] = {}
        # Adaptive egress codec controller (MOR-588, ADR §3.6): per-client
        # PCM16↔Opus switching driven by the MOR-585 link-quality signals.
        # INERT when ``adaptive_egress`` is off (the default) — egress
        # stays the static MOR-584 per-connection choice and a client's
        # codec never changes mid-stream. Windows/dwell and the clock are
        # per-instance so tests can drive them deterministically.
        self._adaptive_egress = adaptive_egress
        self._adaptive_degrade_window_s: float = DEGRADE_WINDOW_S
        self._adaptive_clean_window_s: float = CLEAN_WINDOW_S
        self._adaptive_dwell_s: float = DWELL_S
        self._adaptive_monotonic: Callable[[], float] = time.monotonic
        self._client_adaptive: dict[int, _AdaptiveEgressState] = {}
        self._ack_tasks: set[asyncio.Task[None]] = set()
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
        # Named RX tap stages (MOR-565): the broadcaster hosts ``rx.post_dsp``
        # — decoded PCM16 after the optional DSP pipeline. This is the
        # pre-existing multi-consumer tap registry, renamed into the
        # stage scheme; ``_tap_registry`` stays as a compat alias for
        # existing consumers (CW auto-tuner, tests). The pre-DSP ``rx.pcm``
        # stage lives on AudioBus (see ``rigplane.audio.bus``).
        self._stage_taps: dict[str, TapRegistry] = {STAGE_RX_POST_DSP: TapRegistry()}
        self._tap_registry = self._stage_taps[STAGE_RX_POST_DSP]
        self._legacy_tap_handle: TapHandle | None = None
        # Flag raised by invalidate_codec_state(); checked at top of each
        # _relay_loop iteration to pick up mid-stream mono↔stereo switches
        # driven by the audio_config WS handler (issue #766, unblocks #721).
        self._codec_stale: bool = False

    async def subscribe(
        self,
        ws: Connection | None = None,
        *,
        preferred_rx_codec: int | None = None,
    ) -> asyncio.Queue[bytes]:
        """Register a new WebSocket client and start relaying if first."""
        queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=self.HIGH_WATERMARK)
        client_id = id(queue)
        async with self._lock:
            self._clients[client_id] = queue
            if ws is not None:
                self._client_ws[client_id] = ws
            if preferred_rx_codec is not None:
                self._client_rx_codec[client_id] = preferred_rx_codec
            if self._adaptive_egress and preferred_rx_codec == AUDIO_CODEC_OPUS:
                # Adaptive eligibility (MOR-588): only clients that
                # DECLARED Opus decode capability adapt; explicit-PCM16
                # and no-preference clients keep the static MOR-584
                # choice (clients without Opus decode are pinned PCM16,
                # ADR §3.6). Initial state is always PCM16.
                self._client_adaptive[client_id] = _AdaptiveEgressState()
            # Start relay if no active subscription, or if relay task died
            relay_alive = self._relay_task is not None and not self._relay_task.done()
            if self._subscription is None or not relay_alive:
                # Clean up stale subscription/task if needed
                if self._subscription is not None and not relay_alive:
                    logger.info("audio-broadcaster: relay task dead, restarting")
                    if self._relay_task is not None:
                        self._relay_task.cancel()
                        self._relay_task = None
                    await self._release_subscription()
                if self._radio:
                    await self._start_relay()
        self._notify_client_count_change()
        logger.info("audio-broadcaster: client added (total=%d)", len(self._clients))
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[bytes]) -> None:
        """Unregister a client and stop relay if last (unless PCM tap is active)."""
        client_id = id(queue)
        removed = False
        async with self._lock:
            removed = self._clients.pop(client_id, None) is not None
            self._client_ws.pop(client_id, None)
            self._client_rx_codec.pop(client_id, None)
            self._client_opus_transcoders.pop(client_id, None)
            self._client_opus_pcm_buffers.pop(client_id, None)
            self._client_link_quality.pop(client_id, None)
            self._client_queue_drops.pop(client_id, None)
            self._client_adaptive.pop(client_id, None)
            if (
                not self._clients
                and self._subscription is not None
                and not self._tap_registry.active
            ):
                await self._stop_relay()
        if removed:
            self._notify_client_count_change()
        logger.info("audio-broadcaster: client removed (total=%d)", len(self._clients))

    def _notify_client_count_change(self) -> None:
        if self._on_client_count_change is None:
            return
        try:
            self._on_client_count_change()
        except Exception:
            logger.debug(
                "audio-broadcaster: client count callback failed", exc_info=True
            )

    def taps(self, stage: str) -> TapRegistry:
        """Return the :class:`TapRegistry` for a named RX stage (MOR-565).

        Only ``STAGE_RX_POST_DSP`` is hosted on the broadcaster; reserved
        stage names raise ``KeyError``. Same accessor shape as
        :meth:`rigplane.audio.bus.AudioBus.taps`.
        """
        return self._stage_taps[stage]

    def set_pcm_tap(self, callback: "Callable[[bytes], None] | None") -> None:
        """Register a tap that receives decoded PCM16 audio data.

        Compatibility wrapper around :class:`TapRegistry` (Pro-stable
        surface). Manages a single "legacy" tap slot on the
        ``rx.post_dsp`` stage. For new consumers prefer
        ``taps(STAGE_RX_POST_DSP).register()`` directly.

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
                # A dead relay may leave a stale handle behind — release it
                # so session RX demand never leaks across a restart.
                await self._release_subscription()
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
                self._client_rx_codec.pop(cid, None)
                self._client_opus_transcoders.pop(cid, None)
                self._client_opus_pcm_buffers.pop(cid, None)
                self._client_link_quality.pop(cid, None)
                self._client_queue_drops.pop(cid, None)
                self._client_adaptive.pop(cid, None)
            # Stop relay if no clients remain (and no PCM tap active)
            if (
                not self._clients
                and self._subscription is not None
                and not self._tap_registry.active
            ):
                await self._stop_relay()
            remaining = len(self._clients)
        if dead_ids:
            self._notify_client_count_change()
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
        # Format may have changed — drop partial reframing buffers so no
        # stale PCM concatenates across a codec/format switch (MOR-596).
        self._client_opus_pcm_buffers.clear()
        # Re-check DSP-on-Opus after the codec may have just flipped.
        # Issue #762.
        self._maybe_warn_dsp_opus_gate()

    def _resolve_web_rx_codec(self, radio_codec: AudioCodec) -> int:
        """Resolve the DEFAULT browser RX transport from profile policy.

        Applies to clients that sent no ``preferred_rx_codec``; clients
        with an explicit preference are resolved per-connection in
        :meth:`_client_egress_codec` (MOR-584).
        """
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

    def _client_egress_codec(self, client_id: int) -> int:
        """Resolve one client's egress codec (MOR-584, static per connection).

        Ladder: adaptive controller state (MOR-588, flag-gated; PCM16
        initial, Opus only on sustained degradation) → client-requested
        (``preferred_rx_codec`` at negotiation) → profile policy default
        (``_resolve_web_rx_codec`` cached in ``_web_codec``) → PCM16.
        Opus-native radios pass through un-decoded for every client —
        there is no PCM to re-encode (issue #762).
        """
        if self._radio_codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return AUDIO_CODEC_OPUS
        adaptive = self._client_adaptive.get(client_id)
        if adaptive is not None:
            return adaptive.codec
        return self._client_rx_codec.get(client_id, self._web_codec)

    def negotiated_rx_format(self, queue: asyncio.Queue[bytes]) -> dict[str, Any]:
        """Describe the egress format a subscribed client will receive.

        Backs the ``audio_format`` ack sent after ``audio_start``
        (MOR-584).  ``frame_ms`` is the 20 ms nominal — advisory like
        the wire header; consumers must size buffers from actual
        payloads (#765).
        """
        codec = self._client_egress_codec(id(queue))
        return {
            "codec": "opus" if codec == AUDIO_CODEC_OPUS else "pcm16",
            "sample_rate": self._sample_rate,
            "channels": self._channels,
            "frame_ms": 20,
        }

    def record_client_stats(
        self,
        queue: asyncio.Queue[bytes],
        stats: dict[str, int | float],
    ) -> None:
        """Record one client's latest self-reported link-quality (MOR-585).

        Latest-wins snapshot from the periodic ``audio_stats`` uplink
        (underruns, buffer depth, client-side drops).  Ignored for
        queues not (or no longer) subscribed, so a stats message racing
        a disconnect cannot leak per-client state.
        """
        client_id = id(queue)
        if client_id not in self._clients:
            return
        self._client_link_quality[client_id] = dict(stats)
        self._adaptive_evaluate(client_id)

    def client_link_quality(
        self, queue: asyncio.Queue[bytes]
    ) -> dict[str, int | float]:
        """One client's link-quality snapshot (MOR-585, ADR §3.6).

        The latest client-reported ``audio_stats`` fields merged with the
        server-side ``ws_queue_drops`` counter (drop-oldest evictions of
        the bounded per-client WS queue).  This is the signal surface the
        step-19 adaptive egress codec controller and the ``rx.egress``
        taps read; nothing in this step consumes it.
        """
        client_id = id(queue)
        snapshot: dict[str, int | float] = dict(
            self._client_link_quality.get(client_id, {})
        )
        snapshot["ws_queue_drops"] = self._client_queue_drops.get(client_id, 0)
        return snapshot

    def _adaptive_evaluate(self, client_id: int) -> None:
        """Run one adaptive controller step for one client (MOR-588).

        Called per ``audio_stats`` uplink and per relay-loop frame (the
        latter covers the server-side ``ws_queue_drops`` signal for
        clients whose stats uplink is absent or stalled). No-op for
        non-adaptive clients, so the flag-off path stays MOR-584 static.

        Evidence = either MOR-585 cumulative counter rising (client
        playback ``underruns``, server ``ws_queue_drops``). A degradation
        episode is continuous evidence with gaps shorter than the degrade
        window; PCM16→Opus fires once an episode spans the window, Opus→
        PCM16 once no evidence arrives for the clean window — both gated
        by the dwell since the previous switch.
        """
        state = self._client_adaptive.get(client_id)
        if state is None:
            return
        if self._radio_codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return  # pass-through for everyone — nothing to adapt (#762)
        now = self._adaptive_monotonic()
        stats = self._client_link_quality.get(client_id, {})
        underruns = int(stats.get("underruns", 0))
        queue_drops = self._client_queue_drops.get(client_id, 0)
        evidence = (
            underruns > state.seen_underruns or queue_drops > state.seen_queue_drops
        )
        state.seen_underruns = max(state.seen_underruns, underruns)
        state.seen_queue_drops = max(state.seen_queue_drops, queue_drops)
        if evidence:
            state.last_evidence = now
            if state.degrade_since is None:
                state.degrade_since = now
        elif (
            state.degrade_since is not None
            and state.last_evidence is not None
            and now - state.last_evidence > self._adaptive_degrade_window_s
        ):
            state.degrade_since = None  # episode over — evidence stopped

        dwell_ok = (
            state.last_switch is None
            or now - state.last_switch >= self._adaptive_dwell_s
        )
        if (
            state.codec == AUDIO_CODEC_PCM16
            and dwell_ok
            and state.degrade_since is not None
            and now - state.degrade_since >= self._adaptive_degrade_window_s
        ):
            self._adaptive_switch(client_id, state, AUDIO_CODEC_OPUS, now)
        elif (
            state.codec == AUDIO_CODEC_OPUS
            and dwell_ok
            and (
                state.last_evidence is None
                or now - state.last_evidence >= self._adaptive_clean_window_s
            )
        ):
            self._adaptive_switch(client_id, state, AUDIO_CODEC_PCM16, now)

    def _adaptive_switch(
        self, client_id: int, state: _AdaptiveEgressState, codec: int, now: float
    ) -> None:
        """Apply one adaptive codec switch (MOR-588).

        Changing ``state.codec`` re-routes ``_client_egress_codec`` and
        thereby the MOR-584 encoder pool: the next Opus frame lazily
        constructs a FRESH per-client transcoder; switching back to
        PCM16 tears the encoder down here. A fresh ``audio_format`` ack
        mirrors the new codec to the client.
        """
        state.codec = codec
        state.last_switch = now
        state.degrade_since = None
        if codec != AUDIO_CODEC_OPUS:
            self._client_opus_transcoders.pop(client_id, None)
            self._client_opus_pcm_buffers.pop(client_id, None)
        logger.info(
            "audio-broadcaster: adaptive egress switch → %s (client=%d)",
            "opus" if codec == AUDIO_CODEC_OPUS else "pcm16",
            client_id,
        )
        self._send_adaptive_format_ack(client_id)

    def _send_adaptive_format_ack(self, client_id: int) -> None:
        """Send a fresh ``audio_format`` ack after an adaptive switch.

        Advisory and fire-and-forget, like the ``audio_start`` ack
        (MOR-584): the browser decodes from the per-frame header codec
        byte regardless; this only refreshes its negotiated-format view.
        Never blocks the relay loop — whole WS frames are written
        atomically, so a text send may interleave with the sender loop's
        binary frames but never corrupt them.
        """
        ws = self._client_ws.get(client_id)
        queue = self._clients.get(client_id)
        if ws is None or queue is None:
            return
        payload = encode_json(
            {"type": "audio_format", **self.negotiated_rx_format(queue)}
        )

        async def _send() -> None:
            try:
                await ws.send_text(payload)
            except Exception:
                logger.debug(
                    "audio-broadcaster: adaptive audio_format ack failed",
                    exc_info=True,
                )

        try:
            task = asyncio.get_running_loop().create_task(_send())
        except RuntimeError:
            return  # no running loop (sync test context) — ack is advisory
        self._ack_tasks.add(task)
        task.add_done_callback(self._ack_tasks.discard)

    def _encode_client_rx_frame(
        self,
        client_id: int,
        pcm_data: bytes,
        frame_ms: int,
    ) -> list[tuple[int, int, bytes]]:
        """Apply ONE client's RX transport encoding after PCM consumers run.

        Sits downstream of the PCM spine (decode → DSP → tap fan-out):
        only browser WS clients reach here.  The AudioBridge, FFT scope
        and other taps consume PCM upstream and never touch these
        encoders (T1a digital-path invariant).

        Returns 0..N ``(codec, frame_ms, payload)`` frames.  PCM16 and
        Opus-native pass-through stay strictly 1:1; Opus egress on PCM
        radios buffers s16le per client and emits exact 20 ms Opus
        frames (MOR-596) — radio packets are not Opus-frame-aligned, so
        per-packet encode raised ``AudioFormatError`` on every frame and
        silently fell back to PCM16.
        """
        if self._client_egress_codec(client_id) != AUDIO_CODEC_OPUS:
            return [(AUDIO_CODEC_PCM16, frame_ms, pcm_data)]
        if self._radio_codec in (AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH):
            return [(AUDIO_CODEC_OPUS, frame_ms, pcm_data)]

        key = (self._sample_rate, self._channels, OPUS_EGRESS_FRAME_MS)
        frame_bytes = (
            self._sample_rate * OPUS_EGRESS_FRAME_MS // 1000 * self._channels * 2
        )
        try:
            entry = self._client_opus_transcoders.get(client_id)
            if entry is None or entry[1] != key:
                entry = (
                    create_pcm_opus_transcoder(
                        sample_rate=self._sample_rate,
                        channels=self._channels,
                        frame_ms=OPUS_EGRESS_FRAME_MS,
                    ),
                    key,
                )
                self._client_opus_transcoders[client_id] = entry
            buf = self._client_opus_pcm_buffers.setdefault(client_id, bytearray())
            buf.extend(pcm_data)
            frames: list[tuple[int, int, bytes]] = []
            while len(buf) >= frame_bytes:
                chunk = bytes(buf[:frame_bytes])
                del buf[:frame_bytes]
                opus = entry[0].pcm_to_opus(chunk)
                frames.append((AUDIO_CODEC_OPUS, OPUS_EGRESS_FRAME_MS, opus))
            return frames
        except Exception as exc:
            if not self._browser_opus_warned:
                logger.warning(
                    "audio: browser Opus transcode unavailable, emitting PCM16: %s",
                    exc,
                )
                self._browser_opus_warned = True
            self._client_opus_transcoders.pop(client_id, None)
            self._client_opus_pcm_buffers.pop(client_id, None)
            return [(AUDIO_CODEC_PCM16, frame_ms, pcm_data)]

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
            session = getattr(self._radio, "audio_session", None)
            if session is not None:
                # Session-routed RX demand (MOR-608, ADR §3.2 option a):
                # subscribing through the radio-owned AudioSession registers
                # this relay's demand (session leaves IDLE → RX_ONLY), so
                # the MOR-581 health watchdog covers browser-only listeners
                # and reconnects go through ``AudioSession.reestablish()``.
                # Mirrors the MOR-580 TX-lease pattern in AudioHandler.
                self._subscription = await session.subscribe_rx("web-audio")
            else:
                # Legacy bus path for radios without a session (bare test
                # doubles, not-yet-migrated backends).
                bus = self._radio.audio_bus  # type: ignore[attr-defined]
                subscription = cast(_AudioBus, bus).subscribe(name="web-audio")
                await subscription.start()
                self._subscription = subscription
            self._relay_task = asyncio.create_task(self._relay_loop())
        except Exception as exc:
            logger.exception("audio-broadcaster: failed to start relay")
            self._subscription = None
            await self._notify_relay_start_failure(exc)

    async def _notify_relay_start_failure(self, exc: Exception) -> None:
        """Surface a relay/RX start failure to connected WS clients (MOR-582).

        Without this the browser was told nothing and waited on dead air
        forever (ADR §3.4 problem P2: "subscribed" with zero frames). Reuses
        the handler's error envelope shape: ``{"type": "error", "message"}``.
        """
        if not self._client_ws:
            return
        payload = encode_json(
            {
                "type": "error",
                "message": f"audio_start: RX audio failed to start: {exc}",
            }
        )
        for ws in list(self._client_ws.values()):
            try:
                await ws.send_text(payload)
            except Exception:
                logger.debug(
                    "audio-broadcaster: failed to notify client of RX start failure",
                    exc_info=True,
                )

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
                # Per-client egress encode (MOR-584): the PCM spine above
                # (decode → DSP → taps) is shared; from here each browser
                # WS client gets its own negotiated wire codec.  Pass-
                # through payloads (PCM16, Opus-native) reuse one cached
                # frame per codec and stay strictly 1:1; Opus transcodes
                # are per-client and N:M — the reframing accumulator may
                # emit 0..N exact 20 ms frames per radio packet (MOR-596),
                # all carrying this packet's seq.
                shared_frames: dict[int, bytes] = {}
                dead_ids: list[int] = []
                for client_id, q in list(self._clients.items()):
                    ws = self._client_ws.get(client_id)
                    if ws is not None and not ws.is_alive():
                        dead_ids.append(client_id)
                        continue
                    # Adaptive controller step (MOR-588): no-op unless the
                    # client opted into adaptation on a flag-on broadcaster.
                    if self._client_adaptive:
                        self._adaptive_evaluate(client_id)
                    out_frames = self._encode_client_rx_frame(
                        client_id, audio_data, _frame_ms
                    )
                    for codec, hdr_frame_ms, payload in out_frames:
                        shared = payload is audio_data
                        frame = shared_frames.get(codec) if shared else None
                        if frame is None:
                            frame = encode_audio_frame(
                                MSG_TYPE_AUDIO_RX,
                                codec,
                                self._seq,
                                self._sample_rate // 100,
                                self._channels,
                                hdr_frame_ms,
                                payload,
                            )
                            if shared:
                                shared_frames[codec] = frame
                        try:
                            q.put_nowait(frame)
                        except asyncio.QueueFull:
                            # Drop-oldest eviction: count it per client —
                            # this is the server-side WS congestion signal
                            # for the adaptive egress codec controller
                            # (MOR-585, ADR §3.6 detection-signals table).
                            self._client_queue_drops[client_id] = (
                                self._client_queue_drops.get(client_id, 0) + 1
                            )
                            try:
                                q.get_nowait()
                            except asyncio.QueueEmpty:
                                pass
                            try:
                                q.put_nowait(frame)
                            except asyncio.QueueFull:
                                pass
                self._seq = (self._seq + 1) & 0xFFFF
                for client_id in dead_ids:
                    self._clients.pop(client_id, None)
                    self._client_ws.pop(client_id, None)
                    self._client_rx_codec.pop(client_id, None)
                    self._client_opus_transcoders.pop(client_id, None)
                    self._client_opus_pcm_buffers.pop(client_id, None)
                    self._client_link_quality.pop(client_id, None)
                    self._client_queue_drops.pop(client_id, None)
                    self._client_adaptive.pop(client_id, None)
                    logger.info("audio-broadcaster: removed dead client during relay")
                if dead_ids:
                    self._notify_client_count_change()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("audio-broadcaster: relay loop error")

    async def _release_subscription(self) -> None:
        """Release the RX source handle, whichever path owns it (MOR-608).

        Session-routed handles drop their RX demand via the ASYNC
        ``RxSubscription.release()`` (serialized by the session lock);
        legacy bus handles keep the sync ``stop()``. Awaiting the release
        while holding the broadcaster ``_lock`` is safe: the session lock
        and ``_lock`` are distinct and the session never calls back into
        the broadcaster, so there is no inverse acquisition order.
        """
        subscription = self._subscription
        self._subscription = None
        if subscription is None:
            return
        if isinstance(subscription, RxSubscription):
            await subscription.release()
        else:
            subscription.stop()

    async def _stop_relay(self) -> None:
        if self._relay_task is not None:
            self._relay_task.cancel()
            try:
                await self._relay_task
            except asyncio.CancelledError:
                pass
            self._relay_task = None
        await self._release_subscription()
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
        ws: Connection,
        radio: "Radio | None",
        broadcaster: "AudioBroadcaster | None" = None,
    ) -> None:
        self._ws = ws
        self._radio = radio
        self._broadcaster = broadcaster
        self._rx_active = False
        self._tx_active = False
        # TX lease on the radio-owned AudioSession singleton (MOR-580,
        # ADR §3.3). None when the radio lacks a session (legacy direct
        # path) or when TX is not active.
        self._tx_lease: TxLease | None = None
        self._tx_stop_lock = asyncio.Lock()
        self._seq: int = 0
        # Latest client-reported link-quality snapshot from the periodic
        # ``audio_stats`` uplink (MOR-585) — mirrored per client on the
        # broadcaster while RX is subscribed.
        self._link_quality: dict[str, int | float] = {}
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
        except asyncio.CancelledError:
            logger.info("audio: handler cancelled")
            reader.cancel()
            sender.cancel()
            raise
        except Exception:
            logger.exception("audio: handler error")
            reader.cancel()
            sender.cancel()
        finally:
            self._done.set()
            for task in (reader, sender):
                if not task.done():
                    task.cancel()
            await asyncio.gather(reader, sender, return_exceptions=True)
            await self._stop_rx()
            await self._stop_tx(
                reason="handler exit",
                timeout=_TX_CLEANUP_STOP_TIMEOUT_SECONDS,
                suppress_errors=True,
            )
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
        msg_type = msg.get("type", "")
        # Periodic audio_stats (~1.5 s per client, MOR-585) stays at DEBUG;
        # everything else keeps the established INFO trace.
        logger.log(
            logging.DEBUG if msg_type == "audio_stats" else logging.INFO,
            "audio: control msg: %s",
            msg,
        )
        direction = msg.get("direction", "rx")

        if msg_type == "audio_start":
            if direction == "rx":
                await self._start_rx(preferred_rx_codec=_parse_preferred_rx_codec(msg))
            elif direction == "tx":
                if self._radio and CAP_AUDIO in self._radio.capabilities:
                    self._ensure_tx_transcoder()
                    session = getattr(self._radio, "audio_session", None)
                    if session is not None:
                        # Shared AudioSession lease (MOR-580, ADR §3.3): the
                        # session's single-lock refcount serializes arming
                        # with other lease holders (bridge, poller PTT), so
                        # the double-start benign-tolerance below is
                        # unnecessary on this path.
                        if self._tx_lease is None or self._tx_lease.released:
                            self._tx_lease = await session.acquire_tx("web")
                    else:
                        # Legacy direct-arm fallback for radios without a
                        # session (bare test doubles, not-yet-migrated
                        # backends).
                        try:
                            start_tx = getattr(self._radio, "start_tx", None)
                            if start_tx is not None:
                                # Neutral AudioTransport surface (MOR-544):
                                # the backend resolves the TX format from
                                # its negotiated contract.
                                await start_tx()
                            else:
                                await self._legacy_tx_lifecycle("start")
                        except RuntimeError as exc:
                            if _is_benign_tx_restart(exc):
                                logger.info(
                                    "audio: TX already started by poller, reusing"
                                )
                            else:
                                raise
                self._tx_active = True
                logger.info("audio: TX active")
        elif msg_type == "audio_stop":
            if direction == "rx":
                await self._stop_rx()
            elif direction == "tx":
                await self._stop_tx(reason="client request", force=True)
        elif msg_type == "audio_config":
            await self._handle_audio_config(msg)
        elif msg_type == "audio_stats":
            self._handle_audio_stats(msg)

    def _handle_audio_stats(self, msg: dict[str, Any]) -> None:
        """Record the client's periodic link-quality report (MOR-585).

        Stats collection only (ADR §3.6 step 18): the browser player
        reports playback ``underruns``, ``buffer_depth_ms`` and
        ``dropped_frames`` every ~1.5 s; only numeric fields are kept
        (latest-wins), and unknown numeric fields pass through so future
        carriers (WebRTC RTCP, step 19) can reuse the envelope.  Clients
        that never send ``audio_stats`` are entirely unaffected.
        """
        stats: dict[str, int | float] = {}
        for key, value in msg.items():
            if key == "type" or isinstance(value, bool):
                continue
            if isinstance(value, (int, float)):
                stats[key] = value
        self._link_quality = stats
        if self._rx_active and self._broadcaster is not None:
            self._broadcaster.record_client_stats(self._frame_queue, stats)

    async def _stop_tx(
        self,
        *,
        reason: str,
        timeout: float | None = None,
        suppress_errors: bool = False,
        force: bool = False,
    ) -> None:
        """Release this handler's active TX ownership, optionally bounded."""
        async with self._tx_stop_lock:
            if not self._tx_active and not force and self._tx_lease is None:
                return
            self._tx_active = False

            if not self._radio or CAP_AUDIO not in getattr(
                self._radio, "capabilities", ()
            ):
                logger.info("audio: TX cleanup skipped (%s, no audio radio)", reason)
                return

            try:
                lease = self._tx_lease
                if lease is not None:
                    # Session path (MOR-580): drop this handler's TX demand;
                    # the session disarms radio TX only when the lease
                    # refcount reaches zero (release is idempotent).
                    self._tx_lease = None
                    stop_tx: Awaitable[None] = lease.release()
                elif getattr(self._radio, "audio_session", None) is not None:
                    # Session radio with no held lease: this handler owns no
                    # TX demand — never direct-stop the radio out from under
                    # other lease holders (bridge, poller PTT). MOR-580.
                    logger.info(
                        "audio: TX stop skipped (%s, no session lease held)", reason
                    )
                    return
                else:
                    stop_tx_method = getattr(self._radio, "stop_tx", None)
                    if stop_tx_method is not None:
                        # Neutral AudioTransport surface (MOR-544).
                        stop_tx = stop_tx_method()
                    else:
                        stop_tx = self._legacy_tx_lifecycle("stop")
                if timeout is None:
                    await stop_tx
                else:
                    await self._await_bounded_tx_stop(stop_tx, timeout=timeout)
            except TimeoutError:
                if timeout is None:
                    logger.warning(
                        "audio: TX stop timed out during %s", reason, exc_info=True
                    )
                else:
                    logger.warning(
                        "audio: TX stop timed out during %s after %.1fs",
                        reason,
                        timeout,
                    )
                if not suppress_errors:
                    raise
            except Exception:
                logger.warning("audio: TX stop failed during %s", reason, exc_info=True)
                if not suppress_errors:
                    raise
            else:
                logger.info("audio: TX stopped (%s)", reason)

    async def _await_bounded_tx_stop(
        self,
        stop_tx: Awaitable[None],
        *,
        timeout: float,
    ) -> None:
        task = asyncio.ensure_future(stop_tx)
        done, pending = await asyncio.wait({task}, timeout=timeout)
        if pending:
            task.cancel()
            task.add_done_callback(self._consume_late_tx_stop)
            raise TimeoutError
        await next(iter(done))

    @staticmethod
    def _consume_late_tx_stop(task: asyncio.Future[None]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.debug("audio: late TX stop failed after timeout", exc_info=True)

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
        rate = self._tx_sample_rate()
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
        # Under the ``rigplane.web.*`` strict override with
        # ``follow_imports = "skip"``, ``AudioCodec`` resolves to ``Any`` in
        # this module's view, so the function effectively returns
        # ``Any | None`` and the ``getattr`` results below are also ``Any``.
        # That triggers ``no-any-return``; suppress it locally rather than
        # carrying a runtime-redundant ``cast``.  ``warn_unused_ignores`` is
        # off for ``rigplane.web.*``, so the ignore stays safe in any future
        # non-strict context.
        #
        # Preference order (MOR-544): first-class ``audio_tx_codec``
        # (AudioTransport) → contract ``tx_codec`` → RX ``audio_codec``.
        tx_codec = getattr(self._radio, "audio_tx_codec", None)
        if tx_codec is not None:
            return tx_codec  # type: ignore[no-any-return]
        contract = getattr(self._radio, "audio_stream_contract", None)
        tx_codec = getattr(contract, "tx_codec", None)
        if tx_codec is not None:
            return tx_codec  # type: ignore[no-any-return]
        return getattr(self._radio, "audio_codec", None)  # type: ignore[no-any-return]

    def _tx_sample_rate(self) -> int:
        """Resolve the TX sample rate: contract → radio property → 48000.

        Single source for both the legacy ``start_audio_tx_pcm`` call and
        the TX transcoder (the two used to carry half-duplicated fallback
        chains; unified in MOR-544)."""
        contract = getattr(self._radio, "audio_stream_contract", None)
        sr = getattr(contract, "tx_sample_rate_hz", None)
        if not isinstance(sr, int) or isinstance(sr, bool) or sr <= 0:
            sr = getattr(self._radio, "audio_sample_rate", None)
        if isinstance(sr, int) and not isinstance(sr, bool) and sr > 0:
            return sr
        return 48000

    async def _legacy_tx_lifecycle(self, op: str) -> None:
        """Per-codec TX ``"start"``/``"stop"`` fallback for radios without
        the neutral ``AudioTransport`` surface (MOR-544)."""
        if self._tx_codec() == AudioCodec.PCM_1CH_16BIT:
            if op == "start":
                await self._radio.start_audio_tx_pcm(  # type: ignore[union-attr]
                    sample_rate=self._tx_sample_rate()
                )
            else:
                await self._radio.stop_audio_tx_pcm()  # type: ignore[union-attr]
        elif op == "start":
            await self._radio.start_audio_tx_opus()  # type: ignore[union-attr]
        else:
            await self._radio.stop_audio_tx_opus()  # type: ignore[union-attr]

    async def _push_tx(self, data: bytes, *, legacy_method: str) -> None:
        """Push TX bytes via the held session lease (MOR-580); without one,
        via neutral ``push_tx``, falling back to the named legacy per-codec
        push method (MOR-544)."""
        lease = self._tx_lease
        if lease is not None and not lease.released:
            await lease.push(data)
            return
        push_tx = getattr(self._radio, "push_tx", None)
        if push_tx is not None:
            await push_tx(data)
        else:
            await getattr(self._radio, legacy_method)(data)

    async def _start_rx(self, *, preferred_rx_codec: int | None = None) -> None:
        """Subscribe to audio broadcaster for RX frames."""
        if not self._broadcaster:
            return
        self._rx_active = True
        self._frame_queue = await self._broadcaster.subscribe(
            ws=self._ws,
            preferred_rx_codec=preferred_rx_codec,
        )
        # Per-connection negotiation ack (MOR-584): tell the client which
        # wire codec/format it will receive.  Advisory and fire-and-forget
        # — legacy clients ignore text frames on the audio WS and keep
        # decoding from the per-frame binary header.
        await self._send_json(
            {
                "type": "audio_format",
                **self._broadcaster.negotiated_rx_format(self._frame_queue),
            }
        )
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
        if payload[0] != MSG_TYPE_AUDIO_TX:
            logger.warning("audio: TX frame wrong type 0x%02x, ignoring", payload[0])
            return
        browser_codec = payload[1]
        audio_data = payload[AUDIO_HEADER_SIZE:]
        if audio_data:
            try:
                tx_codec = self._tx_codec()
                if browser_codec == AUDIO_CODEC_PCM16:
                    await self._push_tx(audio_data, legacy_method="push_audio_tx_pcm")
                    tx_data_desc = f"{len(audio_data)} bytes pcm"
                elif (
                    browser_codec == AUDIO_CODEC_OPUS
                    and tx_codec == AudioCodec.PCM_1CH_16BIT
                ):
                    if self._transcoder is None:
                        logger.warning(
                            "audio: TX frame dropped incoming_codec=opus "
                            "radio_tx_codec=%s sample_rate=%d "
                            "action=dropped_no_transcoder",
                            tx_codec.name,
                            self._tx_sample_rate(),
                        )
                        return
                    try:
                        # Decode Opus → PCM16
                        pcm_data = self._transcoder.opus_to_pcm(audio_data)
                        await self._push_tx(pcm_data, legacy_method="push_audio_tx_pcm")
                        tx_data_desc = f"{len(pcm_data)} bytes pcm"
                    except Exception as e:
                        logger.warning(
                            "audio: TX frame dropped incoming_codec=opus "
                            "radio_tx_codec=%s sample_rate=%d "
                            "action=dropped_transcode_failed error=%s",
                            tx_codec.name,
                            self._tx_sample_rate(),
                            e,
                        )
                        return
                elif browser_codec == AUDIO_CODEC_OPUS:
                    # Radio uses Opus or PCM_1CH_8BIT/etc → send Opus as-is
                    await self._push_tx(audio_data, legacy_method="push_audio_tx_opus")
                    tx_data_desc = f"{len(audio_data)} bytes opus"
                else:
                    logger.warning(
                        "audio: unsupported browser TX codec 0x%02x, dropping frame",
                        browser_codec,
                    )
                    return

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
