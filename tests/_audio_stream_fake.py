"""FakeAudioStream — deterministic double for AudioStream in tests.

Implements the public surface of :class:`rigplane.audio.lan_stream.AudioStream`
that production code and tests interact with:

    start_rx(callback, *, jitter_depth=None)
    stop_rx()
    start_tx()
    stop_tx()
    push_tx(opus_data)
    add_rx_tap(callback) / remove_rx_tap(callback)

Call-tracking attributes allow assertions without MagicMock:

    fake.start_rx_count              # times start_rx was awaited
    fake.stop_rx_count               # times stop_rx was awaited
    fake.start_tx_count              # times start_tx was awaited
    fake.stop_tx_count               # times stop_tx was awaited
    fake.last_start_rx_callback      # callback passed to last start_rx call
    fake.last_start_rx_jitter_depth  # jitter_depth kwarg from last start_rx
    fake.tx_frames                   # list of bytes pushed via push_tx
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rigplane.audio import AudioState


class FakeAudioStream:
    """Deterministic double for ``AudioStream`` — no real transport needed."""

    def __init__(self) -> None:
        self.state: AudioState = AudioState.IDLE

        self.start_rx_count: int = 0
        self.stop_rx_count: int = 0
        self.start_tx_count: int = 0
        self.stop_tx_count: int = 0

        self.last_start_rx_callback: Callable[..., Any] | None = None
        self.last_start_rx_jitter_depth: int | None = None

        self.rx_taps: list[Callable[..., Any]] = []
        self.tx_frames: list[bytes] = []

    async def start_rx(
        self,
        callback: Callable[..., Any],
        *,
        jitter_depth: int | None = None,
    ) -> None:
        self.last_start_rx_callback = callback
        self.last_start_rx_jitter_depth = jitter_depth
        self.start_rx_count += 1

    async def stop_rx(self) -> None:
        self.stop_rx_count += 1

    async def start_tx(self) -> None:
        self.start_tx_count += 1

    async def stop_tx(self) -> None:
        self.stop_tx_count += 1

    async def push_tx(self, opus_data: bytes) -> None:
        self.tx_frames.append(opus_data)

    def add_rx_tap(self, callback: Callable[..., Any]) -> None:
        """Mirror of ``AudioStream.add_rx_tap`` (parallel RX listener)."""
        if callback not in self.rx_taps:
            self.rx_taps.append(callback)

    def remove_rx_tap(self, callback: Callable[..., Any]) -> None:
        """Mirror of ``AudioStream.remove_rx_tap``."""
        try:
            self.rx_taps.remove(callback)
        except ValueError:
            pass

    def emit_rx(self, packet: Any) -> None:
        """Deliver one RX packet to the callback and all taps (real fan-out order)."""
        if self.last_start_rx_callback is not None:
            self.last_start_rx_callback(packet)
        for tap in list(self.rx_taps):
            tap(packet)
