"""The fail-closed TX audio gate shared by every audio playout path.

``tx_gate_open`` is the one extracted implementation of the rule that used
to live in ``WebServer._bridge_tx_gate_open`` (MOR-2863, MOR-3821 P0b):
open only while the managed TX intent is keyed — RigPlane itself keyed
the rig — or a fresh observed PTT is exactly ON. Everything else closes
the gate: unknown or stale observation, no authority, a snapshot that
cannot be read, or a controller in ``remote`` mode.

It lives in ``runtime`` because ``runtime`` sits below both UI servers and
next to ``audio``, so ``web``, the audio bridge and a future TCI adapter
can all reach it while it reaches nothing but ``core``.
"""

from __future__ import annotations

import logging

from ..core.state_store import StateStore
from ..core.tx_observation import ObservedPtt, project_observed_ptt
from .controller_authority import ControllerAuthority
from .managed_tx_authority import ManagedTxAuthority
from .managed_tx_state import ManagedTxIntentKind

logger = logging.getLogger(__name__)

__all__ = ["tx_gate_open"]


async def tx_gate_open(
    authority: ManagedTxAuthority | None,
    store: StateStore | None = None,
    controller: ControllerAuthority | None = None,
) -> bool:
    """Whether an audio path may push captured audio to the radio.

    Fail-closed (MOR-2863). Open only when the managed TX intent is
    keyed or a fresh observed PTT is exactly ON.
    """
    if controller is not None and controller.mode == "remote":
        return False
    if authority is None:
        return False
    try:
        projection = await authority.snapshot()
        observed = (
            project_observed_ptt(store.snapshot())
            if store is not None
            else ObservedPtt.UNKNOWN
        )
    except Exception:
        logger.debug("tx-gate: TX gate failed closed", exc_info=True)
        return False
    keyed = projection.state.intent.kind is not ManagedTxIntentKind.RX
    return keyed or observed is ObservedPtt.ON
