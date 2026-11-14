"""Profile-backed VFO-centered scope source over one control writer."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
import math
from typing import Literal, Protocol

from rigplane.profiles import RadioProfile
from rigplane.core.types import ScopeFixedEdge
from rigplane.core.exceptions import CommandError, TimeoutError
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

    async def observed_scope_mode(self, receiver: int) -> int | None:
        """The requested receiver's live radio-observed scope mode.

        The CI-V 0x27 0x14 values: 0 center, 1 fixed, 2 scroll-C,
        3 scroll-F. MOR-4392 round 2: the writer owns the radio's
        observed scope state (ScopeSession, refreshed by every waveform
        frame and able to read an explicit receiver); the source consults
        it to decide whether a follow gesture may drive center mode.
        None when the receiver's mode was never observed — the source
        then drives the radio as before.
        """
        ...

    async def set_output(self, enabled: bool, *, release: bool = True) -> None: ...
    async def set_fixed(self, edges: ScopeFixedEdge, receiver: int) -> None: ...

    async def set_dual(self, enabled: bool, receiver: int) -> None:
        """Acquire dual-scope output for a SUB subscription (0x27 0x13).

        Single-scope output streams only the selected receiver, so a SUB
        subscription needs dual on — the same acquisition set_center and
        set_fixed perform, restored by the session at teardown. Never a
        mode/span/edge write.
        """

    async def set_center(self, center_hz: float, span_hz: float, receiver: int) -> None:
        """Tune the center-mode viewport.

        ``span_hz`` is the profile preset value (an entry of
        ``RadioProfile.scope_span_presets_hz``): on Icom CI-V it is the
        ±half-width the 0x27 0x15 span code selects, not the full view
        width the grant reports (MOR-4385).
        """


_FIXED_SCOPE_MODES = (1, 3)
"""CI-V scope modes whose window is radio-held: FIX and SCROLL-F.

A follow gesture in either is a client crop, never a mode change — the
same family the frontend's fixed-mode presentation (FIX/S-F) uses.
"""


class RadioScopeSource:
    kind: Literal["radio"] = "radio"

    def __init__(
        self,
        profile: RadioProfile,
        writer: RadioScopeWriter,
        *,
        max_fps: float = 30,
    ) -> None:
        self._writer = writer
        self._fixed_ranges = profile.scope_fixed_edge_ranges
        self._fixed_step = profile.scope_fixed_edge_step_hz
        self._presets = profile.scope_span_presets_hz
        self._preset_semantics = profile.scope_span_preset_semantics
        self._caps = SourceCapabilities(
            tuple(range(profile.receiver_count)),
            ("center", "fixed") if self._fixed_ranges else ("center",),
            # MOR-4385: published choices are real VIEW widths (end -
            # start), so a ±half-width preset appears as twice its
            # number and the UI steps read like the radio's own table.
            profile.scope_span_view_widths_hz,
            None,
            max_fps,
            "profile fixed edges or center ± span/2"
            if self._fixed_ranges
            else "center ± span/2",
        )
        if not self._caps.span_choices_hz:
            raise ValueError("radio scope requires profile span presets")
        self._callbacks: list[Callable[[NativeFrame], None]] = []
        self._active = False
        self._native_widths: dict[int, int] = {}
        # MOR-4392 round 2: last streamed (mode, coverage) per receiver —
        # lets a FIX follow grant clip to the live fixed window already
        # at subscribe time instead of granting a stale wide window for
        # a frame. Dies with the stream (stop clears it), unlike
        # _native_widths, which is a radio property that survives.
        self._native_coverages: dict[int, tuple[int, Coverage]] = {}

    def capabilities(self) -> SourceCapabilities:
        return replace(
            self._caps, receiver_native_widths=tuple(self._native_widths.items())
        )

    def _receiver(self, receiver: int | None) -> int:
        selected = 0 if receiver is None else receiver
        if selected not in self._caps.receivers:
            raise ValueError("unsupported receiver")
        return selected

    def _coverage_half_width(self, preset: float) -> float:
        """Half the coverage a preset grants, per the profile semantics.

        A ``half_width`` preset (Icom CI-V 0x27 0x15) is itself the
        ±half-width; a ``full_width`` preset covers its number on both
        sides, i.e. half its number either side of center.
        """
        return preset if self._preset_semantics == "half_width" else preset / 2

    def _center_span(self, view: ViewRequest, receiver: int) -> tuple[Coverage, int]:
        """The center-mode grant for a view: coverage and radio preset.

        Picks the smallest preset whose coverage contains the requested
        view, so every granted view is covered with the least waste and
        the radio receives the preset's own index.
        """
        resolved = self.resolve_view(view, receiver)
        center = self._writer.tuned_frequency(receiver)
        distance = max(abs(resolved.start_hz - center), abs(resolved.end_hz - center))
        preset = next(
            (
                preset
                for preset in self._presets
                if self._coverage_half_width(preset) >= distance
            ),
            self._presets[-1],
        )
        half = self._coverage_half_width(preset)
        return Coverage(center - half, center + half), preset

    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View:
        selected = self._receiver(receiver)
        if isinstance(view, FollowView):
            center = self._writer.tuned_frequency(selected)
            return View(center - view.span_hz / 2, center + view.span_hz / 2)
        return view

    def resolve_follow(
        self, view: FollowView, native: Coverage, receiver: int | None = None
    ) -> View:
        """Granted view for a follow request: the requested window,
        kept at its width and VFO centre while the radio catches up.

        MOR-4392: ZOOM asks for a visible width (follow, centred on
        the VFO); the radio is driven to the smallest preset covering
        it (``_center_span``) and the remainder is a crop — so the
        grant is the requested window, not the native coverage. When
        an external change narrows native below the request, the grant
        is the clipped intersection and nothing is written back
        (MOR-4385 adoption). A disjoint request falls back to native.
        MOR-4451: a native that is merely OFFSET (same or larger
        width, shifted — the VFO moved while the radio still streams
        the older coverage) keeps the requested view unchanged, so
        the view never narrows off-centre for a few frames; the wire
        frame still carries the intersected coverage. Only a truly
        NARROWER native clips.
        """
        requested = self.resolve_view(view, receiver)
        start = max(requested.start_hz, native.start_hz)
        end = min(requested.end_hz, native.end_hz)
        if start >= end:
            return native
        if (native.end_hz - native.start_hz) < (
            requested.end_hz - requested.start_hz
        ):
            return View(start, end)
        return requested

    def coverage_for(self, view: ViewRequest, receiver: int | None = None) -> Coverage:
        resolved = self.resolve_view(view, receiver)
        if not isinstance(view, FollowView):
            fixed = self._fixed_edges(resolved, self._receiver(receiver))
            if fixed is not None:
                return Coverage(fixed.start_hz, fixed.end_hz)
        return self._center_span(view, self._receiver(receiver))[0]

    def _fixed_edges(self, view: View, receiver: int) -> ScopeFixedEdge | None:
        if self._fixed_step is None:
            return None
        tuned = self._writer.tuned_frequency(receiver)
        for limits in self._fixed_ranges:
            if not limits.start_hz <= tuned < limits.end_hz:
                continue
            start = math.floor(view.start_hz / self._fixed_step) * self._fixed_step
            end = math.ceil(view.end_hz / self._fixed_step) * self._fixed_step
            if limits.start_hz <= start < end <= limits.end_hz:
                return ScopeFixedEdge(limits.range_index, receiver + 1, start, end)
        return None

    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView:
        selected = self._receiver(receiver)
        resolved = self.resolve_view(view, selected)
        observed_mode = (
            await self._writer.observed_scope_mode(selected)
            if isinstance(view, FollowView)
            else None
        )
        if isinstance(view, FollowView) and selected:
            await self._writer.set_dual(True, selected)
        if observed_mode in _FIXED_SCOPE_MODES:
            # MOR-4392 round 2, gate round 1: the REQUESTED receiver is in
            # a fixed-family mode (FIX or SCROLL-F) — a follow zoom/span
            # gesture is a client crop (MOR-4387), never a mode change, so
            # nothing is written: no 0x14 mode flip, no span, no
            # fixed-edge rewrite. The grant is the requested window
            # clipped to the last streamed fixed window (the same
            # resolve_follow rule the hub applies per frame), so the
            # browser never holds a stale wide grant; with no fixed-mode
            # frame seen yet the coverage stays unknown and the first
            # frame resolves it. Back in CENTER the hub branch re-resolves
            # against the native coverage. The FIX/CTR gestures themselves
            # are explicit mode commands outside this path and still write.
            remembered = self._native_coverages.get(selected)
            if remembered is not None and remembered[0] in _FIXED_SCOPE_MODES:
                native = remembered[1]
                return GrantedView(self.resolve_follow(view, native, selected), native)
            return GrantedView(resolved, None)
        fixed = (
            None
            if isinstance(view, FollowView)
            else self._fixed_edges(resolved, self._receiver(receiver))
        )
        if fixed is not None:
            await self._writer.set_fixed(fixed, selected)
            return GrantedView(resolved, Coverage(fixed.start_hz, fixed.end_hz))
        coverage, preset = self._center_span(view, selected)
        try:
            await self._writer.set_center(
                (coverage.start_hz + coverage.end_hz) / 2,
                preset,
                selected,
            )
        except (CommandError, TimeoutError, asyncio.TimeoutError):
            if not isinstance(view, FollowView):
                raise
            remembered = self._native_coverages.get(selected)
            if remembered is not None:
                native = remembered[1]
                return GrantedView(self.resolve_follow(view, native, selected), native)
            return GrantedView(resolved, None)
        return GrantedView(resolved, coverage)

    async def start(self) -> None:
        if self._active:
            return
        self._active = True
        try:
            await self._writer.set_output(True)
        except BaseException:
            await self.stop()
            raise

    async def stop(self, *, release: bool = True) -> None:
        await self._writer.set_output(False, release=release)
        self._active = False
        # The remembered coverage belongs to the stream that produced it:
        # a window changed externally while the stream was stopped must
        # not clip the next session's first grant.
        self._native_coverages.clear()

    def on_frame(self, callback: Callable[[NativeFrame], None]) -> Callable[[], None]:
        self._callbacks.append(callback)

        def unsubscribe() -> None:
            if callback in self._callbacks:
                self._callbacks.remove(callback)

        return unsubscribe

    def feed_frame(self, frame: ScopeFrame) -> None:
        if not self._active:
            return
        receiver = self._receiver(frame.receiver)
        native_width = self._native_widths.get(receiver)
        if frame.pixels and not frame.out_of_range:
            native_width = len(frame.pixels)
            self._native_widths[receiver] = native_width
        start, end = frame.start_freq_hz, frame.end_freq_hz
        if frame.out_of_range and frame.mode == 0:
            start, end = start - end, start + end
        self._native_coverages[receiver] = (frame.mode, Coverage(start, end))
        native = NativeFrame(
            frame.receiver,
            Coverage(start, end),
            frame.pixels or None,
            frame.out_of_range,
            native_width=native_width,
        )
        for callback in tuple(self._callbacks):
            callback(native)
