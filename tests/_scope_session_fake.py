"""Typed session fixture for web and poller radio fakes."""

from dataclasses import replace
from unittest.mock import Mock

from rigplane.core.types import ScopeFixedEdge
from rigplane.runtime.scope_session import ScopeSession, ScopeState


def attach_scope_session(radio, **kwargs):
    current = ScopeState(False, False, 0, 0, 1)
    writes = []

    async def read():
        panel, output = await radio.get_scope_session_state()
        return replace(current, scope_on=panel, output_on=output)

    async def write(name, value, receiver):
        nonlocal current
        writes.append((name, value, receiver))
        if name == "output_on":
            if value:
                await radio.enable_scope(policy="fast")
            else:
                await radio.disable_scope(policy="fast")
        elif name in ("mode", "span", "edge"):
            await getattr(radio, f"set_scope_{name}")(value)
        elif name == "fixed_edge":
            await radio.set_scope_fixed_edge(
                edge=value.edge,
                start_hz=value.start_hz,
                end_hz=value.end_hz,
                range_index=value.range_index,
            )
        if name != "fixed_edge":
            current = replace(current, **{name: value})

    async def read_fixed(range_index, edge):
        return ScopeFixedEdge(range_index, edge, 14000000, 14350000)

    session = ScopeSession(
        read,
        write,
        read_fixed,
        ready=True,
        frequency=lambda r: 14_100_000,
        spans=radio.profile.scope_span_presets_hz,
        **kwargs,
    )
    radio.scope_session = Mock(return_value=session)
    radio.scope_writes = writes
    return session
