"""MOR-3534 property: interleaved keying from two or three controller sessions.

Random interleavings of PTT down/up, latched transmit_on, force_off and session
expiry never put two owners on air (between any two ON effects the lane sees a
FORCE_RECEIVE), and an expired session never keeps the transmitter.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, str(Path(__file__).parent.parent))

from test_controller_sessions import open_session, stack  # noqa: E402

from rigplane.runtime.controller_authority import ControllerError  # noqa: E402
from rigplane.runtime.managed_tx_state import (  # noqa: E402
    ActuationOperation,
    ManagedTxIntentKind,
)

pytestmark = pytest.mark.property

_SESSIONS = ("a", "b", "c")
_OPS = ("ptt_down", "ptt_up", "transmit_on", "expire", "force_off")
_ON = (ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON)
_RELEASE = ActuationOperation.FORCE_RECEIVE


async def _scenario(
    session_count: int, steps: list[list[tuple[str, int]]]
) -> tuple[ManagedTxIntentKind, list[ActuationOperation]]:
    clock, controller, managed, lane = stack()
    names = _SESSIONS[:session_count]
    tickets = {name: open_session(controller, name)[1] for name in names}
    live = set(names)
    expired_owners: set[str] = set()
    clock.now += 0.5  # renewals from here on are strictly later than the opens
    try:
        for step in steps:
            for op, index in step:
                name = names[index % session_count]
                if op == "expire" and name in live:
                    # Every other session renews now; this one stops renewing
                    # and the clock reaches its deadline, which is strictly
                    # earlier: the clock moved on since it last renewed.
                    owner = str(tickets[name].tx_owner)
                    for other in live - {name}:
                        controller.heartbeat(tickets[other])
                    clock.now = controller._sessions[owner].deadline  # noqa: SLF001
                    controller.status()
                    live.discard(name)
                    expired_owners.add(owner)
                    for other in live:
                        controller.validate(tickets[other])

            async def apply(op: str, name: str) -> None:
                ticket = tickets[name]
                owner = str(ticket.tx_owner)
                try:
                    if op in ("ptt_down", "transmit_on"):
                        with controller.bind(ticket):
                            submission = (
                                managed.start_ptt_submission(True, owner)
                                if op == "ptt_down"
                                else managed.start_transmit_on_submission()
                            )
                        try:
                            await (await submission).wait_settlement()
                        except asyncio.CancelledError:
                            # A concurrent force_off cancels pending keying.
                            assert submission.cancelled()
                    elif op == "ptt_up":
                        await managed.ptt_up(owner)
                    elif op == "force_off":
                        await managed.force_off()
                except ControllerError:
                    assert name not in live, "a live session was refused currency"

            await asyncio.gather(
                *(
                    apply(op, names[index % session_count])
                    for op, index in step
                    if op != "expire"
                )
            )
            await controller.settle()

            on_air = False
            for effect in lane.effects:
                if effect.operation in _ON:
                    assert not on_air, "two ON effects without a release between"
                    on_air = True
                elif effect.operation is ActuationOperation.FORCE_RECEIVE:
                    on_air = False
            holder = managed.tx_holder_nowait()
            assert holder not in expired_owners, f"expired {holder} still holds TX"
            state = (await managed.snapshot()).state
            if state.intent.kind is not ManagedTxIntentKind.RX:
                assert holder in {str(tickets[name].tx_owner) for name in live}

        for name in tuple(live):
            controller.detach(tickets[name])
        await controller.settle()
        final = (await managed.snapshot()).state.intent.kind
        operations = [effect.operation for effect in lane.effects]
    finally:
        controller.revoke()
        await controller.settle()
        off = await managed.submit_force_off()
        await off.wait_settlement()
        await managed.close()
    return final, operations


@given(
    session_count=st.integers(min_value=2, max_value=3),
    steps=st.lists(
        st.lists(
            st.tuples(st.sampled_from(_OPS), st.integers(min_value=0, max_value=2)),
            min_size=1,
            max_size=3,
        ),
        min_size=1,
        max_size=8,
    ),
)
def test_interleaved_sessions_never_put_two_owners_on_air(
    session_count: int, steps: list[list[tuple[str, int]]]
) -> None:
    final, operations = asyncio.run(_scenario(session_count, steps))
    # Every session ended: the transmitter is released, and over the whole
    # run no ON effect followed another without a FORCE_RECEIVE between.
    assert final is ManagedTxIntentKind.RX
    on_air = [op for op in operations if op in _ON or op is _RELEASE]
    assert all(
        not (prev in _ON and op in _ON) for prev, op in zip(on_air, on_air[1:])
    ), operations
