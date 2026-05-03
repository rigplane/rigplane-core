"""Audio bridge — bidirectional PCM bridge between radio and a system audio device.

Routes decoded PCM audio from the radio to a virtual audio output device
and captures PCM from the same device back to the radio for TX.  This
allows applications like WSJT-X, fldigi, or JS8Call to use the radio
without any special configuration — they simply select the virtual audio
device as their sound card.

Architecture::

    Radio ←(LAN/Opus)→ icom-lan ←(PCM)→ AudioBackend ←(PortAudio)→ Loopback device ←→ WSJT-X

Supported loopback drivers:
    - **macOS**: BlackHole (``brew install blackhole-2ch``) or Rogue Amoeba Loopback
    - **Linux**: PipeWire loopback, PulseAudio null-sink, or ALSA snd-aloop
    - **Windows**: VB-Cable (https://vb-audio.com/Cable/)

Requirements:
    - ``sounddevice`` and ``numpy`` (``pip install icom-lan[bridge]``)
    - A virtual audio loopback driver (see above)

Usage::

    bridge = AudioBridge(radio, device_name="BlackHole 2ch")  # macOS
    bridge = AudioBridge(radio, device_name="VB-Cable")       # Windows
    await bridge.start()    # begins bidirectional audio flow
    ...
    await bridge.stop()     # clean shutdown
"""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import Executor
from typing import TYPE_CHECKING, Any, Callable

import math

from icom_lan.core._optional_deps import _require_opuslib, _require_sounddevice
from icom_lan.core.types import _AUDIO_CODEC_CHANNELS

from ._bridge_metrics import BridgeMetrics
from ._bridge_state import BridgeState, BridgeStateChange
from .backend import (
    AudioBackend,
    AudioDeviceInfo,
    PortAudioBackend,
    RxStream,
    TxStream,
)

if TYPE_CHECKING:
    from icom_lan.radio_protocol import AudioCapable

logger = logging.getLogger(__name__)

__all__ = [
    "AudioBridge",
    "BridgeMetrics",
    "BridgeState",
    "BridgeStateChange",
    "LoopbackNotFoundError",
    "derive_bridge_label",
    "find_loopback_device",
]


class LoopbackNotFoundError(RuntimeError):
    """Raised when no virtual loopback audio device can be located.

    Subclass of :class:`RuntimeError` for backward compatibility with
    callers that catch the generic exception. Use this type to distinguish
    "loopback driver not installed" (recoverable in auto-bridge mode) from
    other bridge setup failures (unsupported radio, missing audio backend,
    runtime errors).
    """


# Audio format constants — must match radio PCM settings
SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 20
SAMPLES_PER_FRAME = SAMPLE_RATE * FRAME_MS // 1000  # 960
BYTES_PER_SAMPLE = 2  # s16le
FRAME_BYTES = SAMPLES_PER_FRAME * CHANNELS * BYTES_PER_SAMPLE  # 1920

# Virtual loopback device name candidates for auto-detection
_LOOPBACK_CANDIDATES = (
    "BlackHole",  # macOS (brew install blackhole-2ch)
    "Loopback",  # macOS (Rogue Amoeba) / Linux (generic)
    "VB-Audio",  # Windows (VB-Cable)
    "Virtual",  # generic virtual device
    "pipewire",  # Linux (PipeWire loopback)
    "PipeWire",  # Linux (PipeWire loopback, title case)
    "null",  # Linux (PulseAudio null-sink)
    "snd-aloop",  # Linux (ALSA loopback)
    "JACK",  # Linux (JACK audio)
)

_INT16_MAX = 32767.0


def _std_dev(values: list[float]) -> float:
    """Compute standard deviation of a list of floats."""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    return math.sqrt(variance)


def _rms_dbfs(pcm: bytes) -> float:
    """Compute RMS level of PCM s16le data in dBFS."""
    import numpy as np

    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    rms = float(np.sqrt(np.mean(samples**2)))
    if rms < 1.0:
        return -96.0
    return 20.0 * math.log10(rms / _INT16_MAX)


def _downmix_stereo_to_mono(pcm: bytes) -> bytes:
    """Downmix L+R interleaved s16le → mono s16le via average.

    Used when the radio negotiates a stereo codec (PCM_2CH_16BIT, OPUS_2CH,
    etc.) but the bridge output stream remains mono — WSJT-X / JS8Call /
    fldigi are mono-only consumers. Without this, interleaved stereo bytes
    fed to a mono PortAudio stream halve the effective sample-rate and
    compress the spectrum 2x (issue #1381).
    """
    import numpy as np

    arr = np.frombuffer(pcm, dtype=np.int16).reshape(-1, 2).astype(np.int32)
    mono = ((arr[:, 0] + arr[:, 1]) // 2).astype(np.int16)
    return bytes(mono.tobytes())


def derive_bridge_label(radio: Any, explicit: str | None = None) -> str:
    """Derive a descriptive bridge label.

    Args:
        radio: Radio instance (used to read ``.model`` when *explicit* is not set).
        explicit: Caller-supplied label. Returned as-is when truthy.

    Returns:
        ``"icom-lan (<model>)"`` when the model is known, otherwise ``"icom-lan"``.
    """
    if explicit:
        return explicit
    model = getattr(radio, "model", None)
    return f"icom-lan ({model})" if isinstance(model, str) and model else "icom-lan"


def find_loopback_device(name: str | None = None) -> dict[str, Any] | None:
    """Find a virtual loopback audio device by name.

    .. deprecated::
        Use :class:`AudioBridge` with an :class:`AudioBackend` instead.
        This function is kept for backward compatibility.
    """
    _require_sounddevice()
    import sounddevice as sd

    devices = sd.query_devices()
    search_names = [name] if name else list(_LOOPBACK_CANDIDATES)

    for dev in devices:
        dev_name = dev.get("name", "")
        for search in search_names:
            if search.lower() in dev_name.lower():
                return dict(dev)
    return None


def list_audio_devices() -> list[dict[str, Any]]:
    """List all available audio devices."""
    _require_sounddevice()
    import sounddevice as sd

    return list(sd.query_devices())


def _find_device_in_backend(
    backend: AudioBackend,
    name: str | None,
) -> AudioDeviceInfo | None:
    """Find a virtual loopback device using the backend's device list."""
    devices = backend.list_devices()
    search_names = [name] if name else list(_LOOPBACK_CANDIDATES)

    for dev in devices:
        for search in search_names:
            if search.lower() in dev.name.lower():
                return dev
    return None


class AudioBridge:
    """Bidirectional PCM audio bridge between radio and a system audio device.

    Args:
        radio: A connected radio instance implementing :class:`AudioCapable`.
        device_name: Name (or substring) of the audio device to use.
        sample_rate: PCM sample rate (default 48000).
        channels: Number of audio channels (default 1, mono).
        frame_ms: PCM frame duration in milliseconds (default 20).
        tx_enabled: Whether to bridge TX audio (device → radio). Default True.
        tx_executor: Deprecated — backend now owns threading.
        label: Descriptive label used in log messages (default ``"icom-lan"``).
        backend: Audio backend for device discovery and stream I/O.
        max_retries: Maximum reconnect attempts (0 = infinite).
        retry_base_delay: Initial backoff delay in seconds.
        retry_max_delay: Maximum backoff delay in seconds.
        on_state_changed: Callback fired on every state transition.
    """

    def __init__(
        self,
        radio: "AudioCapable",
        *,
        device_name: str | None = None,
        tx_device_name: str | None = None,
        sample_rate: int = SAMPLE_RATE,
        channels: int = CHANNELS,
        frame_ms: int = FRAME_MS,
        tx_enabled: bool = True,
        tx_executor: Executor | None = None,
        label: str = "icom-lan",
        backend: AudioBackend | None = None,
        max_retries: int = 5,
        retry_base_delay: float = 1.0,
        retry_max_delay: float = 30.0,
        on_state_changed: Callable[[BridgeStateChange], None] | None = None,
        on_metrics: Callable[[BridgeMetrics], None] | None = None,
    ) -> None:
        self._radio = radio
        self._label = label
        self._device_name = device_name
        self._tx_device_name = tx_device_name
        self._sample_rate = sample_rate
        self._channels = channels
        self._frame_ms = frame_ms
        self._tx_enabled = tx_enabled
        self._tx_started = False
        self._tx_executor = tx_executor
        self._backend: AudioBackend = backend or PortAudioBackend()

        # State machine
        self._bridge_state: BridgeState = BridgeState.IDLE
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._on_state_changed = on_state_changed
        self._reconnect_task: asyncio.Task[None] | None = None
        self._reconnect_attempt: int = 0

        self._running = False
        # Input channels from the radio's negotiated codec, resolved on
        # start(). Defaults to 1 (mono); becomes 2 when the radio sends a
        # stereo codec (PCM_2CH_16BIT, OPUS_2CH, …) in which case
        # ``_rx_loop`` downmixes L+R → mono before writing to the loopback
        # OutputStream (which is fixed at ``self._channels`` = 1 mono —
        # WSJT-X / JS8Call are mono-only). Issue #1381.
        self._input_channels: int = 1
        self._rx_stream: TxStream | None = None  # radio → device (playback)
        self._tx_stream: RxStream | None = None  # device → radio (capture)
        self._rx_task: asyncio.Task[None] | None = None
        self._tx_task: asyncio.Task[None] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._decoder: Any = None
        self._subscription: Any = None
        self._samples_per_frame = sample_rate * frame_ms // 1000
        self._tx_queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)

        # Metrics
        self._rx_frames = 0
        self._tx_frames = 0
        self._rx_drops = 0
        self._rx_underruns = 0
        self._tx_overruns = 0
        self._rx_latency_samples: list[float] = []
        self._tx_latency_samples: list[float] = []
        self._last_rx_time: float = 0.0
        self._last_tx_time: float = 0.0
        self._start_time: float = 0.0
        self._last_rx_level_dbfs: float = -96.0
        self._last_tx_level_dbfs: float = -96.0
        self._on_metrics = on_metrics

        # Silence frame (raw PCM bytes)
        frame_bytes = self._samples_per_frame * channels * BYTES_PER_SAMPLE
        self._silence_bytes: bytes = b"\x00" * frame_bytes

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def label(self) -> str:
        return self._label

    @property
    def running(self) -> bool:
        return self._running

    @property
    def bridge_state(self) -> BridgeState:
        return self._bridge_state

    @property
    def metrics(self) -> BridgeMetrics:
        """Structured bridge telemetry snapshot."""
        uptime = time.monotonic() - self._start_time if self._running else 0.0
        rx_avg = (
            sum(self._rx_latency_samples) / len(self._rx_latency_samples)
            if self._rx_latency_samples
            else 0.0
        )
        tx_avg = (
            sum(self._tx_latency_samples) / len(self._tx_latency_samples)
            if self._tx_latency_samples
            else 0.0
        )
        rx_jitter = (
            _std_dev(self._rx_latency_samples) * 1000
            if self._rx_latency_samples
            else 0.0
        )
        tx_jitter = (
            _std_dev(self._tx_latency_samples) * 1000
            if self._tx_latency_samples
            else 0.0
        )
        return BridgeMetrics(
            running=self._running,
            label=self._label,
            bridge_state=self._bridge_state.value,
            reconnect_attempt=self._reconnect_attempt,
            rx_frames=self._rx_frames,
            tx_frames=self._tx_frames,
            rx_drops=self._rx_drops,
            rx_underruns=self._rx_underruns,
            tx_overruns=self._tx_overruns,
            uptime_seconds=round(uptime, 1),
            rx_interval_ms=round(rx_avg * 1000, 1),
            tx_interval_ms=round(tx_avg * 1000, 1),
            rx_jitter_ms=round(rx_jitter, 2),
            tx_jitter_ms=round(tx_jitter, 2),
            rx_level_dbfs=round(self._last_rx_level_dbfs, 1),
            tx_level_dbfs=round(self._last_tx_level_dbfs, 1),
            buffer_size=len(self._rx_latency_samples),
        )

    @property
    def stats(self) -> dict[str, Any]:
        """Bridge statistics as a dict (backward-compatible)."""
        return self.metrics.to_dict()

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    def _set_state(self, new: BridgeState, reason: str, attempt: int = 0) -> None:
        prev = self._bridge_state
        if prev == new:
            return
        self._bridge_state = new
        logger.info(
            "%s: state %s → %s (reason=%s, attempt=%d)",
            self._label,
            prev.value,
            new.value,
            reason,
            attempt,
        )
        if self._on_state_changed is not None:
            try:
                self._on_state_changed(
                    BridgeStateChange(
                        previous=prev, current=new, reason=reason, attempt=attempt
                    )
                )
            except Exception:
                logger.debug(
                    "%s: on_state_changed callback error",
                    self._label,
                    exc_info=True,
                )

    # ------------------------------------------------------------------
    # Stream setup / teardown (extracted for reconnect reuse)
    # ------------------------------------------------------------------

    async def _setup_streams(self) -> None:
        """Find device, open streams, subscribe to bus, create tasks.

        Raises on failure (RuntimeError, ValueError, OSError, etc.).
        """
        dev = _find_device_in_backend(self._backend, self._device_name)
        if dev is None:
            searched = self._device_name or "BlackHole/Loopback/VB-Cable/PipeWire"
            raise LoopbackNotFoundError(
                f"Virtual audio device not found (searched: {searched}). "
                f"Install a loopback driver: "
                f"macOS → brew install blackhole-2ch | "
                f"Linux → pw-loopback or pactl load-module module-null-sink | "
                f"Windows → vb-audio.com/Cable/"
            )

        dev_id = dev.id
        logger.info("%s: using device %r (id %d)", self._label, dev.name, int(dev_id))

        # --- RX path: radio → virtual device output ---
        self._rx_stream = self._backend.open_tx(
            dev_id,
            sample_rate=self._sample_rate,
            channels=self._channels,
            frame_ms=self._frame_ms,
        )
        await self._rx_stream.start()

        # Codec detection
        from icom_lan.types import AudioCodec

        _codec = getattr(self._radio, "audio_codec", None)
        self._is_opus = isinstance(_codec, AudioCodec) and _codec in (
            AudioCodec.OPUS_1CH,
            AudioCodec.OPUS_2CH,
        )

        # Resolve input channels from the negotiated codec (issue #1381).
        # The radio may have selected a stereo codec (PCM_2CH_16BIT default
        # since v0.17.0); the bridge output is mono so we downmix below.
        if isinstance(_codec, AudioCodec):
            self._input_channels = _AUDIO_CODEC_CHANNELS.get(_codec, 1)
        else:
            self._input_channels = 1

        logger.info(
            "%s: bridge codec=%s input_channels=%d output_channels=%d sample_rate=%d",
            self._label,
            _codec.name if isinstance(_codec, AudioCodec) else _codec,
            self._input_channels,
            self._channels,
            self._sample_rate,
        )

        if self._is_opus:
            _require_opuslib()
            import opuslib

            self._decoder = opuslib.Decoder(self._sample_rate, self._channels)
        else:
            self._decoder = None

        # Subscribe to AudioBus
        bus = self._radio.audio_bus
        self._subscription = bus.subscribe(name="audio-bridge")
        await self._subscription.start()
        self._rx_task = asyncio.create_task(self._rx_loop())

        # --- TX path: virtual device input → radio ---
        if self._tx_enabled:
            await self._radio.start_audio_tx_pcm(
                sample_rate=self._sample_rate,
                channels=self._channels,
                frame_ms=self._frame_ms,
            )
            self._tx_started = True

            tx_dev_id = dev_id
            if self._tx_device_name:
                tx_dev = _find_device_in_backend(self._backend, self._tx_device_name)
                if tx_dev is None:
                    logger.warning(
                        "%s: TX device %r not found, using RX device",
                        self._label,
                        self._tx_device_name,
                    )
                else:
                    tx_dev_id = tx_dev.id

            self._tx_queue = asyncio.Queue(maxsize=64)
            self._tx_stream = self._backend.open_rx(
                tx_dev_id,
                sample_rate=self._sample_rate,
                channels=self._channels,
                frame_ms=self._frame_ms,
            )
            await self._tx_stream.start(self._on_tx_capture)
            self._tx_task = asyncio.create_task(self._tx_loop())

    async def _teardown_streams(self) -> None:
        """Cancel tasks and stop streams.

        Does NOT call ``radio.stop_audio_tx_pcm()`` — that belongs in
        :meth:`stop` only.
        """
        if self._rx_task and not self._rx_task.done():
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
        self._rx_task = None

        if self._subscription is not None:
            self._subscription.stop()
            self._subscription = None

        if self._tx_task and not self._tx_task.done():
            self._tx_task.cancel()
            try:
                await self._tx_task
            except asyncio.CancelledError:
                pass
        self._tx_task = None

        if self._tx_stream is not None:
            try:
                await self._tx_stream.stop()
            except Exception:
                logger.debug("%s: TX stream stop error", self._label, exc_info=True)
            self._tx_stream = None

        if self._rx_stream is not None:
            try:
                await self._rx_stream.stop()
            except Exception:
                logger.debug("%s: RX stream stop error", self._label, exc_info=True)
            self._rx_stream = None

    # ------------------------------------------------------------------
    # Reconnect
    # ------------------------------------------------------------------

    def _on_stream_error(self, exc: Exception) -> None:
        """Called from _rx_loop/_tx_loop on stream failure.

        Idempotent — only the first call triggers reconnect.
        """
        if self._bridge_state != BridgeState.RUNNING:
            return
        logger.warning("%s: stream error, will reconnect: %s", self._label, exc)
        self._running = False
        self._set_state(BridgeState.RECONNECTING, "device_lost")
        if self._reconnect_task is None or self._reconnect_task.done():
            self._reconnect_task = asyncio.create_task(
                self._reconnect_loop(), name="bridge-reconnect"
            )

    async def _reconnect_loop(self) -> None:
        self._reconnect_attempt = 0
        while self._max_retries == 0 or self._reconnect_attempt < self._max_retries:
            delay = min(
                self._retry_base_delay * (2**self._reconnect_attempt),
                self._retry_max_delay,
            )
            self._reconnect_attempt += 1
            logger.info(
                "%s: reconnect attempt %d (delay=%.1fs)",
                self._label,
                self._reconnect_attempt,
                delay,
            )
            await asyncio.sleep(delay)

            # If stop() was called while we were sleeping, bail out
            if self._bridge_state not in (
                BridgeState.RECONNECTING,
                BridgeState.CONNECTING,
            ):
                return

            try:
                await self._teardown_streams()
                self._set_state(
                    BridgeState.CONNECTING, "retry", self._reconnect_attempt
                )
                await self._setup_streams()
                self._running = True
                self._set_state(
                    BridgeState.RUNNING, "reconnected", self._reconnect_attempt
                )
                self._reconnect_attempt = 0
                return
            except Exception as exc:
                logger.warning(
                    "%s: reconnect attempt %d failed: %s",
                    self._label,
                    self._reconnect_attempt,
                    exc,
                )
                self._set_state(
                    BridgeState.RECONNECTING, "retry_failed", self._reconnect_attempt
                )

        # Exhausted retries
        logger.error(
            "%s: max_retries=%d exhausted, giving up",
            self._label,
            self._max_retries,
        )
        self._set_state(BridgeState.FAILED, "max_retries", self._reconnect_attempt)

    # ------------------------------------------------------------------
    # Public start / stop
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start the audio bridge.

        Raises:
            ImportError: If sounddevice/numpy not installed.
            RuntimeError: If virtual audio device not found.
        """
        if self._running:
            logger.warning("%s: already running", self._label)
            return

        self._start_time = time.monotonic()
        self._loop = asyncio.get_running_loop()

        self._set_state(BridgeState.CONNECTING, "start")
        try:
            await self._setup_streams()
        except Exception:
            self._set_state(BridgeState.IDLE, "start_failed")
            raise

        self._running = True
        self._set_state(BridgeState.RUNNING, "started")

        direction = "RX+TX" if self._tx_enabled else "RX only"
        logger.info(
            "%s: started (%s, %dHz, %dch, %dms frames)",
            self._label,
            direction,
            self._sample_rate,
            self._channels,
            self._frame_ms,
        )

    async def stop(self) -> None:
        """Stop the audio bridge and release resources."""
        if self._bridge_state == BridgeState.IDLE:
            return

        self._running = False
        # Immediately leave RUNNING to prevent _on_stream_error from
        # scheduling new reconnect tasks while we tear down.
        self._set_state(BridgeState.IDLE, "stopped")

        # Cancel reconnect if in progress
        if self._reconnect_task and not self._reconnect_task.done():
            self._reconnect_task.cancel()
            try:
                await self._reconnect_task
            except asyncio.CancelledError:
                pass
        self._reconnect_task = None

        await self._teardown_streams()

        if self._tx_started:
            self._tx_started = False
            try:
                await self._radio.stop_audio_tx_pcm()
            except Exception:
                logger.debug("%s: stop TX error", self._label, exc_info=True)

        logger.info(
            "%s: stopped (rx=%d frames, tx=%d frames, drops=%d)",
            self._label,
            self._rx_frames,
            self._tx_frames,
            self._rx_drops,
        )

    # ------------------------------------------------------------------
    # Audio loops
    # ------------------------------------------------------------------

    def _emit_metrics(self) -> None:
        """Fire on_metrics callback with current telemetry."""
        if self._on_metrics is not None:
            try:
                self._on_metrics(self.metrics)
            except Exception:
                logger.debug(
                    "%s: on_metrics callback error", self._label, exc_info=True
                )

    def _on_tx_capture(self, frame: bytes) -> None:
        """Callback from RxStream — enqueue captured audio for TX processing.

        May be called from a worker thread (PortAudioBackend), so we
        schedule the enqueue on the event loop to avoid data races.
        """
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._enqueue_tx, frame)

    def _enqueue_tx(self, frame: bytes) -> None:
        """Thread-safe target for _on_tx_capture — runs on the event loop."""
        try:
            self._tx_queue.put_nowait(frame)
        except asyncio.QueueFull:
            self._tx_overruns += 1

    async def _rx_loop(self) -> None:
        """Read packets from AudioBus subscription, decode, write to device."""
        try:
            async for packet in self._subscription:
                if not self._running:
                    break
                if packet is None:
                    self._rx_drops += 1
                    if self._rx_drops <= 3:
                        logger.debug(
                            "%s: None packet (gap) #%d", self._label, self._rx_drops
                        )
                    if self._rx_stream and self._rx_stream.running:
                        await self._rx_stream.write(self._silence_bytes)
                    continue

                opus_data = getattr(packet, "data", None)
                if opus_data is None:
                    continue

                try:
                    if self._is_opus:
                        pcm_data = self._decoder.decode(
                            opus_data, self._samples_per_frame
                        )
                    else:
                        pcm_data = opus_data
                    now = time.monotonic()
                    if self._last_rx_time > 0:
                        delta = now - self._last_rx_time
                        self._rx_latency_samples.append(delta)
                        if len(self._rx_latency_samples) > 100:
                            self._rx_latency_samples.pop(0)
                    self._last_rx_time = now
                    self._rx_frames += 1
                    self._last_rx_level_dbfs = _rms_dbfs(pcm_data)
                    if self._rx_frames % 50 == 0:
                        self._emit_metrics()
                    if self._rx_stream and self._rx_stream.running:
                        out_data = pcm_data
                        if self._input_channels == 2:
                            out_data = _downmix_stereo_to_mono(out_data)
                        await self._rx_stream.write(out_data)
                except OSError:
                    raise  # device-level error → outer handler → reconnect
                except Exception as exc:
                    self._rx_drops += 1
                    if self._rx_drops <= 5 or self._rx_drops % 1000 == 0:
                        logger.warning(
                            "%s: decode error #%d: %s (data=%d bytes)",
                            self._label,
                            self._rx_drops,
                            exc,
                            len(opus_data),
                        )
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("%s: RX loop error", self._label, exc_info=True)
            self._on_stream_error(exc)

    async def _tx_loop(self) -> None:
        """Read captured audio from TX queue and push to the radio."""
        import numpy as np

        silence_threshold = 10  # ~-70dB for int16

        try:
            while self._running:
                # Check TX capture stream health periodically
                if self._tx_stream is not None and not self._tx_stream.running:
                    raise OSError("TX capture stream stopped unexpectedly")

                try:
                    pcm_bytes = await asyncio.wait_for(
                        self._tx_queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    continue

                frame_array = np.frombuffer(pcm_bytes, dtype=np.int16)
                if np.max(np.abs(frame_array)) < silence_threshold:
                    continue

                now = time.monotonic()
                if self._last_tx_time > 0:
                    delta = now - self._last_tx_time
                    self._tx_latency_samples.append(delta)
                    if len(self._tx_latency_samples) > 100:
                        self._tx_latency_samples.pop(0)
                self._last_tx_time = now
                self._tx_frames += 1
                self._last_tx_level_dbfs = _rms_dbfs(pcm_bytes)

                if self._tx_frames <= 3 or self._tx_frames % 1000 == 0:
                    peak = int(np.max(np.abs(frame_array)))
                    logger.info(
                        "%s: TX frame #%d, %d bytes, peak=%d",
                        self._label,
                        self._tx_frames,
                        len(pcm_bytes),
                        peak,
                    )

                try:
                    if self._is_opus:
                        await self._radio.push_audio_tx_opus(pcm_bytes)
                    else:
                        await self._radio.push_audio_tx_pcm(pcm_bytes)
                except Exception:
                    if self._tx_frames <= 5:
                        logger.warning("%s: TX push error", self._label, exc_info=True)
                    else:
                        logger.debug("%s: TX push error", self._label, exc_info=True)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.error("%s: TX loop error", self._label, exc_info=True)
            self._on_stream_error(exc)
