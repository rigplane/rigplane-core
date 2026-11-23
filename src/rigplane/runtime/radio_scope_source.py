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

    def span_settled(self, receiver: int) -> bool:
        """Whether the receiver's last span request is settled (MOR-4532).

        False while the span write awaits its answer, while it is deferred
        (TX, readiness), and after an acknowledged or unanswered write until
        the first frame that follows it. A refused write and a write that
        was not needed are settled at once.
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

    async def set_span(self, span_hz: float, receiver: int) -> None:
        """Write only the scope span preset (no mode/centre/edge write).

        ``span_hz`` is the profile preset value (an entry of
        ``RadioProfile.scope_span_presets_hz``), like ``set_center``'s —
        on Icom CI-V it is the ±half-width the 0x27 0x15 span code
        selects (MOR-4385). For a span-applicable radio-owned mode
        (SCROLL-C), where the radio keeps its own window and a mode
        write would flip it away.
        """

    async def set_center(self, center_hz: float, span_hz: float, receiver: int) -> None:
        """Tune the center-mode viewport.

        ``span_hz`` is the profile preset value (an entry of
        ``RadioProfile.scope_span_presets_hz``): on Icom CI-V it is the
        ±half-width the 0x27 0x15 span code selects, not the full view
        width the grant reports (MOR-4385).
        """


RADIO_OWNED_WINDOW_SCOPE_MODES = (1, 2, 3)
"""CI-V scope modes whose window is radio-held: FIX, SCROLL-C, SCROLL-F.

A follow gesture in any of them is a client crop, never a mode change —
the grant IS the window the frames report. In the scroll modes the
radio moves its own edges ("follow the radio" = view = the reported
window). Shared fact with the frontend: see
contracts/scope-mode-families.json and its contract tests.
"""

SPAN_APPLICABLE_SCOPE_MODES = (0, 2)
"""CI-V scope modes where SPAN (0x27 0x15) selects the window width.

Center and SCROLL-C (the Icom manual's SPAN pair; EDGE number 0x27 0x16
selects the window in FIX and SCROLL-F instead). The client's
``isSpanApplicable``/``isSpanDisabledScope`` split is the same fact —
see contracts/scope-mode-families.json and its contract tests. Used
inside the radio-owned family branch above: SCROLL-C is the one family
mode a follow span gesture drives, span-only.
"""


class RadioScopeSource:
    kind: Literal["radio"] = "radio"

    def __init__(
        self,
        profile: RadioProfile,
        writer: RadioScopeWriter,
        *,
        max_fps: float = 30,
        on_mode_change: Callable[[int, int], None] | None = None,
    ) -> None:
        self._writer = writer
        # (receiver, new mode): called by feed_frame when a streamed frame
        # reports a different scope mode than the previous frame of the same
        # receiver (a switch between CENTER, FIX and the SCROLL modes, from
        # the UI or the radio's front panel).
        self._on_mode_change = on_mode_change
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
        if not isinstance(view, FollowView):
            # A pinned window is granted once and never re-sent when the VFO
            # moves, so its span must cover the whole window from ANY VFO
            # position inside it, not just from the tuned frequency now.
            distance = max(distance, resolved.end_hz - resolved.start_hz)
        preset = next(
            (
                preset
                for preset in self._presets
                if self._coverage_half_width(preset) >= distance
            ),
            self._presets[-1],
        )
        half = self._coverage_half_width(preset)
        # MOR-4566: the spectrum ends at 0 Hz. A valid low-frequency window
        # (a VFO near the bottom, e.g. 65 kHz with a 30–100 kHz pinned
        # view) selects a preset whose ±coverage reaches underground; the
        # grant is clamped at the physical floor — the window, whose edges
        # are all real frequencies, stays covered instead of the grant
        # raising.
        return Coverage(max(0, center - half), center + half), preset

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
        MOR-4451 radio-owned window: in a family mode (FIX, SCROLL-C,
        SCROLL-F) the panorama stands still — the grant's view IS the
        native reported window, unchanged, independent of the VFO and of
        the requested follow width. The mode is the one ``feed_frame``
        already remembered in ``_native_coverages`` (stored before
        the hub regrants); the ``set_view`` fixed branch passes that
        same remembered window, so both paths agree. The scroll modes
        move their own edges on the radio; the view is whatever window
        the frames report. CENTER keeps the rule above.
        """
        remembered = self._native_coverages.get(self._receiver(receiver))
        if remembered is not None and remembered[0] in RADIO_OWNED_WINDOW_SCOPE_MODES:
            return View(native.start_hz, native.end_hz)
        requested = self.resolve_view(view, receiver)
        start = max(requested.start_hz, native.start_hz)
        end = min(requested.end_hz, native.end_hz)
        if start >= end:
            return native
        if (native.end_hz - native.start_hz) < (requested.end_hz - requested.start_hz):
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
        self,
        view: ViewRequest,
        receiver: int | None = None,
        *,
        apply_width: bool = True,
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
        if isinstance(view, FollowView) and not apply_width:
            # MOR-4532: a re-sent subscription or requestSeq 0 carries its
            # follow width as context only: no span or mode write. The
            # grant follows the radio's own window, as after a failed write.
            return self._follow_grant(view, selected)
        if observed_mode in RADIO_OWNED_WINDOW_SCOPE_MODES:
            # MOR-4392 round 2, gate round 1: the REQUESTED receiver is in
            # a radio-owned-window mode (FIX, SCROLL-C or SCROLL-F) — a
            # follow zoom/span gesture is a client crop (MOR-4387), never
            # a mode change. In the span-applicable family mode (SCROLL-C)
            # the gesture DOES select the span: SPAN (27 15) applies in
            # Center and SCROLL-C per the manual, EDGE number (27 16) in
            # FIX and SCROLL-F — so SCROLL-C writes ONLY the smallest
            # preset covering the requested follow width (the CENTER
            # ladder, no 0x14 mode flip, no centre/edge write), while FIX
            # and SCROLL-F write nothing at all. The grant stays the
            # reported window (the same resolve_follow rule the hub
            # applies per frame), so the browser never holds a stale wide
            # grant; the radio's next frames carry the new edges. With no
            # family-mode frame seen yet the coverage stays unknown and
            # the first frame resolves it. Back in CENTER the hub branch
            # re-resolves against the native coverage. The FIX/CTR
            # gestures themselves are explicit mode commands outside this
            # path and still write.
            if observed_mode in SPAN_APPLICABLE_SCOPE_MODES:
                _, preset = self._center_span(view, selected)
                try:
                    await self._writer.set_span(preset, selected)
                except (CommandError, TimeoutError, asyncio.TimeoutError):
                    pass
            remembered = self._native_coverages.get(selected)
            if (
                remembered is not None
                and remembered[0] in RADIO_OWNED_WINDOW_SCOPE_MODES
            ):
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
            return self._follow_grant(view, selected)
        return GrantedView(resolved, coverage)

    def _follow_grant(self, view: FollowView, receiver: int) -> GrantedView:
        """A follow grant from the radio's last streamed window, no write."""
        remembered = self._native_coverages.get(receiver)
        if remembered is not None:
            native = remembered[1]
            return GrantedView(self.resolve_follow(view, native, receiver), native)
        return GrantedView(self.resolve_view(view, receiver), None)

    def view_settled(self, receiver: int | None = None) -> bool:
        return self._writer.span_settled(self._receiver(receiver))

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

    #: MOR-4452: how far below a span preset's view width a reported
    #: span-mode window may sit and still be that preset's sweep extent.
    #: The extent loses only the trimmed outer bins of the sweep (a few
    #: bins of 475 ≈ up to ~2%); shipped preset ladders space presets at
    #: least 2× apart, so 5% names exactly one preset and can never reach
    #: a neighbouring one.
    _SWEEP_EXTENT_TOLERANCE = 0.05

    def _preset_window(self, start: float, end: float) -> tuple[float, float]:
        """The display window a span-mode wave's reported extent names.

        MOR-4452: in the span modes the radio's waveform header reports
        the extent of the sweep points, which sits a few trimmed bins
        inside the display width the span preset selects (at the ±50 kHz
        IC-705 preset the wave reports ±49.25 kHz). CENTER mode never
        shows it: the assembler synthesises centre ± preset, so its bins
        already span the preset width. A SCROLL-C wave reports raw
        edges, so adopting them verbatim narrowed the view (and the
        SPAN readout) by the trimmed fraction. Expand a reported window
        that sits just below a preset's view width to that width around
        its own centre — the same "bins span the preset width" rule
        CENTER already gets. A window at or above the nearest preset, or
        far from every preset, is reported verbatim: it is not a sweep
        extent of any preset this profile declares.
        """
        width = end - start
        if width <= 0 or not self._caps.span_choices_hz:
            return start, end
        preset = min(self._caps.span_choices_hz, key=lambda choice: abs(choice - width))
        if width < preset and (preset - width) / preset <= self._SWEEP_EXTENT_TOLERANCE:
            center = (start + end) / 2
            return center - preset / 2, center + preset / 2
        return start, end

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
        if not frame.out_of_range and frame.mode in SPAN_APPLICABLE_SCOPE_MODES:
            start, end = self._preset_window(start, end)
        previous = self._native_coverages.get(receiver)
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
        # After the frame is handed to the stream: whatever the listener asks
        # the radio for is answered after this frame boundary.
        if (
            previous is not None
            and previous[0] != frame.mode
            and self._on_mode_change is not None
        ):
            self._on_mode_change(receiver, frame.mode)
