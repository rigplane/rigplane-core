"""TCI adapter audio phase: per-client RX pump and stream settings (MOR-3821 A1).

``TciRxPump`` is the TCI-side counterpart of the web
``AudioBroadcaster``: the broadcaster re-encodes radio PCM for browser
WebSockets, while this pump re-blocks and converts it into TCI binary
frames for one TCI client with that client's own stream settings.
Radio facts (codec, rate, route) come from the profile and the
``AudioTransport`` surface, never from a radio-model branch.
"""

from __future__ import annotations

import array
import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..audio.pcm import PcmFrame, packet_to_pcm
from ..audio.resample import PcmResampler
from ..audio.route import RxAudioSource, resolve_audio_route
from ..tci.frames import (
    SampleType,
    StreamHeader,
    StreamType,
    convert_samples,
    pack_header,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BEHIND_CLOSE_S",
    "RX_PUMP_QUEUE_DEPTH",
    "TciAudioSettings",
    "TciRxPump",
    "rx_audio_available",
]

#: Sample rates a TCI client may request (spec 3.4).
SUPPORTED_SAMPLERATES = (8000, 12000, 24000, 48000)

#: Default ``audio_stream_samples`` per client rate (spec 4.3).
DEFAULT_SAMPLES = {48000: 2048, 24000: 1024, 12000: 512, 8000: 256}

#: Bounds for ``audio_stream_samples`` and ``tx_stream_audio_buffering`` (spec 4.3).
MIN_SAMPLES = 100
MAX_SAMPLES = 2048
MIN_BUFFERING_MS = 50
MAX_BUFFERING_MS = 500

#: Sample-type names the server accepts, as the client spells them (spec 3.4).
SAMPLE_TYPES = ("int16", "int24", "int32", "float32")

#: Per-session RX frame queue depth: past it the oldest frame is dropped
#: and counted, never blocking the radio-side loop (spec 8.2 item 3).
RX_PUMP_QUEUE_DEPTH = 32

#: A client whose queue never drains for this long is closed (spec 8.2 item 3).
BEHIND_CLOSE_S = 5.0


def _sample_type_of(name: str) -> SampleType:
    return {
        "int16": SampleType.INT16,
        "int24": SampleType.INT24,
        "int32": SampleType.INT32,
        "float32": SampleType.FLOAT32,
    }[name]


def _downmix_to_mono(payload: bytes) -> bytes:
    """Average one interleaved stereo s16le frame down to mono."""
    values = array.array("h", payload)
    if len(values) % 2:
        values = values[: len(values) - 1]
    mono = array.array("h", ((a + b) // 2 for a, b in zip(values[::2], values[1::2])))
    return mono.tobytes()


def rx_audio_available(radio: Any) -> bool:
    """Whether this radio can serve TCI RX audio right now (spec 8.2).

    The route facts must name a usable RX source *and* the radio must
    expose the session surface the pump subscribes through, with a
    known wire codec. Anything less and every ``audio_*`` command is
    ignored, the control-phase behaviour.
    """
    try:
        route = resolve_audio_route(radio)
    except Exception:
        return False
    if route.rx_audio_source is RxAudioSource.UNAVAILABLE:
        return False
    session = getattr(radio, "audio_session", None)
    if session is None or not callable(getattr(session, "subscribe_rx", None)):
        return False
    return getattr(radio, "audio_codec", None) is not None


@dataclass
class TciAudioSettings:
    """Per-client TCI stream settings (spec 4.3/8.1).

    Every setter answers with the value in force: unsupported values
    keep (echo) the current one, clamps reply with the clamped value.
    """

    samplerate: int = 48000
    sample_type: str = "float32"
    channels: int = 1
    samples: int = 2048
    buffering_ms: int = 50

    @staticmethod
    def samples_for(rate: int) -> int:
        """Default ``audio_stream_samples`` for a client rate (spec 4.3)."""
        return DEFAULT_SAMPLES.get(rate, 2048)

    @classmethod
    def from_radio(cls, radio: Any) -> TciAudioSettings:
        """Seed client settings from the radio rate when it is servable."""
        rate = getattr(radio, "audio_sample_rate", 48000)
        rate = rate if rate in SUPPORTED_SAMPLERATES else 48000
        return cls(samplerate=rate, samples=cls.samples_for(rate))

    def set_samplerate(self, rate: int) -> int:
        """Unsupported values keep (echo) the current rate (spec 8.1)."""
        if rate in SUPPORTED_SAMPLERATES:
            self.samplerate = rate
            self.samples = self.samples_for(rate)
        return self.samplerate

    def set_sample_type(self, name: str) -> str:
        """Unknown formats keep (echo) the current type; reply is lower case."""
        normalized = str(name).lower()
        if normalized in SAMPLE_TYPES:
            self.sample_type = normalized
        return self.sample_type

    def set_channels(self, count: int) -> int:
        """The server announces and serves real mono in every mode (spec 8.2).

        The request is clamped to 1..2 and then answered with the value
        in force, 1: a request for 2 gets the current value back, the
        same "unsupported value ignored, current echoed" treatment the
        spec gives the other stream settings.
        """
        clamped = min(2, max(1, int(count)))
        _ = clamped
        self.channels = 1
        return self.channels

    def set_samples(self, count: int) -> int:
        """Clamp ``audio_stream_samples`` to 100..2048 (spec 4.3)."""
        self.samples = min(MAX_SAMPLES, max(MIN_SAMPLES, int(count)))
        return self.samples

    def set_buffering(self, millis: int) -> int:
        """Clamp ``tx_stream_audio_buffering`` to 50..500 ms (spec 4.3)."""
        self.buffering_ms = min(MAX_BUFFERING_MS, max(MIN_BUFFERING_MS, int(millis)))
        return self.buffering_ms


class TciRxPump:
    """One client's RX path: subscription, resample, re-block, frame (spec 8.2).

    ``radio.audio_session.subscribe_rx(owner)`` registers radio-side
    demand; each delivered packet becomes a :class:`PcmFrame` through
    the shared :func:`packet_to_pcm` helper, is resampled to the
    client rate, downmixed to real mono, re-blocked to the client's
    ``audio_stream_samples`` and queued as a complete TCI binary
    frame. The queue is bounded: on overflow the oldest frame is
    dropped and counted, and a client that never drains for
    :data:`BEHIND_CLOSE_S` is reported stale so the server closes it.
    Neither ever blocks the radio-side loop or another client.
    """

    def __init__(
        self,
        radio: Any,
        owner: str,
        *,
        settings: TciAudioSettings | None = None,
        queue_depth: int = RX_PUMP_QUEUE_DEPTH,
        clock: Any = None,
    ) -> None:
        self._radio = radio
        self._owner = owner
        self._settings = settings or TciAudioSettings()
        self._queue: deque[bytes] = deque(maxlen=queue_depth)
        self._clock = clock or time.monotonic
        self._subscription: Any = None
        self._task: asyncio.Task[None] | None = None
        self._ready: asyncio.Event | None = None
        self._resampler: PcmResampler | None = None
        self._resampler_key: tuple[int, int] | None = None
        self._reblock = bytearray()
        self._counter = 0
        self._seq = 0
        self._opus_decoder: Any = None
        self._opus_dead = False
        self.dropped = 0
        self.gaps = 0
        self.skipped = 0
        self.emitted = 0
        self.behind_since: float | None = None

    @property
    def settings(self) -> TciAudioSettings:
        """The live per-client settings this pump serves."""
        return self._settings

    @property
    def running(self) -> bool:
        """Whether the pump task and subscription are up."""
        return self._task is not None and not self._task.done()

    def stale_behind(self, now: float) -> bool:
        """True when the client stayed behind for :data:`BEHIND_CLOSE_S`."""
        return (
            self.behind_since is not None and now - self.behind_since >= BEHIND_CLOSE_S
        )

    async def start(self) -> None:
        """Subscribe and start pumping; raises when the radio cannot serve."""
        session = getattr(self._radio, "audio_session", None)
        if session is None or not callable(getattr(session, "subscribe_rx", None)):
            raise RuntimeError("radio has no RX session surface")
        codec = getattr(self._radio, "audio_codec", None)
        if codec is None:
            raise RuntimeError("radio has no audio codec")
        self._subscription = await session.subscribe_rx(self._owner)
        self._ready = asyncio.Event()
        self._task = asyncio.create_task(self._run())

    def reconfigure(self, settings: TciAudioSettings) -> None:
        """Apply new client settings live: pending partial blocks drop."""
        self._settings = settings
        self._resampler = None
        self._resampler_key = None
        self._reblock.clear()

    async def stop(self) -> None:
        """Cancel the pump and close the subscription (idempotent)."""
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.debug("tci rx pump stop failed", exc_info=True)
        subscription, self._subscription = self._subscription, None
        if subscription is not None:
            try:
                await subscription.release()
            except Exception:
                logger.debug("tci rx release failed", exc_info=True)

    def drain(self) -> list[bytes]:
        """Take every queued frame; clears the behind marker when empty."""
        frames = list(self._queue)
        self._queue.clear()
        if self._ready is not None and not frames:
            self._ready.clear()
        if not self._queue:
            self.behind_since = None
        return frames

    async def get_frame(self, timeout: float = 5.0) -> bytes | None:
        """Wait for one queued frame (tests); ``None`` on timeout."""
        if self._ready is None:
            return None
        try:
            async with asyncio.timeout(timeout):
                while not self._queue:
                    await self._ready.wait()
                    if self._ready is not None:
                        self._ready.clear()
        except TimeoutError:
            return None
        try:
            frame = self._queue.popleft()
        except IndexError:
            return None
        if not self._queue:
            self.behind_since = None
        return frame

    def _enqueue(self, frame: bytes) -> None:
        was_full = len(self._queue) == self._queue.maxlen
        self._queue.append(frame)
        if was_full:
            self.dropped += 1
            if self.behind_since is None:
                self.behind_since = self._clock()
        self.emitted += 1
        if self._ready is not None:
            self._ready.set()

    def _opus_decode(self, data: bytes, channels: int) -> bytes | None:
        """Default Opus RX decode through ``opuslib``, 20 ms frames."""
        if self._opus_dead:
            return None
        try:
            import opuslib
        except ImportError:
            self._opus_dead = True
            return None
        rate = int(getattr(self._radio, "audio_sample_rate", 48000) or 48000)
        key = (rate, channels)
        decoder = self._opus_decoder
        if not isinstance(decoder, tuple) or decoder[0] != key:
            try:
                decoder = (key, opuslib.Decoder(rate, channels))
            except Exception:
                logger.debug("tci rx opus decoder unavailable", exc_info=True)
                self._opus_dead = True
                return None
            self._opus_decoder = decoder
        try:
            return decoder[1].decode(data, rate // 50)
        except Exception:
            logger.debug("tci rx opus decode failed", exc_info=True)
            return None

    async def _run(self) -> None:
        subscription = self._subscription
        assert subscription is not None
        radio_rate = int(getattr(self._radio, "audio_sample_rate", 48000) or 48000)
        codec = getattr(self._radio, "audio_codec")
        try:
            async for packet in subscription:
                if packet is None:
                    self.gaps += 1
                    continue
                frame = packet_to_pcm(
                    packet,
                    codec=codec,
                    sample_rate=radio_rate,
                    seq=self._seq,
                    opus_decoder=self._opus_decode,
                )
                self._seq += 1
                if frame is None:
                    self.skipped += 1
                    continue
                self._emit_frame(frame)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("tci rx pump failed", exc_info=True)

    def _emit_frame(self, frame: PcmFrame) -> None:
        payload = (
            _downmix_to_mono(frame.payload) if frame.channels == 2 else frame.payload
        )
        key = (frame.sample_rate, self._settings.samplerate)
        if self._resampler is None or self._resampler_key != key:
            self._resampler = PcmResampler(
                from_rate=frame.sample_rate,
                to_rate=self._settings.samplerate,
                channels=1,
            )
            self._resampler_key = key
        out = self._resampler.process(payload)
        self._reblock.extend(out)
        need = self._settings.samples * 2
        while len(self._reblock) >= need:
            chunk = bytes(self._reblock[:need])
            del self._reblock[:need]
            self._enqueue(self._build_frame(chunk))

    def _build_frame(self, chunk: bytes) -> bytes:
        sample_type = _sample_type_of(self._settings.sample_type)
        body = convert_samples(chunk, SampleType.INT16, sample_type)
        header = StreamHeader(
            receiver=0,
            sample_rate=self._settings.samplerate,
            sample_type=sample_type,
            codec=0,
            crc=0,
            length=self._settings.samples,
            stream_type=StreamType.RX_AUDIO,
            channels=1,
            count=self._counter % (1 << 32),
        )
        self._counter += 1
        return pack_header(header) + body
