"""Multi-consumer PCM audio tap registry.

Provides fan-out of raw PCM audio data to multiple analysis consumers
(FFT scope, CW auto-tuner, future analyzers) without requiring each
to independently subscribe to the audio bus.

Thread-safety is NOT required — all calls happen from the single
asyncio relay loop in AudioBroadcaster.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["TapHandle", "TapRegistry"]

logger = logging.getLogger(__name__)

_next_id: int = 0


def _alloc_id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


@dataclass(frozen=True, slots=True)
class TapHandle:
    """Opaque handle returned by :meth:`TapRegistry.register`, used to unregister."""

    _id: int = field(repr=False)
    name: str = ""


class TapRegistry:
    """Multi-consumer PCM audio fan-out registry.

    Registered taps receive a copy of every PCM buffer via :meth:`feed`.
    Exceptions in individual taps are logged and do not propagate to the
    caller or affect other taps.
    """

    __slots__ = ("_taps",)

    def __init__(self) -> None:
        self._taps: dict[int, tuple[str, Callable[[bytes], None]]] = {}

    def register(self, name: str, callback: Callable[[bytes], None]) -> TapHandle:
        """Register a named tap. Returns an opaque handle for :meth:`unregister`."""
        tap_id = _alloc_id()
        self._taps[tap_id] = (name, callback)
        logger.debug(
            "tap-registry: registered '%s' (id=%d, total=%d)",
            name,
            tap_id,
            len(self._taps),
        )
        return TapHandle(_id=tap_id, name=name)

    def unregister(self, handle: TapHandle) -> None:
        """Remove a tap by handle. No-op if already unregistered."""
        removed = self._taps.pop(handle._id, None)
        if removed is not None:
            logger.debug(
                "tap-registry: unregistered '%s' (id=%d, total=%d)",
                removed[0],
                handle._id,
                len(self._taps),
            )

    def feed(self, pcm: bytes) -> None:
        """Fan out PCM data to all registered taps.

        Exceptions are logged per-tap and do not propagate.
        """
        for tap_id, (name, callback) in self._taps.items():
            try:
                callback(pcm)
            except Exception:
                logger.warning(
                    "tap-registry: error in tap '%s' (id=%d)",
                    name,
                    tap_id,
                    exc_info=True,
                )

    @property
    def active(self) -> bool:
        """True if any taps are registered."""
        return len(self._taps) > 0
