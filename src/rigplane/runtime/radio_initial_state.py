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
    from ._state_queries import build_state_queries, wire_parts_for_query

    try:
        is_serial = not radio._profile.has_lan
        gap = (
            radio._INITIAL_STATE_GAP_SERIAL
            if is_serial
            else radio._INITIAL_STATE_GAP_LAN
        )
        queries = build_state_queries(radio._profile)
        if not queries:
            radio._initial_state_fetched = True
            return

        logger.info(
            "initial state fetch (%d queries, gap=%.0fms)...",
            len(queries),
            gap * 1000,
        )
        sent = 0
        for query in queries:
            try:
                command, sub, data = wire_parts_for_query(
                    query, radio.radio_state.scope_controls.receiver
                )
                await radio.send_civ(
                    command,
                    sub=sub,
                    data=data,
                    wait_response=False,
                )
                sent += 1
            except Exception as exc:
                # non-fatal; regular polling will retry
                logger.debug(
                    "initial state query failed: command=0x%02X sub=%s data=%s (%s)",
                    query.command,
                    "None" if query.sub is None else f"0x{query.sub:02X}",
                    query.data.hex(),
                    type(exc).__name__,
                )
            await asyncio.sleep(gap)

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
