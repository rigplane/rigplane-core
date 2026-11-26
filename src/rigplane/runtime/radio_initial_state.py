"""Initial-state fetch orchestration for :class:`IcomRadio`.

Extracted from ``radio.py`` (issue #1260, Tier 3 wave 3 of #1063) to slim down
the god-object module. The single function here drives a one-shot population
of :class:`RadioState` immediately after connect by iterating CI-V state
queries built from the radio profile and dispatching them as fire-and-forget
reads.

Behaviour is intentionally identical to the previous
``IcomRadio._fetch_initial_state`` method: per-query failures are swallowed,
the overall fetch is non-fatal, and ``_initial_state_fetched`` is always set
to ``True`` on exit. The public ``IcomRadio._fetch_initial_state`` method now
delegates here.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rigplane.core.acquisition_scheduler import AcquisitionQuery

    # Internal implementation module for IcomRadio — the TID251 ban targets
    # external consumers (web/, rigctld/), not radio.py's own helpers.
    from rigplane.radio import IcomRadio  # type: ignore[attr-defined]  # noqa: TID251

logger = logging.getLogger(__name__)


async def fetch_initial_state(radio: IcomRadio) -> None:
    """Fetch full radio state once to populate RadioState.

    Iterates through all state queries built from the radio profile and
    sends each as a fire-and-forget CI-V read.  On completion sets
    ``_initial_state_fetched = True``.

    This is non-fatal: failures are logged but do not raise.
    """
    from rigplane.core.acquisition_scheduler import resolve_available_when

    from ._state_queries import availability_source_queries, build_state_queries

    try:
        is_serial = not radio._profile.has_lan
        gap = (
            radio._INITIAL_STATE_GAP_SERIAL
            if is_serial
            else radio._INITIAL_STATE_GAP_LAN
        )
        store = radio._state_store
        # MOR-3630: the sweep also runs after an external CAT owner hands the
        # radio back, when the store still holds what the radio was left in
        # before that owner changed it. A read the declared ``available_when``
        # contradicts is not sent (the radio would NAK it), so the fields those
        # clauses read (the mode, PTT) are read first, and awaited -- the
        # answer is in the store when ``send_civ`` returns -- and the sweep
        # filters on that, not on the stale snapshot. A source that does not
        # answer is read once more, then keeps its stored value (MOR-3924).
        answered = await _refresh_sources(
            radio, availability_source_queries(radio._profile), gap=gap
        )
        snapshot = store.snapshot()
        queries = [
            query
            for query in build_state_queries(radio._profile, snapshot=snapshot)
            if query not in answered
        ]
        if not queries:
            radio._initial_state_fetched = True
            return

        logger.info(
            "initial state fetch (%d queries, gap=%.0fms)...",
            len(queries),
            gap * 1000,
        )
        acquisition = radio._profile.state_acquisition
        assert acquisition is not None  # no query is built without one
        # MOR-3924: the list was filtered on ``snapshot``, but an answer can
        # land while an earlier read of this loop is dispatched and turn a
        # later read's clause false (MAIN back to FM). Ask the same resolver
        # again right before each send, and rebuild the eligible list only
        # when its answer changed: a read the list no longer holds is not
        # sent, exactly as one it never held.
        availability = resolve_available_when(acquisition, snapshot)
        eligible = frozenset(queries)
        sent = 0
        withheld = 0
        for query in queries:
            snapshot = store.snapshot()
            resolved = resolve_available_when(acquisition, snapshot)
            if resolved != availability:
                availability = resolved
                eligible = frozenset(
                    build_state_queries(radio._profile, snapshot=snapshot)
                )
            if query not in eligible:
                withheld += 1
                continue
            if await _send_query(radio, query, wait_response=False):
                sent += 1
            await asyncio.sleep(gap)

        if withheld:
            logger.debug(
                "initial state fetch withheld %d reads whose available_when "
                "clause turned false during the sweep",
                withheld,
            )
        logger.info(
            "initial state fetch sent %d/%d queries",
            sent,
            len(queries),
        )
    except Exception:
        logger.warning(
            "initial state fetch failed",
            exc_info=True,
        )
    finally:
        radio._initial_state_fetched = True


async def _refresh_sources(
    radio: IcomRadio, sources: list[AcquisitionQuery], *, gap: float
) -> set[AcquisitionQuery]:
    """Read the fields ``available_when`` clauses read, awaited (MOR-3630).

    Returns the sources that answered. One the radio did not answer is read
    once more before the sweep filters on it (MOR-3924): the store then still
    holds what the previous owner left the radio in, and filtering on that
    drops every dependent read for the whole reconciliation, which a
    standalone ``reconcile_state()`` has no poller to recover. A source that
    stays silent keeps its stored value and goes out again, unawaited, with
    the rest of the sweep.
    """
    answered: set[AcquisitionQuery] = set()
    silent: list[AcquisitionQuery] = []
    for query in sources:
        if await _send_query(radio, query, wait_response=True):
            answered.add(query)
        else:
            silent.append(query)
        await asyncio.sleep(gap)
    for query in silent:
        if await _send_query(radio, query, wait_response=True):
            answered.add(query)
        await asyncio.sleep(gap)
    return answered


async def _send_query(
    radio: IcomRadio, query: AcquisitionQuery, *, wait_response: bool
) -> bool:
    """Send one sweep read; a failure is logged and reported as not sent."""
    from ._state_queries import wire_parts_for_query

    try:
        command, sub, data = wire_parts_for_query(
            query, radio.radio_state.scope_controls.receiver
        )
        await radio.send_civ(
            command,
            sub=sub,
            data=data,
            wait_response=wait_response,
        )
    except Exception as exc:
        # non-fatal; regular polling will retry
        logger.debug(
            "initial state query failed: command=0x%02X sub=%s data=%s (%s)",
            query.command,
            "None" if query.sub is None else f"0x{query.sub:02X}",
            query.data.hex(),
            type(exc).__name__,
        )
        return False
    return True
