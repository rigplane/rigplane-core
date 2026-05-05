"""Initial-state fetch orchestration for :class:`IcomRadio`.

Extracted from ``radio.py`` (issue #1260, Tier 3 wave 3 of #1063) to slim down
the god-object module. The single function here drives a one-shot population
of :class:`RadioState` immediately after connect by iterating CI-V state
queries built from the radio profile and capabilities and dispatching them as
fire-and-forget reads.

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
    from icom_lan.radio import IcomRadio  # type: ignore[attr-defined]  # noqa: TID251

logger = logging.getLogger(__name__)


async def fetch_initial_state(radio: IcomRadio) -> None:
    """Fetch full radio state once to populate RadioState.

    Iterates through all state queries built from the radio profile and
    sends each as a fire-and-forget CI-V read.  On completion sets
    ``_initial_state_fetched = True``.

    This is non-fatal: failures are logged but do not raise.
    """
    from ._state_queries import build_state_queries

    try:
        is_serial = not radio._profile.has_lan
        gap = (
            radio._INITIAL_STATE_GAP_SERIAL
            if is_serial
            else radio._INITIAL_STATE_GAP_LAN
        )
        queries = build_state_queries(
            radio._profile,
            radio.capabilities,
            is_serial=is_serial,
        )
        if not queries:
            radio._initial_state_fetched = True
            return

        logger.info(
            "initial state fetch (%d queries, gap=%.0fms)...",
            len(queries),
            gap * 1000,
        )
        ok = 0
        for cmd_byte, sub_byte, receiver in queries:
            try:
                if receiver is not None:
                    if cmd_byte in (0x25, 0x26):
                        # Freq/mode: receiver byte as data payload
                        await radio.send_civ(
                            cmd_byte,
                            data=bytes([receiver]),
                            wait_response=False,
                        )
                    else:
                        # cmd29-wrapped: 0x29 with [receiver, cmd, sub?]
                        inner = bytes([receiver, cmd_byte])
                        if sub_byte is not None:
                            inner += bytes([sub_byte])
                        await radio.send_civ(0x29, data=inner, wait_response=False)
                else:
                    await radio.send_civ(
                        cmd_byte,
                        sub=sub_byte,
                        data=b"",
                        wait_response=False,
                    )
                ok += 1
            except Exception:
                pass  # non-fatal; regular polling will retry
            await asyncio.sleep(gap)

        logger.info(
            "initial state fetch done (%d/%d ok)",
            ok,
            len(queries),
        )
    except Exception:
        logger.warning(
            "initial state fetch failed",
            exc_info=True,
        )
    finally:
        radio._initial_state_fetched = True
