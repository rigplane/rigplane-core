"""IQ source and IQ scope-sink protocols for the SDR panadapter (MOR-3151).

:class:`IqSource` is the backend-neutral contract a SoapySDR adapter (and
the test fake) implements; :class:`IqScopeSink` is the surface the
controller drives and the future ``IqFftScope`` implements.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from rigplane.scope import ScopeFrame

from .types import IqBlock

__all__ = ["IqSource", "IqScopeSink"]


@runtime_checkable
class IqSource(Protocol):
    """A streamable complex-IQ receiver.

    Lifecycle: ``open()`` → configure/setters → blocks stream to the
    single ``on_block`` callback → ``close()``. The callback runs on the
    source's own reader thread; consumers must not block it.
    """

    def open(self) -> None:
        """Open the device and prepare streaming; idempotent."""
        ...

    def close(self) -> None:
        """Stop streaming and release the device; idempotent."""
        ...

    @property
    def is_open(self) -> bool:
        """Whether the source is open."""
        ...

    def set_center_freq(self, hz: int) -> None:
        """Retune the hardware center frequency."""
        ...

    @property
    def center_freq_hz(self) -> int:
        """Current hardware center frequency in Hz."""
        ...

    def set_sample_rate(self, hz: int) -> None:
        """Set the complex sample rate in Hz."""
        ...

    @property
    def sample_rate_hz(self) -> int:
        """Current complex sample rate in Hz."""
        ...

    def set_gain(self, db: float | None) -> None:
        """Set hardware gain in dB; ``None`` enables device AGC."""
        ...

    def frequency_range_hz(self) -> tuple[int, int]:
        """Return the ``(low, high)`` tunable range in Hz."""
        ...

    def on_block(self, callback: Callable[[IqBlock], None] | None) -> None:
        """Set (or clear with ``None``) the single block callback.

        The callback is invoked on the source's reader thread; it must
        not block.
        """
        ...


@runtime_checkable
class IqScopeSink(Protocol):
    """Scope surface an IQ source drives; implemented by ``IqFftScope``.

    ``feed`` is called from the source's reader thread and must stay
    cheap — enqueue only; FFT and frame work happen on the sink's own
    thread. Frame consumers register through ``on_frame``.
    """

    def feed(self, block: IqBlock) -> None:
        """Accept one IQ block from the source's reader thread."""
        ...

    def set_view_center(self, hz: int) -> None:
        """Move the display center inside the current IQ window.

        Display-only: no hardware retune happens.
        """
        ...

    def set_span(self, hz: int | None) -> None:
        """Set the displayed span; ``None`` = full sample rate."""
        ...

    def set_tx_active(self, active: bool) -> None:
        """Flag transmit state; frames produced while active are
        marked/frozen."""
        ...

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        """Set (or clear with ``None``) the single frame callback."""
        ...
