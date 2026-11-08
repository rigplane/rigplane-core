"""Demand-owned audio FFT with RF passband geometry."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from rigplane.audio.fft_scope import AudioFftScope
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


@dataclass(frozen=True)
class AudioPassband:
    tuned_hz: float
    low_offset_hz: float
    high_offset_hz: float
    sideband: Literal["upper", "lower", "both"]
    carrier_offset_hz: float = 0

    def __post_init__(self) -> None:
        Coverage(
            self.tuned_hz + self.low_offset_hz, self.tuned_hz + self.high_offset_hz
        )
        if self.sideband not in ("upper", "lower", "both") or not math.isfinite(
            self.carrier_offset_hz
        ):
            raise ValueError("invalid audio passband mapping")
        if self.sideband == "both" and self.carrier_offset_hz != 0:
            raise ValueError("invalid audio passband mapping")


class PcmTap(Protocol):
    async def acquire(self, callback: Callable[[bytes], None]) -> None: ...
    async def release(self) -> None: ...


class AudioFftSource:
    kind: Literal["audio_fft"] = "audio_fft"

    def __init__(
        self,
        tap: PcmTap,
        facts: Callable[[], AudioPassband],
        *,
        fft_size: int = 4096,
        sample_rate: int = 48000,
        fps: int = 20,
    ) -> None:
        self._tap = tap
        self._facts = facts
        self._fft_size = fft_size
        self._sample_rate = sample_rate
        self._fps = fps
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._active = False
        self._frame_facts: AudioPassband | None = None
        self._native_width: int | None = None
        self.producer = self._make_producer()

    def _make_producer(self) -> AudioFftScope:
        return AudioFftScope(
            fft_size=self._fft_size, sample_rate=self._sample_rate, fps=self._fps
        )

    def capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            (None,),
            ("free",),
            (),
            self._native_width,
            self._fps,
            "tuned frequency + receiver passband",
        )

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        if receiver is not None:
            raise ValueError("audio FFT has no receiver selector")
        if isinstance(view, FollowView):
            center = self._facts().tuned_hz
            return View(center - view.span_hz / 2, center + view.span_hz / 2)
        return view

    def coverage_for(
        self, view: ViewRequest, receiver: int | None = None
    ) -> Coverage | None:
        self.resolve_view(view, receiver)
        facts = self._facts()
        if facts.sideband == "both":
            low = max(facts.low_offset_hz, -self._sample_rate / 2)
            high = min(facts.high_offset_hz, self._sample_rate / 2)
            return (
                Coverage(facts.tuned_hz + low, facts.tuned_hz + high)
                if low < high
                else None
            )
        sign = 1 if facts.sideband == "upper" else -1
        a = facts.carrier_offset_hz
        b = a + sign * self._sample_rate / 2
        lo = max(facts.low_offset_hz, min(a, b))
        hi = min(facts.high_offset_hz, max(a, b))
        return Coverage(facts.tuned_hz + lo, facts.tuned_hz + hi) if lo < hi else None

    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView:
        resolved = self.resolve_view(view, receiver)
        return GrantedView(resolved, self.coverage_for(resolved, receiver))

    async def start(self) -> None:
        if self._active:
            return
        self._native_width = None
        self.producer = self._make_producer()
        self.producer.on_frame(self._emit)
        self._active = True
        try:
            await self._tap.acquire(self._feed)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if not self._active:
            return
        self._active = False
        try:
            self.producer.on_frame(None)
        finally:
            try:
                self.producer.stop()
            finally:
                await self._tap.release()

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def _feed(self, pcm: bytes) -> None:
        if self._active:
            try:
                facts = self._facts()
            except ValueError:
                if self._frame_facts is not None:
                    self.producer.stop()
                    self._frame_facts = None
                return
            if facts != self._frame_facts:
                self._native_width = None
                self.producer.stop()
                self.producer = self._make_producer()
                self.producer.on_frame(self._emit)
            self._frame_facts = facts
            self.producer.set_center_freq(int(self._frame_facts.tuned_hz))
            self.producer.feed_audio(pcm)

    def _emit(self, frame: ScopeFrame) -> None:
        if not self._active:
            return
        facts = self._frame_facts
        if facts is None:
            return
        if facts.sideband == "both":
            self._emit_symmetric(frame, facts)
            return
        positive = frame.pixels[len(frame.pixels) // 2 :]
        sign = 1 if facts.sideband == "upper" else -1
        step = self._sample_rate / self._fft_size
        # AudioFftScope emits a symmetric rFFT; only its positive half is independent.
        selected = [
            i
            for i in range(len(positive))
            if facts.low_offset_hz
            <= facts.carrier_offset_hz + sign * i * step
            <= facts.high_offset_hz
        ]
        if len(selected) < 2:
            return
        if sign < 0:
            selected.reverse()
        start = facts.tuned_hz + facts.carrier_offset_hz + sign * selected[0] * step
        end = facts.tuned_hz + facts.carrier_offset_hz + sign * selected[-1] * step
        native_width = len(selected)
        self._native_width = native_width
        native = NativeFrame(
            None,
            Coverage(start, end),
            bytes(positive[i] for i in selected),
            native_width=native_width,
        )
        for callback in tuple(self._callbacks):
            callback(native)

    def _emit_symmetric(self, frame: ScopeFrame, facts: AudioPassband) -> None:
        """Emit the observed passband crop within the PCM Nyquist range.

        Ambiguous mode polarity remains MOR-4288; a passband spanning the
        VFO retains both FFT sides.
        """
        full = frame.pixels
        center = len(full) // 2
        step = self._sample_rate / self._fft_size
        low = max(facts.low_offset_hz, -self._sample_rate / 2)
        high = min(facts.high_offset_hz, self._sample_rate / 2)
        if low >= high:
            return
        lo = max(0, center + math.ceil(low / step))
        hi = min(len(full), center + math.floor(high / step) + 1)
        selected = full[lo:hi]
        if len(selected) < 2:
            return
        native_width = len(selected)
        self._native_width = native_width
        native = NativeFrame(
            None,
            Coverage(
                facts.tuned_hz + low,
                facts.tuned_hz + high,
            ),
            bytes(selected),
            native_width=native_width,
        )
        for callback in tuple(self._callbacks):
            callback(native)
