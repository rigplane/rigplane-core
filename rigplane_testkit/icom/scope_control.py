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
