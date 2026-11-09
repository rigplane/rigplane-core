"""Typed scope-source, subscription and v2 frame contracts."""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol, TypeAlias

SourceKind: TypeAlias = Literal["radio", "audio_fft", "iq_fft"]
ViewMode: TypeAlias = Literal["center", "fixed", "free"]


def _number(value: object, name: str, minimum: float = 0) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value < minimum
    ):
        raise ValueError(f"{name} must be a finite number >= {minimum}")


def _integer(value: object, name: str, maximum: int, minimum: int = 0) -> None:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer in [{minimum}, {maximum}]")


def _receiver(value: object) -> None:
    if value is not None:
        _integer(value, "receiver", 65534)


def _identity(value: object) -> None:
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 255
        or not value.isascii()
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ValueError(
            "source_id must contain 1..255 printable ASCII characters without spaces"
        )


@dataclass(frozen=True)
class ScopeDemand:
    subscribers: int
    active: int

    def __post_init__(self) -> None:
        if not 0 <= self.active <= self.subscribers:
            raise ValueError("scope demand requires 0 <= active <= subscribers")


@dataclass(frozen=True)
class View:
    start_hz: float
    end_hz: float

    def __post_init__(self) -> None:
        _number(self.start_hz, "start_hz")
        _number(self.end_hz, "end_hz")
        if self.end_hz <= self.start_hz:
            raise ValueError("end_hz must exceed start_hz")


@dataclass(frozen=True)
class Coverage(View):
    pass


@dataclass(frozen=True)
class FollowView:
    follow: Literal["vfo"]
    span_hz: float

    def __post_init__(self) -> None:
        if self.follow != "vfo":
            raise ValueError("follow must be vfo")
        _number(self.span_hz, "span_hz")
        if self.span_hz == 0:
            raise ValueError("span_hz must be positive")


ViewRequest: TypeAlias = View | FollowView


@dataclass(frozen=True)
class GrantedView:
    view: View
    coverage: Coverage | None

    def __post_init__(self) -> None:
        if not isinstance(self.view, View) or (
            self.coverage is not None and not isinstance(self.coverage, Coverage)
        ):
            raise ValueError("granted view and coverage must be typed geometry")


@dataclass(frozen=True)
class SourceCapabilities:
    receivers: tuple[int | None, ...]
    view_modes: tuple[ViewMode, ...]
    span_choices_hz: tuple[float, ...]
    native_width: int | None
    max_fps: float
    coverage_rule: str
    receiver_native_widths: tuple[tuple[int | None, int], ...] = ()

    def native_width_for(self, receiver: int | None) -> int | None:
        return dict(self.receiver_native_widths).get(receiver, self.native_width)

    def __post_init__(self) -> None:
        if not isinstance(self.receivers, tuple) or not self.receivers:
            raise ValueError("receivers must be a nonempty tuple")
        for receiver in self.receivers:
            _receiver(receiver)
        if (
            not isinstance(self.view_modes, tuple)
            or not self.view_modes
            or any(m not in ("center", "fixed", "free") for m in self.view_modes)
        ):
            raise ValueError("invalid view_modes")
        if not isinstance(self.span_choices_hz, tuple):
            raise ValueError("span_choices_hz must be a tuple")
        for span in self.span_choices_hz:
            _number(span, "span")
            if span == 0:
                raise ValueError("span must be positive")
        if self.native_width is not None:
            _integer(self.native_width, "native_width", 65535, 1)
        if not isinstance(self.receiver_native_widths, tuple):
            raise ValueError("receiver_native_widths must be a tuple")
        seen: set[int | None] = set()
        for entry in self.receiver_native_widths:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise ValueError("invalid receiver native width")
            receiver, width = entry
            _receiver(receiver)
            if receiver not in self.receivers or receiver in seen:
                raise ValueError("invalid receiver native width")
            _integer(width, "native_width", 65535, 1)
            seen.add(receiver)
        _number(self.max_fps, "max_fps")
        if (
            self.max_fps == 0
            or not isinstance(self.coverage_rule, str)
            or not self.coverage_rule
        ):
            raise ValueError("capabilities need positive max_fps and coverage_rule")


@dataclass(frozen=True)
class NativeFrame:
    receiver: int | None
    coverage: Coverage
    pixels: bytes | None
    out_of_range: bool = False
    epoch: int = 0
    native_width: int | None = None

    def __post_init__(self) -> None:
        _receiver(self.receiver)
        if not isinstance(self.coverage, Coverage) or (
            self.pixels is not None and not isinstance(self.pixels, bytes)
        ):
            raise ValueError("native frame needs Coverage and bytes or None")
        if type(self.out_of_range) is not bool:
            raise ValueError("out_of_range must be bool")
        _integer(self.epoch, "epoch", 2**32 - 1)
        if self.native_width is not None:
            _integer(self.native_width, "native_width", 65535, 1)


@dataclass(frozen=True)
class ScopeTarget:
    source_id: str
    receiver: int | None

    def __post_init__(self) -> None:
        _identity(self.source_id)
        _receiver(self.receiver)


@dataclass(frozen=True)
class Subscription:
    source_id: str
    receiver: int | None
    view: ViewRequest
    width: int
    max_fps: float

    def __post_init__(self) -> None:
        _identity(self.source_id)
        _receiver(self.receiver)
        if not isinstance(self.view, (View, FollowView)):
            raise ValueError("view must be View or FollowView")
        _integer(self.width, "width", 65535, 1)
        _number(self.max_fps, "max_fps")


@dataclass(frozen=True)
class GrantedSubscription:
    source_id: str
    receiver: int | None
    view: View
    coverage: Coverage | None
    width: int
    max_fps: float

    def __post_init__(self) -> None:
        Subscription(self.source_id, self.receiver, self.view, self.width, self.max_fps)
        GrantedView(self.view, self.coverage)


@dataclass(frozen=True)
class ScopeWireFrame:
    source: SourceKind
    source_id: str
    receiver: int | None
    view_start_hz: float
    view_end_hz: float
    coverage_start_hz: float
    coverage_end_hz: float
    width: int
    pixels: bytes | None
    out_of_range: bool
    epoch: int
    seq: int
    sent_at_ms: int | None

    def __post_init__(self) -> None:
        if self.source not in ("radio", "audio_fft", "iq_fft"):
            raise ValueError("invalid source")
        _identity(self.source_id)
        _receiver(self.receiver)
        View(self.view_start_hz, self.view_end_hz)
        _number(self.coverage_start_hz, "coverage_start_hz")
        _number(self.coverage_end_hz, "coverage_end_hz")
        if (
            not self.view_start_hz
            <= self.coverage_start_hz
            <= self.coverage_end_hz
            <= self.view_end_hz
        ):
            raise ValueError(
                "coverage must be inside view (equal edges mean no coverage)"
            )
        _integer(self.width, "width", 65535, 1)
        if self.pixels is not None and (
            not isinstance(self.pixels, bytes) or len(self.pixels) != self.width
        ):
            raise ValueError("pixels must be None or exactly width bytes")
        if type(self.out_of_range) is not bool:
            raise ValueError("out_of_range must be bool")
        _integer(self.epoch, "epoch", 2**32 - 1)
        _integer(self.seq, "seq", 2**32 - 1)
        if self.sent_at_ms is not None:
            _integer(self.sent_at_ms, "sent_at_ms", 2**53 - 1)


class ScopeSourcePlugin(Protocol):
    @property
    def kind(self) -> SourceKind: ...

    def capabilities(self) -> SourceCapabilities: ...
    def resolve_view(self, view: ViewRequest, receiver: int | None = None) -> View: ...
    def coverage_for(
        self, view: ViewRequest, receiver: int | None = None
    ) -> Coverage | None: ...
    async def set_view(
        self, view: ViewRequest, receiver: int | None = None
    ) -> GrantedView: ...
    async def start(self) -> None: ...
    async def stop(self, *, release: bool = True) -> None: ...
    def on_frame(
        self, callback: Callable[[NativeFrame], None]
    ) -> Callable[[], None]: ...
