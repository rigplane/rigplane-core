"""AudioBackend protocol and implementations.

Defines the abstract ``AudioBackend`` interface for discovering devices and
opening RX/TX audio streams, plus two concrete implementations:

- **PortAudioBackend** — wraps *sounddevice* + *numpy* (requires ``[bridge]``
  extras).
- **FakeAudioBackend** — deterministic, dependency-free backend for tests.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any, Callable, NewType, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Identifiers & descriptors
# ---------------------------------------------------------------------------

AudioDeviceId = NewType("AudioDeviceId", int)
"""Opaque device identifier (maps to a host-API device index)."""


@dataclass(frozen=True, slots=True)
class AudioDeviceInfo:
    """Normalized audio device descriptor returned by a backend."""

    id: AudioDeviceId
    name: str
    input_channels: int = 0
    output_channels: int = 0
    default_samplerate: int = 48_000
    is_default_input: bool = False
    is_default_output: bool = False

    @property
    def supports_rx(self) -> bool:
        return self.input_channels > 0

    @property
    def supports_tx(self) -> bool:
        return self.output_channels > 0

    @property
    def duplex(self) -> bool:
        return self.supports_rx and self.supports_tx


# ---------------------------------------------------------------------------
# Stream protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class RxStream(Protocol):
    """Readable audio capture stream."""

    @property
    def running(self) -> bool: ...

    async def start(self, callback: Callable[[bytes], None]) -> None:
        """Begin capture; deliver PCM s16le frames to *callback*."""
        ...

    async def stop(self) -> None: ...


@runtime_checkable
class TxStream(Protocol):
    """Writable audio playback stream."""

    @property
    def running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def write(self, frame: bytes) -> None:
        """Queue one PCM s16le frame for playback."""
        ...


# ---------------------------------------------------------------------------
# Backend protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class AudioBackend(Protocol):
    """Abstract audio backend capable of listing devices and opening streams.

    **Tier 2 — Best-effort.** Import path:
    ``from icom_lan.audio.backend import AudioBackend`` (also lazily exposed
    on the top-level ``icom_lan`` package via PEP 562 ``__getattr__``).

    The contract is the four methods declared below: :meth:`list_devices`,
    :meth:`check_sample_rate`, :meth:`open_rx`, :meth:`open_tx`. Streams
    returned by ``open_rx`` / ``open_tx`` follow the :class:`RxStream` and
    :class:`TxStream` protocols.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def list_devices(self) -> list[AudioDeviceInfo]: ...

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        """Check whether *device* supports *sample_rate* for the given direction.

        Args:
            device: Target device id.
            sample_rate: Desired rate in Hz.
            direction: ``"rx"`` (capture) or ``"tx"`` (playback).

        Returns:
            ``True`` if the rate is supported, ``False`` otherwise.
        """
        ...

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> RxStream: ...

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> TxStream: ...


# ---------------------------------------------------------------------------
# PortAudioBackend (sounddevice)
# ---------------------------------------------------------------------------

_DEPENDENCY_HINT = (
    "PortAudioBackend requires optional dependencies sounddevice and numpy. "
    "Install with: pip install icom-lan[bridge]"
)


def _ensure_portaudio_deps() -> tuple[Any, Any]:
    """Return ``(sounddevice, numpy)`` or raise :class:`ImportError`."""
    try:
        import sounddevice as sd
    except ImportError as exc:
        raise ImportError(_DEPENDENCY_HINT) from exc
    try:
        import numpy as np
    except ImportError as exc:
        raise ImportError(_DEPENDENCY_HINT) from exc
    return sd, np


class _PortAudioRxStream:
    """RxStream backed by a sounddevice InputStream."""

    def __init__(
        self,
        sd: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
    ) -> None:
        self._sd = sd
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._stream: Any = None
        self._task: asyncio.Task[None] | None = None
        self._callback: Callable[[bytes], None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self.running:
            raise RuntimeError("RX stream already running.")
        self._callback = callback
        self._stream = self._sd.InputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device_index,
            blocksize=self._blocksize,
            latency="low",
        )
        self._stream.start()
        self._task = asyncio.create_task(self._loop(), name="portaudio-rx")

    async def stop(self) -> None:
        stream = self._stream
        task = self._task
        self._stream = None
        self._task = None
        self._callback = None
        # Close stream FIRST — unblocks executor thread stuck in stream.read()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("portaudio-rx: stream stop failed", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("portaudio-rx: stream close failed", exc_info=True)
        # Now cancel the task (thread is already unblocked)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        stream = self._stream
        try:
            while True:
                data, _overflowed = await asyncio.to_thread(
                    stream.read, self._blocksize
                )
                cb = self._callback
                if cb is None:
                    continue
                pcm = bytes(data.tobytes()) if hasattr(data, "tobytes") else bytes(data)
                cb(pcm)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("portaudio-rx: loop failed", exc_info=True)


class _PortAudioTxStream:
    """TxStream backed by a sounddevice OutputStream."""

    def __init__(
        self,
        sd: Any,
        np: Any,
        device_index: int,
        sample_rate: int,
        channels: int,
        blocksize: int,
    ) -> None:
        self._sd = sd
        self._np = np
        self._device_index = device_index
        self._sample_rate = sample_rate
        self._channels = channels
        self._blocksize = blocksize
        self._stream: Any = None
        self._task: asyncio.Task[None] | None = None
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=64)

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self.running:
            raise RuntimeError("TX stream already running.")
        self._stream = self._sd.OutputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            device=self._device_index,
            blocksize=self._blocksize,
            latency="low",
        )
        self._stream.start()
        self._queue = asyncio.Queue(maxsize=64)
        self._task = asyncio.create_task(self._loop(), name="portaudio-tx")

    async def stop(self) -> None:
        stream = self._stream
        task = self._task
        self._stream = None
        self._task = None
        self._queue = asyncio.Queue(maxsize=64)
        # Close stream FIRST — unblocks executor thread stuck in stream.write()
        if stream is not None:
            try:
                stream.stop()
            except Exception:
                logger.debug("portaudio-tx: stream stop failed", exc_info=True)
            try:
                stream.close()
            except Exception:
                logger.debug("portaudio-tx: stream close failed", exc_info=True)
        # Now cancel the task (thread is already unblocked)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def write(self, frame: bytes) -> None:
        if not self.running:
            raise RuntimeError("TX stream is not running.")
        await self._queue.put(frame)

    async def _loop(self) -> None:
        stream = self._stream
        np = self._np
        channels = self._channels
        try:
            while True:
                pcm = await self._queue.get()
                arr = np.frombuffer(pcm, dtype=np.int16).reshape(-1, channels)
                await asyncio.to_thread(stream.write, arr)
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.warning("portaudio-tx: loop failed", exc_info=True)


class PortAudioBackend:
    """AudioBackend backed by PortAudio via *sounddevice*.

    **Tier 2 — Best-effort.** Import path:
    ``from icom_lan.audio.backend import PortAudioBackend``. Implements the
    :class:`AudioBackend` protocol on top of the optional ``[bridge]`` extras
    (``sounddevice`` + ``numpy``); dependencies are loaded lazily on first
    method call so importing this class does not require PortAudio at import
    time.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def __init__(
        self,
        *,
        dependency_loader: Callable[[], tuple[Any, Any]] | None = None,
    ) -> None:
        self._sd: Any = None
        self._np: Any = None
        self._dependency_loader = dependency_loader

    @property
    def sounddevice_module(self) -> Any | None:
        """Return the underlying sounddevice module, or None if not loaded."""
        try:
            sd, _ = self._ensure_deps()
            return sd
        except ImportError:
            return None

    def _ensure_deps(self) -> tuple[Any, Any]:
        if self._sd is not None and self._np is not None:
            return self._sd, self._np
        if self._dependency_loader is not None:
            try:
                self._sd, self._np = self._dependency_loader()
            except ImportError as exc:
                raise ImportError(_DEPENDENCY_HINT) from exc
        else:
            self._sd, self._np = _ensure_portaudio_deps()
        return self._sd, self._np

    def list_devices(self) -> list[AudioDeviceInfo]:
        sd, _ = self._ensure_deps()
        raw_devices = list(sd.query_devices())
        default_raw = getattr(getattr(sd, "default", None), "device", None)
        default_in: int | None = None
        default_out: int | None = None
        if isinstance(default_raw, (list, tuple)) and len(default_raw) >= 2:
            default_in = int(default_raw[0]) if default_raw[0] is not None else None
            default_out = int(default_raw[1]) if default_raw[1] is not None else None

        result: list[AudioDeviceInfo] = []
        for idx, raw in enumerate(raw_devices):
            dev_idx = int(raw.get("index", idx))
            result.append(
                AudioDeviceInfo(
                    id=AudioDeviceId(dev_idx),
                    name=str(raw.get("name", f"device-{dev_idx}")),
                    input_channels=int(raw.get("max_input_channels", 0)),
                    output_channels=int(raw.get("max_output_channels", 0)),
                    default_samplerate=int(raw.get("default_samplerate", 48_000)),
                    is_default_input=(default_in is not None and dev_idx == default_in),
                    is_default_output=(
                        default_out is not None and dev_idx == default_out
                    ),
                )
            )
        return result

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        sd, _ = self._ensure_deps()
        idx = int(device)
        try:
            if direction == "rx":
                sd.check_input_settings(device=idx, samplerate=sample_rate)
            else:
                sd.check_output_settings(device=idx, samplerate=sample_rate)
            return True
        except Exception:
            return False

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> RxStream:
        sd, _ = self._ensure_deps()
        blocksize = (sample_rate * frame_ms) // 1000
        return _PortAudioRxStream(
            sd,
            device_index=int(device),
            sample_rate=sample_rate,
            channels=channels,
            blocksize=blocksize,
        )

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> TxStream:
        sd, np = self._ensure_deps()
        blocksize = (sample_rate * frame_ms) // 1000
        return _PortAudioTxStream(
            sd,
            np,
            device_index=int(device),
            sample_rate=sample_rate,
            channels=channels,
            blocksize=blocksize,
        )


# ---------------------------------------------------------------------------
# FakeAudioBackend (for tests)
# ---------------------------------------------------------------------------


class FakeRxStream:
    """Test double: records lifecycle and delivers injected frames."""

    def __init__(self) -> None:
        self._running = False
        self._callback: Callable[[bytes], None] | None = None
        self.started_count = 0
        self.stopped_count = 0
        self.fail_on_inject: Exception | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self, callback: Callable[[bytes], None]) -> None:
        if self._running:
            raise RuntimeError("FakeRxStream already running.")
        self._callback = callback
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self._running = False
        self._callback = None
        self.stopped_count += 1

    def inject_frame(self, frame: bytes) -> None:
        """Push a frame to the registered callback (test helper).

        If *fail_on_inject* is set, raises it once and clears the flag.
        """
        exc = self.fail_on_inject
        if exc is not None:
            self.fail_on_inject = None
            raise exc
        if self._callback is not None:
            self._callback(frame)


class FakeTxStream:
    """Test double: records lifecycle and captures written frames."""

    def __init__(self) -> None:
        self._running = False
        self.started_count = 0
        self.stopped_count = 0
        self.written_frames: list[bytes] = []
        self.fail_on_write: OSError | None = None

    @property
    def running(self) -> bool:
        return self._running

    async def start(self) -> None:
        if self._running:
            raise RuntimeError("FakeTxStream already running.")
        self._running = True
        self.started_count += 1

    async def stop(self) -> None:
        self._running = False
        self.stopped_count += 1

    async def write(self, frame: bytes) -> None:
        if not self._running:
            raise RuntimeError("FakeTxStream is not running.")
        exc = self.fail_on_write
        if exc is not None:
            self.fail_on_write = None
            raise exc
        self.written_frames.append(frame)


class FakeAudioBackend:
    """Deterministic AudioBackend for tests — no real audio hardware.

    **Tier 2 — Best-effort.** Import path:
    ``from icom_lan.audio.backend import FakeAudioBackend``. Implements the
    :class:`AudioBackend` protocol with in-memory :class:`FakeRxStream` /
    :class:`FakeTxStream` doubles so consumer code can be exercised without
    PortAudio or any optional extras.

    Stability: breaking changes require a CHANGELOG note plus a minor version
    bump per ``docs/api/public-api-surface.md``. No strict semver guarantee.
    """

    def __init__(
        self,
        devices: list[AudioDeviceInfo] | None = None,
        *,
        supported_sample_rates: set[int] | None = None,
    ) -> None:
        self._devices: list[AudioDeviceInfo] = devices or []
        self._supported_rates: set[int] = supported_sample_rates or {
            8_000,
            16_000,
            44_100,
            48_000,
            96_000,
        }
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []

    def list_devices(self) -> list[AudioDeviceInfo]:
        return list(self._devices)

    def check_sample_rate(
        self,
        device: AudioDeviceId,
        sample_rate: int,
        *,
        direction: str = "rx",
    ) -> bool:
        return sample_rate in self._supported_rates

    def add_device(self, device: AudioDeviceInfo) -> None:
        """Add a device (test helper — simulates hotplug)."""
        self._devices.append(device)

    def remove_devices(self) -> None:
        """Remove all devices (test helper — simulates device loss)."""
        self._devices.clear()

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> FakeRxStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeRxStream()
        self.rx_streams.append(stream)
        return stream

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
    ) -> FakeTxStream:
        if not any(d.id == device for d in self._devices):
            raise ValueError(f"Unknown device id {device}")
        stream = FakeTxStream()
        self.tx_streams.append(stream)
        return stream


__all__ = [
    "AudioBackend",
    "AudioDeviceId",
    "AudioDeviceInfo",
    "FakeAudioBackend",
    "FakeRxStream",
    "FakeTxStream",
    "PortAudioBackend",
    "RxStream",
    "TxStream",
]
