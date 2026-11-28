"""Fail-closed TX gate shared by web audio paths and the future TCI adapter.

``rigplane.runtime.tx_gate.tx_gate_open`` is the one extracted implementation
of the rule that used to live in ``WebServer._bridge_tx_gate_open`` (MOR-2863):
open only while the managed TX intent is keyed or a fresh observed PTT is
exactly ON. Everything else — unknown or stale observation, no authority, a
snapshot that cannot be read, a remote controller — closes the gate.

Observation time comes from the shared virtual clock (MOR-4075): each store's
freshness seam starts at the virtual now, so snapshot ages are exact with no
wall-clock read.
"""

from __future__ import annotations

from test_managed_tx_web_projection import _gate_authority

from rigplane.core.state_pipeline_contracts import (
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateSnapshot, StateStore
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.tx_gate import tx_gate_open
from rigplane.testing.timing import VirtualClock

_SOURCE = SourceMetadata(source="test", provider="tests")


def _store(
    value: object, clock: VirtualClock, *, age_seconds: float = 0.0
) -> StateStore:
    """A store whose freshness seam starts at the virtual now (MOR-4075).

    The observation is stamped ``age_seconds`` before that same instant, so
    the snapshot age is exactly ``age_seconds`` with no wall-clock read.
    """
    store_clock = FreshnessClock(start=clock.now)
    store = StateStore(freshness_clock=store_clock)
    store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=_SOURCE,
            timestamp_monotonic=store_clock.now() - age_seconds,
            max_age=60.0,
        )
    )
    return store


def _controller(*, remote: bool = False) -> ControllerAuthority:
    async def _cleanup() -> None:
        return None

    controller = ControllerAuthority(ready=lambda: True, cleanup=_cleanup)
    if remote:
        controller.mode = "remote"
    return controller


class _ExplodingAuthority:
    """Authority double whose projection read always fails."""

    async def snapshot(self) -> ManagedTxProjection:
        raise RuntimeError("projection unreadable")


async def test_gate_is_closed_when_unkeyed(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        assert (
            await tx_gate_open(
                managed, _store(ObservedPtt.OFF, virtual_clock), _controller()
            )
            is False
        )
    finally:
        await managed.close()


async def test_gate_opens_while_the_managed_intent_is_keyed(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        await managed.ptt_down("web")
        assert (
            await tx_gate_open(
                managed, _store(ObservedPtt.OFF, virtual_clock), _controller()
            )
            is True
        )
        await managed.ptt_up("web")
        assert (
            await tx_gate_open(
                managed, _store(ObservedPtt.OFF, virtual_clock), _controller()
            )
            is False
        )
    finally:
        await managed.close()


async def test_gate_opens_on_fresh_observed_ptt_on(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        assert (
            await tx_gate_open(
                managed, _store(ObservedPtt.ON, virtual_clock), _controller()
            )
            is True
        )
    finally:
        await managed.close()


async def test_gate_is_closed_when_observation_is_stale(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        assert (
            await tx_gate_open(
                managed,
                _store(ObservedPtt.ON, virtual_clock, age_seconds=3600.0),
                _controller(),
            )
            is False
        )
    finally:
        await managed.close()


async def test_gate_is_closed_when_observation_is_unknown(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        assert (
            await tx_gate_open(
                managed, _store(ObservedPtt.UNKNOWN, virtual_clock), _controller()
            )
            is False
        )
    finally:
        await managed.close()


async def test_gate_is_closed_without_an_authority(
    virtual_clock: VirtualClock,
) -> None:
    assert (
        await tx_gate_open(None, _store(ObservedPtt.ON, virtual_clock), _controller())
        is False
    )


async def test_gate_is_closed_when_the_remote_controller_holds(
    virtual_clock: VirtualClock,
) -> None:
    managed = _gate_authority()
    try:
        await managed.ptt_down("web")
        assert (
            await tx_gate_open(
                managed,
                _store(ObservedPtt.OFF, virtual_clock),
                _controller(remote=True),
            )
            is False
        )
        await managed.ptt_up("web")
    finally:
        await managed.close()


async def test_gate_is_closed_when_the_projection_cannot_be_read(
    virtual_clock: VirtualClock,
) -> None:
    assert (
        await tx_gate_open(
            _ExplodingAuthority(),
            _store(ObservedPtt.ON, virtual_clock),
            _controller(),
        )
        is False
    )


async def test_gate_is_closed_when_the_store_cannot_be_read() -> None:
    class _ExplodingStore:
        def snapshot(self) -> StateSnapshot:
            raise RuntimeError("store unreadable")

    managed = _gate_authority()
    try:
        assert (
            await tx_gate_open(
                managed,
                _ExplodingStore(),
                _controller(),
            )
            is False
        )
    finally:
        await managed.close()
