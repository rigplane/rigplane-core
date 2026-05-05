"""AudioBus — pub/sub distribution for radio audio streams.

Provides a fan-out mechanism so multiple consumers (WebSocket broadcaster,
virtual audio bridge, WAV recorder, etc.) can independently subscribe to
the same audio stream from the radio.

Architecture::

    Radio ──(opus packets)──► AudioBus ──► subscriber 1 (browser)
                                       ──► subscriber 2 (BlackHole)
                                       ──► subscriber 3 (recorder)
                                       ──► ...

Usage::

    bus = AudioBus(radio)

    # Subscribe — returns an async context manager
    sub = bus.subscribe(name="wsjt-bridge")
    await sub.start()
    async for packet in sub:
        process(packet)
    sub.stop()

    # Or with async context manager:
    async with bus.subscribe(name="recorder") as sub:
        async for packet in sub:
            save(packet)

Lifecycle:
    - First subscriber triggers ``radio.start_audio_rx_opus()``
    - Last subscriber removal triggers ``radio.stop_audio_rx_opus()``
    - Subscribers can join/leave at any time
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from icom_lan.audio import AudioPacket

logger = logging.getLogger(__name__)

__all__ = ["AudioBus", "AudioSubscription"]

_DEFAULT_QUEUE_SIZE = 64


class AudioSubscription:
    """A single subscriber to the audio bus.

    Receives copies of every audio packet via an internal asyncio.Queue.
    Can be iterated with ``async for`` or read manually with :meth:`get`.

    Args:
        bus: Parent AudioBus.
        name: Human-readable subscriber name (for logging).
        queue_size: Maximum buffered packets before dropping.
    """

    def __init__(
        self,
        bus: AudioBus,
        name: str = "",
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> None:
        self._bus = bus
        self.name = name or f"sub-{id(self):x}"
        self._queue: asyncio.Queue[AudioPacket | None] = asyncio.Queue(
            maxsize=queue_size,
        )
        self._active = False
        self._dropped = 0
        self._received = 0

    @property
    def active(self) -> bool:
        return self._active

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "active": self._active,
            "received": self._received,
            "dropped": self._dropped,
            "queued": self._queue.qsize(),
        }

    async def start(self) -> None:
        """Activate this subscription (registers with the bus)."""
        if self._active:
            return
        self._active = True
        await self._bus._add_subscriber(self)

    def stop(self) -> None:
        """Deactivate this subscription (unregisters from the bus)."""
        if not self._active:
            return
        self._active = False
        # Schedule async removal; we can't await in sync context
        asyncio.create_task(self._bus._remove_subscriber(self))

    def deliver(self, packet: AudioPacket | None) -> None:
        """Called by the bus to deliver a packet (non-blocking)."""
        if not self._active:
            return
        self._received += 1
        try:
            self._queue.put_nowait(packet)
        except asyncio.QueueFull:
            # Drop oldest, enqueue newest (sliding window)
            self._dropped += 1
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._queue.put_nowait(packet)
            except asyncio.QueueFull:
                pass

    async def get(self, timeout: float | None = None) -> AudioPacket | None:
        """Get the next packet (blocks until available or timeout)."""
        if timeout is not None:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout)
        return await self._queue.get()

    def get_nowait(self) -> AudioPacket | None:
        """Get a packet without blocking (raises QueueEmpty)."""
        return self._queue.get_nowait()

    def __aiter__(self) -> AudioSubscription:
        return self

    async def __anext__(self) -> AudioPacket | None:
        if not self._active and self._queue.empty():
            raise StopAsyncIteration
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=1.0)
        except asyncio.CancelledError:
            raise StopAsyncIteration
        except asyncio.TimeoutError:
            if not self._active:
                raise StopAsyncIteration
            # Still active — just no data yet, keep iterating
            return None

    async def __aenter__(self) -> AudioSubscription:
        await self.start()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        self.stop()


class AudioBus:
    """Fan-out distribution bus for radio audio packets.

    Args:
        radio: A radio instance implementing AudioCapable.
        jitter_depth: Jitter buffer depth passed to radio RX start.
    """

    def __init__(self, radio: Any, *, jitter_depth: int = 5) -> None:
        self._radio = radio
        self._jitter_depth = jitter_depth
        self._subscribers: list[AudioSubscription] = []
        self._rx_active = False
        self._lock = asyncio.Lock()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @property
    def rx_active(self) -> bool:
        return self._rx_active

    @property
    def stats(self) -> dict[str, Any]:
        return {
            "rx_active": self._rx_active,
            "subscriber_count": len(self._subscribers),
            "subscribers": [s.stats for s in self._subscribers],
        }

    def subscribe(
        self,
        name: str = "",
        queue_size: int = _DEFAULT_QUEUE_SIZE,
    ) -> AudioSubscription:
        """Create a new subscription (not yet active — call start() or use as context manager)."""
        return AudioSubscription(self, name=name, queue_size=queue_size)

    def _on_opus_packet(self, packet: "AudioPacket | None") -> None:
        """Internal callback — distributes packet to all active subscribers."""
        for sub in self._subscribers:
            sub.deliver(packet)

    async def _add_subscriber(self, sub: AudioSubscription) -> None:
        """Register a subscriber and start RX if this is the first one."""
        async with self._lock:
            if sub not in self._subscribers:
                self._subscribers.append(sub)
                logger.info(
                    "audio-bus: +subscriber %r (%d total)",
                    sub.name,
                    len(self._subscribers),
                )
            if not self._rx_active and len(self._subscribers) > 0:
                await self._start_rx()

    async def _remove_subscriber(self, sub: AudioSubscription) -> None:
        """Unregister a subscriber. Stops RX if no subscribers remain."""
        async with self._lock:
            try:
                self._subscribers.remove(sub)
            except ValueError:
                pass
            logger.info(
                "audio-bus: -subscriber %r (%d remaining)",
                sub.name,
                len(self._subscribers),
            )
            if self._rx_active and len(self._subscribers) == 0:
                # Stop RX synchronously while holding the lock
                # to prevent new subscribers from seeing a dying stream
                await self._stop_rx()

    async def _start_rx(self) -> None:
        """Start receiving audio from the radio."""
        if self._rx_active:
            return
        try:
            await self._radio.start_audio_rx_opus(
                self._on_opus_packet,
                jitter_depth=self._jitter_depth,
            )
            self._rx_active = True
            logger.info("audio-bus: RX started")
        except Exception:
            logger.exception("audio-bus: failed to start RX")

    async def _stop_rx(self) -> None:
        """Stop receiving audio from the radio."""
        if not self._rx_active:
            return
        self._rx_active = False
        try:
            await self._radio.stop_audio_rx_opus()
            logger.info("audio-bus: RX stopped")
        except Exception:
            logger.debug("audio-bus: stop RX error", exc_info=True)

    async def stop(self) -> None:
        """Stop the bus and all subscribers."""
        for sub in list(self._subscribers):
            sub.stop()
        if self._rx_active:
            await self._stop_rx()
