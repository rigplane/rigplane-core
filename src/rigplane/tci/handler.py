"""The session contract the adapter implements (MOR-3821 L2, spec 4).

The leaf carries no radio vocabulary: a command reaches
:meth:`TciHandler.on_command` as a name plus a tuple of strings, and
the adapter decides what it means. Routing follows spec 5.2/5.5: a
reply goes to the sender, and an accepted set additionally reaches
every other client — the two scopes are the two fields of
:class:`CommandOutcome`, so a refusal never leaks to observers.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

__all__ = [
    "CommandOutcome",
    "TciHandler",
]


@dataclass(frozen=True)
class CommandOutcome:
    """What one command sends: replies to the sender, sync to the rest."""

    to_sender: tuple[str, ...] = ()
    to_others: tuple[str, ...] = ()

    def __init__(
        self,
        to_sender: Sequence[str] = (),
        to_others: Sequence[str] = (),
    ) -> None:
        object.__setattr__(self, "to_sender", tuple(to_sender))
        object.__setattr__(self, "to_others", tuple(to_others))


class TciHandler(Protocol):
    """Adapter side of one TCI session (implemented once, in the adapter)."""

    async def on_connect(self) -> Sequence[str]:
        """Return the init burst: one ``name:args;`` line per command.

        The last line must be ``ready;`` (spec 5.4): clients seed their
        state from the burst and wait for it.
        """
        ...

    async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
        """Handle one parsed command; never raises into the server."""
        ...

    async def on_binary(self, payload: bytes) -> None:
        """Handle one binary frame (TX audio in phase audio)."""
        ...

    async def on_disconnect(self) -> None:
        """Release the session. Runs exactly once, survives one
        cancellation, never raises (the server enforces all three)."""
        ...
