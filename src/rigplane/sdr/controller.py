"""SdrScopeController: keep the IQ panorama on the radio VFO (MOR-3156).

Pure decision logic between the :class:`~rigplane.sdr.protocol.IqSource`
and :class:`~rigplane.sdr.protocol.IqScopeSink` contracts: no threads,
no I/O, no DSP. The server feeds radio state through
:meth:`SdrScopeController.on_radio_state` and calls
:meth:`SdrScopeController.tick` periodically; time comes from the
injectable ``clock``.
"""

from __future__ import annotations

import math
import time
from typing import Callable

from .protocol import IqScopeSink, IqSource
from .types import SdrConfig

__all__ = [
    "DEFAULT_SPAN_DIVISOR",
    "RETUNE_DEBOUNCE_S",
    "SdrScopeController",
    "TX_HOLD_S",
]

RETUNE_DEBOUNCE_S = 0.150
"""Minimum spacing in seconds between two hardware retunes."""

TX_HOLD_S = 0.300
"""Default TX release hold in seconds."""

DEFAULT_SPAN_DIVISOR = 4
"""``sample_rate_hz`` divisor used as display span when ``span_hz`` is
``None`` (a quarter of the sample rate; 600 kHz at the 2.4 MS/s
RTL-SDR default). A span this narrow leaves the guard-band rule room
for view shifts around the +1/4 retune placement."""


class SdrScopeController:
    """Follow the radio VFO with view shifts, debounced retunes and a
    TX freeze (MOR-3156).

    All frequency decisions run in SDR RF space: radio VFO +
    ``config.freq_offset_hz`` (the frequency the SDR must see; view
    centers passed to the sink are in this space too). ``ppm`` and
    ``invert_spectrum`` belong to the source/sink wiring — this
    controller does no DSP.

    Decision rules, evaluated on every :meth:`on_radio_state` and
    :meth:`tick`:

    * The source range is checked first: when the RF frequency lies
      outside ``source.frequency_range_hz()`` no retune and no view
      call happen, and :attr:`frequency_in_range` reports ``False``.
    * View shift vs retune: when the display window (effective span
      around the VFO) fits inside the current IQ window minus a 10 %
      guard band at each edge, only ``sink.set_view_center()`` is
      called. Otherwise the source is retuned so the VFO sits at +1/4
      of the IQ bandwidth from center (keeping the LO DC spike away
      from the VFO), clamped to the source range, and then the view is
      set.
    * Retune debounce: hardware retunes are at least
      ``RETUNE_DEBOUNCE_S`` apart. A suppressed retune stays pending
      and is re-evaluated on the next :meth:`on_radio_state` or
      :meth:`tick`; a later state whose window fits supersedes it with
      a plain view shift. Display-only calls are never debounced.
    * TX freeze: a ``tx_active`` rising edge calls
      ``sink.set_tx_active(True)`` immediately; a falling edge
      releases only after ``tx_hold_s`` (the RF-sense switch has
      re-connected the SDR by then), applied from :meth:`tick` or the
      next :meth:`on_radio_state`.

    ``freq_hz=None`` leaves the last known frequency tuning untouched
    (only ``tx_active`` is processed).

    Args:
        source: IQ source to (re)tune; never opened or closed here.
        sink: Scope sink driven with span/view/TX decisions.
        config: SDR settings. ``span_hz=None`` selects
            ``source.sample_rate_hz // DEFAULT_SPAN_DIVISOR``; the
            effective span is sent to the sink once, at construction.
        clock: Monotonic time source, injectable for determinism.
        tx_hold_s: Seconds to keep the sink TX-frozen after the radio
            leaves TX.
    """

    def __init__(
        self,
        source: IqSource,
        sink: IqScopeSink,
        config: SdrConfig,
        clock: Callable[[], float] = time.monotonic,
        *,
        tx_hold_s: float = TX_HOLD_S,
    ) -> None:
        self._source = source
        self._sink = sink
        self._config = config
        self._clock = clock
        self._tx_hold_s = float(tx_hold_s)

        self._span_hz: int = (
            config.span_hz
            if config.span_hz is not None
            else source.sample_rate_hz // DEFAULT_SPAN_DIVISOR
        )

        self._freq_hz: int | None = None
        self._last_view_hz: int | None = None
        self._last_retune_s = -math.inf
        self._retune_pending = False
        self._frequency_in_range = True

        self._tx_observed = False
        self._tx_sink_active = False
        self._tx_release_at: float | None = None

        sink.set_span(self._span_hz)

    @property
    def span_hz(self) -> int:
        """Effective display span in Hz actually sent to the sink."""
        return self._span_hz

    @property
    def retune_pending(self) -> bool:
        """Whether a needed retune is waiting out the debounce window."""
        return self._retune_pending

    @property
    def frequency_in_range(self) -> bool:
        """Whether the latest evaluated RF frequency is inside the
        source range; ``True`` before any frequency arrives.

        The sink protocol has no out-of-range flag, so the server
        wiring reads this property to mark frames.
        """
        return self._frequency_in_range

    def on_radio_state(self, freq_hz: int | None, tx_active: bool) -> None:
        """Feed one radio state snapshot (VFO frequency, TX state).

        A rising TX edge freezes the sink immediately; a falling edge
        schedules the release. The frequency decision (and any retune
        whose debounce has elapsed) is applied within this call.
        ``freq_hz=None`` keeps the last known frequency.
        """
        now = self._clock()

        if tx_active:
            self._tx_release_at = None
            if not self._tx_sink_active:
                self._sink.set_tx_active(True)
                self._tx_sink_active = True
        elif self._tx_observed:
            self._tx_release_at = now + self._tx_hold_s
        self._tx_observed = tx_active

        self._release_tx_if_due(now)

        if freq_hz is not None:
            self._freq_hz = int(freq_hz)
        self._apply_frequency(now)

    def tick(self) -> None:
        """Apply due deferred actions at the current clock time.

        The server calls this periodically: it executes a retune still
        pending from the debounce window and releases the TX freeze
        once the hold has elapsed.
        """
        now = self._clock()
        self._release_tx_if_due(now)
        self._apply_frequency(now)

    def _release_tx_if_due(self, now: float) -> None:
        release_at = self._tx_release_at
        if release_at is None or now < release_at:
            return
        self._tx_release_at = None
        if self._tx_sink_active and not self._tx_observed:
            self._sink.set_tx_active(False)
            self._tx_sink_active = False

    def _apply_frequency(self, now: float) -> None:
        if self._freq_hz is None:
            return
        rf_hz = self._freq_hz + self._config.freq_offset_hz

        source = self._source
        low_hz, high_hz = source.frequency_range_hz()
        if not low_hz <= rf_hz <= high_hz:
            self._retune_pending = False
            self._frequency_in_range = False
            return
        self._frequency_in_range = True

        sample_rate_hz = source.sample_rate_hz
        center_hz = source.center_freq_hz
        half_span_hz = self._span_hz // 2
        guard_hz = sample_rate_hz // 10
        window_low_hz = center_hz - sample_rate_hz // 2 + guard_hz
        window_high_hz = center_hz + sample_rate_hz // 2 - guard_hz
        if (
            rf_hz - half_span_hz >= window_low_hz
            and rf_hz + half_span_hz <= window_high_hz
        ):
            self._retune_pending = False
            self._set_view_center(rf_hz)
            return

        target_hz = max(low_hz, min(high_hz, rf_hz - sample_rate_hz // 4))
        if target_hz != center_hz:
            if now - self._last_retune_s < RETUNE_DEBOUNCE_S:
                self._retune_pending = True
                return
            source.set_center_freq(target_hz)
            self._last_retune_s = now
        self._retune_pending = False
        self._set_view_center(rf_hz)

    def _set_view_center(self, hz: int) -> None:
        if self._last_view_hz != hz:
            self._sink.set_view_center(hz)
            self._last_view_hz = hz
