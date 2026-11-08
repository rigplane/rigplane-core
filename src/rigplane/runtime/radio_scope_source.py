"""Profile-backed CENTER scope source over one injected control writer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Literal, Protocol

from rigplane.profiles import RadioProfile
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


class RadioScopeWriter(Protocol):
    def tuned_frequency(self, receiver: int) -> float: ...
    async def set_output(self, enabled: bool) -> None: ...
    async def set_center(
        self, center_hz: float, span_hz: float, receiver: int
    ) -> None: ...


class RadioScopeSource:
    kind: Literal["radio"] = "radio"

    def __init__(
        self,
        profile: RadioProfile,
        writer: RadioScopeWriter,
        *,
        native_width: int = 475,
        max_fps: float = 30,
    ) -> None:
        self._writer = writer
        self._caps = SourceCapabilities(
            tuple(range(profile.receiver_count)),
            ("center",),
            profile.scope_span_presets_hz,
            native_width,
            max_fps,
            "center ± span/2",
        )
        if not self._caps.span_choices_hz:
            raise ValueError("radio scope requires profile span presets")
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._active = False

    def capabilities(self) -> SourceCapabilities:
        return self._caps

    def _receiver(self, receiver: int | None) -> int:
        selected = 0 if receiver is None else receiver
        if selected not in self._caps.receivers:
            raise ValueError("unsupported receiver")
        return selected

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        selected = self._receiver(receiver)
        if isinstance(view, FollowView):
            center = self._writer.tuned_frequency(selected)
            return View(center - view.span_hz / 2, center + view.span_hz / 2)
        return view

    def coverage_for(self, view: ViewRequest, receiver: int | None = None) -> Coverage:
        resolved = self.resolve_view(view, receiver)
        needed = resolved.end_hz - resolved.start_hz
        spans = self._caps.span_choices_hz
        span = next((span for span in spans if span >= needed), spans[-1])
        center = (resolved.start_hz + resolved.end_hz) / 2
        return Coverage(center - span / 2, center + span / 2)

    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView:
        selected = self._receiver(receiver)
        resolved = self.resolve_view(view, selected)
        coverage = self.coverage_for(resolved, selected)
        await self._writer.set_center(
            (coverage.start_hz + coverage.end_hz) / 2,
            coverage.end_hz - coverage.start_hz,
            selected,
        )
        return GrantedView(resolved, coverage)

    async def start(self) -> None:
        if self._active:
            return
        try:
            await self._writer.set_output(True)
        except BaseException:
            await self._writer.set_output(False)
            raise
        self._active = True

    async def stop(self) -> None:
        if self._active:
            await self._writer.set_output(False)
            self._active = False

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def feed_frame(self, frame: ScopeFrame) -> None:
        if not self._active:
            return
        start, end = frame.start_freq_hz, frame.end_freq_hz
        if frame.out_of_range and frame.mode == 0:
            start, end = start - end, start + end
        native = NativeFrame(
            frame.receiver,
            Coverage(start, end),
            frame.pixels or None,
            frame.out_of_range,
        )
        for callback in tuple(self._callbacks):
            callback(native)
