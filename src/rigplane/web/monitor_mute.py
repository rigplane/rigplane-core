"""Server-owned monitor MUTE (MOR-2583).

The page used to remember the pre-MUTE AF levels itself, so a reload while
muted left both receivers at 0 with nothing to restore. This module holds
whether monitor MUTE is on and the saved level of each receiver the radio
has. It is process state, not radio state, so a reconnect does not clear it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.exceptions import CommandError
from ..core.state_pipeline_contracts import FieldPath
from ..core.state_store import FreshnessState, StateStore

if TYPE_CHECKING:
    from ..radio_protocol import Radio

_RECEIVERS = (("main", 0), ("sub", 1))


@dataclass
class MonitorMuteState:
    """Whether monitor MUTE is on, and the AF each receiver had before it."""

    on: bool = False
    saved_af: dict[str, float] = field(default_factory=dict)

    def public(self, receiver_count: int) -> dict[str, Any]:
        """The state-payload object. A receiver the radio lacks is omitted."""
        saved: dict[str, float] = {}
        for name, index in _RECEIVERS:
            if index >= receiver_count or name not in self.saved_af:
                continue
            saved[name] = self.saved_af[name]
        return {"on": self.on, "savedAf": saved}


def _fresh_af(store: StateStore, receiver: int) -> float | None:
    """The receiver's current AF, only while the store still holds a fresh one."""
    try:
        snapshot = store.snapshot().field(
            FieldPath.receiver(str(receiver), "operator_controls", "af_level")
        )
    except KeyError:
        return None
    if snapshot.freshness is not FreshnessState.FRESH:
        return None
    value = snapshot.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    level = float(value)
    if not 0.0 <= level <= 1.0:
        return None
    return level


async def apply_monitor_mute(
    state: MonitorMuteState,
    radio: "Radio",
    store: StateStore,
    *,
    on: bool,
) -> None:
    """Mute or restore every receiver the radio has, through the Radio protocol.

    Mute saves each receiver's current AF, and commits those saved levels
    before the first write. A later write that fails must not lose a level
    already taken off the radio. A receiver whose level is already saved is
    not overwritten, so a retry after that failure does not save the 0 the
    failed write left behind. Unmute restores every saved level and then
    clears it, even when that receiver's AF has not been read again since a
    reconnect. A receiver the radio does not have is never written and never
    appears in the saved levels.
    """
    profile = getattr(radio, "profile", None)
    receiver_count = getattr(profile, "receiver_count", None)
    if not isinstance(receiver_count, int) or isinstance(receiver_count, bool):
        raise CommandError("monitor mute requires a radio with a known receiver count")
    receivers = [index for _name, index in _RECEIVERS if index < receiver_count]
    if on:
        saved = dict(state.saved_af)
        for index in receivers:
            name = "main" if index == 0 else "sub"
            if name in saved:
                continue
            current = _fresh_af(store, index)
            if current is None:
                raise CommandError(
                    f"monitor mute has no fresh AF level for receiver {index}"
                )
            saved[name] = current
        state.saved_af = saved
        state.on = True
        for index in receivers:
            await radio.set_af_level(0, receiver=index)
        return
    for index in receivers:
        name = "main" if index == 0 else "sub"
        if name not in state.saved_af:
            continue
        await radio.set_af_level(state.saved_af[name], receiver=index)
    state.saved_af.clear()
    state.on = False
