"""IQ FFT source composed with a backend-neutral IQ provider."""

from __future__ import annotations

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
from rigplane.sdr.protocol import IqSource
from rigplane.sdr.types import IqBlock


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
    ) -> None:
        self._provider = provider
        self._fft_size = fft_size
        self._width = native_width
        self._fps = fps
        self._start_worker = start_worker
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._active = False
        self._view: ViewRequest | None = None
        self.producer = self._make_producer()

    def _make_producer(self) -> IqFftScope:
        return IqFftScope(
            fft_size=self._fft_size,
            fps=self._fps,
            pixel_width=self._width,
            start_worker=self._start_worker,
        )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            (None,), ("free",), (), self._width, self._fps, "center ± sample_rate/2"
        )

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        if receiver is not None:
            raise ValueError("IQ FFT has no receiver selector")
        if isinstance(view, FollowView):
            center = self._provider.center_freq_hz
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
        self._view = view
        self._configure_view()
        return GrantedView(resolved, self.coverage_for(resolved, receiver))

    def _configure_view(self) -> None:
        coverage = self.coverage_for(self._view) if self._view is not None else None
        if coverage is None:
            self.producer.set_view_center(self._provider.center_freq_hz)
            self.producer.set_span(None)
        else:
            self.producer.set_view_center(
                round((coverage.start_hz + coverage.end_hz) / 2)
            )
            self.producer.set_span(max(1, round(coverage.end_hz - coverage.start_hz)))

    async def start(self) -> None:
        if self._active:
            return
        self.producer = self._make_producer()
        self._configure_view()
        self.producer.on_frame(self._emit)
        self._active = True
        try:
            self._provider.on_block(self._feed)
            self._provider.open()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._active:
            return
        try:
            self._provider.on_block(None)
        finally:
            try:
                self._provider.close()
                self._active = False
            finally:
                try:
                    self.producer.on_frame(None)
                finally:
                    self.producer.close()

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _feed(self, block: IqBlock) -> None:
        if self._active:
            self._configure_view()
            self.producer.feed(block)

    def _emit(self, frame: ScopeFrame) -> None:
        if not self._active:
            return
        native = NativeFrame(
            None,
            Coverage(frame.start_freq_hz, frame.end_freq_hz),
            frame.pixels or None,
            frame.out_of_range,
        )
        for callback in tuple(self._callbacks):
            callback(native)
