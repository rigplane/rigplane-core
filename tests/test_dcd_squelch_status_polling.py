"""MOR-3630: the squelch-open (RX-busy) status polls on polling-only links.

The BUSY lamp reads the receiver's own ``dcd`` leaf. On links that never
emit spontaneous 0x15 frames (USB serial), nothing scheduled that leaf, so
the lamp stayed unknown. The shared acquisition resolver now maps
``receiver.*.operator_toggles.dcd`` onto the S-meter SQL-status getter
(CI-V 0x15 0x01), and every profile declaring that getter polls it.

Mutation that turns these red: drop the ``"dcd"`` row from
``runtime/_state_queries.py::_RECEIVER_TOGGLE_GETTERS`` (the resolver stops
resolving) or remove the path from a profile's ``polling_only`` (the
scheduler stops dispatching).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from rigplane.commands._frame import (
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionQuery,
    AcquisitionRequest,
    AcquisitionScheduler,
    IcomCivAcquisitionExecutor,
    StateFreshnessService,
)
from rigplane.core.state_pipeline_contracts import (
    ChangeSet,
    FieldChange,
    FieldPath,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.runtime._state_queries import (
    acquisition_query_resolver_for_profile,
    build_state_queries,
    wire_parts_for_query,
)

_DCD = FieldPath.receiver("main", "operator_toggles", "dcd")
_DCD_STORE_KEY = "receiver.0.operator_toggles.dcd"
# CI-V 0x15 sub 0x01, the single-byte S-meter SQL-status read every Icom
# CI-V guide documents next to the S-meter level read (0x15 0x02).
_SQL_STATUS_WIRE = (0x15, 0x01, b"")
# USB serial: the 25 ms Icom gap bounds the transport at 40 q/s.
_SERIAL_BUDGET_HZ = 1.0 / 0.025
_TICK_STEP_SECONDS = 0.05


def query_for_dcd(query: AcquisitionQuery) -> bool:
    """Match the exact post-unwrap dcd query the executor sends."""
    return (
        query.command == 0x15
        and query.sub == 0x01
        and query.data == b""
        and query.receiver is None
    )


@pytest.mark.parametrize("model", ["IC-705", "IC-7300"])
def test_dcd_is_a_polled_path_with_a_squelch_status_query(model: str) -> None:
    """The profile polls dcd, and the poll reads 0x15 0x01 (MOR-3630)."""
    profile = resolve_radio_profile(model=model)
    acquisition = profile.state_acquisition
    assert acquisition is not None
    assert _DCD in acquisition.pollable_paths()
    assert acquisition_query_resolver_for_profile(profile)(_DCD) is not None
    queries = build_state_queries(profile)
    dcd_queries = [query for query in queries if query_for_dcd(query)]
    assert dcd_queries, f"{model}: no built query reads dcd"
    for query in dcd_queries:
        assert wire_parts_for_query(query, 0) == _SQL_STATUS_WIRE


async def test_polled_squelch_status_reply_publishes_dcd_open_then_closed() -> None:
    """Only polled 0x15 0x01 replies move dcd, open then closed (MOR-3630).

    Drives the real scheduler over a fake clock on the IC-7300 profile and
    answers the dispatched dcd poll through a fake transport — never a
    spontaneous frame. The reply bytes travel the real parse + ingress path
    into a real ``IcomRadio`` store, open (0x01) then closed (0x00).
    """
    rounds = await _run_polled_rounds(model="IC-7300", sql_bytes=(0x01, 0x00))

    assert [r.published for r in rounds] == [True, False]
    assert [r.wire for r in rounds] == [[_SQL_STATUS_WIRE], [_SQL_STATUS_WIRE]]
    assert [r.before for r in rounds] == [None, True]


async def test_polled_squelch_status_reply_publishes_dcd_on_ic705() -> None:
    """The IC-705 polls the same shared getter (MOR-3630)."""
    rounds = await _run_polled_rounds(model="IC-705", sql_bytes=(0x01, 0x00))

    assert [r.published for r in rounds] == [True, False]
    assert [r.wire for r in rounds] == [[_SQL_STATUS_WIRE], [_SQL_STATUS_WIRE]]
    assert [r.before for r in rounds] == [None, True]


@dataclass(frozen=True)
class _Round:
    """One poll round: the wire frame sent and the dcd value before/after."""

    wire: list[tuple[int, int | None, bytes]]
    before: bool | None
    published: bool | None


async def _run_polled_rounds(*, model: str, sql_bytes: tuple[int, ...]) -> list[_Round]:
    profile = resolve_radio_profile(model=model)
    acquisition = profile.state_acquisition
    assert acquisition is not None
    radio = IcomRadio("192.0.2.1", model=model)
    rounds: list[_Round] = []
    try:
        clock = FreshnessClock(start=1000.0)
        sched_store = StateStore(freshness_clock=clock)
        scheduler = AcquisitionScheduler(
            profile=acquisition,
            clock=clock,
            transport_budget_hz=_SERIAL_BUDGET_HZ,
        )
        service = StateFreshnessService(
            store=sched_store,
            scheduler=scheduler,
            interval_seconds=_TICK_STEP_SECONDS,
        )
        sent: list[AcquisitionQuery] = []

        async def send_query(
            query: AcquisitionQuery, _priority: AcquisitionPriority
        ) -> None:
            sent.append(query)

        executor = IcomCivAcquisitionExecutor(
            send_query,
            resolve_query=acquisition_query_resolver_for_profile(profile),
            supports_cmd29=profile.supports_cmd29,
        )
        for sql_byte in sql_bytes:
            sent.clear()
            request = _dispatch_dcd(service, scheduler, clock, model=model)
            execution = await executor.execute(request, already_sent_paths=frozenset())
            assert _DCD in execution.sent_paths
            assert execution.failed_paths == ()
            wire = [
                wire_parts_for_query(query, 0)
                for query in sent
                if query.command == 0x15 and query.sub == 0x01
            ]
            previous = _published_dcd(radio)
            _feed_polled_sql_reply(radio, profile.civ_addr, sql_byte)
            current = _published_dcd(radio)
            rounds.append(_Round(wire=wire, before=previous, published=current))
            _record_round(scheduler, sched_store, request, previous, current)
            # A CONTROL-class cadence (2.0 s) re-dispatches well inside the
            # freshness window even at the decay ceiling (TTL/2 = 2.5 s).
            clock.advance(3.0)
    finally:
        radio._connected = False
    return rounds


def _dispatch_dcd(
    service: StateFreshnessService,
    scheduler: AcquisitionScheduler,
    clock: FreshnessClock,
    *,
    model: str,
) -> AcquisitionRequest:
    """Tick until the scheduler dispatches dcd, or fail (never scheduled)."""
    deadline = clock.now() + 5.0
    while clock.now() <= deadline:
        service.tick(now=clock.now())
        for request in scheduler.dispatchable_requests():
            if _DCD in request.paths:
                return request
        clock.advance(_TICK_STEP_SECONDS)
    raise AssertionError(f"{model}: dcd never dispatched within 5 s of polls")


def _feed_polled_sql_reply(radio: IcomRadio, rig_addr: int, sql_byte: int) -> None:
    """Feed one scripted answer to the dispatched 0x15 0x01 poll.

    The only ingress this test ever feeds: a reply to the poll above, never
    a spontaneous frame. Real wire bytes through the real frame parser and
    the radio's real RX observation path.
    """
    reply = build_civ_frame(
        CONTROLLER_ADDR, rig_addr, 0x15, sub=0x01, data=bytes([sql_byte])
    )
    radio._civ_runtime._update_state_cache_from_frame(parse_civ_frame(reply))


def _published_dcd(radio: IcomRadio) -> bool | None:
    try:
        return radio._state_store.snapshot().field(_DCD_STORE_KEY).value
    except KeyError:
        return None


def _record_round(
    scheduler: AcquisitionScheduler,
    sched_store: StateStore,
    request: AcquisitionRequest,
    previous: bool | None,
    current: bool | None,
) -> None:
    """Report the round the way the production drain does: one changeset."""
    snapshot = sched_store.snapshot()
    scheduler.record_acquisition_result(
        request,
        ChangeSet(
            revision=snapshot.state_revision + 1,
            freshness_revision=snapshot.freshness_revision + 1,
            observation_seq=snapshot.observation_seq + 1,
            changes=(FieldChange(path=_DCD, previous=previous, current=current),),
            timestamp_monotonic=snapshot.generated_at_monotonic,
            sources=(
                SourceMetadata(
                    source="poll_response",
                    provider="mor3630_test",
                    transport="fake",
                ),
            ),
        ),
    )
