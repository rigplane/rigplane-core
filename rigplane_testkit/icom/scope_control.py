"""Scope-control simulator using Core's CI-V response parsers."""

from rigplane.commands import (
    parse_civ_frame,
    parse_scope_mode_response,
    parse_scope_span_response,
    parse_scope_edge_response,
    parse_scope_fixed_edge_response,
)
from rigplane.core.types import bcd_encode
from rigplane.core.types import ScopeFixedEdge
from rigplane.profiles import resolve_radio_profile
from rigplane_testkit.icom import MockIcomRadio
from typing import Any


class ScopeControlSimulator(MockIcomRadio):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._scope_10 = 0
        self.profile = resolve_radio_profile(model="IC-7610")
        self.views: dict[int, tuple[int, int, int]] = {r: (0, 3, 1) for r in (0, 1)}
        self.fixed: dict[bytes, ScopeFixedEdge] = {}

    def _dispatch_scope(self, payload: bytes, from_addr: int) -> bytes | None:
        sub, rest = payload[0], payload[1:]
        if sub == 0x10 and rest == b"\x00":
            self._scope_10 = 0
            return self._civ_ack(from_addr, self._radio_addr)
        if sub in (0x14, 0x15, 0x16) and rest:
            receiver = rest[0]
            mode, span, edge = self.views[receiver]
            if len(rest) == 1:
                value = (
                    bytes([mode])
                    if sub == 0x14
                    else bcd_encode(self.profile.scope_span_presets_hz[span])
                    if sub == 0x15
                    else bytes([edge])
                )
                return self._civ_frame(
                    from_addr,
                    self._radio_addr,
                    0x27,
                    data=bytes([sub, receiver]) + value,
                )
            frame = parse_civ_frame(
                self._civ_frame(from_addr, self._radio_addr, 0x27, data=payload)
            )
            if sub == 0x14:
                _, mode = parse_scope_mode_response(frame)
            elif sub == 0x15:
                _, span = parse_scope_span_response(
                    frame, self.profile.scope_span_presets_hz
                )
            else:
                _, edge = parse_scope_edge_response(frame)
            self.views[receiver] = mode, span, edge
            return self._civ_ack(from_addr, self._radio_addr)
        if sub == 0x1E and len(rest) >= 2:
            key = rest[:2]
            if len(rest) == 2:
                fixed_value = self.fixed.get(
                    key,
                    ScopeFixedEdge(int(f"{key[0]:02x}"), key[1], 14000000, 14350000),
                )
                data = (
                    key
                    + bcd_encode(fixed_value.start_hz)
                    + bcd_encode(fixed_value.end_hz)
                )
                return self._civ_frame(
                    from_addr, self._radio_addr, 0x27, data=b"\x1e" + data
                )
            frame = parse_civ_frame(
                self._civ_frame(from_addr, self._radio_addr, 0x27, data=payload)
            )
            self.fixed[key] = parse_scope_fixed_edge_response(frame)
            return self._civ_ack(from_addr, self._radio_addr)
        return super()._dispatch_scope(payload, from_addr)


class ScopeWindowSimulator(ScopeControlSimulator):
    """Replay scope waves with the mode and window from applied CI-V settings."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.windows: dict[int, tuple[int, int]] = {}
        # SCROLL-C keeps its window until the VFO leaves it or the span changes.
        self._scroll: dict[int, tuple[int, int]] = {}
        self._scroll_span: dict[int, int] = {}

    def pump_scope_stream(self) -> int:
        for receiver, templates in self._scope_templates.items():
            mode, span, _edge = self.views[receiver]
            if mode == 0:
                # Center mode carries (center, span); the assembler expands.
                first = self._frequency
                last = self.profile.scope_span_presets_hz[span]
            elif mode == 2:
                preset = self.profile.scope_span_presets_hz[span]
                tuned = self._frequency if receiver == 0 else self._unselected_frequency
                window = self._scroll.get(receiver)
                if (
                    window is None
                    or self._scroll_span.get(receiver) != span
                    or not window[0] <= tuned < window[1]
                ):
                    window = (tuned - preset, tuned + preset)
                    self._scroll[receiver] = window
                    self._scroll_span[receiver] = span
                first, last = window
            else:
                # FIX/SCROLL-F carry configured edges.
                first, last = self.windows.get(receiver, (14_000_000, 14_350_000))
            self._scope_templates[receiver] = [
                template[:9]
                + bytes([mode])
                + bcd_encode(first)
                + bcd_encode(last)
                + b"\x00"
                + bytes(15 + index % 30 for index in range(len(template) - 22))
                + template[-1:]
                for template in templates
            ]
        return super().pump_scope_stream()
