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
import math
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ..audio.pcm import PcmFrame, packet_to_pcm
from ..audio.resample import PcmResampler
from ..audio.route import (
    RxAudioSource,
    TxAudioSource,
    resolve_audio_route,
    rigctld_wsjtx_policy,
    tx_audio_facts,
)
from ..tci.frames import (
    HEADER_SIZE,
    PacketCounter,
    SampleType,
    StreamHeader,
    StreamType,
    convert_samples,
    pack_header,
    unpack_header,
)

logger = logging.getLogger(__name__)

__all__ = [
    "BEHIND_CLOSE_S",
    "RX_PUMP_QUEUE_DEPTH",
    "TX_FRAME_MS",
    "TciAudioSettings",
    "TciRxPump",
    "TciTxSession",
    "chrono_interval",
    "convert_tx_frame",
    "rx_audio_available",
    "tx_route_ready",
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


#: One radio TX frame the playout task pushes per tick (spec 8.3 item 4).
TX_FRAME_MS = 20

#: Playout depth past the target that still stays buffered before the
#: oldest is dropped and counted (spec 8.3 item 4).
TX_OVERRUN_SLACK_MS = 500

#: Client TX frame channels the server accepts (spec 8.1/8.3 step 3).
TX_CLIENT_CHANNELS = (1, 2)


def tx_route_ready(radio: Any) -> Any:
    """PCM TX route facts for a ``source=tci`` key, else ``None`` (8.3).

    The shared :func:`tx_audio_facts` plus the TCI-only rules: release 1
    serves radios whose ``audio_tx_codec`` is ``PCM_1CH_16BIT`` with an
    accepted route and a usable TX lease surface
    (``audio_session.acquire_tx``). The web seat's full neutral
    lifecycle probe is not required here: the lease is the only TX
    audio path TCI uses. Anything else refuses the key with no PTT
    write.
    """
    from ..types import AudioCodec

    has_routing = any(
        callable(getattr(radio, f"set_data{n}_mod_input", None)) for n in (1, 2, 3)
    )
    facts = tx_audio_facts(radio, has_mod_input_routing=has_routing)
    if facts.codec is not AudioCodec.PCM_1CH_16BIT or not facts.route_accepted:
        return None
    session = getattr(radio, "audio_session", None)
    acquire = getattr(session, "acquire_tx", None)
    if not callable(acquire) or not asyncio.iscoroutinefunction(acquire):
        return None
    return facts


def chrono_interval(samples: int, channels: int, sample_rate: int) -> float:
    """``TX_CHRONO`` cadence for one client frame (spec 8.3).

    ``samples`` counts interleaved scalars (the header ``length``),
    ``frames`` is the per-channel count (``samples // channels``); the
    interval is ``samples / (channels * sample_rate)``.
    """
    frames = samples // channels
    if frames <= 0 or sample_rate <= 0 or channels <= 0:
        raise ValueError("TX_CHRONO needs positive samples, channels and rate")
    return frames / sample_rate


def convert_tx_frame(
    data: bytes,
    *,
    channels: int,
    sample_type: SampleType | int,
    iq: bool,
    from_rate: int,
    to_rate: int,
) -> bytes:
    """Convert one client TX frame to s16le mono at the radio rate (8.3.3).

    Two client channels in DIGU/DIGL are I/Q: channel 0 is used;
    otherwise the pair is averaged. Float is resampled in the float
    domain, then clamped to [-1, 1] with NaN/Inf replaced by 0, then
    scaled to int16 — so a NaN/Inf frame yields silence, not noise.
    """
    if channels not in TX_CLIENT_CHANNELS:
        raise ValueError(f"client TX channels must be 1 or 2, got {channels}")
    if from_rate <= 0 or to_rate <= 0:
        raise ValueError("client and radio TX rates must be positive")
    if not data:
        return b""
    floats = array.array("f", convert_samples(bytes(data), sample_type, SampleType.FLOAT32))
    usable = len(floats) - (len(floats) % channels)
    del floats[usable:]
    if channels == 2:
        if iq:
            mono = floats[::2]
        else:
            mono = array.array("f", ((a + b) / 2 for a, b in zip(floats[::2], floats[1::2])))
    else:
        mono = floats
    resampled = _resample_mono_float(list(mono), from_rate, to_rate)
    out = array.array("h", (0 for _ in resampled))
    for index, value in enumerate(resampled):
        if not math.isfinite(value):
            out[index] = 0
        elif value >= 1.0:
            out[index] = 32767
        elif value <= -1.0:
            out[index] = -32768
        else:
            out[index] = int(round(value * 32767))
    return out.tobytes()


def _resample_mono_float(values: list[float], from_rate: int, to_rate: int) -> list[float]:
    """Linear-interpolate one mono float channel between rates."""
    if to_rate == from_rate or len(values) < 2:
        return list(values)
    import numpy as np

    source = np.asarray(values, dtype=np.float64)
    count = max(1, round(len(source) * to_rate / from_rate))
    positions = np.linspace(0.0, float(len(source) - 1), count)
    return [float(v) for v in np.interp(positions, np.arange(len(source)), source)]


class TciTxSession:
    """One keyed client's TX path: chrono, conversion, playout (spec 8.3).

    Created after ``trx:0,true,tci`` is accepted, holding the TX lease
    from ``radio.audio_session.acquire_tx``. Client frames arrive
    through :meth:`feed_client_frame`; the playout task pushes one
    :data:`TX_FRAME_MS` radio frame per tick, but only while the
    authority still holds ``PTT(owner)`` — every push runs inside
    ``authority.owned_tx_submission(owner)``, so a frame for A never
    reaches B's over. ``tick`` is the single playout step, used by the
    task loop and directly by tests.
    """

    def __init__(
        self,
        radio: Any,
        owner: str,
        authority: Any,
        lease: Any,
        *,
        settings: TciAudioSettings,
        iq: bool,
        emit: Callable[[bytes], None],
        clock: Callable[[], float] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self._radio = radio
        self._owner = owner
        self._authority = authority
        self._lease = lease
        self._settings = settings
        self._iq = iq
        self._emit = emit
        self._clock = clock or time.monotonic
        self._sleep = sleep or asyncio.sleep
        self._tx_rate = int(getattr(radio, "audio_sample_rate", 48000) or 48000)
        self._buffer = bytearray()
        self._chrono_task: asyncio.Task[None] | None = None
        self._playout_task: asyncio.Task[None] | None = None
        self._chrono_counter = PacketCounter()
        self.chrono_times: list[float] = []
        self.buffering_ms_override: int | None = None
        self.pushed = 0
        self.underruns = 0
        self.overruns = 0
        self.dropped_no_owner = 0
        self.dropped_not_owner = 0
        self.dropped_malformed = 0
        self._warn_counts: dict[str, int] = {}

    @property
    def lease(self) -> Any:
        """The TX lease this session pushes through."""
        return self._lease

    @property
    def running(self) -> bool:
        """Whether the chrono/playout tasks are up."""
        tasks = (self._chrono_task, self._playout_task)
        return any(task is not None and not task.done() for task in tasks)

    def _target_bytes(self) -> int:
        millis = (
            self.buffering_ms_override
            if self.buffering_ms_override is not None
            else self._settings.buffering_ms
        )
        return self._tx_rate * millis // 1000 * 2

    def _max_bytes(self) -> int:
        return self._target_bytes() + self._tx_rate * TX_OVERRUN_SLACK_MS // 1000 * 2

    def _frame_bytes(self) -> int:
        return self._tx_rate * TX_FRAME_MS // 1000 * 2

    async def start(self) -> None:
        """Launch the chrono and playout tasks."""
        self._chrono_task = asyncio.create_task(self._chrono_loop())
        self._playout_task = asyncio.create_task(self._playout_loop())

    async def stop(self) -> None:
        """Cancel both tasks and flush the buffer (unkey/disconnect, 8.3.4)."""
        for task in (self._chrono_task, self._playout_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._chrono_task, self._playout_task):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.debug("tci tx stop failed", exc_info=True)
        self._chrono_task = self._playout_task = None
        self.flush()

    def flush(self) -> None:
        """Drop all buffered TX audio (unkey, disconnect, gate close)."""
        self._buffer.clear()

    def feed_pcm(self, pcm: bytes) -> None:
        """Buffer s16le mono at the radio rate (conversion already done)."""
        self._buffer.extend(pcm)
        if len(self._buffer) > self._max_bytes():
            del self._buffer[: len(self._buffer) - self._max_bytes()]
            self.overruns += 1

    def feed_client_frame(
        self, data: bytes, *, channels: int, sample_rate: int, sample_type: SampleType | int
    ) -> None:
        """Convert one client TX frame and buffer it for playout."""
        try:
            pcm = convert_tx_frame(
                bytes(data),
                channels=channels,
                sample_type=sample_type,
                iq=self._iq,
                from_rate=sample_rate,
                to_rate=self._tx_rate,
            )
        except ValueError:
            self.dropped_malformed += 1
            logger.debug("tci tx malformed client frame", exc_info=True)
            return
        if pcm:
            self.feed_pcm(pcm)

    async def emit_chrono(self) -> None:
        """Emit one ``TX_CHRONO`` marker at the client frame cadence."""
        header = StreamHeader(
            receiver=0,
            sample_rate=self._settings.samplerate,
            sample_type=_sample_type_of(self._settings.sample_type),
            codec=0,
            crc=0,
            length=self._settings.samples,
            stream_type=StreamType.TX_CHRONO,
            channels=1,
            count=self._chrono_counter.next(),
        )
        self.chrono_times.append(self._clock())
        self._emit(pack_header(header))

    async def _chrono_loop(self) -> None:
        interval = chrono_interval(
            self._settings.samples, 1, self._settings.samplerate
        )
        try:
            while True:
                await self.emit_chrono()
                await self._sleep(interval)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("tci tx chrono failed", exc_info=True)

    async def _playout_loop(self) -> None:
        step = TX_FRAME_MS / 1000
        try:
            while len(self._buffer) < self._target_bytes():
                await self._sleep(step)
            while True:
                await self._sleep(step)
                await self.tick()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.debug("tci tx playout failed", exc_info=True)

    def _owner_keyed(self) -> bool:
        """Whether the authority still holds ``PTT(owner)`` for this session."""
        if self._authority is None or self._lease is None:
            return False
        try:
            return self._authority.tx_holder_nowait() == self._owner
        except Exception:
            logger.debug("tci tx holder probe failed", exc_info=True)
            return False

    async def tick(self) -> None:
        """Push one radio frame, silence on underrun, drop when not ours."""
        if self._lease is None:
            self.dropped_no_owner += 1
            return
        if not self._owner_keyed():
            self.dropped_not_owner += 1
            self._warn_tx_throttled(
                "not-owner",
                "tci tx: dropping frame with no keyed owner",
            )
            self.flush()
            return
        need = self._frame_bytes()
        if len(self._buffer) >= need:
            frame = bytes(self._buffer[:need])
            del self._buffer[:need]
        else:
            self.underruns += 1
            frame = b"\x00" * need
            self._buffer.clear()
        admission = self._authority.owned_tx_submission
        owner = self._owner
        try:
            await self._lease.push(
                frame, admission=lambda: admission(owner)
            )
        except Exception:
            logger.debug("tci tx push failed", exc_info=True)
            return
        self.pushed += 1

    def _warn_tx_throttled(self, key: str, message: str, *args: Any) -> None:
        """Once-per-frame TX warnings, throttled like the web audio seat.

        Same policy :meth:`AudioHandler._warn_tx_throttled` uses: the
        first three occurrences log in full, then every hundredth with
        the suppressed count, so a hot TX path never buries diagnostics.
        """
        count = self._warn_counts.get(key, 0) + 1
        self._warn_counts[key] = count
        if count <= 3:
            logger.warning("%s (#%d)", message % args if args else message, count)
        elif count % 100 == 0:
            logger.warning(
                "%s (#%d, suppressed %d)",
                message % args if args else message,
                count,
                count - (3 if count == 100 else count - 100) - 1,
            )
