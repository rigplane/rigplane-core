"""IQ FFT source composed with a backend-neutral IQ provider."""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from typing import Literal

from rigplane.scope import ScopeFrame
from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedView,
    NativeFrame,
    SourceCapabilities,
    View,
    ViewRequest,
)
from rigplane.sdr.iq_scope import IqFftScope
from rigplane.sdr.controller import SdrScopeController
from rigplane.sdr.protocol import IqScopeSink, IqSource
from rigplane.sdr.types import IqBlock

FRAME_STALE_S = 2.0


class _CaptureSink:
    """Forward acquisition/TX while the hub owns display-view projection."""

    def __init__(self, producer: IqFftScope) -> None:
        self._producer = producer

    def set_span(self, hz: int | None) -> None:
        """Controller display advice does not crop the shared capture."""

    def set_view_center(self, hz: int) -> None:
        """Each hub subscription projects its own display center."""

    def set_tx_active(self, active: bool) -> None:
        self._producer.set_tx_active(active)

    def feed(self, block: IqBlock) -> None:
        self._producer.feed(block)

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        self._producer.on_frame(callback)


class IqFftSource:
    kind: Literal["iq_fft"] = "iq_fft"

    def __init__(
        self,
        provider: IqSource,
        *,
        fft_size: int = 4096,
        native_width: int = 689,
        fps: int = 20,
        start_worker: bool = True,
        invert_spectrum: bool = False,
        controller_factory: Callable[[IqScopeSink], SdrScopeController] | None = None,
        loop: asyncio.AbstractEventLoop | None = None,
    ) -> None:
        self._provider = provider
        self._fft_size = fft_size
        self._width = native_width
        self._fps = fps
        self._start_worker = start_worker
        self._invert_spectrum = invert_spectrum
        self._controller_factory = controller_factory
        self._controller: SdrScopeController | None = None
        self._loop = loop
        self._timer: asyncio.TimerHandle | None = None
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._active = False
        self._started_at: float | None = None
        self._overflow_count = 0
        self._last_error: str | None = None
        self.producer = self._make_producer()

    def _make_producer(self) -> IqFftScope:
        return IqFftScope(
            fft_size=self._fft_size,
            fps=self._fps,
            pixel_width=self._width,
            start_worker=self._start_worker,
            invert_spectrum=self._invert_spectrum,
        )

    @property
    def active(self) -> bool:
        return self._active

    @property
    def started_at(self) -> float | None:
        return self._started_at

    @property
    def overflow_count(self) -> int:
        return self._overflow_count

    @property
    def last_error(self) -> str | None:
        return self._last_error

    @property
    def tx_frozen(self) -> bool:
        return self._controller is not None and self._controller.tx_frozen

    @property
    def frequency_in_range(self) -> bool:
        return self._controller is None or self._controller.frequency_in_range

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            (None,), ("free",), (), self._width, self._fps, "center ± sample_rate/2"
        )

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        if receiver is not None:
            raise ValueError("IQ FFT has no receiver selector")
        if isinstance(view, FollowView):
            center = self._provider.center_freq_hz
            if self._controller is not None:
                target = self._controller.target_frequency_hz
                if target is not None:
                    center = target
            return View(center - view.span_hz / 2, center + view.span_hz / 2)
        return view

    def coverage_for(
        self, view: ViewRequest, receiver: int | None = None
    ) -> Coverage | None:
        resolved = self.resolve_view(view, receiver)
        center = self._provider.center_freq_hz
        half = self._provider.sample_rate_hz / 2
        start = max(resolved.start_hz, center - half)
        end = min(resolved.end_hz, center + half)
        return Coverage(start, end) if start < end else None

    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView:
        resolved = self.resolve_view(view, receiver)
        return GrantedView(resolved, self.coverage_for(resolved, receiver))

    async def start(self) -> None:
        if self._active:
            return
        self._active = True
        self._last_error = None
        self._overflow_count = 0
        try:
            self.producer = self._make_producer()
            if self._loop is None:
                self._loop = asyncio.get_running_loop()
            self._started_at = self._loop.time()
            if self._controller_factory is not None:
                self._controller = self._controller_factory(_CaptureSink(self.producer))
            self.producer.on_frame(self._emit)
            self._provider.on_block(self._feed)
            self._provider.open()
            self._schedule_controller()
        except BaseException as exc:
            try:
                await self.stop()
            finally:
                if isinstance(exc, Exception):
                    self._last_error = str(exc)
            raise

    async def stop(self, *, release: bool = True) -> None:
        if not self._active:
            return
        self._last_error = None
        self._started_at = None
        self._active = False
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._controller = None
        try:
            self._provider.on_block(None)
        finally:
            try:
                self._provider.close()
            finally:
                try:
                    self.producer.on_frame(None)
                finally:
                    self.producer.close()

    def on_radio_state(self, freq_hz: int | None, tx_active: bool) -> None:
        if self._active and self._controller is not None:
            self._controller.on_radio_state(freq_hz, tx_active)
            self._schedule_controller()

    def _schedule_controller(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._active and self._controller is not None and self._loop is not None:
            delay = self._controller.next_action_delay
            if delay is not None:
                # asyncio may fire one clock resolution early. A pending
                # retry must clear that window and advance representable time.
                delay = max(
                    delay,
                    time.get_clock_info("monotonic").resolution,
                    math.ulp(self._loop.time()),
                )
                self._timer = self._loop.call_later(delay, self._tick_controller)

    def _tick_controller(self) -> None:
        self._timer = None
        if self._active and self._controller is not None:
            self._controller.tick()
            self._schedule_controller()

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _feed(self, block: IqBlock) -> None:
        if not self._active:
            return
        if block.overflow:
            self._overflow_count += 1
        if block.center_freq_hz != 0:
            self.producer.feed(block)

    def _emit(self, frame: ScopeFrame) -> None:
        if not self._active:
            return
        # A TX freeze before the first live capture has no measured frame
        # geometry yet. Wait for a real frame instead of inventing coverage.
        if frame.start_freq_hz >= frame.end_freq_hz:
            return
        native = NativeFrame(
            None,
            Coverage(frame.start_freq_hz, frame.end_freq_hz),
            frame.pixels or None,
            frame.out_of_range or not self.frequency_in_range,
        )
        for callback in tuple(self._callbacks):
            callback(native)
